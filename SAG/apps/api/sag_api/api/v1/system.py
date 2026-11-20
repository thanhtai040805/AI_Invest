from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, get_session
from sag_api.core.deps import get_current_user, get_current_user_or_connector, require_model_config_operator
from sag_api.core.errors import ApiError, ConflictError, ForbiddenError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import model_provider_catalog
from sag_api.core.principal_assertion import VerifiedPrincipal, require_principal_assertion
from sag_api.db.models import User
from sag_api.generation import LLMClient
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.sag.engine_manager import EngineManager
from sag_api.schemas.dsh_integration import (
    DshCapabilityDescriptor,
    DshConnectionDescriptor,
    DshIntegrationUpdate,
    DshUploadCapability,
)
from sag_api.schemas.system import (
    ModelConfigUpdate,
    QuickModelSetupRequest,
    SystemPreferencesUpdate,
)
from sag_api.services import dsh_integration_service, settings_service
from sag_api.services.source_service import list_sources

router = APIRouter(prefix="/system", tags=["system"])
log = get_logger("system")

DSH_CAPABILITIES = (
    "sources.list",
    "sources.create",
    "knowledge.search",
    "knowledge.read",
    "documents.list",
    "documents.get",
    "documents.upload",
    "documents.ingest",
    "documents.reprocess",
    "documents.delete",
)


def _request_is_loopback(request: Request) -> bool:
    """Return whether the ASGI peer is a loopback address."""
    if request.client is None:
        return False
    try:
        return ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def _request_targets_loopback(request: Request) -> bool:
    """Return whether the request targets a loopback HTTP origin."""
    hostname = request.url.hostname
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


@lru_cache(maxsize=1)
def _docker_gateway_address() -> str | None:
    """Return the Linux default-route gateway used for host-to-container traffic."""
    try:
        routes = Path("/proc/net/route").read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for route in routes[1:]:
        fields = route.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            gateway = int.from_bytes(bytes.fromhex(fields[2]), byteorder="little")
            return str(ip_address(gateway))
        except ValueError:
            continue
    return None


def _discovery_binding_is_loopback() -> bool:
    """Return whether Docker publishes the API only on the host loopback."""
    try:
        return ip_address(settings.dsh_local_discovery_bind_address).is_loopback
    except ValueError:
        return False


def _request_can_discover_dsh(request: Request) -> bool:
    """Allow direct loopback peers or explicit local-container discovery."""
    if _request_is_loopback(request):
        return True
    if request.client is None:
        return False
    return (
        settings.dsh_local_discovery
        and _discovery_binding_is_loopback()
        and request.client.host == _docker_gateway_address()
        and _request_targets_loopback(request)
    )


def _dsh_urls(request: Request) -> tuple[str, str]:
    """Build HTTP connection URLs from the address the caller actually used."""
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/v1", f"{base_url}/mcp/"


def _dsh_capability_descriptor(state: dsh_integration_service.DshIntegrationState) -> DshCapabilityDescriptor:
    """Project persisted source selection without exposing the connector credential."""
    return DshCapabilityDescriptor(
        capabilities=list(DSH_CAPABILITIES),
        upload=DshUploadCapability(
            maxMb=settings.max_upload_mb,
            extensions=sorted(extension.lstrip(".") for extension in settings.allowed_upload_exts),
        ),
        defaultSourceId=state.default_source_id,
    )


def _dsh_connection_descriptor(
    request: Request,
    state: dsh_integration_service.DshIntegrationState,
) -> DshConnectionDescriptor:
    """Build a directly usable descriptor for the current HTTP origin."""
    api_url, mcp_url = _dsh_urls(request)
    return DshConnectionDescriptor(
        name="SAG 知识库",
        apiUrl=api_url,
        mcpUrl=mcp_url,
        accessToken=state.token,
        defaultSourceId=state.default_source_id,
    )


def _capabilities() -> dict:
    strategy_report = EngineManager.strategies_capability_report(settings)
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
        "search_strategies": strategy_report["enabled"],
        "search_strategies_disabled": strategy_report["disabled"],
        "timezone": settings.timezone,
        "max_upload_mb": settings.max_upload_mb,
        "allowed_upload_exts": sorted(settings.allowed_upload_exts),
    }


@router.get("/health")
async def health() -> dict:
    """存活探针：进程在跑即 200（不触碰依赖）。"""
    return {"status": "ok"}


