from .settings import get_settings, get_dnse_client, get_ws_client

# Khởi tạo instance để bóc tách biến cho các file khác sử dụng
_settings = get_settings()

DNSE_API_KEY = _settings.dnse_api_key
DNSE_API_SECRET = _settings.dnse_api_secret
DNSE_BASE_URL = _settings.dnse_base_url
DNSE_WS_URL = _settings.dnse_ws_url
BOARD_ID = _settings.board_id
ENCODING = _settings.encoding

REDIS_URL = _settings.redis_url
REDIS_CHANNEL_PREFIX = _settings.redis_channel_prefix

LLM_API_KEY = _settings.llm_api_key
LLM_PROVIDER = _settings.llm_provider

__all__ = [
    "get_settings",
    "get_dnse_client",
    "get_ws_client",
    "DNSE_API_KEY",
    "DNSE_API_SECRET",
    "DNSE_BASE_URL",
    "DNSE_WS_URL",
    "BOARD_ID",
    "ENCODING",
    "REDIS_URL",
    "REDIS_CHANNEL_PREFIX",
    "LLM_API_KEY",
    "LLM_PROVIDER",
]