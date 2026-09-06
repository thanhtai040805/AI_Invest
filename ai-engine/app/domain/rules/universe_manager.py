"""Universe Manager — TASK-201 (IOS v5.1 Production Ready)

Quản lý danh mục Universe (A, B, C, Sandbox, Excluded).
Phân loại cổ phiếu dựa trên thanh khoản, vốn hóa, thời gian niêm yết và trạng thái giao dịch.
Tuân thủ các quy tắc trong DATA_SCHEMA.md và IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.infrastructure.database.pg_pool import get_conn

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

    _vn30_cache: Optional[List[str]] = None

    def _get_vn30_list(self) -> List[str]:
        """Lấy danh sách VN30 với bộ nhớ đệm (cached) tốc độ cao, hoàn toàn offline."""
        if UniverseManager._vn30_cache:
            return UniverseManager._vn30_cache

        UniverseManager._vn30_cache = [
            "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
            "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI", "STB",
            "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE",
        ]
        return UniverseManager._vn30_cache

    def classify_universe(
        self,
        tickers: List[str],
        target_date: Optional[date] = None,
        strategy_mode: str = "Quant",
    ) -> Dict[str, Any]:
        """Phân loại danh sách cổ phiếu vào các nhóm Universe (Group A/B/C/Sandbox/Excluded)."""
        if target_date is None:
            target_date = date.today()

        vn30_set = set(self._get_vn30_list())
        results = []
        exclusion_log = []

        with get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Nạp metadata của toàn bộ danh sách cổ phiếu
                cur.execute(
                    """
                    SELECT symbol, trading_status, market_cap, audit_opinion,
                           beneish_status, gil_flag, COALESCE(industry, sector, '') as industry
                    FROM stocks
                    WHERE symbol = ANY(%s)
                    """,
                    (tickers,),
                )
                stocks_meta = {
                    r[0]: {
                        "trading_status": str(r[1] or "NORMAL").upper().strip(),
                        "market_cap": float(r[2] or 0.0),
                        "audit_opinion": str(r[3] or "UNQUALIFIED").upper().strip(),
                        "beneish_status": str(r[4] or "PASS").upper().strip(),
                        "gil_flag": str(r[5] or "PASS").upper().strip(),
                        "industry": str(r[6] or "").strip(),
                    }
                    for r in cur.fetchall()
                }

                # 2. Tính rolling ADTV20 và ngày niêm yết
                # Lấy 20 ngày giao dịch gần nhất trước target_date
                cur.execute(
                    """
                    WITH recent_days AS (
                        SELECT DISTINCT date 
                        FROM market_data_daily 
                        WHERE date <= %s
                        ORDER BY date DESC 
                        LIMIT 20
                    )
                    SELECT ticker, 
                           AVG(close_adj * volume_total * 1000) as adtv20_vnd,
                           MIN(date) as min_date,
                           COUNT(*) as trade_days
                    FROM market_data_daily
                    WHERE date IN (SELECT date FROM recent_days) AND ticker = ANY(%s)
                    GROUP BY ticker
                    """,
                    (target_date, tickers),
                )
                liquidity_map = {
                    r[0]: {
                        "adtv20": float(r[1] or 0.0),
                        "min_date": r[2],
                        "trade_days": int(r[3] or 0),
                    }
                    for r in cur.fetchall()
                }

                # 3. Lấy ngày giao dịch sớm nhất lịch sử để tính listing_months
                cur.execute(
                    """
                    SELECT ticker, MIN(date), COUNT(*)
                    FROM market_data_daily
                    WHERE ticker = ANY(%s) AND date <= %s
                    GROUP BY ticker
                    """,
                    (tickers, target_date),
                )
                listing_map = {
                    r[0]: {
                        "first_date": r[1],
                        "total_days": int(r[2] or 0),
                    }
                    for r in cur.fetchall()
                }

                for ticker in tickers:
                    sym = str(ticker).upper().strip()
                    meta = stocks_meta.get(sym, {})
                    status = meta.get("trading_status", "NORMAL")
                    mcap = meta.get("market_cap", 0.0)
                    beneish = meta.get("beneish_status", "PASS")
                    gil = meta.get("gil_flag", "PASS")
                    audit = meta.get("audit_opinion", "UNQUALIFIED")

                    liq_info = liquidity_map.get(sym, {})
                    adtv20 = liq_info.get("adtv20", 0.0)

                    list_info = listing_map.get(sym, {})
                    total_days = list_info.get("total_days", 0)
                    first_date = list_info.get("first_date")

                    listed_months = 0.0
                    if first_date and target_date:
                        listed_months = (target_date - first_date).days / 30.0
                    elif total_days >= 250:
                        listed_months = 12.0

                    group = UniverseGroup.B
                    reason = ""

                    # --- Hard Filters (Điều kiện loại trừ bất biến) ---
                    if status not in ("NORMAL", ""):
                        group = UniverseGroup.EXCLUDED
                        reason = f"Trading status: {status}"
                    elif audit != "UNQUALIFIED":
                        group = UniverseGroup.EXCLUDED
                        reason = f"Audit Opinion: {audit}"
                    elif gil == "CATASTROPHIC":
                        group = UniverseGroup.EXCLUDED
                        reason = "GIL Flag: CATASTROPHIC"
                    elif beneish == "FAIL":
                        group = UniverseGroup.EXCLUDED
                        reason = "Beneish M-Score: FAIL"
                    elif adtv20 < 15_000_000_000 and sym not in vn30_set:
                        group = UniverseGroup.EXCLUDED
                        reason = f"ADTV20 ({adtv20:,.0f} VND) < 15B"
                    elif strategy_mode == "Quant" and listed_months < 12.0:
                        group = UniverseGroup.EXCLUDED
                        reason = f"Listed {listed_months:.1f} months < 12 months for Quant strategy"

                    if group == UniverseGroup.EXCLUDED:
                        exclusion_log.append({
                            "ticker": sym,
                            "reason": reason,
                            "date": str(target_date),
                        })
                    else:
                        # Phân loại nhóm Universe
                        if sym in vn30_set or (adtv20 >= 50_000_000_000 and mcap >= 10_000_000_000_000):
                            group = UniverseGroup.A
                        elif adtv20 < 20_000_000_000:
                            group = UniverseGroup.C

                        # Sandbox Criteria (tăng trưởng cao)
                        if self._check_sandbox_criteria(cur, sym, adtv20, mcap):
                            group = UniverseGroup.SANDBOX

                    results.append({
                        "ticker": sym,
                        "universe_group": group.value,
                        "trading_status": status,
                        "adtv20": adtv20,
                        "market_cap": mcap,
                        "beneish_status": beneish,
                        "gil_flag": gil,
                        "updated_at": datetime.now(),
                    })

                # 4. Ghi nhận kết quả vào CSDL
                self._update_db(cur, results)

        return {
            "results": results,
            "exclusion_log": exclusion_log,
        }

    def _check_sandbox_criteria(self, cur, ticker: str, adtv20: float, mcap: float) -> bool:
        """Kiểm tra 4 điều kiện cho Sandbox group."""
        if adtv20 < 2_000_000_000 or mcap < 300_000_000_000:
            return False

        try:
            cur.execute(
                """
                SELECT yoy_revenue_growth, debt_equity
                FROM financial_ratios
                WHERE symbol = %s
                ORDER BY ratio_date DESC
                LIMIT 3
                """,
                (ticker,),
            )
            ratios = cur.fetchall()
            if len(ratios) < 3:
                return False

            growth_ok = all((r[0] or 0) > 0.25 for r in ratios)
            debt_ok = (ratios[0][1] or 1.0) < 0.15
            return growth_ok and debt_ok
        except Exception:
            return False

    def _update_db(self, cur, results: List[Dict[str, Any]]):
        """Cập nhật universe_group vào cả hai bảng stocks và universe_securities."""
        for res in results:
            sym = res["ticker"]
            ugroup = res["universe_group"]
            t_status = res.get("trading_status", "NORMAL")
            b_status = res.get("beneish_status", "PASS")
            g_flag = res.get("gil_flag", "PASS")

            # 1. Update stocks table
            cur.execute(
                """
                UPDATE stocks
                SET universe_group = %s, group_updated_at = NOW()
                WHERE symbol = %s
                """,
                (ugroup, sym),
            )

            # 2. Upsert universe_securities table
            cur.execute(
                """
                INSERT INTO universe_securities (
                    ticker, universe_group, trading_status, beneish_status, gil_flag, updated_at
                ) VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (ticker) DO UPDATE SET
                    universe_group = EXCLUDED.universe_group,
                    trading_status = EXCLUDED.trading_status,
                    beneish_status = EXCLUDED.beneish_status,
                    gil_flag = EXCLUDED.gil_flag,
                    updated_at = NOW()
                """,
                (sym, ugroup, t_status, b_status, g_flag),
            )


universe_manager = UniverseManager()
