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

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

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
            SELECT time, open, high, low, close, volume
            FROM ohlcv_unadjusted
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
        conditions = ["md.ticker = %s"]
        params: List[Any] = [ticker]

        if start_date:
            conditions.append("md.date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("md.date <= %s")
            params.append(end_date)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT md.date, md.open_adj, md.high_adj, md.low_adj, md.close_adj, md.close_unadj,
                   calc.open, calc.high, calc.low, calc.vwap,
                   md.volume_continuous, md.volume_atc, md.volume_ato, md.volume_total,
                   md.foreign_buy_vol, md.foreign_sell_vol, md.foreign_net_vol,
                   calc.adtv20_continuous, calc.market_cap
            FROM market_data_daily md
            LEFT JOIN market_data_daily_calculation calc USING (ticker, date)
            WHERE {where_clause}
            ORDER BY md.date DESC
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
                        "close_unadj": float(r[5]) if r[5] is not None else None,
                        "open": float(r[6]) if r[6] is not None else None,
                        "high": float(r[7]) if r[7] is not None else None,
                        "low": float(r[8]) if r[8] is not None else None,
                        "close": float(r[5]) if r[5] is not None else None,
                        "vwap": float(r[9]) if r[9] is not None else 0.0,
                        "volume_continuous": int(r[10]) if r[10] is not None else 0,
                        "volume_atc": int(r[11]) if r[11] is not None else 0,
                        "volume_ato": int(r[12]) if r[12] is not None else 0,
                        "volume_total": int(r[13]) if r[13] is not None else 0,
                        "foreign_net_vol": int(r[16]) if r[16] is not None else 0,
                        "adtv20_continuous": float(r[17]) if r[17] is not None else 0.0,
                        "market_cap": float(r[18]) if r[18] is not None else 0.0,
                    }
                    for r in rows
                ]
            else:
                # Fallback sang bảng ohlcv nếu market_data_daily chưa được backfill cho ticker này
                ohlcv_rows = self.get_ohlcv(
                    ticker, start_date=start_date, end_date=end_date, limit=limit
                )
                if ohlcv_rows:
                    return [
                        {
                            "date": o["time"][:10],
                            "open": o["open"],
                            "high": o["high"],
                            "low": o["low"],
                            "close_unadj": o["close"],
                            "close": o["close"],
                            "vwap": o["close"],
                            "volume_continuous": o["volume"],
                            "volume_atc": 0,
                            "volume_ato": 0,
                            "volume_total": o["volume"],
                            "foreign_net_vol": 0,
                            "adtv20_continuous": 0.0,
                            "market_cap": 0.0,
                        }
                        for o in ohlcv_rows
                    ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc market_data_daily cho {ticker} ({e})")
            # Fallback sang ohlcv khi gặp lỗi truy vấn
            try:
                ohlcv_rows = self.get_ohlcv(
                    ticker, start_date=start_date, end_date=end_date, limit=limit
                )
                if ohlcv_rows:
                    return [
                        {
                            "date": o["time"][:10],
                            "open": o["open"],
                            "high": o["high"],
                            "low": o["low"],
                            "close_unadj": o["close"],
                            "close": o["close"],
                            "vwap": o["close"],
                            "volume_continuous": o["volume"],
                            "volume_atc": 0,
                            "volume_ato": 0,
                            "volume_total": o["volume"],
                            "foreign_net_vol": 0,
                            "adtv20_continuous": 0.0,
                            "market_cap": 0.0,
                        }
                        for o in ohlcv_rows
                    ]
            except Exception:
                pass
        return []

    def get_previous_close(self, symbol: str, before_date: date) -> Optional[float]:
        """Lấy giá đóng cửa phiên gần nhất trước ngày được chỉ định (Giá tham chiếu sàn)."""
        symbol = symbol.upper().strip()
        try:
            # 1. Ưu tiên market_data_daily
            rows = self.storage.fetch_all(
                """
                SELECT close FROM market_data_daily_calculation
                WHERE ticker = %s AND date < %s
                ORDER BY date DESC LIMIT 1
                """,
                (symbol, before_date),
            )
            if rows and rows[0][0] is not None:
                return float(rows[0][0])
            # 2. Fallback sang ohlcv
            rows_ohlcv = self.storage.fetch_all(
                """
                SELECT close FROM ohlcv_unadjusted
                WHERE symbol = %s AND time < %s
                ORDER BY time DESC LIMIT 1
                """,
                (symbol, before_date),
            )
            if rows_ohlcv and rows_ohlcv[0][0] is not None:
                return float(rows_ohlcv[0][0])
        except Exception as exc:
            logger.warning("Could not get previous close for %s: %s", symbol, exc)
        return None

    def get_intraday_1m_bar(
        self,
        symbol: str,
        bar_time: datetime,
        fallback_prev_close: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Lấy cây nến 1 phút từ bảng ohlcv_intraday_1m theo chiến lược 3 lớp (LOCF - As-Of Join):
        1. Ưu tiên nến khớp đúng phút bar_time (ví dụ 09:45:00).
        2. Nếu phút đó không có giao dịch: lùi lại tìm nến gần nhất trong cùng phiên sáng (09:00 -> bar_time).
        3. Nếu cả phiên sáng không có giao dịch: lấy giá đóng cửa phiên hôm trước (Previous Close / Tham chiếu) với volume = 0.
        """
        symbol = symbol.upper().strip()
        try:
            # Lớp 1: Khớp đúng phút bar_time
            rows = self.storage.fetch_all(
                """
                SELECT time, symbol, open, high, low, close, volume, data_source
                FROM ohlcv_intraday_1m
                WHERE symbol = %s AND time >= %s AND time < %s
                ORDER BY time LIMIT 1
                """,
                (symbol, bar_time, bar_time + timedelta(minutes=1)),
            )
            if rows and rows[0]:
                r = rows[0]
                is_ff = (r[7] == "FORWARD_FILL") if len(r) > 7 else False
                return {
                    "time": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                    "symbol": symbol,
                    "open": float(r[2]),
                    "high": float(r[3]),
                    "low": float(r[4]),
                    "close": float(r[5]),
                    "volume": int(r[6]),
                    "data_source": r[7] if len(r) > 7 else "DNSE",
                    "is_synthetic": is_ff,
                }

            # Lớp 2: Lùi lại tìm nến khớp gần nhất trong cùng phiên giao dịch (LOCF - Last Observation Carried Forward)
            session_start = bar_time.replace(hour=9, minute=0, second=0, microsecond=0)
            rows_backward = self.storage.fetch_all(
                """
                SELECT time, symbol, open, high, low, close, volume, data_source
                FROM ohlcv_intraday_1m
                WHERE symbol = %s AND time < %s AND time >= %s AND data_source != 'FORWARD_FILL'
                ORDER BY time DESC LIMIT 1
                """,
                (symbol, bar_time, session_start),
            )
            if rows_backward and rows_backward[0]:
                rb = rows_backward[0]
                last_price = float(rb[5])  # Lấy giá Close của nến gần nhất làm thị giá hiện tại
                return {
                    "time": bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time),
                    "symbol": symbol,
                    "open": last_price,
                    "high": last_price,
                    "low": last_price,
                    "close": last_price,
                    "volume": 0,
                    "data_source": "LOCF_INTRADAY",
                    "is_synthetic": True,
                    "last_traded_at": rb[0].isoformat() if hasattr(rb[0], "isoformat") else str(rb[0]),
                }
        except Exception as exc:
            logger.warning("Could not load intraday 1m bar for %s: %s", symbol, exc)

        # Lớp 3: Fallback lấy giá đóng cửa phiên hôm trước (Giá tham chiếu) khi mã chưa hề giao dịch trong sáng nay
        if fallback_prev_close:
            prev_close = self.get_previous_close(symbol, bar_time.date())
            if prev_close and prev_close > 0:
                return {
                    "time": bar_time.isoformat() if hasattr(bar_time, "isoformat") else str(bar_time),
                    "symbol": symbol,
                    "open": prev_close,
                    "high": prev_close,
                    "low": prev_close,
                    "close": prev_close,
                    "volume": 0,
                    "data_source": "PREV_CLOSE_FORWARD_FILL",
                    "is_synthetic": True,
                }

        return None

    def get_intraday_open(self, symbol: str, bar_time: datetime) -> Optional[float]:
        """Return the stored 1-minute bar open beginning at the requested replay time."""
        bar = self.get_intraday_1m_bar(symbol, bar_time, fallback_prev_close=False)
        return bar["open"] if bar else None

    def get_replay_market_price(self, symbol: str, replay_at: datetime) -> Optional[Dict[str, Any]]:
        """Lấy giá thị trường tại thời điểm Replay (09:45) theo chuẩn 3 lớp LOCF:
        1. Nến đúng phút replay_at hoặc nến lùi lại trong cùng phiên (LOCF).
        2. Snapshot sổ lệnh (DNSE_QUOTE_MIDPOINT).
        3. Giá đóng cửa ngày hôm trước (PREV_CLOSE_FORWARD_FILL).
        """
        bar = self.get_intraday_1m_bar(symbol, replay_at, fallback_prev_close=False)
        if bar and bar.get("open") and bar["open"] > 0:
            source = "DNSE_1M_OPEN" if not bar.get("is_synthetic") else bar.get("data_source", "LOCF_INTRADAY")
            return {
                "price": bar["open"],
                "source": source,
                "volume": bar.get("volume", 0),
                "is_synthetic": bar.get("is_synthetic", False),
            }

        try:
            rows = self.storage.fetch_all(
                """
                SELECT quote_time, bid, offer,
                       EXTRACT(EPOCH FROM (replay_at - quote_time))
                FROM market_data_quote_snapshots
                WHERE symbol = %s AND replay_at = %s AND quote_time <= replay_at
                """,
                (symbol.upper().strip(), replay_at),
            )
            if rows:
                quote_time, bid, offer, age_seconds = rows[0]
                if isinstance(bid, str):
                    bid = json.loads(bid)
                if isinstance(offer, str):
                    offer = json.loads(offer)
                if bid and offer:
                    bid_price = float(bid[0]["price"])
                    ask_price = float(offer[0]["price"])
                    if 0 < bid_price <= ask_price:
                        return {
                            "price": (bid_price + ask_price) / 2,
                            "source": "DNSE_QUOTE_MIDPOINT",
                            "quote_time": quote_time.isoformat(),
                            "age_seconds": float(age_seconds),
                        }
        except Exception as exc:
            logger.warning("Could not load historical quote for %s: %s", symbol, exc)

        # Lớp 3: Forward-fill từ Giá Tham Chiếu / Previous Close khi chưa có khớp lệnh trong cả sáng nay
        prev_close = self.get_previous_close(symbol, replay_at.date())
        if prev_close and prev_close > 0:
            return {
                "price": prev_close,
                "source": "PREV_CLOSE_FORWARD_FILL",
                "volume": 0,
                "is_synthetic": True,
            }

        return None

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
            if rows and len(rows) > 0:
                raw_ind = rows[0][0]
                if isinstance(raw_ind, dict):
                    return raw_ind
                elif isinstance(raw_ind, str):
                    try:
                        return json.loads(raw_ind)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Lỗi khi đọc technical_indicators cho {symbol} ({e})")
        return None

    def get_latest_technical_indicators_for_symbols(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Read each symbol's latest stored technical row in one query."""
        normalized = sorted({symbol.upper().strip() for symbol in symbols if symbol})
        if not normalized:
            return {}
        try:
            rows = self.storage.fetch_all(
                """
                SELECT DISTINCT ON (symbol) symbol, indicators
                FROM technical_indicators
                WHERE symbol = ANY(%s)
                ORDER BY symbol, calc_date DESC
                """,
                (normalized,),
            )
        except Exception as exc:
            logger.warning("Could not load screener technical indicators: %s", exc)
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for symbol, indicators in rows:
            if isinstance(indicators, str):
                try:
                    indicators = json.loads(indicators)
                except json.JSONDecodeError:
                    continue
            if isinstance(indicators, dict):
                result[str(symbol).upper()] = indicators
        return result

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
            logger.debug(f"Không thể đọc từ market_regime ({e}), thử đọc market_regimes fallback")

        # Fallback sang bảng market_regimes (plural) nếu market_regime rỗng hoặc chưa có dữ liệu
        try:
            query_plural = """
                SELECT date, current_regime, breadth_above_ma50_pct
                FROM market_regimes
                ORDER BY date DESC
                LIMIT 1
            """
            rows_plural = self.storage.fetch_all(query_plural)
            if rows_plural and len(rows_plural) > 0:
                r_p = rows_plural[0]
                b50 = float(r_p[2]) / 100.0 if r_p[2] is not None and float(r_p[2]) > 1.0 else (float(r_p[2]) if r_p[2] is not None else 0.5)
                return {
                    "date": r_p[0].isoformat() if hasattr(r_p[0], "isoformat") else str(r_p[0]),
                    "regime_label": str(r_p[1]) if r_p[1] else "BULL_MARKET",
                    "breadth_ma50": b50,
                    "breadth_ma200": 0.5,
                    "breadth_rsi_oversold": 0.0,
                    "breadth_rsi_overbought": 0.0,
                    "volume_ratio": 1.0,
                    "net_foreign_flow_bil": 0.0,
                }
        except Exception:
            pass

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
        socket_only: bool = False,
    ) -> Optional[float]:
        """
        Lấy giá thị trường:
        1. Ưu tiên 1: Giá khớp realtime từ DNSE WebSocket Stream Hub (lưu trong Redis `stock:{symbol}:quote`).
        2. Ưu tiên 2: Giá nến 1 phút realtime gần nhất từ DNSE REST API (DnseIntradayTool).
        3. Ưu tiên 3 (Ngoài giờ giao dịch): Giá đóng cửa ngày hôm qua từ CSDL (market_data_daily / ohlcv).
        """
        symbol_clean = str(symbol).upper().strip()

        def fresh_socket_quote(quote: dict) -> bool:
            try:
                updated = datetime.fromisoformat(str(quote["lastUpdate"]).replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
                age = (datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")) - updated).total_seconds()
                return -1 <= age <= 10
            except (KeyError, TypeError, ValueError):
                return False

        # 1. DNSE WebSocket Stream Hub In-Memory Cache (0ms latency)
        try:
            from app.infrastructure.external_api.dnse.stream_hub import get_stream_hub
            hub = get_stream_hub()
            quote = hub.get_quote(symbol_clean)
            if quote and (not socket_only or fresh_socket_quote(quote)):
                price_raw = float(quote.get("price", 0.0) or quote.get("matchPrice", 0.0) or 0.0)
                if price_raw > 0:
                    price = price_raw * 1000.0 if price_raw < 1000.0 else price_raw
                    logger.debug(f"[DNSE StreamHub] Lấy giá realtime từ In-Memory Hub cho {symbol_clean}: {price:,.0f} VND")
                    return price
        except Exception as e:
            logger.debug(f"Không thể đọc quote từ StreamHub ({e})")

        # 2. DNSE Realtime WebSocket qua Redis Cache
        try:
            from app.infrastructure.external_api.dnse.redis_pub import get_redis
            import json
            r = get_redis()
            cached_data = r.get(f"stock:{symbol_clean}:quote")
            if cached_data:
                quote = json.loads(cached_data)
                price = float(quote.get("price", 0.0)) if not socket_only or fresh_socket_quote(quote) else 0.0
                if price > 0:
                    logger.debug(f"[DNSE Realtime] Lấy giá khớp realtime từ Redis cho {symbol_clean}: {price:,} VND")
                    return price
        except Exception as e:
            logger.debug(f"Không thể đọc quote realtime từ Redis ({e})")

        if socket_only:
            return None

        # 2. DNSE OpenAPI Security Info (Trực tiếp từ openapi.dnse.com.vn, phản hồi ~20ms)
        try:
            from app.infrastructure.external_api.dnse.rest_client import get_rest_client
            rc = get_rest_client()
            if rc.is_live:
                info = rc.get_security_info(symbol_clean)
                price_raw = float(info.get("price", 0.0) or 0.0)
                if price_raw > 0:
                    price = price_raw * 1000.0 if price_raw < 1000.0 else price_raw
                    logger.debug(f"[DNSE OpenAPI] Lấy giá khớp realtime trực tiếp từ DNSE cho {symbol_clean}: {price:,.0f} VND")
                    return price
        except Exception as e:
            logger.debug(f"Không thể đọc security_info từ DNSE OpenAPI ({e})")

        # 3. DNSE REST API Intraday (Nến 1m)
        try:
            from app.infrastructure.external_api.dnse.intraday_tool import DnseIntradayTool
            tool = DnseIntradayTool()
            intraday_candles = tool.fetch(symbol_clean, resolution="1")
            if intraday_candles and len(intraday_candles) > 0:
                latest_candle = intraday_candles[-1]
                price = float(latest_candle.get("close", 0.0))
                if price > 0:
                    price = price * 1000.0 if price < 1000.0 else price
                    logger.debug(f"[DNSE REST] Lấy giá 1m realtime từ DNSE REST cho {symbol_clean}: {price:,} VND")
                    return price
        except Exception as e:
            logger.debug(f"Không thể đọc intraday từ DNSE REST ({e})")

        # 3. Fallback: Giá đóng cửa ngày hôm qua từ PostgreSQL
        if allow_eod_fallback:
            daily = self.get_market_data_daily(symbol_clean, limit=1)
            if daily:
                eod_close = daily[0].get("close_unadj")
                if eod_close and float(eod_close) > 0:
                    price = float(eod_close)
                    if price < 1000.0:  # Chuẩn hóa đơn vị nghìn đồng sàn HOSE sang VND
                        price = price * 1000.0
                    logger.info(f"[EOD Fallback] Sử dụng giá đóng cửa unadjusted gần nhất ({price:,.0f} VND) cho {symbol_clean}.")
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
