"""DNSE Open API integration — REST + WebSocket market stream."""

from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client
from .api._version import __version__ as APIVersion
from .api.client import DNSEClient
from .websocket._version import __version__ as WSVersion
from .websocket.client import TradingClient
from .websocket.exceptions import (
    TradingWebSocketError,
    ConnectionError,
    ConnectionClosed,
    AuthenticationError,
    SubscriptionError,
    EncodingError,
)

__all__ = [
    "get_stream_hub", 
    "get_rest_client", 
    "DNSEClient",
    "APIVersion",
    "WSVersion",
    "TradingClient",
    "TradingWebSocketError",
    "ConnectionError",
    "ConnectionClosed",
    "AuthenticationError",
    "SubscriptionError",
    "EncodingError"
]
