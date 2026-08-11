-- ============================================================
-- ⚠️  모든 데이터를 삭제한다. 개발 중에만 사용할 것.
-- ⚠️  Cloud SQL(배포) 인스턴스에서는 절대 실행하지 않는다.
-- 실행: psql -f reset.sql   (init.sql / seed.sql을 이어서 재실행한다)
-- ============================================================

DROP TABLE IF EXISTS coupon_rules CASCADE;
DROP TABLE IF EXISTS coupons      CASCADE;
DROP TABLE IF EXISTS stores       CASCADE;
DROP TABLE IF EXISTS users        CASCADE;

\i init.sql
\i seed.sql

-- stores_seed.csv(공공데이터 431건)는 SQL 파일로 관리하지 않으므로 여기서 자동 적재되지 않는다.
-- reset 후에는 아래를 별도로 실행할 것:
--   python scripts/load_stores.py