01. API 명세 (API Specification)

문서 상태: v0.6 최종 수정: 2026-08-11 적용 범위: MVP 1차 (F-01 쿠폰 OCR 파싱, F-02 GPS 기반 매장 매칭, F-03 브리핑 생성, F-04 RAG 예외 조건) 원칙: 이 문서에 합의된 스키마가 계약이다. 코드가 문서와 달라지면 문서를 먼저 고친다.

v0.6 변경점

§5, §6: F-03(Gemini 브리핑) + F-04(RAG 검색) 구현 완료 — 이 문서가 이미 기술해 둔 계약(§8 흐름도, briefing.generated_by, rules[].similarity 등) 그대로 동작한다. 스키마 변경은 없다
§6: coupon_rules에 실제로 적재된 규칙은 2026-08-11 기준 스타벅스 백화점/대형마트/병원/공항/고속도로 휴게소 제외 1건(§6 예시 그대로, 5개 store_type으로 복제)뿐이다. 다른 브랜드는 아직 규칙이 없어 rules: []가 정상이다 (00_PROGRESS.md 결정 이력 C-21)

v0.5 변경점

§1.1: Base URL (Prod) — Cloud Run 배포 후 deploy.ps1 출력 URL로 채울 것 (현재 placeholder)
§1.2: **1차 MVP는 `AUTH_DISABLED=true`로 배포한다** (C-16). 기존 "배포 시 반드시 false로 전환한다" 문장을 대체하고 한계를 명시
§3: `--no-cpu-throttling` 적용 필요성 및 배포 후 재검증 항목 반영 (00_PROGRESS.md D-1~D-6)

v0.4 변경점

§5: 속도 이상치 필터(IMPLAUSIBLE_SPEED) 제거 (C-15). 근거는 §5.1 하단
§5: POST /api/v1/locations 실측 검증 완료 (TC-L1~L6, L9, L10)

v0.3 변경점

§3: 요청 전체 이미지 용량 상한(15MB) 명문화. 다중 이미지 파싱 시간 실측치 반영
§5: 좌표 배열 처리를 최신 유효 좌표 1건 기준으로 축소(C-13). rejected_reasons 형식 확정, matches[].store_type 추가, notification.reason 값 확정
§5: 브리핑 현재 상태(F-03 이전 TEMPLATE 폴백) 명시
1. 공통 규약
1.1 기본 정보
항목	값
Base URL (Local)	http://127.0.0.1:8000
Base URL (Prod)	https://couponkok-api-xiwykxhkda-du.a.run.app
버전 프리픽스	/api/v1
요청 인코딩	application/json (단, 이미지 업로드는 multipart/form-data)
응답 인코딩	application/json; charset=utf-8
문자 인코딩	UTF-8 고정

1차 클라이언트를 안드로이드 네이티브에서 웹으로 전환했다(C-2). 에뮬레이터 전용 주소(10.0.2.2)는 2차 프로젝트로 이관한다.

1.2 인증

모든 엔드포인트(/health 제외)는 Firebase 익명 인증으로 발급된 ID 토큰을 요구한다.

Authorization: Bearer <Firebase ID Token>
서버는 firebase_admin.auth.verify_id_token()으로 서명을 검증하고 uid를 추출한다.
요청 본문에 user_id를 절대 포함하지 않는다. 사용자 식별은 토큰에서만 얻는다.
토큰은 발급 후 약 1시간 뒤 만료된다. 만료 시 401 TOKEN_EXPIRED를 반환하며, 클라이언트는 토큰 갱신 후 1회 자동 재시도한다. 재시도 후에도 401이면 사용자에게 오류를 노출한다.

**1차 MVP는 배포 환경에서도 `AUTH_DISABLED=true`로 운영한다 (C-16).** 모든 요청은 고정 `uid`(`dev-uid-0001`)로 처리되며, `Authorization` 헤더는 검사하지 않는다.

**근거:** 1차 클라이언트가 웹으로 전환되면서(C-2) 심사자는 배포 URL에 접속만 하면 되는 상태가 목표다. Firebase 익명 인증을 붙이면 심사자 브라우저마다 서로 다른 `uid`가 생겨 시연용으로 등록해 둔 쿠폰이 보이지 않는다. 인증 자체보다 "URL 열면 데이터가 보인다"가 1차 검증 목표에 가깝다.