async def _qdrant_ready(request: Request) -> bool | None:
    """Check Qdrant connectivity when it is the configured vector provider."""
    if settings.sag_vector_provider != "qdrant":
        return None
    engine_manager = getattr(request.app.state, "engine_manager", None)
    if engine_manager is None:
        return False
    try:
        client = await engine_manager.get_search_unit_qdrant_client()
        response = await client.get("/collections", timeout=3.0)
    except Exception as error:  # noqa: BLE001
        log.warning("Qdrant readiness check failed (%s)", type(error).__name__)
        return False
    if response.status_code != 200:
        log.warning("Qdrant readiness check returned HTTP %d", response.status_code)
        return False
    return True


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """就绪探针：数据库、知识运行时与配置的向量后端均就绪才返回 200。"""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        log.warning("就绪检查失败：%s", e)
        return JSONResponse(status_code=503, content={"status": "unavailable", "db": False})

    runtime = getattr(request.app.state, "knowledge_runtime", None)
    runtime_ready = bool(runtime and runtime.ready)
    qdrant_ready = await _qdrant_ready(request)
    if not runtime_ready or qdrant_ready is False:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "db": True,
                "knowledge_runtime": runtime_ready,
                "qdrant": qdrant_ready,
            },
        )
    return JSONResponse(content={"status": "ready", "db": True, "qdrant": qdrant_ready})


