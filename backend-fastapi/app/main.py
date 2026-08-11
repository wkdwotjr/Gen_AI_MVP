"""FastAPI 진입점.

실행: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import core
from app.api import coupons, locations, rules

from pathlib import Path
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
logger = logging.getLogger("coupon-master")

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = core.get_settings()
    logger.info(
        "모델: %s / 인증: %s",
        settings.gemini_model,
        f"vertex({settings.gcp_location})" if settings.use_vertex else "api_key",
    )
    if settings.use_mock_gemini:
        logger.warning("GEMINI_API_KEY 미설정 — MOCK 모드로 기동합니다.")
    if settings.auth_disabled:
        logger.warning("AUTH_DISABLED=true — 모든 요청을 uid=%s로 처리합니다.", settings.dev_uid)
    yield
    logger.info("서버를 종료합니다.")


app = FastAPI(
    title="쿠폰콕 Backend API",
    description="이미지 기반 쿠폰 인식 및 위치 기반 알림 서비스 (MVP 1차)",
    version=core.get_settings().app_version,
    lifespan=lifespan,
)

# 1차 클라이언트가 웹으로 바뀌어(C-2) CORS가 실제 동작 경로가 되었다.
# 배포 시에는 Cloud Run 서비스 URL로 좁힐 것.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)

core.register_exception_handlers(app)
app.include_router(coupons.router, prefix=API_V1_PREFIX)
app.include_router(locations.router, prefix=API_V1_PREFIX)
app.include_router(rules.router, prefix=API_V1_PREFIX)


@app.get("/health")
async def health():
    settings = core.get_settings()
    ok = await core.db_health()
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "error",
            "version": settings.app_version,
            "db": "ok" if ok else "error",
            "checked_at": core.iso(core.utcnow()),
        },
    )


# ★ 반드시 include_router() 들이 모두 끝난 뒤, main.py 맨 아래에 둔다.
#   "/" 마운트를 라우터보다 위에 두면 /api/v1/* 와 /docs 를 전부 삼킨다.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")