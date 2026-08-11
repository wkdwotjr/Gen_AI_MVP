"""위치 전송 및 매장 매칭 — 01_API_SPEC.md §5

**A안 (C-13): 배열로 받되 최신 유효 좌표 1건으로만 매칭한다.**

`location_points` 테이블을 두지 않아(02_DB_SCHEMA §0.2) 배열을 순차 판정해도
중간 결과가 저장되지 않는다. 즉 배열 내부를 훑어 정교하게 판정해봐야 다음 요청에
아무 영향을 주지 못한다. 그래서:

- 좌표별로 판정 가능한 필터(범위·정확도·신선도)는 **모든 좌표에 적용**한다.
- 직전 좌표가 필요한 필터(50m 중복 억제·속도 이상치)는 `users.last_location`
  **1건을 기준으로만** 판정한다.

브리핑(F-03)은 매장별로 F-04 RAG 검색(`rag_rules_for`) → Gemini 문장 생성
(`build_briefing`)을 거친다. 검색·생성 어느 쪽이 실패해도 `template_briefing()`
폴백으로 떨어진다 — LLM 호출 실패 시 경로는 §5 규정대로 유지한다.
"""
import asyncio
import logging
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import core
from app.services import gemini

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/locations", tags=["locations"])

MAX_POINTS = 50            # §5 요청당 좌표 상한
MAX_ACCURACY_M = 100.0     # 이보다 부정확하면 폐기 (지하·실내 GPS 음영)
STALE_AFTER_S = 600        # 10분보다 오래된 좌표는 매칭에 쓰지 않는다
DUP_SUPPRESS_M = 50.0      # 직전 저장 좌표와 이 거리 이내면 DB 갱신 생략
MAX_SPEED_KMH = 300.0      # 이보다 빠르면 좌표 이상치


# ══════════════════════════════════════════════════════════════════
# 요청 / 응답 스키마
# ══════════════════════════════════════════════════════════════════
PointSource = Literal["PERIODIC", "GEOFENCE_ENTER", "MANUAL_REFRESH"]


class LocationPoint(BaseModel):
    # lat/lng에 pydantic 제약을 걸지 않는다. 걸면 VALIDATION_ERROR로 나가는데
    # §5는 범위 위반을 INVALID_COORDINATE로 구분하도록 정하고 있다.
    lat: float
    lng: float
    accuracy_m: float
    recorded_at: str
    source: PointSource = "PERIODIC"


class LocationRequest(BaseModel):
    points: list[LocationPoint]


class RejectedReason(BaseModel):
    index: int
    reason: str


class BriefingRule(BaseModel):
    rule_id: str
    content: str
    source_name: str
    similarity: float


class Briefing(BaseModel):
    text: str
    generated_by: Literal["GEMINI", "TEMPLATE"]
    rules: list[BriefingRule] = []


class StoreMatch(BaseModel):
    store_id: str
    store_name: str
    brand_id: str
    store_type: str
    lat: float
    lng: float
    distance_m: int
    road_address: str | None
    source: str
    available_coupons: list[dict[str, Any]]
    briefing: Briefing | None = None


class NotificationResult(BaseModel):
    sent: bool
    reason: str


class LocationResponse(BaseModel):
    accepted: int
    rejected: int
    rejected_reasons: list[RejectedReason] = []
    search_radius_m: int
    matches: list[StoreMatch] = []
    notification: NotificationResult


# ══════════════════════════════════════════════════════════════════
# 좌표 계산
# ══════════════════════════════════════════════════════════════════
def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리(m). 반경 검색은 PostGIS가 하고, 여기서는
    직전 좌표와의 비교(50m·속도)에만 쓴다. 파이썬 계산으로 충분한 정밀도다."""
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def parse_recorded_at(value: str) -> datetime | None:
    """ISO-8601 UTC(Z 접미사) → aware datetime. 형식 위반이면 None."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════
# 브리핑 — F-03 이전의 템플릿 폴백
# ══════════════════════════════════════════════════════════════════
def template_briefing(match: dict[str, Any]) -> Briefing:
    """LLM 없이 만드는 100자 이내 문장.

    F-03이 붙으면 Gemini가 이 자리를 대신하고, 이 함수는 호출 실패 시
    폴백으로 남는다. 확인된 규칙이 없으므로 **사용 가능 여부를 단정하지 않는다**
    (02_DB_SCHEMA §8.6).
    """
    coupons = match.get("available_coupons") or []
    head = coupons[0] if coupons else {}
    name = head.get("product_name") or "보유 쿠폰"
    days = head.get("days_left")

    parts = [f"{match['distance_m']}m 앞 {match['store_name']}."]
    if len(coupons) > 1:
        parts.append(f"사용 가능한 쿠폰 {len(coupons)}장이 있어요.")
    else:
        parts.append(f"'{name}' 쿠폰이 있어요.")
        # 쿠폰이 한 장일 때만 붙인다 — 여러 장이면 이 문구가 어느 쿠폰
        # 얘기인지 모호해진다. 브랜드 정책이 아니라 이 쿠폰 한 장에 적힌
        # 조건이므로 "이 쿠폰은"으로 한정한다 (usage_note는 RAG 미색인).
        if head.get("usage_note"):
            parts.append(f"이 쿠폰은 {head['usage_note']}")
    if isinstance(days, int) and days <= 7:
        parts.append(f"만료까지 {days}일 남았습니다.")

    text = " ".join(parts)
    return Briefing(text=gemini.truncate_briefing(text), generated_by="TEMPLATE", rules=[])


