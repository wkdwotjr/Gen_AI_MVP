"""Gemini Vision 호출과 파싱 결과 후처리.

**중요한 경계 하나만 지킨다:**
Gemini는 "이미지에 보이는 글자"를 뽑고, 정해진 브랜드 코드 목록 중에서 고르기만 한다.
날짜 계산·마스킹·신뢰도 판정 같은 결정론적 로직은 `process_coupon`이 파이썬으로 한다.
LLM에 계산을 시키면 같은 이미지가 매번 다른 값을 뱉어 파싱 정확도 측정이 불가능해진다.

브랜드 코드는 `config/brands.json`이 단일 출처다. 이 파일과 `load_stores.py`가
같은 목록을 봐야 `coupons.brand_id ⋈ stores.brand_id` 조인이 성립한다.

**다중 이미지 (C-10):**
업로드 1건에 이미지가 1~3장 들어온다. 전부 한 번에 Gemini에 넣어 하나의 결과로
병합시킨다(API 호출 1회 유지). 서로 다른 쿠폰이 섞여 들어오면 잘못 병합된 쿠폰이
DB에 남고 위치 알림이 엉뚱한 브랜드로 나가므로, 모델 판정(`is_same_coupon`)과
서버 재검증(`_mismatch_reason`)을 2단으로 건다.
"""
import asyncio
import hashlib
import json
import logging
import random
import re
from datetime import date
from pathlib import Path

from app import core

logger = logging.getLogger(__name__)


class GeminiUnavailable(Exception):
    """API 장애·타임아웃·인증 실패 → UPSTREAM_UNAVAILABLE"""


class GeminiParseError(Exception):
    """응답이 스키마에 맞지 않음 → PARSE_FAILED"""


# ══════════════════════════════════════════════════════════════════
# 1. 브랜드 코드 — config/brands.json 이 단일 출처
#
#    ★ 이 블록이 RESPONSE_SCHEMA보다 반드시 위에 있어야 한다.
#      아래에서 BRAND_IDS를 enum으로 주입하기 때문이다.
#
#    1차 판정: Gemini 가 enum 으로 분류 (표기 변형에 강함)
#    2차 판정: 별칭 문자열 매칭 (Gemini 가 UNKNOWN 을 반환했을 때만)
# ══════════════════════════════════════════════════════════════════
# app/services/gemini.py  →  parents[2] == backend-fastapi/
_BRANDS_PATH = Path(__file__).resolve().parents[2] / "config" / "brands.json"
_BRANDS = json.loads(_BRANDS_PATH.read_text(encoding="utf-8"))["brands"]

BRAND_IDS: list[str] = [b["brand_id"] for b in _BRANDS]
_BRAND_ID_SET = set(BRAND_IDS)

COUPON_TYPES = ["PRODUCT", "AMOUNT", "DISCOUNT", "UNKNOWN"]


def _norm(s: str) -> str:
    """비교용 정규화: 공백·기호 제거 후 대문자."""
    return re.sub(r"[\s\-_'’()·.]", "", s).upper()


# 긴 별칭을 먼저 검사한다. '이마트'가 '이마트24'를 가로채는 유형의 오분류 방지.
_ALIAS_PAIRS: list[tuple[str, str]] = sorted(
    ((_norm(a), b["brand_id"]) for b in _BRANDS for a in b["aliases"]),
    key=lambda p: len(p[0]),
    reverse=True,
)


def resolve_brand_id(llm_brand_id: str | None, brand_name: str | None) -> str | None:
    """Gemini 분류 → 별칭 매칭 순으로 시도. 둘 다 실패하면 None(정상 상태).

    None은 오류가 아니라 "아직 지원하지 않는 브랜드"를 뜻한다.
    다만 위치 매칭(F-02) 대상에서는 제외된다 — 02_DB_SCHEMA.md §4 참조.
    """
    if llm_brand_id and llm_brand_id in _BRAND_ID_SET:
        return llm_brand_id
    if not brand_name:
        return None
    key = _norm(brand_name)
    for alias, brand_id in _ALIAS_PAIRS:
        if alias == key or alias in key:
            logger.info("별칭 폴백 적중: %r → %s", brand_name, brand_id)
            return brand_id
    logger.info("브랜드 미매칭: %r (지원 목록에 없음)", brand_name)
    return None


