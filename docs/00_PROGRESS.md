00. 진행상황

최종 수정: 2026-08-11 (v7) 규칙: 세션을 옮길 때마다 이 문서를 먼저 갱신한다. 갱신하지 않은 채 다음 세션을 시작하지 않는다. 마감: 2026-08-12 발표

지금 상태 한 줄

Cloud Run 배포 완료. https://couponkok-api-xiwykxhkda-du.a.run.app — D-1~D-6 전부 통과(카카오맵·비동기 파싱 포함, 사용자 수동 확인). 다음 세션은 F-03(Gemini 브리핑) + F-04(RAG).

완료
 01_API_SPEC.md / 02_DB_SCHEMA.md 합의
 FastAPI 뼈대 4파일 (main / core / api/coupons / api/locations)
 GET /health — db 실제 확인으로 전환, "db":"ok" 반환
 POST /api/v1/coupons — 202 + 비동기 파싱 + 멱등성 키
 GET /api/v1/coupons/{id} — 폴링 조회
 Gemini 인증: Vertex AI + ADC, 실제 이미지 파싱 성공
 매장 시드 431건 확보 (경기도 상가정보 CSV → 수원시 8개 브랜드)
 Cloud SQL 인스턴스 생성 proj-aj06-211200020328:asia-northeast3:couponkok-db (PostgreSQL 15.18)
 확장 설치 확인 — PostGIS 3.6.0 / pgcrypto 1.3 / pgvector 0.8.5
 init.sql / seed.sql 적용, load_store.py로 431건 적재 (총 433건, PUBLIC_DATA 431 + MANUAL 2)
 F-02 매칭 쿼리 실측 검증 — EXPLAIN ANALYZE에서 BitmapAnd(idx_stores_geog, idx_stores_brand) 확인, 실행 0.155ms
 브랜드 정규화 재설계 — config/brands.json 단일 출처 + Gemini enum 분류 (C-7, C-8)
 저장소 인메모리 → Cloud SQL(SQLAlchemy) 교체. 서버 재시작 후에도 쿠폰 유지 확인
 마이그레이션 적용 db/migrate_20260810.sql — coupon_type / face_value / barcode_hash / image_gcs_paths / coupon_rules
 POST /api/v1/coupons 다중 이미지 (1~3장) — 2화면 병합 실측 성공 (C-10) megamgc 상단+선물정보 2장 → product_name + expires_at 동시 확보, 파싱 11초
 RESPONSE_SCHEMA에 coupon_type/face_value 누락 수정 — 수정 후 PRODUCT / 2000 정상 저장 확인
 다중 이미지 안전장치 실측 — 서로 다른 브랜드 2장 투입 시 MULTIPLE_COUPONS 정상 반환
 요청 전체 이미지 용량 상한 15MB (MAX_TOTAL_IMAGE_BYTES)
2026-08-11 세션 (웹 UI + 배포)
 웹 UI 1페이지 — static/index.html + StaticFiles 마운트. 로컬에서 지도·업로드·매칭 동작 확인
 Dockerfile / .dockerignore / deploy.ps1 작성 (.ps1은 ASCII 전용)
 git init + .gitignore 작성 (.env / tools/ / test_images/ / data/raw/ 제외 확인)
 deploy.ps1 $EnvVars 검토 — app/core.py Settings와 어긋난 이름 3건 수정
   (DB_PASS→DB_PASSWORD, GEMINI_BACKEND→GEMINI_AUTH_MODE, GOOGLE_CLOUD_PROJECT→GCP_PROJECT_ID).
   안 고쳤으면 배포는 성공해도 DB 연결 실패 + Gemini가 MOCK으로 동작했을 것
 Cloud SQL에 배포 전용 유저 couponkok_app 생성 + coupons/stores/users/coupon_rules 4개 테이블
   SELECT/INSERT/UPDATE/DELETE 권한 부여(db/grants_app_user.sql). 슈퍼유저(postgres)는 로컬 전용으로 남김
 deploy.ps1 PS 5.1 버그 2건 수정 — (1) 2>&1 병합 시 gcloud의 무해한 안내 메시지가
   $ErrorActionPreference=Stop과 만나 스크립트를 죽이던 것 (2) 서비스 계정 생성 직후
   IAM 바인딩이 전파 지연으로 실패하던 것 → 재시도 로직 추가
 .env에 DB_PASSWORD_APP 추가 — deploy.ps1 -SyncSecret이 로컬 postgres 비밀번호가 아니라
   실제 런타임 유저(couponkok_app)의 비밀번호를 Secret Manager에 올리도록 분리
 Cloud Run 배포 완료 — .\deploy.ps1 -Bootstrap
   URL: https://couponkok-api-xiwykxhkda-du.a.run.app (asia-northeast3, revision 20260811-120846)
 D-1~D-6 전부 통과 — D-1/D-2/D-5/D-6은 자동 확인, D-3(카카오맵 도메인)·D-4(비동기 파싱)는 사용자가 직접 확인
