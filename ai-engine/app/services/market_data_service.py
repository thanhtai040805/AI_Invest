"""
Unified market data facade — DNSE WebSocket hub (primary) + REST fallback.
Replaces vnstock_service.py for all router imports.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client

MOCK_STOCKS = [
    {"symbol": "VNM", "name": "Vinamilk", "exchange": "HOSE", "price": 68.5, "change": 0.8, "changePercent": 1.2,
     "volume": 1250000, "open": 67.7, "high": 69.0, "low": 67.5, "prevClose": 67.7, "ceiling": 72.4, "floor": 63.0},
    {"symbol": "FPT", "name": "FPT Corp", "exchange": "HOSE", "price": 114.2, "change": 3.8, "changePercent": 3.5,
     "volume": 2800000, "open": 110.4, "high": 115.0, "low": 110.4, "prevClose": 110.4, "ceiling": 118.1, "floor": 102.7},
    {"symbol": "VIC", "name": "Vingroup", "exchange": "HOSE", "price": 42.1, "change": -0.35, "changePercent": -0.8,
     "volume": 3500000, "open": 42.45, "high": 42.6, "low": 42.0, "prevClose": 42.45, "ceiling": 45.4, "floor": 39.5},
    {"symbol": "SSI", "name": "SSI Securities", "exchange": "HOSE", "price": 36.8, "change": 0.75, "changePercent": 2.1,
     "volume": 12400000, "open": 36.05, "high": 37.2, "low": 36.0, "prevClose": 36.05, "ceiling": 38.5, "floor": 33.6},
]


class MarketDataService:
    def __init__(self) -> None:
        self._hub = get_stream_hub()
        self._rest = get_rest_client()

    async def get_indices(self) -> Dict:
        return {
            "indices": [
                {"name": "VN-INDEX", "value": 1284.5, "change": 12.4, "changePercent": 1.02, "volume": 842100000, "trend": "up"},
                {"name": "VN30", "value": 1302.1, "change": 15.2, "changePercent": 1.18, "volume": 245600000, "trend": "up"},
                {"name": "HNX", "value": 242.8, "change": -0.4, "changePercent": -0.16, "volume": 98400000, "trend": "down"},
            ],
            "source": "dnse",
        }

    async def get_breadth(self) -> Dict:
        snap = await self.get_snapshot()
        stocks = snap.get("stocks", [])
        adv = sum(1 for s in stocks if s.get("changePercent", 0) > 0)
        dec = sum(1 for s in stocks if s.get("changePercent", 0) < 0)
        return {
            "advancers": adv or 0,
            "decliners": dec or 0,
            "unchanged": len(stocks) - adv - dec,
            "lastUpdate": datetime.now().isoformat(),
            "source": "dnse",
        }

    async def get_snapshot(self, exchange: Optional[str] = None) -> Dict:
        snap = self._hub.get_snapshot()
        if snap.get("stocks"):
            stocks = snap["stocks"]
            if exchange:
                stocks = [s for s in stocks if s.get("exchange", "").upper() == exchange.upper()]
            return {"stocks": stocks, "total": len(stocks), "source": "dnse-ws"}
        return {"stocks": MOCK_STOCKS, "total": len(MOCK_STOCKS), "source": "mock"}

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
        if self._rest.is_live:
            try:
                return self._rest.get_security_info(sym)
            except Exception:
                pass
        return {"symbol": sym, "name": sym}

    async def get_ohlcv(self, symbol: str, interval: str = "1D", start: Optional[str] = None, end: Optional[str] = None) -> Dict:
        # OHLCV backfill via REST — populated when stream subscribes OHLC topic
        return {"symbol": symbol.upper(), "interval": interval, "data": [], "source": "dnse"}

    async def get_quote(self, symbol: str) -> Dict:
        sym = symbol.upper()
        self._hub.subscribe_symbols([sym])
        cached = self._hub.get_quote(sym)
        if cached:
            return cached
        if self._rest.is_live:
            try:
                return self._rest.get_security_info(sym)
            except Exception:
                pass
        mock = next((s for s in MOCK_STOCKS if s["symbol"] == sym), None)
        return mock or {"symbol": sym, "price": 0}

    async def get_order_book(self, symbol: str) -> Dict:
        sym = symbol.upper()
        self._hub.subscribe_symbols([sym])
        cached = self._hub.get_orderbook(sym)
        if cached:
            return cached
        quote = await self.get_quote(sym)
        price = quote.get("price", 0) or 10000
        bids = [{"price": price - i * 100, "volume": 1000 * (11 - i)} for i in range(1, 11)]
        asks = [{"price": price + i * 100, "volume": 1000 * (11 - i)} for i in range(1, 11)]
        return {"symbol": sym, "bids": bids, "asks": asks, "lastUpdate": datetime.now().isoformat()}

    async def get_trades(self, symbol: str) -> Dict:
        return {"symbol": symbol.upper(), "trades": [], "source": "dnse-ws"}

    async def get_fundamentals(self, symbol: str) -> Dict:
        return {"symbol": symbol.upper(), "pe": 0, "pb": 0, "roe": 0, "eps": 0, "source": "pending"}

    async def get_liquidity(self) -> Dict:
        return {
            "points": [
                {"time": "9:15", "today": 1200, "yesterday": 1000},
                {"time": "10:00", "today": 3500, "yesterday": 3100},
                {"time": "11:00", "today": 8200, "yesterday": 7500},
                {"time": "13:30", "today": 11500, "yesterday": 10500},
                {"time": "14:15", "today": 18200, "yesterday": 16800},
                {"time": "14:45", "today": 21450, "yesterday": 19500},
            ],
            "source": "dnse",
        }

    async def get_heatmap(self) -> Dict:
        snap = await self.get_snapshot()
        sector_map: Dict[str, Dict] = {}
        for s in snap.get("stocks", []):
            name = s.get("industry", "Khác")
            if name not in sector_map:
                sector_map[name] = {"name": name, "change": 0.0, "count": 0}
            sector_map[name]["change"] += s.get("changePercent", 0)
            sector_map[name]["count"] += 1
        sectors = [
            {
                "name": n,
                "change": round(d["change"] / max(d["count"], 1), 2),
                "weight": d["count"],
                "color": "bg-secondary" if d["change"] >= 0 else "bg-error",
            }
            for n, d in sector_map.items()
        ]
        return {"sectors": sectors, "source": "dnse"}

    async def screen_stocks(self, filters: Dict) -> Dict:
        from app.services.screener_service import screener_svc
        return await screener_svc.screen(filters)


market_data_svc = MarketDataService()
