-- ============================================================
-- 쿠폰콕 마이그레이션 2026-08-11
--   M-5  coupons: 쿠폰 인스턴스별 이용조건 문구 (usage_note)
--
-- 실행: python db/apply_sql.py db/migrate_20260811.sql
-- 재실행: 멱등. 여러 번 돌려도 안전하다.
-- ============================================================


-- ── M-5. 쿠폰별 이용조건 문구 ──────────────────────────────
--
-- coupon_rules(F-04 RAG)는 "브랜드 전체에 공통으로 적용되는" 정책만 담기로
-- 했다(02_DB_SCHEMA.md §8). 그런데 같은 브랜드라도 쿠폰(발행 캠페인·SKU)마다
-- 다른 제한이 찍혀 있을 수 있다 — 실측 사례: 이마트 금액권에 "안양점/부천점
-- 제외" 문구가 있었는데, 그게 이마트 전체의 영구 정책인지 그 발행분에만
-- 붙은 조건인지는 사진 한 장으로는 확인할 방법이 없었다. 이런 문구를
-- brand_id 단위 RAG에 넣으면 근거 없이 전체 사용자에게 일반화하는 셈이 된다.
--
-- 대신 F-01 파싱 시 그 쿠폰 이미지에 실제로 적힌 제한 문구를 이 컬럼에
-- 그대로 저장하고, 그 쿠폰의 브리핑을 만들 때만 참고한다(app/api/locations.py
-- build_briefing → app/services/gemini.py _briefing_context). RAG 색인도,
-- 사람 검수(verified_by)도 필요 없다 — 본인 쿠폰에 적힌 사실을 그대로
-- 쓰는 것이라 다른 사용자의 브리핑을 오염시키지 않는다.
--
ALTER TABLE coupons ADD COLUMN IF NOT EXISTS usage_note TEXT;

COMMENT ON COLUMN coupons.usage_note IS
    '이 쿠폰 이미지에 적힌 이용 제한/조건 문구(있으면 그대로/요약). 브랜드 전체
     정책이 아니라 이 쿠폰 한 장에 한정된 사실 — coupon_rules(RAG)와 달리
     검증·색인하지 않고 이 쿠폰의 브리핑에서만 참고한다.';


-- ── 확인 ───────────────────────────────────────────────────
-- SELECT column_name, data_type FROM information_schema.columns
--  WHERE table_name = 'coupons' AND column_name = 'usage_note';
