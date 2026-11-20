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
            raise ValueError("API Key 不能为空")
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
            raise ValueError("必须使用有效的 IANA 时区，例如 Asia/Shanghai") from error
        return normalized


class ModelConfigUpdate(BaseModel):
    """模型与知识库配置的部分更新（未出现的字段保持不变）。

    密钥字段留空表示「保持原值」（不清空）；base_url / dimensions 留空表示清除。
    MinerU 仅支持 Official API v4（`SAG_MINERU_OFFICIAL_API_TOKEN`）。
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

    decision_provider: Literal["siliconflow", "disabled"] | None = None
    decision_model: str | None = Field(default=None, min_length=1, max_length=200)
    siliconflow_api_key: str | None = Field(default=None, max_length=500)

    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_base_url: str | None = Field(default=None, max_length=500)
    embedding_api_key: str | None = Field(default=None, max_length=500)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=8192)

    document_parser: Literal["auto", "markitdown", "mineru"] | None = None
    mineru_provider: Literal["official"] | None = None
    mineru_base_url: str | None = Field(default=None, max_length=500)
    mineru_official_api_token: str | None = Field(default=None, max_length=2048)
    mineru_api_key: str | None = Field(default=None, max_length=2048)
    mineru_official_model: Literal["pipeline", "vlm"] | None = None
    document_extract_concurrency: int | None = Field(default=None, ge=1, le=50)
    document_chunk_max_tokens: int | None = Field(default=None, ge=100, le=100_000)
    document_chunk_mode: Literal["standard", "heading_strict"] | None = None
    document_extraction_profile: Literal["standard", "concise"] | None = None

    search_strategy: SearchStrategy | None = None
    search_top_k: int | None = Field(default=None, ge=1)
    sag_language: Literal["en", "zh"] | None = None

    @field_validator("sag_language", mode="before")
    @classmethod
    def normalize_sag_language(cls, value: str | None) -> str | None:
        return "en" if value is not None else None

    @field_validator("mineru_provider", mode="before")
    @classmethod
    def normalize_mineru_provider(cls, value: object) -> str:
        if value is None:
            raise ValueError("解析器与 MinerU 服务商/模型配置不能为 null")
        return "official"

    @field_validator(
        "document_parser",
        "mineru_official_model",
    )
    @classmethod
    def reject_null_parser_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("解析器与 MinerU 服务商/模型配置不能为 null")
        return value

    @field_validator("document_extract_concurrency", "document_chunk_max_tokens")
    @classmethod
    def reject_null_document_numbers(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("知识库解析参数不能为 null")
        return value

    @field_validator("document_chunk_mode")
    @classmethod
    def reject_null_chunk_mode(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("切片模式不能为 null")
        return value

    @field_validator("document_extraction_profile")
    @classmethod
    def reject_null_extraction_profile(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("document_extraction_profile cannot be null")
        return value

    @field_validator("llm_timeout_ms", "llm_max_retries")
    @classmethod
    def reject_null_llm_resilience_fields(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("模型超时与重试次数不能为 null")
        return value

    @field_validator("llm_provider")
    @classmethod
    def reject_null_llm_provider(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("模型接入方式不能为 null")
        return value

    @field_validator("decision_provider")
    @classmethod
    def reject_null_decision_provider(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Decision Router 接入方式不能为 null")
        return value

    @field_validator("decision_model")
    @classmethod
    def normalize_decision_model(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("Decision model ID 不能为空")
        return value.strip()

    @field_validator("siliconflow_api_key", "mineru_official_api_token", "mineru_api_key")
    @classmethod
    def normalize_optional_secret(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None