**감수하는 한계 (숨기지 않고 기록):**
- 배포 URL을 아는 누구나 `dev-uid-0001`의 쿠폰을 조회·등록·삭제할 수 있다. 사실상 단일 공용 계정이다.
- 따라서 실제 개인 쿠폰을 등록하면 안 된다. 시연용 쿠폰만 등록한다. 바코드 원문을 저장하지 않는 설계(C-11)가 이 위험의 상한을 낮춰 준다.
- `403 FORBIDDEN`(타인 리소스 접근) 분기는 정의만 있고 도달 불가다.
- 2차(안드로이드) 전환 시 Firebase 익명 인증을 실제로 켜는 것이 최우선 항목이다.

위 본문의 토큰 검증·만료·재시도 규약은 2차 적용 대상 명세로 유지한다.
1.3 시간 및 좌표 표기
종류	형식	예시	비고
타임스탬프	ISO-8601 UTC, Z 접미사	2026-08-10T06:53:05Z	DB는 TIMESTAMPTZ로 UTC 저장, 표시는 클라이언트가 KST 변환
쿠폰 유효기간	날짜만 (YYYY-MM-DD)	2027-03-17	KST 기준 달력 날짜. 해당 날짜 23:59:59(KST)까지 유효
위경도	소수점 6자리 double	37.281500	WGS84 (EPSG:4326)
1.4 리소스 ID 형식

접두사를 붙인 문자열로 통일한다. 숫자 시퀀스는 노출하지 않는다.

리소스	접두사	예시
쿠폰	cpn_	cpn_01KZN4HG9FDCNV26B3KXF4NHMB
기기	dev_	dev_01J9XKQ8B2...
매장	str_	str_01J9XKR3F1...
RAG 규칙	rul_	rul_emart_excl_01
1.5 공통 에러 응답

모든 4xx/5xx 응답은 아래 단일 형식을 따른다.

json
{
  "error": {
    "code": "INVALID_IMAGE_FORMAT",
    "message": "지원하지 않는 이미지 형식입니다. JPEG, PNG, WebP만 업로드할 수 있습니다.",
    "detail": { "received_content_type": "image/heic" }
  }
}
code: 클라이언트 분기용 상수. 이 값으로만 분기하고 message 문자열로 분기하지 않는다.
message: 사용자에게 그대로 노출 가능한 한국어 문장.
detail: 디버깅용 부가 정보. 없을 수 있다.

공통 에러 코드

HTTP	code	상황
401	UNAUTHORIZED	Authorization 헤더 없음 또는 형식 오류
401	TOKEN_EXPIRED	ID 토큰 만료 (갱신 후 재시도 대상)
401	TOKEN_INVALID	서명 검증 실패
403	FORBIDDEN	타인의 리소스 접근 시도
404	RESOURCE_NOT_FOUND	존재하지 않는 ID
422	VALIDATION_ERROR	필수 필드 누락, 타입 불일치
429	RATE_LIMITED	호출 한도 초과 (Retry-After 헤더 동반)
500	INTERNAL_ERROR	서버 내부 오류
503	UPSTREAM_UNAVAILABLE	Gemini/외부 API 장애
2. GET /health

배포 확인 및 Cloud Run 헬스 체크용. 인증 불필요.

Response 200 OK

json
{
  "status": "ok",
  "version": "0.1.0",
  "db": "ok",
  "checked_at": "2026-08-10T06:53:05Z"
}

DB 연결 실패 시 503과 함께 "status":"error", "db":"error"를 반환한다. db는 실제 연결 시도 결과이며 고정값이 아니다.

3. 쿠폰 등록 (이미지 업로드)
POST /api/v1/coupons

갤러리에서 선택한 쿠폰 캡처 이미지를 업로드한다. 서버는 즉시 202를 반환하고 Gemini 파싱은 비동기로 처리한다. 클라이언트는 이후 §4의 조회 API를 폴링한다.

이미지가 여러 장인 이유 (C-10)

