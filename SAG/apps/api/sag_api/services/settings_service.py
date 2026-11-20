"""运行期模型与知识库配置 —— DB 覆盖层叠加在 env 默认（`settings` 单例）之上。

MinerU 文档解析仅使用 MinerU Official API v4（`https://mineru.net` + `SAG_MINERU_OFFICIAL_API_TOKEN`），
旧版 302 MinerU 覆盖项在读取与保存时会自动清理迁移，避免覆盖官方 Token。
"""

from __future__ import annotations

import os
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.core.config import Settings
from sag_api.core.config import settings as _settings
from sag_api.core.errors import ConfigurationError, ConflictError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import get_model_provider
from sag_api.db.models import Document, Setting
from sag_api.enums import SEARCH_STRATEGIES, normalize_search_strategy

_SCOPE = "global"
_KEY = "model_config"
_PREFERENCES_KEY = "system_preferences"
log = get_logger("settings")

# 允许运行期覆盖的字段（值已由请求 schema 校验/转型）
_FIELDS = frozenset(
    {
        "llm_provider",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_context_window",
        "llm_timeout_ms",
        "llm_max_retries",
        "decision_provider",
        "decision_model",
        "siliconflow_api_key",
        "embedding_model",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_dimensions",
        "document_parser",
        "mineru_provider",
        "mineru_base_url",
        "mineru_official_api_token",
        "mineru_official_model",
        "document_extract_concurrency",
        "document_chunk_max_tokens",
        "document_chunk_mode",
        "document_extraction_profile",
        "search_strategy",
        "search_top_k",
        "sag_language",
    }
)
_SECRET_FIELDS = frozenset(
    {
        "llm_api_key",
        "siliconflow_api_key",
        "embedding_api_key",
        "mineru_official_api_token",
    }
)
_NULLABLE_FIELDS = frozenset(
    {"llm_base_url", "embedding_base_url", "embedding_dimensions", "mineru_base_url"}
)
_LOCKABLE_LLM_FIELDS = frozenset(
    {
        "llm_provider",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_context_window",
        "llm_timeout_ms",
        "llm_max_retries",
        "decision_provider",
        "decision_model",
        "siliconflow_api_key",
    }
)
_MODEL_CONFIG_SOURCES = {field: "default" for field in _FIELDS - _SECRET_FIELDS}

_OPENAI_COMPATIBLE = get_model_provider("openai")
_OFFICIAL_MINERU_BASE_URL = "https://mineru.net"

QUICK_SETUP_302 = {
    "llm_provider": _OPENAI_COMPATIBLE.id,
    "llm_base_url": _OPENAI_COMPATIBLE.default_base_url,
    "llm_model": _OPENAI_COMPATIBLE.default_model,
    "llm_temperature": _OPENAI_COMPATIBLE.default_temperature,
    "llm_max_tokens": 20_000,
    "llm_context_window": _OPENAI_COMPATIBLE.default_context_window,
    "llm_timeout_ms": 60_000,
    "llm_max_retries": 2,
    "embedding_model": "Qwen/Qwen3-Embedding-4B",
    "embedding_base_url": "https://api.302ai.cn/v1",
    "embedding_dimensions": 1024,
    "document_parser": "auto",
    "mineru_provider": "official",
    "mineru_base_url": _OFFICIAL_MINERU_BASE_URL,
    "document_extract_concurrency": 30,
    "document_chunk_max_tokens": 1_000,
    "document_chunk_mode": "standard",
    "document_extraction_profile": "standard",
    "search_strategy": "multi_es_fast",
    "search_top_k": 8,
    "sag_language": "en",
}

_LEGACY_302_BASE_URLS = {
    "https://api.302.ai": "https://api.302ai.cn",
    "https://api.302.ai/v1": "https://api.302ai.cn/v1",
    "https://api.siliconflow.cn": "https://api.siliconflow.com",
    "https://api.siliconflow.cn/v1": "https://api.siliconflow.com/v1",
}


