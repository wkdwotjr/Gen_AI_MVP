"""data/rules.json → coupon_rules 테이블 적재 (F-04) — 02_DB_SCHEMA.md §8.3, §8.4

동작:
  - 규칙마다 content를 임베딩(gemini-embedding-001, RETRIEVAL_DOCUMENT)한 뒤
    coupon_rules에 INSERT ... ON CONFLICT (rule_id) DO UPDATE.
  - rule_id가 이미 있으면 다시 계산해 덮어쓴다 — rules.json을 고치고 재실행하면
    그대로 반영되는 멱등 스크립트다. 몇 번을 다시 돌려도 안전하다.

전제:
  - Cloud SQL Auth Proxy가 켜져 있어야 한다 (00_PROGRESS.md ① 참조).
  - .env에 Gemini 인증 정보가 있어야 실제 임베딩이 나온다. 없으면 MOCK
    임베딩(의미 없는 해시 기반 벡터, app/services/gemini.py의 _mock_embedding)이
    들어가 검색 순위가 무의미해진다 — 시연 전 반드시 실제 자격증명으로 재실행할 것.

실행:
  cd backend-fastapi
  python scripts/index_rules.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app import core  # noqa: E402
from app.services import gemini  # noqa: E402

RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "rules.json"

_UPSERT_SQL = text(
    """
    INSERT INTO coupon_rules (
        rule_id, content, embedding, embed_model,
        brand_id, rule_type, store_type,
        source_name, source_url, effective_date, verified_by
    ) VALUES (
        :rule_id, :content, CAST(:embedding AS vector), :embed_model,
        :brand_id, :rule_type, :store_type,
        :source_name, :source_url, CAST(:effective_date AS date), :verified_by
    )
    ON CONFLICT (rule_id) DO UPDATE SET
        content        = EXCLUDED.content,
        embedding      = EXCLUDED.embedding,
        embed_model    = EXCLUDED.embed_model,
        brand_id       = EXCLUDED.brand_id,
        rule_type      = EXCLUDED.rule_type,
        store_type     = EXCLUDED.store_type,
        source_name    = EXCLUDED.source_name,
        source_url     = EXCLUDED.source_url,
        effective_date = EXCLUDED.effective_date,
        verified_by    = EXCLUDED.verified_by
    """
)


def _vec_literal(vec: list[float]) -> str:
    """core._vec_literal과 동일한 포맷. 스크립트가 app.core 내부에 의존하지
    않도록 여기서 따로 정의한다 — pgvector-python 없이 텍스트 캐스트로 넣는다."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def main() -> None:
    if not RULES_PATH.exists():
        sys.exit(f"[error] {RULES_PATH} 가 없습니다.")
    payload = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    rules = payload.get("rules", [])
    if not rules:
        sys.exit("[error] rules.json에 규칙이 없습니다.")

    settings = core.get_settings()
    if settings.use_mock_gemini:
        print(
            "[warn] Gemini 자격증명 미설정 — MOCK 임베딩으로 적재합니다. "
            "검색 순위가 무의미하니 시연 전 실제 자격증명으로 재실행하세요."
        )

    engine = core.get_engine()
    with engine.begin() as conn:
        for i, rule in enumerate(rules, 1):
            vec = await gemini.embed_text(rule["content"], is_query=False)
            conn.execute(
                _UPSERT_SQL,
                {
                    "rule_id": rule["rule_id"],
                    "content": rule["content"],
                    "embedding": _vec_literal(vec),
                    "embed_model": settings.embedding_model,
                    "brand_id": rule["brand_id"],
                    "rule_type": rule["rule_type"],
                    "store_type": rule["store_type"],
                    "source_name": rule["source_name"],
                    "source_url": rule.get("source_url"),
                    "effective_date": rule.get("effective_date"),
                    "verified_by": rule.get("verified_by"),
                },
            )
            print(f"[{i}/{len(rules)}] {rule['rule_id']} ({rule['store_type']}) 적재 완료")

    print(f"[ok] {len(rules)}건 적재 완료")


if __name__ == "__main__":
    asyncio.run(main())
