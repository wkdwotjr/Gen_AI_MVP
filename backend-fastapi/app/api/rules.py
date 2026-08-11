"""RAG 규칙 검색 (F-04 시연용) — 01_API_SPEC.md §6

§5의 브리핑에도 같은 검색이 자동으로 포함되지만(app/api/locations.py의
rag_rules_for), 이 엔드포인트는 RAG 동작 자체를 브랜드·매장유형·질의문을
직접 바꿔가며 보여주기 위한 단독 화면용이다. 질의문이 매번 다르므로
locations.py처럼 고정 질의 임베딩을 캐시하지 않는다.
"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import core
from app.services import gemini

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rules", tags=["rules"])


class RuleSearchRequest(BaseModel):
    brand_id: str
    store_type: str
    query: str


class RuleResult(BaseModel):
    rule_id: str
    content: str
    rule_type: str
    source_name: str
    source_url: str | None
    similarity: float


class RuleSearchResponse(BaseModel):
    rules: list[RuleResult]


@router.post("/search", response_model=RuleSearchResponse, summary="RAG 규칙 검색")
async def search_rules(
    body: RuleSearchRequest,
    _uid: str = Depends(core.get_current_uid),
) -> RuleSearchResponse:
    if not body.query.strip():
        raise core.ApiError(422, "VALIDATION_ERROR", "query가 비어 있습니다.")

    threshold = core.get_settings().rag_similarity_threshold
    try:
        qvec = await gemini.embed_text(body.query, is_query=True)
        rows = await core.search_rules(body.brand_id, body.store_type, qvec)
    except gemini.GeminiUnavailable as exc:
        logger.warning("RAG 검색 실패: %s", exc)
        raise core.ApiError(
            503, "UPSTREAM_UNAVAILABLE", "검색 서버에 일시적으로 연결할 수 없습니다."
        )

    rules = [
        RuleResult(
            rule_id=r["rule_id"],
            content=r["content"],
            rule_type=r["rule_type"],
            source_name=r["source_name"],
            source_url=r["source_url"],
            similarity=round(float(r["similarity"]), 4),
        )
        for r in rows
        if float(r["similarity"]) >= threshold
    ]
    return RuleSearchResponse(rules=rules)
