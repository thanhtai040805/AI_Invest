"""AGENT-03: Equity Research Agent (IOS v5.1)

Chức năng:
- Phân tích định lượng chuyên sâu 6 nhóm Factor Score (F1 Value, F2 Quality, F3 Momentum, F4 Earnings, F5 Flow, F6 Technical).
- Truy vấn Dịch vụ RAG Moat AI từ phân hệ SAG hoặc bộ nhớ đệm O(1) moat_profiles: Định lượng 5 trụ cột lợi thế cạnh tranh.
- Tính toán điểm Composite Stock Score (CSS) thích ứng qua CSSScoringEngine: Tự động nạp bộ trọng số động (rl_factor_weights) từ Agent-10 (Reinforcement Learning) theo từng Market Regime và nhân hệ số Moat Multiplier.
- Gán mức độ tự tin (Conviction Level: A+, A, B, C, D, E) và sinh Research Report hoàn chỉnh.
- Bảng nghiệp vụ quản lý: factor_scores, moat_profiles
- Bảng log audit: log_equity_research
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional
import pandas as pd

from app.core.base_agent import BaseAgent
from app.domain.rules.scoring import CSSScoringEngine
from app.domain.services.factor_service import FactorService
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.adapters.sag_connector import sag_connector
from app.adapters.postgres_adapter import PostgresAdapter
from app.domain.rules.market.hmm_classifier import MarketRegime

logger = logging.getLogger(__name__)


class EquityResearchAgent(BaseAgent):
    """
    AGENT-03: Chuyên viên Nghiên cứu & Định giá Cổ phiếu.
    Tổng hợp sức mạnh cơ bản, dòng tiền và lợi thế hào kinh tế Moat thành điểm số đầu tư.
    """

    def __init__(self):
        super().__init__(
            agent_name="equity_research",
            state_tables=["factor_scores", "moat_profiles"],
            log_table="log_equity_research",
            enabled=True,
        )
        self.scoring_engine = CSSScoringEngine()
        self.factor_service = FactorService()
        self.intel_repo = IntelligenceRepository()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Nghiên cứu cổ phiếu:
        - event_data:
            - ticker: str (mã cổ phiếu, bắt buộc)
            - sector: str (ngành ICB, vd: "Technology")
            - current_regime: str (mặc định "BULL_TRENDING")
            - policy_weights: Dict[str, float] (bộ trọng số động nạp từ Agent-10 RL)
            - factor_overrides: Dict[str, float] (tùy chọn)
            - target_date: date / str (tùy chọn, mặc định hôm nay)
        """
        ticker = event_data.get("ticker")
        if not ticker:
            raise ValueError("[EquityResearchAgent] Thiếu mã cổ phiếu (ticker) bắt buộc trong event_data.")
        ticker = str(ticker).upper().strip()

        # Xác định ngành ICB nếu chưa truyền
        sector = event_data.get("sector")
        if not sector:
            try:
                storage = PostgresAdapter()
                rows_sec = storage.fetch_all(
                    "SELECT COALESCE(industry, sector, 'Technology') FROM stocks WHERE symbol = %s LIMIT 1",
                    (ticker,)
                )
                sector = str(rows_sec[0][0]) if rows_sec and rows_sec[0][0] else "Technology"
            except Exception:
                sector = "Technology"

        regime_str = event_data.get("current_regime", "BULL_TRENDING")
        policy_weights = event_data.get("policy_weights")

        target_date_raw = event_data.get("target_date") or event_data.get("date") or date.today()
        if isinstance(target_date_raw, str):
            try:
                target_d = date.fromisoformat(target_date_raw)
            except Exception:
                target_d = date.today()
        elif isinstance(target_date_raw, datetime):
            target_d = target_date_raw.date()
        else:
            target_d = target_date_raw

        # =========================================================================
        # 1. Truy vấn Lợi thế cạnh tranh (Economic Moat)
        # Ưu tiên O(1) từ bảng moat_profiles trong CSDL; nếu chưa có, gọi SAG Moat AI
        # =========================================================================
        evidence_quote = ""
        moat_score = 65.0
        moat_multiplier = 1.0
        moat_source = "DEFAULT"

        cached_moat = self.intel_repo.get_moat_profile(ticker)
        if cached_moat and not cached_moat.get("is_stale", False) and float(cached_moat.get("moat_score", 0.0)) > 0:
            moat_score = float(cached_moat["moat_score"])
            moat_multiplier = float(cached_moat.get("multiplier", 1.0))
            evidence_summary = cached_moat.get("evidence_summary") or {}
            evidence_quote = evidence_summary.get("evidence_quote", "")
            moat_source = "POSTGRES_MOAT_PROFILES"
        else:
            try:
                moat_result = await sag_connector.get_moat_assessment(ticker, sector)
                moat_score = float(moat_result.get("moat_score", 70.0))
                moat_multiplier = float(moat_result.get("multiplier", 1.0))
                evidence_quote = str(moat_result.get("evidence_quote", ""))
                moat_source = "SAG_FAST_MCP_RAG"

                # Lưu vào bảng moat_profiles để tái sử dụng O(1)
                self.intel_repo.save_moat_profile({
                    "ticker": ticker,
                    "moat_score": moat_score,
                    "intangibles_score": moat_result.get("intangibles_score", 18.0),
                    "switching_costs_score": moat_result.get("switching_costs_score", 18.0),
                    "network_effect_score": moat_result.get("network_effect_score", 14.0),
                    "cost_advantage_score": moat_result.get("cost_advantage_score", 13.0),
                    "efficient_scale_score": moat_result.get("efficient_scale_score", 12.0),
                    "evidence_summary": {"evidence_quote": evidence_quote},
                    "source_sag_doc_id": moat_result.get("source_sag_doc_id"),
                })
            except Exception as e:
                logger.warning(f"SAG Moat AI query failed for {ticker}: {e}")
                if cached_moat:
                    moat_score = float(cached_moat.get("moat_score", 65.0))
                    moat_multiplier = float(cached_moat.get("multiplier", 1.0))
                    moat_source = "POSTGRES_FALLBACK"
                else:
                    moat_score = 65.0
                    moat_multiplier = 1.0
                    moat_source = "DEFAULT_FALLBACK"

        # Hiệu chuẩn Moat từ Agent-10 (Triệt tiêu Ảo giác Moat AI)
        data_quality_flag = "VERIFIED"
        moat_calibrations = event_data.get("moat_calibrations", {})
        if ticker in moat_calibrations:
            m_calib = moat_calibrations[ticker]
            moat_score = float(m_calib.get("calibrated_moat_score", moat_score))
            moat_multiplier = float(m_calib.get("calibrated_multiplier", moat_multiplier))
            data_quality_flag = f"MOAT_CALIBRATED_AGENT10_{m_calib.get('hallucination_risk', 'NORMAL')}"
        else:
            try:
                storage = PostgresAdapter()
                rows_flag = storage.fetch_all(
                    """
                    SELECT numeric_value, metadata
                    FROM mral_metrics
                    WHERE ticker = %s AND metric_type = 'MOAT_CALIBRATION'
                    ORDER BY metric_date DESC LIMIT 1
                    """,
                    (ticker,)
                )
                if rows_flag and rows_flag[0][0] is not None:
                    calib_score = float(rows_flag[0][0])
                    meta = rows_flag[0][1] if isinstance(rows_flag[0][1], dict) else {}
                    if calib_score < moat_score:
                        moat_score = calib_score
                        moat_multiplier = float(meta.get("calibrated_multiplier", 0.70))
                        data_quality_flag = f"MOAT_CALIBRATED_DB_{meta.get('hallucination_risk', 'CRITICAL')}"
            except Exception as e_mc:
                logger.debug(f"Không thể tra cứu mral_metrics cho Moat {ticker}: {e_mc}")

        # =========================================================================
        # 2. Tính toán / Nạp 6 nhóm Factor Scores (F1 - F6) từ Dữ liệu Thật
        # =========================================================================
        factor_overrides = event_data.get("factor_overrides", {})
        raw_factor_metrics: Dict[str, Any] = {}
        factor_source = "COMPUTED"

        if factor_overrides:
            f1_value = float(factor_overrides.get("f1_value", 65.0))
            f2_quality = float(factor_overrides.get("f2_quality", (0.5 * 70.0 + 0.5 * moat_score)))
            f3_momentum = float(factor_overrides.get("f3_momentum", 65.0))
            f4_earnings = float(factor_overrides.get("f4_earnings", 65.0))
            f5_flow = float(factor_overrides.get("f5_flow", 65.0))
            f6_technical = float(factor_overrides.get("f6_technical", 65.0))
            data_quality_flag = "USER_OVERRIDE"
            factor_source = "USER_OVERRIDE"
        else:
            # Ưu tiên kiểm tra CSDL factor_scores
            db_factors = self.intel_repo.get_factor_score(ticker, score_date=target_d)
            has_db_valid = (
                db_factors is not None
                and (
                    db_factors.get("f3_momentum") != 50.0
                    or db_factors.get("f4_earnings") != 50.0
                    or db_factors.get("f5_flow") != 50.0
                )
            )

            if has_db_valid:
                f1_value = float(db_factors.get("f1_value", 50.0))
                f2_quality = float(db_factors.get("f2_quality", 50.0))
                f3_momentum = float(db_factors.get("f3_momentum", 50.0))
                f4_earnings = float(db_factors.get("f4_earnings", 50.0))
                f5_flow = float(db_factors.get("f5_flow", 50.0))
                f6_technical = float(db_factors.get("f6_technical", 50.0))
                raw_factor_metrics = db_factors.get("factor_details", {})
                factor_source = "POSTGRES_FACTOR_SCORES"
            else:
                # Tính toán ĐỘNG thực tế từ financial_ratios và market_data_daily
                computed = self.factor_service.compute_factors_for_ticker(ticker, target_d)
                f1_value = float(computed.get("f1_value", 50.0))
                base_f2 = float(computed.get("f2_quality", 50.0))
                # Tích hợp Moat vào F2 Quality theo rubric chuẩn IOS v5.1
                f2_quality = round(0.5 * base_f2 + 0.5 * moat_score, 2)
                f3_momentum = float(computed.get("f3_momentum", 50.0))
                f4_earnings = float(computed.get("f4_earnings", 50.0))
                f5_flow = float(computed.get("f5_flow", 50.0))
                f6_technical = float(computed.get("f6_technical", 50.0))
                raw_factor_metrics = computed.get("raw_metrics", {})
                factor_source = "FACTOR_SERVICE_DYNAMIC"

        # Lấy giá thị trường hiện tại (current_price) phục vụ định giá Agent-04 (Ưu tiên DNSE Realtime)
        current_price = float(event_data.get("current_price", 0.0))
        if current_price <= 0:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                rt_price = m_repo.get_realtime_or_latest_price(ticker)
                if rt_price and rt_price > 0:
                    current_price = float(rt_price)
                else:
                    storage = PostgresAdapter()
                    p_rows = storage.fetch_all(
                        "SELECT close_adj FROM market_data_daily WHERE ticker = %s ORDER BY date DESC LIMIT 1",
                        (ticker,)
                    )
                    if p_rows and p_rows[0][0]:
                        current_price = float(p_rows[0][0])
            except Exception:
                current_price = 50000.0

        # =========================================================================
        # 3. Tính điểm Composite Stock Score (CSS) qua CSSScoringEngine
        # =========================================================================
        weights_source = "DEFAULT"
        if not policy_weights:
            try:
                storage = PostgresAdapter()
                clean_regime = "BULL_MARKET" if "BULL" in regime_str else ("BEAR_MARKET" if "BEAR" in regime_str else "RANGE_BOUND")
                query = """
                    SELECT f1_value_weight, f2_quality_weight, f3_momentum_weight,
                           f4_earnings_weight, f5_flow_weight, f6_technical_weight
                    FROM rl_factor_weights
                    WHERE regime = %s OR regime = %s
                    ORDER BY updated_at DESC LIMIT 1
                """
                rows_w = storage.fetch_all(query, (regime_str, clean_regime))
                if rows_w and len(rows_w) > 0:
                    policy_weights = {
                        "f1_value": float(rows_w[0][0]),
                        "f2_quality": float(rows_w[0][1]),
                        "f3_momentum": float(rows_w[0][2]),
                        "f4_earnings": float(rows_w[0][3]),
                        "f5_flow": float(rows_w[0][4]),
                        "f6_technical": float(rows_w[0][5]),
                    }
                    weights_source = f"AGENT-10 (PostgreSQL rl_factor_weights - {clean_regime})"
            except Exception as e_pw:
                logger.debug(f"Không thể tải rl_factor_weights từ DB ({e_pw})")

        if policy_weights and weights_source == "DEFAULT":
            weights_source = "AGENT-10 (Reinforcement Learning Adaptive Weights)"

        # Tạo DataFrame đầu vào cho CSSScoringEngine
        df_factors = pd.DataFrame([{
            "ticker": ticker,
            "sector": sector,
            "f1_value": f1_value,
            "f2_quality": f2_quality,
            "f3_momentum": f3_momentum,
            "f4_earnings": f4_earnings,
            "f5_flow": f5_flow,
            "f6_technical": f6_technical,
            "moat_multiplier": moat_multiplier,
            "audit_opinion": event_data.get("audit_opinion", "UNQUALIFIED"),
            "gil_flag": event_data.get("gil_flag", "PASS"),
        }])

        df_scored = self.scoring_engine.calculate_css(
            factor_scores=df_factors,
            regime=regime_str,
            custom_weights=policy_weights,
        )

        base_css = float(df_scored["base_css"].iloc[0])
        css = float(df_scored["css"].iloc[0])
        conviction = str(df_scored["conviction"].iloc[0])

        applied_weights = policy_weights or self.scoring_engine.regime_weights.get(regime_str, {})
        eligible_for_thesis = (conviction in ["A+", "A", "B"]) and (css >= 60.0)

        research_report = {
            "ticker": ticker,
            "sector": sector,
            "f1_value": round(f1_value, 2),
            "f2_quality": round(f2_quality, 2),
            "f3_momentum": round(f3_momentum, 2),
            "f4_earnings": round(f4_earnings, 2),
            "f5_flow": round(f5_flow, 2),
            "f6_technical": round(f6_technical, 2),
            "moat_score": round(moat_score, 2),
            "moat_multiplier": round(moat_multiplier, 2),
            "base_css": round(base_css, 2),
            "css": round(css, 2),
            "conviction": conviction,
            "current_price": current_price,
            "data_quality_flag": data_quality_flag,
            "eligible_for_thesis": eligible_for_thesis,
            "applied_weights": applied_weights,
        }

        # =========================================================================
        # 4. Ghi nhận dữ liệu vào Bảng Nghiệp vụ & Bảng Log Audit
        # =========================================================================
        try:
            self.intel_repo.save_factor_score(
                symbol=ticker,
                f1_value=f1_value,
                f2_quality=f2_quality,
                f3_momentum=f3_momentum,
                f4_earnings=f4_earnings,
                f5_flow=f5_flow,
                f6_technical=f6_technical,
                css=css,
                conviction=conviction,
                score_date=target_d,
                factor_details_extra=raw_factor_metrics,
            )
            self.intel_repo.log_equity_research(
                ticker=ticker,
                factor_raw_metrics=raw_factor_metrics,
                moat_citations_evidence={"evidence_quote": evidence_quote, "moat_score": moat_score},
                llm_prompt_tokens=event_data.get("llm_prompt_tokens", 0),
                research_date=target_d,
            )
        except Exception as e_save:
            logger.warning(f"Lỗi ghi nhận state/log cho {ticker}: {e_save}")

        trace = {
            "scoring_engine": self.scoring_engine.__class__.__name__,
            "factor_service": self.factor_service.__class__.__name__,
            "factor_source": factor_source,
            "moat_source": moat_source,
            "sag_connector": "FastMCP RAG Moat Inquisitor",
            "weights_source": weights_source,
            "evidence_quote": evidence_quote,
            "regime_applied": regime_str,
            "data_quality_flag": data_quality_flag,
        }

        return {"data": research_report, "trace": trace}