카카오톡 선물하기는 한 쿠폰의 정보가 두 화면에 쪼개져 있다.

상단 화면: 브랜드 / 상품명 / 바코드
"선물 사용 정보" 화면: 유효기간 / 사용가능금액 / 교환처

한 장만으로는 필수 필드(브랜드, 유효기간)가 채워지지 않을 수 있다. 그래서 업로드 1건이 이미지 여러 장을 받도록 한다. 서버는 모든 이미지를 Gemini에 한 번에 넣어 하나의 결과로 병합시킨다 (API 호출 1회 유지).

Request

POST /api/v1/coupons
Authorization: Bearer <ID_TOKEN>
Content-Type: multipart/form-data; boundary=----Boundary
파트 이름	타입	필수	설명
images	File (반복)	O	쿠폰 캡처 이미지 1~3장. image/jpeg, image/png, image/webp. 장당 최대 10MB, 요청 전체 최대 15MB
client_upload_id	String	O	클라이언트가 생성한 UUID v4. 멱등성 키
captured_at	String	X	원본 이미지의 촬영/저장 시각 (ISO-8601 UTC)

요청 전체 15MB 상한이 따로 있는 이유 서버는 이미지를 Gemini 요청 본문에 inline으로 실어 보낸다. 장당 10MB만 제한하면 3장 30MB가 통과해 파싱 이전에 upstream 요청 자체가 거부되고, 사용자에게는 원인을 알 수 없는 실패로 보인다. 합계 초과 시 413 IMAGE_TOO_LARGE(detail.total_bytes)로 반환한다.

client_upload_id가 필요한 이유 모바일 네트워크는 응답 유실이 잦다. 사용자가 업로드 버튼을 두 번 누르거나 앱이 재시도하면 같은 쿠폰이 중복 등록된다. 서버는 (uid, client_upload_id)에 유니크 제약을 걸고, 이미 존재하는 키가 오면 새로 만들지 않고 기존 리소스를 200 OK로 반환한다. 클라이언트는 업로드 시도 단위로 UUID를 만들고, 재시도 시에는 같은 값을 재사용해야 한다.

검증 순서 (서버)

client_upload_id 형식 → 멱등성 확인 → 장수 → MIME 타입 → 장당 용량 → 누적 용량. 크기를 형식보다 먼저 보면 HEIC 10MB 파일이 413으로 나가 클라이언트가 "형식 오류" 분기를 타지 못한다. 검증 실패 시 접수된 행은 삭제된다(같은 client_upload_id로 재시도 가능해야 하므로).

Response 202 Accepted — 신규 접수

json
{
  "coupon_id": "cpn_01KZN4HG9FDCNV26B3KXF4NHMB",
  "client_upload_id": "9f1b7c2e-4a3d-4f1e-8b21-5c9d0e7a1234",
  "status": "PROCESSING",
  "created_at": "2026-08-10T06:07:44Z",
  "poll_after_ms": 1500
}
poll_after_ms: 클라이언트가 첫 폴링까지 대기할 권장 시간. 서버 부하에 따라 조절 가능하므로 하드코딩하지 말고 이 값을 사용한다.

Response 200 OK — 중복 업로드 시도 (동일 client_upload_id 재전송)

바디 구조는 §4의 조회 응답과 동일하다.

에러

HTTP	code	상황
413	IMAGE_TOO_LARGE	장당 10MB 초과 또는 요청 전체 15MB 초과
415	INVALID_IMAGE_FORMAT	지원하지 않는 MIME 타입 (HEIC 등)
422	TOO_MANY_IMAGES	이미지 4장 이상
422	VALIDATION_ERROR	images가 비었거나 client_upload_id 누락
429	RATE_LIMITED	분당 업로드 한도 초과 (기본 20건/분, 미구현)

파싱 소요 시간 (실측, 2026-08-10)

이미지	실측	비고
1장	약 9초	
2장	약 11초	병합 성공(megamgc)

문서 5절의 성능 요구사항 "3초 이내"는 달성 불가로 확인되었다. 목표를 15초로 상향 조정하고 사유를 명시한다 — Gemini Vision 멀티모달 추론 특성상 이미지 1장당 4~5초가 소요되며, 다중 이미지 병합은 필수 필드 확보(정확도)와의 교환이다. §4의 폴링 예산(약 30초) 안에 들어오므로 기능상 문제는 없다.

