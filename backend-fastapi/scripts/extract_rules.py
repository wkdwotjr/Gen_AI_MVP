"""쿠폰 이미지 → RAG 규칙 후보 추출 (F-04) — 02_DB_SCHEMA.md §8.3 추출 파이프라인 ①

data/rule_images/*.jpg (팀원이 보유한 쿠폰 캡처 — 뒷면·상세 화면 등 이용조건이
적힌 부분)를 Gemini Vision에 넣어 규칙 후보를 뽑고 data/rules.json에 append한다.

**사람 검수가 반드시 필요하다.** 이 스크립트가 만드는 항목은 verified_by=null로
들어간다 — 02_DB_SCHEMA.md §8.6에 따라 미검증 규칙은 브리핑에서 단정 표현을
쓰지 않는다. 실제 인덱싱(coupon_rules 적재) 전에 사람이 data/rules.json을 열어
    1) content가 사실과 맞는지
    2) 저작권 문제 없이 요약 재작성됐는지 (원문 복제 금지, §8.3)
    3) verified_by에 검수자 이름을 채웠는지
확인해야 한다. scripts/index_rules.py는 검수 여부를 확인하지 않고 있는 그대로
적재하므로, 검수는 반드시 이 스크립트와 index_rules.py 사이에서 사람이 한다.

2026-08-11 세션 기준: data/rule_images/가 비어 있어 아직 실행되지 않았다
(팀 보유 쿠폰 이미지 미확보). 이미지가 확보되면 그 폴더에 넣고 실행한다.
data/rule_images/는 .gitignore 처리되어 있다 — 실제 쿠폰 캡처라 바코드가
보일 수 있어 커밋하지 않는다 (test_images/와 같은 취급).

실행:
  cd backend-fastapi
  python scripts/extract_rules.py
"""
import asyncio
import json
import sys
from pathlib import Path

# Windows 콘솔 기본 코드페이지(cp949)는 em dash(—) 등 일부 유니코드를 인코딩하지
# 못해 print()가 죽는다 (00_PROGRESS.md의 .ps1 CP949 이슈와 같은 계열). 실행 결과
# 자체(data/rules.json 저장)는 이 줄과 무관하게 이미 끝난 뒤라 출력만 보호하면 된다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import core  # noqa: E402
from app.services import gemini  # noqa: E402

IMAGES_DIR = Path(__file__).resolve().parents[1] / "data" / "rule_images"
RULES_PATH = Path(__file__).resolve().parents[1] / "data" / "rules.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}

# 02_DB_SCHEMA.md §9 DDL의 CHECK 제약과 값 집합을 맞춘다.
STORE_TYPES = [
    "NORMAL", "DEPARTMENT_STORE", "MART_TENANT",
    "HIGHWAY_REST_AREA", "AIRPORT", "HOSPITAL", "CAMPUS",
]
RULE_TYPES = ["EXCLUSION", "CROSS_USE", "BALANCE", "EXTENSION", "PAYMENT", "GENERAL"]

_SYSTEM_INSTRUCTION = f"""당신은 한국 모바일 쿠폰(기프티콘) 이미지에서 "이용 조건" 규칙을
뽑아내는 리서처다. 화면에 보이는 유효기간·상품명이 아니라, 사용 가능/불가 조건,
교환처 제한, 잔액·환불 처리, 유효기간 연장, 다른 상품 교차 사용 가능 여부처럼
**정책성 문구**만 대상으로 한다.

1. 이미지에 실제로 그 근거가 되는 문구가 보일 때만 후보를 만든다. 없으면
   candidates를 빈 배열로 둔다. 추측하거나 지어내지 않는다.
2. content는 원문을 그대로 베끼지 않는다. 사실만 남기고 한국어 문장으로
   요약 재작성한다 (저작권 — 02_DB_SCHEMA.md §8.3).
3. rule_type은 다음 중 하나: {", ".join(RULE_TYPES)}
   - EXCLUSION: 특정 매장 유형·조건에서 사용 불가
   - CROSS_USE: 다른 상품으로 교환·차액 결제 가능 여부
   - BALANCE: 잔액 처리·환불
   - EXTENSION: 유효기간 연장 가능 여부
   - PAYMENT: 결제수단 제한·중복 할인 가능 여부
   - GENERAL: 위에 안 맞는 일반 이용 안내
4. applicable_store_types는 다음 값의 부분집합이다: {", ".join(STORE_TYPES)}
   문구가 특정 매장 유형(백화점·대형마트·병원·공항·휴게소·캠퍼스)을 지목하면
   그 유형들만 넣는다. 매장 유형과 무관한 일반 규칙이면 ["NORMAL"] 하나만 넣는다.
5. brand_name에는 이미지에서 읽은 브랜드명을 원문 그대로 적는다. 코드 정규화는
   이 스크립트가 별도로 한다.
6. source_excerpt_hint는 사람이 나중에 원본 이미지에서 이 문구를 다시 찾을 때
   참고할 아주 짧은 위치 설명("하단 교환처 안내" 등)이다. 원문 인용이 아니다.

출력은 지정된 JSON 스키마만 반환한다."""

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "brand_name": {"type": "string", "nullable": True},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "rule_type": {"type": "string", "enum": RULE_TYPES},
                    "applicable_store_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": STORE_TYPES},
                    },
                    "source_excerpt_hint": {"type": "string", "nullable": True},
                },
                "required": ["content", "rule_type", "applicable_store_types"],
            },
        },
    },
    "required": ["candidates"],
}


