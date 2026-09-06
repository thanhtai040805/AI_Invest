"""
DNSE Intraday OHLCV Tool — direct REST API calls, independent of WebSocket stream hub.
Supports resolutions: 1, 5, 15, 30, 1H, 1D
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.infrastructure.external_api.dnse.api.client import DNSEClient

logger = logging.getLogger(__name__)

RESOLUTION_MAP: Dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1H": "1H",
    "1h": "1H",
    "1D": "1D",
    "1d": "1D",
    "1": "1",
    "3": "3",
    "5": "5",
    "15": "15",
    "30": "30",
}


class DnseIntradayTool:
    """Fetch OHLCV data directly from DNSE REST API.

    Independent of the WebSocket stream hub / Redis pipeline.
    Works for any resolution the DNSE API supports.
    """

    def __init__(self) -> None:
        self._client: Optional[DNSEClient] = None
        self._last_request_ts: float = 0.0

    def _get_client(self) -> DNSEClient:
        if self._client is None:
            settings = get_settings()
            self._client = DNSEClient(
                api_key=settings.dnse_api_key,
                api_secret=settings.dnse_api_secret,
                base_url=settings.dnse_base_url,
            )
        return self._client

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        self._last_request_ts = time.time()

    def fetch(
        self,
        symbol: str,
        resolution: str = "1D",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch OHLCV from DNSE REST API.

        Args:
            symbol: Stock symbol (e.g. "VIC")
            resolution: Candle resolution ("1", "5", "15", "30", "1H", "1D")
            from_ts: Start unix timestamp (seconds)
            to_ts: End unix timestamp (seconds)

        Returns:
            List of {time, open, high, low, close, volume}
        """
        res = RESOLUTION_MAP.get(resolution, resolution)
        if to_ts is None:
            to_ts = int(time.time())
        if from_ts is None:
            lookback = 86400 if str(res) in ("1", "3", "5", "15", "30", "1H") else 30 * 86400
            from_ts = to_ts - lookback

        query: Dict[str, Any] = {"symbol": symbol.upper(), "resolution": res, "from": from_ts, "to": to_ts}

        self._rate_limit()
        client = self._get_client()

        for attempt in range(3):
            status, body = client.get_ohlc(bar_type="STOCK", query=query)

            if status == 429:
                logger.warning(f"DNSE REST rate limited ({symbol} {res}), sleeping 30s")
                time.sleep(30)
                continue

            if status != 200 or not body:
                logger.warning(f"DNSE REST OHLCV {symbol} {res}: status={status}")
                return []

            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                logger.error(f"DNSE REST OHLCV {symbol} {res}: invalid JSON")
                return []

            timestamps = data.get("t", [])
            opens = data.get("o", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            closes = data.get("c", [])
            volumes = data.get("v", [])

            if not timestamps:
                return []

            result = []
            n = len(timestamps)
            for i in range(n):
                ts = timestamps[i]
                if isinstance(ts, (int, float)):
                    ts_sec = ts
                    if ts_sec > 1e12:
                        ts_sec = ts_sec / 1_000_000_000
                    elif ts_sec > 1e10:
                        ts_sec = ts_sec / 1_000
                    time_str = datetime.utcfromtimestamp(ts_sec).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                else:
                    time_str = str(ts)

                result.append({
                    "time": time_str,
                    "open": float(opens[i]) if i < len(opens) else 0.0,
                    "high": float(highs[i]) if i < len(highs) else 0.0,
                    "low": float(lows[i]) if i < len(lows) else 0.0,
                    "close": float(closes[i]) if i < len(closes) else 0.0,
                    "volume": int(volumes[i]) if i < len(volumes) else 0,
                })

            logger.info(
                f"DNSE REST OHLCV {symbol} {res}: {len(result)} candles "
                f"({result[0]['time']} -> {result[-1]['time']})"
            )
            return result

        logger.error(f"DNSE REST OHLCV {symbol} {res}: failed after 3 attempts")
        return []


_intraday_tool: Optional[DnseIntradayTool] = None


def get_intraday_tool() -> DnseIntradayTool:
    global _intraday_tool
    if _intraday_tool is None:
        _intraday_tool = DnseIntradayTool()
    return _intraday_tool