# ══════════════════════════════════════════════════════════════════
# 2. 프롬프트와 출력 스키마
# ══════════════════════════════════════════════════════════════════
_BRAND_TABLE = "\n".join(
    f"   - {b['brand_id']} : {b['display']}" for b in _BRANDS
)

SYSTEM_INSTRUCTION = f"""당신은 한국 모바일 쿠폰(기프티콘) 이미지에서 정보를 추출하는 파서다.
아래 규칙을 반드시 지킨다.

1. 이미지에 실제로 보이는 글자만 사용한다. 추측하거나 지어내지 않는다.
2. 읽을 수 없는 항목은 반드시 null로 둔다. 빈 문자열("")을 쓰지 않는다.
3. brand_name은 상품 판매 브랜드명만 적는다. "카카오톡 선물하기", "쿠폰함" 같은
   전송 플랫폼 이름은 브랜드가 아니다.
4. expires_at은 "유효기간", "사용기한", "교환처 유효기간"으로 표시된 날짜다.
   반드시 YYYY-MM-DD 형식으로 변환한다. "2026.09.30" → "2026-09-30".
   구매일자나 주문일자를 유효기간으로 착각하지 않는다.
5. barcode_number는 바코드 아래 표기된 숫자 원문 그대로 적는다. 공백/하이픈은 제거한다.
6. confidence는 각 항목을 얼마나 확실히 읽었는지를 0.0~1.0으로 적는다.
   글자가 흐리거나 잘려 있으면 낮은 값을 준다. 항목이 null이면 0.0을 준다.
7. 쿠폰/기프티콘 화면이 하나도 없으면 is_coupon을 false로 하고 나머지는 모두 null로 둔다.
   (단 brand_id는 null 대신 UNKNOWN을 쓴다.)
8. brand_id는 아래 목록에서 정확히 하나를 고른다.
{_BRAND_TABLE}
   표기가 달라도 같은 브랜드면 해당 코드를 고른다.
   예: "메가MGC커피", "메가엠지씨커피", "MEGA MGC COFFEE" → megamgc
   목록에 없는 브랜드이거나 확신이 서지 않으면 반드시 UNKNOWN을 고른다.
   비슷해 보인다는 이유로 다른 브랜드를 고르지 않는다. 틀린 코드는 사용자를
   엉뚱한 매장으로 보내지만, UNKNOWN은 아무 피해도 주지 않는다.
9. coupon_type: 특정 상품명이 있으면 PRODUCT, 상품명 없이 금액만 있으면 AMOUNT,
   "○% 할인"·"○원 할인"이면 DISCOUNT, 판단이 서지 않으면 UNKNOWN.
   face_value는 "사용가능금액"·"금액권" 등에 표시된 금액을 숫자만 적는다.
   PRODUCT에도 금액이 함께 표시될 수 있다. 그 경우 둘 다 채운다.
10. usage_note: **"교환처" 줄을 반드시 별도로 확인한다.** 브랜드/상품명을 정할 때
    이미 봤더라도, 그 줄 안에 괄호나 슬래시로 예외가 적혀 있는지 다시 확인해야
    한다. 예: "교환처 : 이마트(안양/부천점제외/트레이더스/노브랜드(직영))" →
    괄호 안의 "안양/부천점제외" 부분이 사용 제한이다. 이런 제외·한정 표현
    ("○○ 제외", "○○에서만 사용 가능", "타 쿠폰과 중복 사용 불가" 등)이 하나라도
    있으면 절대 놓치지 말고 usage_note에 한국어 한 문장으로 옮겨 적는다.
    (이 이마트 예시라면 usage_note = "안양점과 부천점에서는 사용할 수 없다.")
    그런 제외·한정 표현이 전혀 없으면 null. 교환처가 단순히 "전국 매장" 처럼
    제한 없이 적혀 있으면 그것도 null이다(제한이 없다는 것도 사용자에게
    말해줄 새로운 정보가 아니다).
    이 필드는 **이 쿠폰 한 장에 적힌 사실**이다. 같은 브랜드의 다른 쿠폰에도
    똑같이 적용된다고 확대 해석하지 않는다 — 보이는 문구만 그대로 옮긴다.

── 이미지가 여러 장일 때 ──────────────────────────────────────

11. 여러 장은 대개 **같은 쿠폰의 서로 다른 화면**이다. 카카오톡 선물하기는
    상단 화면(브랜드·상품명·바코드)과 "선물 사용 정보" 화면(유효기간·사용가능금액·교환처)이
    분리돼 있다. 각 화면에서 읽은 값을 하나로 합쳐 최상위 필드에 채운다.
    한 화면에만 있는 값도 반드시 채운다.
12. 같은 항목이 두 화면에 다르게 보이면:
    - expires_at은 "선물 사용 정보"/"유효기간" 화면 쪽을 우선한다.
      상단에 보이는 날짜는 발송일·주문일인 경우가 있다.
    - 그 밖의 항목은 더 또렷하게 읽히는 쪽을 택한다.
13. per_image에는 이미지별로 그 화면에서만 읽은 값을 그대로 적는다.
    합치기 전의 값이다. 브랜드를 못 읽은 화면은 UNKNOWN, 바코드가 없는 화면은 null.
14. is_same_coupon: 주어진 이미지들이 같은 쿠폰이면 true. 브랜드가 서로 다르거나
    바코드 숫자가 서로 달라 명백히 다른 쿠폰이면 false로 하고 mismatch_reason에
    한 줄로 이유를 적는다. 확신이 없으면 true로 두지 말고 false로 한다 —
    잘못 합친 쿠폰은 사용자를 엉뚱한 매장으로 보낸다.

출력은 지정된 JSON 스키마만 반환한다. 설명 문장이나 마크다운 코드펜스를 붙이지 않는다."""