서버 구현 메모 — Cloud Run 주의 FastAPI BackgroundTasks로 응답 후 Gemini를 호출하면, Cloud Run은 기본 설정에서 응답 반환 후 CPU를 회수하므로 작업이 중단될 수 있다. 배포 시 --no-cpu-throttling(CPU 상시 할당)을 적용한다. 로컬에서는 정상 동작하고 배포 후에만 깨지는 유형이므로 반드시 배포 환경에서 검증할 것.

미적용 시 증상: 202 반환 직후 CPU가 회수되어 BackgroundTasks의 Gemini 파싱(9~11초)이 중단되고, 쿠폰이 PROCESSING에 영구히 머무른 채 클라이언트 폴링만 30초 만에 종료된다. 배포 URL에서 업로드→COMPLETED 전이를 반드시 재검증한다 (00_PROGRESS.md D-4).

4. 쿠폰 상태 조회 (폴링)
GET /api/v1/coupons/{coupon_id}

업로드한 쿠폰의 파싱 진행 상태와 결과를 조회한다.

폴링 규칙 (클라이언트)

202 수신 후 poll_after_ms 만큼 대기
2초 간격으로 조회, 최대 15회 (약 30초)
status가 COMPLETED 또는 FAILED가 되면 중단
15회 초과 시 중단하고 "분석이 지연되고 있습니다" 안내

status 값

값	의미
PROCESSING	Gemini 분석 진행 중
COMPLETED	파싱 성공, data 채워짐
FAILED	파싱 실패, error 채워짐

Response 200 OK — COMPLETED

json
{
  "coupon_id": "cpn_01KZN4HG9FDCNV26B3KXF4NHMB",
  "status": "COMPLETED",
  "created_at": "2026-08-10T06:07:44Z",
  "completed_at": "2026-08-10T06:07:53Z",
  "data": {
    "brand_id": "megamgc",
    "brand_name": "메가MGC커피",
    "coupon_type": "PRODUCT",
    "product_name": "(ICE)아메리카노",
    "face_value": 2000,
    "expires_at": "2027-03-17",
    "days_left": 219,
    "barcode_masked": "5014****8004",
    "barcode_format": "CODE128",
    "is_used": false,
    "confidence": { "brand": 1.0, "product_name": 0.95, "expires_at": 1.0 },
    "needs_review": false
  },
  "error": null
}
필드	타입	설명
brand_id	string | null	브랜드 정규화 코드 (config/brands.json 단일 출처). Gemini가 목록에서 직접 고르며(enum), 확신이 없으면 UNKNOWN을 반환하고 서버가 별칭 매칭을 2차로 시도한다. 둘 다 실패하면 null (→ 위치 매칭 불가)
brand_name	string	이미지에서 읽어낸 원문 브랜드명
coupon_type	enum	PRODUCT(상품교환권) / AMOUNT(금액권) / DISCOUNT(할인권) / UNKNOWN. 화면에 보이는 사실만 담으며, 상품 교차 사용 가능 여부 같은 정책 판단은 담지 않는다(→ F-04 RAG)
face_value	int | null	액면가·사용가능금액(원). PRODUCT에도 존재할 수 있다 (상품교환권에 금액이 함께 표기되는 경우가 흔함). 인식 실패 시 null
expires_at	string | null	KST 달력 날짜. 인식 실패 시 null
days_left	int | null	서버가 KST 기준으로 계산해 내려준다. 클라이언트가 직접 계산하지 않는다
barcode_masked	string | null	마스킹된 바코드. 원문은 어떤 응답에도, DB에도 저장하지 않는다
confidence	object	필드별 신뢰도 0.0~1.0
needs_review	bool	신뢰도가 임계값(0.7) 미만인 필드가 있으면 true. coupon_type='AMOUNT'인 경우 product_name 신뢰도는 이 판정에서 제외한다 — 금액권에 상품명이 없는 것은 정상이다

Response 200 OK — FAILED

