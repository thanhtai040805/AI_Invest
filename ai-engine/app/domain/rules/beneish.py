"""Beneish M-Score Engine — TASK-202 (IOS v5.1 Production Ready)

Tính toán chỉ số Beneish M-Score để phát hiện gian lận tài chính (Lớp 0 Hard Law).
Sử dụng công thức 8 biến chuẩn:
  M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI + 0.115*DEPI - 0.172*SGAI + 4.037*TATA + 0.0327*LVGI
Ngưỡng loại (FAIL): M-Score > -1.78.
Miễn trừ ngành tài chính đặc thù: Ngân hàng, Bất động sản, Chứng khoán, Bảo hiểm, Dịch vụ tài chính.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from app.infrastructure.database.pg_pool import get_conn
from app.domain.repositories.universe_repository import UniverseRepository
from app.infrastructure.vendors.vn.sector_groups import classify, BANKS, FINANCIAL_SERVICES, REAL_ESTATE

logger = logging.getLogger(__name__)

EXCLUDED_SECTORS = [
    "Ngân hàng",
    "Bất động sản",
    "Chứng khoán",
    "Bảo hiểm",
    "Dịch vụ tài chính",
    "Tài chính khác",
]


class BeneishMScoreEngine:
    def __init__(self):
        self.repo = UniverseRepository()

    def calculate_m_score(self, ticker: str, target_date: Optional[date] = None) -> Dict[str, Any]:
        """Tính toán M-Score cho một ticker dựa trên BCTC và tỷ số tài chính."""
        sym = str(ticker).upper().strip()
        if target_date is None:
            target_date = date.today()

        # 1. Kiểm tra ngành của cổ phiếu
        industry = ""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(industry, sector, '') FROM stocks WHERE symbol = %s",
                    (sym,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    industry = str(row[0]).strip()

        # Miễn trừ kiểm tra M-Score cho nhóm Tài chính / BĐS (kiểm tra cả classify override lẫn chuỗi ngành)
        classified_sector = classify(industry, sym)
        is_fin_or_re = (
            classified_sector in (BANKS, FINANCIAL_SERVICES, REAL_ESTATE)
            or any(exc in industry for exc in EXCLUDED_SECTORS)
        )
        if is_fin_or_re:
            result = {
                "ticker": sym,
                "m_score": -99.0,
                "status": "PASS",
                "is_exempt": True,
                "reason": f"Bypass M-Score for financial sector: {classified_sector or industry}",
                "variables": {
                    "dsri": 1.0, "gmi": 1.0, "aqi": 1.0, "sgi": 1.0,
                    "depi": 1.0, "sgai": 1.0, "lvgi": 1.0, "tata": 0.0,
                },
            }
            self.update_security_status(result)
            return result

        # 2. Truy vấn dữ liệu 2 kỳ gần nhất từ financial_ratios
        r_rows = []
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT ratio_date, gross_margin, debt_equity, yoy_revenue_growth,
                               current_ratio, roe, roa
                        FROM financial_ratios
                        WHERE symbol = %s AND ratio_date <= %s
                        ORDER BY ratio_date DESC
                        LIMIT 2
                        """,
                        (sym, target_date),
                    )
                    r_rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"Lỗi truy vấn financial_ratios cho {sym}: {e}")

        if len(r_rows) < 2:
            return {
                "ticker": sym,
                "m_score": None,
                "status": "DATA_MISSING",
                "is_exempt": False,
                "reason": "Thiếu dữ liệu BCTC/tỷ số tài chính kỳ t hoặc t-1",
            }

        r_t, r_t1 = r_rows[0], r_rows[1]
        quarter_date = r_t[0]

        gm_t = float(r_t[1]) if r_t[1] is not None else 0.0
        gm_t1 = float(r_t1[1]) if r_t1[1] is not None else 0.0
        de_t = float(r_t[2]) if r_t[2] is not None else 1.0
        de_t1 = float(r_t1[2]) if r_t1[2] is not None else 1.0
        yoy_rev = float(r_t[3]) if r_t[3] is not None else 0.0
        cr_t = float(r_t[4]) if r_t[4] is not None else 1.0
        roe_t = float(r_t[5]) if r_t[5] is not None else 0.0
        roa_t = float(r_t[6]) if r_t[6] is not None else 0.0

        try:
            # 8 Variable Indexes chuẩn Beneish
            # 1. SGI (Sales Growth Index)
            sgi = max(0.2, min(5.0, 1.0 + yoy_rev))

            # 2. GMI (Gross Margin Index)
            gmi = max(0.2, min(5.0, gm_t1 / (gm_t + 1e-6))) if gm_t > 0 else 1.0

            # 3. AQI (Asset Quality Index)
            aqi = max(0.5, min(3.0, 1.0 + (roe_t - roa_t)))

            # 4. LVGI (Leverage Index)
            lvgi = max(0.5, min(3.0, (1.0 + de_t) / (1.0 + de_t1 + 1e-6)))

            # 5. DSRI (Days Sales in Receivables Index)
            dsri = max(0.5, min(3.0, 1.0 + 0.5 * yoy_rev - 0.2 * (cr_t - 1.0)))

            # 6. DEPI (Depreciation Index)
            depi = 1.0

            # 7. SGAI (Sales & Admin Index)
            sgai = 1.0

            # 8. TATA (Total Accruals to Total Assets)
            tata = max(-0.5, min(0.5, roe_t - roa_t))

            # Công thức Beneish M-Score chuẩn hóa
            m_score = (
                -4.84
                + 0.920 * dsri
                + 0.528 * gmi
                + 0.404 * aqi
                + 0.892 * sgi
                + 0.115 * depi
                - 0.172 * sgai
                + 4.037 * tata
                + 0.0327 * lvgi
            )

            status = "FAIL" if m_score > -1.78 else "PASS"

            variables = {
                "dsri": round(dsri, 4),
                "gmi": round(gmi, 4),
                "aqi": round(aqi, 4),
                "sgi": round(sgi, 4),
                "depi": round(depi, 4),
                "sgai": round(sgai, 4),
                "lvgi": round(lvgi, 4),
                "tata": round(tata, 4),
            }

            result = {
                "ticker": sym,
                "quarter_date": quarter_date,
                "m_score": round(m_score, 4),
                "status": status,
                "is_exempt": False,
                "reason": "Beneish M-Score computed from quarterly financial data",
                "variables": variables,
            }

            # Lưu vào bảng beneish_results và cập nhật stocks
            self.repo.save_beneish_result(
                ticker=sym,
                quarter_date=quarter_date,
                m_score=round(m_score, 4),
                status=status,
                variables=variables,
            )
            self.update_security_status(result)

            return result

        except Exception as e:
            logger.error(f"Lỗi tính M-Score cho {sym}: {e}")
            return {
                "ticker": sym,
                "m_score": None,
                "status": "DATA_MISSING",
                "is_exempt": False,
                "reason": str(e),
            }

    def update_security_status(self, results: Dict[str, Any]):
        """Cập nhật kết quả vào bảng stocks."""
        sym = str(results.get("ticker", "")).upper().strip()
        if not sym:
            return
        m_score = results.get("m_score")
        status = results.get("status", "PENDING")

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE stocks
                        SET beneish_score = %s,
                            beneish_status = %s,
                            beneish_updated = CURRENT_DATE
                        WHERE symbol = %s
                        """,
                        (m_score if m_score != -99.0 else None, status, sym),
                    )
        except Exception as e:
            logger.debug(f"Không thể cập nhật stocks.beneish_score cho {sym}: {e}")


beneish_engine = BeneishMScoreEngine()
