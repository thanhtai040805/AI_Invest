"""
Application settings — DNSE Open API credentials and feature flags.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv

# Xác định đường dẫn file .env chính xác (đi lên 2 cấp từ app/config/settings.py để ra thư mục gốc)
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


@dataclass(frozen=True)
class Settings:
    dnse_api_key: str
    dnse_api_secret: str
    dnse_account_no: str
    dnse_enabled: bool
    dnse_base_url: str
    dnse_ws_url: str
    board_id: str
    encoding: str
    redis_url: str
    redis_channel_prefix: str
    llm_api_key: str
    llm_provider: str
    evomap_api_key: str

    # AI Multi-Model Configuration
    llm_nvidia_key: str
    llm_nvidia_model: str
    llm_groq_key: str
    llm_groq_key0: str
    llm_groq_key1: str
    llm_groq_model: str
    llm_groq_model0: str
    llm_groq_model1: str
    llm_routing_mode: str
    llm_fallback_hardcoded: bool

    # Multi-Model Routing Configuration
    enable_fallback: bool
    confidence_threshold: float
    max_parallel_calls: int

    @property
    def dnse_configured(self) -> bool:
        return bool(self.dnse_api_key and self.dnse_api_secret)

@lru_cache
def get_settings() -> Settings:
    return Settings(
        dnse_api_key=os.getenv("DNSE_API_KEY", ""),
        dnse_api_secret=os.getenv("DNSE_API_SECRET", ""),
        dnse_account_no=os.getenv("DNSE_ACCOUNT_NO", ""),
        dnse_enabled=os.getenv("DNSE_ENABLED", "false").lower() in ("1", "true", "yes"),
        dnse_base_url=os.getenv("DNSE_BASE_URL", "https://openapi.dnse.com.vn"),
        dnse_ws_url=os.getenv("DNSE_WS_URL", "wss://ws-openapi.dnse.com.vn"),
        board_id=os.getenv("DNSE_BOARD_ID", "G1"),
        encoding=os.getenv("DNSE_ENCODING", "msgpack"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        redis_channel_prefix=os.getenv("DNSE_REDIS_CHANNEL_PREFIX", "dnse:event"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_provider=os.getenv("LLM_PROVIDER", "openai"),
        evomap_api_key=os.getenv("EVOMAP_API_KEY", ""),
        
        # AI Multi-Model env loaders (supports standard names and shorthand variables)
        llm_nvidia_key=os.getenv("NVDIA", os.getenv("NVIDIA_API_KEY", "")),
        llm_nvidia_model=os.getenv("NVIDIA_MODEL", "minimaxai/minimax-m2.7"),
        llm_groq_key=os.getenv("GROQ_API_KEY0", os.getenv("GROQ_API_KEY", "")),
        llm_groq_key0=os.getenv("GROQ_API_KEY0", os.getenv("GROQ_API_KEY", "")),
        llm_groq_key1=os.getenv("GROQ_API_KEY1", os.getenv("GROQ_API_KEY", "")),
        llm_groq_model=os.getenv("GROQ_MODEL0", os.getenv("GROQ_MODEL", "qwen/qwen3-32b")),
        llm_groq_model0=os.getenv("GROQ_MODEL0", os.getenv("GROQ_MODEL", "qwen/qwen3-32b")),
        llm_groq_model1=os.getenv("GROQ_MODEL1", "qwen/qwen3-32b"),
        llm_routing_mode=os.getenv("LLM_ROUTING_MODE", "auto"),
        llm_fallback_hardcoded=os.getenv("LLM_FALLBACK_HARDCODED", "true").lower() in ("1", "true", "yes"),

        # Multi-Model Routing Configuration
        enable_fallback=os.getenv("ENABLE_FALLBACK", "true").lower() in ("1", "true", "yes"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.75")),
        max_parallel_calls=int(os.getenv("MAX_PARALLEL_CALLS", "3")),
    )


# Các hàm helper để tạo client từ cấu hình tập trung ở trên
def get_dnse_client():
    from dnse import DnseClient  
    settings = get_settings()  #  Lấy instance settings ra trước
    return DnseClient(          
        api_key=settings.dnse_api_key,       #  Sửa thành settings.dnse_api_key
        api_secret=settings.dnse_api_secret, #  Sửa thành settings.dnse_api_secret
        base_url=settings.dnse_base_url,     #  Sửa thành settings.dnse_base_url
    )


def get_ws_client():
    from app.infrastructure.external_api.dnse.websocket.client import TradingClient
    settings = get_settings()

    return TradingClient(
        api_key=settings.dnse_api_key,
        api_secret=settings.dnse_api_secret,
        base_url=settings.dnse_ws_url,
        encoding=settings.encoding,
    )


def get_evomap_client():
    from openai import OpenAI
    settings = get_settings()
    key = settings.evomap_api_key
    if key and not key.startswith("sk-evomap-"):
        key = f"sk-evomap-{key}"
    return OpenAI(
        base_url="https://api.evomap.ai/v1",
        api_key=key,
    )


def get_async_evomap_client():
    from openai import AsyncOpenAI
    settings = get_settings()
    key = settings.evomap_api_key
    if key and not key.startswith("sk-evomap-"):
        key = f"sk-evomap-{key}"
    return AsyncOpenAI(
        base_url="https://api.evomap.ai/v1",
        api_key=key,
    )
