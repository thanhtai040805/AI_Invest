"""Factor Service (IOS v5.1 Production Ready)

Tính toán động 6 nhóm nhân tố (F1 Value, F2 Quality, F3 Momentum, F4 Earnings, F5 Flow, F6 Technical)
từ dữ liệu BCTC và biến động thị trường thực tế trong CSDL PostgreSQL.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from app.infrastructure.database.pg_pool import get_conn
from app.application.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


class FactorService:
    def __init__(self, storage: Optional[StoragePort] = None):
        self.storage = storage or PostgresAdapter()

    def compute_factors_for_ticker(
        self, ticker: str, target_date: Optional[date] = None
    ) -> Dict[str, float]:
        """Tính toán động 6 nhóm nhân tố (F1-F6) cho một mã cổ phiếu cụ thể."""
        sym = str(ticker).upper().strip()
        if target_date is None:
            target_date = date.today()

        with get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Lấy tỷ số tài chính mới nhất tính đến target_date
                cur.execute(
                    """
                    SELECT pe, pb, ev_ebitda, roe, roa, debt_equity, current_ratio,
                           gross_margin, net_margin, yoy_revenue_growth, yoy_earnings_growth
                    FROM financial_ratios
                    WHERE symbol = %s AND ratio_date <= %s
                    ORDER BY ratio_date DESC
                    LIMIT 1
                    """,
                    (sym, target_date),
                )
                fin_row = cur.fetchone()

                # 2. Lấy 60 phiên giao dịch gần nhất từ market_data_daily
                cur.execute(
                    """
                    SELECT close_adj, volume_total, foreign_net_vol, date
                    FROM market_data_daily
                    WHERE ticker = %s AND date <= %s
                    ORDER BY date DESC
                    LIMIT 60
                    """,
                    (sym, target_date),
                )
                mkt_rows = cur.fetchall()

        # Dữ liệu mặc định nếu thiếu
        pe = float(fin_row[0] or 15.0) if fin_row else 15.0
        pb = float(fin_row[1] or 2.0) if fin_row else 2.0
        ev_ebitda = float(fin_row[2] or 12.0) if fin_row else 12.0
        roe = float(fin_row[3] or 0.05) if fin_row else 0.05
        roa = float(fin_row[4] or 0.03) if fin_row else 0.03
        debt_equity = float(fin_row[5] or 0.8) if fin_row else 0.8
        net_margin = float(fin_row[8] or 0.10) if fin_row else 0.10
        yoy_rev = float(fin_row[9] or 0.0) if fin_row else 0.0
        yoy_earn = float(fin_row[10] or 0.0) if fin_row else 0.0

        p0 = float(mkt_rows[0][0]) if mkt_rows and mkt_rows[0][0] else 50.0
        p20 = float(mkt_rows[min(19, len(mkt_rows) - 1)][0]) if mkt_rows else p0
        p60 = float(mkt_rows[min(59, len(mkt_rows) - 1)][0]) if mkt_rows else p20

        vols = [float(r[1] or 0.0) for r in mkt_rows] if mkt_rows else [1000000.0]
        vol20 = sum(vols[:20]) / max(1, len(vols[:20]))
        vol60 = sum(vols[:60]) / max(1, len(vols[:60]))
        foreign_vol_20 = sum(float(r[2] or 0.0) for r in mkt_rows[:20]) if mkt_rows else 0.0

        # Tỷ suất sinh lời 1 tháng (20 phiên) và 3 tháng (60 phiên)
        ret_1m = (p0 - p20) / (p20 + 1e-6)
        ret_3m = (p0 - p60) / (p60 + 1e-6)

        # Trung bình động MA20 và MA50
        prices = [float(r[0] or 0.0) for r in mkt_rows] if mkt_rows else [p0]
        ma20 = sum(prices[:20]) / max(1, len(prices[:20]))
        ma50 = sum(prices[:50]) / max(1, len(prices[:50]))

        # --- 1. F1: Value (Thang 10 - 95, P/E và P/B hợp lý) ---
        # Điểm cao khi định giá rẻ (P/E < 15, P/B < 2.0)
        f1_value = max(10.0, min(95.0, 50.0 + (15.0 - pe) * 2.0 + (2.0 - pb) * 10.0))

        # --- 2. F2: Quality (Thang 15 - 95, Hiệu quả sinh lời và đòn bẩy an toàn) ---
        # Điểm cao khi ROE/ROA cao, biên ròng cao và nợ vay thấp
        f2_quality = max(
            15.0,
            min(95.0, 40.0 + (roe * 400.0) - (debt_equity - 0.8) * 15.0 + (net_margin * 100.0)),
        )

        # --- 3. F3: Momentum (Thang 10 - 95, Sức mạnh giá tương đối) ---
        # Điểm cao khi xu hướng tăng giá 1 tháng và 3 tháng tích cực
        f3_momentum = max(10.0, min(95.0, 50.0 + (ret_1m * 150.0) + (ret_3m * 100.0)))

        # --- 4. F4: Earnings (Thang 10 - 95, Tăng trưởng kết quả kinh doanh) ---
        # Điểm cao khi doanh thu và lợi nhuận tăng trưởng cùng kỳ
        f4_earnings = max(10.0, min(95.0, 50.0 + (yoy_rev * 50.0) + (yoy_earn * 50.0)))

        # --- 5. F5: Flow (Thang 15 - 95, Dòng tiền thông minh và khối ngoại) ---
        # Điểm cao khi thanh khoản bùng nổ so với 60 phiên hoặc khối ngoại gom ròng
        flow_trend = (vol20 / (vol60 + 1e-6)) - 1.0
        foreign_bonus = 5.0 if foreign_vol_20 > 0 else (-5.0 if foreign_vol_20 < 0 else 0.0)
        f5_flow = max(15.0, min(95.0, 50.0 + (flow_trend * 50.0) + foreign_bonus))

        # --- 6. F6: Technical (Thang 10 - 95, Vị thế giá so với các đường MA) ---
        # Điểm cao khi giá nằm trên MA20 và MA50
        tech_ma20 = (p0 / (ma20 + 1e-6)) - 1.0
        tech_ma50 = (p0 / (ma50 + 1e-6)) - 1.0
        f6_technical = max(10.0, min(95.0, 50.0 + (tech_ma20 * 200.0) + (tech_ma50 * 100.0)))

        raw_metrics = {
            "pe": pe,
            "pb": pb,
            "roe": roe,
            "de": debt_equity,
            "ret_1m": round(ret_1m, 4),
            "ret_3m": round(ret_3m, 4),
            "vol_20_ratio": round(vol20 / (vol60 + 1e-6), 2),
            "p_vs_ma20": round(tech_ma20, 4),
            "p_vs_ma50": round(tech_ma50, 4),
        }

        return {
            "f1_value": round(f1_value, 2),
            "f2_quality": round(f2_quality, 2),
            "f3_momentum": round(f3_momentum, 2),
            "f4_earnings": round(f4_earnings, 2),
            "f5_flow": round(f5_flow, 2),
            "f6_technical": round(f6_technical, 2),
            "raw_metrics": raw_metrics,
        }

    def compute_daily_factors(self, target_date: date):
        """Tính toán F1-F6 và lưu trữ vào bảng factor_scores cho toàn sàn."""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM stocks WHERE exchange = 'HOSE'")
                symbols = [r[0] for r in cur.fetchall()]

        from app.domain.repositories.intelligence_repository import IntelligenceRepository
        intel_repo = IntelligenceRepository()

        count = 0
        for sym in symbols:
            try:
                factors = self.compute_factors_for_ticker(sym, target_date)
                css = round(
                    0.2 * factors["f1_value"]
                    + 0.2 * factors["f2_quality"]
                    + 0.2 * factors["f3_momentum"]
                    + 0.2 * factors["f4_earnings"]
                    + 0.1 * factors["f5_flow"]
                    + 0.1 * factors["f6_technical"],
                    2,
                )
                conviction = "A" if css >= 70 else ("B" if css >= 60 else ("C" if css >= 50 else "D"))
                intel_repo.save_factor_score(
                    symbol=sym,
                    f1_value=factors["f1_value"],
                    f2_quality=factors["f2_quality"],
                    f3_momentum=factors["f3_momentum"],
                    f4_earnings=factors["f4_earnings"],
                    f5_flow=factors["f5_flow"],
                    f6_technical=factors["f6_technical"],
                    css=css,
                    conviction=conviction,
                    score_date=target_date,
                )
                count += 1
            except Exception as e:
                logger.debug(f"Không thể tính factors cho {sym}: {e}")

        logger.info(f"Hoàn thành tính toán Factor Scores cho {count}/{len(symbols)} mã vào {target_date}")
        return {"symbols_computed": count, "total_symbols": len(symbols), "date": str(target_date)}
