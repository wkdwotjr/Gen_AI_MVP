# CLAUDE.md — 이 프로젝트에서 작업할 때 먼저 읽을 것

## 프로젝트
쿠폰콕(couponkok) — 이미지 기반 쿠폰 인식 + 위치 기반 자동 추천 MVP.
아주대 부트캠프 PBL 1차 제출. **마감: 2026-08-12 발표.**

## 진행 상황의 단일 출처
`docs/00_PROGRESS.md`를 항상 먼저 읽는다. "지금 상태 한 줄", "완료", "다음 할 일",
"결정 이력(C-1~)"이 여기 있다. 이 파일이 낡았으면 코드를 보고 갱신부터 한다.

## 계약 문서 (코드보다 우선)
- `docs/01_API_SPEC.md` — API 계약. 코드와 어긋나면 **문서를 먼저 고친다**.
- `docs/02_DB_SCHEMA.md` — DB 스키마 + 3테이블 제약(§0.1)과 잘라낸 것(§0.2) 근거.

## 하지 말 것
- `.env`, `test_images/`, `tools/`(cloud-sql-proxy.exe) 커밋 금지. `.gitignore` 확인 후 커밋.
- `git push`는 반드시 실행 전 확인받을 것 (자동 승인 목록에 넣지 않는다).
- `.ps1` 스크립트는 ASCII 전용으로 작성/수정 (PS 5.1 CP949 파싱 이슈, `deploy.ps1` 상단 주석 참조).
- DB 비밀번호는 코드/문서에 평문으로 쓰지 않는다. Secret Manager(`couponkok-db-pass`) 사용.

## 환경
Windows + PowerShell, 작업 디렉터리는 `backend-fastapi/` 기준.
로컬 DB 접속은 Cloud SQL Auth Proxy 필요 (`backend-fastapi\tools\cloud-sql-proxy.exe`).

## 지금 세션에서 하려는 것
<!-- 세션 시작할 때 이 아래를 그때그때 채워서 붙여넣기 -->