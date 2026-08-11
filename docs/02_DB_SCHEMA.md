02. 데이터베이스 스키마 (DB Schema)

문서 상태: v0.3 최종 수정: 2026-08-10 적용 범위: MVP 1차 (F-01 쿠폰 OCR 파싱, F-02 GPS 기반 매장 매칭, F-04 RAG 예외 조건) DBMS: PostgreSQL 15.18 / PostGIS 3.6.0 / pgcrypto 1.3 / pgvector 0.8.5 (Cloud SQL asia-northeast3) 원칙: 4일 스프린트다. 테이블은 3개(+RAG 인덱스 1개)로 고정하고, 정규화보다 조회 한 방에 끝나는 구조를 택한다.

0. 설계 원칙과 의도적으로 잘라낸 것
0.1 원칙
핵심 테이블 3개 고정. users, coupons, stores. 그 외는 컬럼으로 흡수하거나 다음 스프린트로 넘긴다. coupon_rules는 RDB 도메인 테이블이 아니라 RAG 인덱스라 이 정책 밖이다(§8 참조).
정규화보다 조회 성능. 브랜드 마스터 테이블을 만들지 않고 brand_id를 양쪽 테이블에 문자열로 중복 보관한다. 위치 매칭 쿼리가 stores ⋈ coupons 2-way 조인으로 끝나야 하기 때문이다. 대신 config/brands.json을 코드 레벨의 단일 출처로 둔다(§4 참조).
계산값은 저장하지 않는다. days_left는 컬럼이 아니라 조회 시점에 expires_at - CURRENT_DATE로 계산한다. 저장하면 자정마다 전체 UPDATE가 필요해지고, 배치가 한 번만 밀려도 앱에 틀린 숫자가 뜬다.
ENUM 타입 대신 TEXT + CHECK. PostgreSQL 네이티브 ENUM은 값 추가에 ALTER TYPE이 필요하고 트랜잭션 안에서 다루기 번거롭다. 4일 동안 값이 몇 번 바뀔 것이 확실하므로 CHECK 제약이 낫다.
ID는 애플리케이션이 생성한다. 01_API_SPEC.md §1.4의 cpn_/str_ ULID를 그대로 PK로 쓴다. DB 시퀀스를 쓰면 API 응답에 넣기 전에 변환 계층이 하나 더 생긴다.
store_id는 적재 스크립트가 아니라 시드 파일 생성 시점에 고정한다. 재적재 시마다 ID가 바뀌면 이미 보낸 알림·로그의 매장 추적이 끊긴다. db/stores_seed.csv에 ULID를 박아 커밋하고, 적재는 ON CONFLICT (external_id) DO UPDATE로 멱등 처리한다.
바코드 원문은 저장하지 않는다. 암호화 방식(pgcrypto vs 애플리케이션 레이어) 택일 문제 자체를 없앤다 — 자세한 이유는 §4 참조.
스키마 변경은 두 경로로 관리한다. 신규 환경은 init.sql, 이미 데이터가 있는 환경(Cloud SQL)은 migrate_<날짜>.sql. CREATE TABLE IF NOT EXISTS는 기존 테이블에 컬럼을 추가하지 못하므로 ALTER가 별도로 필요하다. 두 파일이 같은 결과를 내도록 손으로 맞춰야 하며, 이 이중 관리가 Alembic을 뺀 대가다.
0.2 3테이블 제약 때문에 잘라낸 것 — 반드시 인지할 것
원래 필요한 것	어떻게 대체했나	감수하는 결과
devices 테이블 (§6 FCM 토큰)	users.fcm_token 단일 컬럼	1 사용자 = 1 기기. 명세를 1:1로 하향 조정했다(§10). 단, FCM 자체가 1차에서는 미구현이라(C-2, 웹 클라이언트) 당장 영향은 없다
location_points 테이블 (§5 좌표 이력)	users.last_location 최신 1건만 갱신	이동 경로가 남지 않는다. "직전 저장 좌표와 50m 이내면 저장 생략" 규칙은 마지막 좌표 1개로만 판정 가능. 속도 이상치 필터는 이 제약 때문에 성립하지 않아 제거했다(C-15, 01_API_SPEC §5.1)
notification_logs 테이블	coupons.last_notified_at + users.last_notified_at	쿨다운을 (사용자, 매장)이 아니라 (사용자, 쿠폰) 단위로 근사한다. 같은 쿠폰을 여러 매장에서 쓸 수 있는 브랜드에서는 알림이 덜 간다. 스팸보다는 낫다는 판단
brands 마스터 테이블	brand_id TEXT 중복 보관 + config/brands.json	브랜드명 오타·표기 변경 시 두 테이블과 파일을 함께 고쳐야 한다. 브랜드 수가 9개인 MVP에서는 감당 가능
이미지 테이블 (업로드 1건 = N장)	coupons.image_gcs_paths TEXT[]	이미지별 메타데이터(순서·역할·크기)를 남기지 않는다. 어느 화면에서 어떤 필드를 얻었는지 추적 불가
바코드 암호화 저장	저장하지 않음. 마스킹값 + SHA-256 해시만	사용자가 앱에서 바코드 원문을 열 수 없다. 매장에서는 원본 캡처를 직접 보여줘야 한다. 대신 유출 위험이 구조적으로 0이 된다

