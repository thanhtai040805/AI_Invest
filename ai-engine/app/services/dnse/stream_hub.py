"""
DNSE WebSocket market stream hub.

Runs DnseMarketStream in a background thread, caches latest ticks,
and publishes to Redis for the Node.js Socket.IO relay.
"""

import asyncio
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from app.config.settings import get_settings
from app.services.dnse.redis_pub import publish_json, set_cache

try:
    from dnse import DnseMarketStream

    DNSE_SDK_AVAILABLE = True
except ImportError:
    DNSE_SDK_AVAILABLE = False
    DnseMarketStream = None  # type: ignore


def _trend(change_pct: float) -> str:
    if change_pct > 0:
        return "up"
    if change_pct < 0:
        return "down"
    return "steady"


class DnseStreamHub:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._quotes: Dict[str, Dict[str, Any]] = {}
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._subscribed: Set[str] = set()
        self._stream: Any = None
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        if self._settings.dnse_configured and DNSE_SDK_AVAILABLE:
            return "live" if self._connected else "connecting"
        return "mock"

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "running": self._running,
            "connected": self._connected,
            "sdk_available": DNSE_SDK_AVAILABLE,
            "configured": self._settings.dnse_configured,
            "subscribed_count": len(self._subscribed),
            "cached_quotes": len(self._quotes),
        }

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._quotes.get(symbol.upper())

    def get_orderbook(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._orderbooks.get(symbol.upper())

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            stocks = list(self._quotes.values())
        return {"stocks": stocks, "total": len(stocks)}

    def subscribe_symbols(self, symbols: List[str]) -> None:
        """Register symbols for stream (reconnect applies new set)."""
        for sym in symbols:
            self._subscribed.add(sym.upper())
        if self._running and self._settings.dnse_configured:
            self._restart_stream()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for sym in self._settings.default_symbol_list:
            self._subscribed.add(sym)

        if self._settings.dnse_configured and DNSE_SDK_AVAILABLE:
            self._thread = threading.Thread(target=self._run_stream, daemon=True, name="dnse-ws")
            self._thread.start()
            print("[DNSE Stream] Starting live WebSocket hub...")
        else:
            self._thread = threading.Thread(target=self._run_mock_loop, daemon=True, name="dnse-mock")
            self._thread.start()
            print("[DNSE Stream] No API keys — mock tick loop active")

    def stop(self) -> None:
        self._running = False
        self._connected = False

    def _restart_stream(self) -> None:
        # Next iteration will pick up symbol set; full reconnect on stop/start
        pass

    def _map_trade(self, msg: Any) -> Dict[str, Any]:
        sym = str(getattr(msg, "symbol", "") or "").upper()
        price = float(getattr(msg, "price", 0) or 0)
        volume = int(getattr(msg, "volume", 0) or 0)
        change = float(getattr(msg, "change", 0) or 0)
        pct = float(getattr(msg, "change_percent", 0) or getattr(msg, "pct_change", 0) or 0)
        return {
            "symbol": sym,
            "name": sym,
            "price": price,
            "change": change,
            "changePercent": pct,
            "volume": volume,
            "tradingValue": price * volume,
            "open": price,
            "high": price,
            "low": price,
            "prevClose": price - change if change else price,
            "ceiling": 0,
            "floor": 0,
            "trend": _trend(pct),
            "lastUpdate": datetime.now().isoformat(),
        }

    def _map_quote(self, msg: Any, symbol: str) -> Dict[str, Any]:
        bid = float(getattr(msg, "bid_price", 0) or getattr(msg, "bid1", 0) or 0)
        ask = float(getattr(msg, "ask_price", 0) or getattr(msg, "ask1", 0) or 0)
        bids = [{"price": bid, "volume": int(getattr(msg, "bid_volume", 0) or 1000)}]
        asks = [{"price": ask, "volume": int(getattr(msg, "ask_volume", 0) or 1000)}]
        return {
            "symbol": symbol,
            "bids": bids,
            "asks": asks,
            "lastUpdate": datetime.now().isoformat(),
        }

    async def _on_trade(self, msg: Any) -> None:
        quote = self._map_trade(msg)
        sym = quote["symbol"]
        if not sym:
            return
        with self._lock:
            self._quotes[sym] = quote
        set_cache(f"stock:{sym}:quote", quote, 2)
        publish_json(f"quote:{sym}", quote)
        self._maybe_broadcast_snapshot()

    async def _on_quote(self, msg: Any) -> None:
        sym = str(getattr(msg, "symbol", "") or "").upper()
        if not sym:
            return
        book = self._map_quote(msg, sym)
        with self._lock:
            self._orderbooks[sym] = book
        set_cache(f"stock:{sym}:orderbook", book, 2)
        publish_json(f"orderbook:{sym}", book)

    def _maybe_broadcast_snapshot(self) -> None:
        snap = self.get_snapshot()
        if not snap["stocks"]:
            return
        set_cache("market:snapshot", snap, 3)
        publish_json("snapshot", snap)
        self._publish_breadth(snap["stocks"])

    def _publish_breadth(self, stocks: List[Dict]) -> None:
        adv = sum(1 for s in stocks if s.get("changePercent", 0) > 0)
        dec = sum(1 for s in stocks if s.get("changePercent", 0) < 0)
        unch = len(stocks) - adv - dec
        payload = {
            "advancers": adv,
            "decliners": dec,
            "unchanged": unch,
            "lastUpdate": datetime.now().isoformat(),
        }
        set_cache("market:breadth", payload, 5)
        publish_json("breadth", payload)

    def _run_stream(self) -> None:
        settings = self._settings
        symbols = list(self._subscribed) or settings.default_symbol_list

        def run_blocking() -> None:
            try:
                stream = DnseMarketStream(
                    api_key=settings.dnse_api_key,
                    api_secret=settings.dnse_api_secret,
                )
                self._stream = stream
                stream.subscribe_trades(symbols, self._on_trade)
                stream.subscribe_quotes(symbols, self._on_quote)
                self._connected = True
                print(f"[DNSE Stream] Subscribed trades+quotes: {symbols[:10]}...")
                stream.run()
            except Exception as e:
                self._connected = False
                print(f"[DNSE Stream] Error: {e}")

        while self._running:
            run_blocking()
            if self._running:
                import time
                time.sleep(5)

    def _run_mock_loop(self) -> None:
        """Emit mock ticks when DNSE keys are not configured."""
        from app.services.market_data_service import MOCK_STOCKS

        while self._running:
            for row in MOCK_STOCKS:
                sym = row["symbol"]
                quote = {**row, "lastUpdate": datetime.now().isoformat(), "trend": _trend(row.get("changePercent", 0))}
                with self._lock:
                    self._quotes[sym] = quote
                publish_json(f"quote:{sym}", quote)
            self._maybe_broadcast_snapshot()
            indices = {
                "indices": [
                    {"name": "VN-INDEX", "value": 1284.5, "change": 12.4, "changePercent": 1.02, "volume": 842100000, "trend": "up"},
                    {"name": "VN30", "value": 1302.1, "change": 15.2, "changePercent": 1.18, "volume": 245600000, "trend": "up"},
                    {"name": "HNX", "value": 242.8, "change": -0.4, "changePercent": -0.16, "volume": 98400000, "trend": "down"},
                ]
            }
            publish_json("indices", indices)
            import time
            time.sleep(2)


_hub: Optional[DnseStreamHub] = None


def get_stream_hub() -> DnseStreamHub:
    global _hub
    if _hub is None:
        _hub = DnseStreamHub()
    return _hub
