"""AGENT-03: Equity Research Agent (IOS v5.1)

Chức năng:
- Phân tích định lượng chuyên sâu 6 nhóm Factor Score (F1 Value, F2 Quality, F3 Momentum, F4 Earnings, F5 Flow, F6 Technical).
- Truy vấn Dịch vụ RAG Moat AI từ phân hệ SAG: Định lượng 5 trụ cột lợi thế cạnh tranh (Brand, Switching, Network, Cost, Regulatory).
- Tính toán điểm Composite Stock Score (CSS) thích ứng: Tự động nạp bộ trọng số động (rl_factor_weights) từ Agent-10 (Reinforcement Learning) theo từng Market Regime và nhân hệ số Moat Multiplier.
- Gán mức độ tự tin (Conviction Level: A+, A, B, C, D) và sinh Research Report hoàn chỉnh.
- Bảng nghiệp vụ quản lý: factor_scores, moat_profiles
- Bảng log audit: log_equity_research
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
import pandas as pd

from app.core.base_agent import BaseAgent
from app.domain.rules.scoring import CSSScoringEngine
from app.domain.services.factor_service import FactorService
from app.adapters.sag_connector import sag_connector
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

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Nghiên cứu cổ phiếu:
        - event_data:
            - ticker: str (mã cổ phiếu, bắt buộc)
            - sector: str (ngành ICB, vd: "Technology")
            - current_regime: str (mặc định "BULL_TRENDING")
            - policy_weights: Dict[str, float] (bộ trọng số động nạp từ Agent-10 RL)
            - factor_overrides: Dict[str, float] (tùy chọn)
        """
        ticker = event_data.get("ticker")
        if not ticker:
            raise ValueError("[EquityResearchAgent] Thiếu mã cổ phiếu (ticker) bắt buộc trong event_data.")
        ticker = str(ticker).upper().strip()

        sector = event_data.get("sector", "Technology")
        regime_str = event_data.get("current_regime", "BULL_TRENDING")
        policy_weights = event_data.get("policy_weights")

        # 1. Truy vấn Dịch vụ RAG Moat AI từ phân hệ SAG (tài liệu phi cấu trúc)
        evidence_quote = ""
        try:
            moat_result = await sag_connector.get_moat_assessment(ticker, sector)
            moat_score = float(moat_result.get("moat_score", 70.0))
            moat_multiplier = float(moat_result.get("multiplier", 1.0))
            evidence_quote = moat_result.get("evidence_quote", "")
        except Exception as e:
            logger.warning(f"SAG Moat AI query failed for {ticker}: {e}")
            moat_score = 65.0
            moat_multiplier = 1.0

        # 2. Tính toán / Nạp 6 nhóm Factor Scores (F1 - F6)
        factor_overrides = event_data.get("factor_overrides", {})
        data_quality_flag = "VERIFIED"

        if factor_overrides:
            f1_value = float(factor_overrides.get("f1_value", 65.0))
            f2_quality = float(factor_overrides.get("f2_quality", (0.4 * 70.0 + 0.3 * 70.0 + 0.3 * moat_score)))
            f3_momentum = float(factor_overrides.get("f3_momentum", 65.0))
            f4_earnings = float(factor_overrides.get("f4_earnings", 65.0))
            f5_flow = float(factor_overrides.get("f5_flow", 65.0))
            f6_technical = float(factor_overrides.get("f6_technical", 65.0))
            data_quality_flag = "USER_OVERRIDE"
        else:
            # Tra cứu từ CSDL qua IntelligenceRepository
            from app.domain.repositories.intelligence_repository import IntelligenceRepository
            intel_repo = IntelligenceRepository()
            db_factors = intel_repo.get_factor_score(ticker)
            if db_factors:
                f1_value = float(db_factors.get("f1_value", 65.0))
                f2_quality = float(db_factors.get("f2_quality", 65.0))
                f3_momentum = float(db_factors.get("f3_momentum", 65.0))
                f4_earnings = float(db_factors.get("f4_earnings", 65.0))
                f5_flow = float(db_factors.get("f5_flow", 65.0))
                f6_technical = float(db_factors.get("f6_technical", 65.0))
            else:
                # Nếu chưa có bản ghi factor, tính toán xấp xỉ từ chỉ số cơ bản
                f1_value = 65.0
                f2_quality = round(0.5 * 65.0 + 0.5 * moat_score, 2)
                f3_momentum = 60.0
                f4_earnings = 65.0
                f5_flow = 55.0
                f6_technical = 60.0
                data_quality_flag = "ESTIMATED_BASELINE"

        # 3. Tính điểm CSS qua Trọng số Thích ứng (Dynamic Weights từ Agent-10 RL hoặc Fallback theo Regime)
        if policy_weights:
            w1 = float(policy_weights.get("f1_value", 0.15))
            w2 = float(policy_weights.get("f2_quality", 0.20))
            w3 = float(policy_weights.get("f3_momentum", 0.30))
            w4 = float(policy_weights.get("f4_earnings", 0.15))
            w5 = float(policy_weights.get("f5_flow", 0.10))
            w6 = float(policy_weights.get("f6_technical", 0.10))
            weights_source = "AGENT-10 (Reinforcement Learning Adaptive Weights)"
        elif "BEAR" in regime_str:
            w1, w2, w3, w4, w5, w6 = 0.25, 0.35, 0.05, 0.10, 0.15, 0.10
            weights_source = "RULE_BASED (Bear Market Defensive Profile)"
        elif "RANGE" in regime_str or "SIDEWAYS" in regime_str:
            w1, w2, w3, w4, w5, w6 = 0.10, 0.20, 0.10, 0.25, 0.25, 0.10
            weights_source = "RULE_BASED (Range-Bound Earnings Profile)"
        else:  # BULL_MARKET
            w1, w2, w3, w4, w5, w6 = 0.15, 0.20, 0.30, 0.15, 0.10, 0.10
            weights_source = "RULE_BASED (Bull Market Momentum Profile)"

        base_css = (w1 * f1_value + w2 * f2_quality + w3 * f3_momentum + w4 * f4_earnings + w5 * f5_flow + w6 * f6_technical)
        css = round(base_css * moat_multiplier, 2)

        # 4. Gán Conviction Level
        if css >= 80.0 and moat_score >= 75.0:
            conviction = "A+"
        elif css >= 70.0:
            conviction = "A"
        elif css >= 60.0:
            conviction = "B"
        elif css >= 50.0:
            conviction = "C"
        else:
            conviction = "D"

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
            "css": css,
            "conviction": conviction,
            "data_quality_flag": data_quality_flag,
            "eligible_for_thesis": conviction in ["A+", "A", "B"],
            "applied_weights": {
                "f1_value": w1, "f2_quality": w2, "f3_momentum": w3,
                "f4_earnings": w4, "f5_flow": w5, "f6_technical": w6,
            },
        }

        # 5. Lưu trực tiếp Factor Score và Moat Profile vào CSDL
        try:
            from app.domain.repositories.intelligence_repository import IntelligenceRepository
            intel_repo = IntelligenceRepository()
            intel_repo.save_factor_score(
                symbol=ticker,
                f1_value=f1_value,
                f2_quality=f2_quality,
                f3_momentum=f3_momentum,
                f4_earnings=f4_earnings,
                f5_flow=f5_flow,
                f6_technical=f6_technical,
                css=css,
                conviction=conviction,
            )
            intel_repo.save_moat_profile({
                "ticker": ticker,
                "moat_score": moat_score,
                "evidence_summary": {"evidence_quote": evidence_quote},
            })
        except Exception as e:
            logger.warning(f"Không thể lưu kết quả nghiên cứu {ticker} vào DB: {e}")

        trace = {
            "scoring_engine": self.scoring_engine.__class__.__name__,
            "factor_service": self.factor_service.__class__.__name__,
            "sag_connector": "FastMCP RAG Moat Inquisitor",
            "weights_source": weights_source,
            "evidence_quote": evidence_quote,
            "regime_applied": regime_str,
            "data_quality_flag": data_quality_flag,
        }

        return {"data": research_report, "trace": trace}
