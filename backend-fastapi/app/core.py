"""공통 인프라: 설정 · 에러 · 인증 · ID · 응답 스키마 · 저장소(Cloud SQL).

라우터와 서비스가 공유하는 것들만 모았다. 파일 하나에 몰아넣은 이유는
4일 스프린트에서 모듈을 6개 열어보며 흐름을 따라가는 비용이 더 크기 때문이다.

저장소는 §6에서 Cloud SQL(PostgreSQL 15 + PostGIS)로 교체되었다.
동기 SQLAlchemy를 `asyncio.to_thread`로 감쌌다. asyncpg를 쓰면 드라이버가 하나
더 늘고 GeoAlchemy 호환을 따로 봐야 하는데, 시연 규모에서 얻는 게 없다.
`db.py` 분리는 후반기 백로그.
"""
import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import quote_plus

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, Row
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


# ══════════════════════════════════════════════════════════════════
# 1. 설정
# ══════════════════════════════════════════════════════════════════
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_version: str = "0.1.0"

    # ── Gemini — 인증 방식 두 가지 지원
    #   AUTH_MODE=api_key : AI Studio API 키 (generativelanguage.googleapis.com)
    #   AUTH_MODE=vertex  : GCP ADC 기반 Vertex AI (aiplatform.googleapis.com)
    gemini_auth_mode: str = "api_key"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_s: float = 30.0
    gcp_project_id: str = ""
    gcp_location: str = "us-central1"

    # ── 데이터베이스
    #   로컬:      Cloud SQL Auth Proxy 가 127.0.0.1:5432 를 열어준다
    #   Cloud Run: INSTANCE_CONN 을 채우면 유닉스 소켓(/cloudsql/...)으로 붙는다
    #              → 코드 변경 없이 .env 값만 바뀐다
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "couponkok"
    db_user: str = "postgres"
    db_password: str = ""
    instance_conn: str = ""          # 예: proj-aj06-xxx:asia-northeast3:couponkok-db

    # 개발 단계에서는 true로 두고 uid를 고정한다.
    auth_disabled: bool = True
    dev_uid: str = "dev-uid-0001"

    max_image_bytes: int = 10 * 1024 * 1024   # 01_API_SPEC.md §3 (장당)
    max_images: int = 3                       # 카카오톡 선물하기는 2화면으로 쪼개진다
    poll_after_ms: int = 1500
    confidence_threshold: float = 0.7         # 01_API_SPEC.md §4
    search_radius_m: int = 300                # 서버 고정값. 클라이언트가 정하지 않는다

    # ── F-04 RAG — 02_DB_SCHEMA.md §8
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    rag_top_k: int = 3
    rag_similarity_threshold: float = 0.6     # 미만이면 "확인된 정보 없음"

    # ── F-03 브리핑 — 01_API_SPEC.md §5
    briefing_timeout_s: float = 10.0          # 초과·실패 시 TEMPLATE 폴백

    # 바코드 해시 salt. 해시는 중복 판정 전용이며 복호화되지 않는다.
    # 값이 바뀌면 기존 해시와 매칭되지 않으므로 배포 후에는 고정한다.
    barcode_salt: str = "couponkok-dev-salt"

    @property
    def allowed_image_types(self) -> set[str]:
        return {"image/jpeg", "image/png", "image/webp"}

    @property
    def use_vertex(self) -> bool:
        return self.gemini_auth_mode.strip().lower() == "vertex"

    @property
    def use_mock_gemini(self) -> bool:
        """인증 정보가 없으면 목(mock) 응답으로 대체 — 자격증명 없이도 흐름 검증이 가능하도록."""
        if self.use_vertex:
            return not self.gcp_project_id.strip()
        return not self.gemini_api_key.strip()

    @property
    def db_url(self) -> str:
        user = quote_plus(self.db_user)
        pw = quote_plus(self.db_password)
        name = self.db_name
        if self.instance_conn.strip():
            socket = f"/cloudsql/{self.instance_conn.strip()}"
            return f"postgresql+psycopg://{user}:{pw}@/{name}?host={socket}"
        return f"postgresql+psycopg://{user}:{pw}@{self.db_host}:{self.db_port}/{name}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ══════════════════════════════════════════════════════════════════