def _user_prompt(n_images: int) -> str:
    if n_images == 1:
        return "이 이미지에서 쿠폰 정보를 추출해줘."
    return (
        f"이미지 {n_images}장이 순서대로 주어졌다. 같은 쿠폰의 서로 다른 화면일 가능성이 높다. "
        "각 화면에서 읽은 값을 최상위 필드 하나로 합치고, per_image에는 합치기 전 "
        "이미지별 값을 그대로 적어줘."
    )


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_coupon": {"type": "boolean"},
        "brand_name": {"type": "string", "nullable": True},
        # enum 제약이 핵심이다. 목록 밖 값은 SDK 레벨에서 나올 수 없으므로
        # 'megamgc' / 'mega_mgc' 같은 표기 흔들림이 구조적으로 차단된다.
        "brand_id": {"type": "string", "enum": BRAND_IDS + ["UNKNOWN"]},
        "product_name": {"type": "string", "nullable": True},
        # coupon_type / face_value 가 스키마에 없으면 응답에 절대 나오지 않는다.
        # (C-9로 컬럼은 추가했는데 값이 안 채워지던 원인)
        "coupon_type": {"type": "string", "enum": COUPON_TYPES},
        "face_value": {"type": "integer", "nullable": True},
        "expires_at": {"type": "string", "nullable": True},
        # 이 쿠폰 한 장에 적힌 이용 제한 문구. 브랜드 전체 정책이 아니다 —
        # coupon_rules(RAG)로 색인하지 않고 이 쿠폰의 브리핑에서만 참고한다.
        "usage_note": {"type": "string", "nullable": True},
        "barcode_number": {"type": "string", "nullable": True},
        "barcode_format": {
            "type": "string",
            "nullable": True,
            "enum": ["CODE128", "EAN13", "QR", "UNKNOWN"],
        },
        "confidence": {
            "type": "object",
            "properties": {
                "brand": {"type": "number"},
                "product_name": {"type": "number"},
                "expires_at": {"type": "number"},
            },
            "required": ["brand", "product_name", "expires_at"],
        },
        # ── 다중 이미지 판정 (C-10) ──
        "is_same_coupon": {"type": "boolean"},
        "mismatch_reason": {"type": "string", "nullable": True},
        "per_image": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "brand_id": {"type": "string", "enum": BRAND_IDS + ["UNKNOWN"]},
                    "barcode_number": {"type": "string", "nullable": True},
                },
                "required": ["index", "brand_id"],
            },
        },
    },
    "required": ["is_coupon", "confidence", "is_same_coupon"],
}


