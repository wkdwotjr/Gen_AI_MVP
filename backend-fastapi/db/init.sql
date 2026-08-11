-- ============================================================
-- 쿠폰콕 MVP 1차 스키마
-- 실행: psql -f init.sql   (또는 scripts/db_init.py)
-- 원칙: CREATE ... IF NOT EXISTS 만 포함한다. DROP은 reset.sql에서만.
-- 참조: docs/02_DB_SCHEMA.md, docs/03_CLOUD_SQL_SETUP.md D-1/D-2
-- ============================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- CREATE EXTENSION IF NOT EXISTS vector;   -- F-04 착수 시 해제

-- ---------- users ----------
CREATE TABLE IF NOT EXISTS users (
    uid                   TEXT        PRIMARY KEY,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    fcm_token             TEXT        UNIQUE,
    fcm_token_updated_at  TIMESTAMPTZ,
    notification_granted  BOOLEAN     NOT NULL DEFAULT false,
    device_model          TEXT,
    app_version           TEXT,
    last_location         geometry(Point, 4326),
    last_location_at      TIMESTAMPTZ,
    last_notified_at      TIMESTAMPTZ
);

-- ---------- coupons ----------
CREATE TABLE IF NOT EXISTS coupons (
    coupon_id         TEXT        PRIMARY KEY,
    uid               TEXT        NOT NULL REFERENCES users(uid) ON DELETE CASCADE,
    client_upload_id  UUID        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'PROCESSING',
 
    -- 브랜드 (config/brands.json 이 단일 출처)
    brand_id          TEXT,                    -- NULL이면 위치 매칭 대상에서 제외
    brand_name        TEXT,                    -- 이미지에서 읽은 원문
 
    -- 쿠폰 내용
    coupon_type       TEXT        NOT NULL DEFAULT 'UNKNOWN',
    product_name      TEXT,
    face_value        INTEGER,                 -- 액면가·사용가능금액 (원)
    expires_at        DATE,                    -- KST 달력 날짜
 
    -- 바코드: 원문은 어디에도 저장하지 않는다
    barcode_masked    TEXT,                    -- 표시용
    barcode_hash      TEXT,                    -- 중복 판정용 SHA-256. 복호화 불가
    barcode_format    TEXT,
 
    -- 사용 상태
    is_used           BOOLEAN     NOT NULL DEFAULT false,
    used_at           TIMESTAMPTZ,             -- 가설 검증 지표(전환율)의 원천
 
    -- 파싱 품질
    confidence        JSONB,
    needs_review      BOOLEAN     NOT NULL DEFAULT false,
    error_code        TEXT,
    error_message     TEXT,
 
    -- 원본 (한 쿠폰이 여러 화면에 걸쳐 있을 수 있다 — 카카오톡 선물하기)
    image_gcs_paths   TEXT[],

    -- 이 쿠폰 한 장에 적힌 이용조건 문구. 브랜드 전체 정책(coupon_rules)이
    -- 아니라 이 쿠폰에 한정된 사실이라 검증·색인 없이 브리핑에서만 참고한다
    usage_note        TEXT,

    last_notified_at  TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
 
    CONSTRAINT uq_coupons_upload  UNIQUE (uid, client_upload_id),
    CONSTRAINT ck_coupons_status
        CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED')),
    CONSTRAINT ck_coupons_completed
        CHECK (status = 'PROCESSING' OR completed_at IS NOT NULL),
    CONSTRAINT ck_coupons_type
        CHECK (coupon_type IN ('PRODUCT', 'AMOUNT', 'DISCOUNT', 'UNKNOWN')),
    CONSTRAINT ck_coupons_face_value
        CHECK (face_value IS NULL OR face_value > 0)
);
 
COMMENT ON COLUMN coupons.coupon_type IS
    'PRODUCT(상품교환권) / AMOUNT(금액권) / DISCOUNT(할인권) / UNKNOWN';
COMMENT ON COLUMN coupons.face_value IS
    '액면가·사용가능금액 (원). PRODUCT에도 존재할 수 있다. 인식 실패 시 NULL';
COMMENT ON COLUMN coupons.barcode_hash IS
    '바코드 숫자 + 앱 고정 salt 의 SHA-256 hex. 중복 판정 전용. 복호화 불가';