2026-08-10 세션 (F-02 검증)
 core.get_last_location() — 이미 구현돼 있었다. 추가 작업 없음
 _SQL_MATCH 좌표 파라미터를 EWKT 문자열 → ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) 로 변경 psycopg3가 str을 text로 보내 text→geography 캐스트에 의존하고 있었다. 02_DB_SCHEMA §7 원문 형태로 복귀
 json_agg 정렬에 c.created_at DESC tie-breaker 추가 — 만료일 동률 시 브리핑 문구가 실행마다 바뀌던 문제
 속도 이상치 필터 제거 (C-15) — 근거는 아래 결정 이력
 POST /api/v1/locations 실측 검증 완료 — TC-L1~L6, L9, L10 전부 통과
L1 아주대 37.2803, 127.0433 → 스타벅스 아주대점(143m) + 메가엠지씨커피 아주대점(207m) 2건. 거리순 정렬 / json_agg 다중 쿠폰 / days_left 서버 계산 / 브리핑 1장·2장 문구 분기 확인
L2 accuracy_m:150 → LOW_ACCURACY, L3 15분 전 recorded_at → STALE
L4 30m 이동 시 last_location_at 불변, 60m 이동 시 갱신 (scripts/verify_l4_l7.py)
L5 컴포즈커피 수원매교점 → matches: [] (INNER JOIN으로 쿠폰 없는 브랜드 제외)
L6 INVALID_COORDINATE / L9 TOO_MANY_POINTS / L10 VALIDATION_ERROR
 검증 도구 3종 추가 — test_locations.ps1(ASCII 전용), scripts/check_f02.py, scripts/verify_l4_l7.py
다음 할 일 (순서대로)
 git 첫 커밋 ← 지금 여기 (push는 별도 확인 후)
 F-03 Gemini 브리핑 (현재는 TEMPLATE 폴백만 동작) ← 다음 세션
 F-04 RAG — 쿠폰 이미지에서 규칙 추출 → 색인 → 검색
 파싱 정확도 10장 측정 / TC-01~05 / 사용자 테스트 2명
 제출 문서 06~17절 갱신, 발표자료, 리허설

배포 후 재검증 체크리스트 (D-1~D-6, 제출문서 SECTION 12·13에 그대로 옮길 것) — 2026-08-11 전부 통과

ID	항목	절차	통과 기준	결과
D-1	헬스 체크	Invoke-RestMethod <URL>/health	status=ok, db=ok (db=error면 유닉스 소켓/Secret 문제)	PASS (deploy.ps1 자체 검증)
D-2	정적 페이지	브라우저로 <URL>/	업로드 폼 + 지도 컨테이너 렌더	PASS (200, 33.5KB)
D-3	카카오맵	같은 화면	지도 타일 표시. 안 뜨면 도메인 등록 누락(콘솔 F12로 확인)	PASS (사용자 확인)
D-4	비동기 파싱	쿠폰 2장 업로드 → 폴링	15초 내 COMPLETED. PROCESSING 고착 시 --no-cpu-throttling 미적용	PASS (사용자 확인)
D-5	위치 매칭	아주대 37.2803, 127.0433	스타벅스 아주대점(143m) + 메가엠지씨커피 아주대점(207m), 거리순	PASS
D-6	비밀값 비노출	gcloud run services describe <SVC> --region ... --format yaml	출력에 DB 비밀번호 평문 없음(secretKeyRef만 보임)	PASS

D-3 주의: 브라우저 Geolocation API는 HTTPS 또는 localhost에서만 동작한다. Cloud Run은 HTTPS라 문제없지만, 배포 URL을 http://로 열면 위치 권한 요청 자체가 뜨지 않는다.
시연 전 정리 (5분)

sql