@router.get("/metrics")
async def metrics(request: Request, format: str = "prometheus") -> Response:
    """Hệ thống giám sát chỉ số (Metrics endpoint) phục vụ chẩn đoán tải & sức khỏe."""
    db_ready = False
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
            db_ready = True
    except Exception:
        db_ready = False

    runtime = getattr(request.app.state, "knowledge_runtime", None)
    knowledge_ready = bool(runtime and runtime.ready)
    qdrant_ready = await _qdrant_ready(request)

    from sag_api.core.token_usage import get_runtime_token_usage
    from sag_api.services.route_cache_service import route_decision_cache

    runtime_token_usage = get_runtime_token_usage()

    cache_size = len(route_decision_cache)
    cache_max = route_decision_cache.max_entries
    cache_enabled = 1 if (settings.search_route_decision_cache_enabled or route_decision_cache.enabled) else 0

    if format.lower() == "json":
        return JSONResponse(
            content={
                "status": "ok",
                "system_up": 1,
                "token_usage": runtime_token_usage,
                "db_ready": db_ready,
                "knowledge_runtime_ready": knowledge_ready,
                "qdrant_ready": qdrant_ready,
                "route_cache": {
                    "enabled": bool(cache_enabled),
                    "size": cache_size,
                    "max_size": cache_max,
                },
                "queues": {
                    "job_concurrency": settings.job_concurrency,
                    "knowledge_concurrency": settings.knowledge_concurrency,
                    "knowledge_queue_capacity": settings.knowledge_queue_capacity,
                },
                "search": {
                    "top_k": settings.search_top_k,
                    "candidate_limit": settings.search_source_candidate_limit,
                    "unit_candidate_limit": settings.search_unit_candidate_limit,
                    "rerank_min_remaining_seconds": settings.search_rerank_min_remaining_seconds,
                },
            }
        )

    lines = [
        "# HELP sag_system_up Operational status of SAG API process",
        "# TYPE sag_system_up gauge",
        "sag_system_up 1",
        "# HELP sag_db_ready PostgreSQL database connectivity readiness",
        "# TYPE sag_db_ready gauge",
        f"sag_db_ready {1 if db_ready else 0}",
        "# HELP sag_knowledge_runtime_ready Knowledge runtime readiness status",
        "# TYPE sag_knowledge_runtime_ready gauge",
        f"sag_knowledge_runtime_ready {1 if knowledge_ready else 0}",
        "# HELP sag_route_cache_enabled Exact route decision cache enablement",
        "# TYPE sag_route_cache_enabled gauge",
        f"sag_route_cache_enabled {cache_enabled}",
        "# HELP sag_route_cache_entries Current entries in route decision LRU cache",
        "# TYPE sag_route_cache_entries gauge",
        f"sag_route_cache_entries {cache_size}",
        "# HELP sag_route_cache_max_entries Maximum capacity of route decision LRU cache",
        "# TYPE sag_route_cache_max_entries gauge",
        f"sag_route_cache_max_entries {cache_max}",
        "# HELP sag_job_concurrency Maximum worker concurrency for document ingestion",
        "# TYPE sag_job_concurrency gauge",
        f"sag_job_concurrency {settings.job_concurrency}",
        "# HELP sag_knowledge_queue_capacity Maximum capacity for knowledge foundation queue",
        "# TYPE sag_knowledge_queue_capacity gauge",
        f"sag_knowledge_queue_capacity {settings.knowledge_queue_capacity}",
        "# HELP sag_search_top_k Initial candidate breadth for adaptive search (not a result cap)",
        "# TYPE sag_search_top_k gauge",
        f"sag_search_top_k {settings.search_top_k}",
        "# HELP sag_search_unit_candidate_limit Hard cap of candidates pool for search units",
        "# TYPE sag_search_unit_candidate_limit gauge",
        f"sag_search_unit_candidate_limit {settings.search_unit_candidate_limit}",
        "# HELP sag_search_rerank_min_remaining_seconds Latency budget margin triggering fast rank interleave",
        "# TYPE sag_search_rerank_min_remaining_seconds gauge",
        f"sag_search_rerank_min_remaining_seconds {settings.search_rerank_min_remaining_seconds}",
        "# HELP sag_tokens_total Total measured tokens consumed since process start",
        "# TYPE sag_tokens_total counter",
        f'sag_tokens_total{{token_type="embedding_input"}} {int(runtime_token_usage["totals"]["embedding_input_tokens"])}',
        f'sag_tokens_total{{token_type="llm_input"}} {int(runtime_token_usage["totals"]["llm_input_tokens"])}',
        f'sag_tokens_total{{token_type="llm_cached_input"}} {int(runtime_token_usage["totals"]["llm_cached_input_tokens"])}',
        f'sag_tokens_total{{token_type="llm_output"}} {int(runtime_token_usage["totals"]["llm_output_tokens"])}',
        f'sag_tokens_total{{token_type="llm_reasoning"}} {int(runtime_token_usage["totals"]["llm_reasoning_tokens"])}',
        f'sag_tokens_total{{token_type="all"}} {int(runtime_token_usage["totals"]["total_tokens"])}',
        "# HELP sag_token_cost_usd_total Estimated cumulative USD cost of measured tokens since process start",
        "# TYPE sag_token_cost_usd_total counter",
        f'sag_token_cost_usd_total {float(runtime_token_usage["cost_usd"]):.8f}',
    ]
    if qdrant_ready is not None:
        lines.extend(
            [
                "# HELP sag_qdrant_ready Qdrant REST connectivity readiness",
                "# TYPE sag_qdrant_ready gauge",
                f"sag_qdrant_ready {1 if qdrant_ready else 0}",
            ]
        )
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/capabilities")
async def capabilities() -> dict:
    """能力探测：供前端判断是否已配置 LLM、当前引擎后端等。"""
    return _capabilities()


@router.get("/dsh", response_model=DshCapabilityDescriptor)
async def dsh_capabilities(
    _user: User = Depends(get_current_user_or_connector),
    session: AsyncSession = Depends(get_session),
) -> DshCapabilityDescriptor:
    """Return connector capabilities without exposing its credential."""
    state = await dsh_integration_service.get_or_create_state(session)
    return _dsh_capability_descriptor(state)


