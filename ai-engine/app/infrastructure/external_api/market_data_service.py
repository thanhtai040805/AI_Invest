"""
Unified market data facade — DNSE WebSocket hub (primary) + PostgreSQL + REST fallback.

NO mock data merged with live data. Mock mode is separate (DNSE_ENABLED=false).
Data sources: PostgreSQL (historical daily) → Redis (recent 1-min) → in-memory hub (live) → DNSE REST API.
"""

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config.settings import get_settings
from app.infrastructure.external_api.dnse.stream_hub import get_stream_hub
from app.infrastructure.external_api.sector_heatmap import build_sector_heatmap
from app.infrastructure.external_api.dnse.market_session import MarketSessionManager
from app.infrastructure.external_api.dnse.rest_client import get_rest_client
from app.infrastructure.external_api.dnse.redis_pub import (
    get_redis,
    get_list_range,
    get_sorted_set_range,
)
from app.infrastructure.external_api.dnse.price_units import to_vnd_price

_PG_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


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


def _query_pg_calculation_ohlcv(symbol: str, start: Optional[str] = None, end: Optional[str] = None) -> List[Dict]:
    """Read only unadjusted OHLC for price calculations; never falls back to display prices."""
    try:
        import psycopg2
        conn = psycopg2.connect(_PG_URL)
        cur = conn.cursor()
        where = ["ticker = %s"]
        params: list = [symbol.upper()]
        if start:
            where.append("date >= %s::date")
            params.append(start)
        if end:
            where.append("date <= %s::date")
            params.append(end)
        cur.execute(
            f"SELECT date, open, high, low, close, volume_total FROM market_data_daily_calculation WHERE {' AND '.join(where)} ORDER BY date",
            params,
        )
        rows = [{
            "time": r[0].isoformat(),
            "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
            "close": float(r[4]), "volume": int(r[5] or 0),
        } for r in cur.fetchall() if all(v is not None for v in r[1:5])]
        cur.close()
        conn.close()
        return rows
    except Exception:
        import logging
        logging.getLogger("ai_engine.market_data").exception(
            "Failed to load unadjusted calculation OHLC for %s", symbol.upper()
        )
        return []


