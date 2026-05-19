"""
DNSE WebSocket market stream hub.

Runs TradingClient in a background thread, caches latest ticks,
and publishes to Redis for the Node.js Socket.IO relay.

Based on market_data_stream.py logic with full channel support:
- expected_price, foreign_trading, quotes, sec_def, trade_extra
- trades, ohlc_closed, ohlc, market_index
"""

import asyncio
import json
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from app.config import (
    DNSE_API_KEY,
    DNSE_API_SECRET,
    DNSE_BASE_URL,
    DNSE_WS_URL,
    BOARD_ID,
    ENCODING,
)
from app.config.settings import get_settings
from app.services.dnse.redis_pub import (
    publish_json,
    set_cache,
    push_to_list,
    get_list_range,
    add_to_sorted_set,
    get_sorted_set_range,
)


class DnseStreamHub:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._quotes: Dict[str, Dict[str, Any]] = {}
        self._orderbooks: Dict[str, Dict[str, Any]] = {}
        self._trades: Dict[str, List[Dict[str, Any]]] = {}
        self._ohlc: Dict[str, Dict[str, Any]] = {}
        self._market_index: Dict[str, Dict[str, Any]] = {}
        self._foreign: Dict[str, Dict[str, Any]] = {}
        self._sec_def: Dict[str, Dict[str, Any]] = {}
        self._subscribed: Set[str] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        if DNSE_API_KEY and DNSE_API_SECRET:
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

    def get_trade_history(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trade history from Redis List (most recent first)."""
        sym = symbol.upper()
        return get_list_range(f"trade:{sym}", 0, limit - 1)

    def get_trade_extra_history(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trade extra history from Redis List."""
        sym = symbol.upper()
        return get_list_range(f"trade_extra:{sym}", 0, limit - 1)

    def get_ohlc_history(
        self,
        symbol: str,
        resolution: str = "1",
        from_time: Optional[int] = None,
        to_time: Optional[int] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Get OHLC closed history from Redis Sorted Set."""
        sym = symbol.upper()
        min_score = from_time if from_time else "-inf"
        max_score = to_time if to_time else "+inf"
        return get_sorted_set_range(f"ohlc_closed:{sym}:{resolution}", min_score, max_score)[-limit:]

    def get_ohlc_live(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current live OHLC (not closed)."""
        with self._lock:
            ohlc = self._ohlc.get(symbol.upper(), {})
            return ohlc if ohlc.get("type") == "live" else None

    def subscribe_symbols(self, symbols: List[str]) -> None:
        """Register symbols for stream."""
        for sym in symbols:
            self._subscribed.add(sym.upper())

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        if DNSE_API_KEY and DNSE_API_SECRET:
            self._thread = threading.Thread(target=self._run_ws_loop, daemon=True, name="dnse-ws")
            self._thread.start()
            print("[DNSE Stream] Starting live WebSocket hub with TradingClient...")
        else:
            self._thread = threading.Thread(target=self._run_mock_loop, daemon=True, name="dnse-mock")
            self._thread.start()
            print("[DNSE Stream] No API keys — mock tick loop active")

    def stop(self) -> None:
        self._running = False
        self._connected = False

    def _map_trade(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        price = float(getattr(data, "price", 0) or 0)
        volume = int(getattr(data, "volume", 0) or 0)
        change = float(getattr(data, "change", 0) or 0)
        pct = float(getattr(data, "change_percent", 0) or getattr(data, "pct_change", 0) or 0)
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
            "trend": "up" if pct > 0 else "down" if pct < 0 else "steady",
            "lastUpdate": datetime.now().isoformat(),
        }

    def _map_quote(self, data: Any, symbol: str) -> Dict[str, Any]:
        bids = []
        asks = []
        for i in range(1, 11):
            bid_price = getattr(data, f"bid{i}", None)
            bid_vol = getattr(data, f"bid_volume{i}", None)
            ask_price = getattr(data, f"ask{i}", None)
            ask_vol = getattr(data, f"ask_volume{i}", None)
            if bid_price and bid_vol:
                bids.append({"price": float(bid_price), "volume": int(bid_vol)})
            if ask_price and ask_vol:
                asks.append({"price": float(ask_price), "volume": int(ask_vol)})
        return {
            "symbol": symbol,
            "bids": bids,
            "asks": asks,
            "lastUpdate": datetime.now().isoformat(),
        }

    def _map_ohlc(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        return {
            "symbol": sym,
            "open": float(getattr(data, "open", 0) or 0),
            "high": float(getattr(data, "high", 0) or 0),
            "low": float(getattr(data, "low", 0) or 0),
            "close": float(getattr(data, "close", 0) or 0),
            "volume": int(getattr(data, "volume", 0) or 0),
            "resolution": getattr(data, "resolution", "1"),
            "timestamp": getattr(data, "timestamp", None),
            "lastUpdate": datetime.now().isoformat(),
        }

    def _map_market_index(self, data: Any) -> Dict[str, Any]:
        name = getattr(data, "market_index", "") or ""
        return {
            "name": name.upper(),
            "value": float(getattr(data, "index_value", 0) or 0),
            "change": float(getattr(data, "change", 0) or 0),
            "changePercent": float(getattr(data, "pct_change", 0) or 0),
            "volume": int(getattr(data, "total_volume", 0) or 0),
            "lastUpdate": datetime.now().isoformat(),
        }

    def _map_foreign(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        return {
            "symbol": sym,
            "buyVolume": int(getattr(data, "buy_volume", 0) or 0),
            "sellVolume": int(getattr(data, "sell_volume", 0) or 0),
            "netVolume": int(getattr(data, "net_volume", 0) or 0),
            "buyValue": float(getattr(data, "buy_value", 0) or 0),
            "sellValue": float(getattr(data, "sell_value", 0) or 0),
            "netValue": float(getattr(data, "net_value", 0) or 0),
            "lastUpdate": datetime.now().isoformat(),
        }

    def _map_sec_def(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        return {
            "symbol": sym,
            "name": getattr(data, "company_name", "") or "",
            "exchange": getattr(data, "exchange", "") or "",
            "ceiling": float(getattr(data, "ceiling_price", 0) or 0),
            "floor": float(getattr(data, "floor_price", 0) or 0),
            "prevClose": float(getattr(data, "previous_close", 0) or 0),
            "lastUpdate": datetime.now().isoformat(),
        }

    def _on_expected_price(self, data: Any) -> None:
        sym = str(getattr(data, "symbol", "") or "").upper()
        if not sym:
            return
        payload = {
            "symbol": sym,
            "expectedPrice": float(getattr(data, "expected_price", 0) or 0),
            "matchedVolume": int(getattr(data, "matched_volume", 0) or 0),
            "receivedAt": getattr(data, "receivedAt", None),
            "lastUpdate": datetime.now().isoformat(),
        }
        set_cache(f"stock:{sym}:expected_price", payload, 2)
        publish_json(f"expected_price:{sym}", payload)

    def _on_foreign_trading(self, data: Any) -> None:
        payload = self._map_foreign(data)
        sym = payload.get("symbol")
        if not sym:
            return
        with self._lock:
            self._foreign[sym] = payload
        set_cache(f"stock:{sym}:foreign", payload, 5)
        publish_json(f"foreign:{sym}", payload)

    def _on_market_index(self, data: Any) -> None:
        payload = self._map_market_index(data)
        name = payload.get("name", "")
        if not name:
            return
        with self._lock:
            self._market_index[name] = payload
        set_cache(f"index:{name}", payload, 3)
        publish_json(f"index:{name}", payload)
        self._publish_indices()

    def _on_ohlc_closed(self, data: Any) -> None:
        payload = self._map_ohlc(data)
        sym = payload.get("symbol")
        if not sym:
            return
        timestamp = payload.get("timestamp") or int(datetime.now().timestamp())
        resolution = payload.get("resolution", "1")
        with self._lock:
            self._ohlc[sym] = {**payload, "type": "closed"}
        set_cache(f"stock:{sym}:ohlc_closed", payload, 10)
        ohlc_key = f"ohlc_closed:{sym}:{resolution}"
        add_to_sorted_set(ohlc_key, timestamp, payload, ttl=86400)
        publish_json(f"ohlc_closed:{sym}", payload)

    def _on_ohlc(self, data: Any) -> None:
        payload = self._map_ohlc(data)
        sym = payload.get("symbol")
        if not sym:
            return
        with self._lock:
            self._ohlc[sym] = {**payload, "type": "live"}
        set_cache(f"stock:{sym}:ohlc", payload, 2)
        publish_json(f"ohlc:{sym}", payload)

    def _on_quote(self, data: Any) -> None:
        sym = str(getattr(data, "symbol", "") or "").upper()
        if not sym:
            return
        book = self._map_quote(data, sym)
        with self._lock:
            self._orderbooks[sym] = book
        set_cache(f"stock:{sym}:orderbook", book, 2)
        publish_json(f"orderbook:{sym}", book)

    def _on_sec_def(self, data: Any) -> None:
        payload = self._map_sec_def(data)
        sym = payload.get("symbol")
        if not sym:
            return
        with self._lock:
            self._sec_def[sym] = payload
        set_cache(f"stock:{sym}:sec_def", payload, 3600)
        publish_json(f"sec_def:{sym}", payload)

    def _on_trade_extra(self, data: Any) -> None:
        sym = str(getattr(data, "symbol", "").upper())
        if not sym:
            return
        payload = {
            "symbol": sym,
            "price": float(getattr(data, "price", 0) or 0),
            "volume": int(getattr(data, "volume", 0) or 0),
            "orderId": getattr(data, "order_id", "") or "",
            "matchType": getattr(data, "match_type", "") or "",
            "receivedAt": getattr(data, "receivedAt", None),
            "lastUpdate": datetime.now().isoformat(),
        }
        set_cache(f"stock:{sym}:trade_extra", payload, 2)
        push_to_list(f"trade_extra:{sym}", payload, max_len=100, ttl=300)
        publish_json(f"trade_extra:{sym}", payload)

    def _on_trade(self, data: Any) -> None:
        trade = self._map_trade(data)
        sym = trade.get("symbol")
        if not sym:
            return
        with self._lock:
            self._quotes[sym] = trade
            if sym not in self._trades:
                self._trades[sym] = []
            self._trades[sym].append(trade)
            if len(self._trades[sym]) > 100:
                self._trades[sym] = self._trades[sym][-100:]
        set_cache(f"stock:{sym}:quote", trade, 2)
        push_to_list(f"trade:{sym}", trade, max_len=100, ttl=300)
        publish_json(f"trade:{sym}", trade)
        self._maybe_broadcast_snapshot()

    def _publish_indices(self) -> None:
        with self._lock:
            indices = list(self._market_index.values())
        if indices:
            payload = {"indices": indices, "lastUpdate": datetime.now().isoformat()}
            set_cache("market:indices", payload, 3)
            publish_json("indices", payload)

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

    def _get_core_symbols(self) -> List[str]:
        """Fetch all stock symbols from DNSE REST API."""
        from dnse import DNSEClient
        client = DNSEClient(
            api_key=DNSE_API_KEY,
            api_secret=DNSE_API_SECRET,
            base_url=DNSE_BASE_URL,
        )
        core_symbols = []
        for market in ["STO", "HNX"]:
            page = 1
            while True:
                status, body = client.get_instruments(
                    symbol="",
                    market_id=market,
                    security_group_id="ST",
                    index_name="",
                    limit=100,
                    page=page,
                )
                if status != 200:
                    break
                parsed = json.loads(body) if isinstance(body, str) else body
                data_list = parsed if isinstance(parsed, list) else parsed.get("data", [])
                if not data_list:
                    break
                for item in data_list:
                    sym = item.get("symbol")
                    if sym:
                        core_symbols.append(sym)
                if len(data_list) < 100:
                    break
                page += 1
        print(f"[DNSE] Fetched {len(core_symbols)} symbols")
        return core_symbols

    def _run_ws_loop(self) -> None:
        from dnse.websocket.client import TradingClient
        symbols = list(self._subscribed) if self._subscribed else self._get_core_symbols()
        if not symbols:
            print("[DNSE Stream] No symbols to subscribe")
            return

        async def run_async():
            client = TradingClient(
                api_key=DNSE_API_KEY,
                api_secret=DNSE_API_SECRET,
                base_url=DNSE_WS_URL,
                encoding=ENCODING,
            )
            print("[DNSE] Connecting to WebSocket...")
            await client.connect()
            self._connected = True
            print(f"[DNSE] Connected! Session ID: {client._session_id}")

            print(f"[DNSE] Subscribing to {len(symbols)} symbols across all channels...")
            await client.subscribe_expected_price(
                symbols, on_expected_price=self._on_expected_price,
                encoding=ENCODING, board_id=BOARD_ID,
            )
            await client.subscribe_foreign_trading(
                symbols, on_trade=self._on_foreign_trading,
                encoding=ENCODING, board_id=BOARD_ID,
            )
            await client.subscribe_quotes(
                symbols, on_quote=self._on_quote, encoding=ENCODING, board_id=BOARD_ID
            )
            await client.subscribe_sec_def(
                symbols, on_sec_def=self._on_sec_def,
                encoding=ENCODING, board_id=BOARD_ID,
            )
            await client.subscribe_trade_extra(
                symbols, on_trade_extra=self._on_trade_extra,
                encoding=ENCODING, board_id=BOARD_ID,
            )
            await client.subscribe_trades(
                symbols, on_trade=self._on_trade, encoding=ENCODING, board_id=BOARD_ID
            )
            await client.subscribe_ohlc_closed(
                symbols, resolution="1", on_ohlc=self._on_ohlc_closed, encoding=ENCODING
            )
            await client.subscribe_ohlc(
                symbols, resolution="1", on_ohlc=self._on_ohlc, encoding=ENCODING
            )
            await client.subscribe_market_index(
                market_index="HNX", on_market_index=self._on_market_index, encoding=ENCODING
            )
            await client.subscribe_market_index(
                market_index="HOSE", on_market_index=self._on_market_index, encoding=ENCODING
            )
            print("[DNSE] All channels subscribed. Listening for data...")
            await asyncio.sleep(60 * 60 * 8)

        while self._running:
            try:
                asyncio.run(run_async())
            except Exception as e:
                print(f"[DNSE Stream] Error: {e}")
                self._connected = False
                if self._running:
                    import time
                    time.sleep(5)

    def _run_mock_loop(self) -> None:
        from app.services.market_data_service import MOCK_STOCKS
        while self._running:
            for row in MOCK_STOCKS:
                sym = row["symbol"]
                quote = {**row, "lastUpdate": datetime.now().isoformat()}
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