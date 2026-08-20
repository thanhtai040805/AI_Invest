"""Lối vào ứng dụng sag-api."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sag_agent import AgentRuntime
from sag_api import __version__
from sag_api.api.v1 import api_router
from sag_api.branding import PRODUCT_NAME
from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, dispose_db, init_db
from sag_api.core.error_taxonomy import ErrorCode, ErrorLayer, ErrorStage
from sag_api.core.errors import ApiError
from sag_api.core.litellm_policy import install_litellm_policy, uninstall_litellm_policy
from sag_api.core.logging import RequestContextMiddleware, configure_logging, get_logger
from sag_api.generation import LLMClient
from sag_api.jobs import InProcessAsyncQueue
from sag_api.sag import EngineManager
from sag_api.sag.compat import install_zleap_sag_extract_compat, install_zleap_sag_vietnamese

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

    # Ghi đè cấu hình mô hình lưu trong DB lên singleton settings (trước khi xây LLM/engine)
    from sag_api.services.settings_service import apply_startup_overrides

    await apply_startup_overrides(SessionLocal)

    # Gieo agent mặc định (lối vào hội thoại chính dùng ngay được; idempotent)
    from sag_api.services.agent_domain import get_default_agent

    async with SessionLocal() as _session:
        await get_default_agent(_session)

    # Bên trong zleap-sag cũng gọi LiteLLM; policy pre-call toàn cục để nó chia sẻ cùng tham số
    # provider với chuỗi sinh của Muse, mà không sửa package phụ thuộc.
    install_zleap_sag_extract_compat()
    install_zleap_sag_vietnamese()
    litellm_policy = install_litellm_policy(settings)
    app.state.engine_manager = EngineManager(settings)
    app.state.llm = LLMClient(settings)
    app.state.agent_runtime = AgentRuntime()
    await app.state.agent_runtime.start()
    app.state.job_queue = InProcessAsyncQueue(
        SessionLocal, app.state.engine_manager, concurrency=settings.job_concurrency
    )
    await app.state.job_queue.start()

    # Làm nóng engine của nguồn được dùng gần đây ở hậu trường (không chặn khởi động; lỗi không ảnh hưởng dịch vụ)
    warmup_task = asyncio.create_task(_warmup_engines(app.state.engine_manager))

    log.info(
        "sag-api đã khởi động · env=%s · llm_configured=%s · vector=%s",
        settings.environment,
        settings.llm_configured,
        settings.sag_vector_provider,
    )
    source_mcp = getattr(app.state, "source_mcp", None)
    try:
        # Session manager của endpoint MCP cần chạy trong lifespan; lỗi chỉ đóng /mcp, không ảnh hưởng các dịch vụ còn lại
        async with AsyncExitStack() as stack:
            if source_mcp is not None:
                try:
                    await stack.enter_async_context(source_mcp.session_manager.run())
                    log.info("Endpoint MCP đã sẵn sàng · /mcp/ (toàn kho) · tùy chọn ?source_id=<id nguồn>")
                except Exception as e:  # noqa: BLE001
                    log.warning("Khởi động session manager MCP thất bại (/mcp không khả dụng): %s", e)
            yield
    finally:
        try:
            warmup_task.cancel()
            with suppress(asyncio.CancelledError):
                await warmup_task
            await app.state.agent_runtime.stop()
            await app.state.job_queue.stop()
            await app.state.engine_manager.aclose_all()
            await dispose_db()
        finally:
            uninstall_litellm_policy(litellm_policy)


async def _warmup_engines(engine_manager: EngineManager) -> None:
    """Làm nóng engine của nguồn cập nhật gần đây, rút ngắn thời gian chờ thao tác đầu tiên của người dùng."""
    if settings.engine_warmup_count <= 0:
        return
    try:
        from sqlalchemy import select

        from sag_api.db.models import Source

        async with SessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(Source).order_by(Source.updated_at.desc()).limit(settings.engine_warmup_count)
                    )
                )
                .scalars()
                .all()
            )
        for source in rows:
            try:
                await engine_manager.provision(source.sag_source_config_id, source)
            except Exception as e:  # noqa: BLE001
                log.warning("Làm nóng engine thất bại source=%s: %s", source.id, e)
        if rows:
            log.info("Đã làm nóng %d engine nguồn", len(rows))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("Tác vụ làm nóng engine bất thường: %s", e)


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        summary="Nền tảng kho kiến thức mã nguồn mở · từ nguồn thông tin đến hỏi đáp kiến thức",
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

    # Nguồn chính là MCP: gắn endpoint Streamable-HTTP (lỗi không chặn khởi động ứng dụng)
    try:
        from sag_api.mcp.mount import attach_source_mcp

        app.state.source_mcp = attach_source_mcp(app)
    except Exception as e:  # noqa: BLE001
        app.state.source_mcp = None
        log.warning("Gắn endpoint MCP thất bại: %s", e)

    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {"name": PRODUCT_NAME, "version": __version__, "docs": "/docs"}

    return app


app = create_app()