sql
-- 스키마 수정 전에 등록돼 필드가 빈 쿠폰. 카드를 펼치면 상품명 없는 행이 보인다
DELETE FROM coupons WHERE coupon_id IN (
  'cpn_01KZN78N7NDBP3469BRWG4ZF2F',
  'cpn_01KZNVFZ2H6VSHSFQHVPQ60ZVE'
);
결정 이력 (제출문서 11.1에 그대로 옮길 것)
#	변경	이유
C-1	AI Studio API 키 → Vertex AI + ADC	학교 프로젝트가 Vertex Express 모드라 generativelanguage 엔드포인트가 403(API_KEY_SERVICE_BLOCKED)
C-2	1차 클라이언트를 안드로이드 → 웹으로	심사자가 설치 없이 배포 URL로 접근 가능. 백그라운드 위치추적·FCM은 2차로 이관
C-3	파이썬 모듈 13개 → 4개로 통합	4일 스프린트에서 파일 탐색 비용이 모듈 분리 이득보다 큼
C-4	매장 데이터는 공공데이터 CSV 주력, 카카오는 런타임 보조 조회로 한정	카카오 약관상 결과의 영구 저장에 제한
C-5	DB는 로컬 Docker 건너뛰고 Cloud SQL 직행	Windows Home + WSL2 세팅 리스크가 4일 일정에 부적합. 배포 환경과 동일한 곳에서 개발
C-6	store_type 자동 태깅 폐기, 전량 NORMAL + 수동 태깅	경기도 CSV 실측: 이마트 매칭 2,670건 대부분이 이마트24 편의점(오탐)
C-7	브랜드 정규화를 문자열 사전 → Gemini enum 분류 + 사전 폴백 2단으로	표기 변형을 별칭으로 전수 등록하는 것이 비현실적. response_schema의 enum 제약으로 LLM 출력의 비결정성은 차단. 확신 없으면 UNKNOWN
C-8	브랜드 코드 단일 출처를 **config/brands.json**으로 분리	파싱 후처리와 적재 스크립트가 각자 사전을 들고 있다가 3건이 어긋나 매칭이 조용히 0건이 되는 상태였음
C-9	쿠폰 유형(coupon_type) + 액면가(face_value) 도입	메가 상품교환권에도 "사용가능금액 2,000원"이 표기됨. 상품권/금액권 경계는 쿠폰 속성이 아니라 브랜드 정책이므로, 화면에 보이는 사실만 컬럼에 담고 정책 판단은 F-04 RAG로 넘김
C-10	업로드 1건 = 이미지 최대 3장	카카오톡 선물하기는 한 쿠폰 정보가 2화면에 쪼개짐. Gemini 호출 1회로 병합. 2026-08-10 실측 검증 완료
C-11	바코드 원문 저장하지 않음. 마스킹값 + SHA-256 해시만	암호화(pgcrypto vs Fernet) 택일 문제를 "아예 저장하지 않는다"로 해소. 해시는 중복 등록 판정 전용이며 복호화 불가
C-12	RAG 지식 원천을 쿠폰 이미지 추출 + 수기 보강 하이브리드로	기프티콘 사용 조건은 정리된 공개 문서가 없고 브랜드마다 흩어져 있음. F-01의 Gemini Vision 파이프라인을 프롬프트만 바꿔 재사용. 이미지 추출은 EXCLUSION에 편중되므로 잔액·연장·중복할인은 수기 보강
C-13	POST /locations 좌표 배열 순차 필터링 → 최신 유효 좌표 1건 기준으로 축소	location_points 테이블을 두지 않아(02_DB_SCHEMA §0.2) 배열을 순차 판정해도 중간 결과가 저장되지 않는다. 얻는 것이 rejected_reasons 상세도뿐이므로 구현 비용을 지불하지 않음
C-14	다중 이미지 병합에 2단 안전장치 도입	잘못 병합된 쿠폰이 저장되면 위치 알림이 엉뚱한 브랜드로 나간다. 모델의 is_same_coupon과 서버의 per_image 재검증 중 하나라도 걸리면 MULTIPLE_COUPONS로 실패시킨다. 등록 실패가 오알림보다 싸다
C-15	속도 이상치 필터(IMPLAUSIBLE_SPEED) 제거	① 이상치 판정은 연속된 좌표열이 있어야 성립하는데 비교 대상이 덮어쓰기 컬럼 1건뿐이라, 한 번 튄 좌표가 저장되면 이후 정상 좌표가 반대로 폐기된다. ② recorded_at은 초 단위 절삭, last_location_at은 마이크로초 저장이라 요청 간격 1초 미만이면 경과시간이 음수가 되어 검사가 조용히 스킵된다(TC-L7 실측 -0.64초). ③ 웹 클라이언트(C-2)에서 지도로 좌표를 지정하는 조작에는 이동 속도 개념이 없어 시연 중 전 요청이 폐기된다. GPS 이상치 방어는 accuracy_m > 100 폐기가 대신하고, 좌표 이력 도입 시 재검토
C-16	배포 환경에서도 AUTH_DISABLED=true 유지 (01_API_SPEC §1.2 원문을 대체)	웹 클라이언트 전환(C-2)으로 심사자가 URL 접속만으로 시연을 봐야 하는데, Firebase 익명 인증은 브라우저마다 다른 uid를 발급해 시연용 쿠폰이 보이지 않게 만든다. 대신 배포 URL이 사실상 공용 계정이 되는 한계를 §1.2에 명시하고, 실제 개인 쿠폰은 등록하지 않는 운영 규칙으로 상쇄한다. 바코드 원문 미저장(C-11)이 노출 상한을 제한한다
C-17	비밀값 주입을 --set-env-vars가 아니라 Secret Manager(--set-secrets)로	DB 비밀번호가 Cloud Run 콘솔의 환경변수 탭과 gcloud run services describe 출력에 평문으로 남는다. 제출문서 점검 항목("환경 변수와 Secret의 실제 값이 노출되지 않았다")과 직접 충돌
C-18	배포 환경 DB 유저를 postgres(슈퍼유저)가 아니라 couponkok_app 전용 계정으로 분리	로컬 개발은 계속 postgres + Cloud SQL Auth Proxy를 쓰지만, 배포 환경은 4개 테이블(coupons/stores/users/coupon_rules)에 대한 SELECT/INSERT/UPDATE/DELETE만 가능한 별도 계정(db/grants_app_user.sql)으로 최소 권한 원칙 적용. .env에 DB_PASSWORD_APP을 별도로 두고 deploy.ps1 -SyncSecret이 이 값을 Secret Manager에 올린다
미해결 이슈
항목	내용
파싱 응답 시간	실측 1장 9초 / 2장 11초. 문서 5절 요구사항 3초는 달성 불가 → 목표를 15초로 상향 조정하고 사유를 5절에 명시(Gemini Vision 멀티모달 추론 특성). 폴링 예산 30초 내라 기능상 문제 없음
시연 대표 브랜드	위치 매칭(F-02)은 메가MGC커피(128건), RAG 예외조건(F-04)은 스타벅스. 아주대 좌표에서 두 브랜드가 동시에 잡히므로 한 화면에 다 보인다
좌표 이력 부재	location_points 없음 → 이동 경로·이상치 판정 불가 (C-13, C-15). 후반기 백로그
스키마 수정 전 쿠폰 잔존	coupon_type=UNKNOWN, product_name/face_value NULL인 행이 DB에 남아 있다. 재파싱하려면 원본 이미지가 필요한데 image_gcs_paths 미구현이라 삭제로 처리한다
Swagger UI 다중 파일 업로드	list[UploadFile]을 파일 선택기로 렌더하지 못함. 서버 문제 아님. 웹 UI의 <input type="file" multiple>로 해소 예정
GCS 업로드 미구현	image_gcs_paths 컬럼만 있고 실제 업로드 없음
Rate limiting 미구현	명세 §3·§5의 429는 정의만 있음. 시연 규모에서 불필요
Alembic 부재	init.sql과 migrate_*.sql 수동 이중 관리
카카오맵 키	해결됨 (2026-08-11) — localhost:8000 + 배포 URL 둘 다 등록 완료, 배포 환경에서 지도 렌더 확인
인증 우회 배포	C-16. 배포 URL = 단일 공용 계정(dev-uid-0001). 2차 최우선 과제
min-instances=1 비용	콜드 스타트(Vertex 초기화 포함 수 초) 회피용으로 --min-instances 1 사용. 발표 종료 후 --min-instances 0으로 되돌릴 것. Cloud SQL db-f1-micro도 상시 과금
	
