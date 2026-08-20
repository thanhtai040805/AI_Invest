"""Cấu hình mô hình và kho tri thức thời chạy — lớp ghi đè DB nằm trên mặc định env (singleton `settings`).

Minh họa cục bộ một người dùng: lưu cấu hình «mô hình và tìm kiếm» vào bảng `settings` (scope=global, key=model_config).
Khi khởi động và sau khi lưu, ghi đè **tại chỗ** các trường tương ứng của singleton `settings`, endpoint lại dựng lại `LLMClient` / đặt lại engine ấm,
khiến thay đổi cấu hình **có hiệu lực không cần khởi động lại**. api_key lưu vào DB dạng plaintext (chấp nhận được với cục bộ một người dùng), khi đọc thì khử nhạy cảm (chỉ trả về đã đặt hay chưa).
"""

from __future__ import annotations

from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.core.config import Settings
from sag_api.core.config import settings as _settings
from sag_api.core.errors import ConfigurationError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import get_model_provider
from sag_api.db.models import Setting
from sag_api.enums import SEARCH_STRATEGIES, normalize_search_strategy

_SCOPE = "global"
_KEY = "model_config"
_PREFERENCES_KEY = "system_preferences"
log = get_logger("settings")

# Các trường được phép ghi đè thời chạy (giá trị đã được schema request kiểm tra/chuyển kiểu)
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
        "embedding_model",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_dimensions",
        "document_parser",
        "mineru_base_url",
        "mineru_api_key",
        "mineru_version",
        "document_extract_concurrency",
        "document_chunk_max_tokens",
        "document_chunk_mode",
        "search_strategy",
        "search_top_k",
        "sag_language",
    }
)
_SECRET_FIELDS = frozenset({"llm_api_key", "embedding_api_key", "mineru_api_key"})
_NULLABLE_FIELDS = frozenset({"llm_base_url", "embedding_base_url", "embedding_dimensions", "mineru_base_url"})
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
    }
)
_MODEL_CONFIG_SOURCES = {field: "default" for field in _FIELDS - _SECRET_FIELDS}

_OPENAI_COMPATIBLE = get_model_provider("openai")

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
    "mineru_base_url": "https://api.302ai.cn",
    "mineru_version": "2.5",
    "document_extract_concurrency": 30,
    "document_chunk_max_tokens": 12_000,
    "document_chunk_mode": "standard",

    "search_strategy": "vector",
    "search_top_k": 8,
    "sag_language": "vi",
}

_LEGACY_302_BASE_URLS = {
    "https://api.302.ai": "https://api.302ai.cn",
    "https://api.302.ai/v1": "https://api.302ai.cn/v1",
}


async def _load_row(session: AsyncSession, key: str = _KEY) -> Setting | None:
    return await session.scalar(select(Setting).where(Setting.scope == _SCOPE, Setting.key == key))


