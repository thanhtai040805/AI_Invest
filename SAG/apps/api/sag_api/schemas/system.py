from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from sag_api.core.model_providers import ModelProviderId
from sag_api.enums import SearchStrategy


class QuickModelSetupRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=500)

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("API Key không được để trống")
        return value


class SystemPreferencesUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Phải dùng múi giờ IANA hợp lệ, ví dụ Asia/Ho_Chi_Minh") from error
        return normalized


class ModelConfigUpdate(BaseModel):
    """Cập nhật một phần cấu hình model và kho tri thức (các trường không xuất hiện sẽ giữ nguyên).

    Để trống trường khóa nghĩa là "giữ nguyên giá trị" (không xóa); để trống base_url / dimensions nghĩa là xóa bỏ.
    """

    llm_provider: ModelProviderId | None = None
    llm_base_url: str | None = Field(default=None, max_length=500)
    llm_api_key: str | None = Field(default=None, max_length=500)
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    llm_context_window: int | None = Field(default=None, ge=1024, le=2_000_000)
    llm_timeout_ms: int | None = Field(default=None, ge=1_000, le=600_000)
    llm_max_retries: int | None = Field(default=None, ge=0, le=10)

    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_base_url: str | None = Field(default=None, max_length=500)
    embedding_api_key: str | None = Field(default=None, max_length=500)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=8192)

    document_parser: Literal["auto", "markitdown", "mineru"] | None = None
    mineru_base_url: str | None = Field(default=None, max_length=500)
    mineru_api_key: str | None = Field(default=None, max_length=500)
    mineru_version: Literal["2.0", "2.5"] | None = None
    document_extract_concurrency: int | None = Field(default=None, ge=1, le=50)
    document_chunk_max_tokens: int | None = Field(default=None, ge=100, le=2_000_000)
    document_chunk_mode: Literal["standard", "heading_strict", "full"] | None = None

    search_strategy: SearchStrategy | None = None
    search_top_k: int | None = Field(default=None, ge=1, le=50)
    sag_language: Literal["vi", "en", "zh"] | None = None

    @field_validator("document_parser", "mineru_version")
    @classmethod
    def reject_null_parser_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Trình phân tích và phiên bản MinerU không thể là null")
        return value

    @field_validator("document_extract_concurrency", "document_chunk_max_tokens")
    @classmethod
    def reject_null_document_numbers(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("Tham số phân tích kho tri thức không thể là null")
        return value

    @field_validator("document_chunk_mode")
    @classmethod
    def reject_null_chunk_mode(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Chế độ chia chunk không thể là null")
        return value

    @field_validator("llm_timeout_ms", "llm_max_retries")
    @classmethod
    def reject_null_llm_resilience_fields(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("Thời gian chờ và số lần thử lại của model không thể là null")
        return value

    @field_validator("llm_provider")
    @classmethod
    def reject_null_llm_provider(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Cách kết nối model không thể là null")
        return value
