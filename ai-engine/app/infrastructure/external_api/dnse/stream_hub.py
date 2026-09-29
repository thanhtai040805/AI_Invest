"""
DNSE WebSocket market stream hub.

Runs TradingClient in a background thread, caches latest ticks,
and publishes to Redis for the Node.js Socket.IO relay.

Production features:
- MarketSessionManager: auto-connect/disconnect based on VN trading hours
- Coalesced market snapshots with per-trade events
- Liquidity + Heatmap real-time aggregation
- Dual-write: Pub/Sub (real-time) + Streams (durable replay)
"""

import asyncio
import json
import threading
import time
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional, Set

from app.infrastructure.external_api.dnse.websocket.client import TradingClient
from app.infrastructure.external_api.dnse.price_units import to_vnd_price
from app.config.settings import get_settings
from app.infrastructure.external_api.dnse.redis_pub import (
    publish_json,
    set_cache,
    push_to_list,
    get_list_range,
    add_to_sorted_set,
    get_sorted_set_range,
    add_to_stream,
)
from app.infrastructure.external_api.dnse.market_session import MarketSessionManager, MarketState, TZ_VN
from app.infrastructure.database.connection import get_raw_connection
from app.infrastructure.external_api.dnse.health import ChannelHealthTracker
from app.infrastructure.external_api.dnse.models import (
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
from app.infrastructure.external_api.sector_heatmap import build_sector_heatmap

DNSE_CONNECTION_LIMIT = 10
RESERVED_DNSE_CONNECTIONS = 2
MAX_UPSTREAM_CONNECTIONS = DNSE_CONNECTION_LIMIT - RESERVED_DNSE_CONNECTIONS
DNSE_STREAM_LIMIT = 200
MARKET_INDEXES = ("VNINDEX", "VN30", "VN100")
INDEX_STREAMS = len(MARKET_INDEXES) + 1  # includes Estimated VN30
OHLC_RESOLUTIONS = {"1", "3", "5", "15", "30", "1H", "1D", "1W"}
SUBSCRIBED_SYMBOLS_KEY = "socket:subscribed:symbol-counts:v2"
SUBSCRIBED_OHLC_KEY = "socket:subscribed:ohlc-counts:v1"


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
        self._estimated_vn30: Optional[Dict[str, Any]] = None
        self._foreign: Dict[str, Dict[str, Any]] = {}
        self._sec_def: Dict[str, Dict[str, Any]] = {}
        self._subscribed: Set[str] = set()
        self._active_symbols: Set[str] = set()
        self._requested_symbols: Dict[str, None] = {}
        self._requested_ohlc: Dict[str, Set[str]] = {}
        self._include_sec_def = False
        self._universe_symbols: Set[str] = set()
        self._stock_metadata: Dict[str, Dict[str, Any]] = {}
        self._market_baseline: Dict[str, Dict[str, Any]] = {}
        self._connection_subscriptions: List[Dict[str, Set[str]]] = []
        self._sent_subscriptions: List[Dict[str, Set[str]]] = []
        self._symbol_assignments: Dict[str, Dict[str, int]] = {}
        self._sent_assignments: Dict[str, Dict[str, int]] = {}
        self._observed_feeds: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._subscription_errors: List[Dict[str, Any]] = []
        self._pending_symbols: Set[str] = set()
        self._pending_streams: List[str] = []
        self._upstream_connections = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

        self._session_mgr = MarketSessionManager()
        self._health_tracker = ChannelHealthTracker(stale_threshold=30.0)
        self._market_flush_handle: Optional[asyncio.TimerHandle] = None
        self._total_trades_received = 0
        self._last_message_at: Optional[float] = None
        self._validation_rejects = 0

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
        with self._lock:
            requested = list(self._requested_symbols)
            active = set(self._active_symbols)
            universe_symbols = set(self._universe_symbols)
            universe_count = len(self._universe_symbols)
            shards = [
                {kind: set(symbols) for kind, symbols in shard.items()}
                for shard in self._connection_subscriptions
            ]
            pending = sorted(self._pending_symbols)
            pending_streams = list(self._pending_streams)
            sent = [{kind: set(symbols) for kind, symbols in shard.items()} for shard in self._sent_subscriptions]
            observed = {symbol for symbol, feeds in self._observed_feeds.items() if "trades" in feeds}
            subscription_errors = list(self._subscription_errors[-20:])
            planned_streams = sum(len(symbols) for shard in shards for symbols in shard.values()) + INDEX_STREAMS
            sent_streams = sum(len(symbols) for shard in sent for symbols in shard.values()) + (INDEX_STREAMS if self._connected else 0)
        return {
            "mode": self.mode,
            "running": self._running,
            "connected": self._connected,
            "subscribed_count": len(requested),
            "total_requested_symbols": len(set(requested) | universe_symbols),
            "active_symbols": len(active),
            "active_symbol_names": sorted(active),
            "observed_trade_symbols": len(observed & universe_symbols),
            "planned_streams": planned_streams,
            "sent_streams": sent_streams,
            "sent_trade_symbols": sum(len(shard.get("trades", set())) for shard in sent),
            "sent_foreign_symbols": sum(len(shard.get("foreign", set())) for shard in sent),
            "universe_symbols": universe_count,
            "pending_symbols": len(pending),
            "pending_symbol_names": pending,
            "pending_streams": pending_streams,
            "subscription_errors": subscription_errors,
            "upstream_connections": self._upstream_connections,
            "max_upstream_connections": MAX_UPSTREAM_CONNECTIONS,
            "reserved_connections": RESERVED_DNSE_CONNECTIONS,
            "stream_limit_per_connection": DNSE_STREAM_LIMIT,
            "connections": [
                {
                    "connection": index + 1,
                    "connected": index < self._upstream_connections,
                    "trade_symbols": sorted(shard.get("trades", set())),
                    "quote_symbols": sorted(shard.get("quotes", set())),
                    "foreign_symbols": sorted(shard.get("foreign", set())),
                    "security_definition_symbols": sorted(shard.get("sec_def", set())),
                    "ohlc_symbols": {
                        kind: sorted(symbols) for kind, symbols in shard.items()
                        if kind.startswith("ohlc:") or kind.startswith("ohlc_closed:")
                    },
                    "index_channels": [*MARKET_INDEXES, "VN30_ESTIMATED"] if index == 0 else [],
                    "stream_count": sum(len(symbols) for symbols in shard.values())
                    + (INDEX_STREAMS if index == 0 else 0),
                    "stream_limit": DNSE_STREAM_LIMIT,
                    "sent_stream_count": sum(len(symbols) for symbols in sent[index].values()) if index < len(sent) else 0,
                }
                for index, shard in enumerate(shards)
            ],
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
            stocks = [
                quote for symbol, quote in self._quotes.items()
                if symbol in self._universe_symbols
            ]
        return {"stocks": stocks, "total": len(stocks)}

    def get_market_board_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            stocks = [
                {**baseline, **self._quotes.get(symbol, {}), **self._market_foreign(symbol)}
                for symbol, baseline in self._market_baseline.items()
            ]
            live_symbols = sum(1 for symbol in self._market_baseline if self._quotes.get(symbol, {}).get("source") == "dnse-ws")
        return {"stocks": stocks, "total": len(stocks), "liveSymbols": live_symbols, "source": "mixed" if live_symbols else "postgres"}

    def get_assignment(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.strip().upper()
        with self._lock:
            if sym not in self._universe_symbols and sym not in MARKET_INDEXES:
                return {"symbol": sym, "found": False}
            feeds = dict(self._symbol_assignments.get(sym, {}))
            pending = [feed for feed in self._pending_streams if feed.endswith(f":{sym}")]
            sent_feeds = dict(self._sent_assignments.get(sym, {}))
            if sym in MARKET_INDEXES:
                feeds["market_index"] = 1
                if self._upstream_connections:
                    sent_feeds["market_index"] = 1
            observed = {
                kind: info for kind, info in self._observed_feeds.get(sym, {}).items()
                if sent_feeds.get(kind) == info["connection"]
            }
        return {"symbol": sym, "found": True, "plannedFeeds": feeds, "sentFeeds": sent_feeds, "observedFeeds": observed, "pending": pending}

    def _observe_feed(self, kind: str, data: Any, connection: int) -> None:
        symbol = str(getattr(data, "symbol", None) or getattr(data, "indexName", "") or "").upper()
        if not symbol:
            return
        if kind in {"ohlc", "ohlc_closed"}:
            kind = f"{kind}:{getattr(data, 'resolution', '')}"
        with self._lock:
            self._observed_feeds.setdefault(symbol, {})[kind] = {
                "connection": connection,
                "receivedAt": time.time(),
            }

    def _market_foreign(self, symbol: str) -> Dict[str, Any]:
        foreign = self._foreign.get(symbol)
        return {"foreign_flow": foreign["netValue"] / 1e9} if foreign else {}

    def _get_market_stocks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {**baseline, **self._quotes.get(symbol, {}), **self._market_foreign(symbol)}
                for symbol, baseline in self._market_baseline.items()
            ]

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
        with self._lock:
            for sym in symbols:
                normalized = sym.strip().upper()
                if normalized in self._universe_symbols:
                    self._requested_symbols.setdefault(normalized, None)
            self._refresh_symbols_locked()

    def subscribe_ohlc(self, symbols: List[str], resolution: str) -> None:
        if resolution not in OHLC_RESOLUTIONS:
            raise ValueError(f"Unsupported OHLC resolution: {resolution}")
        with self._lock:
            requested = self._requested_ohlc.setdefault(resolution, set())
            valid_symbols = self._universe_symbols | set(MARKET_INDEXES)
            requested.update(sym.strip().upper() for sym in symbols if sym.strip().upper() in valid_symbols)
            self._refresh_symbols_locked()

    def unsubscribe_ohlc(self, symbols: List[str], resolution: str) -> None:
        with self._lock:
            requested = self._requested_ohlc.get(resolution)
            if requested is not None:
                requested.difference_update(sym.strip().upper() for sym in symbols)
                if not requested:
                    del self._requested_ohlc[resolution]
            self._refresh_symbols_locked()

    def set_market_universe(self, stocks: List[Dict[str, Any]]) -> None:
        """Subscribe the database stock universe to trade ticks for market-wide data."""
        stocks = [stock for stock in stocks if str(stock.get("exchange") or "").upper() == "HOSE"]
        with self._lock:
            self._universe_symbols = {
                str(stock["symbol"]).strip().upper() for stock in stocks if stock.get("symbol")
            }
            self._stock_metadata = {
                str(stock["symbol"]).strip().upper(): {
                    "name": str(stock.get("name") or stock["symbol"]),
                    "exchange": str(stock.get("exchange") or ""),
                    "industry": str(stock.get("industry") or ""),
                    "sector": str(stock.get("sector") or stock.get("industry") or "Khác"),
                    "marketCap": float(stock.get("market_cap") or 0),
                    "refPrice": to_vnd_price(stock.get("ref_price")),
                    "ceiling": to_vnd_price(stock.get("ceiling")),
                    "floor": to_vnd_price(stock.get("floor")),
                }
                for stock in stocks if stock.get("symbol")
            }
            self._market_baseline = {}
            for stock in stocks:
                symbol = str(stock.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                open_price = float(stock.get("open") or 0)
                close_price = float(stock.get("close") or 0)
                reference_price = to_vnd_price(stock.get("ref_price")) or open_price * 1000
                price = close_price * 1000
                change_pct = ((price - reference_price) / reference_price * 100) if reference_price else 0
                self._market_baseline[symbol] = {
                    **self._stock_metadata[symbol],
                    "symbol": symbol,
                    "price": price,
                    "ref": reference_price,
                    "ceiling": to_vnd_price(stock.get("ceiling")),
                    "floor": to_vnd_price(stock.get("floor")),
                    "changePercent": change_pct,
                    "change_pct": change_pct,
                    "volume": int(stock.get("volume_total") or 0),
                    "tradingValue": 0,
                    "source": "postgres",
                    "stale": True,
                }
            self._refresh_symbols_locked()

    def unsubscribe_symbols(self, symbols: List[str]) -> None:
        with self._lock:
            for sym in symbols:
                self._requested_symbols.pop(sym.strip().upper(), None)
            self._refresh_symbols_locked()

    def _refresh_symbols_locked(self) -> None:
        self._subscribed = set(self._requested_symbols) & self._universe_symbols
        if self._include_sec_def:
            desired: Dict[str, Set[str]] = {"sec_def": set(self._universe_symbols)}
        else:
            desired = {
                "trades": set(self._universe_symbols),
                "foreign": set(self._universe_symbols),
            }
            for resolution, symbols in self._requested_ohlc.items():
                valid = symbols & (self._universe_symbols | set(MARKET_INDEXES))
                desired[f"ohlc:{resolution}"] = valid
                desired[f"ohlc_closed:{resolution}"] = valid
            desired["quotes"] = set(self._universe_symbols)

        quote_overflow: Set[str] = set()
        if "quotes" in desired:
            high_priority = INDEX_STREAMS + sum(len(symbols) for kind, symbols in desired.items() if kind != "quotes")
            quote_budget = max(0, MAX_UPSTREAM_CONNECTIONS * DNSE_STREAM_LIMIT - high_priority)
            if len(desired["quotes"]) > quote_budget:
                requested_first = sorted(desired["quotes"] & set(self._requested_symbols))
                others = sorted(desired["quotes"] - set(requested_first))
                kept = set((requested_first + others)[:quote_budget])
                quote_overflow = desired["quotes"] - kept
                desired["quotes"] = kept

        previous = self._connection_subscriptions
        shards = [{kind: set() for kind in desired} for _ in range(max(1, len(previous)))]
        loads = [INDEX_STREAMS] + [0] * (len(shards) - 1)
        for index, shard in enumerate(previous):
            for kind, symbols in shard.items():
                if kind not in desired:
                    continue
                kept = symbols & desired[kind]
                shards[index][kind].update(kept)
                loads[index] += len(kept)

        pending_streams = [f"quotes:{symbol}" for symbol in sorted(quote_overflow)]
        for kind in desired:
            assigned = {symbol for shard in shards for symbol in shard[kind]}
            for symbol in sorted(desired[kind] - assigned):
                candidates = sorted(range(len(shards)), key=lambda index: (loads[index], index))
                index = next((i for i in candidates if loads[i] < DNSE_STREAM_LIMIT), None)
                if index is None and len(shards) < MAX_UPSTREAM_CONNECTIONS:
                    shards.append({feed: set() for feed in desired})
                    loads.append(0)
                    index = len(shards) - 1
                if index is None:
                    pending_streams.append(f"{kind}:{symbol}")
                    continue
                shards[index][kind].add(symbol)
                loads[index] += 1

        while len(shards) > 1 and not any(shards[-1].values()):
            shards.pop()
        self._connection_subscriptions = shards
        self._symbol_assignments = {}
        for index, shard in enumerate(shards):
            for kind, symbols in shard.items():
                for symbol in symbols:
                    self._symbol_assignments.setdefault(symbol, {})[kind] = index + 1
        self._pending_streams = pending_streams
        self._pending_symbols = {item.split(":")[-1] for item in pending_streams}
        self._active_symbols = {symbol for shard in shards for symbol in shard.get("trades", set())}

    def _restore_subscriptions(self) -> None:
        """Restore the backend's shared subscription registry after a restart."""
        try:
            from app.infrastructure.external_api.dnse.redis_pub import get_redis

            counts = get_redis().hgetall(SUBSCRIBED_SYMBOLS_KEY)
            self.subscribe_symbols(
                [symbol for symbol, count in counts.items() if int(count) > 0]
            )
            ohlc_counts = get_redis().hgetall(SUBSCRIBED_OHLC_KEY)
            for key, count in ohlc_counts.items():
                if int(count) > 0 and ":" in key:
                    resolution, symbol = key.split(":", 1)
                    if resolution in OHLC_RESOLUTIONS:
                        self.subscribe_ohlc([symbol], resolution)
        except Exception as e:
            print(f"[DNSE Stream] Could not restore Redis subscriptions: {e}")

    def _load_market_universe(self) -> None:
        try:
            conn = get_raw_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT s.symbol, s.name, s.exchange, s.industry, s.sector, s.market_cap,
                               s.ref_price, s.ceiling, s.floor, latest.open, latest.close,
                               daily.volume_total
                        FROM stocks s
                        LEFT JOIN LATERAL (
                            SELECT open, close, date
                            FROM market_data_daily_calculation
                            WHERE ticker = s.symbol ORDER BY date DESC LIMIT 1
                        ) latest ON TRUE
                        LEFT JOIN market_data_daily daily
                          ON daily.ticker = s.symbol AND daily.date = latest.date
                        WHERE s.exchange = 'HOSE'
                        ORDER BY s.symbol
                    """)
                    columns = [column[0] for column in cur.description]
                    self.set_market_universe([dict(zip(columns, row)) for row in cur.fetchall()])
            finally:
                conn.close()
            print(f"[DNSE Stream] Loaded {len(self._universe_symbols)} HOSE symbols from stocks table")
        except Exception as e:
            print(f"[DNSE Stream] Could not load stock universe from database: {e}")

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        if self._settings.dnse_api_key and self._settings.dnse_api_secret:
            self._load_market_universe()
            self._restore_subscriptions()
            self._thread = threading.Thread(
                target=self._run_ws_loop, daemon=True, name="dnse-ws"
            )
            self._thread.start()
            print("[DNSE Stream] Starting live WebSocket hub with TradingClient...")

    def stop(self) -> None:
        self._running = False
        self._connected = False

    def _replay_missed_streams(self) -> int:
        """Restore the latest trade for this session as a stale seed."""
        try:
            from app.infrastructure.external_api.dnse.redis_pub import get_redis
            r = get_redis()
        except Exception:
            return 0

        total_replayed = 0
        try:
            for key in r.scan_iter("dnse:stream:trade:*", count=100):
                key_str = key.decode() if isinstance(key, bytes) else key
                entries = r.xrevrange(key_str, "+", "-", count=1)
                if not entries:
                    continue
                _, fields = entries[0]
                try:
                    data = json.loads(fields.get("data", "{}"))
                    trade_date = str(data.get("time") or data.get("lastUpdate") or "")[:10]
                    if trade_date != datetime.now(TZ_VN).date().isoformat():
                        continue
                    suffix = key_str.replace("dnse:stream:", "")
                    self._handle_replayed_message(suffix, data)
                    total_replayed += 1
                except Exception:
                    pass
        except Exception as e:
            print(f"[DNSE Stream] Stream replay error: {e}")

        if total_replayed > 0:
            print(f"[DNSE Stream] Restored {total_replayed} current-session trade seeds")
        return total_replayed

    def _handle_replayed_message(self, suffix: str, data: Dict[str, Any]) -> None:
        """Keep replayed prices visibly stale until a new DNSE tick arrives."""
        if suffix.startswith("trade:"):
            sym = suffix.replace("trade:", "").upper()
            with self._lock:
                if sym in self._universe_symbols:
                    self._quotes[sym] = {**data, "source": "dnse-replay", "stale": True}

    def _map_trade(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        metadata = self._stock_metadata.get(sym, {})
        price = to_vnd_price(getattr(data, "price", 0))
        volume = int(getattr(data, "totalVolumeTraded", 0) or 0)
        open_price = to_vnd_price(getattr(data, "openPrice", 0))
        prev_close = float(metadata.get("refPrice", 0) or 0) or open_price
        change = price - prev_close if prev_close else 0
        pct = change / prev_close * 100 if prev_close else 0
        return {
            "symbol": sym,
            "name": metadata.get("name", sym),
            "exchange": metadata.get("exchange", ""),
            "industry": metadata.get("industry", ""),
            "sector": metadata.get("sector", "Khác"),
            "price": price,
            "change": change,
            "changePercent": pct,
            "change_pct": pct,
            "ref": prev_close,
            "volume": volume,
            "tradingValueRaw": float(getattr(data, "grossTradeAmount", 0) or 0),
            "matchVolume": int(getattr(data, "quantity", 0) or 0),
            "time": getattr(data, "time", None),
            "open": open_price or price,
            "high": to_vnd_price(getattr(data, "highestPrice", price) or price),
            "low": to_vnd_price(getattr(data, "lowestPrice", price) or price),
            "prevClose": prev_close or price,
            "ceiling": metadata.get("ceiling", 0),
            "floor": metadata.get("floor", 0),
            "trend": "up" if pct > 0 else "down" if pct < 0 else "steady",
            "lastUpdate": getattr(data, "time", None) or datetime.now().astimezone().isoformat(),
            "receivedAt": getattr(data, "receivedAt", None),
            "source": "dnse-ws",
            "stale": False,
        }

    def _map_quote(self, data: Any, symbol: str) -> Dict[str, Any]:
        bids = [
            {"price": to_vnd_price(level.price), "volume": int(level.quantity)}
            for level in (getattr(data, "bid", None) or [])
        ]
        asks = [
            {"price": to_vnd_price(level.price), "volume": int(level.quantity)}
            for level in (getattr(data, "offer", None) or [])
        ]
        return {
            "symbol": symbol,
            "bids": bids,
            "asks": asks,
            "lastUpdate": getattr(data, "time", None) or datetime.now().astimezone().isoformat(),
        }

    def _map_ohlc(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        return {
            "symbol": sym,
            "open": to_vnd_price(getattr(data, "open", 0)),
            "high": to_vnd_price(getattr(data, "high", 0)),
            "low": to_vnd_price(getattr(data, "low", 0)),
            "close": to_vnd_price(getattr(data, "close", 0)),
            "volume": int(getattr(data, "volume", 0) or 0),
            "resolution": getattr(data, "resolution", "1"),
            "timestamp": getattr(data, "time", None),
            "lastUpdate": datetime.now().isoformat(),
        }

    def _map_market_index(self, data: Any) -> Dict[str, Any]:
        name = getattr(data, "indexName", "") or ""
        return {
            "name": name.upper(),
            "value": float(getattr(data, "valueIndexes", 0) or 0),
            "change": float(getattr(data, "changedValue", 0) or 0),
            "changePercent": float(getattr(data, "changedRatio", 0) or 0),
            "volume": int(getattr(data, "totalVolumeTraded", 0) or 0),
            "tradingValueRaw": float(getattr(data, "grossTradeAmount", 0) or 0),
            "lastUpdate": getattr(data, "transactTime", None)
            or getattr(data, "time", None)
            or datetime.now().isoformat(),
            "receivedAt": getattr(data, "receivedAt", None),
        }

    def _map_foreign(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        # ForeignInvestor fields are camelCase; map safely
        buy_vol = int(getattr(data, "buyVolume", 0) or 0)
        sell_vol = int(getattr(data, "sellVolume", 0) or 0)
        buy_val = float(getattr(data, "buyTradedAmount", 0) or 0)
        sell_val = float(getattr(data, "sellTradedAmount", 0) or 0)
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
            "lastUpdate": getattr(data, "transactTime", None) or datetime.now().isoformat(),
        }

    def _map_sec_def(self, data: Any) -> Dict[str, Any]:
        sym = str(getattr(data, "symbol", "") or "").upper()
        return {
            "symbol": sym,
            "name": self._stock_metadata.get(sym, {}).get("name", sym),
            "exchange": self._stock_metadata.get(sym, {}).get("exchange", "HOSE"),
            "ceiling": to_vnd_price(getattr(data, "ceilingPrice", 0)),
            "floor": to_vnd_price(getattr(data, "floorPrice", 0)),
            "prevClose": to_vnd_price(getattr(data, "basicPrice", 0)),
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
            "expectedPrice": to_vnd_price(getattr(data, "expected_price", 0)),
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
        self._queue_market_flush()

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
        set_cache(f"index:{name}", payload, 15)
        publish_json(f"index:{name}", payload)
        self._publish_indices()
        if name == "VNINDEX":
            self._publish_liquidity_from_index(payload)

    def _on_estimated_market_index(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("estimated_market_index")
        payload = self._map_market_index(data)
        name = payload.get("name", "")
        if name != "VN30":
            return
        payload["estimated"] = True
        payload["name"] = "VN30_ESTIMATED"
        if validate_payload(ValidatedMarketIndex, payload) is None:
            self._validation_rejects += 1
            return
        with self._lock:
            self._estimated_vn30 = payload
        set_cache("index:VN30_ESTIMATED", payload, 15)
        publish_json("index:VN30_ESTIMATED", payload)
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
            if sym in self._stock_metadata:
                metadata = self._stock_metadata[sym]
                metadata.update({key: value for key, value in (
                    ("refPrice", payload["prevClose"]), ("ceiling", payload["ceiling"]), ("floor", payload["floor"])
                ) if value > 0})
                baseline = self._market_baseline.get(sym)
                if baseline:
                    baseline.update({key: value for key, value in (
                        ("ref", payload["prevClose"]), ("ceiling", payload["ceiling"]), ("floor", payload["floor"])
                    ) if value > 0})
                    if payload["prevClose"]:
                        change = (baseline["price"] - payload["prevClose"]) / payload["prevClose"] * 100
                        baseline.update(changePct=change, changePercent=change)
        set_cache(f"stock:{sym}:sec_def", payload, 3600)
        publish_json(f"sec_def:{sym}", payload)
        self._queue_market_flush()

    def _on_trade_extra(self, data: Any) -> None:
        self._last_message_at = time.time()
        self._health_tracker.record_message("trade_extra")
        sym = str(getattr(data, "symbol", "").upper())
        if not sym:
            return
        payload = {
            "symbol": sym,
            "price": to_vnd_price(getattr(data, "price", 0)),
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
        self._queue_market_flush()

    def _queue_market_flush(self) -> None:
        if self._market_flush_handle is not None:
            return
        try:
            self._market_flush_handle = asyncio.get_running_loop().call_later(0.5, self._flush_market)
        except RuntimeError:
            self._flush_market()

    def _flush_market(self) -> None:
        self._market_flush_handle = None
        self._maybe_broadcast_snapshot()
        self._maybe_publish_heatmap()

    def _publish_indices(self) -> None:
        with self._lock:
            indices = list(self._market_index.values())
            if self._estimated_vn30:
                indices.append(self._estimated_vn30)
        if indices:
            payload = {"indices": indices, "lastUpdate": datetime.now().isoformat()}
            set_cache("market:indices", payload, 15)
            publish_json("indices", payload)

    def _maybe_broadcast_snapshot(self) -> None:
        snap = self.get_market_board_snapshot()
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

    def _publish_liquidity_from_index(self, index: Dict[str, Any]) -> None:
        stocks = self._get_market_stocks()
        payload = {
            "totalValueBillion": index.get("tradingValueRaw", 0),
            "stockCount": len(stocks),
            "topByVolume": sorted(stocks, key=lambda s: s.get("volume", 0), reverse=True)[:10],
            "lastUpdate": index["lastUpdate"],
            "source": "dnse-market-index",
        }
        set_cache("market:liquidity", payload, 5)
        publish_json("liquidity", payload)

    def _maybe_publish_heatmap(self) -> None:
        stocks = self._get_market_stocks()
        if not stocks:
            return
        sectors = build_sector_heatmap(stocks, include_live_count=True)
        payload = {"sectors": sectors, "lastUpdate": datetime.now().isoformat()}
        set_cache("market:heatmap", payload, 10)
        publish_json("heatmap", payload)

    def _run_ws_loop(self) -> None:
        def channel_for(kind: str) -> str:
            encoding = self._settings.encoding
            board = self._settings.board_id
            if kind == "trades":
                return f"tick.{board}.{encoding}"
            if kind == "quotes":
                return f"top_price.{board}.{encoding}"
            if kind == "foreign":
                return f"foreign.{board}.{encoding}"
            if kind == "sec_def":
                return f"security_definition.{board}.{encoding}"
            feed, resolution = kind.split(":", 1)
            return f"{feed}.{resolution}.{encoding}"

        async def run_async():
            clients: List[TradingClient] = []
            applied_by_connection: List[Dict[str, Set[str]]] = []
            with self._lock:
                self._estimated_vn30 = None
            replayed = self._replay_missed_streams()
            if replayed:
                print(f"[DNSE] Replayed {replayed} messages before connecting")

            try:
                while self._running:
                    if not self._session_mgr.is_connected():
                        state = self._session_mgr.get_market_state().value
                        print(f"[DNSE Stream] Market {state} — disconnecting until next session")
                        return

                    include_sec_def = datetime.now(TZ_VN).time() < dt_time(8, 30)
                    with self._lock:
                        if self._include_sec_def != include_sec_def:
                            self._include_sec_def = include_sec_def
                            self._refresh_symbols_locked()
                        target_shards = [
                            {kind: set(symbols) for kind, symbols in shard.items()}
                            for shard in self._connection_subscriptions
                        ]

                    # Keep one connection for index feeds; add symbol shards as demand grows.
                    needed_connections = max(1, len(target_shards))
                    while len(clients) < needed_connections:
                        index = len(clients)
                        client = TradingClient(
                            api_key=self._settings.dnse_api_key,
                            api_secret=self._settings.dnse_api_secret,
                            base_url=self._settings.dnse_ws_url,
                            encoding=self._settings.encoding,
                        )
                        print(f"[DNSE] Connecting upstream {index + 1}/{needed_connections}...")
                        try:
                            await client.connect()
                        except Exception:
                            await client.disconnect()
                            raise
                        clients.append(client)
                        applied_by_connection.append({})
                        def on_error(error, connection=index + 1):
                            detail = {"connection": connection, "error": str(error), "at": datetime.now().isoformat()}
                            with self._lock:
                                self._subscription_errors.append(detail)
                                self._subscription_errors = self._subscription_errors[-20:]
                            print(f"[DNSE] Upstream {connection}: {error}")
                        client.on("error", on_error)
                        for event, kind, handler in (
                            ("quote", "quotes", self._on_quote),
                            ("trade", "trades", self._on_trade),
                            ("foreign", "foreign", self._on_foreign_trading),
                            ("security_definition", "sec_def", self._on_sec_def),
                            ("ohlc", "ohlc", self._on_ohlc),
                            ("ohlc_closed", "ohlc_closed", self._on_ohlc_closed),
                        ):
                            def observed(data, kind=kind, handler=handler, connection=index + 1):
                                self._observe_feed(kind, data, connection)
                                handler(data)
                            client.on(event, observed)
                        if index == 0:
                            def observed_index(data):
                                self._observe_feed("market_index", data, 1)
                                self._on_market_index(data)
                            def observed_estimated(data):
                                self._observe_feed("estimated_market_index", data, 1)
                                self._on_estimated_market_index(data)
                            client.on("market_index", observed_index)
                            client.on("estimated_market_index", observed_estimated)
                            for market_index in MARKET_INDEXES:
                                await client.subscribe_market_index(
                                    market_index=market_index, encoding=self._settings.encoding
                                )
                            await client.subscribe_estimated_market_index(
                                "VN30", encoding=self._settings.encoding
                            )
                        self._upstream_connections = len(clients)
                        self._connected = True
                        await asyncio.sleep(0.2)

                    while len(clients) > needed_connections:
                        client = clients.pop()
                        applied_by_connection.pop()
                        await client.disconnect()
                        self._upstream_connections = len(clients)

                    for index, client in enumerate(clients):
                        target = target_shards[index] if index < len(target_shards) else {}
                        applied = applied_by_connection[index]
                        for kind in set(applied) | set(target):
                            removed = applied.get(kind, set()) - target.get(kind, set())
                            if removed:
                                await client.unsubscribe(channel_for(kind), sorted(removed))
                                applied[kind].difference_update(removed)

                        for kind, symbols in target.items():
                            added = symbols - applied.get(kind, set())
                            if not added:
                                continue
                            ordered = sorted(added)
                            if kind == "trades":
                                await client.subscribe_trades(ordered, encoding=self._settings.encoding, board_id=self._settings.board_id)
                            elif kind == "quotes":
                                await client.subscribe_quotes(ordered, encoding=self._settings.encoding, board_id=self._settings.board_id)
                            elif kind == "foreign":
                                await client.subscribe_foreign_trading(ordered, board_id=self._settings.board_id, encoding=self._settings.encoding)
                            elif kind == "sec_def":
                                await client.subscribe_sec_def(ordered, board_id=self._settings.board_id, encoding=self._settings.encoding)
                            elif kind.startswith("ohlc_closed:"):
                                await client.subscribe_ohlc_closed(ordered, resolution=kind.split(":", 1)[1], encoding=self._settings.encoding)
                            else:
                                await client.subscribe_ohlc(ordered, resolution=kind.split(":", 1)[1], encoding=self._settings.encoding)
                            applied.setdefault(kind, set()).update(added)
                            print(f"[DNSE] Upstream {index + 1}: subscribed {len(added)} {kind} symbols")

                    with self._lock:
                        self._sent_subscriptions = [
                            {kind: set(symbols) for kind, symbols in shard.items()}
                            for shard in applied_by_connection
                        ]
                        self._sent_assignments = {}
                        for connection, shard in enumerate(applied_by_connection, start=1):
                            for kind, symbols in shard.items():
                                for symbol in symbols:
                                    self._sent_assignments.setdefault(symbol, {})[kind] = connection

                    await asyncio.sleep(1)
            finally:
                self._connected = False
                with self._lock:
                    self._sent_subscriptions = []
                    self._sent_assignments = {}
                for client in reversed(clients):
                    try:
                        await client.disconnect()
                    except Exception as e:
                        print(f"[DNSE] Upstream disconnect failed: {e}")
                self._upstream_connections = 0

        retry_count = 0
        max_retries = 20
        base_delay = 1.0
        max_delay = 120.0
        loaded_session_date = None

        while self._running:
            market_state = self._session_mgr.get_market_state()
            if not self._session_mgr.is_connected():
                print(f"[DNSE Stream] Market {market_state.value} — waiting for trading hours...")
                _, wait_secs = self._session_mgr.next_state_change()
                time.sleep(min(wait_secs, 60))
                continue

            today = datetime.now(TZ_VN).date()
            if loaded_session_date != today:
                with self._lock:
                    self._quotes.clear()
                    self._foreign.clear()
                    self._sec_def.clear()
                    self._ohlc.clear()
                    self._market_index.clear()
                    self._observed_feeds.clear()
                    self._estimated_vn30 = None
                self._load_market_universe()
                loaded_session_date = today

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