class MarketDataService:
    def __init__(self) -> None:
        self._hub = get_stream_hub()
        self._rest = get_rest_client()
        self._session = MarketSessionManager()

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
                    hose_indices = [
                        item for item in rest_indices
                        if (
                            str(item.get("name") or item.get("indexName") or item.get("symbol") or "")
                            .upper().replace("-", "") in {"VNINDEX", "VN30", "VN100"}
                        )
                    ]
                    return {"indices": hose_indices, "source": "dnse-rest"}
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
        """Return the database HOSE universe with the latest DNSE updates."""
        import json

        live_stocks: Dict[str, Dict] = {}
        board = self._hub.get_market_board_snapshot()
        has_database_universe = bool(board.get("stocks"))
        for stock in board.get("stocks", []):
            if stock.get("symbol"):
                live_stocks[stock["symbol"]] = stock

        # Use Redis's live snapshot only when the database universe is unavailable.
        if not has_database_universe:
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
            universe_symbols = set(self._hub._universe_symbols)

        for sym, quote in hub_quotes.items():
            if has_database_universe and sym not in universe_symbols:
                continue
            if sym in live_stocks:
                live_stocks[sym] = {**live_stocks[sym], **quote}
            else:
                live_stocks[sym] = quote

        stocks = [
            stock for stock in live_stocks.values()
            if str(stock.get("exchange") or "HOSE").upper() == "HOSE"
        ]
        stocks.sort(key=lambda stock: float(stock.get("volume", 0) or 0), reverse=True)

        if exchange:
            stocks = [s for s in stocks if s.get("exchange", "HOSE").upper() == exchange.upper()]

        live_symbols = sum(
            1 for symbol, quote in hub_quotes.items()
            if symbol in universe_symbols and quote.get("source") == "dnse-ws"
        )
        return {
            "stocks": stocks,
            "total": len(stocks),
            "source": "mixed" if live_symbols else "postgres" if stocks else "empty",
            "stale": not bool(live_symbols),
            "liveSymbols": live_symbols,
            "coverage": live_symbols / len(universe_symbols) if universe_symbols else 0,
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
        from app.infrastructure.data_pipelines.data_enricher import DataEnricher
        try:
            profile = DataEnricher.fetch_vnstock_profile(sym)
            if profile:
                return profile
        except Exception:
            pass

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
        res = await self._get_ohlcv_raw(symbol, interval, start, end)
        data = res.get("data", [])
        if data:
            for c in data:
                for field in ("open", "high", "low", "close"):
                    c[field] = to_vnd_price(c.get(field, 0.0))
                close = float(c.get("close", 0.0))
                volume = float(c.get("volume", 0.0))
                turnover = c.get("value", c.get("turnover"))
                if turnover is not None:
                    value = float(turnover)
                    c["value"] = value
                    c["vwap"] = value / volume if volume > 0 else None
                c["adj_close"] = c.get("adj_close", close)
        return res

    async def get_calculation_ohlcv(self, symbol: str, start: Optional[str] = None, end: Optional[str] = None) -> Dict:
        data = _query_pg_calculation_ohlcv(symbol, start, end)
        return {"symbol": symbol.upper(), "data": data, "source": "postgres-unadjusted"}

    async def _get_ohlcv_raw(self, symbol: str, interval: str = "1D", start: Optional[str] = None, end: Optional[str] = None) -> Dict:
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
            today = datetime.now(TZ_VN).date()
            today_str = today.isoformat()
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

            # Refresh the recent window so a missed end-of-day backfill does not
            # leave an otherwise healthy PostgreSQL history one session behind.
            recent_start = (today - timedelta(days=30)).isoformat()
            refresh_start = max(start, recent_start) if start else recent_start
            if end is None or end >= recent_start:
                try:
                    rest_data = await self._fetch_rest_ohlcv(
                        sym, interval, refresh_start, end, logger
                    )
                    rest_rows = rest_data.get("data", []) if rest_data else []
                    if rest_rows:
                        merged_by_date = {
                            str(row.get("time", row.get("date", "")))[:10]: row
                            for row in historical_data
                        }
                        for row in rest_rows:
                            candle_date = str(row.get("time", row.get("date", "")))[:10]
                            if candle_date:
                                merged_by_date[candle_date] = row
                        historical_data = sorted(
                            merged_by_date.values(),
                            key=lambda row: str(row.get("time", row.get("date", ""))),
                        )
                except Exception as e:
                    logger.warning(f"OHLCV {sym} {interval}: recent REST refresh failed: {e}")

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
        with self._hub._lock:
            if sym not in self._hub._universe_symbols:
                return {"symbol": sym, "price": 0, "source": "unsupported"}
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
        market_state = self._session.get_market_state().value

        cached = self._hub.get_orderbook(sym)
        if cached:
            return {**cached, "marketState": market_state}

        try:
            r = get_redis()
            ob_cached = r.get(f"stock:{sym}:orderbook")
            if ob_cached:
                import json
                return {**json.loads(ob_cached), "marketState": market_state}
        except Exception:
            pass

        return {"symbol": sym, "bids": [], "asks": [], "marketState": market_state}

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

        from app.infrastructure.data_pipelines.data_enricher import DataEnricher
        try:
            enriched = DataEnricher.fetch_vnstock_financials(sym)
            result.update(enriched)
            result["source"] = "vnstock+enricher"
        except Exception as e:
            import logging
            logging.getLogger("ai_engine.market_data").warning(f"Failed enrichment fundamentals: {e}")

        if self._rest.is_live and (not result.get("income_statement") or not result.get("ratios")):
            try:
                rest_fund = self._rest.get_fundamentals(sym)
                if rest_fund:
                    for k, v in rest_fund.items():
                        if k not in result:
                            result[k] = v
            except Exception:
                pass

        # Try to compute market cap from latest price + shares outstanding
        if result.get("market_cap") is None or result.get("market_cap") == 0:
            sh_out = result.get("balance_sheet", {}).get("shares_outstanding")
            price_data = await self.get_quote(sym)
            price = price_data.get("price", 0) or price_data.get("close", 0)
            if price > 0 and sh_out:
                result["market_cap"] = float(price) * float(sh_out)
            else:
                result["market_cap"] = result.get("ratios", {}).get("pe_ratio", 15.0) * result.get("income_statement", {}).get("net_income", 1e9)

        # Copy to top level for compatibility with screener & frontend
        ratios = result.get("ratios", {})
        result.setdefault("pe", ratios.get("pe_ratio"))
        result.setdefault("pb", ratios.get("pb_ratio"))
        result.setdefault("roe", ratios.get("roe"))
        result.setdefault("roa", ratios.get("roa"))
        result.setdefault("eps", ratios.get("eps_basic"))
        result.setdefault("de", ratios.get("debt_to_equity"))
        result.setdefault("beta", ratios.get("beta"))
        result.setdefault("dividend_yield", ratios.get("dividend_yield"))

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

        return {
            "totalValueBillion": None,
            "lastUpdate": datetime.now().isoformat(),
            "source": "unavailable",
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
        return {"sectors": build_sector_heatmap(snap.get("stocks", [])), "source": "computed"}

    async def screen_stocks(self, filters: Dict) -> Dict:
        from app.domain.services.screener_service import screener_svc
        return await screener_svc.screen(filters)


market_data_svc = MarketDataService()