# ══════════════════════════════════════════════════════════════════
# 3. Gemini 호출
# ══════════════════════════════════════════════════════════════════
def _mock_result(n_images: int) -> dict:
    """자격증명 미설정 시 사용. 키 없이도 업로드→폴링 흐름을 검증할 수 있게 한다."""
    return {
        "is_coupon": True,
        "brand_name": "메가MGC커피",
        "brand_id": "megamgc",
        "product_name": "(ICE)아메리카노",
        "coupon_type": "PRODUCT",
        "face_value": 2000,
        "expires_at": "2027-03-17",
        "usage_note": None,
        "barcode_number": "501412348004",
        "barcode_format": "CODE128",
        "confidence": {"brand": 1.0, "product_name": 0.95, "expires_at": 1.0},
        "is_same_coupon": True,
        "mismatch_reason": None,
        "per_image": [
            {"index": i, "brand_id": "megamgc",
             "barcode_number": "501412348004" if i == 0 else None}
            for i in range(n_images)
        ],
    }


def _clean_int(value) -> int | None:
    """'2,000원' 같은 표기도 받아낸다. 0·음수는 CHECK 제약에 걸리므로 None으로."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
    else:
        digits = re.sub(r"\D", "", str(value))
        if not digits:
            return None
        number = int(digits)
    return number if number > 0 else None


def _validate(payload) -> dict:
    if not isinstance(payload, dict) or not {"is_coupon", "confidence"} <= payload.keys():
        raise GeminiParseError("필수 키 누락")
    conf = payload.get("confidence")
    if not isinstance(conf, dict):
        raise GeminiParseError("confidence 형식 오류")
    for key in ("brand", "product_name", "expires_at"):
        try:
            conf[key] = max(0.0, min(1.0, float(conf.get(key, 0.0))))
        except (TypeError, ValueError):
            conf[key] = 0.0

    if payload.get("coupon_type") not in COUPON_TYPES:
        payload["coupon_type"] = "UNKNOWN"
    payload["face_value"] = _clean_int(payload.get("face_value"))

    # 다중 이미지 판정 필드. 없으면 "같은 쿠폰"으로 보지 않고 명시적으로 채운다.
    payload["is_same_coupon"] = payload.get("is_same_coupon") is not False
    per_image = payload.get("per_image")
    payload["per_image"] = per_image if isinstance(per_image, list) else []
    return payload


def _get_client():
    """Vertex/API Key 두 인증 경로를 공통으로 만든다. 임베딩·브리핑 생성도 이걸 쓴다."""
    settings = core.get_settings()
    try:
        from google import genai
    except ImportError as exc:
        raise GeminiUnavailable("google-genai 미설치") from exc

    if settings.use_vertex:
        # ADC(gcloud auth application-default login)로 인증한다.
        # 배포 시에는 Cloud Run 서비스 계정이 자동으로 주입되므로 코드 변경이 없다.
        return genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
        )
    return genai.Client(api_key=settings.gemini_api_key)


def _call_sync(images: list[tuple[bytes, str]]) -> dict:
    """이미지 전체를 한 번의 호출에 담는다. 장수가 늘어도 API 호출은 1회다."""
    settings = core.get_settings()
    try:
        from google.genai import types
    except ImportError as exc:
        raise GeminiUnavailable("google-genai 미설치") from exc

    client = _get_client()

    parts = [
        types.Part.from_bytes(data=data, mime_type=mime) for data, mime in images
    ]
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[*parts, _user_prompt(len(images))],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.0,          # 추출 작업이므로 창의성은 해롭다
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            ),
        )
    except Exception as exc:
        logger.warning("Gemini 호출 실패: %s", exc)
        raise GeminiUnavailable(str(exc)) from exc

    text = (response.text or "").strip()
    if not text:
        raise GeminiParseError("빈 응답")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Gemini JSON 파싱 실패: %.200s", text)
        raise GeminiParseError("JSON 디코딩 실패") from exc


async def parse_coupon(images: list[tuple[bytes, str]]) -> dict:
    """이미지 1~3장 → 병합된 dict. 스키마 위반 시 1회 재시도한다 (§4 PARSE_FAILED)."""
    if not images:
        raise GeminiParseError("이미지가 없습니다")

    settings = core.get_settings()
    if settings.use_mock_gemini:
        logger.warning("Gemini 자격증명 미설정 — MOCK 응답을 반환합니다.")
        await asyncio.sleep(1.0)   # 폴링 흐름을 실제와 비슷하게 재현
        return _validate(_mock_result(len(images)))

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_call_sync, images),
                timeout=settings.gemini_timeout_s,
            )
            return _validate(raw)
        except GeminiParseError as exc:
            last_error = exc
            logger.info("파싱 실패, 재시도 %d/2", attempt)
        except asyncio.TimeoutError as exc:
            raise GeminiUnavailable("timeout") from exc
    raise GeminiParseError(str(last_error))


# ══════════════════════════════════════════════════════════════════
# 4. 후처리 — 여기서부터는 LLM이 관여하지 않는다
# ══════════════════════════════════════════════════════════════════
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def mask_barcode(number: str | None) -> str | None:
    """앞 4자리와 뒤 4자리만 남긴다. 원문은 응답에 절대 싣지 않는다 (§4)."""
    if not number:
        return None
    digits = re.sub(r"\D", "", number)
    if len(digits) <= 8:
        return "*" * len(digits)
    return f"{digits[:4]}{'*' * (len(digits) - 8)}{digits[-4:]}"


def parse_expires_at(value: str | None) -> date | None:
    if not value or not _DATE_RE.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _mismatch_reason(raw: dict) -> str | None:
    """서버 2차 검증. 다른 쿠폰이 섞였다고 판단되면 사유 문자열, 아니면 None.

    모델 판정(is_same_coupon)과 서버 판정 중 **하나라도** 걸리면 막는다.
    한쪽에서만 바코드가 읽힌 경우는 통과시킨다 — 선물 사용 정보 화면에
    바코드가 없는 것은 정상이고, 여기서 막으면 C-10 자체가 무의미해진다.
    """
    if raw.get("is_same_coupon") is False:
        return raw.get("mismatch_reason") or "모델이 서로 다른 쿠폰으로 판정"

    per_image = raw.get("per_image") or []

    brands = {
        p.get("brand_id") for p in per_image
        if isinstance(p, dict) and p.get("brand_id") and p.get("brand_id") != "UNKNOWN"
    }
    if len(brands) > 1:
        return f"이미지 간 브랜드 불일치: {sorted(brands)}"

    barcodes = set()
    for p in per_image:
        if not isinstance(p, dict):
            continue
        digits = re.sub(r"\D", "", p.get("barcode_number") or "")
        if len(digits) >= 8:      # 너무 짧은 숫자는 바코드가 아닐 수 있다
            barcodes.add(digits)
    if len(barcodes) > 1:
        # 바코드 숫자 자체는 로그에 남기지 않는다.
        return "이미지 간 바코드 불일치"

    return None


async def process_coupon(coupon_id: str, images: list[tuple[bytes, str]]) -> None:
    """백그라운드 파싱 작업. 예외를 밖으로 던지지 않고 항상 상태를 확정시킨다."""
    try:
        raw = await parse_coupon(images)
    except GeminiUnavailable:
        await core.mark_failed(
            coupon_id, "UPSTREAM_UNAVAILABLE",
            "AI 분석 서버에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        )
        return
    except GeminiParseError:
        await core.mark_failed(
            coupon_id, "PARSE_FAILED",
            "쿠폰 정보를 자동으로 읽지 못했습니다. 직접 입력해 주세요.",
        )
        return
    except Exception as exc:   # 예상 못한 오류로 카드가 PROCESSING에 묶이지 않게 한다
        logger.exception("파싱 처리 중 오류: %s", exc)
        await core.mark_failed(coupon_id, "PARSE_FAILED", "쿠폰 분석 중 오류가 발생했습니다.")
        return

    if not raw.get("is_coupon"):
        await core.mark_failed(
            coupon_id, "NOT_A_COUPON",
            "쿠폰 이미지로 보이지 않습니다. 기프티콘 화면 전체가 나오도록 다시 캡처해 주세요.",
        )
        return

    # 여러 장이 서로 다른 쿠폰이면 병합 결과를 저장하지 않는다.
    if len(images) > 1 and (reason := _mismatch_reason(raw)) is not None:
        logger.info("다중 이미지 불일치 (%s): %s", coupon_id, reason)
        await core.mark_failed(
            coupon_id, "MULTIPLE_COUPONS",
            "서로 다른 쿠폰이 섞여 있는 것 같습니다. 한 쿠폰씩 나눠서 올려 주세요.",
        )
        return

    brand_name = (raw.get("brand_name") or "").strip()
    expires = parse_expires_at(raw.get("expires_at"))

    # 브랜드와 유효기간은 서비스 성립 조건이다. 하나라도 없으면 매칭도 알림도 불가능하다.
    if not brand_name or expires is None:
        missing = [n for n, ok in (("브랜드", brand_name), ("유효기간", expires)) if not ok]
        hint = (
            " 카카오톡 선물하기라면 '선물 사용 정보' 화면도 함께 올려 주세요."
            if len(images) == 1 else ""
        )
        await core.mark_failed(
            coupon_id, "REQUIRED_FIELD_MISSING",
            f"{', '.join(missing)}을(를) 읽지 못했습니다. 직접 입력해 주세요.{hint}",
        )
        return

    conf = core.Confidence(**raw["confidence"])
    coupon_type = raw.get("coupon_type") or "UNKNOWN"
    data = core.CouponData(
        brand_id=resolve_brand_id(raw.get("brand_id"), brand_name),
        brand_name=brand_name,
        coupon_type=coupon_type,
        product_name=(raw.get("product_name") or None),
        face_value=raw.get("face_value"),
        expires_at=expires.isoformat(),
        days_left=(expires - core.today_kst()).days,
        usage_note=(raw.get("usage_note") or None),
        barcode_masked=mask_barcode(raw.get("barcode_number")),
        barcode_format=(raw.get("barcode_format") or None),
        is_used=False,
        confidence=conf,
        needs_review=core.compute_needs_review(coupon_type, conf),
    )
    result = await core.mark_completed(
        coupon_id,
        data.model_dump(),
        barcode_hash=core.hash_barcode(raw.get("barcode_number")),
    )
    if result == "DUPLICATE":
        logger.info("중복 쿠폰으로 처리됨: %s", coupon_id)


def to_status_response(record: core.CouponRecord) -> core.CouponStatusResponse:
    """저장 레코드 → §4 조회 응답."""
    data = None
    if record.data is not None:
        data = core.CouponData(**record.data)
        # days_left는 조회 시점 기준으로 다시 계산한다 (하루 지나도 카드가 정확하도록).
        if (expires := parse_expires_at(data.expires_at)) is not None:
            data.days_left = (expires - core.today_kst()).days

    return core.CouponStatusResponse(
        coupon_id=record.coupon_id,
        status=record.status,
        created_at=core.iso(record.created_at),
        completed_at=core.iso(record.completed_at),
        data=data,
        error=core.CouponError(**record.error) if record.error else None,
    )


# ══════════════════════════════════════════════════════════════════
# 5. 임베딩 (F-04) — 02_DB_SCHEMA.md §8
#    coupon_rules 적재(scripts/index_rules.py)와 검색 질의(app/api/rules.py,
#    app/api/locations.py) 양쪽이 이 함수 하나를 쓴다. gemini-embedding-001은
#    비대칭 모델이라 "저장할 문서"와 "찾는 질문"에 task_type을 다르게 줘야
#    같은 벡터 공간에서 검색 품질이 나온다.
# ══════════════════════════════════════════════════════════════════
def _mock_embedding(text: str, dim: int) -> list[float]:
    """자격증명이 없어도 벡터 검색 SQL·정렬 경로는 확인할 수 있게 한다.

    의미 있는 임베딩이 아니다 — 같은 문자열이면 항상 같은 벡터가 나온다는
    것만 보장한다 (해시 시드). 실제 유사도 순위는 신뢰하지 않는다.
    """
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    rng = random.Random(seed)
    vec = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def _embed_sync(text: str, task_type: str) -> list[float]:
    from google.genai import types

    settings = core.get_settings()
    client = _get_client()
    response = client.models.embed_content(
        model=settings.embedding_model,
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.embedding_dim,
        ),
    )
    return list(response.embeddings[0].values)


async def embed_text(text: str, *, is_query: bool) -> list[float]:
    """RETRIEVAL_DOCUMENT(적재 시) / RETRIEVAL_QUERY(검색 시) 비대칭 임베딩.

    실패·타임아웃 시 GeminiUnavailable을 던진다. 호출부가 "규칙 없음"으로
    안전하게 처리한다 — 검색 실패를 사용 가능/불가 어느 쪽으로도 단정하지 않는다.
    """
    settings = core.get_settings()
    if settings.use_mock_gemini:
        return _mock_embedding(text, settings.embedding_dim)
    task_type = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_embed_sync, text, task_type),
            timeout=settings.gemini_timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise GeminiUnavailable("임베딩 timeout") from exc
    except Exception as exc:
        logger.warning("임베딩 실패: %s", exc)
        raise GeminiUnavailable(str(exc)) from exc


# ══════════════════════════════════════════════════════════════════
# 6. 브리핑 생성 (F-03) — 01_API_SPEC.md §5, 02_DB_SCHEMA.md §8.6
#    실패·타임아웃이면 예외를 던진다. TEMPLATE 폴백 문장은 여기서 만들지
#    않는다 — app/api/locations.py의 template_briefing()이 담당한다.
# ══════════════════════════════════════════════════════════════════
BRIEFING_SYSTEM_INSTRUCTION = """당신은 쿠폰콕 앱이 사용자 근처 매장을 안내할 때 보여줄
한국어 브리핑 문장을 만든다. 다음을 반드시 지킨다.