# ══════════════════════════════════════════════════════════════════
# F-04 RAG 검색 + F-03 Gemini 브리핑 생성 — 01_API_SPEC.md §8 전체 흐름
# ══════════════════════════════════════════════════════════════════
# 매칭된 모든 매장에 같은 질의문을 쓴다. 요청마다 매장별로 다른 문장을 지어
# 임베딩하면 호출 수만 늘고, 브랜드/매장유형 필터(§8.5 ①)가 실질적인 검색
# 좁히기를 이미 담당하므로 질의문 자체를 다양화할 이유가 없다.
RAG_QUERY = "이 매장에서 이 쿠폰을 사용할 수 있나요? 사용이 제한되는 조건이 있나요?"
_rag_query_vec: list[float] | None = None


async def _get_rag_query_vec() -> list[float]:
    """고정 질의문의 임베딩을 프로세스 생애주기 동안 1회만 계산해 재사용한다."""
    global _rag_query_vec
    if _rag_query_vec is None:
        _rag_query_vec = await gemini.embed_text(RAG_QUERY, is_query=True)
    return _rag_query_vec


async def rag_rules_for(brand_id: str, store_type: str) -> list[dict[str, Any]]:
    """brand_id(+_common) × store_type(+NORMAL) 사전 필터 → 코사인 유사도 top_k.

    임계값(0.6) 미만인 규칙은 여기서 걸러낸다 — 02_DB_SCHEMA.md §8.6.
    검색 자체가 실패하면(임베딩 장애 등) 빈 목록으로 처리한다. "규칙을 못 찾음"과
    "검색이 실패함"을 사용자에게는 같은 결과("확인된 정보 없음")로 보여준다 —
    실패를 "사용 가능"으로 오인시키는 것보다 훨씬 안전하다.
    """
    try:
        qvec = await _get_rag_query_vec()
        rows = await core.search_rules(brand_id, store_type, qvec)
    except gemini.GeminiUnavailable as exc:
        logger.warning("RAG 검색 실패, 규칙 없이 진행: %s", exc)
        return []
    threshold = core.get_settings().rag_similarity_threshold
    return [r for r in rows if float(r["similarity"]) >= threshold]


async def build_briefing(match: dict[str, Any]) -> Briefing:
    """규칙 검색(F-04) → Gemini 브리핑 생성(F-03). 실패 시 TEMPLATE로 폴백한다.

    폴백이어도 검색된 규칙(rules)은 그대로 응답에 싣는다 — 문장은 Gemini가
    아니어도, 근거 자체는 RAG가 이미 찾아낸 사실이기 때문이다.
    """
    rules = await rag_rules_for(match["brand_id"], match["store_type"])
    briefing_rules = [
        BriefingRule(
            rule_id=r["rule_id"],
            content=r["content"],
            source_name=r["source_name"],
            similarity=round(float(r["similarity"]), 4),
        )
        for r in rules
    ]
    try:
        text = await gemini.generate_briefing_text(match, rules)
        return Briefing(text=text, generated_by="GEMINI", rules=briefing_rules)
    except (gemini.GeminiUnavailable, gemini.GeminiParseError) as exc:
        logger.info("브리핑 생성 실패, TEMPLATE 폴백 (%s): %s", match["store_id"], exc)
        fallback = template_briefing(match)
        fallback.rules = briefing_rules
        return fallback