json
{
  "coupon_id": "cpn_01KZN4HG9FDCNV26B3KXF4NHMB",
  "status": "FAILED",
  "created_at": "2026-08-10T06:07:44Z",
  "completed_at": "2026-08-10T06:07:53Z",
  "data": null,
  "error": {
    "code": "NOT_A_COUPON",
    "message": "쿠폰 이미지로 보이지 않습니다. 기프티콘 화면 전체가 나오도록 다시 캡처해 주세요."
  }
}

파싱 실패 코드

code	상황	앱 처리
NOT_A_COUPON	쿠폰이 아닌 이미지	재업로드 유도
PARSE_FAILED	Gemini 응답이 스키마에 맞지 않음 (재시도 1회 후에도 실패)	수동 입력 유도
REQUIRED_FIELD_MISSING	브랜드 또는 유효기간을 못 읽음	해당 필드만 수동 입력. 1장만 올린 경우 "선물 사용 정보 화면도 함께 올려주세요" 안내가 메시지에 포함된다
UPSTREAM_UNAVAILABLE	Gemini API 장애	재시도 버튼 노출
MULTIPLE_COUPONS	여러 장이 서로 다른 쿠폰으로 판정됨	한 쿠폰씩 다시 올리도록 안내
DUPLICATE_COUPON	같은 바코드의 쿠폰이 이미 등록됨	기존 쿠폰으로 이동

MULTIPLE_COUPONS 판정 (2단 안전장치)

모델 판정과 서버 재검증 중 하나라도 걸리면 실패 처리한다. 잘못 병합된 쿠폰이 저장되면 위치 알림이 엉뚱한 브랜드로 나가므로, 등록 실패가 오알림보다 싸다.

모델 판정 — 응답 스키마의 is_same_coupon이 false이면 실패. 확신이 없으면 false를 내도록 프롬프트에 명시했다.
서버 재검증 — 응답의 per_image(합치기 전 이미지별 값)에서
서로 다른 brand_id가 2개 이상 → 실패
서로 다른 바코드 숫자가 2개 이상 → 실패
한쪽에만 바코드가 있는 경우는 통과. 선물 사용 정보 화면에 바코드가 없는 것은 정상이며, 여기서 막으면 C-10 자체가 무의미해진다

Response 404 RESOURCE_NOT_FOUND — 존재하지 않는 ID Response 403 FORBIDDEN — 타인의 쿠폰 조회 시도 (서버는 uid 소유권을 항상 검증)

5. 위치 전송 및 매장 매칭 + 브리핑
POST /api/v1/locations

백그라운드에서 수집한 좌표를 서버로 전송하고, 주변에서 사용 가능한 쿠폰 매칭 결과와 브리핑을 받는다.

좌표를 배열로 받는 이유 클라이언트는 네트워크 단절 중 좌표를 로컬에 쌓아두었다가 한꺼번에 올려야 한다. 매 좌표마다 요청하면 배터리와 요청 수가 낭비된다.

Request

json
{
  "points": [
    {
      "lat": 37.280300,
      "lng": 127.043300,
      "accuracy_m": 12.5,
      "recorded_at": "2026-08-10T05:10:00Z",
      "source": "PERIODIC"
    }
  ]
}
필드	타입	필수	설명
points	array	O	좌표 배열. 최대 50개, 초과 시 422
points[].lat	double	O	-90 ~ 90
points[].lng	double	O	-180 ~ 180
points[].accuracy_m	double	O	위치 정확도(m). GPS 신뢰도 판정에 사용
points[].recorded_at	string	O	수집 시각 (ISO-8601 UTC). 지연 전송 대비
points[].source	enum	X	PERIODIC(기본) | GEOFENCE_ENTER | MANUAL_REFRESH
5.1 좌표 처리 방식 — 최신 유효 좌표 1건 기준 (C-13)

배열로 받되 매칭과 저장은 유효한 좌표 중 가장 최근 1건으로만 수행한다.

location_points 테이블을 두지 않기로 했고(02_DB_SCHEMA §0.2) users.last_location은 덮어쓰기 컬럼이므로, 배열 내부를 순차 판정해도 중간 결과가 저장되지 않는다. 즉 요청 하나 안에서만 정교해지고 다음 요청에는 아무 영향을 주지 못한다. 얻는 것이 rejected_reasons의 상세도뿐이므로 구현 비용을 지불하지 않는다.

