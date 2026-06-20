"""Universe Manager — TASK-201

Quản lý danh mục Universe (A, B, C, Sandbox, Excluded).
Phân loại cổ phiếu dựa trên thanh khoản, vốn hóa và trạng thái giao dịch.
Tuân thủ các quy tắc trong DATA_SCHEMA.md và IMPLEMENTATION_PLAN.md.
"""

import logging
import os
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional

import pandas as pd
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

class UniverseGroup(Enum):
    A = "A"
    B = "B"
    C = "C"
    SANDBOX = "SANDBOX"
    EXCLUDED = "EXCLUDED"

class TradingStatus(Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CONTROLLED = "CONTROLLED"
    SUSPENDED = "SUSPENDED"

class UniverseManager:
    def __init__(self):
        self.settings = get_settings()
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def classify_universe(self, tickers: List[str], target_date: Optional[datetime.date] = None) -> Dict[str, Any]:
        """Phân loại danh sách cổ phiếu vào các nhóm Universe."""
        if target_date is None:
            target_date = datetime.now().date()
            
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Lấy VN30 list
        vn30_tickers = self._get_vn30_list()
        
        results = []
        exclusion_log = []
        
        for ticker in tickers:
            # 2. Lấy dữ liệu cần thiết: trading_status, adtv20_continuous, market_cap, audit_opinion, etc.
            # (Giả định table 'stocks' và 'market_data_daily' đã có data)
            cur.execute("""
                SELECT s.symbol, s.trading_status, s.market_cap as stock_mcap, 
                       m.adtv20_continuous, m.market_cap as market_mcap,
                       s.audit_opinion, s.beneish_status, s.gil_flag
                FROM stocks s
                LEFT JOIN market_data_daily m ON s.symbol = m.ticker AND m.date = %s
                WHERE s.symbol = %s
            """, (target_date, ticker))
            
            data = cur.fetchone()
            if not data:
                logger.warning(f"No data for {ticker} on {target_date}, skipping classification")
                continue
                
            status = data.get("trading_status", "NORMAL").upper()
            adtv20 = data.get("adtv20_continuous") or 0
            mcap = data.get("market_mcap") or data.get("stock_mcap") or 0
            beneish = data.get("beneish_status", "PENDING")
            gil = data.get("gil_flag", "PASS")
            
            group = UniverseGroup.B # Mặc định
            reason = ""
            
            # --- Hard Filters (Điều kiện loại trừ) ---
            if status != "NORMAL":
                group = UniverseGroup.EXCLUDED
                reason = f"Trading status: {status}"
            elif beneish == "FAIL":
                group = UniverseGroup.EXCLUDED
                reason = "Beneish M-Score: FAIL"
            elif gil == "CATASTROPHIC":
                group = UniverseGroup.EXCLUDED
                reason = "GIL Flag: CATASTROPHIC"
                
            if group == UniverseGroup.EXCLUDED:
                exclusion_log.append({"ticker": ticker, "reason": reason, "date": target_date})
            else:
                # --- Classification Logic ---
                # Group A: VN30 members (bắt buộc) hoặc Bluechips cực lớn
                if ticker in vn30_tickers:
                    group = UniverseGroup.A
                elif adtv20 >= 50_000_000_000 and mcap >= 10_000_000_000_000: # Ví dụ: ADTV > 50 tỷ, Cap > 10k tỷ
                    group = UniverseGroup.A
                
                # Group C: Cổ phiếu rác hoặc quá nhỏ
                elif adtv20 < 1_000_000_000 or mcap < 100_000_000_000: # Ví dụ: ADTV < 1 tỷ hoặc Cap < 100 tỷ
                    group = UniverseGroup.C
                
                # Sandbox Logic (4 điều kiện đồng thời)
                # ADTV20 ≥ 2 tỷ, vốn hóa ≥ 300 tỷ, revenue growth > 25% (3 quý), net_debt/equity < 15%
                # (Revenue growth và debt/equity cần data từ TASK-104)
                if self._check_sandbox_criteria(cur, ticker, adtv20, mcap):
                    group = UniverseGroup.SANDBOX

            results.append({
                "ticker": ticker,
                "universe_group": group.value,
                "updated_at": datetime.now()
            })
            
        # 3. Update DB
        self._update_db(cur, results)
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {
            "results": results,
            "exclusion_log": exclusion_log
        }

    def _get_vn30_list(self) -> List[str]:
        """Lấy danh sách VN30 từ vnstock (cached hoặc live)."""
        try:
            from vnstock import Listing
            df = Listing().symbols_by_group("VN30")
            return df.tolist()
        except Exception as e:
            logger.error(f"Error fetching VN30 list: {e}")
            # Fallback list (static - Jun 2026 approximation)
            return ["ACB", "BID", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG", "MBB", "MSN", 
                    "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI", "STB", "TCB", "TPB", 
                    "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE"]

    def _check_sandbox_criteria(self, cur, ticker: str, adtv20: float, mcap: float) -> bool:
        """Kiểm tra 4 điều kiện cho Sandbox group."""
        if adtv20 < 2_000_000_000 or mcap < 300_000_000_000:
            return False
            
        # Lấy financial data (ROIC, Growth, Debt) từ Task 104
        cur.execute("""
            SELECT yoy_revenue_growth, debt_equity
            FROM financial_ratios
            WHERE symbol = %s
            ORDER BY ratio_date DESC
            LIMIT 3
        """, (ticker,))
        ratios = cur.fetchall()
        
        if len(ratios) < 3:
            return False
            
        # Revenue growth > 25% (3 quý liên tiếp)
        growth_ok = all((r.get("yoy_revenue_growth") or 0) > 0.25 for r in ratios)
        # net_debt/equity < 15% (dùng debt_equity làm proxy nếu net_debt chưa có)
        debt_ok = (ratios[0].get("debt_equity") or 1.0) < 0.15
        
        return growth_ok and debt_ok

    def _update_db(self, cur, results: List[Dict]):
        """Cập nhật universe_group vào bảng stocks."""
        for res in results:
            cur.execute("""
                UPDATE stocks
                SET universe_group = %s, group_updated_at = %s
                WHERE symbol = %s
            """, (res["universe_group"], res["updated_at"], res["ticker"]))

universe_manager = UniverseManager()
