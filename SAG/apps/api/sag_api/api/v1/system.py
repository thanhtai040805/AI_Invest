from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, get_session
from sag_api.core.deps import get_current_user
from sag_api.core.errors import ApiError, ConflictError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import model_provider_catalog
from sag_api.db.models import Source, User
from sag_api.generation import LLMClient
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.schemas.system import (
    ModelConfigUpdate,
    QuickModelSetupRequest,
    SystemPreferencesUpdate,
)
from sag_api.services import settings_service

router = APIRouter(prefix="/system", tags=["system"])
log = get_logger("system")


def _capabilities() -> dict:
    return {
        "llm_configured": settings.llm_configured,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "context_window": settings.llm_context_window,
        "embedding_model": settings.embedding_model,
        "document_parser": settings.document_parser,
        "effective_document_parser": settings.effective_document_parser,
        "mineru_configured": settings.mineru_configured,
        "vector_provider": settings.sag_vector_provider,
        "language": settings.sag_language,
        "search_strategy": settings.search_strategy,
        "timezone": settings.timezone,
        "max_upload_mb": settings.max_upload_mb,
        "allowed_upload_exts": sorted(settings.allowed_upload_exts),
    }


@router.get("/health")
async def health() -> dict:
    """Probe liveness: tiến trình đang chạy là 200 (không đụng tới phụ thuộc)."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Probe readiness: chỉ 200 khi DB kết nối được, ngược lại 503 (cho kiểm tra sức khỏe compose/K8s)."""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        log.warning("Kiểm tra sẵn sàng thất bại: %s", e)
        return JSONResponse(status_code=503, content={"status": "unavailable", "db": False})
    return JSONResponse(content={"status": "ready", "db": True})


@router.get("/capabilities")
async def capabilities() -> dict:
    """Dò khả năng: cho frontend biết đã cấu hình LLM chưa, engine backend hiện tại, v.v."""
    return _capabilities()


@router.get("/model-config")
async def get_model_config(
    _user: User = Depends(get_current_user),
) -> dict:
    """Cấu hình mô hình và truy vấn hiện đang hiệu lực (khóa được làm mờ thành boolean *_set)."""
    return settings_service.effective_model_config()


@router.get("/model-providers")
async def get_model_providers(
    _user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """Khả năng kết nối mô hình và giá trị mặc định kỹ thuật dùng chung giữa frontend và backend."""
    return model_provider_catalog()


@router.get("/preferences")
async def get_system_preferences(
    _user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Presentation preferences shared by this local-first installation."""
    return settings_service.effective_system_preferences()


@router.put("/preferences")
async def update_system_preferences(
    body: SystemPreferencesUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    return await settings_service.save_system_preferences(
        session,
        body.model_dump(exclude_unset=True),
    )


@router.get("/model-setup")
async def get_model_setup_status(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Khi vào lần đầu, xác định xem có cần hiển thị cấu hình mô hình nhanh không."""
    return await settings_service.model_setup_status(session)


@router.get("/mcp")
async def knowledge_mcp_descriptor(
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Trả về thông tin kết nối để gắn toàn bộ kho kiến thức SAG vào host MCP bên ngoài."""
    source_count = await session.scalar(select(func.count(Source.id))) or 0
    base = str(request.base_url).rstrip("/")
    return {
        "name": "Kho kiến thức SAG",
        "scope": "knowledge_base",
        "source_count": source_count,
        "tools": list(MCP_TOOL_NAMES),
        "tool_details": list(MCP_TOOL_DETAILS),
        "http": {
            "transport": "streamable-http",
            "url": f"{base}/mcp/",
            "headers": {"Authorization": "Bearer <SAG_TOKEN>"},
            "note": (
                "Mặc định mở tất cả nguồn; host như Dify hãy dùng truyền tải streamable_http/Streamable HTTP, "
                "có thể thêm ?source_id=<id> vào URL để tạm giới hạn một nguồn duy nhất."
            ),
        },
        "stdio": {
            "command": "python",
            "args": ["-m", "sag_api.mcp.server"],
            "env": {},
            "note": "Mặc định mở tất cả nguồn; đặt SAG_MCP_SOURCE_ID để giới hạn một nguồn duy nhất.",
        },
    }


@router.post("/model-setup/302")
async def quick_setup_302(
    body: QuickModelSetupRequest,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Chỉ nhận một Key 302.AI, ghi vào preset sinh, vector, MinerU và truy vấn."""
    status = await settings_service.model_setup_status(session)
    if not status["required"]:
        raise ConflictError("Cấu hình mô hình đã tồn tại, hãy sửa trong cài đặt")

    config = await settings_service.save_302_quick_setup(session, body.api_key)
    await request.app.state.engine_manager.aclose_all()
    return {"config": config, "capabilities": _capabilities()}


@router.put("/model-config")
async def update_model_config(
    body: ModelConfigUpdate,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Lưu cấu hình runtime; chỉ tái dựng engine an toàn khi cấu hình mô hình/vector thực sự thay đổi."""
    patch = body.model_dump(exclude_unset=True)
    before = settings_service.effective_model_config()
    config = await settings_service.save_model_config(session, patch)

    # Lưu tham số parser/truy vấn không cần ngắt warm engine; chỉ khi cấu hình engine thực sự thay đổi mới tái dựng an toàn.
    engine_fields = {
        "llm_provider",
        "llm_base_url",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_timeout_ms",
        "llm_max_retries",
        "embedding_model",
        "embedding_base_url",
        "embedding_dimensions",
        "sag_language",
    }
    engine_changed = any(before.get(key) != config.get(key) for key in engine_fields)
    engine_changed = engine_changed or bool(patch.get("llm_api_key") or patch.get("embedding_api_key"))
    if engine_changed:
        await request.app.state.engine_manager.aclose_all()
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/mineru/302")
async def configure_302_mineru(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Người dùng 302 LLM/Embedding hiện có, bấm một cái để tái dùng Key đã lưu trên server bật MinerU."""
    config = await settings_service.save_302_mineru_setup(session)
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/test")
async def test_model_config(
    request: Request,
    body: ModelConfigUpdate | None = None,
    _user: User = Depends(get_current_user),
) -> dict:
    """Kiểm tra kết nối: ưu tiên xác thực bản nháp form, không lưu vĩnh viễn và không sửa singleton runtime."""
    llm: LLMClient
    active = settings
    if body is None:
        llm = request.app.state.llm
    else:
        patch = body.model_dump(exclude_unset=True)
        updates = {
            key: (None if key in {"llm_base_url"} and value == "" else value)
            for key, value in patch.items()
            if not (key == "llm_api_key" and not value)
        }
        active = settings.model_copy(update=updates)
        llm = LLMClient(active)
    if not llm.configured:
        return {"ok": False, "message": "Chưa cấu hình API Key"}
    try:
        await llm.complete([{"role": "user", "content": "ping"}])
        return {
            "ok": True,
            "message": f"Kết nối thành công · {active.llm_provider} / {active.llm_model}",
        }
    except ApiError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
