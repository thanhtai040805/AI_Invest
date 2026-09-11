"""Lối vào ứng dụng sag-api v2 financial evidence service."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sag_api import __version__
from sag_api.api.v2 import api_router
from sag_api.branding import PRODUCT_NAME
from sag_api.core.config import settings
from sag_api.core.db import dispose_db, init_db
from sag_api.core.db import SessionLocal
from sag_api.core.error_taxonomy import ErrorCode, ErrorLayer, ErrorStage
from sag_api.core.errors import ApiError
from sag_api.core.logging import RequestContextMiddleware, configure_logging, get_logger

log = get_logger("app")


# Khóa mặc định đã biết là không an toàn (môi trường production từ chối khởi động)
_INSECURE_SECRETS = {
    "dev-insecure-secret-change-me-in-production-0123456789",
    "please-change-this-in-production-0123456789",
    "dev-secret-change-me",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("DEBUG" if settings.debug else "INFO")
    if settings.environment == "prod" and settings.secret_key in _INSECURE_SECRETS:
        raise RuntimeError(
            "Môi trường production cấm dùng SAG_SECRET_KEY mặc định. Hãy đặt giá trị ngẫu nhiên mạnh (≥32 byte), ví dụ: openssl rand -hex 32"
        )
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.upload_dir, exist_ok=True)

    await init_db()

    # OCR jobs do not require the optional zleap engine. Keep the queue alive
    # in the API process so URL/R2 OCR submissions are executable in dev and
    # production without a second API-side scheduler process.
    from sag_api.jobs.inproc import InProcessAsyncQueue

    queue = InProcessAsyncQueue(SessionLocal, engine_manager=None, concurrency=settings.job_concurrency)
    app.state.job_queue = queue
    await queue.start()

    log.info(
        "sag-api v2 financial evidence service đã khởi động · env=%s · llm_configured=%s · embedding=%s",
        settings.environment,
        settings.llm_configured,
        settings.embedding_model,
    )
    try:
        yield
    finally:
        await queue.stop()
        await dispose_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        summary="Financial evidence engine for MOAT/GIL",
        lifespan=lifespan,
    )

    cors_kwargs: dict = {
        "allow_origins": settings.cors_origins,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["X-Request-Id"],
    }
    # Môi trường dev cho phép frontend mạng LAN (như http://192.168.x.x:3000), tránh CORS chặn khi truy cập bằng IP máy này
    if settings.environment == "dev":
        cors_kwargs["allow_origin_regex"] = (
            r"https?://("
            r"localhost|"
            r"127\.0\.0\.1|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
            r")(:\d+)?"
        )
    app.add_middleware(CORSMiddleware, **cors_kwargs)
    # Theo dõi yêu cầu (thêm sau CORS → chạy lớp ngoài nhất, phân phối request_id trước tiên)
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_envelope(request_id=request_id),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Ngoại lệ chưa xử lý: %s", exc)
        request_id = getattr(request.state, "request_id", None)
        error: dict[str, object] = {
            "code": ErrorCode.INTERNAL_ERROR,
            "message": "Lỗi nội bộ máy chủ",
            "layer": ErrorLayer.API.value,
            "stage": ErrorStage.UNKNOWN.value,
            "retryable": False,
        }
        if request_id:
            error["request_id"] = request_id
        return JSONResponse(status_code=500, content={"error": error})

    app.include_router(api_router)

    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {"name": PRODUCT_NAME, "version": __version__, "api": "/api/v2", "docs": "/docs"}

    @app.get("/health/live", tags=["system"])
    async def health_live() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["system"])
    async def health_ready() -> dict:
        return {
            "status": "ok",
            "database": "configured",
            "embedding_model": settings.embedding_model,
            "llm_configured": settings.llm_configured,
        }

    @app.get("/metrics", tags=["system"])
    async def metrics() -> dict:
        return {"service": "sag-v2", "metrics": {}}

    return app


app = create_app()
