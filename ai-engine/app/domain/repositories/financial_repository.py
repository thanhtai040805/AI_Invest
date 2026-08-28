"""Financial Repository (IOS v5.1)
Quản lý dữ liệu tài chính phục vụ phân tích cơ bản, định giá và kiểm toán chất lượng:
- financial_statements: Báo cáo tài chính Point-in-time theo quý/năm
- financial_ratios: Chỉ số tài chính định lượng (P/E, P/B, ROE, ROA, Debt/Equity...)
- corporate_actions: Sự kiện doanh nghiệp (chia tách, cổ tức tiền mặt, cổ phiếu)
- insider_trades: Lịch sử giao dịch nội bộ và ban lãnh đạo
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


class FinancialRepository:
    """Repository quản lý dữ liệu BCTC và chỉ số tài chính phục vụ AI định giá & chấm điểm Moat."""

    def __init__(self, storage: Optional[PostgresAdapter] = None):
        self.storage = storage or PostgresAdapter()

    def get_financial_statements(
        self,
        symbol: str,
        statement_type: Optional[str] = None,
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách các kỳ BCTC gần nhất của cổ phiếu."""
        symbol = symbol.upper().strip()
        conditions = ["symbol = %s"]
        params: List[Any] = [symbol]

        if statement_type:
            conditions.append("statement_type = %s")
            params.append(statement_type)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT period_end, statement_type, frequency, data, published_date, source
            FROM financial_statements
            WHERE {where_clause}
            ORDER BY period_end DESC
            LIMIT %s
        """
        params.append(limit)

        try:
            rows = self.storage.fetch_all(query, tuple(params))
            if rows:
                return [
                    {
                        "period_end": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                        "statement_type": str(r[1]),
                        "frequency": str(r[2]),
                        "data": r[3] if isinstance(r[3], dict) else {},
                        "published_date": r[4].isoformat() if hasattr(r[4], "isoformat") and r[4] else None,
                        "source": str(r[5]) if r[5] else "vnstock",
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc financial_statements cho {symbol} ({e})")
        return []

    def get_latest_ratios(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Lấy chỉ số tài chính gần nhất của cổ phiếu (P/E, P/B, ROE, ROA, Debt/Equity...)."""
        symbol = symbol.upper().strip()
        query = """
            SELECT ratio_date, pe, pb, roe, roa, debt_equity, current_ratio,
                   gross_margin, net_margin, fcf_yield, ev_ebitda,
                   yoy_revenue_growth, yoy_earnings_growth, published_date
            FROM financial_ratios
            WHERE symbol = %s
            ORDER BY ratio_date DESC
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, (symbol,))
            if rows and len(rows) > 0:
                r = rows[0]
                return {
                    "symbol": symbol,
                    "ratio_date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                    "pe": float(r[1]) if r[1] is not None else 0.0,
                    "pb": float(r[2]) if r[2] is not None else 0.0,
                    "roe": float(r[3]) if r[3] is not None else 0.0,
                    "roa": float(r[4]) if r[4] is not None else 0.0,
                    "debt_equity": float(r[5]) if r[5] is not None else 0.0,
                    "current_ratio": float(r[6]) if r[6] is not None else 0.0,
                    "gross_margin": float(r[7]) if r[7] is not None else 0.0,
                    "net_margin": float(r[8]) if r[8] is not None else 0.0,
                    "fcf_yield": float(r[9]) if r[9] is not None else 0.0,
                    "ev_ebitda": float(r[10]) if r[10] is not None else 0.0,
                    "yoy_revenue_growth": float(r[11]) if r[11] is not None else 0.0,
                    "yoy_earnings_growth": float(r[12]) if r[12] is not None else 0.0,
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc financial_ratios cho {symbol} ({e})")

        # Fallback dữ liệu mặc định an toàn
        return {
            "symbol": symbol,
            "pe": 15.0,
            "pb": 2.0,
            "roe": 0.18,
            "roa": 0.08,
            "debt_equity": 0.5,
            "gross_margin": 0.25,
            "net_margin": 0.12,
        }

    def get_corporate_actions(self, symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Lấy lịch sử sự kiện doanh nghiệp và hệ số điều chỉnh giá."""
        symbol = symbol.upper().strip()
        query = """
            SELECT action_date, action_type, value, ratio, note, applied, adjustment_factor
            FROM corporate_actions
            WHERE symbol = %s
            ORDER BY action_date DESC
            LIMIT %s
        """
        try:
            rows = self.storage.fetch_all(query, (symbol, limit))
            if rows:
                return [
                    {
                        "action_date": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                        "action_type": str(r[1]),
                        "value": float(r[2]) if r[2] is not None else 0.0,
                        "ratio": float(r[3]) if r[3] is not None else 1.0,
                        "note": str(r[4]) if r[4] else "",
                        "applied": bool(r[5]) if r[5] is not None else True,
                        "adjustment_factor": float(r[6]) if r[6] is not None else 1.0,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc corporate_actions cho {symbol} ({e})")
        return []

    def get_insider_trades(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Lấy lịch sử giao dịch nội bộ của ban lãnh đạo."""
        symbol = symbol.upper().strip()
        query = """
            SELECT trade_date, trader_name, trader_position, trade_type, quantity, ownership_pct
            FROM insider_trades
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
                        "trader_name": str(r[1]),
                        "trader_position": str(r[2]) if r[2] else "",
                        "trade_type": str(r[3]),
                        "quantity": int(r[4]) if r[4] is not None else 0,
                        "ownership_pct": float(r[5]) if r[5] is not None else 0.0,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"Lỗi khi đọc insider_trades cho {symbol} ({e})")
        return []