위 항목은 기술 부채로 기록하고 넘어가는 것이지 문제가 없다는 뜻이 아니다. 제출 문서 11.1(주요 변경 결정)과 16절(후반기 백로그)에 그대로 옮겨 적을 것.

1. 공통 규약
항목	규칙	이유
시각 컬럼	TIMESTAMPTZ, 항상 UTC 저장	앱이 KST 변환. 서버·DB가 어느 리전에 있든 값이 흔들리지 않는다
날짜 컬럼	DATE (expires_at만 해당)	쿠폰 유효기간은 KST 달력 날짜이지 시각이 아니다. TIMESTAMPTZ로 두면 UTC 변환 과정에서 하루가 밀린다
문자열 길이	VARCHAR(n) 대신 TEXT	PostgreSQL에서 성능 차이가 없다. 길이 제한은 애플리케이션(pydantic)에서 건다
좌표	geometry(Point, 4326)	WGS84. GPS가 그대로 주는 좌표계
스키마 변경	신규 환경은 init.sql, 기존 DB는 migrate_<날짜>.sql	§0.1-8 참조. Cloud SQL에는 매장 431건이 이미 있어 재생성 비용이 크다
2. ERD
┌─────────────────────────┐
│         users           │
│─────────────────────────│
│ PK uid            TEXT  │◄──────┐  (Firebase 익명 인증 uid)
│    fcm_token       TEXT  │       │
│    last_location   Point │       │
│    last_location_at      │       │
└─────────────────────────┘       │
                                  │ 1
                                  │
                                  │ N
┌─────────────────────────┐       │
│        coupons          │       │
│─────────────────────────│       │
│ PK coupon_id       TEXT  │       │
│ FK uid             TEXT  │───────┘
│    brand_id        TEXT  │──────┐   ※ FK 아님. 논리적 조인 키
│    coupon_type      TEXT  │      │      (brands 마스터 테이블 없음, config/brands.json이 단일 출처)
│    face_value       INT   │      │
│    expires_at       DATE  │      │
│    barcode_hash      TEXT  │      │   ※ 원문 없음. 중복 판정 전용
│    image_gcs_paths  TEXT[]│      │
│    status           TEXT  │      │
└─────────────────────────┘      │
                                 │
┌─────────────────────────┐      │
│         stores          │      │
│─────────────────────────│      │
│ PK store_id       TEXT  │      │
│    brand_id       TEXT  │◄─────┘
│    geom           Point │  ← GIST 인덱스 (반경 검색)
│    store_type     TEXT  │  ← RAG 예외 조건 필터에 사용
└─────────────────────────┘

┌─────────────────────────────┐
│       coupon_rules           │   ※ RAG 인덱스. §0.1의 "3테이블" 정책 밖
│───────────────────────────── │
│ PK rule_id          TEXT     │
│    embedding    vector(768)  │
│    brand_id          TEXT    │──── stores.brand_id / coupons.brand_id 와 같은 코드 공간
│    store_type        TEXT    │──── stores.store_type 과 같은 값 집합
│    rule_type          TEXT    │
│    source_name        TEXT    │
│    verified_by        TEXT    │  ← NULL이면 미검증, 브리핑에서 단정 표현 금지
└─────────────────────────────┘

관계 요약

users 1 : N coupons — 실제 외래키(ON DELETE CASCADE). 사용자 탈퇴 시 쿠폰도 함께 삭제되어야 한다.
coupons N : N stores (via brand_id) — 외래키를 걸지 않는다. stores는 공공데이터로 주기 갱신되는 테이블이라 재적재 시 FK가 걸려 있으면 삭제가 막힌다. 또 Gemini가 브랜드 매칭에 실패하면 coupons.brand_id가 NULL이 되는데, 이건 정상 상태이지 무결성 위반이 아니다.
coupon_rules는 brand_id, store_type으로 stores/coupons와 논리적으로만 연결된다. 외래키 없음 — RAG 인덱스는 도메인 테이블과 생명주기가 다르다.
3. users — 사용자

Firebase 익명 인증으로 발급된 uid가 그대로 PK다. 별도 회원가입이 없으므로 이메일·비밀번호 컬럼은 존재하지 않는다.

컬럼명	타입	제약조건	설명
uid	TEXT	PK	Firebase ID 토큰에서 추출한 uid (28자 내외). 요청 본문에서 받지 않는다
created_at	TIMESTAMPTZ	NOT NULL DEFAULT now()	최초 익명 로그인 시각
fcm_token	TEXT	UNIQUE (NULL 허용)	FCM 등록 토큰. 같은 토큰이 다른 uid로 오면 기존 매핑을 덮어쓴다 (기기 초기화 재설치 대응). 1차에서는 미사용 — 웹 클라이언트(C-2)라 FCM이 2차로 이관됨
fcm_token_updated_at	TIMESTAMPTZ	NULL 허용	토큰 갱신 시각. 장기 미갱신 토큰은 발송 대상에서 제외 판단에 사용
notification_granted	BOOLEAN	NOT NULL DEFAULT false	Android 13+ POST_NOTIFICATIONS 권한 상태. false면 서버가 FCM 발송을 시도조차 하지 않는다
device_model	TEXT	NULL 허용	장애 분석용 (예: SM-S928N)
app_version	TEXT	NULL 허용	버전별 호환 처리용
last_location	geometry(Point, 4326)	NULL 허용	마지막으로 수신한 좌표. 중복 좌표 억제(50m 규칙)의 기준점
last_location_at	TIMESTAMPTZ	NULL 허용	마지막 위치 갱신 시각. 50m 억제로 갱신을 생략하면 이 값도 그대로 남는다 — TC-L4의 판정 지표
last_notified_at	TIMESTAMPTZ	NULL 허용	마지막 푸시 발송 시각. 1일 알림 상한·전역 쿨다운 근사에 사용

