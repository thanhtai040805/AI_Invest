"""Financial Repository (IOS v5.1)
Quản lý dữ liệu tài chính phục vụ phân tích cơ bản, định giá và kiểm toán chất lượng:
- financial_statements: Báo cáo tài chính Point-in-time theo quý/năm
- financial_ratios: Chỉ số tài chính định lượng (P/E, P/B, ROE, ROA, Debt/Equity...)
- corporate_actions: Sự kiện doanh nghiệp (chia tách, cổ tức tiền mặt, cổ phiếu)
- insider_trades: Lịch sử giao dịch nội bộ và ban lãnh đạo
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from math import isfinite
from typing import Any, Dict, List, Optional

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


class FinancialRepository:
    """Repository quản lý dữ liệu BCTC và chỉ số tài chính phục vụ AI định giá & Business Quality."""

    def __init__(self, storage: Optional[PostgresAdapter] = None):
        self.storage = storage or PostgresAdapter()

    def get_financial_statements(
        self,
        symbol: str,
        statement_type: Optional[str] = None,
        limit: int = 8,
        as_of: Optional[date] = None,
        frequency: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách các kỳ BCTC gần nhất của cổ phiếu."""
        symbol = symbol.upper().strip()
        conditions = ["symbol = %s"]
        params: List[Any] = [symbol]

        if as_of:
            conditions.extend(["published_date IS NOT NULL", "published_date <= %s", "period_end <= %s"])
            params.extend([as_of, as_of])

        if statement_type:
            conditions.append("statement_type = %s")
            params.append(statement_type)
        if frequency:
            conditions.append("frequency = %s")
            params.append(frequency)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT period_end, statement_type, frequency, data, published_date, source
            FROM financial_statements
            WHERE {where_clause}
            ORDER BY period_end DESC, published_date DESC
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

    def get_peer_valuation_inputs(self, symbol: str, as_of: date) -> Dict[str, Any]:
        """Point-in-time EPS/BVPS priced at the median multiple of published sector peers."""
        symbol = symbol.upper().strip()
        stocks = self.storage.fetch_all("SELECT sector, industry FROM stocks WHERE symbol = %s", (symbol,))
        statements = self.get_financial_statements(
            symbol, statement_type="ratios", limit=1, as_of=as_of, frequency="quarterly",
        )
        if not stocks or not statements:
            logger.warning("Valuation unavailable for %s as_of=%s: missing stock metadata or published ratios statement", symbol, as_of)
            return {}

        statement = statements[0]
        if date.fromisoformat(statement["period_end"]) < as_of - timedelta(days=550):
            logger.warning(
                "Valuation unavailable for %s as_of=%s: latest ratios period_end=%s is older than 550 days",
                symbol, as_of, statement["period_end"],
            )
            return {}
        data = statement["data"]
        try:
            eps = float(data.get("thu_nhập_trên_mỗi_cổ_phần_của_4_quý_gần_nhất_eps") or 0)
            bvps = float(data.get("giá_trị_sổ_sách_của_cổ_phiếu_bvps") or 0)
        except (TypeError, ValueError):
            return {}
        eps = eps if isfinite(eps) else 0.0
        bvps = bvps if isfinite(bvps) else 0.0
        sector, industry = stocks[0]
        peers = self.storage.fetch_all("""
            WITH sector_universe AS (
                SELECT symbol
                FROM stocks
                WHERE sector = %s AND exchange IN ('HOSE', 'HSX') AND symbol <> %s
            ), latest AS (
                SELECT DISTINCT ON (r.symbol) r.symbol, r.pe, r.pb
                FROM financial_ratios r
                JOIN sector_universe u ON u.symbol = r.symbol
                WHERE r.frequency = 'quarterly'
                  AND r.published_date <= %s AND r.ratio_date <= %s
                  AND r.ratio_date >= %s
                ORDER BY r.symbol, r.published_date DESC, r.ratio_date DESC
            )
            SELECT (SELECT count(*) FROM sector_universe), count(*),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY pe)
                       FILTER (WHERE pe BETWEEN 2 AND 50),
                   count(*) FILTER (WHERE pe BETWEEN 2 AND 50),
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY pb)
                       FILTER (WHERE pb BETWEEN 0.2 AND 10),
                   count(*) FILTER (WHERE pb BETWEEN 0.2 AND 10)
            FROM latest
        """, (sector, symbol, as_of, as_of, as_of - timedelta(days=550)))
        if not peers:
            logger.warning("Valuation unavailable for %s as_of=%s: sector peer query returned no row", symbol, as_of)
            return {}
        sector_peer_universe_count, sector_peer_ratio_count, pe, pe_count, pb, pb_count = peers[0]
        financial = sector in {"BANKS", "FINANCIAL_SERVICES"}
        inputs: Dict[str, Any] = {}
        if eps > 0 and pe_count >= 5 and pe is not None:
            inputs["pe_price"] = round(eps * float(pe), 2)
        if financial and bvps > 0 and pb_count >= 5 and pb is not None:
            inputs["pb_price"] = round(bvps * float(pb), 2)
        industry_pb_count = 0
        if not inputs and sector in {"OTHER_INDUSTRIALS", "CONSUMER_SERVICES"} and bvps > 0:
            if industry:
                industry_peers = self.storage.fetch_all("""
                    WITH latest AS (
                        SELECT DISTINCT ON (r.symbol) r.symbol, r.pb
                        FROM financial_ratios r
                        JOIN stocks s ON s.symbol = r.symbol
                        WHERE s.industry = %s AND s.exchange IN ('HOSE', 'HSX') AND r.symbol <> %s
                          AND r.frequency = 'quarterly'
                          AND r.published_date <= %s AND r.ratio_date <= %s
                          AND r.ratio_date >= %s
                        ORDER BY r.symbol, r.published_date DESC, r.ratio_date DESC
                    )
                    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY pb)
                               FILTER (WHERE pb BETWEEN 0.2 AND 10),
                           count(*) FILTER (WHERE pb BETWEEN 0.2 AND 10)
                    FROM latest
                """, (industry, symbol, as_of, as_of, as_of - timedelta(days=550)))
                industry_pb, industry_pb_count = industry_peers[0] if industry_peers else (None, 0)
                # Four exact-industry peers are required when the broader sector is not a clean valuation cohort.
                if bvps > 0 and industry_pb is not None and industry_pb_count >= 4:
                    inputs["pb_price"] = round(bvps * float(industry_pb), 2)
                    inputs["source"] = {
                        "method": "PUBLISHED_INDUSTRY_PEERS_MEDIAN",
                        "as_of": as_of.isoformat(),
                        "period_end": statement["period_end"],
                        "published_date": statement["published_date"],
                        "sector": sector,
                        "industry": industry,
                        "peers_pb": int(industry_pb_count),
                        "median_pb": float(industry_pb),
                    }
        if inputs:
            if "source" not in inputs:
                inputs["source"] = {
                    "method": "PUBLISHED_SECTOR_PEERS_MEDIAN",
                    "as_of": as_of.isoformat(),
                    "period_end": statement["period_end"],
                    "published_date": statement["published_date"],
                    "sector": sector,
                    "peers_pe": int(pe_count),
                    "peers_pb": int(pb_count),
                    "median_pe": float(pe) if pe is not None else None,
                    "median_pb": float(pb) if pb is not None else None,
                }
        else:
            logger.warning(
                "Valuation unavailable for %s as_of=%s sector=%s industry=%s: EPS=%.2f BVPS=%.2f; sector peers=%d, ratios=%d, valid PE=%d/5, PB=%d/5; industry PB peers=%d/4",
                symbol, as_of, sector, industry, eps, bvps, int(sector_peer_universe_count or 0),
                int(sector_peer_ratio_count or 0), int(pe_count or 0), int(pb_count or 0),
                int(industry_pb_count or 0),
            )
            peer_universe_too_small = int(sector_peer_universe_count or 0) < 5
            peer_data_missing = int(sector_peer_ratio_count or 0) < int(sector_peer_universe_count or 0)
            no_supported_multiple = eps <= 0 and not financial and bvps > 0
            return {
                "valuation_diagnostics": {
                    "reason_code": (
                        "EPS_NONPOSITIVE_PB_UNSUPPORTED" if no_supported_multiple else
                        "PEER_UNIVERSE_TOO_SMALL" if peer_universe_too_small else
                        "PEER_RATIO_DATA_MISSING" if peer_data_missing else
                        "PEER_MULTIPLES_INVALID" if eps > 0 or bvps > 0 else
                        "FUNDAMENTAL_METRICS_NONPOSITIVE"
                    ),
                    "sector": sector,
                    "industry": industry,
                    "peer_market": "HOSE",
                    "sector_peer_universe_count": int(sector_peer_universe_count or 0),
                    "sector_peer_ratio_covered_count": int(sector_peer_ratio_count or 0),
                    "sector_peer_universe_shortfall": max(0, 5 - int(sector_peer_universe_count or 0)),
                    "valid_peer_pe": int(pe_count or 0),
                    "required_peer_pe": 5,
                    "valid_peer_pb": int(pb_count or 0),
                    "required_peer_pb": 5,
                    "valid_industry_peer_pb": int(industry_pb_count or 0),
                    "required_industry_peer_pb": 4,
                    "pb_allowed_for_sector": financial,
                    "as_of": as_of.isoformat(),
                }
            }
        return inputs

    def get_latest_ratios(self, symbol: str, as_of: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """Lấy chỉ số tài chính gần nhất của cổ phiếu (P/E, P/B, ROE, ROA, Debt/Equity...)."""
        symbol = symbol.upper().strip()
        query = """
            SELECT ratio_date, pe, pb, roe, roa, debt_equity, current_ratio,
                   gross_margin, net_margin, fcf_yield, ev_ebitda,
                   yoy_revenue_growth, yoy_earnings_growth, published_date
            FROM financial_ratios
            WHERE symbol = %s
              AND frequency = 'quarterly'
              AND published_date IS NOT NULL
              AND published_date <= %s
            ORDER BY published_date DESC, ratio_date DESC
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, (symbol, as_of or date.today()))
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
                    "published_date": r[13].isoformat() if hasattr(r[13], "isoformat") and r[13] else str(r[13]) if r[13] else None,
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
