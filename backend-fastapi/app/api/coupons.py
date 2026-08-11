"""쿠폰 등록/조회 엔드포인트 — 01_API_SPEC.md §3, §4

C-10: 업로드 1건 = 이미지 최대 3장.
카카오톡 선물하기는 한 쿠폰의 정보가 두 화면에 쪼개져 있다
(상단: 브랜드·상품명·바코드 / 선물 사용 정보: 유효기간·사용가능금액).
한 장만으로는 필수 필드가 안 채워지므로 여러 장을 받아 Gemini 호출 1회로 병합한다.
"""
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse

from app import core
from app.services import gemini

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/coupons", tags=["coupons"])

# 장당 10MB와 별개로 요청 전체에도 상한을 둔다.
# Gemini는 inline 이미지를 요청 본문에 그대로 실어 보내므로, 합계가 커지면
# 파싱 이전에 요청 자체가 거부된다. 3장 × 10MB = 30MB는 그 선을 넘는다.
MAX_TOTAL_IMAGE_BYTES = 15 * 1024 * 1024


@router.post(
    "", status_code=202, response_model=None, summary="쿠폰 등록 (이미지 1~3장 업로드)",
    responses={
        200: {"description": "동일 client_upload_id 재전송 — 기존 리소스 반환"},
        202: {"description": "신규 접수. 파싱은 비동기로 진행된다"},
    },
)
async def create_coupon(
    background: BackgroundTasks,
    images: list[UploadFile] = File(
        ...,
        description=(
            "쿠폰 캡처 이미지 1~3장 (JPEG/PNG/WebP, 장당 최대 10MB). "
            "같은 쿠폰의 서로 다른 화면을 함께 올린다."
        ),
    ),
    client_upload_id: str = Form(..., description="클라이언트 생성 UUID v4 — 멱등성 키"),
    captured_at: str | None = Form(None, description="원본 촬영 시각 (ISO-8601 UTC)"),
    uid: str = Depends(core.get_current_uid),
) -> JSONResponse:
    settings = core.get_settings()

    try:
        uuid.UUID(client_upload_id)
    except ValueError:
        raise core.ApiError(
            422, "VALIDATION_ERROR", "client_upload_id는 UUID 형식이어야 합니다.",
            {"client_upload_id": client_upload_id},
        )

    # 1) 멱등성 확인이 먼저다. 재전송이면 이미지를 다시 읽거나 Gemini를 다시 부르지 않는다.
    record, created = await core.get_or_create(uid, client_upload_id, captured_at)
    if not created:
        logger.info("중복 업로드 감지: %s", record.coupon_id)
        return JSONResponse(
            status_code=200,
            content=gemini.to_status_response(record).model_dump(),
        )

    async def rollback(
        status_code: int, code: str, message: str, detail: dict | None = None
    ) -> core.ApiError:
        """접수 행을 지우고 돌려줄 예외를 만든다.

        행을 남기면 사용자가 이미지를 고쳐 같은 client_upload_id로 재시도할 때
        멱등성 제약에 걸려 실패 상태가 영원히 박제된다.
        """
        await core.discard(record.coupon_id)
        return core.ApiError(status_code, code, message, detail)

    # 2) 장수 → 형식 → 크기 순으로 검증한다.
    #    크기를 형식보다 먼저 보면 HEIC 10MB짜리가 413으로 나가서
    #    앱이 "형식이 잘못됐다" 분기를 타지 못한다.
    if not images:
        raise await rollback(422, "VALIDATION_ERROR", "이미지 파일이 필요합니다.")

    if len(images) > settings.max_images:
        raise await rollback(
            422, "TOO_MANY_IMAGES",
            f"이미지는 최대 {settings.max_images}장까지 올릴 수 있습니다.",
            {"received": len(images), "max": settings.max_images},
        )

    payloads: list[tuple[bytes, str]] = []
    total_bytes = 0
    for idx, image in enumerate(images):
        content_type = (image.content_type or "").split(";")[0].strip().lower()
        if content_type not in settings.allowed_image_types:
            raise await rollback(
                415, "INVALID_IMAGE_FORMAT",
                "지원하지 않는 이미지 형식입니다. JPEG, PNG, WebP만 업로드할 수 있습니다.",
                {"index": idx, "received_content_type": image.content_type},
            )

        data = await image.read()
        if not data:
            raise await rollback(
                422, "VALIDATION_ERROR", f"{idx + 1}번째 이미지 파일이 비어 있습니다.",
                {"index": idx},
            )
        if len(data) > settings.max_image_bytes:
            raise await rollback(
                413, "IMAGE_TOO_LARGE", "이미지 용량이 10MB를 초과했습니다.",
                {"index": idx, "received_bytes": len(data)},
            )

        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise await rollback(
                413, "IMAGE_TOO_LARGE",
                "이미지 전체 용량이 너무 큽니다. 장수를 줄이거나 해상도를 낮춰 주세요.",
                {"total_bytes": total_bytes, "limit_bytes": MAX_TOTAL_IMAGE_BYTES},
            )

        payloads.append((data, content_type))

    logger.info(
        "업로드 접수: %s (이미지 %d장, %.1fMB)",
        record.coupon_id, len(payloads), total_bytes / 1024 / 1024,
    )

    # 3) 응답을 먼저 돌려주고 파싱은 뒤에서 돌린다.
    #    ⚠️ Cloud Run 기본 설정은 응답 후 CPU를 회수하므로 BackgroundTasks가 중단될 수 있다.
    #       배포 시 --no-cpu-throttling 적용, 또는 Cloud Tasks로 분리할 것 (§3 메모).
    background.add_task(gemini.process_coupon, record.coupon_id, payloads)

    body = core.CouponAcceptedResponse(
        coupon_id=record.coupon_id,
        client_upload_id=record.client_upload_id,
        created_at=core.iso(record.created_at),
        poll_after_ms=settings.poll_after_ms,
    )
    return JSONResponse(status_code=202, content=body.model_dump())


@router.get(
    "/{coupon_id}", response_model=core.CouponStatusResponse,
    summary="쿠폰 상태 조회 (폴링)",
)
async def get_coupon(
    coupon_id: str,
    uid: str = Depends(core.get_current_uid),
) -> core.CouponStatusResponse:
    record = await core.get_coupon(coupon_id)
    if record is None:
        raise core.ApiError(404, "RESOURCE_NOT_FOUND", "존재하지 않는 쿠폰입니다.")
    if record.uid != uid:   # 소유권 검증은 예외 없이 항상 수행한다
        raise core.ApiError(403, "FORBIDDEN", "접근 권한이 없습니다.")
    return gemini.to_status_response(record)