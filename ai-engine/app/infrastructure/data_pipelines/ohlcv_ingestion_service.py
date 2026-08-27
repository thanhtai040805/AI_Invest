"""OHLCV Ingestion Service — TASK-103

Module chịu trách nhiệm lấy dữ liệu OHLCV từ Primary (DNSE) và Fallback (yfinance).
Thực hiện phân tách volume_continuous và volume_atc phục vụ tính toán ADTV20.
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
from app.config.settings import get_settings
from app.infrastructure.external_api.dnse.api.client import DNSEClient
from app.infrastructure.external_api.dnse.intraday_tool import get_intraday_tool

logger = logging.getLogger(__name__)
TZ_VN = timezone(timedelta(hours=7))

class OHLCVIngestionService:
    def __init__(self):
        self.settings = get_settings()
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
        self._dnse_client = None

    @property
    def dnse_client(self):
        if self._dnse_client is None:
            self._dnse_client = DNSEClient(
                api_key=self.settings.dnse_api_key,
                api_secret=self.settings.dnse_api_secret,
                base_url=self.settings.dnse_base_url,
            )
        return self._dnse_client

    def fetch_ohlcv(
        self, 
        symbol: str, 
        start_date: date, 
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Lấy dữ liệu OHLCV cho một ticker trong khoảng thời gian."""
        if symbol.upper() == 'VNINDEX':
            return self._fetch_vnindex_from_vietfin(start_date, end_date)
            
        # CHỈ lấy từ DNSE (Primary) - Theo mandate: Không dùng fallback data
        data = self._fetch_from_dnse(symbol, start_date, end_date)
        return data

    def _fetch_vnindex_from_vietfin(self, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """Lấy dữ liệu VNINDEX từ thư viện vietfin (bao bọc API DNSE)."""
        try:
            from vietfin import vf
            import math
            
            res = vf.index.price.historical(
                symbol='vnindex', 
                start_date=start_date.strftime('%Y-%m-%d'), 
                end_date=end_date.strftime('%Y-%m-%d'), 
                interval='1d', 
                provider='dnse'
            )
            df = res.to_df()
            if df is None or df.empty:
                return []
                
            df = df.reset_index()
            results = []
            
            for _, row in df.iterrows():
                close_val = float(row['close']) if not math.isnan(row.get('close', 0)) else 0.0
                open_val = float(row.get('open', close_val))
                high_val = float(row.get('high', close_val))
                low_val = float(row.get('low', close_val))
                vol = int(row.get('volume', 0))
                
                # df['date'] might be string or timestamp, convert to date
                if hasattr(row['date'], 'date'):
                    dt = row['date'].date()
                elif isinstance(row['date'], str):
                    dt = datetime.strptime(row['date'][:10], '%Y-%m-%d').date()
                else:
                    dt = row['date']
                    
                results.append({
                    "ticker": 'VNINDEX',
                    "date": dt,
                    "open": open_val,
                    "high": high_val,
                    "low": low_val,
                    "close": close_val,
                    "volume_total": vol,
                    "data_source": "vietfin"
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching VNINDEX from vietfin: {e}")
            return []

    def _fetch_from_dnse(self, symbol: str, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """Lấy dữ liệu từ DNSE REST API."""
        try:
            # Chuyển đổi sang timestamp VN
            from_ts = int(datetime.combine(start_date, datetime.min.time()).replace(tzinfo=TZ_VN).timestamp())
            to_ts = int(datetime.combine(end_date, datetime.max.time()).replace(tzinfo=TZ_VN).timestamp())
            
            status, body = self.dnse_client.get_ohlc(
                bar_type="STOCK",
                query={
                    "symbol": symbol.upper(),
                    "resolution": "1D",
                    "from": from_ts,
                    "to": to_ts,
                }
            )
            
            if status != 200 or not body:
                return []
                
            import json
            if isinstance(body, str):
                body = json.loads(body)
                
            if not body.get('t'):
                return []
                
            results = []
            for i in range(len(body['t'])):
                dt = datetime.fromtimestamp(body['t'][i], tz=TZ_VN).date()
                results.append({
                    "ticker": symbol.upper(),
                    "date": dt,
                    "open": float(body['o'][i]),
                    "high": float(body['h'][i]),
                    "low": float(body['l'][i]),
                    "close": float(body['c'][i]),
                    "volume_total": int(body['v'][i]),
                    "data_source": "dnse"
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching from DNSE for {symbol}: {e}")
            return []

    def fetch_intraday_for_volume_split(self, symbol: str, target_date: date) -> Dict[str, int]:
        """Lấy dữ liệu intraday 1m để phân tách volume continuous vs ATC.
        
        Nguyên tắc: Candle cuối cùng (thường là 14:45:00) chứa volume ATC.
        Các candle trước đó (đến 14:30:00) là volume continuous.
        """
        try:
            tool = get_intraday_tool()
            from_ts = int(datetime.combine(target_date, datetime.min.time()).replace(tzinfo=TZ_VN).timestamp())
            to_ts = int(datetime.combine(target_date, datetime.max.time()).replace(tzinfo=TZ_VN).timestamp())
            
            candles = tool.fetch(symbol.upper(), resolution="1", from_ts=from_ts, to_ts=to_ts)
            
            if not candles:
                return {"continuous": 0, "atc": 0, "ato": 0}
                
            # Phân tích volume dựa trên timestamp (giờ VN)
            # ATO: ~09:15:00
            # Continuous: 09:15:01 -> 14:30:00
            # ATC: 14:45:00
            
            ato_vol = 0
            atc_vol = 0
            cont_vol = 0
            
            for c in candles:
                # time format: 2026-06-15T09:15:00Z (UTC)
                dt = datetime.fromisoformat(c['time'].replace('Z', '+00:00')).astimezone(TZ_VN)
                time_str = dt.strftime("%H:%M:%S")
                vol = c.get('volume', 0)
                
                if time_str == "09:15:00":
                    ato_vol = vol
                elif time_str == "14:45:00":
                    atc_vol = vol
                elif "09:15:00" < time_str <= "14:30:00":
                    cont_vol += vol
                    
            return {
                "continuous": cont_vol,
                "atc": atc_vol,
                "ato": ato_vol
            }
        except Exception as e:
            logger.warning(f"Could not fetch intraday for volume split {symbol} on {target_date}: {e}")
            return {"continuous": 0, "atc": 0, "ato": 0}

    def save_market_data(self, data: List[Dict[str, Any]]) -> int:
        """Lưu dữ liệu vào bảng market_data_daily."""
        if not data:
            return 0
            
        import psycopg2
        from psycopg2.extras import execute_values
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        # Chuẩn bị dữ liệu cho bulk insert
        rows = []
        for d in data:
            # Nếu chưa có volume separation, thử lấy (giới hạn cho ngày gần nhất để tránh overload)
            v_split = d.get("v_split")
            if not v_split and d["date"] >= (date.today() - timedelta(days=2)):
                v_split = self.fetch_intraday_for_volume_split(d["ticker"], d["date"])
            
            v_cont = v_split.get("continuous", d["volume_total"]) if v_split else d["volume_total"]
            v_atc = v_split.get("atc", 0) if v_split else 0
            v_ato = v_split.get("ato", 0) if v_split else 0
            
            rows.append((
                d["ticker"],
                d["date"],
                d["open"],  # open_adj (khởi tạo bằng unadj, TASK-102 sẽ adjust sau)
                d["high"],
                d["low"],
                d["close"],
                d["close"], # close_unadj
                d["close"], # vwap (khởi tạo tạm)
                v_cont,
                v_atc,
                v_ato,
                d["volume_total"],
                d["data_source"]
            ))
            
        execute_values(cur, """
            INSERT INTO market_data_daily (
                ticker, date, open_adj, high_adj, low_adj, close_adj, close_unadj,
                vwap, volume_continuous, volume_atc, volume_ato, volume_total, data_source
            ) VALUES %s
            ON CONFLICT (ticker, date) DO UPDATE SET
                open_adj = EXCLUDED.open_adj,
                high_adj = EXCLUDED.high_adj,
                low_adj = EXCLUDED.low_adj,
                close_adj = EXCLUDED.close_adj,
                close_unadj = EXCLUDED.close_unadj,
                volume_continuous = EXCLUDED.volume_continuous,
                volume_atc = EXCLUDED.volume_atc,
                volume_ato = EXCLUDED.volume_ato,
                volume_total = EXCLUDED.volume_total,
                data_source = EXCLUDED.data_source
        """, rows)
        
        conn.commit()
        count = cur.rowcount
        cur.close()
        conn.close()
        return count

    def calculate_adtv20_continuous(self, symbol: str, target_date: date) -> float:
        """Tính ADTV20 dựa trên volume_continuous (loại bỏ ATC/ATO)."""
        import psycopg2
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT AVG(volume_continuous)
            FROM (
                SELECT volume_continuous
                FROM market_data_daily
                WHERE ticker = %s AND date <= %s
                ORDER BY date DESC
                LIMIT 20
            ) as last_20
        """, (symbol.upper(), target_date))
        
        result = cur.fetchone()
        adtv = float(result[0]) if result and result[0] else 0.0
        
        # Cập nhật ngược lại vào DB
        cur.execute("""
            UPDATE market_data_daily
            SET adtv20_continuous = %s
            WHERE ticker = %s AND date = %s
        """, (adtv, symbol.upper(), target_date))
        
        conn.commit()
        cur.close()
        conn.close()
        return adtv

ohlcv_ingestion_svc = OHLCVIngestionService()