COMMENT ON COLUMN coupons.usage_note IS
    '이 쿠폰 이미지에 적힌 이용 제한/조건 문구(있으면). 브랜드 전체 정책이 아니라
     이 쿠폰 한 장에 한정된 사실 — coupon_rules(RAG)와 달리 검증·색인하지 않고
     이 쿠폰의 브리핑에서만 참고한다.';

CREATE INDEX IF NOT EXISTS idx_coupons_usable
    ON coupons (uid, brand_id)
    WHERE status = 'COMPLETED' AND is_used = false;
 
CREATE INDEX IF NOT EXISTS idx_coupons_uid_expires
    ON coupons (uid, expires_at)
    WHERE status = 'COMPLETED' AND is_used = false;
 
-- 같은 쿠폰 재등록 방지. 바코드를 못 읽은 행(NULL)은 대상에서 빠진다
CREATE UNIQUE INDEX IF NOT EXISTS uq_coupons_barcode
    ON coupons (uid, barcode_hash)
    WHERE barcode_hash IS NOT NULL;

-- ---------- stores ----------
-- source: PUBLIC_DATA(소상공인 상가정보) | MANUAL(직영 브랜드 등 수동 시드) | KAKAO_LOCAL(런타임 보조 조회, 미저장 원칙 — C-4)
CREATE TABLE IF NOT EXISTS stores (
    store_id      TEXT        PRIMARY KEY,
    brand_id      TEXT        NOT NULL,
    store_name    TEXT        NOT NULL,
    road_address  TEXT,
    geom          geometry(Point, 4326) NOT NULL,
    store_type    TEXT        NOT NULL DEFAULT 'NORMAL',
    source        TEXT        NOT NULL,
    external_id   TEXT        UNIQUE,
    is_active     BOOLEAN     NOT NULL DEFAULT true,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_stores_source CHECK (source IN ('PUBLIC_DATA', 'MANUAL', 'KAKAO_LOCAL')),
    CONSTRAINT ck_stores_type   CHECK (store_type IN (
        'NORMAL', 'DEPARTMENT_STORE', 'MART_TENANT',
        'HIGHWAY_REST_AREA', 'AIRPORT', 'HOSPITAL', 'CAMPUS'))
);

-- 반경 검색용. geography 캐스팅 표현식에 걸어야 인덱스를 탄다 (02_DB_SCHEMA.md §5.2)
CREATE INDEX IF NOT EXISTS idx_stores_geog
    ON stores USING GIST ((geom::geography));

CREATE INDEX IF NOT EXISTS idx_stores_brand
    ON stores (brand_id) WHERE is_active = true;

-- ---------- coupon_rules (F-04 RAG) ----------
CREATE EXTENSION IF NOT EXISTS vector;
 
CREATE TABLE IF NOT EXISTS coupon_rules (
    rule_id         TEXT PRIMARY KEY,
    content         TEXT        NOT NULL,      -- 임베딩 대상. 규칙 1개 = 행 1개
    embedding       vector(768) NOT NULL,
    embed_model     TEXT        NOT NULL DEFAULT 'gemini-embedding-001',
    brand_id        TEXT        NOT NULL,      -- 전 브랜드 공통은 '_common'
    rule_type       TEXT        NOT NULL DEFAULT 'GENERAL',
    store_type      TEXT        NOT NULL DEFAULT 'NORMAL',
    source_name     TEXT        NOT NULL,
    source_url      TEXT,
    effective_date  DATE,
    verified_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_rules_type CHECK (rule_type IN (
        'EXCLUSION', 'CROSS_USE', 'BALANCE', 'EXTENSION', 'PAYMENT', 'GENERAL')),
    CONSTRAINT ck_rules_store_type CHECK (store_type IN (
        'NORMAL', 'DEPARTMENT_STORE', 'MART_TENANT',
        'HIGHWAY_REST_AREA', 'AIRPORT', 'HOSPITAL', 'CAMPUS'))
);
 
CREATE INDEX IF NOT EXISTS idx_rules_filter
    ON coupon_rules (brand_id, store_type);
 
-- HNSW는 규칙이 수천 건을 넘길 때만. 100건 규모에서는 순차 스캔이 더 정확하고 빠르다
-- CREATE INDEX idx_rules_hnsw ON coupon_rules USING hnsw (embedding vector_cosine_ops);