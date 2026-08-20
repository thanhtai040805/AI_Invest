"""Lắp ráp `EngineConfig` của zleap-sag từ cấu hình sag.

Hỗ trợ ghi đè cấp nguồn (`overrides`)——hiện hỗ trợ `language`, tương lai có thể mở rộng `entity_types` v.v.
"""

from __future__ import annotations

from typing import Any

from zleap.sag import EngineConfig
from zleap.sag.config import EmbeddingConfig, LLMConfig, RelationalConfig

from sag_api.core.config import Settings

# Placeholder khi LLM chưa được cấu hình: cho phép EngineConfig khởi tạo / start() dựng schema (đường offline),
# các bước ingest / extract / search thực sự sẽ báo lỗi lúc chạy vì thiếu chứng thực (lớp dịch vụ đã chặn trước).
_PLACEHOLDER = "not-configured"


def build_engine_config(settings: Settings, *, overrides: dict[str, Any] | None = None) -> EngineConfig:
    overrides = overrides or {}

    llm = LLMConfig(
        api_key=settings.effective_extraction_llm_api_key or _PLACEHOLDER,
        model=settings.routed_extraction_llm_model,
        provider="litellm",
        base_url=settings.effective_extraction_llm_base_url,
        temperature=settings.effective_llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=max(1, (settings.llm_timeout_ms + 999) // 1000),
        max_retries=settings.llm_max_retries,
    )
    embedding = EmbeddingConfig(
        model=settings.embedding_model,
        base_url=settings.effective_embedding_base_url,
        api_key=settings.effective_embedding_api_key or _PLACEHOLDER,
        dimensions=settings.embedding_dimensions,
    )

    # zleap engine hiện chỉ hỗ trợ zh / en; sag_language cho phép vi (dùng cho Agent/UI), ở đây chuẩn hóa về en.
    raw_language = overrides.get("language", settings.sag_language)
    if raw_language not in ("zh", "en"):
        raw_language = "en"

    kwargs: dict[str, Any] = {
        "llm": llm,
        "embedding": embedding,
        "data_dir": settings.data_dir,
        "language": raw_language,
        "vector_provider": settings.sag_vector_provider,
    }

    # Sản xuất: chuyển sang backend quan hệ (như Postgres), thống nhất cùng một DB với pgvector
    if settings.sag_relational_provider:
        kwargs["relational"] = RelationalConfig(
            provider=settings.sag_relational_provider,
            host=settings.sag_pg_host,
            port=settings.sag_pg_port,
            user=settings.sag_pg_user,
            password=settings.sag_pg_password,
            database=settings.sag_pg_database,
        )

    return EngineConfig(**kwargs)
