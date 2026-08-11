-- ============================================================
-- 시연용 시드 데이터
-- 실행 순서: init.sql → seed.sql → (scripts/load_stores.py)
-- 원칙: 이 파일은 손으로 관리 가능한 소량만 담는다.
--       431건 공공데이터 매장은 db/stores_seed.csv에서 load_stores.py가 적재한다.
-- ============================================================

-- ---------- 개발용 유저 ----------
INSERT INTO users (uid, notification_granted)
VALUES ('dev-uid-0001', true)
ON CONFLICT (uid) DO NOTHING;

-- ---------- 수동 시드 매장 (MANUAL) ----------
-- 직영 브랜드는 소상공인 상가정보에 거의 없다 (스타벅스: 경기도 전체 7건 — 00_PROGRESS.md 미해결 이슈).
-- 시연 설득력을 위해 아주대 인근 매장을 손으로 등록한다.
-- external_id는 NULL로 둔다 (공공데이터 상가업소번호가 없으므로 UNIQUE 제약과 충돌하지 않음).
INSERT INTO stores (store_id, brand_id, store_name, road_address, geom, store_type, source)
VALUES
  ('str_seed_0001', 'starbucks', '스타벅스 아주대점',
   '경기도 수원시 영통구 월드컵로 206',
   ST_SetSRID(ST_MakePoint(127.043900, 37.281500), 4326), 'NORMAL', 'MANUAL'),
  ('str_seed_0002', 'starbucks', '스타벅스 수원역점',
   '경기도 수원시 팔달구 덕영대로 924',
   ST_SetSRID(ST_MakePoint(127.000700, 37.265900), 4326), 'NORMAL', 'MANUAL')
ON CONFLICT (store_id) DO NOTHING;

-- ---------- RAG 시연용 매장 유형 수동 태깅 (F-04, store_type 자동화 폐기 — 02_DB_SCHEMA.md §5.1) ----------
-- "스타벅스 백화점 입점 매장은 사용 불가" 시나리오를 시연하려면 DEPARTMENT_STORE 매장이 최소 1건 필요하다.
-- 아래는 예시 placeholder다. 실제 백화점 입점 매장으로 좌표를 교체해서 사용할 것.
-- INSERT INTO stores (store_id, brand_id, store_name, road_address, geom, store_type, source)
-- VALUES
--   ('str_seed_0003', 'starbucks', '스타벅스 갤러리아백화점수원점',
--    '경기도 수원시 팔달구 덕영대로 924',
--    ST_SetSRID(ST_MakePoint(127.000000, 37.266000), 4326), 'DEPARTMENT_STORE', 'MANUAL')
-- ON CONFLICT (store_id) DO NOTHING;