def _extract_one_sync(image_path: Path) -> dict:
    from google.genai import types

    settings = core.get_settings()
    client = gemini._get_client()
    mime = MIME[image_path.suffix.lower()]
    part = types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[part, "이 쿠폰 이미지에서 이용 조건 규칙 후보를 뽑아줘."],
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
        ),
    )
    text = (response.text or "").strip()
    return json.loads(text) if text else {"candidates": []}


async def main() -> None:
    if not IMAGES_DIR.exists():
        sys.exit(
            f"[error] {IMAGES_DIR} 가 없습니다. 팀 보유 쿠폰 이미지를 이 폴더에 "
            "넣고 다시 실행하세요."
        )
    images = sorted(p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        sys.exit(f"[error] {IMAGES_DIR} 에 이미지가 없습니다.")

    settings = core.get_settings()
    if settings.use_mock_gemini:
        sys.exit("[error] Gemini 자격증명이 없습니다. .env를 확인하세요 — MOCK으로는 추출 의미가 없다.")

    existing = (
        json.loads(RULES_PATH.read_text(encoding="utf-8"))
        if RULES_PATH.exists() else {"rules": []}
    )
    existing_ids = {r["rule_id"] for r in existing["rules"]}
    # source_name에 파일명을 그대로 적어두므로(예: "커피쿠폰_메가.jpg (Gemini 추출 후보 — ...)"),
    # 이미 처리한 이미지인지는 이걸로 판별한다. 별도 상태 파일을 두지 않기 위한 선택 —
    # 재실행 시 같은 이미지를 또 돌려 rul_..._02 같은 사실상 중복 후보가 쌓이는 것을 막는다.
    already_processed = {
        r["source_name"].split(" (Gemini 추출")[0]
        for r in existing["rules"]
        if "(Gemini 추출" in r.get("source_name", "")
    }

    new_rules = []
    for image_path in images:
        if image_path.name in already_processed:
            print(f"[skip] 이미 처리된 이미지: {image_path.name}")
            continue
        print(f"[info] 처리 중: {image_path.name}")
        raw = await asyncio.to_thread(_extract_one_sync, image_path)
        brand_id = gemini.resolve_brand_id(None, raw.get("brand_name")) or "UNKNOWN"
        candidates = raw.get("candidates") or []
        if not candidates:
            print("  → 규칙 후보 없음")
            continue
        for cand in candidates:
            slug = f"{brand_id}_{cand['rule_type'].lower()}"
            for store_type in cand.get("applicable_store_types") or ["NORMAL"]:
                seq = 1
                rule_id = f"rul_{slug}_{store_type.lower()}_{seq:02d}"
                while rule_id in existing_ids:
                    seq += 1
                    rule_id = f"rul_{slug}_{store_type.lower()}_{seq:02d}"
                existing_ids.add(rule_id)
                new_rules.append({
                    "rule_id": rule_id,
                    "content": cand["content"],
                    "brand_id": brand_id,
                    "rule_type": cand["rule_type"],
                    "store_type": store_type,
                    "source_name": f"{image_path.name} (Gemini 추출 후보 — 사람 검수 전)",
                    "source_url": None,
                    "effective_date": None,
                    "verified_by": None,   # 사람 검수 전까지는 항상 null
                })
                print(f"  → 후보: {rule_id} [{cand['rule_type']}/{store_type}]")

    if not new_rules:
        print("[ok] 새 후보가 없습니다.")
        return

    existing["rules"].extend(new_rules)
    RULES_PATH.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"\n[ok] {len(new_rules)}건의 후보를 {RULES_PATH.name}에 추가했다. "
        "verified_by가 모두 null이다 — 사람이 내용을 검수하고 채운 뒤 "
        "scripts/index_rules.py로 적재할 것."
    )


if __name__ == "__main__":
    asyncio.run(main())
