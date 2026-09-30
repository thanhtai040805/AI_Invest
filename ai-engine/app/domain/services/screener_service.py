"""
Multi-criteria stock screener — enriches snapshot with fundamentals and filters.
"""

import asyncio
import json
import logging
import math
from typing import Any, Dict, List, Optional

from app.domain.repositories.financial_repository import FinancialRepository
from app.domain.repositories.market_data_repository import MarketDataRepository
from app.infrastructure.external_api.dnse.redis_pub import get_redis
from app.infrastructure.external_api.market_data_service import market_data_svc

logger = logging.getLogger(__name__)
FUNDAMENTALS_CACHE_TTL = 900
FUNDAMENTAL_FIELDS = ("pe", "pb", "roe", "de", "eps")


def _cached_fundamentals(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    try:
        raw_values = get_redis().mget([f"screener:fundamentals:{symbol}" for symbol in symbols])
    except Exception:
        return {}
    cached: Dict[str, Dict[str, Any]] = {}
    for symbol, raw in zip(symbols, raw_values):
        if not raw:
            continue
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                cached[symbol] = value
        except (TypeError, json.JSONDecodeError):
            continue
    return cached


def _store_fundamentals_cache(values: Dict[str, Dict[str, Any]]) -> None:
    if not values:
        return
    try:
        client = get_redis()
        pipeline = client.pipeline(transaction=False)
        for symbol, value in values.items():
            pipeline.setex(f"screener:fundamentals:{symbol}", FUNDAMENTALS_CACHE_TTL, json.dumps(value, default=str))
        pipeline.execute()
    except Exception as exc:
        logger.debug("Could not cache screener fundamentals: %s", exc)

def _passes(row: Dict[str, Any], f: Dict[str, Any]) -> bool:
    def in_range(val: Optional[float], lo: Optional[float], hi: Optional[float]) -> bool:
        if val is None:
            return lo is None and hi is None
        if lo is not None and val < lo:
            return False
        if hi is not None and val > hi:
            return False
        return True

    if f.get("exchange") and row.get("exchange", "").upper() != f["exchange"].upper():
        return False
    if not in_range(row.get("pe"), f.get("peMin"), f.get("peMax")):
        return False
    if not in_range(row.get("pb"), f.get("pbMin"), f.get("pbMax")):
        return False
    if not in_range(row.get("roe"), f.get("roeMin"), f.get("roeMax")):
        return False
    if not in_range(row.get("rsi"), f.get("rsiMin"), f.get("rsiMax")):
        return False
    if not in_range(row.get("de"), f.get("deMin"), f.get("deMax")):
        return False
    market_cap_min = f.get("marketCapMin")
    market_cap_max = f.get("marketCapMax")
    if market_cap_min is not None or market_cap_max is not None:
        try:
            market_cap = float(row.get("marketCap"))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(market_cap) or market_cap <= 0:
            return False
        if market_cap_min is not None and market_cap < market_cap_min:
            return False
        if market_cap_max is not None and market_cap > market_cap_max:
            return False
    volume_min = f.get("volumeMin")
    if volume_min is not None:
        try:
            volume = float(row.get("volume"))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(volume) or volume < volume_min:
            return False
    return True


BUILTIN_PRESETS: List[Dict[str, Any]] = [
    {"id": "valuation", "name": "Valuation", "filters": {"peMax": 15, "roeMin": 12, "pbMax": 3}},
    {"id": "growth", "name": "Growth", "filters": {"roeMin": 15, "peMax": 25}},
    {"id": "technical", "name": "Technical", "filters": {"rsiMin": 30, "rsiMax": 70}},
    {"id": "dividend", "name": "Dividend", "filters": {"roeMin": 10, "peMax": 12}},
    {"id": "momentum", "name": "Momentum", "filters": {"rsiMin": 55}},
]


class ScreenerService:
    def __init__(self) -> None:
        self._financial_repository = FinancialRepository()
        self._market_data_repository = MarketDataRepository()

    async def screen(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        snap = await market_data_svc.get_snapshot(filters.get("exchange"))
        rows = snap.get("stocks") or []
        symbols = sorted({str(row.get("symbol", "")).upper() for row in rows if row.get("symbol")})
        technical_task = asyncio.to_thread(self._market_data_repository.get_latest_technical_indicators_for_symbols, symbols)
        ratios_task = asyncio.to_thread(self._financial_repository.get_latest_screening_ratios_for_symbols, symbols)
        cached_fundamentals_task = asyncio.to_thread(_cached_fundamentals, symbols)
        technical_by_symbol, ratios_by_symbol, cached_fundamentals = await asyncio.gather(
            technical_task, ratios_task, cached_fundamentals_task
        )
        semaphore = asyncio.Semaphore(4)
        cache_updates: Dict[str, Dict[str, Any]] = {}
        required_fundamental_fields = {
            field for field, keys in {
                "pe": ("peMin", "peMax"),
                "pb": ("pbMin", "pbMax"),
                "roe": ("roeMin", "roeMax"),
                "de": ("deMin", "deMax"),
            }.items()
            if any(filters.get(key) is not None for key in keys)
        }

        def number_or_none(value: Any) -> Optional[float]:
            if value is None:
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) else None

        async def enrich(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            sym = row.get("symbol", "")
            if not sym:
                return None
            sym = str(sym).upper()
            fund = {**ratios_by_symbol.get(sym, {}), **cached_fundamentals.get(sym, {})}
            needs_remote_fundamentals = any(fund.get(field) is None for field in required_fundamental_fields)
            if needs_remote_fundamentals:
                async with semaphore:
                    try:
                        fetched = await market_data_svc.get_fundamentals(sym) or {}
                    except Exception as exc:
                        logger.debug("Could not enrich screener fundamentals for %s: %s", sym, exc)
                        fetched = {}
                for key in FUNDAMENTAL_FIELDS:
                    if fetched.get(key) is not None:
                        fund[key] = fetched[key]
                if any(fund.get(key) is not None for key in FUNDAMENTAL_FIELDS):
                    cache_updates[sym] = {key: fund.get(key) for key in FUNDAMENTAL_FIELDS}

            pe = number_or_none(fund.get("pe"))
            technicals = technical_by_symbol.get(sym, {})
            rsi = number_or_none(technicals.get("rsi_14"))
            roe = number_or_none(fund.get("roe"))
            if roe is not None and abs(roe) <= 1:
                roe *= 100
            item = {
                **row,
                "pe": pe,
                "pb": number_or_none(fund.get("pb")),
                "roe": roe,
                "de": number_or_none(fund.get("de")),
                "eps": number_or_none(fund.get("eps")),
                "rsi": rsi,
                "signal": row.get("signal", "THEO DÕI"),
            }
            return item if _passes(item, filters) else None

        enriched_rows = await asyncio.gather(*(enrich(row) for row in rows))
        await asyncio.to_thread(_store_fundamentals_cache, cache_updates)
        enriched = [row for row in enriched_rows if row is not None]

        sort_key = filters.get("sort", "changePercent")
        reverse = filters.get("sortDir", "desc") != "asc"
        enriched.sort(key=lambda x: x.get(sort_key, 0) or 0, reverse=reverse)

        offset = int(filters.get("offset", 0))
        limit = int(filters.get("limit", 50))
        page = enriched[offset : offset + limit]

        return {"stocks": page, "total": len(enriched), "source": "dnse"}

    def get_builtin_presets(self) -> List[Dict[str, Any]]:
        return BUILTIN_PRESETS


screener_svc = ScreenerService()