주의

last_location은 덮어쓰기 전용이다. 이력이 필요해지는 순간(알림 쿨다운을 매장 단위로 바꿀 때) 별도 테이블이 반드시 필요해진다.
last_location에는 인덱스를 걸지 않는다. "특정 지점 주변의 사용자 찾기"는 MVP 시나리오에 없다. 쓰기만 잦은 컬럼에 GIST를 걸면 손해다.
4. coupons — 파싱된 쿠폰

01_API_SPEC.md §4의 조회 응답이 이 테이블 한 행에서 그대로 만들어지도록 설계했다.

컬럼명	타입	제약조건	설명
coupon_id	TEXT	PK	cpn_ + ULID. 애플리케이션 생성
uid	TEXT	NOT NULL, FK → users(uid) ON DELETE CASCADE	소유자. 모든 조회에서 이 값을 검증한다 (§4 403 FORBIDDEN)
client_upload_id	UUID	NOT NULL	클라이언트 생성 멱등성 키
status	TEXT	NOT NULL DEFAULT 'PROCESSING', CHECK: PROCESSING/COMPLETED/FAILED	파싱 상태
brand_id	TEXT	NULL 허용	정규화된 내부 브랜드 코드 (예: megamgc). config/brands.json이 단일 출처. NULL이면 위치 매칭 대상에서 제외된다
brand_name	TEXT	NULL 허용	이미지에서 읽은 원문 브랜드명 (예: 메가MGC커피)
coupon_type	TEXT	NOT NULL DEFAULT 'UNKNOWN', CHECK: PRODUCT/AMOUNT/DISCOUNT/UNKNOWN	쿠폰 유형. 화면에 보이는 사실만 담는다 — "상품권으로 다른 상품 교차 사용이 되는가" 같은 정책 판단은 F-04 RAG로 넘긴다
product_name	TEXT	NULL 허용	상품명 (예: 아이스 카페 아메리카노 T). coupon_type='AMOUNT'면 정상적으로 없다
face_value	INTEGER	CHECK: NULL 또는 > 0	액면가·사용가능금액(원). PRODUCT에도 존재할 수 있다 (상품교환권에 금액이 함께 표기되는 경우가 흔함). 인식 실패 시 NULL
expires_at	DATE	NULL 허용	KST 달력 날짜. 해당 날짜 23:59:59(KST)까지 유효
barcode_masked	TEXT	NULL 허용	마스킹된 바코드. 목록·상세 응답에는 이 값만 나간다
barcode_hash	TEXT	부분 UNIQUE (아래)	바코드 숫자 + salt의 SHA-256 hex. 중복 등록 판정 전용, 복호화 불가
barcode_format	TEXT	NULL 허용	CODE128/EAN13/QR/UNKNOWN
is_used	BOOLEAN	NOT NULL DEFAULT false	사용 완료 여부. 매칭 대상에서 제외하는 기준
used_at	TIMESTAMPTZ	NULL 허용	사용 완료 처리 시각. 가설 검증 지표(전환율)의 원천 데이터
confidence	JSONB	NULL 허용	필드별 신뢰도 {"brand":0.98,"product_name":0.91,"expires_at":0.87}
needs_review	BOOLEAN	NOT NULL DEFAULT false	신뢰도 0.7 미만 필드 존재 여부. 앱이 "정보를 확인해주세요" 배지 표시. coupon_type='AMOUNT'면 product_name 신뢰도는 이 판정에서 제외한다 — 금액권에 상품명이 없는 것은 정상이다
error_code	TEXT	NULL 허용	status='FAILED'일 때만 채워진다 (NOT_A_COUPON 등)
error_message	TEXT	NULL 허용	사용자 노출용 한국어 문장
image_gcs_paths	TEXT[]	NULL 허용	원본 이미지 GCS 경로 배열. 업로드 1건 = 최대 3장(카카오톡 선물하기가 2화면으로 쪼개져 있어서, C-10)
last_notified_at	TIMESTAMPTZ	NULL 허용	이 쿠폰으로 마지막 알림을 보낸 시각. 쿨다운 근사
created_at	TIMESTAMPTZ	NOT NULL DEFAULT now()	업로드 접수 시각
completed_at	TIMESTAMPTZ	NULL 허용	파싱 종료 시각 (성공/실패 공통). completed_at - created_at이 곧 성능 요구사항 3초의 측정값 (실측 9초, §10 미해결 이슈)

제약조건

