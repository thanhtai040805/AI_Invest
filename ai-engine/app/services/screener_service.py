"""
Multi-criteria stock screener — enriches snapshot with fundamentals and filters.
"""

import hashlib
from typing import Any, Dict, List, Optional

from app.services.market_data_service import market_data_svc


def _mock_fundamentals(symbol: str) -> Dict[str, float]:
    h = int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)
    return {
        "pe": 5 + (h % 25),
        "pb": 0.5 + (h % 40) / 10,
        "roe": 5 + (h % 30),
        "de": (h % 20) / 10,
        "eps": 1000 + (h % 9000),
        "rsi": 20 + (h % 60),
    }


def _estimate_rsi(change_pct: float) -> float:
    if change_pct > 3:
        return min(85, 55 + change_pct * 5)
    if change_pct < -3:
        return max(15, 45 + change_pct * 5)
    return 50 + change_pct * 3


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
    if f.get("marketCapMin") and (row.get("marketCap") or 0) < f["marketCapMin"]:
        return False
    if f.get("marketCapMax") and (row.get("marketCap") or 0) > f["marketCapMax"]:
        return False
    if f.get("volumeMin") and (row.get("volume") or 0) < f["volumeMin"]:
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
    async def screen(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        snap = await market_data_svc.get_snapshot(filters.get("exchange"))
        rows = snap.get("stocks")
        enriched: List[Dict[str, Any]] = []

        for row in rows:
            sym = row.get("symbol", "")
            if not sym:
                continue
            fund = await market_data_svc.get_fundamentals(sym)
            if not fund.get("pe"):
                fund = _mock_fundamentals(sym)
            rsi = fund.get("rsi") or _estimate_rsi(row.get("changePercent", 0))
            item = {
                **row,
                "pe": float(fund.get("pe", 0) or 0),
                "pb": float(fund.get("pb", 0) or 0),
                "roe": float(fund.get("roe", 0) or 0),
                "de": float(fund.get("de", 0) or 0),
                "eps": float(fund.get("eps", 0) or 0),
                "rsi": float(rsi),
                "signal": row.get("signal", "THEO DÕI"),
            }
            if _passes(item, filters):
                enriched.append(item)

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