필터 적용 범위

조건	적용 대상	처리
위경도 범위 밖	전체 좌표	요청 전체를 422 INVALID_COORDINATE로 거절
recorded_at 형식 오류	좌표별	폐기 (INVALID_RECORDED_AT)
accuracy_m > 100	좌표별	폐기 (LOW_ACCURACY) — 지하·실내 GPS 음영
recorded_at이 10분 이상 과거	좌표별	폐기 (STALE). 원안은 "저장하되 매칭 제외"였으나 저장할 이력 테이블이 없다
직전 저장 좌표와 50m 이내	최신 좌표만 (users.last_location 기준)	users.last_location 갱신만 생략. 매칭은 그대로 수행한다

유효 좌표가 하나도 없으면 accepted: 0, matches: []로 정상 응답한다(오류가 아니다).

rejected_reasons[].reason의 가능한 값은 INVALID_RECORDED_AT / LOW_ACCURACY / STALE 세 가지다.

속도 이상치 필터는 제거했다 (C-15). 원안에는 "이동 속도 300km/h 초과 시 IMPLAUSIBLE_SPEED로 폐기" 규칙이 있었으나 다음 이유로 삭제한다.

이상치 판정이 성립하지 않는다. 튄 좌표를 골라내려면 연속된 좌표열이 필요한데, location_points 테이블을 두지 않아(02_DB_SCHEMA §0.2) 비교 대상이 덮어쓰기 컬럼 1건뿐이다. 그 1건 자체가 정상인지 알 방법이 없어, 한 번 튄 좌표가 저장되면 이후의 정상 좌표가 반대로 폐기된다.
정밀도가 다른 두 시각을 뺀다. recorded_at은 초 단위로 절삭되어 오고 last_location_at은 마이크로초까지 저장된다. 요청 간격이 1초 미만이면 경과시간이 음수가 되어 판정이 조용히 스킵된다 (TC-L7 실측: -0.64초).
웹 클라이언트(C-2)와 충돌한다. 지도에서 좌표를 지정하는 조작에는 이동 속도 개념이 없다. 시연 중 좌표를 바꾸면 전 요청이 폐기된다.

GPS 이상치 방어는 accuracy_m > 100 폐기가 담당한다. 크게 튄 좌표는 정확도 값도 함께 나빠지므로 상당 부분 겹쳐 막힌다. 좌표 이력 테이블 도입 시 재검토한다(후반기 백로그).

5.2 실측 검증 결과 (2026-08-10)
TC	입력	결과
L1	아주대 37.2803, 127.0433	200 / 스타벅스 아주대점 143m + 메가엠지씨커피 아주대점 207m. 거리순 정렬, json_agg 다중 쿠폰, days_left 서버 계산, 브리핑 1장·2장 문구 분기 확인
L2	accuracy_m: 150	200 / accepted:0, LOW_ACCURACY
L3	recorded_at 15분 전	200 / accepted:0, STALE
L4	30m 이동 후 60m 이동	30m → last_location_at 불변, 60m → 갱신. 두 경우 모두 matches 정상 반환
L5	컴포즈커피 수원매교점 37.267470, 127.017013	200 / accepted:1, matches: [] (INNER JOIN으로 쿠폰 없는 브랜드 제외)
L6	두 번째 좌표 lat: 91.0	422 INVALID_COORDINATE, detail.index: 1
L9	좌표 51개	422 TOO_MANY_POINTS, detail: {received:51, max:50}
L10	points: []	422 VALIDATION_ERROR

Response 200 OK