@router.get("/dsh-connection", response_model=DshConnectionDescriptor)
async def discover_dsh_connection(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DshConnectionDescriptor:
    """Return the local connector credential only to a loopback peer."""
    if not _request_can_discover_dsh(request):
        raise ForbiddenError("仅允许本机访问 DSH 连接配置")
    state = await dsh_integration_service.get_or_create_state(session)
    return _dsh_connection_descriptor(request, state)


@router.get("/dsh/export")
async def export_dsh_connection(
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download the connector descriptor after normal SAG authentication."""
    state = await dsh_integration_service.get_or_create_state(session)
    descriptor = _dsh_connection_descriptor(request, state)
    return Response(
        content=descriptor.model_dump_json(),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="sag-dsh.json"'},
    )


@router.put("/dsh/settings", response_model=DshCapabilityDescriptor)
async def update_dsh_settings(
    body: DshIntegrationUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DshCapabilityDescriptor:
    """Select the source used by DSH when a request omits one."""
    state = await dsh_integration_service.update_default_source_and_publish(
        session,
        body.default_source_id,
    )
    return _dsh_capability_descriptor(state)


@router.post("/dsh/regenerate", response_model=DshCapabilityDescriptor)
async def regenerate_dsh_connection_token(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DshCapabilityDescriptor:
    """Rotate the connector credential without returning the new secret."""
    state = await dsh_integration_service.regenerate_token_and_publish(session)
    return _dsh_capability_descriptor(state)


@router.get("/model-config")
async def get_model_config(
    _user: User = Depends(get_current_user),
) -> dict:
    """当前生效的模型与检索配置（密钥脱敏为 *_set 布尔）。"""
    return settings_service.effective_model_config()


@router.get("/model-providers")
async def get_model_providers(
    _user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """前后端共享的模型接入能力与技术默认值。"""
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
    """首次进入时判断是否需要展示快捷模型配置。"""
    return await settings_service.model_setup_status(session)


@router.get("/mcp")
async def knowledge_mcp_descriptor(
    request: Request,
    _user: User = Depends(get_current_user),
    principal: VerifiedPrincipal = Depends(require_principal_assertion),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return MCP connection details for the verified principal's Source scope."""
    source_count = len(await list_sources(session, principal=principal))
    base = str(request.base_url).rstrip("/")
    return {
        "name": "SAG Knowledge Base",
        "scope": "knowledge_base",
        "source_count": source_count,
        "tools": list(MCP_TOOL_NAMES),
        "tool_details": list(MCP_TOOL_DETAILS),
        "http": {
            "transport": "streamable-http",
            "url": f"{base}/mcp/",
            "headers": {
                "Authorization": "Bearer <SAG_TOKEN>",
                "X-SAG-Principal-Assertion": "<short-lived-continuum-assertion>",
            },
            "note": (
                "MCP loads only authorized sources for the current principal; trusted server proxies must inject the Continuum assertion. "
                "source_id can only narrow this scope."
            ),
        },
        "stdio": {
            "available": False,
            "note": "stdio is disabled until every tool call carries and validates the current BE/Continuum principal assertion.",
        },
    }


@router.post("/model-setup/302")
async def quick_setup_302(
    body: QuickModelSetupRequest,
    request: Request,
    _user: User = Depends(require_model_config_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """只接收一个 302.AI Key，写入生成、向量、MinerU 与检索预设。"""
    status = await settings_service.model_setup_status(session)
    if not status["required"]:
        raise ConflictError("模型配置已存在，请在设置中修改")

    config = await settings_service.save_302_quick_setup(session, body.api_key)
    await request.app.state.knowledge_runtime.apply_settings(settings, reset_engines=True)
    return {"config": config, "capabilities": _capabilities()}


@router.put("/model-config")
async def update_model_config(
    body: ModelConfigUpdate,
    request: Request,
    _user: User = Depends(require_model_config_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存运行期配置；仅在模型/向量配置实际变化时安全重建引擎。"""
    patch = body.model_dump(exclude_unset=True)
    before = settings_service.effective_model_config()
    config = await settings_service.save_model_config(session, patch)

    # 解析器/检索参数保存无需打断暖引擎；只有引擎配置真的变化才安全重建。
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
    await request.app.state.knowledge_runtime.apply_settings(
        settings,
        reset_engines=engine_changed,
    )
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/mineru/302")
async def configure_302_mineru(
    request: Request,
    _user: User = Depends(require_model_config_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """已有 302 LLM/Embedding 用户一键复用服务端保存的 Key 启用 MinerU。"""
    config = await settings_service.save_302_mineru_setup(session)
    await request.app.state.knowledge_runtime.apply_settings(settings, reset_engines=False)
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/test")
async def test_model_config(
    request: Request,
    body: ModelConfigUpdate | None = None,
    _user: User = Depends(require_model_config_operator),
) -> dict:
    """连接测试：优先验证表单草稿，不持久化也不修改运行期单例。"""
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
        return {"ok": False, "message": "尚未配置 API Key"}
    try:
        await llm.complete([{"role": "user", "content": "ping"}])
        return {
            "ok": True,
            "message": f"连接成功 · {active.llm_provider} / {active.llm_model}",
        }
    except ApiError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
