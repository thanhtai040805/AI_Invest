"""Intelligence Repository (IOS v5.1)
Quản lý kết quả phân tích trí tuệ nhân tạo, điểm số định lượng và luận điểm đầu tư:
- factor_scores: 6 nhóm nhân tố (F1-F6) và điểm tổng hợp Composite Stock Score (CSS)
- moat_profiles: Điểm hào kinh tế định lượng 5 trụ cột và bằng chứng trích dẫn
- knowledge_documents: Dữ liệu OCR BCTC, tin tức, và AI Triage phân tích tác động
- risk_assessments: Đánh giá chấm điểm rủi ro và các cờ cảnh báo Hard/Soft flags
- investment_theses & counter_thesis_verdicts: Luận điểm đầu tư và phán quyết phản biện
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)


class IntelligenceRepository:
    """Repository quản lý kết quả phân tích AI, Factor Scores, Moat và Thesis đầu tư."""

    def __init__(self, storage: Optional[PostgresAdapter] = None):
        self.storage = storage or PostgresAdapter()

    def get_factor_score(self, symbol: str, score_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """Lấy điểm số Factor Scores gần nhất của cổ phiếu."""
        if not symbol:
            raise ValueError("[IntelligenceRepository] symbol không được rỗng.")
        symbol = str(symbol).upper().strip()
        if score_date:
            query = """
                SELECT symbol, score_date, value_score, quality_score, composite_score, percentile, factor_details
                FROM factor_scores
                WHERE symbol = %s AND score_date = %s
                LIMIT 1
            """
            params = (symbol, score_date)
        else:
            query = """
                SELECT symbol, score_date, value_score, quality_score, composite_score, percentile, factor_details
                FROM factor_scores
                WHERE symbol = %s
                ORDER BY score_date DESC
                LIMIT 1
            """
            params = (symbol,)

        try:
            rows = self.storage.fetch_all(query, params)
            if rows and len(rows) > 0:
                r = rows[0]
                details = r[6] if isinstance(r[6], dict) else {}
                return {
                    "ticker": str(r[0]),
                    "symbol": str(r[0]),
                    "date": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]),
                    "f1_value": float(r[2]) if r[2] is not None else 50.0,
                    "f2_quality": float(r[3]) if r[3] is not None else 50.0,
                    "f3_momentum": float(details.get("f3_momentum", 50.0)),
                    "f4_earnings": float(details.get("f4_earnings", 50.0)),
                    "f5_flow": float(details.get("f5_flow", 50.0)),
                    "f6_technical": float(details.get("f6_technical", 50.0)),
                    "css": float(r[4]) if r[4] is not None else 50.0,
                    "composite_score": float(r[4]) if r[4] is not None else 50.0,
                    "conviction": str(details.get("conviction", "B")),
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc factor_scores cho {symbol} ({e})")
        return None

    def save_factor_score(
        self,
        symbol: str,
        f1_value: float,
        f2_quality: float,
        f3_momentum: float,
        f4_earnings: float,
        f5_flow: float,
        f6_technical: float,
        css: float,
        conviction: str = "B",
        score_date: Optional[date] = None,
    ) -> bool:
        """Lưu kết quả tính điểm Factor Score của Research Agent."""
        if not symbol:
            raise ValueError("[IntelligenceRepository] symbol không được rỗng.")
        symbol = str(symbol).upper().strip()
        target_date = score_date or date.today()

        factor_details = json.dumps({
            "f3_momentum": f3_momentum,
            "f4_earnings": f4_earnings,
            "f5_flow": f5_flow,
            "f6_technical": f6_technical,
            "conviction": conviction,
        })

        query = """
            INSERT INTO factor_scores (
                symbol, score_date, value_score, quality_score,
                composite_score, percentile, factor_details, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, score_date) DO UPDATE SET
                value_score = EXCLUDED.value_score,
                quality_score = EXCLUDED.quality_score,
                composite_score = EXCLUDED.composite_score,
                factor_details = EXCLUDED.factor_details,
                updated_at = CURRENT_TIMESTAMP
        """
        try:
            self.storage.execute(
                query,
                (symbol, target_date, f1_value, f2_quality, css, css, factor_details),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu factor_scores cho {symbol} ({e})")
            return False

    def get_moat_profile(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Lấy hồ sơ Moat Profile định lượng của cổ phiếu."""
        if not ticker:
            raise ValueError("[IntelligenceRepository] ticker không được rỗng.")
        ticker = str(ticker).upper().strip()
        query = """
            SELECT ticker, fiscal_year, report_type, moat_score,
                   intangibles_score, switching_costs_score, network_effect_score,
                   cost_advantage_score, efficient_scale_score, evidence_summary,
                   source_sag_doc_id, extracted_at
            FROM moat_profiles
            WHERE ticker = %s
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, (ticker,))
            if rows and len(rows) > 0:
                r = rows[0]
                return {
                    "ticker": str(r[0]),
                    "fiscal_year": int(r[1]) if r[1] is not None else 2025,
                    "report_type": str(r[2]) if r[2] else "ANNUAL_REPORT",
                    "moat_score": float(r[3]) if r[3] is not None else 50.0,
                    "intangibles_score": float(r[4]) if r[4] is not None else 0.0,
                    "switching_costs_score": float(r[5]) if r[5] is not None else 0.0,
                    "network_effect_score": float(r[6]) if r[6] is not None else 0.0,
                    "cost_advantage_score": float(r[7]) if r[7] is not None else 0.0,
                    "efficient_scale_score": float(r[8]) if r[8] is not None else 0.0,
                    "evidence_summary": r[9] if isinstance(r[9], dict) else {},
                    "source_sag_doc_id": str(r[10]) if r[10] else None,
                    "extracted_at": r[11].isoformat() if hasattr(r[11], "isoformat") else str(r[11]),
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc moat_profiles cho {ticker} ({e})")
        return None

    def save_moat_profile(self, moat_data: Dict[str, Any]) -> bool:
        """Lưu hồ sơ Moat Profile từ SAG / RAG Moat AI Service."""
        ticker = moat_data.get("ticker")
        if not ticker:
            raise ValueError("[IntelligenceRepository] Thiếu 'ticker' trong moat_data.")
        ticker = str(ticker).upper().strip()
        now = datetime.now()
        query = """
            INSERT INTO moat_profiles (
                ticker, fiscal_year, report_type, moat_score,
                intangibles_score, switching_costs_score, network_effect_score,
                cost_advantage_score, efficient_scale_score, evidence_summary,
                source_sag_doc_id, extracted_at, is_stale
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker) DO UPDATE SET
                fiscal_year = EXCLUDED.fiscal_year,
                report_type = EXCLUDED.report_type,
                moat_score = EXCLUDED.moat_score,
                intangibles_score = EXCLUDED.intangibles_score,
                switching_costs_score = EXCLUDED.switching_costs_score,
                network_effect_score = EXCLUDED.network_effect_score,
                cost_advantage_score = EXCLUDED.cost_advantage_score,
                efficient_scale_score = EXCLUDED.efficient_scale_score,
                evidence_summary = EXCLUDED.evidence_summary,
                source_sag_doc_id = EXCLUDED.source_sag_doc_id,
                extracted_at = EXCLUDED.extracted_at,
                is_stale = EXCLUDED.is_stale
        """
        try:
            self.storage.execute(
                query,
                (
                    ticker,
                    int(moat_data.get("fiscal_year", 2025)),
                    str(moat_data.get("report_type", "ANNUAL_REPORT")),
                    float(moat_data.get("moat_score", 75.0)),
                    float(moat_data.get("intangibles_score", 18.0)),
                    float(moat_data.get("switching_costs_score", 18.0)),
                    float(moat_data.get("network_effect_score", 14.0)),
                    float(moat_data.get("cost_advantage_score", 13.0)),
                    float(moat_data.get("efficient_scale_score", 12.0)),
                    json.dumps(moat_data.get("evidence_summary", {}), ensure_ascii=False, default=str),
                    moat_data.get("source_sag_doc_id"),
                    now,
                    False,
                ),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu moat_profiles cho {ticker} ({e})")
            return False

    def save_investment_thesis(self, thesis_data: Dict[str, Any]) -> bool:
        """Lưu luận điểm đầu tư từ Thesis Agent."""
        thesis_id = thesis_data.get("thesis_id")
        ticker = thesis_data.get("ticker")
        if not ticker:
            raise ValueError("[IntelligenceRepository] Thiếu 'ticker' trong thesis_data.")
        ticker = str(ticker).upper().strip()
        query = """
            INSERT INTO investment_theses (
                thesis_id, ticker, catalyst_type, catalyst_description, timeline_months,
                target_price, entry_price_estimated, confirming_signals, invalidation_conditions,
                pre_mortem_scenarios, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (thesis_id) DO UPDATE SET
                status = EXCLUDED.status,
                target_price = EXCLUDED.target_price
        """
        now = datetime.now()
        try:
            self.storage.execute(
                query,
                (
                    thesis_id,
                    ticker,
                    thesis_data.get("catalyst_type", "EARNINGS_GROWTH"),
                    thesis_data.get("catalyst_description", ""),
                    int(thesis_data.get("timeline_months", 3)),
                    float(thesis_data.get("target_price", 0.0)),
                    float(thesis_data.get("entry_price_estimated", 0.0)),
                    json.dumps(thesis_data.get("confirming_signals", []), ensure_ascii=False, default=str),
                    json.dumps(thesis_data.get("invalidation_conditions", []), ensure_ascii=False, default=str),
                    json.dumps(thesis_data.get("pre_mortem_scenarios", []), ensure_ascii=False, default=str),
                    thesis_data.get("status", "ACTIVE"),
                    now,
                ),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu investment_theses ({e})")
            return False
