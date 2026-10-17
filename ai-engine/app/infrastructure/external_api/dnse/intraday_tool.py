"""
DNSE Intraday OHLCV Tool — direct REST API calls, independent of WebSocket stream hub.
Supports resolutions: 1, 5, 15, 30, 1H, 1D
"""

import json
import logging
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config.settings import get_settings
from app.infrastructure.external_api.dnse.price_units import to_vnd_price
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
        self.quote_api_requests = 0
        self.quote_api_pages = 0
        self.quote_api_rate_limits = 0

    def _get_client(self) -> DNSEClient:
        if self._client is None:
            settings = get_settings()
            self._client = DNSEClient(
                api_key=settings.dnse_api_key,
                api_secret=settings.dnse_api_secret,
                base_url=settings.dnse_base_url,
            )
        return self._client

    def _rate_limit(self, minimum_interval: float = 0.1) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        self._last_request_ts = time.time()

    def fetch(
        self,
        symbol: str,
        resolution: str = "1D",
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        strict: bool = False,
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
                if strict:
                    raise RuntimeError(f"DNSE REST OHLCV {symbol} {res}: status={status}")
                return []

            try:
                data = json.loads(body) if isinstance(body, str) else body
            except json.JSONDecodeError:
                logger.error(f"DNSE REST OHLCV {symbol} {res}: invalid JSON")
                if strict:
                    raise ValueError(f"DNSE REST OHLCV {symbol} {res}: invalid JSON")
                return []

            timestamps = data.get("t", [])
            opens = data.get("o", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            closes = data.get("c", [])
            volumes = data.get("v", [])

            if not timestamps:
                return []

            if strict and any(len(values) != len(timestamps) for values in (opens, highs, lows, closes, volumes)):
                raise ValueError(f"DNSE REST OHLCV {symbol} {res}: inconsistent array lengths")

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
                    "open": to_vnd_price(opens[i]) if i < len(opens) else 0.0,
                    "high": to_vnd_price(highs[i]) if i < len(highs) else 0.0,
                    "low": to_vnd_price(lows[i]) if i < len(lows) else 0.0,
                    "close": to_vnd_price(closes[i]) if i < len(closes) else 0.0,
                    "volume": int(volumes[i]) if i < len(volumes) else 0,
                })

            logger.info(
                f"DNSE REST OHLCV {symbol} {res}: {len(result)} candles "
                f"({result[0]['time']} -> {result[-1]['time']})"
            )
            return result

        logger.error(f"DNSE REST OHLCV {symbol} {res}: failed after 3 attempts")
        if strict:
            raise RuntimeError(f"DNSE REST OHLCV {symbol} {res}: failed after 3 attempts")
        return []

    def fetch_quotes(
        self,
        symbol: str,
        from_ts: int,
        to_ts: int,
        board_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Fetch raw historical bid/ask depth, following DNSE page tokens."""
        client = self._get_client()
        settings = get_settings()
        page_token = None
        seen_tokens = set()
        quotes = []
        request_count = page_count = rate_limit_count = 0

        while True:
            self._rate_limit()
            for attempt in range(3):
                request_count += 1
                self.quote_api_requests += 1
                status, body = client.get_quotes(
                    symbol=symbol.upper(),
                    board_id=board_id or settings.board_id,
                    from_date=from_ts,
                    to_date=to_ts,
                    limit=limit,
                    order="DESC",
                    next_page_token=page_token,
                )
                if status == 429:
                    rate_limit_count += 1
                    self.quote_api_rate_limits += 1
                    logger.warning(
                        "DNSE quote history rate limited: symbol=%s page=%d attempt=%d",
                        symbol.upper(), page_count + 1, attempt + 1,
                    )
                    time.sleep(30)
                    continue
                if status != 200 or not body:
                    raise RuntimeError(f"DNSE quote history {symbol}: status={status}")
                break
            else:
                raise RuntimeError(f"DNSE quote history {symbol}: failed after 3 attempts")

            try:
                data = json.loads(body) if isinstance(body, str) else body
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"DNSE quote history {symbol}: invalid JSON") from exc
            if not isinstance(data, dict) or not isinstance(data.get("quotes"), list):
                raise ValueError(f"DNSE quote history {symbol}: missing quotes list")
            page_count += 1
            self.quote_api_pages += 1

            for quote in data["quotes"]:
                raw_time = quote.get("time")
                try:
                    quote_time = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"DNSE quote history {symbol}: invalid quote time") from exc
                if quote_time.tzinfo is None:
                    quote_time = quote_time.replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
                quotes.append({
                    "symbol": str(quote.get("symbol") or symbol).upper(),
                    "board_id": str(quote.get("boardId") or board_id or settings.board_id),
                    "time": quote_time.isoformat(),
                    "bid": quote.get("bid") or [],
                    "offer": quote.get("offer") or [],
                    "total_bid_quantity": quote.get("totalBidQtty"),
                    "total_offer_quantity": quote.get("totalOfferQtty"),
                })

            page_token = data.get("nextPageToken")
            if not page_token:
                break
            if page_token in seen_tokens:
                raise RuntimeError(f"DNSE quote history {symbol}: repeated page token")
            seen_tokens.add(page_token)

        quotes.sort(key=lambda quote: datetime.fromisoformat(quote["time"]))
        logger.info(
            "DNSE quote history %s: %d snapshots, %d pages, %d requests, %d rate limits",
            symbol.upper(), len(quotes), page_count, request_count, rate_limit_count,
        )
        return quotes

    def fetch_raw_trade_bars(
        self,
        symbol: str,
        from_ts: int,
        to_ts: int,
        board_id: Optional[str] = None,
        limit: int = 1000,
        min_request_interval: float = 0.1,
        latest_bar_only: bool = False,
        end_exclusive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Build raw 1-minute candles; optionally stop after the latest candle."""
        if from_ts > to_ts:
            raise ValueError("from_ts must be <= to_ts")

        settings = get_settings()
        client = self._get_client()
        start = datetime.fromtimestamp(from_ts, ZoneInfo("Asia/Ho_Chi_Minh"))
        end = datetime.fromtimestamp(to_ts, ZoneInfo("Asia/Ho_Chi_Minh"))
        page_token = None
        seen_tokens = set()
        bars: Dict[datetime, Dict[str, Any]] = {}
        latest_minute = None
        request_count = page_count = rate_limit_count = 0

        while True:
            self._rate_limit(minimum_interval=min_request_interval)
            for attempt in range(3):
                request_count += 1
                status, body = client.get_trades(
                    symbol=symbol.upper(),
                    board_id=board_id or settings.board_id,
                    from_date=from_ts,
                    to_date=to_ts,
                    limit=limit,
                    order="DESC",
                    next_page_token=page_token,
                )
                if status == 429:
                    rate_limit_count += 1
                    logger.warning(
                        "DNSE trade history rate limited: symbol=%s page=%d attempt=%d",
                        symbol.upper(), page_count + 1, attempt + 1,
                    )
                    time.sleep(30 * (attempt + 1))
                    continue
                if status != 200 or not body:
                    raise RuntimeError(f"DNSE trade history {symbol}: status={status}")
                break
            else:
                raise RuntimeError(f"DNSE trade history {symbol}: failed after 3 attempts")

            try:
                data = json.loads(body) if isinstance(body, str) else body
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(f"DNSE trade history {symbol}: invalid JSON") from exc
            if not isinstance(data, dict) or not isinstance(data.get("trades"), list):
                raise ValueError(f"DNSE trade history {symbol}: missing trades list")
            page_count += 1

            page_minutes = []
            for trade in data["trades"]:
                try:
                    trade_time = datetime.fromisoformat(str(trade["time"]).replace("Z", "+00:00"))
                    trade_time = (
                        trade_time.replace(tzinfo=start.tzinfo)
                        if trade_time.tzinfo is None
                        else trade_time.astimezone(start.tzinfo)
                    )
                    raw_price = Decimal(str(trade["matchPrice"]))
                    raw_quantity = Decimal(str(trade["matchQtty"]))
                except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                    raise ValueError(f"DNSE trade history {symbol}: invalid trade record") from exc
                if not start <= trade_time or (trade_time >= end if end_exclusive else trade_time > end):
                    continue
                minute = trade_time.replace(second=0, microsecond=0)
                page_minutes.append(minute)
                if latest_bar_only:
                    if latest_minute is None or minute > latest_minute:
                        latest_minute = minute
                        bars.clear()
                    elif minute < latest_minute:
                        continue
                if not raw_price.is_finite() or raw_price <= 0 or not raw_quantity.is_finite():
                    raise ValueError(f"DNSE trade history {symbol}: non-positive trade price or invalid quantity")
                if raw_quantity <= 0 or raw_quantity != raw_quantity.to_integral_value():
                    raise ValueError(f"DNSE trade history {symbol}: invalid matchQtty")

                price = to_vnd_price(raw_price)
                bar = bars.get(minute)
                if bar is None:
                    bars[minute] = {
                        "time": minute.isoformat(),
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        # DNSE matchQtty is in 10-share units; reconciled against OHLC volume.
                        "volume": int(raw_quantity) * 10,
                        "_open_time": trade_time,
                        "_close_time": trade_time,
                    }
                    continue

                bar["high"] = max(bar["high"], price)
                bar["low"] = min(bar["low"], price)
                bar["volume"] += int(raw_quantity) * 10
                if trade_time <= bar["_open_time"]:
                    bar["open"] = price
                    bar["_open_time"] = trade_time
                if trade_time > bar["_close_time"]:
                    bar["close"] = price
                    bar["_close_time"] = trade_time

            page_token = data.get("nextPageToken")
            if latest_bar_only and latest_minute is not None and page_minutes and min(page_minutes) < latest_minute:
                break
            if not page_token:
                break
            if page_token in seen_tokens:
                raise RuntimeError(f"DNSE trade history {symbol}: repeated page token")
            seen_tokens.add(page_token)

        result = []
        for minute in sorted(bars):
            bar = bars[minute]
            result.append({key: value for key, value in bar.items() if not key.startswith("_")})
        logger.debug(
            "DNSE raw trade bars %s: %d candles, %d pages, %d requests, %d rate limits",
            symbol.upper(), len(result), page_count, request_count, rate_limit_count,
        )
        return result


_intraday_tool: Optional[DnseIntradayTool] = None


def get_intraday_tool() -> DnseIntradayTool:
    global _intraday_tool
    if _intraday_tool is None:
        _intraday_tool = DnseIntradayTool()
    return _intraday_tool