# ══════════════════════════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════════════════════════
@router.post("", response_model=LocationResponse, summary="위치 전송 및 매장 매칭")
async def post_locations(
    body: LocationRequest,
    uid: str = Depends(core.get_current_uid),
) -> LocationResponse:
    settings = core.get_settings()
    points = body.points

    if not points:
        raise core.ApiError(422, "VALIDATION_ERROR", "좌표가 최소 1개 필요합니다.")
    if len(points) > MAX_POINTS:
        raise core.ApiError(
            422, "TOO_MANY_POINTS",
            f"좌표는 한 번에 최대 {MAX_POINTS}개까지 보낼 수 있습니다.",
            {"received": len(points), "max": MAX_POINTS},
        )

    # ── 1) 범위 검증. 하나라도 범위를 벗어나면 요청 전체를 거절한다.
    #       클라이언트 버그를 조용히 삼키면 "왜 알림이 안 오지"의 원인이 숨는다.
    for idx, p in enumerate(points):
        if not (-90.0 <= p.lat <= 90.0) or not (-180.0 <= p.lng <= 180.0):
            raise core.ApiError(
                422, "INVALID_COORDINATE", "위경도 값이 허용 범위를 벗어났습니다.",
                {"index": idx, "lat": p.lat, "lng": p.lng},
            )

    # ── 2) 좌표별 필터 (직전 좌표가 필요 없는 규칙)
    now = core.utcnow()
    survivors: list[tuple[int, LocationPoint, datetime]] = []
    rejected: list[RejectedReason] = []

    for idx, p in enumerate(points):
        recorded = parse_recorded_at(p.recorded_at)
        if recorded is None:
            rejected.append(RejectedReason(index=idx, reason="INVALID_RECORDED_AT"))
            continue
        if p.accuracy_m > MAX_ACCURACY_M:
            rejected.append(RejectedReason(index=idx, reason="LOW_ACCURACY"))
            continue
        if (now - recorded).total_seconds() > STALE_AFTER_S:
            # §5는 "저장은 하되 매칭 제외"지만, 이력 테이블이 없어 저장할 곳이 없다(C-13).
            rejected.append(RejectedReason(index=idx, reason="STALE"))
            continue
        survivors.append((idx, p, recorded))

    # ── 3) 최신 좌표부터 직전 저장 좌표와 비교해 이상치를 걸러낸다.
    survivors.sort(key=lambda t: t[2], reverse=True)
    chosen = survivors[0] if survivors else None
    last = await core.get_last_location(uid)

    accepted = len(survivors)

    chosen: tuple[int, LocationPoint, datetime] | None = None
    for idx, p, recorded in survivors:
        if last is not None and p.source != "MANUAL_REFRESH":
            moved = haversine_m(last["lat"], last["lng"], p.lat, p.lng)
            # recorded_at은 클라이언트 시계다. 서버(DB)의 last_location_at과
            # 빼면 기기 시계 오차가 그대로 들어와 판정이 통째로 무력화된다.
            # 음수가 나오면 서버 수신 시각으로 대체한다.
            elapsed_s = (recorded - last["at"]).total_seconds()
            if elapsed_s <= 0:
                elapsed_s = (now - last["at"]).total_seconds()
            speed = (moved / 1000.0) / (elapsed_s / 3600.0) if elapsed_s > 0 else 0.0
            logger.info(
                "속도 판정: moved=%.0fm elapsed=%.1fs → %.0fkm/h", moved, elapsed_s, speed
            )
            if elapsed_s >= 1.0 and moved >= DUP_SUPPRESS_M and speed > MAX_SPEED_KMH:
                rejected.append(RejectedReason(index=idx, reason="IMPLAUSIBLE_SPEED"))
                continue
        chosen = (idx, p, recorded)
        break

    accepted = len(survivors) - sum(
        1 for r in rejected if r.reason == "IMPLAUSIBLE_SPEED"
    )

    if chosen is None:
        logger.info("유효 좌표 없음 (uid=%s, 수신 %d개)", uid, len(points))
        return LocationResponse(
            accepted=0,
            rejected=len(rejected),
            rejected_reasons=rejected,
            search_radius_m=settings.search_radius_m,
            matches=[],
            notification=NotificationResult(
                sent=False, reason="FCM_DEFERRED_TO_PHASE2"
            ),
        )

    _, point, _recorded = chosen

    # ── 4) 직전 좌표와 50m 이내면 DB 갱신을 생략한다 (중복 억제).
    #       매칭은 생략하지 않는다 — 같은 자리에 서 있어도 쿠폰은 새로 등록될 수 있다.
    should_update = True
    if last is not None:
        if haversine_m(last["lat"], last["lng"], point.lat, point.lng) < DUP_SUPPRESS_M:
            should_update = False
    if should_update:
        await core.update_last_location(uid, point.lat, point.lng)

    # ── 5) 반경 검색 (F-02). 쿼리는 02_DB_SCHEMA §7, core.match_stores()
    raw_matches = await core.match_stores(uid, point.lat, point.lng)

    briefings = await asyncio.gather(*(build_briefing(m) for m in raw_matches))
    matches = [
        StoreMatch(**m, briefing=b) for m, b in zip(raw_matches, briefings)
    ]
    logger.info(
        "위치 매칭: uid=%s (%.6f, %.6f) → 매장 %d곳 (갱신=%s)",
        uid, point.lat, point.lng, len(matches), should_update,
    )

    return LocationResponse(
        accepted=accepted,
        rejected=len(rejected),
        rejected_reasons=rejected,
        search_radius_m=settings.search_radius_m,
        matches=matches,
        notification=NotificationResult(sent=False, reason="FCM_DEFERRED_TO_PHASE2"),
    )