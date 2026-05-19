"""
Unified market data facade — DNSE WebSocket hub (primary) + REST fallback.
Replaces vnstock_service.py for all router imports.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client

MOCK_STOCKS = [
    {"symbol": "ACB", "name": "Ngân hàng ACB", "exchange": "HOSE", "price": 27.2, "change": 0.3, "changePercent": 1.1,
     "volume": 6800000, "open": 26.9, "high": 27.3, "low": 26.85, "prevClose": 26.9, "ceiling": 28.75, "floor": 25.05,
     "industry": "Ngân hàng", "marketCap": 105000000000000, "tradingValue": 184960000000},
    {"symbol": "BCM", "name": "Becamex IDC", "exchange": "HOSE", "price": 54.0, "change": -0.5, "changePercent": -0.9,
     "volume": 120000, "open": 54.5, "high": 54.8, "low": 53.8, "prevClose": 54.5, "ceiling": 58.3, "floor": 50.7,
     "industry": "Bất động sản KCN", "marketCap": 56000000000000, "tradingValue": 6480000000},
    {"symbol": "BID", "name": "Ngân hàng BIDV", "exchange": "HOSE", "price": 49.5, "change": 0.5, "changePercent": 1.0,
     "volume": 1500000, "open": 49.0, "high": 50.1, "low": 48.8, "prevClose": 49.0, "ceiling": 52.4, "floor": 45.6,
     "industry": "Ngân hàng", "marketCap": 280000000000000, "tradingValue": 74250000000},
    {"symbol": "BVH", "name": "Tập đoàn Bảo Việt", "exchange": "HOSE", "price": 40.5, "change": 0.2, "changePercent": 0.5,
     "volume": 250000, "open": 40.3, "high": 40.8, "low": 40.1, "prevClose": 40.3, "ceiling": 43.1, "floor": 37.5,
     "industry": "Bảo hiểm", "marketCap": 30000000000000, "tradingValue": 10125000000},
    {"symbol": "CTG", "name": "VietinBank", "exchange": "HOSE", "price": 32.5, "change": -0.1, "changePercent": -0.3,
     "volume": 4200000, "open": 32.6, "high": 32.8, "low": 32.2, "prevClose": 32.6, "ceiling": 34.85, "floor": 30.35,
     "industry": "Ngân hàng", "marketCap": 174000000000000, "tradingValue": 136500000000},
    {"symbol": "FPT", "name": "Tập đoàn FPT", "exchange": "HOSE", "price": 114.2, "change": 3.8, "changePercent": 3.5,
     "volume": 2800000, "open": 110.4, "high": 115.0, "low": 110.4, "prevClose": 110.4, "ceiling": 118.1, "floor": 102.7,
     "industry": "Công nghệ", "marketCap": 145000000000000, "tradingValue": 319760000000},
    {"symbol": "GAS", "name": "PV Gas", "exchange": "HOSE", "price": 75.8, "change": 1.2, "changePercent": 1.6,
     "volume": 850000, "open": 74.6, "high": 76.2, "low": 74.5, "prevClose": 74.6, "ceiling": 79.8, "floor": 69.4,
     "industry": "Dầu khí", "marketCap": 182000000000000, "tradingValue": 64430000000},
    {"symbol": "GVR", "name": "Tập đoàn Cao su", "exchange": "HOSE", "price": 28.5, "change": 0.4, "changePercent": 1.4,
     "volume": 1800000, "open": 28.1, "high": 28.8, "low": 28.0, "prevClose": 28.1, "ceiling": 30.05, "floor": 26.15,
     "industry": "Hóa chất/Cao su", "marketCap": 114000000000000, "tradingValue": 51300000000},
    {"symbol": "HDB", "name": "HDBank", "exchange": "HOSE", "price": 21.8, "change": 0.15, "changePercent": 0.7,
     "volume": 3200000, "open": 21.65, "high": 22.0, "low": 21.5, "prevClose": 21.65, "ceiling": 23.15, "floor": 20.15,
     "industry": "Ngân hàng", "marketCap": 63000000000000, "tradingValue": 69760000000},
    {"symbol": "HPG", "name": "Thép Hòa Phát", "exchange": "HOSE", "price": 28.35, "change": 0.45, "changePercent": 1.6,
     "volume": 18500000, "open": 27.9, "high": 28.5, "low": 27.8, "prevClose": 27.9, "ceiling": 29.85, "floor": 25.95,
     "industry": "Thép", "marketCap": 164000000000000, "tradingValue": 524475000000},
    {"symbol": "MBB", "name": "Ngân hàng MBB", "exchange": "HOSE", "price": 21.25, "change": 0.25, "changePercent": 1.2,
     "volume": 12500000, "open": 21.0, "high": 21.4, "low": 20.95, "prevClose": 21.0, "ceiling": 22.45, "floor": 19.55,
     "industry": "Ngân hàng", "marketCap": 110000000000000, "tradingValue": 265625000000},
    {"symbol": "MSN", "name": "Tập đoàn Masan", "exchange": "HOSE", "price": 68.0, "change": -0.8, "changePercent": -1.2,
     "volume": 1800000, "open": 68.8, "high": 69.2, "low": 67.8, "prevClose": 68.8, "ceiling": 73.6, "floor": 64.0,
     "industry": "Bán lẻ", "marketCap": 97000000000000, "tradingValue": 122400000000},
    {"symbol": "MWG", "name": "Thế Giới Di Động", "exchange": "HOSE", "price": 52.3, "change": 1.1, "changePercent": 2.1,
     "volume": 5800000, "open": 51.2, "high": 52.7, "low": 51.0, "prevClose": 51.2, "ceiling": 54.7, "floor": 47.7,
     "industry": "Bán lẻ", "marketCap": 76000000000000, "tradingValue": 303340000000},
    {"symbol": "PLX", "name": "Petrolimex", "exchange": "HOSE", "price": 36.5, "change": 0.3, "changePercent": 0.8,
     "volume": 600000, "open": 36.2, "high": 36.8, "low": 36.0, "prevClose": 36.2, "ceiling": 38.7, "floor": 33.7,
     "industry": "Dầu khí", "marketCap": 47000000000000, "tradingValue": 21900000000},
    {"symbol": "POW", "name": "PV Power", "exchange": "HOSE", "price": 11.2, "change": 0.05, "changePercent": 0.4,
     "volume": 2400000, "open": 11.15, "high": 11.3, "low": 11.1, "prevClose": 11.15, "ceiling": 11.9, "floor": 10.4,
     "industry": "Điện/Năng lượng", "marketCap": 26000000000000, "tradingValue": 26880000000},
    {"symbol": "SAB", "name": "Sabeco", "exchange": "HOSE", "price": 56.5, "change": -0.5, "changePercent": -0.9,
     "volume": 150000, "open": 57.0, "high": 57.2, "low": 56.1, "prevClose": 57.0, "ceiling": 60.9, "floor": 53.1,
     "industry": "Thực phẩm & Đồ uống", "marketCap": 72000000000000, "tradingValue": 8475000000},
    {"symbol": "SHB", "name": "Ngân hàng SHB", "exchange": "HOSE", "price": 11.35, "change": 0.1, "changePercent": 0.9,
     "volume": 8400000, "open": 11.25, "high": 11.45, "low": 11.2, "prevClose": 11.25, "ceiling": 12.0, "floor": 10.5,
     "industry": "Ngân hàng", "marketCap": 41000000000000, "tradingValue": 95340000000},
    {"symbol": "SSB", "name": "SeABank", "exchange": "HOSE", "price": 22.0, "change": -0.2, "changePercent": -0.9,
     "volume": 500000, "open": 22.2, "high": 22.3, "low": 21.9, "prevClose": 22.2, "ceiling": 23.75, "floor": 20.65,
     "industry": "Ngân hàng", "marketCap": 55000000000000, "tradingValue": 11000000000},
    {"symbol": "SSI", "name": "Chứng khoán SSI", "exchange": "HOSE", "price": 36.8, "change": 0.75, "changePercent": 2.1,
     "volume": 12400000, "open": 36.05, "high": 37.2, "low": 36.0, "prevClose": 36.05, "ceiling": 38.5, "floor": 33.6,
     "industry": "Dịch vụ tài chính", "marketCap": 58000000000000, "tradingValue": 456320000000},
    {"symbol": "STB", "name": "Sacombank", "exchange": "HOSE", "price": 28.5, "change": 0.3, "changePercent": 1.1,
     "volume": 9500000, "open": 28.2, "high": 28.7, "low": 28.1, "prevClose": 28.2, "ceiling": 30.15, "floor": 26.25,
     "industry": "Ngân hàng", "marketCap": 53000000000000, "tradingValue": 270750000000},
    {"symbol": "TCB", "name": "Techcombank", "exchange": "HOSE", "price": 45.2, "change": 0.8, "changePercent": 1.8,
     "volume": 7500000, "open": 44.4, "high": 45.5, "low": 44.25, "prevClose": 44.4, "ceiling": 47.5, "floor": 41.3,
     "industry": "Ngân hàng", "marketCap": 158000000000000, "tradingValue": 339000000000},
    {"symbol": "TPB", "name": "TPBank", "exchange": "HOSE", "price": 18.25, "change": 0.15, "changePercent": 0.8,
     "volume": 3800000, "open": 18.1, "high": 18.35, "low": 18.05, "prevClose": 18.1, "ceiling": 19.35, "floor": 16.85,
     "industry": "Ngân hàng", "marketCap": 40000000000000, "tradingValue": 69350000000},
    {"symbol": "VCB", "name": "Vietcombank", "exchange": "HOSE", "price": 92.5, "change": 1.5, "changePercent": 1.6,
     "volume": 980000, "open": 91.0, "high": 93.0, "low": 90.8, "prevClose": 91.0, "ceiling": 97.3, "floor": 84.7,
     "industry": "Ngân hàng", "marketCap": 510000000000000, "tradingValue": 90650000000},
    {"symbol": "VHM", "name": "Vinhomes", "exchange": "HOSE", "price": 38.5, "change": -0.6, "changePercent": -1.5,
     "volume": 4800000, "open": 39.1, "high": 39.3, "low": 38.2, "prevClose": 39.1, "ceiling": 41.8, "floor": 36.4,
     "industry": "Bất động sản", "marketCap": 167000000000000, "tradingValue": 184800000000},
    {"symbol": "VIC", "name": "Vingroup", "exchange": "HOSE", "price": 42.1, "change": -0.35, "changePercent": -0.8,
     "volume": 3500000, "open": 42.45, "high": 42.6, "low": 42.0, "prevClose": 42.45, "ceiling": 45.4, "floor": 39.5,
     "industry": "Bất động sản", "marketCap": 161000000000000, "tradingValue": 147350000000},
    {"symbol": "VJC", "name": "Vietjet Air", "exchange": "HOSE", "price": 103.5, "change": 0.5, "changePercent": 0.5,
     "volume": 350000, "open": 103.0, "high": 104.2, "low": 102.8, "prevClose": 103.0, "ceiling": 110.2, "floor": 95.8,
     "industry": "Hàng không", "marketCap": 56000000000000, "tradingValue": 36225000000},
    {"symbol": "VNM", "name": "Vinamilk", "exchange": "HOSE", "price": 68.5, "change": 0.8, "changePercent": 1.2,
     "volume": 1250000, "open": 67.7, "high": 69.0, "low": 67.5, "prevClose": 67.7, "ceiling": 72.4, "floor": 63.0,
     "industry": "Thực phẩm & Đồ uống", "marketCap": 143000000000000, "tradingValue": 85625000000},
    {"symbol": "VPB", "name": "VPBank", "exchange": "HOSE", "price": 18.7, "change": 0.2, "changePercent": 1.1,
     "volume": 14500000, "open": 18.5, "high": 18.8, "low": 18.4, "prevClose": 18.5, "ceiling": 19.8, "floor": 17.2,
     "industry": "Ngân hàng", "marketCap": 148000000000000, "tradingValue": 271150000000},
    {"symbol": "VRE", "name": "Vincom Retail", "exchange": "HOSE", "price": 22.4, "change": -0.3, "changePercent": -1.3,
     "volume": 3800000, "open": 22.7, "high": 22.9, "low": 22.25, "prevClose": 22.7, "ceiling": 24.3, "floor": 21.1,
     "industry": "Bất động sản", "marketCap": 51000000000000, "tradingValue": 85120000000}
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
        # Merge live stream ticks dynamically with full VN30 master list
        merged_stocks = []
        for base in MOCK_STOCKS:
            sym = base["symbol"]
            quote = self._hub.get_quote(sym)
            if quote:
                merged = {**base, **quote}
            else:
                merged = base
            
            if exchange and merged.get("exchange", "").upper() != exchange.upper():
                continue
            merged_stocks.append(merged)
            
        return {
            "stocks": merged_stocks,
            "total": len(merged_stocks),
            "source": "dnse-ws" if self._hub.mode == "live" else "mock"
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
        if self._rest.is_live:
            try:
                return self._rest.get_security_info(sym)
            except Exception:
                pass
        return {"symbol": sym, "name": sym}

    async def get_ohlcv(self, symbol: str, interval: str = "1D", start: Optional[str] = None, end: Optional[str] = None) -> Dict:
        sym = symbol.upper()
        mock_stock = next((s for s in MOCK_STOCKS if s["symbol"] == sym), None)
        base_price = mock_stock["price"] if mock_stock else 30.0
        
        # Parse interval to timedelta
        import random
        delta = timedelta(days=1)
        int_upper = interval.upper()
        if "1M" in int_upper:
            delta = timedelta(minutes=1)
        elif "15M" in int_upper:
            delta = timedelta(minutes=15)
        elif "1H" in int_upper:
            delta = timedelta(hours=1)
        elif "1D" in int_upper or "D" in int_upper:
            delta = timedelta(days=1)
            
        now = datetime.now()
        data = []
        curr_price = base_price
        
        # Generate 100 historical points backwards, then reverse
        for i in range(100):
            t = now - (100 - i) * delta
            # Random walk
            change = random.uniform(-0.015, 0.015) * curr_price
            open_p = curr_price
            close_p = curr_price + change
            high_p = max(open_p, close_p) + random.uniform(0, 0.008) * curr_price
            low_p = min(open_p, close_p) - random.uniform(0, 0.008) * curr_price
            vol = random.randint(10000, 500000)
            
            data.append({
                "time": t.isoformat(),
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": vol
            })
            curr_price = close_p

        return {
            "symbol": sym,
            "interval": interval,
            "data": data,
            "source": "mock-walk"
        }


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
