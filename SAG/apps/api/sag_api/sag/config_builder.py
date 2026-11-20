"""由 sag 配置装配 zleap-sag 0.8.2 的 `EngineConfig`。

支持信源级覆盖（`overrides`）——目前支持 `language`，未来可扩展 `entity_types` 等。
0.8.2 变更:`storage_mode` 必填;向量库改为显式 VectorConfig 家族
(Elasticsearch / PgVector / Qdrant / OceanBase),不再使用 vector_provider 字符串。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from zleap.sag import EngineConfig
from zleap.sag.config import (
    ElasticsearchVectorConfig,
    EmbeddingConfig,
    LLMConfig,
    OceanBaseConnectionConfig,
    OceanBaseVectorConfig,
    PgVectorConfig,
    PostgresConnectionConfig,
    RelationalConfig,
    RuntimeLimits,
)
from zleap.sag.core.adapters import defaults as _zleap_defaults
from zleap.sag.core.adapters import registry as _zleap_registry
from zleap.sag.core.ai.structured import StructuredOutputMode
from zleap.sag.core.storage.runtime import SqlRuntime, _postgres_ssl_context

from sag_api.core.config import Settings
from sag_api.sag.qdrant_store import QdrantVectorConfig

# LLM 未配置时的占位符：允许 EngineConfig 构造 / start() 建 schema（离线路径），
# 真正的 ingest / extract / search 会在运行时因缺少凭证而报错（服务层已前置守卫）。
_PLACEHOLDER = "not-configured"


def extract_postgres_search_path(database_url: str) -> str | None:
    """Extract ?search_path=... from a PostgreSQL SQLAlchemy URL so zleap-sag shares sag_api's schema."""
    try:
        url = make_url(database_url)
    except Exception:
        return None
    if not url.drivername.startswith("postgresql"):
        return None
    raw = url.query.get("search_path")
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


class _SearchPathSqlRuntime(SqlRuntime):
    """SqlRuntime subclass that forwards PostgreSQL search_path into asyncpg server_settings."""

    def __init__(self, config: Any, *, search_path: str | None = None, echo: bool = False) -> None:
        super().__init__(config, echo=echo)
        self._search_path = search_path or getattr(config, "_sag_search_path", None)

    def engine(self) -> AsyncEngine:
        if self._engine is not None:
            return self._engine
        if self.provider not in ("postgres", "postgresql") or not self._search_path:
            return super().engine()
        server_settings: dict[str, str] = {"timezone": "UTC", "search_path": self._search_path}
        connect_args: dict[str, Any] = {
            "server_settings": server_settings,
            "timeout": float(getattr(self.config, "connect_timeout", 10.0)),
            "command_timeout": float(getattr(self.config, "command_timeout", 30.0)),
        }
        tls = getattr(self.config, "tls", None)
        if tls is not None:
            connect_args["ssl"] = _postgres_ssl_context(tls)
        self._engine = create_async_engine(
            self.database_url,
            echo=self._echo,
            pool_size=self.config.pool_size,
            max_overflow=self.config.max_overflow,
            pool_pre_ping=True,
            pool_recycle=self.config.pool_recycle,
            pool_timeout=float(getattr(self.config, "pool_timeout", 30.0)),
            connect_args=connect_args,
        )
        return self._engine


class PostgresRelationalStore(_zleap_defaults.SqlAlchemyRelationalStore):
    """RelationalStore for PostgreSQL that honors _sag_search_path when configured."""

    def __init__(self, provider: str = "postgres", config: Any = None) -> None:
        super().__init__(provider=provider, config=config)
        search_path = getattr(self._config, "_sag_search_path", None)
        if search_path:
            self._runtime = _SearchPathSqlRuntime(self._config, search_path=search_path)


_zleap_registry.register("relational", "postgres", PostgresRelationalStore)


