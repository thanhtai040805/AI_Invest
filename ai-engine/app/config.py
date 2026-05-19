import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

DNSE_API_KEY = os.getenv("DNSE_API_KEY", "")
DNSE_API_SECRET = os.getenv("DNSE_API_SECRET", "")
DNSE_BASE_URL = os.getenv("DNSE_BASE_URL", "https://openapi.dnse.com.vn")
DNSE_WS_URL = os.getenv("DNSE_WS_URL", "wss://ws-openapi.dnse.com.vn")

BOARD_ID = os.getenv("DNSE_BOARD_ID", "G1")
ENCODING = os.getenv("DNSE_ENCODING", "msgpack")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_CHANNEL_PREFIX = os.getenv("DNSE_REDIS_CHANNEL_PREFIX", "dnse:event")
DEFAULT_SYMBOLS = os.getenv("DNSE_DEFAULT_SYMBOLS", "VNM,FPT,VIC,SSI,HPG,VCB,MWG,MSN,TCB,ACB")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")


def get_dnse_client():
    from dnse import DNSEClient

    return DNSEClient(
        api_key=DNSE_API_KEY,
        api_secret=DNSE_API_SECRET,
        base_url=DNSE_BASE_URL,
    )


def get_ws_client():
    from app.services.dnse.websocket.client import TradingClient

    return TradingClient(
        api_key=DNSE_API_KEY,
        api_secret=DNSE_API_SECRET,
        base_url=DNSE_WS_URL,
        encoding=ENCODING,
    )