1. 100자 이내 한두 문장으로 쓴다. 존댓말을 쓰되 광고 문구처럼 딱딱하지 않게 쓴다.
2. 입력으로 주어진 사실(매장명, 거리, 보유 쿠폰, 쿠폰별 usage_note, 확인된 규칙)만
   사용한다. 주어지지 않은 정보(가격, 다른 매장, 재고, 할인율 등)를 지어내지 않는다.
3. "확인된 규칙"이 비어 있고 해당 쿠폰의 usage_note도 없으면, 이 매장에서 쿠폰을
   실제로 쓸 수 있는지 여부를 단정하지 않는다. "사용 가능합니다" 같은 확답을
   쓰지 않는다. 매장·쿠폰 정보만 안내한다.
4. "확인된 규칙"에 rule_type이 EXCLUSION(사용 불가)인 항목이 있으면 반드시
   반영한다. 사용 불가 조건이 있는 매장을 사용 가능하다고 안내하면 안 된다.
5. 각 규칙의 verified가 false면 미검증 정보다. "~일 수 있어요", "~로 안내되어
   있어요" 같은 완곡한 표현을 쓴다. 단정형(~입니다/~됩니다)을 쓰지 않는다.
   verified가 true인 규칙만 단정형으로 말해도 된다.
6. 쿠폰의 usage_note는 "확인된 규칙"과 성격이 다르다 — 그 브랜드 전체의 정책이
   아니라 **이 쿠폰 한 장에 적힌 조건**이다. usage_note를 말할 때는 "이 쿠폰은",
   "이 쿠폰에는" 처럼 해당 쿠폰에 한정해서 말하고, 같은 브랜드의 다른 쿠폰에도
   똑같이 적용된다고 일반화하지 않는다. (예: "이 쿠폰은 안양점에서 사용할 수
   없다고 적혀 있어요" O / "이 브랜드는 안양점에서 사용할 수 없어요" X)
7. 문장만 출력한다. 설명, 마크다운, 따옴표를 붙이지 않는다."""


def _briefing_context(match: dict, rules: list[dict]) -> str:
    coupons = match.get("available_coupons") or []
    payload = {
        "store_name": match["store_name"],
        "distance_m": match["distance_m"],
        "coupons": [
            {
                "product_name": c.get("product_name"),
                "coupon_type": c.get("coupon_type"),
                "face_value": c.get("face_value"),
                "days_left": c.get("days_left"),
                # 이 쿠폰 한 장에 적힌 이용조건. brand_rules와 달리 이 특정
                # 쿠폰에만 해당하는 사실이다 — 아래 rules(브랜드 공통 정책)와
                # 섞어서 일반화하면 안 된다.
                "usage_note": c.get("usage_note"),
            }
            for c in coupons
        ],
        "rules": [
            {
                "content": r["content"],
                "rule_type": r.get("rule_type"),
                "verified": r.get("verified_by") is not None,
            }
            for r in rules
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _generate_briefing_sync(match: dict, rules: list[dict]) -> str:
    from google.genai import types

    settings = core.get_settings()
    client = _get_client()
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[_briefing_context(match, rules)],
        config=types.GenerateContentConfig(
            system_instruction=BRIEFING_SYSTEM_INSTRUCTION,
            temperature=0.4,
            response_mime_type="text/plain",
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise GeminiParseError("빈 브리핑 응답")
    return text


async def generate_briefing_text(match: dict, rules: list[dict]) -> str:
    """MOCK 모드·실패·타임아웃이면 예외를 던진다 — 호출부가 TEMPLATE로 폴백한다."""
    settings = core.get_settings()
    if settings.use_mock_gemini:
        raise GeminiUnavailable("MOCK 모드에서는 브리핑을 생성하지 않는다")
    try:
        text = await asyncio.wait_for(
            asyncio.to_thread(_generate_briefing_sync, match, rules),
            timeout=settings.briefing_timeout_s,
        )
    except asyncio.TimeoutError as exc:
        raise GeminiUnavailable("브리핑 생성 timeout") from exc
    except (GeminiUnavailable, GeminiParseError):
        raise
    except Exception as exc:
        raise GeminiUnavailable(str(exc)) from exc
    return text[:100]