def _build_vector(settings: Settings) -> Any:
    provider = settings.sag_vector_provider
    if provider == "es":
        # 0.8.2 要求 hosts;SAG 未提供 ES 地址配置时退回本地默认。
        # TODO(REQ-7/配置):新增 SAG_ES_HOSTS 设置项,生产显式配置。
        return ElasticsearchVectorConfig(hosts=["http://localhost:9200"])
    if provider == "qdrant":
        # zleap-sag 0.12's EngineConfig has no Qdrant model; the validated
        # config is replaced with QdrantVectorConfig after construction below.
        return ElasticsearchVectorConfig(hosts=[settings.sag_qdrant_url])
    if provider == "pgvector":
        conn = PostgresConnectionConfig(
            host=settings.sag_pg_host,
            port=settings.sag_pg_port,
            user=settings.sag_pg_user,
            password=settings.sag_pg_password,
            database=settings.sag_pg_database,
        )
        search_path = extract_postgres_search_path(settings.database_url)
        if search_path:
            object.__setattr__(conn, "_sag_search_path", search_path)
        return PgVectorConfig(connection=conn)
    if provider == "oceanbase":
        return OceanBaseVectorConfig(
            connection=OceanBaseConnectionConfig(
                host=settings.sag_pg_host,
                port=2881,
                user=settings.sag_pg_user,
                password=settings.sag_pg_password,
                database=settings.sag_pg_database,
            )
        )
    raise ValueError(f"Unsupported vector provider: {provider}")


def _build_relational(settings: Settings) -> RelationalConfig | None:
    provider = settings.sag_relational_provider
    if not provider or provider == "sqlite":
        return None  # EngineConfig 从 data_dir 派生 SQLite
    rel = RelationalConfig(
        provider=provider,  # postgres / mysql / oceanbase
        host=settings.sag_pg_host,
        port=settings.sag_pg_port,
        user=settings.sag_pg_user,
        password=settings.sag_pg_password,
        database=settings.sag_pg_database,
    )
    if provider == "postgres":
        search_path = extract_postgres_search_path(settings.database_url)
        if search_path:
            object.__setattr__(rel, "_sag_search_path", search_path)
    return rel


def _structured_output_mode(settings: Settings) -> StructuredOutputMode:
    """映射显式模式；auto 由 SAG LiteLLM seam 首选 schema 并按能力降级。"""
    mode = settings.llm_structured_output_mode
    if mode == "auto":
        return StructuredOutputMode.JSON_SCHEMA
    return StructuredOutputMode(mode)


def build_engine_config(settings: Settings, *, overrides: dict[str, Any] | None = None) -> EngineConfig:
    overrides = overrides or {}

    llm = LLMConfig(
        api_key=settings.llm_api_key or _PLACEHOLDER,
        model=settings.routed_llm_model,
        provider="litellm",
        base_url=settings.llm_base_url,
        temperature=settings.effective_llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=max(1, (settings.llm_timeout_ms + 999) // 1000),
        max_retries=settings.llm_max_retries,
        structured_output_mode=_structured_output_mode(settings),
    )
    emb_kwargs: dict[str, Any] = {
        "model": settings.embedding_model,
        "base_url": settings.effective_embedding_base_url,
        "api_key": settings.effective_embedding_api_key or _PLACEHOLDER,
        # 当前引擎以该值预建向量 schema；未显式配置时保持既有 1024 维默认值。
        "dimensions": settings.embedding_dimensions or 1024,
        "timeout": settings.embedding_timeout,
    }
    if "max_tokens" in getattr(EmbeddingConfig, "model_fields", {}):
        emb_kwargs["max_tokens"] = 8192
    embedding = EmbeddingConfig(**emb_kwargs)

    config = EngineConfig(
        storage_mode="normal",
        llm=llm,
        embedding=embedding,
        relational=_build_relational(settings),
        vector=_build_vector(settings),
        data_dir=str(overrides.get("data_dir") or settings.data_dir),
        language="en",
        runtime_limits=RuntimeLimits(
            llm_concurrency=max(6, min(256, settings.document_extract_concurrency)),
            embedding_concurrency=settings.embedding_concurrency,
            vector_write_concurrency=max(6, min(256, settings.embedding_concurrency)),
            acquire_timeout_seconds=float(max(30, settings.embedding_timeout)),
        ),
    )
    if settings.sag_vector_provider == "qdrant":
        return config.model_copy(
            update={
                "vector": QdrantVectorConfig(
                    url=settings.sag_qdrant_url,
                    api_key=settings.sag_qdrant_api_key,
                )
            }
        )
    return config