이름	정의	이유
uq_coupons_upload	UNIQUE (uid, client_upload_id)	멱등성 보장. 이 제약 하나가 §3의 중복 업로드 시도 방지 전부다
ck_coupons_status	CHECK (status IN ('PROCESSING','COMPLETED','FAILED'))	상태값 오염 방지
ck_coupons_completed	CHECK (status = 'PROCESSING' OR completed_at IS NOT NULL)	종료 상태인데 종료 시각이 없는 행을 막는다
ck_coupons_type	CHECK (coupon_type IN ('PRODUCT','AMOUNT','DISCOUNT','UNKNOWN'))	유형값 오염 방지
ck_coupons_face_value	CHECK (face_value IS NULL OR face_value > 0)	0원·음수 방지
uq_coupons_barcode	UNIQUE (uid, barcode_hash) WHERE barcode_hash IS NOT NULL	같은 쿠폰의 재등록 차단. client_upload_id는 "같은 업로드 시도"만 막고, 사용자가 나중에 같은 쿠폰을 다시 찍어 올리는 것은 못 막는다. 바코드를 못 읽은 행(NULL)은 부분 인덱스라 제외된다

바코드는 원문을 저장하지 않는다

pgcrypto(DB 레벨) vs 애플리케이션 레이어(Fernet 등) 암호화를 두고 고민했으나, 4일 프로젝트에서 키 관리(Secret Manager 주입, 로테이션, SQL 로그 노출 방지)를 제대로 다룰 여유가 없다고 판단해 저장 자체를 하지 않기로 했다.

표시용 barcode_masked(앞 4자리·뒤 4자리만)와 중복 판정용 barcode_hash(salt + SHA-256)만 남긴다. 둘 다 원문으로 복원할 수 없다.
대가는 앱에서 바코드 원본 화면을 열 수 없다는 것이다. 5.1 제외 범위("바코드 이미지 단순 확대 뷰어까지만 제공")와 이미 정합한다.
GET /api/v1/coupons/{id}/barcode 엔드포인트는 조회할 대상 자체가 없으므로 명세에서 영구 삭제했다(01_API_SPEC.md §9).
5. stores — 매장 위치 (PostGIS)

소상공인시장진흥공단 상가정보 데이터를 1차 적재하고, 누락 브랜드는 카카오 로컬 API로 보완한다.

컬럼명	타입	제약조건	설명
store_id	TEXT	PK	str_ + ULID
brand_id	TEXT	NOT NULL	coupons.brand_id와 매칭되는 정규화 코드. 이 값이 곧 조인 키
store_name	TEXT	NOT NULL	표시용 매장명 (예: 메가MGC커피 아주대점)
road_address	TEXT	NULL 허용	도로명 주소
geom	geometry(Point, 4326)	NOT NULL	매장 좌표 (경도, 위도 순). SRID 4326 고정
store_type	TEXT	NOT NULL DEFAULT 'NORMAL'	NORMAL/DEPARTMENT_STORE/MART_TENANT/HIGHWAY_REST_AREA/AIRPORT/HOSPITAL/CAMPUS. F-04 RAG 예외 조건 판정의 입력값
source	TEXT	NOT NULL, CHECK: PUBLIC_DATA/MANUAL/KAKAO_LOCAL	데이터 출처. MANUAL은 공공데이터에 없는 직영 브랜드 수동 시드
external_id	TEXT	UNIQUE (NULL 허용)	원천 데이터의 상가업소번호. 재적재 시 중복 방지용
is_active	BOOLEAN	NOT NULL DEFAULT true	폐업 매장 처리. 물리 삭제하지 않는다 (이미 발송한 알림의 추적성 유지)
updated_at	TIMESTAMPTZ	NOT NULL DEFAULT now()	마지막 갱신 시각
5.1 store_type을 왜 여기에 두는가

문서 1.1의 RAG 역할이 "스타벅스 기프티콘은 백화점 입점 매장에서는 사용 불가" 다. 이걸 처리하려면 **"지금 이 매장이 백화점 입점점인가"**를 먼저 알아야 하는데, 그건 벡터 검색이 아니라 컬럼 조회로 답할 문제다.

store_type(구조화 사실) → SQL이 판정
"그 매장 유형에서 이 브랜드 쿠폰이 되는가"(비구조화 약관 텍스트) → RAG가 판정

이 경계를 흐리면 RAG가 매장 존재 여부까지 지어내기 시작한다.

[폐기] 상호명 키워드 자동 태깅은 채택하지 않는다 (C-6).

경기도 상가정보 CSV 실측 결과 이마트 매칭 2,670건 중 대부분이 이마트24 편의점이었다
(대형마트 이마트는 경기도에 수십 개뿐). 이 규칙대로 태깅하면 정상 사용 가능한 편의점
전체가 MART_TENANT로 오분류되고, F-04가 이를 "사용 불가"로 안내해 매칭이 조용히 사라진다.
1차 적재는 전량 NORMAL로 두고, RAG 시연에 쓸 매장 2~3곳만 수동으로 태깅한다.

적재 결과 (2026-08-10 확정): 총 433건(공공데이터 431 + 수동 시드 2), 전량 store_type='NORMAL'. 브랜드별 megamgc 128 / compose 79 / paris 55 / ediya 43 / twosome 42 / paikdabang 40 / baskinrobbins 22 / tourlesjours 22 / starbucks 2(수동 시드).

5.2 ⚠️ 반경 검색의 함정 — 반드시 읽을 것

geometry(Point, 4326)에 ST_DWithin(geom, point, 300)을 쓰면 300m가 아니라 300도 로 해석된다. SRID 4326의 단위는 미터가 아니라 도(degree)이기 때문이다. 지구 전체가 반경 안에 들어온다.