async def _load_row(session: AsyncSession, key: str = _KEY) -> Setting | None:
    return await session.scalar(select(Setting).where(Setting.scope == _SCOPE, Setting.key == key))


def _embedding_identity(settings: Settings, patch: dict) -> tuple[str, int]:
    model = str(patch.get("embedding_model", settings.embedding_model))
    dimensions = patch.get("embedding_dimensions", settings.embedding_dimensions)
    return model, int(dimensions or 1024)


async def _reject_incompatible_embedding_change(
    session: AsyncSession,
    patch: dict,
) -> None:
    if _embedding_identity(_settings, patch) == _embedding_identity(_settings, {}):
        return
    indexed_document_id = await session.scalar(
        select(Document.id)
        .where(
            Document.is_active.is_(True),
            Document.sag_source_id.is_not(None),
        )
        .limit(1)
    )
    if indexed_document_id is not None:
        raise ConflictError(
            "当前知识库已有入库文档，更换 Embedding 模型或维度会导致现有向量失效；"
            "请先清理并重新入库文档，或等待向量重建功能。"
        )


def _normalize_overrides(overrides: dict) -> dict:
    """清理持久化配置，确保已下线的 302 MinerU 或旧路由策略不会进入运行时。"""
    normalized = dict(overrides)
    decision_provider = normalized.get("decision_provider")
    legacy_provider = (
        decision_provider.strip().lower()
        if isinstance(decision_provider, str) and decision_provider.strip().lower() in {"laya", "openrouter"}
        else None
    )
    if legacy_provider:
        normalized["decision_provider"] = "siliconflow"
    if legacy_provider or normalized.get("decision_model") == "typesafe/jev-1.14":
        normalized["decision_model"] = "Kev-4b"
    for obsolete_field in (
        "openrouter_api_key",
        "laya_model_path",
        "laya_device",
        "mineru_api_key",
        "mineru_version",
    ):
        normalized.pop(obsolete_field, None)

    for field in ("llm_base_url", "embedding_base_url"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = _LEGACY_302_BASE_URLS.get(value.rstrip("/"), value)

    if "mineru_provider" in normalized:
        normalized["mineru_provider"] = "official"
    if "mineru_base_url" in normalized:
        raw_mineru_url = normalized.get("mineru_base_url")
        if not isinstance(raw_mineru_url, str) or not raw_mineru_url.strip():
            normalized["mineru_base_url"] = _OFFICIAL_MINERU_BASE_URL
        else:
            mineru_host = (urlparse(raw_mineru_url.strip()).hostname or "").lower()
            if mineru_host in {"api.302ai.cn", "api.302.ai"}:
                normalized["mineru_base_url"] = _OFFICIAL_MINERU_BASE_URL
            else:
                normalized["mineru_base_url"] = raw_mineru_url.strip()

    strategy = normalized.get("search_strategy")
    if strategy == "atomic":
        normalized["search_strategy"] = normalize_search_strategy(strategy)
        log.warning("旧检索策略 atomic 已迁移为精确模式 multi")
    elif strategy is not None and strategy not in SEARCH_STRATEGIES:
        normalized.pop("search_strategy", None)
        log.warning("Ignored invalid persisted search strategy: %s", strategy)
    if "sag_language" in normalized:
        normalized["sag_language"] = "en"
    return normalized


async def load_overrides(session: AsyncSession) -> dict:
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    return _normalize_overrides(raw)


async def model_setup_status(session: AsyncSession) -> dict[str, bool]:
    """判断是否需要首次模型配置，不受运行期 DB 覆盖后的 settings 单例干扰。"""
    row = await _load_row(session)
    environment_configured = Settings().llm_configured
    database_configured = bool(row and isinstance(row.value, dict) and row.value.get("llm_api_key"))
    return {
        "required": not environment_configured and not database_configured,
        "environment_configured": environment_configured,
        "database_configured": database_configured,
    }


def apply_overrides(settings: Settings, overrides: dict) -> None:
    """把持久化配置写回 settings；部署锁定时保留 LLM 环境值。"""
    normalized = _normalize_overrides(overrides)
    locked_fields = _LOCKABLE_LLM_FIELDS if settings.lock_llm_config else frozenset()
    for key, value in normalized.items():
        if key in _FIELDS and key not in locked_fields:
            setattr(settings, key, value)
    if settings is _settings:
        for key in _MODEL_CONFIG_SOURCES:
            if key in locked_fields:
                _MODEL_CONFIG_SOURCES[key] = "environment_policy"
            elif key in normalized:
                _MODEL_CONFIG_SOURCES[key] = "database"


_ENV_VAR_PREFIX = "SAG_"
_ENV_COMPARABLE_FIELDS = tuple(sorted(_FIELDS - _SECRET_FIELDS))


def _env_matches_persisted(field: str, persisted: object) -> bool | None:
    raw = os.environ.get(f"{_ENV_VAR_PREFIX}{field.upper()}")
    if raw is None or not raw.strip():
        return None
    raw = raw.strip()
    try:
        if isinstance(persisted, bool):
            env_value: object = raw.lower() in {"1", "true", "yes", "on"}
        elif isinstance(persisted, int):
            env_value = int(raw)
        elif isinstance(persisted, float):
            env_value = float(raw)
        else:
            env_value = raw
    except ValueError:
        return None
    return env_value == persisted


def _warn_persisted_beats_env(overrides: dict) -> None:
    locked_fields = _LOCKABLE_LLM_FIELDS if _settings.lock_llm_config else frozenset()
    for field in _ENV_COMPARABLE_FIELDS:
        if field not in overrides or field in locked_fields:
            continue
        if _env_matches_persisted(field, overrides[field]) is False:
            log.warning(
                "%s: env 与持久化 model_config 不一致，生效值以持久化配置为准（请在设置页修改）",
                field,
            )
    for field in sorted(_SECRET_FIELDS - locked_fields):
        persisted = overrides.get(field)
        raw = os.environ.get(f"{_ENV_VAR_PREFIX}{field.upper()}")
        if not isinstance(persisted, str) or raw is None or not raw.strip():
            continue
        if raw.strip() != persisted:
            log.warning("%s: env 已配置但与持久化的值不同，生效值以持久化配置为准（不打印密钥）", field)


async def apply_startup_overrides(session_factory: async_sessionmaker) -> None:
    """启动时：把 DB 里的模型配置覆盖到 settings 单例（在构建 LLMClient 之前调用）。"""
    async with session_factory() as session:
        row = await _load_row(session)
        raw = dict(row.value) if row and isinstance(row.value, dict) else {}
        overrides = _normalize_overrides(raw)
        if row is not None and overrides != raw:
            row.value = overrides
            await session.commit()
        _warn_persisted_beats_env(overrides)
        apply_overrides(_settings, overrides)
        preferences = await _load_row(session, _PREFERENCES_KEY)
        preference_values = dict(preferences.value) if preferences and isinstance(preferences.value, dict) else {}
        timezone = preference_values.get("timezone")
        if isinstance(timezone, str):
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                log.warning("忽略非法的持久化时区：%s", timezone)
            else:
                _settings.timezone = timezone


def effective_model_config() -> dict:
    """当前生效的模型配置（读 settings 单例；密钥脱敏为 *_set 布尔）。"""
    token_set = bool(_settings.mineru_official_api_token)
    return {
        "llm_provider": _settings.llm_provider,
        "llm_base_url": _settings.llm_base_url,
        "llm_model": _settings.llm_model,
        "llm_temperature": _settings.llm_temperature,
        "llm_max_tokens": _settings.llm_max_tokens,
        "llm_context_window": _settings.llm_context_window,
        "llm_timeout_ms": _settings.llm_timeout_ms,
        "llm_max_retries": _settings.llm_max_retries,
        "llm_api_key_set": bool(_settings.llm_api_key),
        "decision_provider": _settings.decision_provider,
        "decision_model": _settings.decision_model,
        "siliconflow_api_key_set": bool(_settings.siliconflow_api_key),
        "embedding_model": _settings.embedding_model,
        "embedding_base_url": _settings.embedding_base_url,
        "embedding_dimensions": _settings.embedding_dimensions,
        "embedding_api_key_set": bool(_settings.embedding_api_key),
        "document_parser": _settings.document_parser,
        "effective_document_parser": _settings.effective_document_parser,
        "mineru_provider": "official",
        "mineru_base_url": _settings.mineru_base_url,
        "mineru_official_model": _settings.mineru_official_model,
        "mineru_official_api_token_set": token_set,
        "mineru_api_key_set": token_set,
        "document_extract_concurrency": _settings.document_extract_concurrency,
        "document_chunk_max_tokens": _settings.document_chunk_max_tokens,
        "document_chunk_mode": _settings.document_chunk_mode,
        "document_extraction_profile": _settings.document_extraction_profile,
        "search_strategy": _settings.search_strategy,
        "search_top_k": _settings.search_top_k,
        "sag_language": _settings.sag_language,
        "sources": dict(_MODEL_CONFIG_SOURCES),
        "locked_fields": sorted(_LOCKABLE_LLM_FIELDS) if _settings.lock_llm_config else [],
    }


def effective_system_preferences() -> dict[str, str]:
    return {"timezone": _settings.timezone}


async def save_system_preferences(session: AsyncSession, patch: dict) -> dict[str, str]:
    row = await _load_row(session, _PREFERENCES_KEY)
    stored = dict(row.value) if row and isinstance(row.value, dict) else {}
    timezone = patch.get("timezone")
    if isinstance(timezone, str):
        stored["timezone"] = timezone

    if row is None:
        session.add(Setting(scope=_SCOPE, key=_PREFERENCES_KEY, value=stored))
    else:
        row.value = stored
    await session.commit()

    if isinstance(stored.get("timezone"), str):
        _settings.timezone = stored["timezone"]
    return effective_system_preferences()


async def save_model_config(session: AsyncSession, patch: dict) -> dict:
    """合并保存模型配置：入库 + 覆盖 settings 单例；返回生效配置（脱敏）。"""
    effective_patch = dict(patch)
    # 兼容 Web 设置页旧表单字段名 `mineru_api_key`，统一写入官方 Token 字段
    legacy_ui_token = effective_patch.pop("mineru_api_key", None)
    if legacy_ui_token and not effective_patch.get("mineru_official_api_token"):
        effective_patch["mineru_official_api_token"] = legacy_ui_token

    await _reject_incompatible_embedding_change(session, effective_patch)
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    stored = _normalize_overrides(raw)

    for key, value in effective_patch.items():
        if key not in _FIELDS:
            continue
        if _settings.lock_llm_config and key in _LOCKABLE_LLM_FIELDS:
            continue
        if key in _SECRET_FIELDS:
            if value:
                stored[key] = str(value)
            continue
        if key in _NULLABLE_FIELDS and (value is None or value == ""):
            stored[key] = _OFFICIAL_MINERU_BASE_URL if key == "mineru_base_url" else None
            continue
        stored[key] = value

    stored = _normalize_overrides(stored)

    if row is None:
        session.add(Setting(scope=_SCOPE, key=_KEY, value=stored))
    else:
        row.value = stored
    await session.commit()

    apply_overrides(_settings, stored)
    return effective_model_config()


async def save_302_quick_setup(session: AsyncSession, api_key: str) -> dict:
    """用 302.AI Key 写入生成与向量预设；MinerU 始终保持 Official API，不覆盖官方 Token。"""
    return await save_model_config(
        session,
        {
            **QUICK_SETUP_302,
            "llm_api_key": api_key,
            "embedding_api_key": api_key,
        },
    )


async def save_302_mineru_setup(_session: AsyncSession) -> dict:
    raise ConfigurationError(
        "当前系统仅使用 MinerU Official API v4，请配置 SAG_MINERU_OFFICIAL_API_TOKEN"
    )
