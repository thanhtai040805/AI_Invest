"""Beneish M-Score Engine — TASK-202

Tính toán chỉ số Beneish M-Score để phát hiện gian lận tài chính.
Sử dụng công thức 8 biến chuẩn.
Ngưỡng loại (FAIL): M-Score > -1.78.
"""

import logging
import os
import pandas as pd
import numpy as np
from datetime import date
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

class BeneishMScoreEngine:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def calculate_m_score(self, ticker: str, target_date: date) -> Dict[str, Any]:
        """Tính toán M-Score cho một ticker dựa trên BCTC 2 năm gần nhất."""
        EXCLUDED_SECTORS = ["Ngân hàng", "Bất động sản", "Chứng khoán", "Bảo hiểm"]
        
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Lấy BCTC năm hiện tại (t) và năm trước (t-1) cùng với thông tin ngành
        # Ưu tiên báo cáo năm (yearly) để có độ chính xác cao nhất
        cur.execute("""
            SELECT fs.symbol, fs.period_end, fs.data, COALESCE(s.sector, '') as sector
            FROM financial_statements fs
            LEFT JOIN stocks s ON fs.symbol = s.symbol
            WHERE fs.symbol = %s 
              AND fs.period_end <= %s
              AND fs.frequency = 'yearly'
            ORDER BY fs.period_end DESC
            LIMIT 2
        """, (ticker, target_date))
        
        fs_rows = cur.fetchall()
        conn.close()
        
        if len(fs_rows) > 0:
            sector = fs_rows[0].get('sector', '')
            if sector in EXCLUDED_SECTORS:
                return {
                    "ticker": ticker,
                    "m_score": 0.0,
                    "status": "PASS",
                    "reason": f"Bypass M-Score for sector: {sector}"
                }

        if len(fs_rows) < 2:
            return {
                "ticker": ticker,
                "m_score": None,
                "status": "PENDING",
                "reason": "Thiếu BCTC năm t-1"
            }
            
        fs_t = fs_rows[0]['data']
        fs_t1 = fs_rows[1]['data']
        
        try:
            # --- Extract variables (đảm bảo match với keys trong TASK-104/AlphaStock) ---
            # Năm t
            rev_t = fs_t.get('revenue', 0)
            cogs_t = fs_t.get('cogs', 0)
            rec_t = fs_t.get('receivables', 0)
            ca_t = fs_t.get('current_assets', 0)
            ppe_t = fs_t.get('ppe_net', 0)
            assets_t = fs_t.get('total_assets', 1)
            dep_t = fs_t.get('depreciation', 0)
            sga_t = fs_t.get('sga_expense', 0)
            ni_t = fs_t.get('net_income', 0)
            cfo_t = fs_t.get('cfo', 0)
            lt_debt_t = fs_t.get('long_term_debt', 0)
            cl_t = fs_t.get('current_liabilities', 0)
            
            # Năm t-1
            rev_t1 = fs_t1.get('revenue', 1)
            cogs_t1 = fs_t1.get('cogs', 0)
            rec_t1 = fs_t1.get('receivables', 0)
            ca_t1 = fs_t1.get('current_assets', 0)
            ppe_t1 = fs_t1.get('ppe_net', 0)
            assets_t1 = fs_t1.get('total_assets', 1)
            dep_t1 = fs_t1.get('depreciation', 0)
            sga_t1 = fs_t1.get('sga_expense', 0)
            lt_debt_t1 = fs_t1.get('long_term_debt', 0)
            cl_t1 = fs_t1.get('current_liabilities', 0)
            
            # --- 8 Variable Indexes ---
            
            # 1. DSRI (Days Sales in Receivables Index)
            dsri = (rec_t / rev_t) / (rec_t1 / rev_t1) if rev_t > 0 and rev_t1 > 0 else 1.0
            
            # 2. GMI (Gross Margin Index)
            gm_t = (rev_t - cogs_t) / rev_t if rev_t > 0 else 0
            gm_t1 = (rev_t1 - cogs_t1) / rev_t1 if rev_t1 > 0 else 0
            gmi = gm_t1 / gm_t if gm_t > 0 else 1.0
            
            # 3. AQI (Asset Quality Index)
            aq_t = 1 - (ca_t + ppe_t) / assets_t
            aq_t1 = 1 - (ca_t1 + ppe_t1) / assets_t1
            aqi = aq_t / aq_t1 if aq_t1 > 0 else 1.0
            
            # 4. SGI (Sales Growth Index)
            sgi = rev_t / rev_t1 if rev_t1 > 0 else 1.0
            
            # 5. DEPI (Depreciation Index)
            dep_rate_t = dep_t / (ppe_t + dep_t) if (ppe_t + dep_t) > 0 else 0
            dep_rate_t1 = dep_t1 / (ppe_t1 + dep_t1) if (ppe_t1 + dep_t1) > 0 else 0
            depi = dep_rate_t1 / dep_rate_t if dep_rate_t > 0 else 1.0
            
            # 6. SGAI (Sales, General and Administrative expenses Index)
            sgai = (sga_t / rev_t) / (sga_t1 / rev_t1) if rev_t > 0 and rev_t1 > 0 else 1.0
            
            # 7. LVGI (Leverage Index)
            lev_t = (lt_debt_t + cl_t) / assets_t
            lev_t1 = (lt_debt_t1 + cl_t1) / assets_t1
            lvgi = lev_t / lev_t1 if lev_t1 > 0 else 1.0
            
            # 8. TATA (Total Accruals to Total Assets)
            tata = (ni_t - cfo_t) / assets_t
            
            # --- M-Score Formula ---
            m_score = (
                -4.84 + 
                0.92 * dsri + 
                0.528 * gmi + 
                0.404 * aqi + 
                0.892 * sgi + 
                0.115 * depi - 
                0.172 * sgai + 
                4.679 * tata - 
                0.327 * lvgi
            )
            
            status = "FAIL" if m_score > -1.78 else "PASS"
            
            return {
                "ticker": ticker,
                "m_score": round(m_score, 4),
                "status": status,
                "variables": {
                    "dsri": dsri, "gmi": gmi, "aqi": aqi, "sgi": sgi,
                    "depi": depi, "sgai": sgai, "lvgi": lvgi, "tata": tata
                }
            }
            
        except Exception as e:
            logger.error(f"Error calculating M-Score for {ticker}: {e}")
            return {"ticker": ticker, "m_score": None, "status": "PENDING", "reason": str(e)}

    def update_security_status(self, results: Dict[str, Any]):
        """Cập nhật kết quả vào bảng stocks."""
        import psycopg2
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE stocks
            SET beneish_score = %s,
                beneish_status = %s,
                beneish_updated = %s
            WHERE symbol = %s
        """, (results["m_score"], results["status"], date.today(), results["ticker"]))
        
        conn.commit()
        cur.close()
        conn.close()

beneish_engine = BeneishMScoreEngine()