확정된 것 (더 논의 불필요)
인스턴스 타임존 UTC 유지, 쿼리에서 (now() AT TIME ZONE 'Asia/Seoul')::date 명시
로컬 DB 접속: Cloud SQL Auth Proxy (127.0.0.1:5432)
Cloud Run 접속: .env의 INSTANCE_CONN만 채우면 유닉스 소켓으로 전환 (코드 변경 없음)
검색 반경 300m 고정, 임베딩 gemini-embedding-001 / vector(768)
좌표 처리는 최신 유효 좌표 1건 기준 (C-13), 필터는 범위·형식·정확도·신선도·50m 억제 5종
실행 메모

① Cloud SQL Auth Proxy — DB 쓰는 동안 항상 켜둘 것

powershell

powershell
cd backend-fastapi\tools
.\cloud-sql-proxy.exe proj-aj06-211200020328:asia-northeast3:couponkok-db --port 5432
Ready for new connections 확인
wsarecv ... 응답하지 않아 로그는 개별 커넥션 끊김이지 프록시 종료가 아니다. 프록시를 껐다 켜면 해소되고, 앱은 pool_pre_ping=True로 회복한다
배포 환경(Cloud Run)은 유닉스 소켓이라 이 문제가 없다. 로컬 전용 리스크

② 서버

