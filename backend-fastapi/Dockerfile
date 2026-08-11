# backend-fastapi/Dockerfile
# couponkok API - Cloud Run (asia-northeast3)
#
# 빌드 컨텍스트는 backend-fastapi/ 이다.
#   cd backend-fastapi
#   gcloud builds submit --tag <IMAGE> .

FROM python:3.11-slim

# - PYTHONDONTWRITEBYTECODE: 컨테이너에 .pyc 남기지 않음
# - PYTHONUNBUFFERED: print/logging이 Cloud Logging에 즉시 흐르게 함 (버퍼링되면 크래시 시 로그가 통째로 사라진다)
# - TZ=UTC: 02_DB_SCHEMA §1 "인스턴스 타임존 UTC 유지"와 런타임을 일치시킴
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

WORKDIR /app

# psycopg[binary]는 컴파일이 필요 없다. requirements.txt에 psycopg2(비바이너리)나
# 소스 빌드가 필요한 패키지가 있으면 아래 두 줄의 주석을 풀 것.
# RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
#     && rm -rf /var/lib/apt/lists/*

# 의존성을 먼저 복사해야 소스만 바뀐 재빌드에서 pip 레이어가 캐시된다 (재배포 2~3분 단축)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 자산. .env는 .dockerignore로 제외되며, 값은 Cloud Run 환경변수/Secret으로 주입한다.
COPY app/ ./app/
COPY static/ ./static/
COPY config/ ./config/
COPY db/ ./db/

# 비루트 실행 (제출문서 SECTION 12 "키/개인정보/보안" 점검 대응)
RUN useradd --create-home --uid 1001 appuser && chown -R appuser:appuser /app
USER appuser

# Cloud Run은 PORT 환경변수를 주입한다. 로컬 docker run 시에는 8080으로 폴백.
ENV PORT=8080
EXPOSE 8080

# --workers 1 고정:
#   ① Cloud Run은 컨테이너를 수평 확장하므로 프로세스 다중화가 불필요하다.
#   ② BackgroundTasks(비동기 Gemini 파싱)는 요청을 받은 그 프로세스 안에서 끝나야 한다.
#   ③ 워커마다 SQLAlchemy 풀이 따로 생겨 Cloud SQL 커넥션 한도를 갉아먹는다.
# exec 형태여야 uvicorn이 PID 1이 되어 SIGTERM을 직접 받는다 (인스턴스 종료 시 graceful shutdown).
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 65