json
{
  "accepted": 1,
  "rejected": 0,
  "rejected_reasons": [],
  "search_radius_m": 300,
  "matches": [
    {
      "store_id": "str_seed_0001",
      "store_name": "메가MGC커피 아주대점",
      "brand_id": "megamgc",
      "store_type": "NORMAL",
      "lat": 37.281500,
      "lng": 127.043900,
      "distance_m": 150,
      "road_address": "경기도 수원시 영통구 월드컵로 206",
      "source": "PUBLIC_DATA",
      "available_coupons": [
        {
          "coupon_id": "cpn_01KZN4HG9FDCNV26B3KXF4NHMB",
          "coupon_type": "PRODUCT",
          "product_name": "(ICE)아메리카노",
          "face_value": 2000,
          "expires_at": "2027-03-17",
          "days_left": 219
        }
      ],
      "briefing": {
        "text": "150m 앞 메가MGC커피 아주대점. '(ICE)아메리카노' 쿠폰이 있어요.",
        "generated_by": "TEMPLATE",
        "rules": []
      }
    }
  ],
  "notification": {
    "sent": false,
    "reason": "FCM_DEFERRED_TO_PHASE2"
  }
}
필드	설명
accepted / rejected	필터링 통과/폐기된 좌표 수
rejected_reasons	[{ "index": 3, "reason": "LOW_ACCURACY" }] 형식. index는 요청 배열의 위치
search_radius_m	서버 설정값 300m 고정. 클라이언트가 지정하지 않는다
matches	사용 가능한 쿠폰이 1개 이상 있는 매장만 포함. 거리 오름차순, 최대 20곳
matches[].store_type	NORMAL 등. F-04 RAG 예외 조건 판정의 입력값이며 시연 화면에서 함께 보여준다
matches[].source	PUBLIC_DATA(소상공인 상가정보) | MANUAL(직영 브랜드 등 수동 시드) | KAKAO_LOCAL(보조 조회, 미저장)
matches[].available_coupons	만료 임박 순 정렬. 이미 사용 처리되었거나 만료된 쿠폰은 제외
matches[].briefing	F-03(Gemini 브리핑) + F-04(RAG 검색) 결과
briefing.generated_by	GEMINI 또는 TEMPLATE. F-03 구현 이전에는 항상 TEMPLATE이며, 구현 후에도 LLM 호출 실패 시 폴백 경로로 남는다
briefing.rules	RAG 근거. 비어 있으면 **"확인된 정보 없음"**이며 사용 가능 여부를 단정하지 않는다. F-04 이전에는 항상 []
briefing.rules[].similarity	코사인 유사도. 임계값(0.6) 미만인 규칙은 애초에 포함되지 않는다
notification	웹 1차에서는 항상 sent: false, reason: "FCM_DEFERRED_TO_PHASE2". 발송 판정은 2차로 이관(C-2)

에러

HTTP	code	상황
422	VALIDATION_ERROR	points가 비어 있음
422	TOO_MANY_POINTS	points 50개 초과
422	INVALID_COORDINATE	위경도 범위 밖 (detail.index로 위치 표시)
429	RATE_LIMITED	분당 요청 한도 초과 (기본 12건/분, 미구현)
6. RAG 규칙 검색 (F-04 시연용)
POST /api/v1/rules/search

브랜드·매장유형·질의문으로 RAG 검색만 단독으로 실행한다. §5의 브리핑에 자동으로 포함되지만, RAG 동작 자체를 시연하기 위한 화면에서 별도로 쓴다.

Request

json
{
  "brand_id": "starbucks",
  "store_type": "DEPARTMENT_STORE",
  "query": "백화점 입점 매장에서 사용할 수 있나요"
}

Response 200 OK

json
{
  "rules": [
    {
      "rule_id": "rul_starbucks_excl_01",
      "content": "스타벅스 e-Gift 카드는 백화점, 대형마트, 병원, 공항, 고속도로 휴게소 입점 매장에서는 사용할 수 없다.",
      "rule_type": "EXCLUSION",
      "source_name": "스타벅스 e-Gift 이용안내",
      "source_url": null,
      "similarity": 0.87
    }
  ]
}

rules가 빈 배열이면 유사도 임계값(0.6)을 넘는 규칙이 없다는 뜻이며, **"확인된 정보 없음"**으로 처리한다. 규칙을 못 찾은 것과 규칙이 없는 것은 다르다 — 전자를 후자로 단정해 사용 가능하다고 안내하지 않는다.

7. FCM 토큰 등록 — 2차 프로젝트로 이관