powershell

powershell
cd backend-fastapi
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
기동 로그에 인증: vertex(us-central1) 확인. MOCK 모드로 기동합니다가 뜨면 .env가 안 읽힌 것이니 거기서 멈출 것
.env / config/brands.json / SQL 상수 수정 후에는 완전히 껐다 켤 것 (-reload가 감지 못 함)

③ 다중 이미지 업로드 테스트 (PowerShell)

curl은 Invoke-WebRequest 별칭이라 -F를 못 받는다. 반드시 curl.exe.

powershell

powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$id = [guid]::NewGuid().ToString()
curl.exe -s -X POST "http://127.0.0.1:8000/api/v1/coupons" `
  -F "images=@test_images/top.jpg;type=image/jpeg" `
  -F "images=@test_images/info.jpg;type=image/jpeg" `
  -F "client_upload_id=$id" | ConvertFrom-Json | ConvertTo-Json -Depth 6

;type=image/jpeg를 빼면 application/octet-stream으로 나가 415가 난다.

④ 위치 API 검증

powershell

powershell
cd backend-fastapi          # ★ scripts/ 안에서 돌리면 .env를 못 찾는다
python scripts\check_f02.py       # 쿠폰·매장·기준점 상태 한눈에
python scripts\verify_l4_l7.py    # TC-L4 자동 판정 + TC-L5 좌표 후보
.\test_locations.ps1              # TC-L1~L10 일괄
.ps1은 ASCII 전용으로 작성한다. PowerShell 5.1은 BOM 없는 스크립트를 CP949로 읽어, 한글 주석의 UTF-8 바이트가 뒤따르는 "나 )를 삼켜 엉뚱한 줄에서 파서 에러를 낸다
.py는 python scripts\xxx.py로 실행한다. .\xxx.py는 Windows 파일 연결로 넘어가 출력 없이 끝난다

⑤ 테스트 전 쿠폰 비우기 — uq_coupons_barcode 때문에 같은 쿠폰 재등록이 막힌다

sql

sql
DELETE FROM coupons WHERE uid = 'dev-uid-0001';

⑥ SQL 실행 (psql 미설치, 파이썬 러너 사용)

powershell

powershell
python db/apply_sql.py db/init.sql

⑦ 배포 (Windows PowerShell, backend-fastapi 기준)

powershell

powershell
cd backend-fastapi
.\deploy.ps1 -Bootstrap      # 최초 1회: API 활성화 / Artifact Registry / SA+IAM / Secret
.\deploy.ps1                 # 이후: 빌드 + 배포
.\deploy.ps1 -SkipBuild      # 환경변수만 바꿔 재배포 (빌드 생략, 30초)

.ps1은 ASCII 전용. 한글 주석을 넣는 순간 PS 5.1이 CP949로 읽어 엉뚱한 줄에서 파서가 깨진다
배포 후 스크립트가 출력하는 URL을 ① 01_API_SPEC §1.1 ② 카카오 JavaScript SDK 도메인 두 곳에 반영
로컬 개발은 여전히 Cloud SQL Auth Proxy를 쓴다. .env에 INSTANCE_CONN을 채워도 DB_HOST가 있으면 TCP 경로를 탄다(배포 시에는 DB_HOST를 주입하지 않아 유닉스 소켓으로 전환)