-- couponkok_app 전용 배포 계정 권한 부여
-- 실행 전제: gcloud sql users create couponkok_app 로 로그인 역할이 이미 생성돼 있어야 한다.
-- 슈퍼유저(postgres) 연결로 실행할 것. 스키마 소유자는 계속 postgres로 둔다.

GRANT CONNECT ON DATABASE couponkok TO couponkok_app;
GRANT USAGE ON SCHEMA public TO couponkok_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO couponkok_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO couponkok_app;
