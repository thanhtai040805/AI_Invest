"""
DNSE REST client — wraps official `dnse` SDK when credentials are present.
"""

from typing import Any, Dict, List, Optional

from app.config.settings import get_settings

try:
    from dnse import DnseClient, BoardId

    DNSE_SDK_AVAILABLE = True
except ImportError:
    DNSE_SDK_AVAILABLE = False
    DnseClient = None  # type: ignore
    BoardId = None  # type: ignore


class DnseRestClient:
    def __init__(self) -> None:
        self._client: Any = None
        self._settings = get_settings()

    @property
    def is_live(self) -> bool:
        return DNSE_SDK_AVAILABLE and self._settings.dnse_configured

    def _get_client(self) -> Any:
        if not self.is_live:
            raise RuntimeError("DNSE REST client not configured")
        if self._client is None:
            self._client = DnseClient(
                api_key=self._settings.dnse_api_key,
                api_secret=self._settings.dnse_api_secret,
                base_url=self._settings.dnse_base_url,
            )
            self._client.__enter__()
        return self._client

    def get_security_info(self, symbol: str) -> Dict[str, Any]:
        client = self._get_client()
        board = BoardId.ROUND_LOT if BoardId else None
        secs = client.market.security_info(symbol.upper(), board_id=board)
        if not secs:
            return {"symbol": symbol}
        s = secs[0]
        ref = float(getattr(s, "ref_price", 0) or getattr(s, "reference_price", 0) or 0)
        return {
            "symbol": symbol.upper(),
            "name": getattr(s, "symbol", symbol),
            "price": ref,
            "prevClose": ref,
            "ceiling": float(getattr(s, "ceiling_price", 0) or 0),
            "floor": float(getattr(s, "floor_price", 0) or 0),
            "exchange": "HOSE",
        }

    def get_ohlc_history(self, symbol: str, timeframe: str = "1D") -> List[Dict]:
        """Historical OHLC via REST — extend when trading token available."""
        # SDK exposes market endpoints; placeholder for backfill jobs
        return []

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.__exit__(None, None, None)
            except Exception:
                pass
            self._client = None


_rest: Optional[DnseRestClient] = None


def get_rest_client() -> DnseRestClient:
    global _rest
    if _rest is None:
        _rest = DnseRestClient()
    return _rest
