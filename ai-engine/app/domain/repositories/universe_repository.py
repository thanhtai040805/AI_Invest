"""Universe Repository (IOS v5.1)
Quản lý danh sách cổ phiếu, phân nhóm Universe (Group A/B/C/Sandbox), và kết quả lọc Lớp 0 (Beneish/GIL):
- stocks: Danh bạ chứng khoán niêm yết
- instrument_master: Thông tin cơ bản, free float, shares outstanding
- universe_securities: Phân loại nhóm và trạng thái tuân thủ Hard Law
- beneish_results: Kết quả kiểm toán M-Score chống gian lận BCTC
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


class UniverseRepository:
    """Repository quản lý Universe cổ phiếu và kiểm soát Hard Laws."""

    def __init__(self, storage: Optional[PostgresAdapter] = None):
        self.storage = storage or PostgresAdapter()

    def get_all_stocks(
        self,
        exchange: Optional[str] = "HOSE",
        group: Optional[str] = None,
        trading_status: Optional[str] = "NORMAL",
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách mã chứng khoán theo sàn và trạng thái phân nhóm."""
        conditions = []
        params: List[Any] = []

        if exchange:
            conditions.append("exchange = %s")
            params.append(exchange.upper().strip())
        if group:
            conditions.append("universe_group = %s")
            params.append(group.upper().strip())
        if trading_status:
            conditions.append("trading_status = %s")
            params.append(trading_status.upper().strip())

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"""
            SELECT symbol, name, exchange, industry, market_cap,
                   universe_group, trading_status, beneish_status, beneish_score, gil_flag
            FROM stocks
            {where_clause}
            ORDER BY market_cap DESC NULLS LAST
        """

        try:
            rows = self.storage.fetch_all(query, tuple(params))
            if rows:
                return [
                    {
                        "symbol": str(r[0]),
                        "name": str(r[1]),
                        "exchange": str(r[2]),
                        "industry": str(r[3]) if r[3] else "General",
                        "market_cap": int(r[4]) if r[4] is not None else 0,
                        "universe_group": str(r[5]) if r[5] else "B",
                        "trading_status": str(r[6]) if r[6] else "NORMAL",
                        "beneish_status": str(r[7]) if r[7] else "PENDING",
                        "beneish_score": float(r[8]) if r[8] is not None else None,
                        "gil_flag": str(r[9]) if r[9] else "PASS",
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc stocks ({e})")
        return []

    def get_stock(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Lấy thông tin chi tiết một cổ phiếu."""
        symbol = symbol.upper().strip()
        query = """
            SELECT symbol, name, exchange, industry, market_cap, ref_price,
                   universe_group, trading_status, beneish_status, beneish_score, gil_flag
            FROM stocks
            WHERE symbol = %s
        """
        try:
            rows = self.storage.fetch_all(query, (symbol,))
            if rows and len(rows) > 0:
                r = rows[0]
                return {
                    "symbol": str(r[0]),
                    "name": str(r[1]),
                    "exchange": str(r[2]),
                    "industry": str(r[3]) if r[3] else "General",
                    "market_cap": int(r[4]) if r[4] is not None else 0,
                    "ref_price": float(r[5]) if r[5] is not None else 0.0,
                    "universe_group": str(r[6]) if r[6] else "B",
                    "trading_status": str(r[7]) if r[7] else "NORMAL",
                    "beneish_status": str(r[8]) if r[8] else "PENDING",
                    "beneish_score": float(r[9]) if r[9] is not None else None,
                    "gil_flag": str(r[10]) if r[10] else "PASS",
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc stock {symbol} ({e})")
        return None

    def update_stock_compliance(
        self,
        symbol: str,
        group: Optional[str] = None,
        beneish_status: Optional[str] = None,
        beneish_score: Optional[float] = None,
        gil_flag: Optional[str] = None,
    ) -> bool:
        """Cập nhật phân loại Universe và trạng thái tuân thủ Hard Law."""
        symbol = symbol.upper().strip()
        now = datetime.now()
        query = """
            UPDATE stocks
            SET universe_group = COALESCE(%s, universe_group),
                beneish_status = COALESCE(%s, beneish_status),
                beneish_score = COALESCE(%s, beneish_score),
                gil_flag = COALESCE(%s, gil_flag),
                group_updated_at = %s
            WHERE symbol = %s
        """
        try:
            self.storage.execute(query, (group, beneish_status, beneish_score, gil_flag, now, symbol))
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi cập nhật compliance cho {symbol} ({e})")
            return False

    def save_beneish_result(
        self,
        symbol: str,
        fiscal_year: int,
        fiscal_quarter: int,
        m_score: float,
        is_manipulator: bool,
        sub_scores: Dict[str, Any],
    ) -> bool:
        """Lưu kết quả phân tích kiểm toán gian lận báo cáo tài chính M-Score."""
        import json
        symbol = symbol.upper().strip()
        query = """
            INSERT INTO beneish_results (
                symbol, fiscal_year, fiscal_quarter, m_score, is_manipulator, sub_scores, calculated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, fiscal_year, fiscal_quarter) DO UPDATE SET
                m_score = EXCLUDED.m_score,
                is_manipulator = EXCLUDED.is_manipulator,
                sub_scores = EXCLUDED.sub_scores,
                calculated_at = EXCLUDED.calculated_at
        """
        now = datetime.now()
        try:
            self.storage.execute(
                query,
                (symbol, fiscal_year, fiscal_quarter, m_score, is_manipulator, json.dumps(sub_scores), now),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu beneish_result cho {symbol} ({e})")
            return False
