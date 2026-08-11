-- ============================================================
-- 쿠폰콕 마이그레이션 2026-08-10
--   M-1  coupons: 쿠폰 유형(coupon_type) / 액면가(face_value)
--   M-2  coupons: 다중 이미지 (image_gcs_path → image_gcs_paths)
--   M-3  coupons: 바코드 해시 기반 중복 등록 방지
--   M-4  coupon_rules: RAG 인덱스 테이블 활성화 (F-04)
--
-- 실행: python db/apply_sql.py db/migrate_20260810.sql
-- 주의: init.sql은 CREATE TABLE IF NOT EXISTS라 재실행해도 컬럼이 붙지 않는다.
--       신규 환경을 위해 init.sql의 coupons 정의도 함께 갱신할 것.
-- 재실행: 전 구문 멱등. 여러 번 돌려도 안전하다.
-- ============================================================


-- ── M-1. 쿠폰 유형과 액면가 ────────────────────────────────
--
-- 왜 유형을 4개로만 자르는가:
--   메가MGC커피 상품교환권에도 "사용가능금액 2,000원"이 찍혀 있다.
--   즉 "상품권이냐 금액권이냐"는 쿠폰의 속성이 아니라 브랜드 정책이며,
--   차액 결제·잔액 처리 가능 여부는 컬럼이 아니라 F-04 RAG가 답할 문제다.
--   여기서는 화면에 보이는 사실만 담고, 정책 판단은 하지 않는다.
--
ALTER TABLE coupons
    ADD COLUMN IF NOT EXISTS coupon_type TEXT NOT NULL DEFAULT 'UNKNOWN',
    ADD COLUMN IF NOT EXISTS face_value  INTEGER;

COMMENT ON COLUMN coupons.coupon_type IS
    'PRODUCT(상품교환권) / AMOUNT(금액권) / DISCOUNT(할인권) / UNKNOWN';
COMMENT ON COLUMN coupons.face_value IS
    '액면가·사용가능금액 (원). PRODUCT에도 존재할 수 있다. 인식 실패 시 NULL';

DO $$ BEGIN
    ALTER TABLE coupons ADD CONSTRAINT ck_coupons_type
        CHECK (coupon_type IN ('PRODUCT', 'AMOUNT', 'DISCOUNT', 'UNKNOWN'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE coupons ADD CONSTRAINT ck_coupons_face_value
        CHECK (face_value IS NULL OR face_value > 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ── M-2. 다중 이미지 ───────────────────────────────────────
--
-- 카카오톡 선물하기는 한 쿠폰의 정보가 두 화면에 쪼개져 있다.
--   상단 화면 : 브랜드 / 상품명 / 바코드
--   선물정보  : 유효기간 / 사용가능금액 / 교환처
-- 어느 한 장만으로는 필수 필드가 채워지지 않는다.
-- 업로드 1건 = 이미지 N장으로 바꾸고, Gemini에 한 번에 넣어 병합시킨다.
--
ALTER TABLE coupons ADD COLUMN IF NOT EXISTS image_gcs_paths TEXT[];

-- 기존 단일 컬럼이 있으면 배열로 옮기고 제거
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'coupons' AND column_name = 'image_gcs_path') THEN
        UPDATE coupons
           SET image_gcs_paths = ARRAY[image_gcs_path]
         WHERE image_gcs_path IS NOT NULL AND image_gcs_paths IS NULL;
        ALTER TABLE coupons DROP COLUMN image_gcs_path;
    END IF;
END $$;


-- ── M-3. 중복 등록 방지 ────────────────────────────────────
--
-- client_upload_id는 "같은 업로드 시도"의 중복만 막는다.
-- 사용자가 같은 쿠폰을 나중에 다시 찍어 올리면 별개 행이 생겨
-- 위치 알림이 같은 쿠폰으로 두 번 간다.
-- 바코드 숫자의 해시로 실질 중복을 잡는다.
--   · 원문이 아니라 해시만 저장한다 (현금성 정보)
--   · Fernet 암호문은 매번 값이 달라 비교에 쓸 수 없다
--   · 바코드를 못 읽은 쿠폰(NULL)은 제약 대상에서 빠진다 → 부분 인덱스
--
ALTER TABLE coupons ADD COLUMN IF NOT EXISTS barcode_hash TEXT;

COMMENT ON COLUMN coupons.barcode_hash IS
    '바코드 숫자 + 앱 고정 salt 의 SHA-256 hex. 중복 판정 전용. 복호화 불가';

CREATE UNIQUE INDEX IF NOT EXISTS uq_coupons_barcode
    ON coupons (uid, barcode_hash)
    WHERE barcode_hash IS NOT NULL;


-- ── M-4. RAG 인덱스 테이블 (F-04) ──────────────────────────
--
-- pgvector 0.8.5 가용 확인됨 (2026-08-10, Cloud SQL PostgreSQL 15.18).
-- 차원 768 = gemini-embedding-001 output_dimensionality=768 기준.
-- 모델을 바꾸면 차원도 바꿔야 하므로 embed_model 컬럼에 함께 기록한다.
--
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS coupon_rules (
    rule_id         TEXT PRIMARY KEY,
    content         TEXT        NOT NULL,
    embedding       vector(768) NOT NULL,
    embed_model     TEXT        NOT NULL DEFAULT 'gemini-embedding-001',
    brand_id        TEXT        NOT NULL,
    rule_type       TEXT        NOT NULL DEFAULT 'GENERAL',
    store_type      TEXT        NOT NULL DEFAULT 'NORMAL',
    source_name     TEXT        NOT NULL,
    source_url      TEXT,
    effective_date  DATE,
    verified_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_rules_type CHECK (rule_type IN (
        'EXCLUSION',    -- 사용 불가 매장·품목
        'CROSS_USE',    -- 다른 상품 교환·차액 결제  ← 메가 "사용가능금액 2,000원"
        'BALANCE',      -- 잔액 처리·환불
        'EXTENSION',    -- 유효기간 연장
        'PAYMENT',      -- 결제수단·중복 할인
        'GENERAL')),
    CONSTRAINT ck_rules_store_type CHECK (store_type IN (
        'NORMAL', 'DEPARTMENT_STORE', 'MART_TENANT',
        'HIGHWAY_REST_AREA', 'AIRPORT', 'HOSPITAL', 'CAMPUS'))
);

-- 규칙 100건 이하에서는 순차 스캔이 더 빠르고 재현율도 높다.
-- HNSW는 근사 검색이라 소량에서 오히려 손해다. 수천 건을 넘기면 아래를 해제한다.
-- CREATE INDEX idx_rules_hnsw ON coupon_rules USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_rules_filter
    ON coupon_rules (brand_id, store_type);


-- ── 확인 ───────────────────────────────────────────────────
-- SELECT column_name, data_type FROM information_schema.columns
--  WHERE table_name = 'coupons' ORDER BY ordinal_position;