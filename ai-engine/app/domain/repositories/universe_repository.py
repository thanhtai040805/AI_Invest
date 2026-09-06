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

    def upsert_universe_security(
        self,
        ticker: str,
        universe_group: str,
        trading_status: str = "NORMAL",
        beneish_status: str = "PASS",
        gil_flag: str = "PASS",
    ) -> bool:
        """Lưu hoặc cập nhật trạng thái phân nhóm Universe của cổ phiếu vào bảng universe_securities."""
        ticker = ticker.upper().strip()
        query = """
            INSERT INTO universe_securities (
                ticker, universe_group, trading_status, beneish_status, gil_flag, updated_at
            ) VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (ticker) DO UPDATE SET
                universe_group = EXCLUDED.universe_group,
                trading_status = EXCLUDED.trading_status,
                beneish_status = EXCLUDED.beneish_status,
                gil_flag = EXCLUDED.gil_flag,
                updated_at = NOW()
        """
        try:
            self.storage.execute(
                query,
                (ticker, universe_group, trading_status, beneish_status, gil_flag),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi upsert universe_securities cho {ticker} ({e})")
            return False

    def save_beneish_result(
        self,
        ticker: str,
        quarter_date: Optional[Any] = None,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Lưu kết quả phân tích kiểm toán gian lận báo cáo tài chính M-Score vào bảng beneish_results."""
        ticker = ticker.upper().strip()

        # Xử lý tương thích cả 2 chuẩn gọi:
        # Chuẩn mới: (ticker, quarter_date=date, m_score=float, status=str, variables=dict)
        # Chuẩn cũ: (symbol, fiscal_year, fiscal_quarter, m_score, is_manipulator, sub_scores)
        q_date = quarter_date
        m_score = kwargs.get("m_score", 0.0)
        status = kwargs.get("status")
        vars_dict = kwargs.get("variables") or kwargs.get("sub_scores") or {}

        if isinstance(quarter_date, int) and len(args) >= 1:
            fiscal_year = quarter_date
            fiscal_quarter = int(args[0])
            m = max(1, min(12, fiscal_quarter * 3))
            q_date = date(fiscal_year, m, 28)
            if len(args) >= 2:
                m_score = float(args[1])
            if len(args) >= 3:
                is_manip = bool(args[2])
                status = "FAIL" if is_manip else "PASS"
            if len(args) >= 4 and isinstance(args[3], dict):
                vars_dict = args[3]

        if q_date is None:
            q_date = date.today()

        if status is None:
            status = "FAIL" if (m_score is not None and m_score > -1.78) else "PASS"

        dsri = float(vars_dict.get("dsri", 1.0))
        gmi = float(vars_dict.get("gmi", 1.0))
        aqi = float(vars_dict.get("aqi", 1.0))
        sgi = float(vars_dict.get("sgi", 1.0))
        depi = float(vars_dict.get("depi", 1.0))
        sgai = float(vars_dict.get("sgai", 1.0))
        tata = float(vars_dict.get("tata", 0.0))
        lvgi = float(vars_dict.get("lvgi", 1.0))
        m_score_val = float(m_score) if m_score is not None else 0.0

        query = """
            INSERT INTO beneish_results (
                ticker, quarter_date, dsri, gmi, aqi, sgi, depi, sgai, tata, lvgi, m_score, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, quarter_date) DO UPDATE SET
                dsri = EXCLUDED.dsri,
                gmi = EXCLUDED.gmi,
                aqi = EXCLUDED.aqi,
                sgi = EXCLUDED.sgi,
                depi = EXCLUDED.depi,
                sgai = EXCLUDED.sgai,
                tata = EXCLUDED.tata,
                lvgi = EXCLUDED.lvgi,
                m_score = EXCLUDED.m_score,
                status = EXCLUDED.status
        """
        try:
            self.storage.execute(
                query,
                (ticker, q_date, dsri, gmi, aqi, sgi, depi, sgai, tata, lvgi, m_score_val, status),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu beneish_result cho {ticker} ({e})")
            return False