반드시 geography로 캐스팅한다.

sql
-- ❌ 틀림: 단위가 도(degree)
ST_DWithin(s.geom, ST_SetSRID(ST_MakePoint(127.0433, 37.2803), 4326), 300)

-- ✅ 맞음: 단위가 미터
ST_DWithin(s.geom::geography, ST_SetSRID(ST_MakePoint(127.0433, 37.2803), 4326)::geography, 300)

그리고 캐스팅한 표현식에 인덱스를 걸어야 인덱스를 탄다. 일반 GIST(geom)은 위 쿼리에서 사용되지 않는다.

sql
CREATE INDEX idx_stores_geog ON stores USING GIST ((geom::geography));

실측 검증 완료 (2026-08-10). EXPLAIN ANALYZE에서 BitmapAnd(idx_stores_geog, idx_stores_brand) 확인, 실행 시간 0.155ms. §7의 쿼리로 재현 가능하다.

6. 인덱스
인덱스	대상	목적
idx_stores_geog	stores USING GIST ((geom::geography))	반경 300m 검색. F-02의 핵심
idx_stores_brand	stores (brand_id) WHERE is_active	브랜드 조인
idx_coupons_usable	coupons (uid, brand_id) 부분 인덱스	사용 가능한 쿠폰만 좁혀 읽기
idx_coupons_uid_expires	coupons (uid, expires_at) 부분 인덱스	F-05 만료 임박 순 목록
uq_coupons_upload	coupons (uid, client_upload_id)	멱등성 (UNIQUE 제약이 인덱스 겸용)
uq_coupons_barcode	coupons (uid, barcode_hash) WHERE barcode_hash IS NOT NULL	동일 쿠폰 재등록 차단
idx_rules_filter	coupon_rules (brand_id, store_type)	RAG 사전 필터

부분 인덱스(WHERE status='COMPLETED' AND NOT is_used)를 쓰는 이유: 매칭 쿼리는 항상 이 조건을 달고 들어온다. 실패·사용완료 행까지 인덱스에 넣으면 크기만 커진다.

7. 핵심 쿼리 — 위치 기반 매칭 (F-02)

POST /api/v1/locations 응답의 matches 배열을 만드는 쿼리다. 이 한 방으로 끝난다.

sql
SELECT
    s.store_id,
    s.store_name,
    s.brand_id,
    s.store_type,
    ST_Y(s.geom)::numeric(10,6) AS lat,
    ST_X(s.geom)::numeric(10,6) AS lng,
    ROUND(ST_Distance(
        s.geom::geography,
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
    ))::int AS distance_m,
    s.road_address,
    s.source,
    json_agg(
        json_build_object(
            'coupon_id',    c.coupon_id,
            'coupon_type',  c.coupon_type,
            'product_name', c.product_name,
            'face_value',   c.face_value,
            'expires_at',   c.expires_at,
            'days_left',    (c.expires_at - (now() AT TIME ZONE 'Asia/Seoul')::date)  -- 서버가 계산, KST 기준
        )
        ORDER BY c.expires_at ASC, c.created_at DESC         -- 만료 임박 순, 동률이면 최근 등록 순
    ) AS available_coupons
FROM stores s
JOIN coupons c
  ON  c.brand_id = s.brand_id
  AND c.uid      = :uid
  AND c.status   = 'COMPLETED'
  AND c.is_used  = false
  AND c.expires_at >= (now() AT TIME ZONE 'Asia/Seoul')::date  -- 오늘까지 유효 (KST 기준, D-1)
WHERE s.is_active
  AND ST_DWithin(
        s.geom::geography,
        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        :radius_m)
GROUP BY s.store_id
ORDER BY distance_m ASC
LIMIT 20;
좌표는 lat/lng를 각각 파라미터로 넘기고 SQL 안에서 ST_SetSRID(ST_MakePoint(...))로 조립한다. EWKT 문자열('SRID=4326;POINT(...)')을 하나의 파라미터로 넘기면 psycopg3가 이를 text 타입으로 전송하는데, text → geography 캐스트는 text → geometry와 달리 보장되지 않는다. MakePoint의 인자 순서는 (경도, 위도)다. 뒤집으면 서울 좌표가 남극 근처로 간다.
json_agg의 정렬에 c.created_at DESC tie-breaker가 필요하다. 만료일이 같은 쿠폰이 둘 이상이면 순서가 비결정적이 되고, 브리핑이 available_coupons[0]을 참조하므로 같은 요청이 실행마다 다른 문장을 낸다.
JOIN(INNER)이므로 쿠폰이 없는 매장은 자동으로 빠진다 — 명세 §5의 "쿠폰이 1개 이상 있는 매장만 포함" 규칙이 조인 종류만으로 구현된다.
인스턴스 타임존 플래그는 건드리지 않는다(로컬 Docker와 Cloud SQL 간 동작 불일치 방지, D-1).
쿼리에서 항상 (now() AT TIME ZONE 'Asia/Seoul')::date로 명시한다.
8. Vector DB 스키마 (RAG용)
8.1 pgvector를 택한 이유
	pgvector (채택)	FAISS