# 2. 공통 에러 — 01_API_SPEC.md §1.5
#    모든 4xx/5xx는 {"error": {"code","message","detail"}} 형식을 따른다.
# ══════════════════════════════════════════════════════════════════
class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


def error_body(
    code: str, message: str, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return body


_DEFAULT_CODES = {
    401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "RESOURCE_NOT_FOUND",
    405: "RESOURCE_NOT_FOUND", 413: "IMAGE_TOO_LARGE",
    415: "INVALID_IMAGE_FORMAT", 422: "VALIDATION_ERROR",
    429: "RATE_LIMITED", 503: "UPSTREAM_UNAVAILABLE",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            ".".join(str(p) for p in e.get("loc", []) if p != "body")
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "요청 형식이 올바르지 않습니다.",
                {"fields": [f for f in fields if f]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _DEFAULT_CODES.get(exc.status_code, "INTERNAL_ERROR")
        return JSONResponse(
            status_code=exc.status_code, content=error_body(code, str(exc.detail))
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # 내부 예외 메시지는 사용자에게 노출하지 않는다.
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "서버 내부 오류가 발생했습니다."),
        )


# ══════════════════════════════════════════════════════════════════
# 3. 리소스 ID — 01_API_SPEC.md §1.4 (접두사 + ULID)
# ══════════════════════════════════════════════════════════════════
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_B32[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_coupon_id() -> str:
    return f"cpn_{_ulid()}"


def new_device_id() -> str:
    return f"dev_{_ulid()}"


def new_store_id() -> str:
    return f"str_{_ulid()}"


# ══════════════════════════════════════════════════════════════════
# 4. 인증 — 01_API_SPEC.md §1.2
#    요청 본문의 user_id는 신뢰하지 않는다. uid는 토큰에서만 얻는다.
# ══════════════════════════════════════════════════════════════════
_firebase_ready = False


async def get_current_uid(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if settings.auth_disabled:
        return settings.dev_uid

    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "UNAUTHORIZED", "인증 정보가 없습니다.")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise ApiError(401, "UNAUTHORIZED", "인증 정보가 없습니다.")

    global _firebase_ready
    try:
        import firebase_admin
        from firebase_admin import auth as fb_auth

        if not _firebase_ready:
            if not firebase_admin._apps:
                firebase_admin.initialize_app()
            _firebase_ready = True
    except Exception as exc:
        logger.exception("Firebase 초기화 실패: %s", exc)
        raise ApiError(500, "INTERNAL_ERROR", "인증 모듈을 초기화할 수 없습니다.")

    try:
        return fb_auth.verify_id_token(token)["uid"]
    except fb_auth.ExpiredIdTokenError:
        raise ApiError(401, "TOKEN_EXPIRED", "인증이 만료되었습니다. 다시 시도해 주세요.")
    except Exception:
        raise ApiError(401, "TOKEN_INVALID", "유효하지 않은 인증 정보입니다.")


# ══════════════════════════════════════════════════════════════════
# 5. 응답 스키마 — 01_API_SPEC.md §3, §4
#    서버 출력이 명세를 벗어나지 않도록 내보내기 전에 한 번 검증한다.
# ══════════════════════════════════════════════════════════════════
CouponStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]
CouponType = Literal["PRODUCT", "AMOUNT", "DISCOUNT", "UNKNOWN"]


class Confidence(BaseModel):
    brand: float = Field(ge=0.0, le=1.0)
    product_name: float = Field(ge=0.0, le=1.0)
    expires_at: float = Field(ge=0.0, le=1.0)


class CouponData(BaseModel):
    brand_id: str | None
    brand_name: str
    coupon_type: CouponType = "UNKNOWN"
    product_name: str | None
    face_value: int | None = None   # 액면가·사용가능금액(원). PRODUCT에도 있을 수 있다
    expires_at: str | None          # KST 달력 날짜 YYYY-MM-DD
    usage_note: str | None = None   # 이 쿠폰 한 장에 적힌 이용조건. 브랜드 정책 아님(RAG 미색인)
    days_left: int | None           # 서버가 계산 (클라이언트가 계산하지 않는다)
    barcode_masked: str | None      # 원문은 절대 포함하지 않는다
    barcode_format: str | None
    is_used: bool = False
    confidence: Confidence
    needs_review: bool


class CouponError(BaseModel):
    code: str
    message: str


class CouponAcceptedResponse(BaseModel):
    """POST /api/v1/coupons — 202 Accepted"""

    coupon_id: str
    client_upload_id: str
    status: Literal["PROCESSING"] = "PROCESSING"
    created_at: str
    poll_after_ms: int


class CouponStatusResponse(BaseModel):
    """GET /api/v1/coupons/{coupon_id} — 200 OK"""

    coupon_id: str
    status: CouponStatus
    created_at: str
    completed_at: str | None = None
    data: CouponData | None = None
    error: CouponError | None = None


# ══════════════════════════════════════════════════════════════════
# 6. 저장소 — Cloud SQL (PostgreSQL 15 + PostGIS)
#    02_DB_SCHEMA.md 의 3테이블 + coupon_rules 를 그대로 쓴다.
#    ORM 매핑을 두지 않고 text() 원시 SQL을 쓴다. 스키마가 이미 init.sql에
#    확정돼 있어 매핑 계층이 이중 관리 지점만 만들기 때문이다.
# ══════════════════════════════════════════════════════════════════
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today_kst() -> date:
    return datetime.now(KST).date()


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 UTC, Z 접미사 — 01_API_SPEC.md §1.3"""
    return None if dt is None else dt.astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    engine = create_engine(
        settings.db_url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,   # Cloud SQL은 유휴 연결을 끊는다. 없으면 첫 쿼리가 간헐 실패
        pool_recycle=1800,
        future=True,
    )
    target = settings.instance_conn or f"{settings.db_host}:{settings.db_port}"
    logger.info("DB 연결 대상: %s/%s", target, settings.db_name)
    return engine


def _db_health_sync() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("DB 헬스 체크 실패: %s", exc)
        return False


async def db_health() -> bool:
    """GET /health 의 "db" 필드 — 01_API_SPEC.md §2"""
    return await asyncio.to_thread(_db_health_sync)


# ── 바코드 해시 ────────────────────────────────────────────────
def hash_barcode(number: str | None) -> str | None:
    """중복 등록 판정 전용. 원문은 어디에도 저장하지 않으므로 복호화되지 않는다."""
    if not number:
        return None
    digits = re.sub(r"\D", "", number)
    if len(digits) < 8:          # 너무 짧으면 오탐 위험이 크다
        return None
    salt = get_settings().barcode_salt
    return hashlib.sha256(f"{salt}:{digits}".encode()).hexdigest()


def compute_needs_review(
    coupon_type: str, conf: "Confidence | dict[str, float]"
) -> bool:
    """신뢰도 임계값 미달 필드가 있으면 True — 01_API_SPEC.md §4.

    금액권(AMOUNT)에는 상품명이 없는 것이 정상이다. product_name 신뢰도를
    그대로 계산에 넣으면 모든 금액권에 "정보를 확인해주세요" 배지가 붙는다.
    """
    if isinstance(conf, dict):
        values = conf
    else:
        values = conf.model_dump()
    threshold = get_settings().confidence_threshold
    keys = ["brand", "expires_at"]
    if coupon_type != "AMOUNT":
        keys.append("product_name")
    return any(float(values.get(k, 0.0)) < threshold for k in keys)


# ── 레코드 ────────────────────────────────────────────────────
@dataclass
class CouponRecord:
    coupon_id: str
    uid: str
    client_upload_id: str
    status: str = "PROCESSING"
    created_at: datetime = field(default_factory=utcnow)
    completed_at: datetime | None = None
    data: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    captured_at: str | None = None


_COUPON_COLUMNS = """
    coupon_id, uid, client_upload_id, status,
    brand_id, brand_name, coupon_type, product_name, face_value, expires_at,
    usage_note, barcode_masked, barcode_format, is_used,
    confidence, needs_review, error_code, error_message,
    created_at, completed_at
"""


def _row_to_record(row: Row) -> CouponRecord:
    m = row._mapping
    data = None
    if m["status"] == "COMPLETED":
        expires = m["expires_at"]
        conf = m["confidence"] or {"brand": 0.0, "product_name": 0.0, "expires_at": 0.0}
        data = {
            "brand_id": m["brand_id"],
            "brand_name": m["brand_name"] or "",
            "coupon_type": m["coupon_type"] or "UNKNOWN",
            "product_name": m["product_name"],
            "face_value": m["face_value"],
            "expires_at": expires.isoformat() if expires else None,
            # 조회 시점 기준으로 다시 계산한다. 하루가 지나도 카드가 정확해야 한다.
            "days_left": (expires - today_kst()).days if expires else None,
            "usage_note": m["usage_note"],
            "barcode_masked": m["barcode_masked"],
            "barcode_format": m["barcode_format"],
            "is_used": m["is_used"],
            "confidence": conf,
            "needs_review": m["needs_review"],
        }
    error = None
    if m["error_code"]:
        error = {"code": m["error_code"], "message": m["error_message"] or ""}
    return CouponRecord(
        coupon_id=m["coupon_id"],
        uid=m["uid"],
        client_upload_id=str(m["client_upload_id"]),
        status=m["status"],
        created_at=m["created_at"],
        completed_at=m["completed_at"],
        data=data,
        error=error,
    )


# ── 생성 (멱등) ───────────────────────────────────────────────
_SQL_UPSERT_USER = text(
    "INSERT INTO users (uid) VALUES (:uid) ON CONFLICT (uid) DO NOTHING"
)

_SQL_INSERT_COUPON = text(
    """
    INSERT INTO coupons (coupon_id, uid, client_upload_id, status)
    VALUES (:coupon_id, :uid, CAST(:client_upload_id AS uuid), 'PROCESSING')
    ON CONFLICT (uid, client_upload_id) DO NOTHING
    RETURNING coupon_id
    """
)

_SQL_SELECT_BY_UPLOAD = text(
    f"SELECT {_COUPON_COLUMNS} FROM coupons "
    "WHERE uid = :uid AND client_upload_id = CAST(:client_upload_id AS uuid)"
)


def _get_or_create_sync(
    uid: str, client_upload_id: str, captured_at: str | None
) -> tuple[CouponRecord, bool]:
    with get_engine().begin() as conn:
        # 익명 인증이라 별도 가입 절차가 없다. 첫 업로드 시점에 사용자 행을 만든다.
        conn.execute(_SQL_UPSERT_USER, {"uid": uid})
        params = {
            "coupon_id": new_coupon_id(),
            "uid": uid,
            "client_upload_id": client_upload_id,
        }
        inserted = conn.execute(_SQL_INSERT_COUPON, params).fetchone()
        row = conn.execute(
            _SQL_SELECT_BY_UPLOAD,
            {"uid": uid, "client_upload_id": client_upload_id},
        ).fetchone()
        record = _row_to_record(row)
        record.captured_at = captured_at
        return record, inserted is not None


async def get_or_create(
    uid: str, client_upload_id: str, captured_at: str | None = None
) -> tuple[CouponRecord, bool]:
    """멱등 생성 — 01_API_SPEC.md §3. created=False면 동일 키 재전송이다."""
    return await asyncio.to_thread(
        _get_or_create_sync, uid, client_upload_id, captured_at
    )


# ── 조회 ──────────────────────────────────────────────────────
_SQL_SELECT_ONE = text(
    f"SELECT {_COUPON_COLUMNS} FROM coupons WHERE coupon_id = :coupon_id"
)

_SQL_SELECT_LIST = text(
    f"""SELECT {_COUPON_COLUMNS} FROM coupons
        WHERE uid = :uid AND status = 'COMPLETED' AND is_used = false
        ORDER BY expires_at ASC NULLS LAST
        LIMIT :limit"""
)


def _get_coupon_sync(coupon_id: str) -> CouponRecord | None:
    with get_engine().connect() as conn:
        row = conn.execute(_SQL_SELECT_ONE, {"coupon_id": coupon_id}).fetchone()
    return _row_to_record(row) if row else None


async def get_coupon(coupon_id: str) -> CouponRecord | None:
    return await asyncio.to_thread(_get_coupon_sync, coupon_id)


def _list_coupons_sync(uid: str, limit: int) -> list[CouponRecord]:
    with get_engine().connect() as conn:
        rows = conn.execute(_SQL_SELECT_LIST, {"uid": uid, "limit": limit}).fetchall()
    return [_row_to_record(r) for r in rows]


async def list_coupons(uid: str, limit: int = 50) -> list[CouponRecord]:
    """F-05 만료 임박 순 목록. 사용 완료·실패 쿠폰은 제외한다."""
    return await asyncio.to_thread(_list_coupons_sync, uid, limit)


# ── 상태 확정 ─────────────────────────────────────────────────
_SQL_MARK_COMPLETED = text(
    """
    UPDATE coupons SET
        status         = 'COMPLETED',
        brand_id       = :brand_id,
        brand_name     = :brand_name,
        coupon_type    = :coupon_type,
        product_name   = :product_name,
        face_value     = :face_value,
        expires_at     = CAST(:expires_at AS date),
        usage_note     = :usage_note,
        barcode_masked = :barcode_masked,
        barcode_hash   = :barcode_hash,
        barcode_format = :barcode_format,
        confidence     = CAST(:confidence AS jsonb),
        needs_review   = :needs_review,
        error_code     = NULL,
        error_message  = NULL,
        completed_at   = now()
    WHERE coupon_id = :coupon_id
    """
)

_SQL_MARK_FAILED = text(
    """
    UPDATE coupons SET
        status = 'FAILED', error_code = :code, error_message = :message,
        completed_at = now()
    WHERE coupon_id = :coupon_id
    """
)


def _mark_completed_sync(
    coupon_id: str, data: dict[str, Any], barcode_hash: str | None
) -> str:
    params = {
        "coupon_id": coupon_id,
        "brand_id": data.get("brand_id"),
        "brand_name": data.get("brand_name"),
        "coupon_type": data.get("coupon_type") or "UNKNOWN",
        "product_name": data.get("product_name"),
        "face_value": data.get("face_value"),
        "expires_at": data.get("expires_at"),
        "usage_note": data.get("usage_note"),
        "barcode_masked": data.get("barcode_masked"),
        "barcode_hash": barcode_hash,
        "barcode_format": data.get("barcode_format"),
        "confidence": json.dumps(data.get("confidence") or {}),
        "needs_review": bool(data.get("needs_review")),
    }
    try:
        with get_engine().begin() as conn:
            conn.execute(_SQL_MARK_COMPLETED, params)
        return "COMPLETED"
    except IntegrityError:
        # uq_coupons_barcode 위반 = 같은 바코드의 쿠폰이 이미 등록돼 있다.
        # 조회로 미리 확인하지 않고 제약에 맡긴다. 동시 업로드에서도 안전하다.
        logger.info("중복 쿠폰 감지: %s", coupon_id)
        with get_engine().begin() as conn:
            conn.execute(
                _SQL_MARK_FAILED,
                {
                    "coupon_id": coupon_id,
                    "code": "DUPLICATE_COUPON",
                    "message": "이미 등록된 쿠폰입니다. 보유 쿠폰 목록에서 확인해 주세요.",
                },
            )
        return "DUPLICATE"


async def mark_completed(
    coupon_id: str, data: dict[str, Any], barcode_hash: str | None = None
) -> str:
    """파싱 성공 확정. 바코드 중복이면 'DUPLICATE'를 돌려주고 FAILED로 전환한다."""
    return await asyncio.to_thread(_mark_completed_sync, coupon_id, data, barcode_hash)


def _mark_failed_sync(coupon_id: str, code: str, message: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            _SQL_MARK_FAILED,
            {"coupon_id": coupon_id, "code": code, "message": message},
        )


async def mark_failed(coupon_id: str, code: str, message: str) -> None:
    await asyncio.to_thread(_mark_failed_sync, coupon_id, code, message)


_SQL_DELETE = text("DELETE FROM coupons WHERE coupon_id = :coupon_id")


def _discard_sync(coupon_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(_SQL_DELETE, {"coupon_id": coupon_id})


async def discard(coupon_id: str) -> None:
    """접수 자체가 무효인 경우(형식·용량 위반) 행을 지운다.

    남겨두면 사용자가 이미지를 고쳐 같은 client_upload_id로 재시도할 때
    멱등성 제약에 걸려 실패 상태가 영원히 박제된다.
    """
    await asyncio.to_thread(_discard_sync, coupon_id)


# ══════════════════════════════════════════════════════════════════
# 7. 위치 매칭 (F-02) — 02_DB_SCHEMA.md §7
#    EXPLAIN ANALYZE 로 idx_stores_geog 사용을 확인한 쿼리다.
#    MakePoint 인자 순서는 (경도, 위도). 뒤집으면 서울 좌표가 남극으로 간다.
# ══════════════════════════════════════════════════════════════════
_SQL_MATCH = text(
    """
    SELECT
        s.store_id, s.store_name, s.brand_id, s.store_type, s.source,
        ST_Y(s.geom)::numeric(10,6) AS lat,
        ST_X(s.geom)::numeric(10,6) AS lng,
        ROUND(ST_Distance(
            s.geom::geography,
            ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
        ))::int AS distance_m,
        s.road_address,
        json_agg(
            json_build_object(
                'coupon_id',    c.coupon_id,
                'coupon_type',  c.coupon_type,
                'product_name', c.product_name,
                'face_value',   c.face_value,
                'expires_at',   c.expires_at,
                'days_left',    (c.expires_at - (now() AT TIME ZONE 'Asia/Seoul')::date),
                'usage_note',   c.usage_note
            ) ORDER BY c.expires_at ASC, c.created_at DESC
        ) AS available_coupons
    FROM stores s
    JOIN coupons c
      ON  c.brand_id = s.brand_id
      AND c.uid      = :uid
      AND c.status   = 'COMPLETED'
      AND c.is_used  = false
      AND c.expires_at >= (now() AT TIME ZONE 'Asia/Seoul')::date
    WHERE s.is_active
      AND ST_DWithin(
            s.geom::geography,
            ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
            :radius_m)
    GROUP BY s.store_id
    ORDER BY distance_m ASC
    LIMIT 20
    """
)


def _match_stores_sync(uid: str, lat: float, lng: float, radius_m: int) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            _SQL_MATCH,
            {"uid": uid, "lat": lat, "lng": lng, "radius_m": radius_m},
        ).fetchall()
    out = []
    for r in rows:
        m = dict(r._mapping)
        m["lat"] = float(m["lat"])
        m["lng"] = float(m["lng"])
        out.append(m)
    return out


async def match_stores(
    uid: str, lat: float, lng: float, radius_m: int | None = None
) -> list[dict]:
    """반경 내에서 사용 가능한 쿠폰이 1개 이상 있는 매장만 반환 — 01_API_SPEC.md §5."""
    radius = radius_m or get_settings().search_radius_m
    return await asyncio.to_thread(_match_stores_sync, uid, lat, lng, radius)


# ── 마지막 위치 갱신 (users.last_location) ─────────────────────
_SQL_UPDATE_LOCATION = text(
    """
    UPDATE users SET
        last_location    = ST_SetSRID(ST_MakePoint(:lng, :lat), 4326),
        last_location_at = now()
    WHERE uid = :uid
    """
)


def _update_location_sync(uid: str, lat: float, lng: float) -> None:
    with get_engine().begin() as conn:
        conn.execute(_SQL_UPSERT_USER, {"uid": uid})
        conn.execute(_SQL_UPDATE_LOCATION, {"uid": uid, "lat": lat, "lng": lng})


_SQL_SELECT_LAST_LOCATION = text(
    """
    SELECT ST_Y(last_location) AS lat,
           ST_X(last_location) AS lng,
           last_location_at    AS at
    FROM users
    WHERE uid = :uid AND last_location IS NOT NULL
    """
)


def _get_last_location_sync(uid: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(_SQL_SELECT_LAST_LOCATION, {"uid": uid}).fetchone()
    if row is None or row._mapping["at"] is None:
        return None
    m = row._mapping
    return {"lat": float(m["lat"]), "lng": float(m["lng"]), "at": m["at"]}


async def get_last_location(uid: str) -> dict[str, Any] | None:
    """직전 저장 좌표. 50m 중복 억제와 속도 이상치 판정의 기준점이다."""
    return await asyncio.to_thread(_get_last_location_sync, uid)

async def update_last_location(uid: str, lat: float, lng: float) -> None:
    """이력이 아니라 최신 1건만 덮어쓴다 — 02_DB_SCHEMA.md §0.2의 의도적 축소."""
    await asyncio.to_thread(_update_location_sync, uid, lat, lng)


# ══════════════════════════════════════════════════════════════════
# 8. RAG 규칙 검색 (F-04) — 02_DB_SCHEMA.md §8.5
#    ① brand_id/store_type SQL 사전 필터 → ② 코사인 거리 벡터 검색 (LIMIT top_k)
#    ③ 유사도 임계값 컷은 여기서 하지 않고 호출부(app/services/gemini.py)에서
#       한다 — "찾았는데 못 미쳐서 버림"과 "애초에 후보가 없음"을 로그에서
#       구분하기 위해서다.
# ══════════════════════════════════════════════════════════════════
def _vec_literal(vec: list[float]) -> str:
    """pgvector는 '[0.1,0.2,...]' 형식의 텍스트를 vector로 캐스트한다.

    pgvector-python 패키지를 추가하지 않기 위한 선택이다 (4일 스프린트,
    이미 원시 SQL 스타일을 쓰고 있어 의존성을 하나 더 늘릴 이유가 없다).
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


_SQL_RULES_SEARCH = text(
    """
    SELECT rule_id, content, rule_type, source_name, source_url, verified_by,
           1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
    FROM coupon_rules
    WHERE brand_id IN (:brand_id, '_common')
      AND store_type IN (:store_type, 'NORMAL')
    ORDER BY embedding <=> CAST(:qvec AS vector)
    LIMIT :limit
    """
)


def _search_rules_sync(
    brand_id: str, store_type: str, qvec: list[float], limit: int
) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            _SQL_RULES_SEARCH,
            {
                "brand_id": brand_id,
                "store_type": store_type,
                "qvec": _vec_literal(qvec),
                "limit": limit,
            },
        ).fetchall()
    return [dict(r._mapping) for r in rows]


async def search_rules(
    brand_id: str, store_type: str, qvec: list[float], limit: int | None = None
) -> list[dict[str, Any]]:
    """brand_id(+_common) × store_type(+NORMAL)로 좁힌 뒤 코사인 거리순 top_k."""
    settings = get_settings()
    return await asyncio.to_thread(
        _search_rules_sync, brand_id, store_type, qvec, limit or settings.rag_top_k
    )