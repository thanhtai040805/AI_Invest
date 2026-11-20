"""应用配置（pydantic-settings）。

所有配置项均可通过环境变量 `SAG_*` 或 `.env` 覆盖。设计上区分三类后端：

- **sag 元数据库**（用户 / 信源 / 文档 / 会话）：`database_url`
- **zleap-sag 存储**（分块 / 向量 / 事件图谱）：`sag_*` + `data_dir`
- **LLM / embedding**（抽取与答案生成）：`llm_*` / `embedding_*`
- **文档解析**（PDF / Office 等转 Markdown）：`document_parser` / `mineru_*`

默认配置：PostgreSQL 元数据 + Qdrant 向量存储。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from sag_api.core.model_providers import ModelProviderId, get_model_provider
from sag_api.enums import SearchStrategy, normalize_search_strategy

_DEFAULT_LLM_PROVIDER = get_model_provider("openai")
FIXED_SEARCH_TOP_K = 5


def _resolve_default_env_file() -> Path:
    """Resolve the repository dotenv without depending on the process cwd."""
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if (parent / "compose.yaml").is_file():
            return parent / ".env"
    # Installed/container builds receive configuration through process env.
    # Still allow a deployment-local dotenv in the working directory.
    return Path.cwd() / ".env"


_DEFAULT_ENV_FILE = _resolve_default_env_file()


class Settings(BaseSettings):
    _active_data_dir: str | None = PrivateAttr(default=None)

    model_config = SettingsConfigDict(
        env_prefix="SAG_",
        env_file=_DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 应用 ────────────────────────────────────────────────────────────
    app_name: str = "sag"
    environment: Literal["dev", "prod"] = "dev"
    debug: bool = True
    secret_key: str = "dev-insecure-secret-change-me-in-production-0123456789"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 天
    # continuum delegates interactive sign-in to Continuum; local/password are standalone modes.
    auth_mode: Literal["local", "password", "continuum"] = "local"
    # Comma-separated SAG user IDs permitted to change deployment-wide model settings.
    model_config_operator_ids: str = ""
    # 业务展示时区；数据库与 API 时间戳始终使用 UTC。
    timezone: str = "Asia/Shanghai"
    # NoDecode 让逗号分隔值先进入下方 validator，避免 settings 源强制按 JSON 解码。
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3002"])
    # 关闭后仅允许首个用户注册（部署引导），其余返回 403
    allow_registration: bool = True
    # Dify 外部知识库调用的专用服务密钥；未配置时兼容端点拒绝服务。
    dify_api_key: str | None = None
    # Continuum-signed assertion used only for evidence authorization. Missing
    # trust configuration keeps evidence APIs fail-closed instead of enabling
    # a local-user/global-search fallback.
    principal_assertion_issuer: str | None = None
    principal_assertion_audience: str = "sag-api"
    principal_assertion_jwks_url: str | None = None
    principal_assertion_max_lifetime_seconds: int = Field(default=120, ge=1, le=300)
    principal_assertion_clock_skew_seconds: int = Field(default=5, ge=0, le=30)
    principal_assertion_jwks_cache_ttl_seconds: int = Field(default=60, ge=1, le=60)
    principal_assertion_max_project_ids: int = Field(default=256, ge=1, le=4096)
    # Shared only between Continuum BE and this API for project-scoped BFF calls.
    bff_service_token: str | None = None
    # 供桌面端启动时写入本地 DSH 连接文件的公开地址与可选文件位置。
    dsh_public_url: str = "http://127.0.0.1:8000"
    dsh_connection_file: str | None = None
    dsh_local_discovery: bool = False
    dsh_local_discovery_bind_address: str = "127.0.0.1"
    # Dify 检索默认优先低延迟向量召回；可显式设为 multi 启用实体扩展与 LLM 精排。
    dify_search_strategy: SearchStrategy = "vector"

    # ── sag 元数据库 ───────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://sag:sag@localhost:5432/sag"

    # ── 存储 ────────────────────────────────────────────────────────────
    data_dir: str = "./.data/engine"  # OCTX、上传文件和运行时状态
    upload_dir: str = "./.data/uploads"  # 上传原始文件落盘
    max_upload_mb: int = 25  # 单文件上传上限
    job_concurrency: int = 4  # 后台处理并发
    knowledge_worker_enabled: bool = True
    knowledge_graph_timeout_seconds: int = Field(default=300, ge=1)
    knowledge_foundation_timeout_seconds: float | None = Field(default=None, gt=0)
    knowledge_concurrency: int = Field(default=4, ge=1, le=16)
    knowledge_queue_capacity: int = Field(default=256, ge=1, le=10000)
    # Phase 5 runs FOUNDATION (deterministic E0/E1 + DenseGraph) exclusively; E2 LLM enrichment
    # was removed based on 81-file RetrievalCI A/B benchmark evidence (docs/adr-remove-e2-llm-enrichment.md).
    document_extract_concurrency: int = Field(default=6, ge=1, le=50)  # 单文档 chunk 抽取并发
    document_event_extraction_enabled: bool = False
    document_chunk_max_tokens: int = Field(default=1_000, ge=100, le=100_000)
    document_chunk_mode: Literal["standard", "heading_strict"] = "standard"
    document_extraction_profile: Literal["standard", "concise"] = "concise"
    document_write_event_entity_vectors: bool = False
    document_event_entity_attempts: int = Field(default=2, ge=1, le=3)
    # 上传文档已有独立的知识型过滤要求；默认关闭上游基于标题/摘要的严格过滤，
    # 避免无摘要或标题缺失的书籍正文被误判为噪音。
    document_strict_filtering: bool = False
    job_max_attempts: int = 3  # 可重试失败的最大尝试次数（含首次）
    engine_cache_size: int = 16  # 引擎槽 LRU 上限（超限逐出最久未用）
    engine_warmup_count: int = 4  # 启动时预热最近使用的信源引擎数
    # 允许上传的扩展名白名单（小写，含点）；空集合表示不限制
    allowed_upload_exts: set[str] = {
        ".md",
        ".markdown",
        ".txt",
        ".text",
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xls",
        ".xlsx",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".csv",
        ".tsv",
        ".html",
        ".htm",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".drawio",
        ".sql",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".epub",
    }

    # ── OCTX 信源导入导出 ───────────────────────────────────────────────
    # 压缩包在一次性受限子进程中校验/打包；这些值是部署可调的硬上限，
    # 不能只依赖 OCTX SDK 默认值或请求层 Content-Length。
    octx_max_upload_mb: int = Field(default=2048, ge=1)
    octx_max_entries: int = Field(default=10_000, ge=1)
    octx_max_file_mb: int = Field(default=512, ge=1)
    octx_max_uncompressed_mb: int = Field(default=4096, ge=1)
    octx_max_compression_ratio: float = Field(default=100, gt=0)
    octx_max_jsonl_line_mb: int = Field(default=16, ge=1)
    octx_max_jsonl_records: int = Field(default=1_000_000, ge=1)
    octx_max_issues: int = Field(default=1000, ge=1)
    octx_worker_memory_mb: int = Field(default=2048, ge=128)
    octx_worker_timeout_seconds: int = Field(default=1800, ge=1)
    octx_transfer_ttl_hours: int = Field(default=24, ge=1)
    octx_rollback_retention_days: int = Field(default=7, ge=0)
    # OCTX 向量复用是可关闭的兼容加速层；关闭后沿用现有 Embedding 重建路径。
    octx_arrow_vector_reuse_enabled: bool = True
    octx_reused_vector_batch_size: int = Field(default=500, ge=50, le=2000)
    octx_vector_progress_interval_seconds: float = Field(default=1.0, ge=0.1, le=10.0)

    # ── zleap-sag 后端选择 ─────────────────────────────────────────────
    sag_vector_provider: Literal["es", "pgvector", "qdrant", "oceanbase"] = "qdrant"
    sag_relational_provider: Literal["sqlite", "postgres", "mysql", "oceanbase"] = "postgres"
    sag_language: Literal["en", "zh"] = "en"

    # PostgreSQL 元数据连接字段
    sag_pg_host: str = "localhost"
    sag_pg_port: int = 5432
    sag_pg_user: str = "sag"
    sag_pg_password: str = "sag"
    sag_pg_database: str = "sag"
    sag_qdrant_url: str = "http://localhost:6333"
    sag_qdrant_api_key: str | None = None

    # ── LLM（答案生成 + 抽取）─────────────────────────────────────────
    # 协议、路由规则和技术默认值统一由 model_providers 注册表维护。
    llm_provider: ModelProviderId = _DEFAULT_LLM_PROVIDER.id
    llm_base_url: str | None = _DEFAULT_LLM_PROVIDER.default_base_url
    llm_api_key: str | None = None
    llm_model: str = _DEFAULT_LLM_PROVIDER.default_model
    llm_temperature: float = _DEFAULT_LLM_PROVIDER.default_temperature
    llm_max_tokens: int = 20_000
    llm_context_window: int = _DEFAULT_LLM_PROVIDER.default_context_window
    llm_timeout_ms: int = Field(default=60_000, ge=1_000, le=600_000)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    # auto 首选 json_schema，仅在网关明确返回“不支持”时按接入能力缓存降级。
    llm_structured_output_mode: Literal[
        "auto", "json_schema", "json_object", "prompt_only"
    ] = "auto"
    # 部署方可显式锁定 LLM 接入配置；普通 SAG_LLM_* 仅作为首次启动默认值。
    lock_llm_config: bool = False
    # 透传给 chat/completions 的额外请求体（JSON），如 {"enable_thinking": false}；
    # 未配置时对 qwen 系模型通过 LiteLLM reasoning_effort=none 统一关闭思考。
    llm_extra_body: dict | None = None

    # ── Embedding（OpenAI-compatible；仅 OpenAI provider 可复用生成配置）───────
    embedding_model: str = "bge-large-en-v1.5"
    embedding_base_url: str | None = "https://api.302ai.cn/v1"
    embedding_api_key: str | None = None
    embedding_dimensions: int | None = None
    # Per-engine embedding HTTP concurrency. llama.cpp --parallel 1 cannot
    # absorb the zleap default of 8; queued batches hit the client timeout
    # and the indexer rolls the whole document back.
    embedding_concurrency: int = Field(default=6, ge=1, le=64)
    embedding_timeout: int = Field(default=60, ge=5, le=600)
    embedding_max_retries: int = Field(default=3, ge=0, le=10)

    # ── 文档解析（进入 zleap-sag 前统一转为 Markdown）─────────────────
    # auto：PDF 优先 MinerU，未配置或 MinerU 失败时回退本地 MarkItDown。
    document_parser: Literal["auto", "markitdown", "mineru"] = "auto"
    mineru_provider: Literal["official"] = "official"
    mineru_base_url: str | None = "https://mineru.net"
    mineru_official_api_token: str | None = None

    mineru_official_model: Literal["pipeline", "vlm"] = "vlm"
    mineru_parse_method: Literal["auto", "txt", "ocr"] = "auto"
    mineru_request_timeout: float = 60.0
    mineru_poll_interval: float = 2.0
    mineru_poll_timeout: float = 300.0
    mineru_result_max_mb: int = 100

    # ── R2 Object Ingest & Multi-Lane Parsing ──────────────────────────
    object_ingest_allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".r2.cloudflarestorage.com"]
    )
    object_ingest_allow_private_hosts_in_dev: bool = False
    object_ingest_timeout_seconds: float = Field(default=120.0, ge=5.0, le=900.0)
    local_parse_workers: int = Field(default=2, ge=1, le=16)
    local_parse_timeout_seconds: float = Field(default=90.0, ge=5.0, le=600.0)
    local_parse_max_tasks_per_child: int = Field(default=25, ge=1, le=500)
    mineru_io_concurrency: int = Field(default=16, ge=1, le=128)
    fast_parse_concurrency: int = Field(default=16, ge=1, le=128)
    per_project_heavy_parse_limit: int = Field(default=3, ge=1, le=32)
    excel_max_rows_per_sheet: int = Field(default=5000, ge=10, le=100_000)
    excel_max_cols_per_sheet: int = Field(default=64, ge=4, le=512)
    remote_snapshot_gc_enabled: bool = True

    # ── 检索默认 ────────────────────────────────────────────────────────
    # multi_es_fast：默认策略。相关率平均比 vector 高 ~14 个百分点；
    # vector store 不支持 lexical(pgvector/qdrant/oceanbase) 时由 engine_manager 自动降级为 vector。
    search_strategy: SearchStrategy = "multi_es_fast"
    search_top_k: int = Field(default=8, ge=1)  # Initial adaptive search breadth, not a final result cap.
    # Optional scope budget rejects oversized scopes instead of silently truncating sources.
    search_source_candidate_limit: int = Field(default=0, ge=0)  # 0 = all authorized sources.
    search_source_concurrency: int = Field(default=4, ge=1, le=32)
    # Hard cap for canonical SearchUnits hydrated and reranked across all scopes.
    search_unit_candidate_limit: int = Field(default=1024, ge=1)
    search_rerank_min_remaining_seconds: float = Field(default=0.1, ge=0.0, le=2.0)
    search_tree_profile_limit: int = Field(default=8192, ge=256, le=16384)
    # Keep part of the routed query's deadline available to its ACL-scoped global escape.
    search_tree_escape_reserve_seconds: float = Field(default=1.5, ge=0.1, le=10.0)
    search_tree_snapshot_timeout: float = Field(default=3.0, ge=0.05, le=10.0)
    # Exact, profile-content-addressed route decisions only. Semantic matching
    # and answer/evidence caching have no enablement path in this release.
    search_route_decision_cache_enabled: bool = False
    # Diversity knobs are retrieval quality parameters, not answerability/confidence thresholds.
    search_mmr_diversity_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    search_near_duplicate_similarity: float = Field(default=0.98, ge=0.9, le=1.0)
    search_doc_admission_ratio: float = Field(default=0.75, ge=0.0, le=1.0)
    search_same_doc_penalty_weight: float = Field(default=0.06, ge=0.0, le=0.5)
    search_chinese_segmentation_enabled: bool = False
    # 精确模式（multi）含查询侧 LLM 往返；超时/失败/空结果自动回退快速模式（vector）。
    search_source_timeout: float = 25.0
    search_fallback_vector: bool = True

    # ── 检索评测 ────────────────────────────────────────────────────────
    # LLM-as-judge 在评测端点/CLI 里默认开启；线上或不想烧 token 时可临时关闭。
    eval_llm_judge_enabled: bool = True

    # ── 知识宇宙 ──────────────────────────────────────────────────────────
    # 服务端统一下发景深门与场景预算，前端不再散落硬编码阈值。
    universe_manifest_source_limit: int = Field(default=256, ge=16, le=2048)
    universe_timeline_event_page_size: int = Field(default=20, ge=10, le=50)
    # 时间线只返回事件的一屏事实投影；完整邻域由显式探索分页加载。
    universe_event_entity_limit: int = Field(default=8, ge=4, le=8)
    universe_lod_orbit_px: int = Field(default=72, ge=24, le=240)
    universe_lod_near_px: int = Field(default=180, ge=64, le=640)
    universe_lod_deep_px: int = Field(default=360, ge=120, le=1200)
    universe_lod_hysteresis_px: int = Field(default=24, ge=4, le=120)
    universe_lod_debounce_ms: int = Field(default=220, ge=50, le=2000)
    universe_proxy_budget_desktop: int = Field(default=15000, ge=256, le=16000)
    universe_proxy_budget_mobile: int = Field(default=4000, ge=128, le=4800)
    universe_node_budget_desktop: int = Field(default=700, ge=450, le=1200)
    universe_node_budget_mobile: int = Field(default=520, ge=450, le=800)
    universe_edge_budget_desktop: int = Field(default=1000, ge=600, le=1800)
    universe_edge_budget_mobile: int = Field(default=720, ge=600, le=1200)
    universe_planet_radius_min: float = Field(default=42.0, ge=12.0, le=160.0)
    universe_planet_radius_max: float = Field(default=132.0, ge=48.0, le=360.0)
    universe_planet_radius_scale: float = Field(default=22.0, ge=2.0, le=80.0)

    # ── Agent 循环 ──────────────────────────────────────────────────────
    agent_max_steps: int = 6  # 工具调用最大轮数（多轮检索的上界）
    history_keep_recent: int = 8  # 历史压缩时原文保留的最近消息数
    # 只装载最近有界窗口；更旧对话应进入滚动摘要，不做全表回放。
    history_load_limit: int = Field(default=200, ge=1, le=1000)
    session_context_mode: Literal["off", "shadow", "on"] = "off"
    session_context_scope: Literal["thread", "project_owner"] = "thread"
    session_context_embedding_mode: Literal["zero_cost_local", "configured_http"] = "zero_cost_local"
    session_context_persistence_enabled: bool = True
    session_context_qdrant_sync_enabled: bool = True
    session_context_max_messages: int = Field(default=5000, ge=50, le=50000)
    session_context_full_audit_max_batches: int = Field(default=6, ge=1, le=20)
    decision_provider: Literal["siliconflow", "disabled"] = "siliconflow"
    decision_model: str = Field(default="Kev-4b", min_length=1, max_length=200)
    siliconflow_base_url: str = "https://api.siliconflow.com/v1"
    siliconflow_api_key: str | None = None

    @field_validator("siliconflow_base_url")
    @classmethod
    def _validate_siliconflow_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("siliconflow_base_url must be an HTTPS /v1 base URL without credentials")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_decision_config(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        provider = data.get("decision_provider")
        normalized_provider = provider.strip().lower() if isinstance(provider, str) else provider
        if normalized_provider is None or normalized_provider == "" or normalized_provider in ("laya", "openrouter"):
            data["decision_provider"] = "siliconflow"
        if normalized_provider in ("laya", "openrouter") or data.get("decision_model") == "typesafe/jev-1.14":
            data["decision_model"] = "Kev-4b"
        return data

    @field_validator("cors_origins", "object_ingest_allowed_hosts", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """允许用逗号分隔的字符串配置 CORS 源。"""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("mineru_provider", mode="before")
    @classmethod
    def _normalize_mineru_provider(cls, _value: object) -> str:
        """系统仅使用 MinerU Official API v4；旧配置值自动归一为 official。"""
        return "official"

    @field_validator("mineru_base_url", mode="before")
    @classmethod
    def _normalize_mineru_base_url(cls, value: object) -> object:
        """空串或遗留 302 端点自动迁移至 MinerU 官方地址 https://mineru.net。"""
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return "https://mineru.net"
            host = (urlsplit(stripped).hostname or "").lower()
            if host in {"api.302ai.cn", "api.302.ai"}:
                return "https://mineru.net"
            return stripped
        return value

    @field_validator("mineru_official_api_token", mode="before")
    @classmethod
    def _blank_mineru_token_as_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def _blank_embedding_dimensions_as_none(cls, value: object) -> object:
        """compose 以 ${SAG_EMBEDDING_DIMENSIONS} 透传、变量未设置时注入空串，等价于未配置。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("search_strategy", mode="before")
    @classmethod
    def _normalize_legacy_search_strategy(cls, value: object) -> object:
        # 兼容升级前的环境变量；公开 API 已不再接受 atomic。
        return normalize_search_strategy(value) if isinstance(value, str) else value

    @field_validator("sag_language", mode="before")
    @classmethod
    def _normalize_sag_language(cls, value: object) -> str:
        return "en"

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA timezone") from error
        return normalized

    @field_validator("principal_assertion_issuer")
    @classmethod
    def _validate_principal_assertion_issuer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or normalized != value or len(normalized) > 512:
            raise ValueError("principal_assertion_issuer must be a non-empty exact identifier")
        return normalized

    @field_validator("principal_assertion_audience")
    @classmethod
    def _validate_principal_assertion_audience(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or normalized != value or len(normalized) > 256:
            raise ValueError("principal_assertion_audience must be a non-empty exact identifier")
        return normalized

    @field_validator("principal_assertion_jwks_url")
    @classmethod
    def _validate_principal_assertion_jwks_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if (
            normalized != value
            or parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("principal_assertion_jwks_url must be a fixed HTTPS URL without credentials")
        return normalized

    @field_validator("bff_service_token")
    @classmethod
    def _validate_bff_service_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            return None
        if not value or value != value.strip() or len(value) < 32:
            raise ValueError("bff_service_token must contain at least 32 non-padded characters")
        return value

    @field_validator("dsh_public_url")
    @classmethod
    def _validate_dsh_public_url(cls, value: str) -> str:
        """Normalize the trusted HTTP base used in the on-disk DSH descriptor."""
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("dsh_public_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("dsh_public_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("dsh_public_url must not contain query or fragment")
        path = parsed.path.rstrip("/")
        if "//" in path:
            raise ValueError("dsh_public_url path must not contain consecutive slashes")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("dsh_public_url port is invalid") from error
        hostname = parsed.hostname.lower()
        host = f"[{hostname}]" if ":" in hostname else hostname
        authority = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme.lower(), authority, path, "", ""))

    @model_validator(mode="after")
    def _require_production_storage_backends(self) -> Settings:
        if self.environment == "prod":
            if urlsplit(self.database_url).scheme != "postgresql+asyncpg":
                raise ValueError("SAG_DATABASE_URL must use PostgreSQL with asyncpg in prod")
            if self.sag_relational_provider != "postgres":
                raise ValueError("SAG_SAG_RELATIONAL_PROVIDER must be postgres in prod")
            if self.sag_vector_provider != "qdrant":
                raise ValueError("SAG_SAG_VECTOR_PROVIDER must be qdrant in prod")
            qdrant_url = urlsplit(self.sag_qdrant_url)
            if qdrant_url.scheme not in {"http", "https"} or not qdrant_url.hostname:
                raise ValueError("SAG_SAG_QDRANT_URL must be an absolute HTTP(S) URL in prod")
        return self

    @property
    def effective_data_dir(self) -> str:
        """当前进程实际使用的引擎目录；未切换时沿用配置值。"""
        return self._active_data_dir or self.data_dir

    def activate_data_dir(self, path: str | Path) -> None:
        """记录存储引导选定的有效引擎目录，不改写持久配置。"""
        self._active_data_dir = str(Path(path).expanduser().resolve())

    @property
    def llm_configured(self) -> bool:
        """LLM 是否已配置（决定抽取 / 问答能否真正运行）。"""
        return bool(self.llm_api_key)

    @property
    def routed_llm_model(self) -> str:
        """统一调用链使用的 LiteLLM 路由名。"""
        return get_model_provider(self.llm_provider).route_model(self.llm_model)

    @property
    def effective_llm_temperature(self) -> float:
        """应用当前 provider 的采样能力约束。"""
        return get_model_provider(self.llm_provider).resolve_temperature(self.llm_temperature)

    @property
    def effective_embedding_api_key(self) -> str | None:
        provider = get_model_provider(self.llm_provider)
        return self.embedding_api_key or (self.llm_api_key if provider.can_reuse_embedding_credentials else None)

    @property
    def effective_embedding_base_url(self) -> str | None:
        provider = get_model_provider(self.llm_provider)
        return self.embedding_base_url or (self.llm_base_url if provider.can_reuse_embedding_credentials else None)

    @property
    def mineru_configured(self) -> bool:
        """MinerU 是否具备可调用的端点与密钥。"""
        return bool(self.mineru_base_url and self.mineru_official_api_token)

    @property
    def effective_document_parser(self) -> Literal["markitdown", "mineru"]:
        """当前自动解析偏好；具体文件仍由解析服务按格式路由。"""
        if self.document_parser == "markitdown":
            return "markitdown"
        return "mineru" if self.mineru_configured else "markitdown"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