인프라	이미 쓰는 Postgres에 확장만 추가	별도 인덱스 파일 관리
메타데이터 필터	WHERE brand_id = 'megamgc'로 SQL 그대로	별도 사전/후처리 필요
Cloud Run 궁합	상태를 DB가 보유	컨테이너가 stateless라 인스턴스마다 인덱스 로드
문서 갱신	UPDATE 한 줄	인덱스 재빌드 후 재배포
속도 (수백 건 이하)	충분	더 빠르지만 체감 차이 없음

결정적인 건 메타데이터 필터링이다. 우리 검색은 항상 "메가MGC커피 + 일반 매장" 처럼 브랜드로 먼저 좁힌 뒤 의미 검색을 한다. FAISS는 이 사전 필터가 번거롭고, 잘못하면 다른 브랜드 약관이 검색되어 엉뚱한 브랜드의 규칙으로 사용 불가 안내를 하는 사고가 난다. pgvector면 WHERE 절 하나로 원천 차단된다.

pgvector 0.8.5 가용성은 Cloud SQL PostgreSQL 15.18에서 실측 확인했다 (2026-08-10).

8.2 coupon_rules 테이블 (벡터 스토어)

F-04 착수와 함께 실제로 생성했다. §0.1의 "3테이블" 정책 밖이다 — RDB 도메인 테이블이 아니라 RAG 인덱스다.

컬럼명	타입	설명
rule_id	TEXT PK	rul_ + ULID
content	TEXT	임베딩 대상 원문. 규칙 1개 = 행 1개
embedding	vector(768)	gemini-embedding-001 기준 768차원
embed_model	TEXT	NOT NULL DEFAULT 'gemini-embedding-001'. 모델을 바꾸면 차원도 바뀌므로 행마다 함께 기록한다
brand_id	TEXT	필터 키. 전 브랜드 공통 규칙은 _common
rule_type	TEXT	EXCLUSION(사용 불가) / CROSS_USE(다른 상품 교환·차액 결제) / BALANCE(잔액 처리·환불) / EXTENSION(유효기간 연장) / PAYMENT(결제수단·중복 할인) / GENERAL
store_type	TEXT	이 규칙이 걸리는 매장 유형. stores.store_type과 같은 값 집합을 쓴다
source_name	TEXT	출처 (예: 메가MGC커피 쿠폰 표기, 이마트 금액권 쿠폰 표기)
source_url	TEXT	원문 링크 (있는 경우)
effective_date	DATE	확인 시행일. 오래된 규칙 판별용
verified_by	TEXT	팀에서 사람이 직접 확인한 사람 이름. NULL이면 미검증 — 브리핑에서 단정 표현을 쓰지 않는다
created_at	TIMESTAMPTZ	적재 시각

rule_type에 CROSS_USE를 둔 이유: 메가MGC커피 상품교환권에 "사용가능금액 2,000원"이 함께 표기된다. "이 상품권으로 다른 음료를 사고 차액을 낼 수 있는가"는 쿠폰의 속성이 아니라 브랜드 정책이며, 컬럼으로 표현할 수 없는 이 판단이 RAG가 답해야 할 대표 질문이다(§4 coupon_type 참조).

8.3 지식 원천 — 쿠폰 이미지 추출 + 수기 보강 하이브리드 (C-12)

기프티콘 사용 조건은 정리된 공개 문서가 없고 브랜드마다 흩어져 있다. 대신 팀이 보유한 쿠폰 이미지에 실제 조건이 표기되어 있다 — 예: 이마트 금액권의 교환처 : 이마트(안양/부천점제외/트레이더스/노브랜드(직영)).

지식 원천은 두 가지다.

팀 보유 쿠폰 이미지에서 Gemini Vision으로 추출한 규칙 — 실제 표기 기반, 자동 확장 가능. F-01의 파싱 파이프라인을 프롬프트만 바꿔 재사용한다.
이미지에 표기되지 않는 정책(잔액 처리·기간 연장·중복 할인·교차 사용)에 대한 수기 규칙 — 브랜드 고객센터 안내를 요약 재작성.

①만으로는 규칙 유형이 EXCLUSION(사용 제외 매장)에 편중되고, ②만으로는 브랜드가 늘 때 관리가 불가능하다. 두 경로가 서로의 공백을 메운다.

작성 순서는 ① → 공백 확인 → ② 보강이다. 수기부터 쓰면 이미지에서 나올 규칙까지 중복 작성하게 된다.

추출 파이프라인