def _normalize_overrides(overrides: dict) -> dict:
    """Dọn cấu hình đã lưu, đảm bảo chiến lược đã gỡ hoặc không hợp lệ không vào được thời chạy."""
    normalized = dict(overrides)
    for field in ("llm_base_url", "embedding_base_url", "mineru_base_url"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = _LEGACY_302_BASE_URLS.get(value.rstrip("/"), value)
    strategy = normalized.get("search_strategy")
    if strategy == "atomic":
        normalized["search_strategy"] = normalize_search_strategy(strategy)
        log.warning("Chiến lược tìm kiếm cũ atomic đã chuyển sang chế độ chính xác multi")
    elif strategy is not None and strategy not in SEARCH_STRATEGIES:
        normalized.pop("search_strategy", None)
        log.warning("Bỏ qua chiến lược tìm kiếm đã lưu không hợp lệ: %s", strategy)
    return normalized


async def load_overrides(session: AsyncSession) -> dict:
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    return _normalize_overrides(raw)


async def model_setup_status(session: AsyncSession) -> dict[str, bool]:
    """Xác định có cần cấu hình mô hình lần đầu hay không, không bị singleton settings sau khi bị DB ghi đè lúc chạy làm nhiễu."""
    row = await _load_row(session)
    environment_configured = Settings().llm_configured
    database_configured = bool(row and isinstance(row.value, dict) and row.value.get("llm_api_key"))
    return {
        "required": not environment_configured and not database_configured,
        "environment_configured": environment_configured,
        "database_configured": database_configured,
    }


def apply_overrides(settings: Settings, overrides: dict) -> None:
    """Ghi cấu hình đã lưu trở lại settings; khi khóa triển khai thì giữ nguyên giá trị env LLM."""
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


async def apply_startup_overrides(session_factory: async_sessionmaker) -> None:
    """Lúc khởi động: ghi đè cấu hình mô hình trong DB lên singleton settings (gọi trước khi dựng LLMClient)."""
    async with session_factory() as session:
        row = await _load_row(session)
        raw = dict(row.value) if row and isinstance(row.value, dict) else {}
        overrides = _normalize_overrides(raw)
        if row is not None and overrides != raw:
            # Cột JSON không dùng MutableDict, phải gán lại toàn bộ mới lưu bền vững được.
            row.value = overrides
            await session.commit()
        apply_overrides(_settings, overrides)
        preferences = await _load_row(session, _PREFERENCES_KEY)
        preference_values = dict(preferences.value) if preferences and isinstance(preferences.value, dict) else {}
        timezone = preference_values.get("timezone")
        if isinstance(timezone, str):
            # Stored values were validated on write. Settings assignment is kept
            # explicit so model configuration and presentation preferences remain separate.
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                log.warning("Bỏ qua múi giờ đã lưu không hợp lệ: %s", timezone)
            else:
                _settings.timezone = timezone


def effective_model_config() -> dict:
    """Cấu hình mô hình đang có hiệu lực (đọc singleton settings; khóa được khử nhạy cảm thành boolean *_set)."""
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
        "embedding_model": _settings.embedding_model,
        "embedding_base_url": _settings.embedding_base_url,
        "embedding_dimensions": _settings.embedding_dimensions,
        "embedding_api_key_set": bool(_settings.embedding_api_key),
        "document_parser": _settings.document_parser,
        "effective_document_parser": _settings.effective_document_parser,
        "mineru_base_url": _settings.mineru_base_url,
        "mineru_version": _settings.mineru_version,
        "mineru_api_key_set": bool(_settings.mineru_api_key),
        "document_extract_concurrency": _settings.document_extract_concurrency,
        "document_chunk_max_tokens": _settings.document_chunk_max_tokens,
        "document_chunk_mode": _settings.document_chunk_mode,
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
    """Gộp lưu cấu hình mô hình: vào DB + ghi đè singleton settings; trả về cấu hình đang hiệu lực (khử nhạy cảm).

    Quy ước (kết hợp `exclude_unset`):
    - Trường không xuất hiện → giữ nguyên;
    - Trường khóa có giá trị rỗng → bỏ qua (giữ khóa cũ, tránh xóa nhầm); giá trị rỗng chỉ bị ghi đè khi có giá trị mới rõ ràng;
    - Trường nullable (base_url / dimensions) có giá trị rỗng → đặt None (xóa).
    """
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    stored = _normalize_overrides(raw)

    for key, value in patch.items():
        if key not in _FIELDS:
            continue
        if _settings.lock_llm_config and key in _LOCKABLE_LLM_FIELDS:
            continue
        if key in _SECRET_FIELDS:
            if value:  # Chỉ cập nhật khi không rỗng; rỗng/None giữ giá trị cũ
                stored[key] = str(value)
            continue
        if key in _NULLABLE_FIELDS and (value is None or value == ""):
            stored[key] = None
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
    """Dùng một Key 302.AI để ghi các preset sinh văn bản, embedding, MinerU và tìm kiếm nhanh."""
    return await save_model_config(
        session,
        {
            **QUICK_SETUP_302,
            "llm_api_key": api_key,
            "embedding_api_key": api_key,
            "mineru_api_key": api_key,
        },
    )


async def save_302_mineru_setup(session: AsyncSession) -> dict:
    """Tái sử dụng Key hiện có cho cấu hình mô hình 302 đã có, không gửi khóa trở lại trình duyệt."""
    candidates = (
        (_settings.llm_base_url, _settings.llm_api_key),
        (_settings.effective_embedding_base_url, _settings.effective_embedding_api_key),
    )
    for base_url, api_key in candidates:
        parsed = urlparse(base_url or "")
        host = (parsed.hostname or "").lower()
        if host not in {"api.302.ai", "api.302ai.cn"} or not api_key:
            continue
        return await save_model_config(
            session,
            {
                "document_parser": "auto",
                "mineru_base_url": "https://api.302ai.cn",
                "mineru_api_key": api_key,
                "mineru_version": "2.5",
            },
        )
    raise ConfigurationError("Không tìm thấy API Key mô hình 302.AI có thể tái sử dụng")
