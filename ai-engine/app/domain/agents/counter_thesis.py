"""AGENT-05: Counter Thesis Agent (IOS v5.1)

Gom nhóm và điều phối các engines thực tế:
- CounterThesisEngine (app/domain/rules/counter_thesis.py)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from app.core.base_agent import BaseAgent
from app.domain.rules.counter_thesis import CounterThesisEngine, Verdict
from app.adapters.sag_connector import sag_connector

logger = logging.getLogger(__name__)


class CounterThesisAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="counter_thesis",
            state_tables=["counter_thesis_verdicts"],
            log_table="log_counter_thesis",
            enabled=True,
        )
        self.counter_thesis_engine = CounterThesisEngine()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Phản biện luận điểm đầu tư bằng CounterThesisEngine thực tế."""
        thesis = event_data.get("investment_thesis", {})
        ticker = thesis.get("ticker") or event_data.get("ticker")
        if not ticker:
            raise ValueError("[CounterThesisAgent] Thiếu thông tin mã cổ phiếu (ticker) trong thesis.")
        ticker = str(ticker).upper().strip()

        thesis_id = thesis.get("thesis_id")
        signals = [s.get("detail", "") for s in thesis.get("confirming_signals", []) if isinstance(s, dict)]

        # 1. Gọi CounterThesisEngine thực tế để kiểm tra Rule of Three & Holes
        thesis_text = f"Luận điểm mua {ticker} với Target Price {thesis.get('target_price', 0)}"
        try:
            report = await self.counter_thesis_engine.analyze_thesis(ticker, thesis_text, signals)
            rule_of_three_passed = report.rule_of_three_passed
            engine_verdict = report.verdict
            holes = list(report.holes)
            rationale = report.rationale
        except Exception as e:
            logger.warning(f"CounterThesisEngine fallback check: {e}")
            unique_signals = len(set(signals))
            rule_of_three_passed = unique_signals >= 3
            if not rule_of_three_passed:
                engine_verdict = Verdict.REJECT
                holes = ["Vi phạm Hard Law Điều 3: Cần ít nhất 3 tín hiệu độc lập."]
                rationale = f"Luận đề chỉ có {unique_signals} tín hiệu. Yêu cầu tối thiểu 3."
            else:
                engine_verdict = Verdict.APPROVE
                holes = []
                rationale = "Đạt điều kiện Hard Law Điều 3 (Rule of Three) và không phát hiện lỗ hổng trọng yếu."

        # 2. Kiểm tra cờ GIL qua SAG (Failsafe: lỗi kết nối thì BLOCK theo AGENTS.md)
        gil_error = None
        try:
            gil_info = await sag_connector.get_gil_relationships(ticker)
            gil_flag = gil_info.get("gil_flag", "PASS")
        except Exception as e:
            logger.error(f"GIL Service error for {ticker}: {e}")
            gil_flag = "DATA_ERROR"
            gil_error = str(e)

        if gil_flag == "CATASTROPHIC" or engine_verdict == Verdict.REJECT:
            verdict = "BLOCK"
            cts_score = 100.0
            if gil_flag == "CATASTROPHIC":
                holes.append("Phát hiện rủi ro sở hữu chéo CATASTROPHIC từ đồ thị GIL.")
        elif gil_flag == "DATA_ERROR":
            # Tuân thủ Hard Law Failsafe: Dữ liệu GIL lỗi không đủ an toàn để giải ngân
            verdict = "BLOCK"
            cts_score = 90.0
            holes.append(f"Không thể xác minh rủi ro sở hữu chéo GIL (Lỗi kết nối: {gil_error}). Kích hoạt Failsafe BLOCK.")
        elif gil_flag == "WARNING" or engine_verdict == Verdict.NEEDS_REVISION:
            verdict = "CONDITIONAL"
            cts_score = 55.0
        else:
            verdict = "PROCEED"
            cts_score = 25.0

        verdict_output = {
            "thesis_id": thesis_id,
            "ticker": ticker,
            "cts_score": cts_score,
            "verdict": verdict,
            "rule_of_three_passed": rule_of_three_passed,
            "block_reasons": holes,
            "rationale": rationale,
        }

        trace = {
            "counter_thesis_engine": self.counter_thesis_engine.__class__.__name__,
            "engine_verdict": engine_verdict.value if hasattr(engine_verdict, "value") else str(engine_verdict),
            "gil_status": gil_flag,
        }

        return {"data": verdict_output, "trace": trace}
