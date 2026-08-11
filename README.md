# 쿠폰콕 (couponkok)

이미지 기반 쿠폰 인식 + 위치 기반 자동 추천/브리핑 MVP. 아주대 부트캠프 PBL 1차 개인 프로젝트.

카카오톡 선물하기 등으로 받은 기프티콘 캡처 이미지를 Gemini Vision으로 파싱하고, 사용자 위치에 맞춰
인근 매장에서 실제로 사용 가능한 쿠폰을 RAG 근거와 함께 자연어로 브리핑한다.

- 배포: https://couponkok-api-xiwykxhkda-du.a.run.app
- 제출 문서: [`docs/1차_MVP.docx`](docs/1차_MVP.docx), 초안 작업본 [`docs/submission_draft.md`](docs/submission_draft.md)
- 계약 문서: [`docs/01_API_SPEC.md`](docs/01_API_SPEC.md) (API 명세), [`docs/02_DB_SCHEMA.md`](docs/02_DB_SCHEMA.md) (DB 스키마)
- 진행 상황·결정 이력: [`docs/00_PROGRESS.md`](docs/00_PROGRESS.md)

## 기능

| ID | 기능 | 설명 |
|---|---|---|
| F-01 | 쿠폰 OCR 파싱 | 이미지 1~3장 업로드 → Gemini Vision이 브랜드·상품명·유효기간·바코드 추출 |
| F-02 | GPS 기반 매장 매칭 | 좌표 전송 시 반경 300m 내 사용 가능한 쿠폰이 있는 매장 조회 (PostGIS) |
| F-03 | Gemini 브리핑 생성 | 매장·쿠폰·RAG 근거를 종합해 100자 이내 자연어 문장 생성 (실패 시 템플릿 폴백) |
| F-04 | RAG 예외조건 검색 | 브랜드별 사용 제한 규칙을 벡터 검색으로 찾아 브리핑 근거로 제시 (pgvector) |

## 아키텍처

```
Web UI (static/index.html)
    │  이미지 업로드 / 위치 전송
    ▼
FastAPI (Cloud Run) ──▶ Gemini Vision/임베딩 (Vertex AI)
    │                        │ 파싱 JSON / 임베딩 벡터
    ▼                        ▼
Cloud SQL (PostgreSQL + PostGIS + pgvector)
    coupons / stores / users / coupon_rules
```

## 로컬 실행

### 사전 준비
- Python 3.13
- GCP 프로젝트 (Vertex AI API 활성화, ADC 인증: `gcloud auth application-default login`)
- Cloud SQL 인스턴스 (PostgreSQL 15 + PostGIS + pgcrypto + vector 확장) 및 `tools/cloud-sql-proxy.exe`

### 설치 및 환경 변수

```powershell
cd backend-fastapi
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env` 파일 작성 (`backend-fastapi/` 기준, 커밋 금지):

```
GEMINI_AUTH_MODE=vertex
GCP_PROJECT_ID=<프로젝트 ID>
GCP_LOCATION=us-central1
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=couponkok
DB_USER=postgres
DB_PASSWORD=<로컬 DB 비밀번호>
AUTH_DISABLED=true
```

### DB 초기화 및 데이터 적재

```powershell
# 1) Cloud SQL Auth Proxy 실행 (별도 터미널, 계속 켜둘 것)
cd backend-fastapi\tools
.\cloud-sql-proxy.exe <INSTANCE_CONNECTION_NAME> --port 5432

# 2) 스키마 초기화
cd backend-fastapi
python db\apply_sql.py db\init.sql db\seed.sql

# 3) 매장 데이터 적재 (db/stores_seed.csv 사용)
python scripts\load_store.py

# 4) RAG 룰북 적재 (data/rules.json → coupon_rules)
python scripts\index_rules.py
```

### 서버 실행

```powershell
cd backend-fastapi
uvicorn app.main:app --reload --port 8000
```

기동 로그에 `인증: vertex(...)`가 보이면 정상이다. `MOCK 모드로 기동합니다`가 뜨면 `.env`가 안 읽힌 것이므로
거기서 먼저 확인한다. `http://127.0.0.1:8000`에서 웹 UI, `http://127.0.0.1:8000/docs`에서 Swagger UI 확인 가능.

## 테스트

```powershell
python scripts\check_f02.py       # 쿠폰·매장·기준점 상태 확인
python scripts\verify_l4_l7.py    # 위치 API 자동 판정
.\test_locations.ps1              # TC-L1~L10 일괄 실행
```

## 배포 (GCP Cloud Run)

```powershell
cd backend-fastapi
.\deploy.ps1 -Bootstrap      # 최초 1회: API 활성화 / Artifact Registry / SA+IAM / Secret
.\deploy.ps1                 # 이후: 빌드 + 배포
.\deploy.ps1 -SkipBuild      # 환경변수만 변경 시 (빌드 생략)
```

DB 비밀번호는 코드·환경변수에 평문으로 두지 않고 Secret Manager(`couponkok-db-pass`)를 통해 주입한다.

## 알려진 한계

- `AUTH_DISABLED=true`로 배포되어 있어 배포 URL이 사실상 단일 공용 계정이다 (실 서비스 인증은 2차 과제). 실제 개인 쿠폰은 등록하지 않는다
- 실사용자 테스트는 아직 진행하지 않았다
- RAG 지식 베이스는 스타벅스 1건(백화점 등 예외 매장 제외)만 적재된 상태다

전체 배경과 설계 결정 근거는 [`docs/00_PROGRESS.md`](docs/00_PROGRESS.md)의 결정 이력(C-1~C-23)을 참고한다.