data/rule_images/*.jpg          (팀원 보유 쿠폰 캡처)
   ↓ scripts/extract_rules.py   (Gemini Vision, 규칙 추출 프롬프트)
data/rules.json                 (규칙 후보, verified_by = null)
   ↓ 사람이 검수하고 verified_by 기입
   ↓ scripts/index_rules.py     (임베딩 → coupon_rules INSERT)
검색 가능

모든 규칙은 source_name으로 출처를, verified_by로 검증자를 남긴다. verified_by가 NULL인 규칙은 미검증으로 간주하며 브리핑에서 단정 표현("사용 가능합니다")을 쓰지 않는다.

저작권: 원문(카카오톡·기프티쇼 등 발급 플랫폼 표기)을 복제하지 않고 요약 재작성한다.

8.4 청킹 전략

고정 길이(500자 등)로 자르지 않는다. 규칙 1개 = 청크 1개다.

우리가 다루는 지식은 논문이 아니라 짧고 독립적인 규칙 문장들이다. 고정 길이로 자르면 "단, 안양/부천점은 제외합니다" 가 앞 문장과 분리되어, 조건이 사라진 채 '사용 가능'으로 검색되는 최악의 실패가 발생한다.

json
{
  "content": "이마트 금액권은 안양점과 부천점에서 사용할 수 없다. 트레이더스와 노브랜드는 직영점에 한해 사용 가능하다.",
  "brand_id": "emart",
  "rule_type": "EXCLUSION",
  "store_type": "NORMAL",
  "source_name": "이마트 전용 금액권 교환처 표기",
  "source_url": null,
  "effective_date": "2026-08-10",
  "verified_by": "홍길동"
}

한 규칙이 여러 매장 유형에 걸리면 유형별로 행을 복제한다. 검색 필터가 단순해지는 이득이 저장 중복 손해보다 크다. 전체 규칙 수는 브랜드 9개 × 평균 2~3개 = 약 20건으로 목표한다.

8.5 검색 흐름
매장 접근 감지 (brand_id, store_type 확보)
    ↓
① SQL 사전 필터: WHERE brand_id IN (:brand, '_common') AND store_type IN (:type, 'NORMAL')
    ↓
② 벡터 검색: ORDER BY embedding <=> :query_vec LIMIT 3   (코사인 거리)
    ↓
③ 거리 임계값 컷: 유사도가 기준(0.6) 미만이면 "관련 규칙 없음"으로 처리
    ↓
④ Gemini에 근거로 전달 → 브리핑 생성 (source_name 함께 출력)
sql
SELECT rule_id, content, source_name, source_url,
       1 - (embedding <=> :qvec) AS similarity
FROM coupon_rules
WHERE brand_id IN (:brand_id, '_common')
  AND store_type IN (:store_type, 'NORMAL')
ORDER BY embedding <=> :qvec
LIMIT 3;

인덱스는 규칙이 수천 건을 넘길 때만 만든다. 20~100건 규모에서는 순차 스캔이 더 빠르고, HNSW 인덱스는 근사 검색이라 오히려 재현율이 떨어진다.

sql
-- 규칙이 충분히 많아졌을 때만
CREATE INDEX idx_rules_hnsw ON coupon_rules
    USING hnsw (embedding vector_cosine_ops);
8.6 ③번 단계가 가장 중요하다

검색 결과가 없거나 유사도가 낮으면 "제한 없음"이 아니라 "확인된 정보 없음"으로 처리한다.

규칙을 못 찾은 것과 규칙이 없는 것은 완전히 다르다. 전자를 후자로 바꿔 말하는 순간 "이 매장에서 쓸 수 있습니다" 라는 근거 없는 확답이 나가고, 사용자는 매장에 가서 거절당한다. 알림을 안 보내는 실패가, 틀린 알림을 보내는 실패보다 훨씬 싸다.

같은 원칙이 verified_by에도 적용된다 — 미검증 규칙은 근거로 쓰이더라도 브리핑 문장에서 단정형을 쓰지 않는다.

9. 초기화 DDL
sql
-- ============================================================
-- 쿠폰콕 MVP 1차 스키마
-- 실행: python db/apply_sql.py db/init.sql   (psql 대체 러너)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

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

    brand_id          TEXT,
    brand_name        TEXT,

    coupon_type       TEXT        NOT NULL DEFAULT 'UNKNOWN',
    product_name      TEXT,
    face_value        INTEGER,
    expires_at        DATE,

    barcode_masked    TEXT,
    barcode_hash      TEXT,
    barcode_format    TEXT,

    is_used           BOOLEAN     NOT NULL DEFAULT false,
    used_at           TIMESTAMPTZ,

    confidence        JSONB,
    needs_review      BOOLEAN     NOT NULL DEFAULT false,
    error_code        TEXT,
    error_message     TEXT,

    image_gcs_paths   TEXT[],

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

CREATE INDEX IF NOT EXISTS idx_coupons_usable
    ON coupons (uid, brand_id)
    WHERE status = 'COMPLETED' AND is_used = false;

CREATE INDEX IF NOT EXISTS idx_coupons_uid_expires
    ON coupons (uid, expires_at)
    WHERE status = 'COMPLETED' AND is_used = false;

CREATE UNIQUE INDEX IF NOT EXISTS uq_coupons_barcode
    ON coupons (uid, barcode_hash)
    WHERE barcode_hash IS NOT NULL;

-- ---------- stores ----------
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

CREATE INDEX IF NOT EXISTS idx_stores_geog
    ON stores USING GIST ((geom::geography));

CREATE INDEX IF NOT EXISTS idx_stores_brand
    ON stores (brand_id) WHERE is_active = true;

-- ---------- coupon_rules (F-04 RAG) ----------
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
        'EXCLUSION', 'CROSS_USE', 'BALANCE', 'EXTENSION', 'PAYMENT', 'GENERAL')),
    CONSTRAINT ck_rules_store_type CHECK (store_type IN (
        'NORMAL', 'DEPARTMENT_STORE', 'MART_TENANT',
        'HIGHWAY_REST_AREA', 'AIRPORT', 'HOSPITAL', 'CAMPUS'))
);

CREATE INDEX IF NOT EXISTS idx_rules_filter
    ON coupon_rules (brand_id, store_type);

-- HNSW는 규칙이 수천 건을 넘길 때만. 20~100건 규모에서는 순차 스캔이 더 정확하고 빠르다
-- CREATE INDEX idx_rules_hnsw ON coupon_rules USING hnsw (embedding vector_cosine_ops);

파일 분리

backend-fastapi/db/init.sql — 위 DDL 전체 (CREATE만, DROP 없음). 신규 환경용.
backend-fastapi/db/migrate_20260810.sql — 이미 생성된 Cloud SQL에 coupon_type/face_value/barcode_hash/image_gcs_paths/coupon_rules를 ALTER/CREATE로 추가. 적용 완료 (2026-08-10).
backend-fastapi/db/seed.sql — 시연용 시드 (§9.1)
backend-fastapi/db/reset.sql — DROP 포함 재실행용 (신설 예정)
backend-fastapi/db/stores_seed.csv — 431건 매장 시드, scripts/load_store.py가 적재
backend-fastapi/db/apply_sql.py — psql 대체 파이썬 러너. 문장 단위 실행, 실패 시 롤백 + 위치 표시
9.1 시연용 시드 데이터
sql
INSERT INTO users (uid, notification_granted)
VALUES ('dev-uid-0001', true)
ON CONFLICT (uid) DO NOTHING;

INSERT INTO stores (store_id, brand_id, store_name, road_address, geom, source)
VALUES
  ('str_seed_0001', 'starbucks', '스타벅스 아주대점',
   '경기도 수원시 영통구 월드컵로 206',
   ST_SetSRID(ST_MakePoint(127.043900, 37.281500), 4326), 'MANUAL'),
  ('str_seed_0002', 'starbucks', '스타벅스 수원역점',
   '경기도 수원시 팔달구 덕영대로 924',
   ST_SetSRID(ST_MakePoint(127.000700, 37.265900), 4326), 'MANUAL')
ON CONFLICT (store_id) DO NOTHING;

source는 MANUAL이어야 한다 (수동 시드이지 공공데이터가 아니므로). 초기 seed.sql에 PUBLIC_DATA로 잘못 들어간 이력이 있어 UPDATE stores SET source='MANUAL' WHERE store_id LIKE 'str_seed_%'로 정정했다.

시연 대표 브랜드 안내: 스타벅스는 직영 브랜드라 공공데이터에 2건뿐이다. 431건 공공데이터가 실제로 매칭에 쓰이는 그림을 보이려면 메가MGC커피(128건) 를 위치 매칭(F-02) 시연 주인공으로 쓰고, 스타벅스는 F-04 RAG 예외조건(백화점 입점 매장 제외) 시연 전용으로 유지한다.

Mock Location 시연(문서 5절 사용성 요구사항) 시 아주대점 좌표를 목적지로 잡으면 된다.

10. 확정된 사항 / 미확정 사항 체크리스트

확정 (더 논의 불필요)

 바코드 암호화 위치 → 저장하지 않는다. 마스킹값 + SHA-256 해시만 (§4)
 stores 초기 적재 범위 → 수원시 8개 브랜드, 431건 적재 완료
 store_type 자동 태깅 규칙 → 폐기 (§5.1 근거, C-6)
 인스턴스 타임존 → UTC 유지, 쿼리에서 (now() AT TIME ZONE 'Asia/Seoul')::date 명시 (D-1, §7)
 임베딩 모델 및 vector(n) 차원 → gemini-embedding-001, vector(768). pgvector 0.8.5 Cloud SQL에서 가용 확인 (2026-08-10)
 01_API_SPEC.md §6의 uid : device = 1:N 명세 불일치 → 명세를 1:1로 하향. FCM이 2차로 이관되어(C-2) 1차에서는 쟁점 아님

미확정

 users 탈퇴/데이터 삭제 요청 처리 절차 (개인정보 요구사항 대응) — 미구현으로 문서화하고 한계 명시
 Alembic 부재로 인한 init.sql/migrate_*.sql 이중 관리 — 후반기 백로그
11. 후반기 백로그 (16절에 옮길 것)
백로그 ID	항목	기대 가치	노력	검증 방법
B-0X	RAG 지식 확장 자동화	현재 20건 수기+반자동. 브랜드가 늘면 관리 불가. 신규 브랜드 쿠폰 이미지가 등록될 때마다 규칙 후보를 자동 생성하는 파이프라인으로 확장	중	신규 브랜드 쿠폰 10장 투입 시 규칙 후보 생성률 및 사람 검수 통과율 측정
B-0X	notification_logs 테이블 도입	알림 쿨다운을 (사용자, 매장) 단위로 정밀화	중	같은 브랜드 여러 매장 동시 접근 시나리오 테스트
B-0X	devices 테이블 도입	1 사용자 : N 기기 지원 (현재 1:1)	하	다중 기기 로그인 후 알림 도달 확인
B-0X	location_points 테이블 도입 후 좌표 이상치 필터 복원	정확도 값은 정상인데 좌표만 튀는 케이스 방어 (C-15로 제거된 기능)	중	인위적 이상치를 섞은 좌표열 투입 시 해당 좌표만 폐기되는지 확인
B-0X	Alembic 도입	init.sql/migrate_*.sql 수동 이중 관리 해소	중	스키마 변경 후 자동 마이그레이션 생성·적용 확인