C-2로 1차 클라이언트가 웹이 되면서 백그라운드 푸시는 1차 구현 범위 밖이다. 웹은 알림 대신 §5 응답을 화면에 표시하는 방식으로 대체한다. 엔드포인트 정의는 §9(확장 예정)에 보존한다.

8. 전체 호출 흐름

쿠폰 등록 (다중 이미지)

[Client]                     [FastAPI]              [Gemini]
    |-- POST /coupons ---------->|
    |   (images[] multipart)     |-- 이미지 1~3장 한 번에 전달 -------->|
    |<-- 202 {PROCESSING} -------|                                    |
    |                            |<-- 병합된 JSON (+is_same_coupon) ---|
    |                            |-- 2단 안전장치 검증 → DB 저장
    |-- GET /coupons/{id} ------>|
    |<-- 200 {PROCESSING} -------|
    |    (2초 대기)
    |-- GET /coupons/{id} ------>|
    |<-- 200 {COMPLETED, data} --|

위치 매칭 + 브리핑

[Client]                    [FastAPI]              [PostGIS]      [coupon_rules]   [Gemini]
    |-- POST /locations --------->|
    |                             |-- 범위/정확도/신선도 필터 (좌표별)
    |                             |-- 최신 1건 선택 → 50m 중복 억제 판정
    |                             |-- ST_DWithin 반경 검색 -------->|
    |                             |<-- 주변 매장 + 쿠폰 ------------|
    |                             |-- brand_id로 RAG 사전 필터 + 벡터 검색 ------->|   (F-04)
    |                             |<-- 관련 규칙(top-3, 유사도 컷) --------------|
    |                             |-- 매장+쿠폰+규칙을 근거로 브리핑 생성 ------------------------>|   (F-03)
    |                             |<-- 100자 이내 문장 (실패 시 템플릿 폴백) --------------------|
    |<-- 200 {matches, briefing} -|
9. 확장 예정 (본 문서 미포함)
항목	사유
GET /api/v1/coupons (목록 조회)	F-05 구현 시 정의. 만료 임박 순 정렬 + 커서 페이지네이션. 쿼리는 core.list_coupons()에 이미 있음
POST /api/v1/devices/fcm-token, DELETE /api/v1/devices/fcm-token	안드로이드 백그라운드 알림. 2차 프로젝트로 이관(C-2)
PATCH /api/v1/coupons/{id}	needs_review 쿠폰의 수동 수정, 사용 완료 처리
알림 발송 판정 및 쿨다운 규칙	§5 notification 참조. 2차에서 FCM과 함께 확정
Rate limiting (429)	§3·§5에 정의만 있고 미구현. 시연 규모에서 필요 없음
users 탈퇴/데이터 삭제	DELETE /api/v1/me 미구현. 개인정보 요구사항 대응 필요

영구 삭제(구현하지 않음): GET /api/v1/coupons/{id}/barcode — 바코드 원문을 애초에 저장하지 않기로 했으므로(§4, C-11) 조회할 대상이 없다.

10. 확정된 사항 (더 논의 불필요)
 알림 발송 판정 주체 → 웹 1차에서는 미구현, 2차에서 FCM과 함께 확정
 브랜드 정규화 사전 구축 방식 → config/brands.json(단일 출처) + Gemini response_schema enum 분류(1차) + 별칭 문자열 매칭(2차 폴백)
 검색 반경 300m 고정 여부 → 고정. 사용자 설정 미제공
 바코드 원문 처리 → 저장하지 않는다. 마스킹값과 SHA-256 해시(중복 판정용, 복호화 불가)만 보관
 이미지 업로드 형식 → 1~3장, 서버가 Gemini 호출 1회로 병합. 요청 전체 15MB 상한
 다중 이미지 안전장치 → 모델 is_same_coupon + 서버 per_image 재검증 2단. 실측 확인 (2026-08-10)
 좌표 배열 처리 → 최신 유효 좌표 1건 기준 (C-13, §5.1)
 파싱 시간 목표 → 3초 → 15초로 상향. 실측 1장 9초 / 2장 11초
 스팸 방지 규칙 (쿨다운 시간, 1일 상한, 야간 무음 시간대) — FCM 도입 시 확정
 원본 이미지 보관 기간 — GCS 업로드 자체가 미구현