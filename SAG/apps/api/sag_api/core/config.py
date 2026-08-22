"""Cấu hình ứng dụng (pydantic-settings).

Mọi mục cấu hình đều có thể ghi đè qua biến môi trường `SAG_*` hoặc `.env`. Về thiết kế phân biệt ba loại backend:

- **Cơ sở dữ liệu meta của sag** (người dùng / nguồn / tài liệu / phiên hội thoại): `database_url`
- **Lưu trữ zleap-sag** (chunk / vector / đồ thị sự kiện): `sag_*` + `data_dir`
- **LLM / embedding** (trích xuất và sinh câu trả lời): `llm_*` / `embedding_*`
- **Phân tích tài liệu** (PDF / Office... sang Markdown): `document_parser` / `mineru_*`

Mặc định zero-dependency: metadata SQLite + LanceDB cục bộ của zleap-sag. Sản xuất có thể chuyển toàn bộ sang Postgres.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from sag_api.core.model_providers import ModelProviderId, get_model_provider
from sag_api.enums import SearchStrategy, normalize_search_strategy

_PLACEHOLDER = "not-configured"
_DEFAULT_LLM_PROVIDER = get_model_provider("openai")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Ứng dụng ────────────────────────────────────────────────────────────
    app_name: str = "sag"
    environment: Literal["dev", "prod"] = "dev"
    debug: bool = True
    secret_key: str = "dev-insecure-secret-change-me-in-production-0123456789"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 ngày
    # Múi giờ hiển thị nghiệp vụ; timestamp cơ sở dữ liệu và API luôn dùng UTC.
    timezone: str = "Asia/Ho_Chi_Minh"
    # NoDecode cho phép giá trị phân tách bằng dấu phẩy đi vào validator bên dưới trước, tránh nguồn settings ép giải mã theo JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    # Khi tắt chỉ cho phép người dùng đầu tiên đăng ký (hướng dẫn triển khai), các yêu cầu khác trả về 403
    allow_registration: bool = True
    # Khóa dịch vụ riêng cho cuộc gọi kho tri thức ngoài của Dify; khi chưa cấu hình endpoint tương thích từ chối phục vụ.
    dify_api_key: str | None = None
    # Truy vấn Dify mặc định ưu tiên truy hồi vector độ trễ thấp; có thể đặt thành multi để bật mở rộng thực thể và xếp hạng lại bằng LLM.
    dify_search_strategy: SearchStrategy = "vector"

    # ── Cơ sở dữ liệu meta của sag ───────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./.data/sag.db"

    # ── Lưu trữ ────────────────────────────────────────────────────────────
    data_dir: str = "./.data/engine"  # zleap-sag data_dir (LanceDB + SQLite)
    upload_dir: str = "./.data/uploads"  # ghi file gốc upload xuống đĩa
    max_upload_mb: int = 25  # giới hạn upload mỗi file
    job_concurrency: int = 2  # độ đồng thời xử lý nền
    document_extract_concurrency: int = Field(default=30, ge=1, le=50)  # độ đồng thời trích xuất chunk cho mỗi tài liệu
    document_chunk_max_tokens: int = Field(default=12_000, ge=100, le=100_000)
    document_chunk_mode: Literal["standard", "heading_strict"] = "standard"


    # Tài liệu upload đã có yêu cầu lọc theo loại tri thức riêng; mặc định tắt bộ lọc nghiêm ngặt dựa trên tiêu đề/tóm tắt của thượng nguồn,
    # tránh nội dung sách không có tóm tắt hoặc thiếu tiêu đề bị nhận nhầm là nhiễu.
    document_strict_filtering: bool = False
    job_max_attempts: int = 3  # số lần thử tối đa cho lỗi có thể thử lại (gồm lần đầu)
    engine_cache_size: int = 16  # giới hạn LRU của slot engine (vượt giới hạn sẽ đuổi cái dùng lâu nhất)
    engine_warmup_count: int = 4  # số engine nguồn gần nhất được làm nóng lúc khởi động
    # Danh sách trắng đuôi file được phép upload (chữ thường, có dấu chấm); tập rỗng nghĩa là không giới hạn
    allowed_upload_exts: set[str] = {
        ".md",
        ".markdown",
        ".txt",
        ".text",
        ".pdf",
        ".docx",
        ".pptx",
        ".xls",
        ".xlsx",
        ".csv",
        ".tsv",
        ".html",
        ".htm",
        ".json",
        ".epub",
    }

    # ── Lựa chọn backend zleap-sag ─────────────────────────────────────────────
    # None → không cơ sở hạ tầng (LanceDB + SQLite tích hợp, nằm trong data_dir)
    sag_vector_provider: Literal["lancedb", "es", "pgvector", "oceanbase"] = "lancedb"
    sag_relational_provider: Literal["sqlite", "postgres", "mysql", "oceanbase"] | None = None
    sag_language: Literal["vi", "en", "zh"] = "vi"

    @field_validator("sag_language", mode="before")
    @classmethod
    def _normalize_language(cls, value: object) -> object:
        if isinstance(value, str):
            val = value.strip().lower()
            if val in ("zh", "cn", "zh-cn"):
                return "vi"
            return val
        return value

    # Khi dùng một cơ sở dữ liệu sản xuất (pgvector) thì tái sử dụng cùng Postgres — lắp ghép từ các trường này
    sag_pg_host: str = "localhost"
    sag_pg_port: int = 5432
    sag_pg_user: str = "sag"
    sag_pg_password: str = "sag"
    sag_pg_database: str = "sag"

    # ── LLM (sinh câu trả lời + trích xuất) ─────────────────────────────────────────
    # Giao thức, quy tắc route và giá trị mặc định kỹ thuật được bảng model_providers duy trì thống nhất.
    llm_provider: ModelProviderId = _DEFAULT_LLM_PROVIDER.id
    llm_base_url: str | None = _DEFAULT_LLM_PROVIDER.default_base_url
    llm_api_key: str | None = None
    llm_model: str = _DEFAULT_LLM_PROVIDER.default_model
    llm_temperature: float = _DEFAULT_LLM_PROVIDER.default_temperature
    llm_max_tokens: int = 20_000
    llm_context_window: int = _DEFAULT_LLM_PROVIDER.default_context_window
    llm_timeout_ms: int = Field(default=60_000, ge=1_000, le=600_000)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    # Bên triển khai có thể khóa tường minh cấu hình kết nối LLM; các SAG_LLM_* thông thường chỉ là giá trị mặc định lần khởi động đầu.
    lock_llm_config: bool = False
    # Request body bổ sung truyền tiếp tới chat/completions (JSON), ví dụ {"enable_thinking": false};
    # khi chưa cấu hình, với các model họ qwen sẽ tắt suy luận thống nhất qua LiteLLM reasoning_effort=none.
    llm_extra_body: dict | None = None

    # ── Model trích xuất Graph riêng (tùy chọn; mặc định tái dùng llm_model) ────────
    extraction_llm_model: str | None = None
    extraction_llm_base_url: str | None = None
    extraction_llm_api_key: str | None = None

    # ── Model Agent suy luận riêng (tùy chọn; mặc định tái dùng llm_model) ────────
    agent_llm_model: str | None = None
    agent_llm_base_url: str | None = None
    agent_llm_api_key: str | None = None

    # ── Embedding (tương thích OpenAI; chỉ provider OpenAI mới tái dùng được cấu hình sinh) ───────
    embedding_model: str = "bge-large-en-v1.5"
    embedding_base_url: str | None = "https://api.302ai.cn/v1"
    embedding_api_key: str | None = None
    embedding_dimensions: int | None = None

    # ── Phân tích tài liệu (chuyển thống nhất sang Markdown trước khi vào zleap-sag) ─────────────────
    # auto: PDF ưu tiên MinerU, khi chưa cấu hình hoặc MinerU lỗi thì quay lại MarkItDown cục bộ.
    document_parser: Literal["auto", "markitdown", "mineru"] = "auto"
    mineru_base_url: str | None = "https://api.302ai.cn"
    mineru_api_key: str | None = None
    mineru_version: Literal["2.0", "2.5"] = "2.5"
    mineru_parse_method: Literal["auto", "txt", "ocr"] = "auto"
    mineru_request_timeout: float = 60.0
    mineru_poll_interval: float = 2.0
    mineru_poll_timeout: float = 300.0
    mineru_result_max_mb: int = 100

    # ── Mặc định truy vấn ────────────────────────────────────────────────────────
    search_strategy: SearchStrategy = "vector"
    search_top_k: int = 8
    # Truy vấn toàn kho chọn trước các nguồn ứng viên giới hạn; phạm vi @ tường minh cũng được bảo vệ bởi giới hạn cứng này.
    search_source_candidate_limit: int = Field(default=16, ge=1, le=256)
    search_source_concurrency: int = Field(default=4, ge=1, le=32)
    # Chế độ chính xác (multi) bao gồm vòng LLM phía truy vấn; hết thời gian/lỗi/kết quả rỗng tự động quay về chế độ nhanh (vector).
    search_source_timeout: float = 12.0
    search_fallback_vector: bool = True

    # ── Vũ trụ tri thức ──────────────────────────────────────────────────────────
    # Máy chủ gửi thống nhất ngưỡng độ sâu (LOD) và ngân sách cảnh, frontend không còn rải rác ngưỡng hardcode.
    universe_manifest_source_limit: int = Field(default=256, ge=16, le=2048)
    universe_timeline_event_page_size: int = Field(default=20, ge=10, le=50)
    # Dòng thời gian chỉ trả về một màn hình chiếu dữ kiện của sự kiện; vùng lân cận đầy đủ được tải phân trang bằng khám phá tường minh.
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

    # ── Vòng lặp Agent ──────────────────────────────────────────────────────
    agent_max_steps: int = 6  # số vòng gọi công cụ tối đa (cận trên cho truy vấn nhiều vòng)
    history_keep_recent: int = 8  # số tin nhắn gần đây giữ nguyên văn khi nén lịch sử
    # Chỉ nạp cửa sổ giới hạn gần đây; hội thoại cũ hơn nên vào tóm tắt cuộn, không phát lại toàn bảng.
    history_load_limit: int = Field(default=200, ge=1, le=1000)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Cho phép cấu hình nguồn CORS bằng chuỗi phân tách dấu phẩy."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("search_strategy", mode="before")
    @classmethod
    def _normalize_legacy_search_strategy(cls, value: object) -> object:
        # Tương thích biến môi trường trước khi nâng cấp; API công khai không còn chấp nhận atomic.
        return normalize_search_strategy(value) if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone phải là múi giờ IANA hợp lệ") from error
        return normalized

    @property
    def llm_configured(self) -> bool:
        """LLM đã được cấu hình hay chưa (quyết định trích xuất / hỏi đáp có thực sự chạy được không)."""
        return bool(self.llm_api_key)

    @property
    def routed_llm_model(self) -> str:
        """Tên route LiteLLM dùng cho chuỗi gọi thống nhất."""
        return get_model_provider(self.llm_provider).route_model(self.llm_model)

    @property
    def routed_extraction_llm_model(self) -> str:
        """Tên route LiteLLM dùng cho trích xuất Graph."""
        model = self.extraction_llm_model if (self.extraction_llm_model and self.extraction_llm_model != _PLACEHOLDER) else self.llm_model
        return get_model_provider(self.llm_provider).route_model(model)

    @property
    def effective_extraction_llm_base_url(self) -> str | None:
        return self.extraction_llm_base_url or self.llm_base_url

    @property
    def effective_extraction_llm_api_key(self) -> str | None:
        if self.extraction_llm_api_key and self.extraction_llm_api_key != _PLACEHOLDER:
            return self.extraction_llm_api_key
        return self.llm_api_key

    @property
    def routed_agent_llm_model(self) -> str:
        """Tên route LiteLLM dùng cho Agent suy luận."""
        model = self.agent_llm_model if (self.agent_llm_model and self.agent_llm_model != _PLACEHOLDER and "YOUR_TENCENT" not in self.agent_llm_model) else self.llm_model
        return get_model_provider(self.llm_provider).route_model(model)

    @property
    def effective_agent_llm_base_url(self) -> str | None:
        return self.agent_llm_base_url or self.llm_base_url

    @property
    def effective_agent_llm_api_key(self) -> str | None:
        if self.agent_llm_api_key and self.agent_llm_api_key != _PLACEHOLDER and "YOUR_TENCENT" not in self.agent_llm_api_key:
            return self.agent_llm_api_key
        return self.llm_api_key

    @property
    def effective_llm_temperature(self) -> float:
        """Áp dụng ràng buộc khả năng lấy mẫu của provider hiện tại."""
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
        """MinerU có endpoint và khóa gọi được hay không."""
        return bool(self.mineru_base_url and self.mineru_api_key)

    @property
    def effective_document_parser(self) -> Literal["markitdown", "mineru"]:
        """Ưu tiên phân tích tự động hiện tại; file cụ thể vẫn do dịch vụ phân tích route theo định dạng."""
        if self.document_parser == "markitdown":
            return "markitdown"
        return "mineru" if self.mineru_configured else "markitdown"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
