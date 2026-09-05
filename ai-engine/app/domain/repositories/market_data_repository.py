"""Market Data Repository (IOS v5.1)
Quản lý truy xuất và lưu trữ dữ liệu thị trường:
- ohlcv: Chuỗi nến giá lịch sử
- market_data_daily: Dữ liệu phân tách phiên ATO/ATC/Continuous & ADTV20
- technical_indicators: Chỉ số phân tích kỹ thuật (RSI, MACD, MA...)
- foreign_flow: Dòng tiền mua/bán khối ngoại
- market_regime: Trạng thái xu hướng thị trường (HMM Regime)
- macro_indicators: Các biến số kinh tế vĩ mô
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


class MarketDataRepository:
    """Repository chuẩn hóa truy cập dữ liệu thị trường và vĩ mô."""

    def __init__(self, storage: Optional[PostgresAdapter] = None):
        self.storage = storage or PostgresAdapter()

    def get_ohlcv(
        self,
        symbol: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách nến OHLCV của cổ phiếu."""
        symbol = symbol.upper().strip()
        conditions = ["symbol = %s"]
        params: List[Any] = [symbol]

        if start_date:
            conditions.append("time >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("time <= %s")
            params.append(end_date)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT time, open, high, low, close, volume, adj_close, adj_factor
            FROM ohlcv
            WHERE {where_clause}
            ORDER BY time DESC
            LIMIT %s
        """
        params.append(limit)

        try:
            rows = self.storage.fetch_all(query, tuple(params))
            if rows:
                return [
                    {
                        "time": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": int(r[5]),
                        "adj_close": float(r[6]) if r[6] is not None else float(r[4]),
                        "adj_factor": float(r[7]) if r[7] is not None else 1.0,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc ohlcv cho {symbol} ({e})")
        return []

    def get_market_data_daily(
        self,
        ticker: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """Lấy dữ liệu thị trường chi tiết (ADTV20, Continuous Volume, Market Cap)."""
        ticker = ticker.upper().strip()
        conditions = ["ticker = %s"]
        params: List[Any] = [ticker]

        if start_date:
            conditions.append("date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("date <= %s")
            params.append(end_date)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT date, open_adj, high_adj, low_adj, close_adj, vwap,
                   volume_continuous, volume_atc, volume_ato, volume_total,
                   foreign_buy_vol, foreign_sell_vol, foreign_net_vol,
                   adtv20_continuous, market_cap
            FROM market_data_daily
            WHERE {where_clause}
            ORDER BY date DESC
            LIMIT %s
        """
        params.append(limit)

        try:
            rows = self.storage.fetch_all(query, tuple(params))
            if rows:
                return [
                    {
                        "date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                        "open_adj": float(r[1]) if r[1] is not None else 0.0,
                        "high_adj": float(r[2]) if r[2] is not None else 0.0,
                        "low_adj": float(r[3]) if r[3] is not None else 0.0,
                        "close_adj": float(r[4]) if r[4] is not None else 0.0,
                        "vwap": float(r[5]) if r[5] is not None else 0.0,
                        "volume_continuous": int(r[6]) if r[6] is not None else 0,
                        "volume_atc": int(r[7]) if r[7] is not None else 0,
                        "volume_ato": int(r[8]) if r[8] is not None else 0,
                        "volume_total": int(r[9]) if r[9] is not None else 0,
                        "foreign_net_vol": int(r[12]) if r[12] is not None else 0,
                        "adtv20_continuous": float(r[13]) if r[13] is not None else 0.0,
                        "market_cap": float(r[14]) if r[14] is not None else 0.0,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc market_data_daily cho {ticker} ({e})")
        return []

    def get_technical_indicators(self, symbol: str, calc_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """Lấy các chỉ số kỹ thuật đã tính toán sẵn."""
        symbol = symbol.upper().strip()
        if calc_date:
            query = "SELECT indicators FROM technical_indicators WHERE symbol = %s AND calc_date = %s"
            params = (symbol, calc_date)
        else:
            query = "SELECT indicators FROM technical_indicators WHERE symbol = %s ORDER BY calc_date DESC LIMIT 1"
            params = (symbol,)

        try:
            rows = self.storage.fetch_all(query, params)
            if rows and rows[0][0]:
                return rows[0][0]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc technical_indicators cho {symbol} ({e})")
        return None

    def get_foreign_flow(self, symbol: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Lấy lịch sử dòng tiền ngoại theo ngày."""
        symbol = symbol.upper().strip()
        query = """
            SELECT trade_date, buy_volume, sell_volume, net_volume, net_value, room_remaining, ownership_pct
            FROM foreign_flow
            WHERE symbol = %s
            ORDER BY trade_date DESC
            LIMIT %s
        """
        try:
            rows = self.storage.fetch_all(query, (symbol, limit))
            if rows:
                return [
                    {
                        "trade_date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                        "buy_volume": int(r[1]) if r[1] is not None else 0,
                        "sell_volume": int(r[2]) if r[2] is not None else 0,
                        "net_volume": int(r[3]) if r[3] is not None else 0,
                        "net_value": float(r[4]) if r[4] is not None else 0.0,
                        "room_remaining": int(r[5]) if r[5] is not None else 0,
                        "ownership_pct": float(r[6]) if r[6] is not None else 0.0,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc foreign_flow cho {symbol} ({e})")
        return []

    def get_latest_market_regime(self) -> Dict[str, Any]:
        """Lấy trạng thái phân loại thị trường (Regime) gần nhất."""
        query = """
            SELECT date, regime_label, breadth_ma50, breadth_ma200, breadth_rsi_oversold,
                   breadth_rsi_overbought, market_volume_sma20_ratio, net_foreign_flow_bil
            FROM market_regime
            ORDER BY date DESC
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query)
            if rows and len(rows) > 0:
                r = rows[0]
                return {
                    "date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                    "regime_label": str(r[1]) if r[1] else "BULL_MARKET",
                    "breadth_ma50": float(r[2]) if r[2] is not None else 0.5,
                    "breadth_ma200": float(r[3]) if r[3] is not None else 0.5,
                    "breadth_rsi_oversold": float(r[4]) if r[4] is not None else 0.0,
                    "breadth_rsi_overbought": float(r[5]) if r[5] is not None else 0.0,
                    "volume_ratio": float(r[6]) if r[6] is not None else 1.0,
                    "net_foreign_flow_bil": float(r[7]) if r[7] is not None else 0.0,
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc market_regime ({e})")

        return {
            "date": date.today().isoformat(),
            "regime_label": "BULL_MARKET",
            "breadth_ma50": 0.65,
            "breadth_ma200": 0.60,
        }

    def save_market_regime(self, regime_data: Dict[str, Any]) -> bool:
        """Lưu snapshot phân loại Regime thị trường."""
        query = """
            INSERT INTO market_regime (
                date, regime_label, breadth_ma50, breadth_ma200,
                breadth_rsi_oversold, breadth_rsi_overbought,
                market_volume_sma20_ratio, net_foreign_flow_bil, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE SET
                regime_label = EXCLUDED.regime_label,
                breadth_ma50 = EXCLUDED.breadth_ma50,
                breadth_ma200 = EXCLUDED.breadth_ma200,
                market_volume_sma20_ratio = EXCLUDED.market_volume_sma20_ratio,
                net_foreign_flow_bil = EXCLUDED.net_foreign_flow_bil
        """
        now = datetime.now()
        target_date = regime_data.get("date", date.today())
        try:
            self.storage.execute(
                query,
                (
                    target_date,
                    regime_data.get("regime_label", "BULL_MARKET"),
                    regime_data.get("breadth_ma50", 0.5),
                    regime_data.get("breadth_ma200", 0.5),
                    regime_data.get("breadth_rsi_oversold", 0.0),
                    regime_data.get("breadth_rsi_overbought", 0.0),
                    regime_data.get("volume_ratio", 1.0),
                    regime_data.get("net_foreign_flow_bil", 0.0),
                    now,
                ),
            )
            return True
        except Exception as e:
            logger.warning(f"Không thể lưu market_regime ({e})")
            return False

    def get_realtime_or_latest_price(
        self,
        symbol: str,
        allow_eod_fallback: bool = True,
    ) -> Optional[float]:
        """
        Lấy giá thị trường:
        1. Ưu tiên 1: Giá khớp realtime từ DNSE WebSocket Stream Hub (lưu trong Redis `stock:{symbol}:quote`).
        2. Ưu tiên 2: Giá nến 1 phút realtime gần nhất từ DNSE REST API (DnseIntradayTool).
        3. Ưu tiên 3 (Ngoài giờ giao dịch): Giá đóng cửa ngày hôm qua từ CSDL (market_data_daily / ohlcv).
        """
        symbol_clean = str(symbol).upper().strip()

        # 1. DNSE Realtime WebSocket qua Redis Cache
        try:
            from app.infrastructure.external_api.dnse.redis_pub import get_redis
            import json
            r = get_redis()
            cached_data = r.get(f"stock:{symbol_clean}:quote")
            if cached_data:
                quote = json.loads(cached_data)
                price = float(quote.get("price", 0.0))
                if price > 0:
                    logger.debug(f"[DNSE Realtime] Lấy giá khớp realtime từ Redis cho {symbol_clean}: {price:,} VND")
                    return price
        except Exception as e:
            logger.debug(f"Không thể đọc quote realtime từ Redis ({e})")

        # 2. DNSE REST API Intraday (Nến 1m)
        try:
            from app.infrastructure.external_api.dnse.intraday_tool import DnseIntradayTool
            tool = DnseIntradayTool()
            intraday_candles = tool.fetch(symbol_clean, resolution="1")
            if intraday_candles and len(intraday_candles) > 0:
                latest_candle = intraday_candles[-1]
                price = float(latest_candle.get("close", 0.0))
                if price > 0:
                    logger.debug(f"[DNSE REST] Lấy giá 1m realtime từ DNSE REST cho {symbol_clean}: {price:,} VND")
                    return price
        except Exception as e:
            logger.debug(f"Không thể đọc intraday từ DNSE REST ({e})")

        # 3. Fallback: Giá đóng cửa ngày hôm qua từ PostgreSQL
        if allow_eod_fallback:
            daily = self.get_market_data_daily(symbol_clean, limit=1)
            if daily:
                if daily[0].get("close_adj") and float(daily[0]["close_adj"]) > 0:
                    price = float(daily[0]["close_adj"])
                    if price < 1000.0:  # Chuẩn hóa đơn vị nghìn đồng sàn HOSE sang VND
                        price = price * 1000.0
                    logger.info(f"[EOD Fallback] Sử dụng giá đóng cửa ngày hôm qua (close_adj={price:,.0f} VND) cho {symbol_clean}.")
                    return price
                elif daily[0].get("close") and float(daily[0]["close"]) > 0:
                    price = float(daily[0]["close"])
                    if price < 1000.0:
                        price = price * 1000.0
                    logger.info(f"[EOD Fallback] Sử dụng giá đóng cửa ngày hôm qua (close={price:,.0f} VND) cho {symbol_clean}.")
                    return price

            ohlcv_rows = self.get_ohlcv(symbol_clean, limit=1)
            if ohlcv_rows and ohlcv_rows[0].get("close") and float(ohlcv_rows[0]["close"]) > 0:
                price = float(ohlcv_rows[0]["close"])
                if price < 1000.0:
                    price = price * 1000.0
                logger.info(f"[EOD Fallback] Sử dụng giá nến OHLCV gần nhất ({price:,.0f} VND) cho {symbol_clean}.")
                return price

        return None
