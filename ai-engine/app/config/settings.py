"""
Application settings — DNSE Open API credentials and feature flags.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from dotenv import load_dotenv

# Load .env file variables into environment
load_dotenv()

@dataclass(frozen=True)
class Settings:
    dnse_api_key: str
    dnse_api_secret: str
    dnse_account_no: str
    dnse_enabled: bool
    dnse_base_url: str
    redis_url: str
    redis_channel_prefix: str
    dnse_default_symbols: str
    llm_api_key: str
    llm_provider: str

    @property
    def dnse_configured(self) -> bool:
        return bool(self.dnse_api_key and self.dnse_api_secret)

    @property
    def default_symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.dnse_default_symbols.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings(
        dnse_api_key=os.getenv("DNSE_API_KEY", ""),
        dnse_api_secret=os.getenv("DNSE_API_SECRET", ""),
        dnse_account_no=os.getenv("DNSE_ACCOUNT_NO", ""),
        dnse_enabled=os.getenv("DNSE_ENABLED", "false").lower() in ("1", "true", "yes"),
        dnse_base_url=os.getenv("DNSE_BASE_URL", "https://openapi.dnse.com.vn"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        redis_channel_prefix=os.getenv("DNSE_REDIS_CHANNEL_PREFIX", "dnse:event"),
        dnse_default_symbols=os.getenv(
            "DNSE_DEFAULT_SYMBOLS",
            "VNM,FPT,VIC,SSI,HPG,VCB,MWG,MSN,TCB,ACB",
        ),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_provider=os.getenv("LLM_PROVIDER", "openai"),
    )
