"""
DNSE WebSocket market stream hub.

Runs TradingClient in a background thread, caches latest ticks,
and publishes to Redis for the Node.js Socket.IO relay.

Production features:
- MarketSessionManager: auto-connect/disconnect based on VN trading hours
- RateLimitedPublisher: token bucket backpressure on Redis publish
- Liquidity + Heatmap real-time aggregation
- Dual-write: Pub/Sub (real-time) + Streams (durable replay)
"""

import asyncio
import json
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.services.dnse.api.client import DNSEClient
from app.services.dnse.websocket.client import TradingClient
from app.config.settings import get_settings
from app.services.dnse.redis_pub import (
    publish_json,
    set_cache,
    push_to_list,
    get_list_range,
    add_to_sorted_set,
    get_sorted_set_range,
    add_to_stream,
)
from app.services.dnse.market_session import MarketSessionManager, MarketState
from app.services.dnse.health import ChannelHealthTracker
from app.services.dnse.models import (
    ValidatedTrade,
    ValidatedTradeExtra,
    ValidatedOrderBook,
    ValidatedMarketIndex,
    ValidatedForeignTrading,
    ValidatedOhlc,
    ValidatedExpectedPrice,
    ValidatedSecurityDef,
    validate_payload,
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

        self._session_mgr = MarketSessionManager()
        self._health_tracker = ChannelHealthTracker(stale_threshold=30.0)
        self._last_liquidity_publish = 0.0
        self._last_heatmap_publish = 0.0
        self._liquidity_interval = 5.0
        self._heatmap_interval = 10.0
        self._total_trades_received = 0
        self._last_message_at: Optional[float] = None
        self._validation_rejects = 0
        self._stream_last_ids: Dict[str, str] = {}

    @property
    def mode(self) -> str:
        if self._settings.dnse_api_key and self._settings.dnse_api_secret:
            with self._lock:
                return "live" if self._connected else "connecting"
        return "mock"

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> Dict[str, Any]:
        now = time.time()
        time_since_message = (
            round(now - self._last_message_at, 1) if self._last_message_at else None
        )
        return {
            "mode": self.mode,
            "running": self._running,
            "connected": self._connected,
            "subscribed_count": len(self._subscribed),
            "cached_quotes": len(self._quotes),
            "market_state": self._session_mgr.get_market_state().value,
            "is_market_open": self._session_mgr.is_market_open(),
            "total_trades": self._total_trades_received,
            "last_message_at": self._last_message_at,
            "seconds_since_last_message": time_since_message,
            "receiving_data": time_since_message is not None and time_since_message < 30,
            "health": {
                "uptime_seconds": self._health_tracker.uptime_seconds,
                "total_messages": self._health_tracker.total_messages,
                "active_channels": self._health_tracker.active_channels,
                "is_receiving_data": self._health_tracker.is_receiving_data,
                "validation_rejects": self._validation_rejects,
                "channels": self._health_tracker.get_status(),
            },
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
        sym = symbol.upper()
        return get_list_range(f"trade:{sym}", 0, limit - 1)

    def get_trade_extra_history(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
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
        sym = symbol.upper()
        min_score = from_time if from_time else "-inf"
        max_score = to_time if to_time else "+inf"
        return get_sorted_set_range(f"ohlc_closed:{sym}:{resolution}", min_score, max_score)[-limit:]

    def get_ohlc_live(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            ohlc = self._ohlc.get(symbol.upper(), {})
            return ohlc if ohlc.get("type") == "live" else None

    def subscribe_symbols(self, symbols: List[str]) -> None:
        for sym in symbols:
            self._subscribed.add(sym.upper())

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        if self._settings.dnse_api_key and self._settings.dnse_api_secret:
            self._thread = threading.Thread(
                target=self._run_ws_loop, daemon=True, name="dnse-ws"
            )
            self._thread.start()
            print("[DNSE Stream] Starting live WebSocket hub with TradingClient...")

    def stop(self) -> None:
        self._running = False
        self._connected = False

    def _replay_missed_streams(self) -> int:
        """Replay missed messages from Redis Streams after reconnect."""
        try:
            from app.services.dnse.redis_pub import get_redis
            r = get_redis()
        except Exception:
            return 0

        total_replayed = 0
        try:
            for key in r.scan_iter("dnse:stream:*", count=100):
                key_str = key.decode() if isinstance(key, bytes) else key
                last_id = self._stream_last_ids.get(key_str, "0")
                entries = r.xrange(key_str, last_id, "+", count=50)
                for entry_id, fields in entries:
                    try:
                        data = json.loads(fields.get("data", "{}"))
                        suffix = key_str.replace("dnse:stream:", "")
                        self._handle_replayed_message(suffix, data)
                        total_replayed += 1
                    except Exception:
                        pass
                    self._stream_last_ids[key_str] = entry_id
        except Exception as e:
            print(f"[DNSE Stream] Stream replay error: {e}")

        if total_replayed > 0:
            print(f"[DNSE Stream] Replayed {total_replayed} missed messages from Redis Streams")
        return total_replayed

    def _handle_replayed_message(self, suffix: str, data: Dict[str, Any]) -> None:
        """Process a replayed stream message through the appropriate handler."""
        if suffix.startswith("trade:"):
            sym = suffix.replace("trade:", "").upper()
            self._quotes[sym] = data
        elif suffix.startswith("ohlc_closed:"):
            sym = suffix.replace("ohlc_closed:", "").upper()
            self._ohlc[sym] = {**data, "type": "closed"}

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
        # ForeignInvestor fields are camelCase; map safely
        buy_vol = int(getattr(data, "buyVolume", 0) or 0)
        sell_vol = int(getattr(data, "sellVolume", 0) or 0)
        buy_val = float(getattr(data, "buyValue", 0) or 0)
        sell_val = float(getattr(data, "sellValue", 0) or 0)
        room_limit = int(getattr(data, "foreignerOrderLimitQuantity", 0) or 0)
        room_remaining = int(getattr(data, "foreignerBuyPossibleQuantity", 0) or 0)
        return {
            "symbol": sym,
            "buyVolume": buy_vol,
            "sellVolume": sell_vol,
            "netVolume": buy_vol - sell_vol,
            "buyValue": buy_val,
            "sellValue": sell_val,
            "netValue": buy_val - sell_val,
            "roomLimit": room_limit,
            "roomRemaining": room_remaining,
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
        self._last_message_at = time.time()
        self._health_tracker.record_message("expected_price")
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
        validated = validate_payload(ValidatedExpectedPrice, payload)
        if validated is None:
            self._validation_rejects += 1
            return
        set_cache(f"stock:{sym}:expected_price", payload, 2)
        publish_json(f"expected_price:{sym}", payload)

    def _on_foreign_trading(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("foreign")
        payload = self._map_foreign(data)
        sym = payload.get("symbol")
        if not sym:
            return
        validated = validate_payload(ValidatedForeignTrading, payload)
        if validated is None:
            self._validation_rejects += 1
            return
        with self._lock:
            self._foreign[sym] = payload
        set_cache(f"stock:{sym}:foreign", payload, 5)
        publish_json(f"foreign:{sym}", payload)

    def _on_market_index(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("market_index")
        payload = self._map_market_index(data)
        name = payload.get("name", "")
        if not name:
            return
        validated = validate_payload(ValidatedMarketIndex, payload)
        if validated is None:
            self._validation_rejects += 1
            return
        with self._lock:
            self._market_index[name] = payload
        set_cache(f"index:{name}", payload, 3)
        publish_json(f"index:{name}", payload)
        self._publish_indices()

    def _on_ohlc_closed(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("ohlc_closed")
        payload = self._map_ohlc(data)
        sym = payload.get("symbol")
        if not sym:
            return
        validated = validate_payload(ValidatedOhlc, payload)
        if validated is None:
            self._validation_rejects += 1
            return
        timestamp = payload.get("timestamp") or int(datetime.now().timestamp())
        resolution = payload.get("resolution", "1")
        with self._lock:
            self._ohlc[sym] = {**payload, "type": "closed"}
        set_cache(f"stock:{sym}:ohlc_closed", payload, 10)
        ohlc_key = f"ohlc_closed:{sym}:{resolution}"
        add_to_sorted_set(ohlc_key, timestamp, payload, ttl=86400)
        publish_json(f"ohlc_closed:{sym}", payload)
        add_to_stream(f"dnse:stream:ohlc_closed:{sym}", {
            "symbol": sym,
            "data": json.dumps(payload, default=str),
            "ts": str(timestamp),
        })

    def _on_ohlc(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("ohlc_live")
        payload = self._map_ohlc(data)
        sym = payload.get("symbol")
        if not sym:
            return
        validated = validate_payload(ValidatedOhlc, payload)
        if validated is None:
            self._validation_rejects += 1
            return
        with self._lock:
            self._ohlc[sym] = {**payload, "type": "live"}
        set_cache(f"stock:{sym}:ohlc", payload, 2)
        publish_json(f"ohlc:{sym}", payload)

    def _on_quote(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("orderbook")
        sym = str(getattr(data, "symbol", "") or "").upper()
        if not sym:
            return
        book = self._map_quote(data, sym)
        validated = validate_payload(ValidatedOrderBook, book)
        if validated is None:
            self._validation_rejects += 1
            return
        with self._lock:
            self._orderbooks[sym] = book
        set_cache(f"stock:{sym}:orderbook", book, 2)
        publish_json(f"orderbook:{sym}", book)

    def _on_sec_def(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("sec_def")
        payload = self._map_sec_def(data)
        sym = payload.get("symbol")
        if not sym:
            return
        validated = validate_payload(ValidatedSecurityDef, payload)
        if validated is None:
            self._validation_rejects += 1
            return
        with self._lock:
            self._sec_def[sym] = payload
        set_cache(f"stock:{sym}:sec_def", payload, 3600)
        publish_json(f"sec_def:{sym}", payload)

    def _on_trade_extra(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("trade_extra")
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
        validated = validate_payload(ValidatedTradeExtra, payload)
        if validated is None:
            self._validation_rejects += 1
            return
        set_cache(f"stock:{sym}:trade_extra", payload, 2)
        push_to_list(f"trade_extra:{sym}", payload, max_len=100, ttl=300)
        publish_json(f"trade_extra:{sym}", payload)

    def _on_trade(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("trade")
        self._total_trades_received += 1
        trade = self._map_trade(data)
        sym = trade.get("symbol")
        if not sym:
            return
        validated = validate_payload(ValidatedTrade, trade)
        if validated is None:
            self._validation_rejects += 1
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
        add_to_stream(f"dnse:stream:trade:{sym}", {
            "symbol": sym,
            "data": json.dumps(trade, default=str),
        })
        self._maybe_broadcast_snapshot()
        self._maybe_publish_liquidity()
        self._maybe_publish_heatmap()

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

    def _maybe_publish_liquidity(self) -> None:
        now = time.time()
        if now - self._last_liquidity_publish < self._liquidity_interval:
            return
        self._last_liquidity_publish = now
        with self._lock:
            stocks = list(self._quotes.values())
        if not stocks:
            return
        total_value = sum(s.get("tradingValue", 0) for s in stocks) / 1e9
        payload = {
            "totalValueBillion": round(total_value, 2),
            "stockCount": len(stocks),
            "topByVolume": sorted(stocks, key=lambda s: s.get("volume", 0), reverse=True)[:10],
            "lastUpdate": datetime.now().isoformat(),
        }
        set_cache("market:liquidity", payload, 5)
        publish_json("liquidity", payload)

    def _maybe_publish_heatmap(self) -> None:
        now = time.time()
        if now - self._last_heatmap_publish < self._heatmap_interval:
            return
        self._last_heatmap_publish = now
        with self._lock:
            stocks = list(self._quotes.values())
        if not stocks:
            return
        sector_map: Dict[str, Dict] = {}
        for s in stocks:
            name = s.get("industry", s.get("sector", "Khác"))
            if name not in sector_map:
                sector_map[name] = {"name": name, "change": 0.0, "count": 0, "totalVol": 0}
            sector_map[name]["change"] += s.get("changePercent", 0)
            sector_map[name]["count"] += 1
            sector_map[name]["totalVol"] += s.get("volume", 0)
        sectors = []
        for n, d in sector_map.items():
            avg_change = round(d["change"] / max(d["count"], 1), 2)
            sectors.append({
                "name": n,
                "change": avg_change,
                "weight": d["count"],
                "totalVolume": d["totalVol"],
                "color": "bg-secondary" if avg_change >= 0 else "bg-error",
            })
        payload = {"sectors": sectors, "lastUpdate": datetime.now().isoformat()}
        set_cache("market:heatmap", payload, 10)
        publish_json("heatmap", payload)

    def _get_core_symbols(self) -> List[str]:
        client = DNSEClient(
            api_key=self._settings.dnse_api_key,
            api_secret=self._settings.dnse_api_secret,
            base_url=self._settings.dnse_base_url,
        )
        core_symbols = []
        for market in ["STO"]:
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
        symbols = list(self._subscribed) if self._subscribed else self._get_core_symbols()
        if not symbols:
            print("[DNSE Stream] No symbols to subscribe")
            return

        async def run_async():
            client = TradingClient(
                api_key=self._settings.dnse_api_key,
                api_secret=self._settings.dnse_api_secret,
                base_url=self._settings.dnse_ws_url,
                encoding=self._settings.encoding,
            )
            print("[DNSE] Connecting to WebSocket...")
            await client.connect()
            self._connected = True
            print(f"[DNSE] Connected! Session ID: {client._session_id}")

            # Replay missed messages from Redis Streams
            replayed = self._replay_missed_streams()

            print(f"[DNSE] Subscribing to {len(symbols)} symbols across all channels...")
            await client.subscribe_expected_price(
                symbols, on_expected_price=self._on_expected_price,
                encoding=self._settings.encoding, board_id=self._settings.board_id,
            )
            await client.subscribe_foreign_trading(
                symbols, on_trade=self._on_foreign_trading,
                encoding=self._settings.encoding, board_id=self._settings.board_id,
            )
            await client.subscribe_quotes(
                symbols, on_quote=self._on_quote, encoding=self._settings.encoding, board_id=self._settings.board_id
            )
            await client.subscribe_sec_def(
                symbols, on_sec_def=self._on_sec_def,
                encoding=self._settings.encoding, board_id=self._settings.board_id,
            )
            await client.subscribe_trade_extra(
                symbols, on_trade_extra=self._on_trade_extra,
                encoding=self._settings.encoding, board_id=self._settings.board_id,
            )
            await client.subscribe_trades(
                symbols, on_trade=self._on_trade, encoding=self._settings.encoding, board_id=self._settings.board_id
            )
            await client.subscribe_ohlc_closed(
                symbols, resolution="1", on_ohlc=self._on_ohlc_closed, encoding=self._settings.encoding
            )
            await client.subscribe_ohlc_closed(
                symbols, resolution="1D", on_ohlc=self._on_ohlc_closed, encoding=self._settings.encoding
            )
            await client.subscribe_ohlc(
                symbols, resolution="1", on_ohlc=self._on_ohlc, encoding=self._settings.encoding
            )
            await client.subscribe_ohlc(
                symbols, resolution="1D", on_ohlc=self._on_ohlc, encoding=self._settings.encoding
            )
            await client.subscribe_market_index(
                market_index="HNX", on_market_index=self._on_market_index, encoding=self._settings.encoding
            )
            await client.subscribe_market_index(
                market_index="HOSE", on_market_index=self._on_market_index, encoding=self._settings.encoding
            )
            print("[DNSE] All channels subscribed. Listening for data...")

            # Check market state every 60s instead of sleeping 8h blindly
            while self._running:
                if not self._session_mgr.is_market_open():
                    state = self._session_mgr.get_market_state().value
                    print(f"[DNSE Stream] Market {state} — disconnecting until next session")
                    await client.disconnect()
                    self._connected = False
                    return
                await asyncio.sleep(60)

        retry_count = 0
        max_retries = 20
        base_delay = 1.0
        max_delay = 120.0

        while self._running:
            market_state = self._session_mgr.get_market_state()
            if not self._session_mgr.is_connected():
                print(f"[DNSE Stream] Market {market_state.value} — waiting for trading hours...")
                _, wait_secs = self._session_mgr.next_state_change()
                time.sleep(min(wait_secs, 60))
                continue

            try:
                asyncio.run(run_async())
                retry_count = 0
            except Exception as e:
                retry_count += 1
                print(f"[DNSE Stream] Error (attempt {retry_count}/{max_retries}): {e}")
                self._connected = False

                if retry_count >= max_retries:
                    print("[DNSE Stream] Max retries reached. Waiting for next trading session...")
                    retry_count = 0
                    _, wait_secs = self._session_mgr.next_state_change()
                    time.sleep(min(wait_secs, 300))
                else:
                    delay = min(base_delay * (2 ** (retry_count - 1)), max_delay)
                    print(f"[DNSE Stream] Reconnecting in {delay:.0f}s...")
                    time.sleep(delay)


_hub: Optional[DnseStreamHub] = None


def get_stream_hub() -> DnseStreamHub:
    global _hub
    if _hub is None:
        _hub = DnseStreamHub()
    return _hub
