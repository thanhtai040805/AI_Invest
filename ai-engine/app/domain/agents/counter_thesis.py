"""AGENT-05: Counter Thesis Agent (IOS v5.1)

Mục đích:
- Chủ động tìm lý do để BÁC BỎ thesis. Devil's Advocate bắt buộc.
- Tính toán 3-Tier Counter-Thesis Score (Base CTS + ML Interaction + Regime Multiplier).
- Đánh giá ngoại lệ Bắt đáy Khoa học (Capitulation Entry - Bẫy 3).
- Phán quyết: PROCEED / CONDITIONAL (kèm execution_constraints) / BLOCK (kèm block_reasons).
- Bảng nghiệp vụ quản lý: counter_thesis_verdicts
- Bảng log audit: log_counter_thesis
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from datetime import date

from app.core.base_agent import BaseAgent
from app.domain.rules.counter_thesis import CounterThesisEngine, Verdict
from app.adapters.sag_connector import sag_connector
from app.domain.rules.beneish import beneish_engine

logger = logging.getLogger(__name__)


class CounterThesisAgent(BaseAgent):
    """
    AGENT-05: Chuyên viên Phản biện Luận điểm Đầu tư (Devil's Advocate).
    """

    def __init__(self):
        super().__init__(
            agent_name="counter_thesis",
            state_tables=["counter_thesis_verdicts"],
            log_table="log_counter_thesis",
            enabled=True,
        )
        self.counter_thesis_engine = CounterThesisEngine()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xử lý phản biện toàn diện luận điểm đầu tư:
        - event_data:
            - investment_thesis: Dict[str, Any] (từ Agent-04)
            - market_data: Dict[str, Any] (từ Agent-01: regime, csad, derivative_basis, foreign_net_flow)
            - stock_data: Dict[str, Any] (từ MarketData: pe, pb, volume, ma20, current_price)
            - risk_overrides: Dict[str, float] (tùy chọn)
        """
        thesis = event_data.get("investment_thesis", {})
        ticker = thesis.get("ticker") or event_data.get("ticker")
        if not ticker:
            raise ValueError("[CounterThesisAgent] Thiếu thông tin mã cổ phiếu (ticker) trong thesis hoặc event_data.")
        ticker = str(ticker).upper().strip()

        thesis_id = thesis.get("thesis_id", f"THESIS_{ticker}")
        market_data = event_data.get("market_data", {})
        stock_data = event_data.get("stock_data", {})
        risk_overrides = event_data.get("risk_overrides", {})

        # 1. Truy vấn Dữ liệu Sở hữu chéo & Đồ thị GIL từ SAG Connector
        gil_flag = "PASS"
        ocr_score = 0.0
        cycles_detected = 0
        gil_error = None
        try:
            gil_info = await sag_connector.get_gil_relationships(ticker)
            gil_flag = str(gil_info.get("gil_flag", "PASS")).upper()
            ocr_score = float(gil_info.get("ocr_score", 0.0))
            cycles_detected = int(gil_info.get("cycles_detected", 0))
        except Exception as e:
            logger.warning(f"Lỗi truy vấn GIL từ SAG cho {ticker}: {e}")
            gil_flag = "DATA_ERROR"
            gil_error = str(e)

        # 2. Truy vấn Beneish M-Score & Phải thu từ BeneishEngine
        beneish_risk = 20.0
        receivable_spike = 20.0
        try:
            m_res = beneish_engine.calculate_m_score(ticker, date.today())
            m_score = m_res.get("m_score")
            if m_score is not None:
                if m_score > -1.78:
                    beneish_risk = 100.0  # Vùng rủi ro cao
                elif m_score >= -2.22:
                    beneish_risk = 50.0   # Vùng cảnh báo
                else:
                    beneish_risk = 10.0   # Vùng an toàn
                
                # Check DSRI (Days Sales in Receivables Index)
                dsri = m_res.get("variables", {}).get("dsri", 1.0)
                if dsri > 1.30:
                    receivable_spike = 80.0
                elif dsri > 1.15:
                    receivable_spike = 50.0
                else:
                    receivable_spike = 15.0
        except Exception as e:
            logger.warning(f"Lỗi tính toán M-Score cho {ticker}: {e}")
            beneish_risk = 40.0

        # 3. Chuẩn hóa Risk Features cho Base CTS (Đã loại bỏ Margin Tension)
        risk_features = {
            "gil_risk": 100.0 if gil_flag == "CATASTROPHIC" else (60.0 if gil_flag == "WARNING" else (80.0 if gil_flag == "DATA_ERROR" else ocr_score)),
            "gil_status": gil_flag,
            "beneish_risk": float(risk_overrides.get("beneish_risk", beneish_risk)),
            "receivable_spike": float(risk_overrides.get("receivable_spike", receivable_spike)),
            "graph_rpt_risk": float(risk_overrides.get("graph_rpt_risk", 75.0 if cycles_detected > 0 else 20.0)),
            "macro_headwind": float(risk_overrides.get("macro_headwind", 40.0 if "BEAR" in market_data.get("current_regime", "") else 20.0)),
            "liquidity_stress": float(risk_overrides.get("liquidity_stress", 60.0 if market_data.get("breadth_above_ma50_pct", 50.0) < 30.0 else 25.0)),
            "missing_data": float(risk_overrides.get("missing_data", 80.0 if gil_flag == "DATA_ERROR" else 15.0)),
        }

        # 4. Chạy toàn bộ quy trình phản biện qua CounterThesisEngine
        report = await self.counter_thesis_engine.evaluate_counter_thesis(
            ticker=ticker,
            thesis_payload=thesis,
            risk_features=risk_features,
            market_data=market_data,
            stock_data=stock_data,
        )

        verdict_str = report.verdict.value if hasattr(report.verdict, "value") else str(report.verdict)

        verdict_output = {
            "thesis_id": report.thesis_id,
            "ticker": report.ticker,
            "base_cts": report.base_cts,
            "interaction_multiplier": report.interaction_multiplier,
            "regime_multiplier": report.regime_multiplier,
            "cts_score": report.final_cts,
            "verdict": verdict_str,
            "rule_of_three_passed": report.rule_of_three_passed,
            "is_capitulation_rebound": report.is_capitulation_rebound,
            "block_reasons": report.block_reasons,
            "holes": report.holes,
            "execution_constraints": report.execution_constraints,
            "rationale": report.rationale,
        }

        # 5. Lưu phán quyết vào CSDL qua IntelligenceRepository
        try:
            from app.domain.repositories.intelligence_repository import IntelligenceRepository
            intel_repo = IntelligenceRepository()
            intel_repo.save_counter_thesis_verdict(verdict_output)
        except Exception as e:
            logger.warning(f"Không thể lưu counter_thesis_verdict cho {thesis_id}: {e}")

        trace = {
            "counter_thesis_engine": self.counter_thesis_engine.__class__.__name__,
            "gil_status": gil_flag,
            "base_cts": report.base_cts,
            "interaction_multiplier": report.interaction_multiplier,
            "regime_multiplier": report.regime_multiplier,
            "final_cts": report.final_cts,
            "verdict": verdict_str,
        }

        return {"data": verdict_output, "trace": trace}


counter_thesis_agent = CounterThesisAgent()
