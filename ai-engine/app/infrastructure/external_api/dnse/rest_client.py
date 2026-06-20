"""
DNSE REST client — wraps official `dnse` SDK when credentials are present.
Falls back to public DNSE API for OHLC data when SDK unavailable.
"""

import httpx
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
        ref = float(getattr(s, "basic_price", 0) or getattr(s, "ref_price", 0) or getattr(s, "reference_price", 0) or 0)
        return {
            "symbol": symbol.upper(),
            "name": getattr(s, "symbol", symbol),
            "price": ref,
            "prevClose": ref,
            "ceiling": float(getattr(s, "ceiling_price", 0) or 0),
            "floor": float(getattr(s, "floor_price", 0) or 0),
            "exchange": "HOSE",
        }

    def get_ohlcv(self, symbol: str, interval: str = "1D", start: Optional[str] = None, end: Optional[str] = None) -> List[Dict]:
        """Historical OHLC via DNSE REST API.

        Supports resolutions: 1, 5, 15, 30, 1H, 1D.
        Falls back to DNSE REST API (not WebSocket/Redis).
        """
        from datetime import datetime, timezone, timedelta
        from app.infrastructure.external_api.dnse.intraday_tool import get_intraday_tool

        tool = get_intraday_tool()
        TZ_VN = timezone(timedelta(hours=7))

        now = datetime.now(TZ_VN)
        from_dt: Optional[datetime] = None
        to_dt: Optional[datetime] = None

        if start:
            try:
                from_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(TZ_VN)
            except ValueError:
                from_dt = None
        if end:
            try:
                to_dt = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(TZ_VN)
            except ValueError:
                to_dt = None

        if from_dt is None:
            from_dt = now - timedelta(days=30)
        if to_dt is None:
            to_dt = now

        from_ts = int(from_dt.timestamp())
        to_ts = int(to_dt.timestamp())

        return tool.fetch(symbol, resolution=interval, from_ts=from_ts, to_ts=to_ts)

    def get_ohlc_history(self, symbol: str, timeframe: str = "1D") -> List[Dict]:
        """Historical OHLC via REST."""
        return self.get_ohlcv(symbol, timeframe)

    def get_market_indices(self) -> List[Dict]:
        """Get market indices."""
        if self.is_live:
            try:
                client = self._get_client()
                return client.market.indices() or []
            except Exception:
                pass
        return []

    def get_fundamentals(self, symbol: str) -> Dict:
        """Get stock fundamentals from security info."""
        if self.is_live:
            try:
                info = self.get_security_info(symbol.upper())
                if info.get("price", 0) > 0:
                    return info
            except Exception:
                pass
        return {}

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
