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
    from app.services.dnse.websocket.client import TradingClient
    settings = get_settings()

    return TradingClient(
        api_key=settings.dnse_api_key,
        api_secret=settings.dnse_api_secret,
        base_url=settings.dnse_ws_url,
        encoding=settings.encoding,
    )