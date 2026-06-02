"""
Unified market data facade — DNSE WebSocket hub (primary) + PostgreSQL + REST fallback.

NO mock data merged with live data. Mock mode is separate (DNSE_ENABLED=false).
Data sources: PostgreSQL (historical daily) → Redis (recent 1-min) → in-memory hub (live) → DNSE REST API.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client
from app.services.dnse.redis_pub import (
    get_redis,
    get_list_range,
    get_sorted_set_range,
)

_PG_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")


def _query_pg_ohlcv(symbol: str, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict]:
    """Query daily OHLCV from PostgreSQL."""
    try:
        import psycopg2
        conn = psycopg2.connect(_PG_URL)
        cur = conn.cursor()
        where = "symbol = %s"
        params: list = [symbol]
        if start:
            where += " AND time >= %s::timestamptz"
            params.append(start)
        if end:
            where += " AND time <= %s::timestamptz"
            params.append(end)
        cur.execute(
            f"SELECT time, open, high, low, close, volume FROM ohlcv WHERE {where} ORDER BY time",
            params,
        )
        rows = []
        for r in cur.fetchall():
            ts = r[0]
            if isinstance(ts, datetime):
                ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S%z")
            else:
                ts_str = str(ts)[:10]
            rows.append({
                "time": ts_str,
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": int(r[5]),
            })
        cur.close()
        conn.close()
        return rows
    except Exception:
        return []


class MarketDataService:
    def __init__(self) -> None:
        self._hub = get_stream_hub()
        self._rest = get_rest_client()

    async def get_indices(self) -> Dict:
        """Return live indices from hub or Redis. Fallback to REST."""
        try:
            r = get_redis()
            cached = r.get("market:indices")
            if cached:
                import json
                return {**json.loads(cached), "source": "redis"}
        except Exception:
            pass

        with self._hub._lock:
            indices = list(self._hub._market_index.values())

        if indices:
            return {"indices": indices, "source": "dnse-ws"}

        if self._rest.is_live:
            try:
                rest_indices = self._rest.get_market_indices()
                if rest_indices:
                    return {"indices": rest_indices, "source": "dnse-rest"}
            except Exception:
                pass

        return {"indices": [], "source": "empty"}

    async def get_breadth(self) -> Dict:
        """Calculate breadth from live snapshot."""
        try:
            r = get_redis()
            cached = r.get("market:breadth")
            if cached:
                import json
                return {**json.loads(cached), "source": "redis"}
        except Exception:
            pass

        snap = await self.get_snapshot()
        stocks = snap.get("stocks", [])
        adv = sum(1 for s in stocks if s.get("changePercent", 0) > 0)
        dec = sum(1 for s in stocks if s.get("changePercent", 0) < 0)
        return {
            "advancers": adv,
            "decliners": dec,
            "unchanged": len(stocks) - adv - dec,
            "lastUpdate": datetime.now().isoformat(),
            "source": snap.get("source", "computed"),
        }

    async def get_snapshot(self, exchange: Optional[str] = None) -> Dict:
        """Return ALL stocks from DNSE hub.
        
        Strategy:
        1. Get ALL symbols from DNSE REST (if not cached)
        2. Merge with live data from Redis/hub
        3. Filter by exchange if requested
        """
        import json

        live_stocks: Dict[str, Dict] = {}

        # 1. Try Redis snapshot cache first (fastest)
        try:
            r = get_redis()
            cached = r.get("market:snapshot")
            if cached:
                snap = json.loads(cached)
                for s in snap.get("stocks", []):
                    if s.get("symbol"):
                        live_stocks[s["symbol"]] = s
        except Exception:
            pass

        # 2. Enrich with live hub data (most recent ticks)
        with self._hub._lock:
            hub_quotes = dict(self._hub._quotes)
            hub_sec_def = dict(self._hub._sec_def)

        for sym, quote in hub_quotes.items():
            if sym in live_stocks:
                live_stocks[sym] = {**live_stocks[sym], **quote}
            else:
                live_stocks[sym] = quote

        # 3. If still no data, fetch symbol list from REST and merge with hub
        if not live_stocks:
            all_symbols = self._hub._subscribed if self._hub._subscribed else self._hub._get_core_symbols()
            for sym in all_symbols:
                sym = sym.upper()
                quote = hub_quotes.get(sym)
                sec_def = hub_sec_def.get(sym)
                merged = {}
                if sec_def:
                    merged.update(sec_def)
                if quote:
                    merged.update(quote)
                if merged:
                    live_stocks[sym] = merged

        stocks = list(live_stocks.values())

        if exchange:
            stocks = [s for s in stocks if s.get("exchange", "HOSE").upper() == exchange.upper()]

        return {
            "stocks": stocks,
            "total": len(stocks),
            "source": "dnse-ws" if live_stocks else "empty",
        }

    async def get_stock_list(self, exchange: Optional[str] = None) -> Dict:
        return await self.get_snapshot(exchange)

    async def search(self, query: str) -> List:
        q = query.strip().upper()
        snap = await self.get_snapshot()
        return [
            {"symbol": s["symbol"], "name": s.get("name", s["symbol"]), "exchange": s.get("exchange", "HOSE")}
            for s in snap.get("stocks", [])
            if q in s["symbol"].upper() or q in str(s.get("name", "")).upper()
        ][:20]

    async def get_profile(self, symbol: str) -> Dict:
        sym = symbol.upper()
        quote = self._hub.get_quote(sym)
        if quote:
            return {"symbol": sym, "name": quote.get("name", sym), "exchange": quote.get("exchange", "HOSE")}

        try:
            r = get_redis()
            cached = r.get(f"stock:{sym}:sec_def")
            if cached:
                import json
                return json.loads(cached)
        except Exception:
            pass

        if self._rest.is_live:
            try:
                return self._rest.get_security_info(sym)
            except Exception:
                pass

        return {"symbol": sym, "name": sym}

    async def get_ohlcv(self, symbol: str, interval: str = "1D", start: Optional[str] = None, end: Optional[str] = None) -> Dict:
        import logging
        logger = logging.getLogger("ai_engine.market_data")
        
        sym = symbol.upper()
        
        RESOLUTION_MAP = {
            "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
            "1H": "1H", "1h": "1H",
            "1D": "1D", "1d": "1D", "1W": "1W", "1w": "1W",
            "1": "1", "3": "3", "5": "5", "15": "15", "30": "30",
        }
        resolution = RESOLUTION_MAP.get(interval, "1")
        
        # For intraday resolutions (not daily/weekly): try REST API directly first
        if resolution not in ("1D", "1W"):
            rest_data = await self._fetch_rest_ohlcv(sym, interval, start, end, logger)
            if rest_data and rest_data.get("data"):
                return rest_data
            # Fall through to Redis/WS for 1-min if REST failed

        # 1. For 1-minute interval: read from Redis 1-min sorted set + live candle
        if resolution == "1":
            key = f"ohlc_closed:{sym}:1"
            try:
                hist = get_sorted_set_range(key)
                if hist:
                    logger.info(f"OHLCV {sym} {interval}: got {len(hist)} 1-min candles from Redis")
            except Exception as e:
                logger.warning(f"OHLCV {sym} {interval}: Redis error: {e}")
                hist = []

            # Append live candle
            try:
                live = self._hub.get_ohlc_live(sym)
                if live and live.get("resolution") == "1":
                    live_candle = {
                        "time": live.get("timestamp") or live.get("lastUpdate"),
                        "open": float(live.get("open", 0) or 0),
                        "high": float(live.get("high", 0) or 0),
                        "low": float(live.get("low", 0) or 0),
                        "close": float(live.get("close", 0) or 0),
                        "volume": int(live.get("volume", 0) or 0),
                    }
                    if hist:
                        last_ts = hist[-1].get("timestamp") or hist[-1].get("lastUpdate", "")
                        live_ts = live.get("timestamp") or live.get("lastUpdate", "")
                        if last_ts == live_ts:
                            hist[-1] = live_candle
                        else:
                            hist.append(live_candle)
                    else:
                        hist.append(live_candle)
            except Exception as e:
                logger.warning(f"OHLCV {sym} {interval}: live candle error: {e}")

            if hist:
                data = [{"time": pt.get("timestamp") or pt.get("lastUpdate"), "open": pt.get("open", 0), "high": pt.get("high", 0), "low": pt.get("low", 0), "close": pt.get("close", 0), "volume": pt.get("volume", 0)} for pt in hist]
                return {"symbol": sym, "interval": interval, "data": data, "source": "dnse-ws"}

            # Fallback to REST for 1-min
            rest_data = await self._fetch_rest_ohlcv(sym, interval, start, end, logger)
            if rest_data:
                return rest_data
            return {"symbol": sym, "interval": interval, "data": [], "source": "empty"}

        # 2. For daily interval: PostgreSQL (historical) + Redis 1-min (today's live)
        if resolution == "1D":
            today_str = datetime.now().strftime("%Y-%m-%d")
            historical_data: List[Dict] = []
            today_candle: Optional[Dict] = None

            # 2a. Get historical daily candles from PostgreSQL (faster + persistent)
            try:
                pg_rows = _query_pg_ohlcv(sym, start, end)
                if pg_rows:
                    historical_data = pg_rows
                    logger.info(f"OHLCV {sym} {interval}: got {len(historical_data)} candles from PostgreSQL")
            except Exception as e:
                logger.warning(f"OHLCV {sym} {interval}: PostgreSQL error: {e}")

            # 2b. Fallback to REST if PostgreSQL is empty
            if not historical_data:
                rest_data = await self._fetch_rest_ohlcv(sym, interval, start, end, logger)
                if rest_data:
                    historical_data = rest_data.get("data", [])
                    logger.info(f"OHLCV {sym} {interval}: got {len(historical_data)} candles from REST fallback")

            # 2c. Get today's candle from Redis 1-min aggregation (overrides PostgreSQL today)
            try:
                min_key = f"ohlc_closed:{sym}:1"
                min_hist = get_sorted_set_range(min_key)
                if min_hist:
                    live = self._hub.get_ohlc_live(sym)
                    if live and live.get("resolution") == "1":
                        live_candle = {
                            "time": live.get("timestamp") or live.get("lastUpdate"),
                            "open": float(live.get("open", 0) or 0),
                            "high": float(live.get("high", 0) or 0),
                            "low": float(live.get("low", 0) or 0),
                            "close": float(live.get("close", 0) or 0),
                            "volume": int(live.get("volume", 0) or 0),
                        }
                        last_ts = min_hist[-1].get("timestamp") or min_hist[-1].get("lastUpdate", "")
                        live_ts = live.get("timestamp") or live.get("lastUpdate", "")
                        if last_ts != live_ts:
                            min_hist.append(live_candle)
                        else:
                            min_hist[-1] = live_candle

                    daily = self._aggregate_to_daily(min_hist)
                    for d in daily:
                        d_time = d.get("time", "")
                        d_date = d_time[:10] if "T" in d_time else d_time[:10]
                        if d_date == today_str:
                            today_candle = d
                            break
                    if today_candle:
                        logger.info(f"OHLCV {sym} {interval}: got today's candle from Redis 1-min aggregation")
            except Exception as e:
                logger.warning(f"OHLCV {sym} {interval}: aggregation error: {e}")

            # 2d. Merge: PostgreSQL historical + today's Redis candle (replace if same day)
            merged = list(historical_data)
            today_replaced = False
            for i, h in enumerate(merged):
                h_time = h.get("time", "")
                h_date = h_time[:10] if "T" in h_time else h_time[:10]
                if h_date == today_str and today_candle:
                    merged[i] = today_candle
                    today_replaced = True
                    break
            if today_candle and not today_replaced:
                merged.append(today_candle)

            if merged:
                merged.sort(key=lambda x: x.get("time", ""))
                return {"symbol": sym, "interval": interval, "data": merged, "source": "pg+redis"}

            # 2e. Final fallback: Redis closed 1D candles
            try:
                key_1d = f"ohlc_closed:{sym}:1D"
                hist_1d = get_sorted_set_range(key_1d)
                if hist_1d:
                    data = [{"time": pt.get("timestamp") or pt.get("lastUpdate"), "open": pt.get("open", 0), "high": pt.get("high", 0), "low": pt.get("low", 0), "close": pt.get("close", 0), "volume": pt.get("volume", 0)} for pt in hist_1d]
                    return {"symbol": sym, "interval": interval, "data": data, "source": "dnse-ws-1d"}
            except Exception:
                pass

            return {"symbol": sym, "interval": interval, "data": [], "source": "empty"}

        # 3. Other intervals: fallback to REST
        rest_data = await self._fetch_rest_ohlcv(sym, interval, start, end, logger)
        if rest_data:
            return rest_data
        return {"symbol": sym, "interval": interval, "data": [], "source": "empty"}

    async def _fetch_rest_ohlcv(self, symbol: str, interval: str, start: Optional[str], end: Optional[str], logger: Any) -> Optional[Dict]:
        """Fetch OHLCV from DNSE REST API or public fallback."""
        if self._rest.is_live:
            try:
                logger.info(f"OHLCV {symbol} {interval}: fetching from DNSE REST")
                rest_ohlcv = self._rest.get_ohlcv(symbol, interval, start, end)
                if rest_ohlcv:
                    return {"symbol": symbol, "interval": interval, "data": rest_ohlcv, "source": "dnse-rest"}
            except Exception as e:
                logger.warning(f"OHLCV {symbol} {interval}: DNSE REST error: {e}")
        else:
            try:
                rest_ohlcv = self._rest.get_ohlcv(symbol, interval, start, end)
                if rest_ohlcv:
                    return {"symbol": symbol, "interval": interval, "data": rest_ohlcv, "source": "dnse-public"}
            except Exception as e:
                logger.warning(f"OHLCV {symbol} {interval}: public API error: {e}")
        return None

    def _aggregate_to_daily(self, minute_candles: List[Dict]) -> List[Dict]:
        """Aggregate 1-minute candles into daily candles."""
        from collections import defaultdict
        daily: Dict[str, Dict] = {}
        
        for pt in minute_candles:
            ts = pt.get("timestamp") or pt.get("lastUpdate", "")
            if not ts:
                continue
            # Extract date part
            if "T" in ts:
                date_str = ts.split("T")[0]
            elif len(ts) >= 10:
                date_str = ts[:10]
            else:
                continue
            
            o = float(pt.get("open", 0) or 0)
            h = float(pt.get("high", 0) or 0)
            l = float(pt.get("low", 0) or 0)
            c = float(pt.get("close", 0) or 0)
            v = int(pt.get("volume", 0) or 0)
            
            if date_str not in daily:
                daily[date_str] = {
                    "time": ts,
                    "open": o,
                    "high": h,
                    "low": l if l > 0 else o,
                    "close": c,
                    "volume": v,
                }
            else:
                d = daily[date_str]
                d["high"] = max(d["high"], h)
                if l > 0:
                    d["low"] = min(d["low"], l)
                d["close"] = c
                d["volume"] += v
        
        return sorted(daily.values(), key=lambda x: x["time"])

    async def get_quote(self, symbol: str) -> Dict:
        sym = symbol.upper()
        self._hub.subscribe_symbols([sym])

        try:
            r = get_redis()
            cached = r.get(f"stock:{sym}:quote")
            if cached:
                import json
                return json.loads(cached)
        except Exception:
            pass

        cached = self._hub.get_quote(sym)
        if cached:
            return cached

        if self._rest.is_live:
            try:
                return self._rest.get_security_info(sym)
            except Exception:
                pass

        return {"symbol": sym, "price": 0}

    async def get_order_book(self, symbol: str) -> Dict:
        sym = symbol.upper()
        self._hub.subscribe_symbols([sym])

        cached = self._hub.get_orderbook(sym)
        if cached:
            return cached

        try:
            r = get_redis()
            ob_cached = r.get(f"stock:{sym}:orderbook")
            if ob_cached:
                import json
                return json.loads(ob_cached)
        except Exception:
            pass

        return {"symbol": sym, "bids": [], "asks": [], "lastUpdate": datetime.now().isoformat()}

    async def get_trades(self, symbol: str) -> Dict:
        sym = symbol.upper()
        self._hub.subscribe_symbols([sym])

        try:
            trades = get_list_range(f"trade_extra:{sym}", 0, 50)
            if not trades:
                trades = get_list_range(f"trade:{sym}", 0, 50)
            if trades:
                return {"symbol": sym, "trades": trades, "source": "dnse-ws"}
        except Exception:
            pass

        return {"symbol": sym, "trades": [], "source": "empty"}

    async def get_fundamentals(self, symbol: str) -> Dict:
        sym = symbol.upper()
        result = {"symbol": sym, "source": "pending"}

        if self._rest.is_live:
            try:
                result = self._rest.get_fundamentals(sym)
                result.setdefault("symbol", sym)
                result["source"] = "dnse-rest"
            except Exception:
                pass

        # Try to compute market cap from latest price + shares outstanding
        if result.get("market_cap") is None or result.get("market_cap") == 0:
            try:
                price_data = await self.get_quote(sym)
                price = price_data.get("price", 0) or price_data.get("close", 0)
                if price > 0:
                    from app.services.dnse.intraday_tool import get_intraday_tool
                    from datetime import datetime, timedelta, timezone
                    TZ_VN = timezone(timedelta(hours=7))
                    now = datetime.now(TZ_VN)
                    to_ts = int(now.timestamp())
                    from_ts = int((now - timedelta(days=7)).timestamp())
                    tool = get_intraday_tool()
                    candles = tool.fetch(sym, resolution="1D", from_ts=from_ts, to_ts=to_ts)
                    if candles:
                        price = float(candles[-1]["close"])

                    # Get shares outstanding from vnstock
                    try:
                        from vnstock import Vnstock
                        stock = Vnstock().stock(symbol=sym, source="KBS")
                        profile = stock.company.overview()
                        if profile is not None and not profile.empty:
                            shares = profile.iloc[0].get("outstanding_shares", 0) or profile.iloc[0].get("no_of_fluctuation_share", 0) or profile.iloc[0].get("no_of_share", 0)
                            if shares and float(shares) > 0:
                                result["market_cap"] = float(price) * float(shares)
                    except Exception:
                        # vnstock not available or failed — use estimated shares
                        result["market_cap"] = float(price) * 1_000_000_000  # ~1B shares placeholder
            except Exception:
                pass

        return result

    async def get_liquidity(self) -> Dict:
        """Return live liquidity from Redis or compute from snapshot."""
        try:
            r = get_redis()
            cached = r.get("market:liquidity")
            if cached:
                import json
                return {**json.loads(cached), "source": "redis"}
        except Exception:
            pass

        snap = await self.get_snapshot()
        stocks = snap.get("stocks", [])
        total_value = sum(s.get("tradingValue", 0) for s in stocks) / 1e9
        top_by_volume = sorted(stocks, key=lambda s: s.get("volume", 0), reverse=True)[:10]

        return {
            "totalValueBillion": round(total_value, 2),
            "stockCount": len(stocks),
            "topByVolume": top_by_volume,
            "lastUpdate": datetime.now().isoformat(),
            "source": "computed",
        }

    async def get_heatmap(self) -> Dict:
        """Compute heatmap from live snapshot."""
        try:
            r = get_redis()
            cached = r.get("market:heatmap")
            if cached:
                import json
                return {**json.loads(cached), "source": "redis"}
        except Exception:
            pass

        snap = await self.get_snapshot()
        sector_map: Dict[str, Dict] = {}
        for s in snap.get("stocks", []):
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

        return {"sectors": sectors, "source": "computed"}

    async def screen_stocks(self, filters: Dict) -> Dict:
        from app.services.screener_service import screener_svc
        return await screener_svc.screen(filters)


market_data_svc = MarketDataService()
