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
        """Lấy điểm số Factor Scores gần nhất của cổ phiếu từ bảng factor_scores."""
        if not symbol:
            raise ValueError("[IntelligenceRepository] symbol không được rỗng.")
        symbol = str(symbol).upper().strip()
        if score_date:
            query = """
                SELECT symbol, score_date, value_score, quality_score, momentum_3m,
                       earnings_yield_score, foreign_flow_score, volatility_score,
                       composite_score, percentile, factor_details
                FROM factor_scores
                WHERE symbol = %s AND score_date = %s
                LIMIT 1
            """
            params = (symbol, score_date)
        else:
            query = """
                SELECT symbol, score_date, value_score, quality_score, momentum_3m,
                       earnings_yield_score, foreign_flow_score, volatility_score,
                       composite_score, percentile, factor_details
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
                details = r[10] if isinstance(r[10], dict) else {}
                f3 = float(r[4]) if r[4] is not None else float(details.get("f3_momentum", 50.0))
                f4 = float(r[5]) if r[5] is not None else float(details.get("f4_earnings", 50.0))
                f5 = float(r[6]) if r[6] is not None else float(details.get("f5_flow", 50.0))
                f6 = float(r[7]) if r[7] is not None else float(details.get("f6_technical", 50.0))
                css = float(r[8]) if r[8] is not None else 50.0
                return {
                    "ticker": str(r[0]),
                    "symbol": str(r[0]),
                    "date": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]),
                    "f1_value": float(r[2]) if r[2] is not None else 50.0,
                    "f2_quality": float(r[3]) if r[3] is not None else 50.0,
                    "f3_momentum": f3,
                    "f4_earnings": f4,
                    "f5_flow": f5,
                    "f6_technical": f6,
                    "css": css,
                    "composite_score": css,
                    "conviction": str(details.get("conviction", "B")),
                    "factor_details": details,
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
        factor_details_extra: Optional[Dict[str, Any]] = None,
        percentile: Optional[float] = None,
    ) -> bool:
        """Lưu kết quả tính điểm Factor Score của Research Agent vào bảng factor_scores."""
        if not symbol:
            raise ValueError("[IntelligenceRepository] symbol không được rỗng.")
        symbol = str(symbol).upper().strip()
        target_date = score_date or date.today()

        target_percentile = percentile
        if target_percentile is None and factor_details_extra and "percentile" in factor_details_extra:
            try:
                target_percentile = float(factor_details_extra["percentile"])
            except Exception:
                pass

        if target_percentile is None:
            try:
                pct_query = """
                    SELECT COUNT(*), COUNT(*) FILTER (WHERE composite_score <= %s)
                    FROM factor_scores
                    WHERE score_date = %s
                """
                cnt_rows = self.storage.fetch_all(pct_query, (css, target_date))
                if cnt_rows and cnt_rows[0][0] and cnt_rows[0][0] > 0:
                    tot = cnt_rows[0][0]
                    less_eq = cnt_rows[0][1]
                    target_percentile = round((less_eq / tot) * 100.0, 2)
                else:
                    target_percentile = round(min(max(css, 0.0), 100.0), 2)
            except Exception:
                target_percentile = round(min(max(css, 0.0), 100.0), 2)

        details_payload = {
            "f1_value": f1_value,
            "f2_quality": f2_quality,
            "f3_momentum": f3_momentum,
            "f4_earnings": f4_earnings,
            "f5_flow": f5_flow,
            "f6_technical": f6_technical,
            "conviction": conviction,
            "css": css,
            "percentile": target_percentile,
        }
        if factor_details_extra:
            details_payload.update(factor_details_extra)

        factor_details = json.dumps(details_payload, ensure_ascii=False, default=str)

        query = """
            INSERT INTO factor_scores (
                symbol, score_date, value_score, quality_score,
                momentum_3m, earnings_yield_score, foreign_flow_score, volatility_score,
                composite_score, percentile, factor_details, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, score_date) DO UPDATE SET
                value_score = EXCLUDED.value_score,
                quality_score = EXCLUDED.quality_score,
                momentum_3m = EXCLUDED.momentum_3m,
                earnings_yield_score = EXCLUDED.earnings_yield_score,
                foreign_flow_score = EXCLUDED.foreign_flow_score,
                volatility_score = EXCLUDED.volatility_score,
                composite_score = EXCLUDED.composite_score,
                percentile = EXCLUDED.percentile,
                factor_details = EXCLUDED.factor_details,
                updated_at = CURRENT_TIMESTAMP
        """
        try:
            self.storage.execute(
                query,
                (
                    symbol, target_date, f1_value, f2_quality,
                    f3_momentum, f4_earnings, f5_flow, f6_technical,
                    css, target_percentile, factor_details
                ),
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
                   source_sag_doc_id, extracted_at, is_stale
            FROM moat_profiles
            WHERE ticker = %s
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, (ticker,))
            if rows and len(rows) > 0:
                r = rows[0]
                m_score = float(r[3]) if r[3] is not None else 0.0
                intan = float(r[4]) if r[4] is not None else 0.0
                sw = float(r[5]) if r[5] is not None else 0.0
                net = float(r[6]) if r[6] is not None else 0.0
                cost = float(r[7]) if r[7] is not None else 0.0
                scale = float(r[8]) if r[8] is not None else 0.0
                sum_pillars = intan + sw + net + cost + scale
                
                # Tự động hiệu chỉnh moat_score nếu bằng 0 nhưng có điểm 5 trụ cột
                if m_score <= 0.0 and sum_pillars > 0:
                    m_score = sum_pillars

                # Hệ số Moat Multiplier chuẩn hóa
                if m_score >= 70.0:
                    multiplier = 1.15
                elif m_score >= 50.0:
                    multiplier = 1.05
                elif m_score > 0.0:
                    multiplier = 0.90
                else:
                    multiplier = 0.75

                return {
                    "ticker": str(r[0]),
                    "fiscal_year": int(r[1]) if r[1] is not None else 2025,
                    "report_type": str(r[2]) if r[2] else "ANNUAL_REPORT",
                    "moat_score": m_score,
                    "multiplier": multiplier,
                    "intangibles_score": intan,
                    "switching_costs_score": sw,
                    "network_effect_score": net,
                    "cost_advantage_score": cost,
                    "efficient_scale_score": scale,
                    "evidence_summary": r[9] if isinstance(r[9], dict) else {},
                    "source_sag_doc_id": str(r[10]) if r[10] else None,
                    "extracted_at": r[11].isoformat() if hasattr(r[11], "isoformat") else str(r[11]),
                    "is_stale": bool(r[12]) if len(r) > 12 and r[12] is not None else False,
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

        m_score = float(moat_data.get("moat_score", 0.0))
        intan = float(moat_data.get("intangibles_score", 0.0))
        sw = float(moat_data.get("switching_costs_score", 0.0))
        net = float(moat_data.get("network_effect_score", 0.0))
        cost = float(moat_data.get("cost_advantage_score", 0.0))
        scale = float(moat_data.get("efficient_scale_score", 0.0))
        sum_pillars = intan + sw + net + cost + scale
        if m_score <= 0.0 and sum_pillars > 0:
            m_score = sum_pillars

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
                    m_score,
                    intan,
                    sw,
                    net,
                    cost,
                    scale,
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

    def log_equity_research(
        self,
        ticker: str,
        factor_raw_metrics: Dict[str, Any],
        moat_citations_evidence: Dict[str, Any],
        llm_prompt_tokens: int = 0,
        research_date: Optional[date] = None,
    ) -> bool:
        """Lưu nhật ký phân tích equity research vào bảng log_equity_research."""
        if not ticker:
            raise ValueError("[IntelligenceRepository] ticker không được rỗng.")
        ticker = str(ticker).upper().strip()
        target_date = research_date or date.today()
        query = """
            INSERT INTO log_equity_research (
                ticker, date, factor_raw_metrics, moat_citations_evidence,
                llm_prompt_tokens, created_at
            ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """
        try:
            self.storage.execute(
                query,
                (
                    ticker,
                    target_date,
                    json.dumps(factor_raw_metrics, ensure_ascii=False, default=str),
                    json.dumps(moat_citations_evidence, ensure_ascii=False, default=str),
                    int(llm_prompt_tokens),
                ),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu log_equity_research cho {ticker} ({e})")
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
                pre_mortem_scenarios, target_price_range, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (thesis_id) DO UPDATE SET
                catalyst_type = EXCLUDED.catalyst_type,
                catalyst_description = EXCLUDED.catalyst_description,
                timeline_months = EXCLUDED.timeline_months,
                target_price = EXCLUDED.target_price,
                entry_price_estimated = EXCLUDED.entry_price_estimated,
                confirming_signals = EXCLUDED.confirming_signals,
                invalidation_conditions = EXCLUDED.invalidation_conditions,
                pre_mortem_scenarios = EXCLUDED.pre_mortem_scenarios,
                target_price_range = EXCLUDED.target_price_range,
                status = EXCLUDED.status,
                created_at = EXCLUDED.created_at
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
                    json.dumps(thesis_data.get("target_price_range", []), ensure_ascii=False, default=str),
                    thesis_data.get("status", "ACTIVE"),
                    now,
                ),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu investment_theses ({e})")
            return False

    def get_latest_investment_thesis(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Truy vấn luận điểm đầu tư mới nhất của một cổ phiếu."""
        ticker = str(ticker).upper().strip()
        query = """
            SELECT thesis_id, ticker, catalyst_type, catalyst_description, timeline_months,
                   target_price, entry_price_estimated, confirming_signals, invalidation_conditions,
                   pre_mortem_scenarios, target_price_range, status, created_at
            FROM investment_theses
            WHERE ticker = %s
            ORDER BY created_at DESC
            LIMIT 1
        """
        try:
            results = self.storage.fetch_all(query, (ticker,))
            if results:
                row = results[0]
                if isinstance(row, dict):
                    thesis_id = row.get("thesis_id")
                    t_ticker = row.get("ticker")
                    cat_type = row.get("catalyst_type")
                    cat_desc = row.get("catalyst_description")
                    tl_months = row.get("timeline_months")
                    tgt_price = row.get("target_price")
                    entry_price = row.get("entry_price_estimated")
                    signals = row.get("confirming_signals")
                    invalid_conds = row.get("invalidation_conditions")
                    pre_m = row.get("pre_mortem_scenarios")
                    tgt_range = row.get("target_price_range")
                    status = row.get("status")
                    created_at = row.get("created_at")
                else:
                    thesis_id, t_ticker, cat_type, cat_desc, tl_months, tgt_price, entry_price, signals, invalid_conds, pre_m, tgt_range, status, created_at = row

                def parse_json(val):
                    if isinstance(val, str):
                        try:
                            return json.loads(val)
                        except Exception:
                            return val
                    return val or []

                return {
                    "thesis_id": thesis_id,
                    "ticker": t_ticker,
                    "catalyst_type": cat_type,
                    "catalyst_description": cat_desc,
                    "timeline_months": int(tl_months) if tl_months is not None else 3,
                    "target_price": float(tgt_price) if tgt_price is not None else 0.0,
                    "entry_price_estimated": float(entry_price) if entry_price is not None else 0.0,
                    "confirming_signals": parse_json(signals),
                    "invalidation_conditions": parse_json(invalid_conds),
                    "pre_mortem_scenarios": parse_json(pre_m),
                    "target_price_range": parse_json(tgt_range),
                    "status": status,
                    "created_at": created_at,
                }
            return None
        except Exception as e:
            logger.warning(f"Lỗi khi lấy investment_thesis cho {ticker} ({e})")
            return None

    def update_thesis_status(self, thesis_id: str, status: str) -> bool:
        """Cập nhật trạng thái của luận điểm đầu tư (ví dụ: APPROVED_ACTIVE, CONDITIONAL_APPROVED, REJECTED)."""
        if not thesis_id:
            return False
        query = """
            UPDATE investment_theses
            SET status = %s
            WHERE thesis_id = %s
        """
        try:
            self.storage.execute(query, (str(status), str(thesis_id)))
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi cập nhật status cho thesis {thesis_id}: {e}")
            return False

    def save_counter_thesis_verdict(self, verdict_data: Dict[str, Any]) -> bool:
        """Lưu phán quyết phản biện từ Counter Thesis Agent."""
        thesis_id = verdict_data.get("thesis_id")
        ticker = verdict_data.get("ticker")
        if not ticker or not thesis_id:
            raise ValueError("[IntelligenceRepository] Thiếu 'ticker' hoặc 'thesis_id' trong verdict_data.")
        ticker = str(ticker).upper().strip()
        query = """
            INSERT INTO counter_thesis_verdicts (
                thesis_id, ticker, base_cts, interaction_multiplier, regime_multiplier,
                cts_score, verdict, rule_of_three_passed, is_capitulation_rebound,
                block_reasons, holes, execution_constraints, rationale, evaluated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (thesis_id) DO UPDATE SET
                cts_score = EXCLUDED.cts_score,
                verdict = EXCLUDED.verdict,
                block_reasons = EXCLUDED.block_reasons,
                execution_constraints = EXCLUDED.execution_constraints,

                rationale = EXCLUDED.rationale,
                evaluated_at = EXCLUDED.evaluated_at
        """
        now = datetime.now()
        try:
            self.storage.execute(
                query,
                (
                    thesis_id,
                    ticker,
                    float(verdict_data.get("base_cts", 0.0)),
                    float(verdict_data.get("interaction_multiplier", 1.0)),
                    float(verdict_data.get("regime_multiplier", 1.0)),
                    float(verdict_data.get("cts_score", 0.0)),
                    str(verdict_data.get("verdict", "PROCEED")),
                    bool(verdict_data.get("rule_of_three_passed", True)),
                    bool(verdict_data.get("is_capitulation_rebound", False)),
                    json.dumps(verdict_data.get("block_reasons", []), ensure_ascii=False, default=str),
                    json.dumps(verdict_data.get("holes", []), ensure_ascii=False, default=str),
                    json.dumps(verdict_data.get("execution_constraints") or {}, ensure_ascii=False, default=str),
                    str(verdict_data.get("rationale", "")),
                    now,
                ),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu counter_thesis_verdicts ({e})")
            return False

    def get_counter_thesis_verdict(self, thesis_id: str) -> Optional[Dict[str, Any]]:
        """Lấy phán quyết phản biện theo thesis_id."""
        query = """
            SELECT thesis_id, ticker, base_cts, interaction_multiplier, regime_multiplier,
                   cts_score, verdict, rule_of_three_passed, is_capitulation_rebound,
                   block_reasons, holes, execution_constraints, rationale, evaluated_at
            FROM counter_thesis_verdicts
            WHERE thesis_id = %s
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, (thesis_id,))
            if rows and len(rows) > 0:
                row = rows[0]
                if isinstance(row, dict):
                    t_id = row.get("thesis_id")
                    ticker = row.get("ticker")
                    base_cts = row.get("base_cts")
                    int_mult = row.get("interaction_multiplier")
                    reg_mult = row.get("regime_multiplier")
                    cts_score = row.get("cts_score")
                    verdict = row.get("verdict")
                    r3_passed = row.get("rule_of_three_passed")
                    is_cap = row.get("is_capitulation_rebound")
                    b_reasons = row.get("block_reasons")
                    holes = row.get("holes")
                    constraints = row.get("execution_constraints")
                    rationale = row.get("rationale")
                    eval_at = row.get("evaluated_at")
                else:
                    t_id, ticker, base_cts, int_mult, reg_mult, cts_score, verdict, r3_passed, is_cap, b_reasons, holes, constraints, rationale, eval_at = row

                def parse_json_obj(val, default_type):
                    if isinstance(val, str):
                        try:
                            return json.loads(val)
                        except Exception:
                            return default_type()
                    return val if val is not None else default_type()

                return {
                    "thesis_id": str(t_id),
                    "ticker": str(ticker),
                    "base_cts": float(base_cts) if base_cts is not None else 0.0,
                    "interaction_multiplier": float(int_mult) if int_mult is not None else 1.0,
                    "regime_multiplier": float(reg_mult) if reg_mult is not None else 1.0,
                    "cts_score": float(cts_score) if cts_score is not None else 0.0,
                    "verdict": str(verdict),
                    "rule_of_three_passed": bool(r3_passed),
                    "is_capitulation_rebound": bool(is_cap),
                    "block_reasons": parse_json_obj(b_reasons, list),
                    "holes": parse_json_obj(holes, list),
                    "execution_constraints": parse_json_obj(constraints, dict),
                    "rationale": str(rationale or ""),
                    "evaluated_at": eval_at.isoformat() if hasattr(eval_at, "isoformat") else str(eval_at or ""),
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc counter_thesis_verdicts cho {thesis_id} ({e})")
        return None

    def get_latest_counter_thesis_verdict(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Lấy phán quyết phản biện mới nhất của một cổ phiếu theo ticker."""
        if not ticker:
            return None
        ticker = str(ticker).upper().strip()
        query = """
            SELECT thesis_id, ticker, base_cts, interaction_multiplier, regime_multiplier,
                   cts_score, verdict, rule_of_three_passed, is_capitulation_rebound,
                   block_reasons, holes, execution_constraints, rationale, evaluated_at
            FROM counter_thesis_verdicts
            WHERE ticker = %s
            ORDER BY evaluated_at DESC
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, (ticker,))
            if rows and len(rows) > 0:
                row = rows[0]
                if isinstance(row, dict):
                    t_id = row.get("thesis_id")
                    ticker_val = row.get("ticker")
                    base_cts = row.get("base_cts")
                    int_mult = row.get("interaction_multiplier")
                    reg_mult = row.get("regime_multiplier")
                    cts_score = row.get("cts_score")
                    verdict = row.get("verdict")
                    r3_passed = row.get("rule_of_three_passed")
                    is_cap = row.get("is_capitulation_rebound")
                    b_reasons = row.get("block_reasons")
                    holes = row.get("holes")
                    constraints = row.get("execution_constraints")
                    rationale = row.get("rationale")
                    eval_at = row.get("evaluated_at")
                else:
                    t_id, ticker_val, base_cts, int_mult, reg_mult, cts_score, verdict, r3_passed, is_cap, b_reasons, holes, constraints, rationale, eval_at = row

                def parse_json_obj(val, default_type):
                    if isinstance(val, str):
                        try:
                            return json.loads(val)
                        except Exception:
                            return default_type()
                    return val if val is not None else default_type()

                return {
                    "thesis_id": str(t_id),
                    "ticker": str(ticker_val),
                    "base_cts": float(base_cts) if base_cts is not None else 0.0,
                    "interaction_multiplier": float(int_mult) if int_mult is not None else 1.0,
                    "regime_multiplier": float(reg_mult) if reg_mult is not None else 1.0,
                    "cts_score": float(cts_score) if cts_score is not None else 0.0,
                    "verdict": str(verdict),
                    "rule_of_three_passed": bool(r3_passed),
                    "is_capitulation_rebound": bool(is_cap),
                    "block_reasons": parse_json_obj(b_reasons, list),
                    "holes": parse_json_obj(holes, list),
                    "execution_constraints": parse_json_obj(constraints, dict),
                    "rationale": str(rationale or ""),
                    "evaluated_at": eval_at.isoformat() if hasattr(eval_at, "isoformat") else str(eval_at or ""),
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc counter_thesis_verdicts cho {ticker} ({e})")
        return None

    def save_risk_snapshot(self, snapshot_data: Dict[str, Any]) -> bool:
        """Lưu snapshot rủi ro danh mục hàng ngày vào CSDL."""
        query = """
            INSERT INTO risk_snapshots (
                date, es_97_5, garch_cash_target, drawdown_tier,
                max_drawdown_from_peak, cdc_active, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (date) DO UPDATE SET
                es_97_5 = EXCLUDED.es_97_5,
                garch_cash_target = EXCLUDED.garch_cash_target,
                drawdown_tier = EXCLUDED.drawdown_tier,
                max_drawdown_from_peak = EXCLUDED.max_drawdown_from_peak,
                cdc_active = EXCLUDED.cdc_active,
                updated_at = CURRENT_TIMESTAMP
        """
        req_date = snapshot_data.get("date") or date.today()
        if hasattr(req_date, "isoformat"):
            req_date = req_date.isoformat()
        try:
            self.storage.execute(
                query,
                (
                    req_date,
                    float(snapshot_data.get("es_97_5", 0.0)),
                    float(snapshot_data.get("garch_cash_target", 0.0)),
                    str(snapshot_data.get("drawdown_tier", "GREEN")),
                    float(snapshot_data.get("max_drawdown_from_peak", 0.0)),
                    bool(snapshot_data.get("cdc_active", False)),
                ),
            )
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu risk_snapshots ({e})")
            return False

    def get_latest_risk_snapshot(self) -> Optional[Dict[str, Any]]:
        """Truy vấn snapshot rủi ro danh mục gần nhất."""
        query = """
            SELECT date, es_97_5, garch_cash_target, drawdown_tier,
                   max_drawdown_from_peak, cdc_active, updated_at
            FROM risk_snapshots
            ORDER BY date DESC
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, ())
            if rows and len(rows) > 0:
                row = rows[0]
                if isinstance(row, dict):
                    r_date = row.get("date")
                    es = row.get("es_97_5")
                    cash_target = row.get("garch_cash_target")
                    dd_tier = row.get("drawdown_tier")
                    max_dd = row.get("max_drawdown_from_peak")
                    cdc_active = row.get("cdc_active")
                    updated_at = row.get("updated_at")
                else:
                    r_date, es, cash_target, dd_tier, max_dd, cdc_active, updated_at = row

                return {
                    "date": r_date.isoformat() if hasattr(r_date, "isoformat") else str(r_date),
                    "es_97_5": float(es) if es is not None else 0.0,
                    "garch_cash_target": float(cash_target) if cash_target is not None else 0.0,
                    "drawdown_tier": str(dd_tier),
                    "max_drawdown_from_peak": float(max_dd) if max_dd is not None else 0.0,
                    "cdc_active": bool(cdc_active),
                    "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at or ""),
                }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc risk_snapshots ({e})")
        return None

    def get_risk_limits(self, limit_type: str = "HOSE_EQUITY") -> Dict[str, float]:
        """Truy vấn hạn mức rủi ro thể chế từ bảng risk_limits (hoặc fallback mặc định IOS v5.1)."""
        default_limits = {
            "limit_type": limit_type,
            "max_single_stock_pct": 15.0,
            "max_sector_pct": 35.0,
            "hard_stop_loss_pct": 2.0,
        }
        query = """
            SELECT limit_type, max_single_stock_pct, max_sector_pct, hard_stop_loss_pct
            FROM risk_limits
            WHERE limit_type = %s
            LIMIT 1
        """
        try:
            rows = self.storage.fetch_all(query, (limit_type,))
            if rows and len(rows) > 0:
                row = rows[0]
                if isinstance(row, dict):
                    return {
                        "limit_type": str(row.get("limit_type", limit_type)),
                        "max_single_stock_pct": float(row.get("max_single_stock_pct", 15.0)),
                        "max_sector_pct": float(row.get("max_sector_pct", 35.0)),
                        "hard_stop_loss_pct": float(row.get("hard_stop_loss_pct", 2.0)),
                    }
                else:
                    l_type, max_stock, max_sector, stop_loss = row
                    return {
                        "limit_type": str(l_type),
                        "max_single_stock_pct": float(max_stock) if max_stock is not None else 15.0,
                        "max_sector_pct": float(max_sector) if max_sector is not None else 35.0,
                        "hard_stop_loss_pct": float(stop_loss) if stop_loss is not None else 2.0,
                    }
        except Exception as e:
            logger.warning(f"Lỗi khi đọc risk_limits từ DB ({e}), sử dụng hạn mức chuẩn thể chế IOS v5.1")
        return default_limits

    def save_risk_limits(self, limits_data: Dict[str, Any]) -> bool:
        """Cập nhật hoặc tạo mới hạn mức rủi ro trong bảng risk_limits."""
        limit_type = str(limits_data.get("limit_type", "HOSE_EQUITY")).strip()
        max_stock = float(limits_data.get("max_single_stock_pct", 15.0))
        max_sector = float(limits_data.get("max_sector_pct", 35.0))
        stop_loss = float(limits_data.get("hard_stop_loss_pct", 2.0))
        query = """
            INSERT INTO risk_limits (limit_type, max_single_stock_pct, max_sector_pct, hard_stop_loss_pct, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (limit_type) DO UPDATE SET
                max_single_stock_pct = EXCLUDED.max_single_stock_pct,
                max_sector_pct = EXCLUDED.max_sector_pct,
                hard_stop_loss_pct = EXCLUDED.hard_stop_loss_pct,
                updated_at = CURRENT_TIMESTAMP
        """
        try:
            self.storage.execute(query, (limit_type, max_stock, max_sector, stop_loss))
            return True
        except Exception as e:
            logger.warning(f"Lỗi khi lưu risk_limits vào DB ({e})")
            return False

