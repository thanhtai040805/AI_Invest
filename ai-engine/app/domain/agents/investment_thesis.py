"""AGENT-04: Investment Thesis Agent (IOS v5.1)

Chức năng:
- Xây dựng luận điểm đầu tư có cấu trúc (Investment Thesis) cho các mã có Conviction >= B (CSS >= 60-65).
- Tự động lọc Hard Filter Lớp 0 (GIL CATASTROPHIC) -> REJECT ngay.
- Tự động nhận diện Ngòi nổ Catalyst từ 6 nhóm Factor Score (F1-F6).
- Tính toán Target Price thích ứng đa mô hình (loại bỏ DCF cho timeline <= 3 tháng).
- Xác lập bắt buộc tối thiểu 3 tín hiệu độc lập, 3 kịch bản Pre-Mortem và điều kiện hủy luận điểm Invalidation.
- Đóng gói Output Schema JSON chuẩn hóa bàn giao cho Counter Thesis Agent (Agent-05).
- Bảng nghiệp vụ quản lý: investment_theses
- Bảng log audit: log_investment_thesis
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.domain.rules.thesis_engine import ThesisEngine
from app.domain.rules.catalyst_validator import CatalystValidator

logger = logging.getLogger(__name__)


class InvestmentThesisAgent(BaseAgent):
    """
    AGENT-04: Chuyên viên Xây dựng Luận điểm Đầu tư.
    Trả lời 3 câu hỏi cốt tử: Tại sao bây giờ? Tại sao cổ phiếu này? Tôi có thể sai như thế nào?
    """

    def __init__(self):
        super().__init__(
            agent_name="investment_thesis",
            state_tables=["investment_theses"],
            log_table="log_investment_thesis",
            enabled=True,
        )
        self.thesis_engine = ThesisEngine()
        self.catalyst_validator = CatalystValidator()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Quy trình xử lý chuẩn hóa của Investment Thesis Agent:
        - event_data:
            - research_report: Dict[str, Any] (từ Agent-03)
            - market_context: Dict[str, Any] (từ Agent-01)
            - valuation_inputs: {pe_price, ev_ebitda_price, dcf_price}
            - timeline_months: int (1, 3, 6; mặc định 3)
            - seq_num: int (mặc định 1)
            - custom_catalyst_desc: str (tùy chọn)
        """
        research_report = event_data.get("research_report", {})
        ticker = research_report.get("ticker") or event_data.get("ticker")
        if not ticker:
            raise ValueError("[InvestmentThesisAgent] Thiếu thông tin mã cổ phiếu (ticker) bắt buộc.")
        ticker = str(ticker).upper().strip()

        market_context = event_data.get("market_context", {})
        val_inputs = event_data.get("valuation_inputs", {})
        timeline_months = int(event_data.get("timeline_months", 3))
        seq_num = int(event_data.get("seq_num", 1))
        custom_catalyst_desc = event_data.get("custom_catalyst_desc")

        # 1. Truy vấn giá hiện tại nếu chưa có
        current_price = float(research_report.get("current_price") or market_context.get("current_price", 0.0))
        if current_price <= 0:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                latest_m = m_repo.get_market_data_daily(ticker, limit=1)
                if latest_m and "close" in latest_m[0]:
                    current_price = float(latest_m[0]["close"])
            except Exception:
                pass
            if current_price <= 0:
                current_price = 10000.0
            research_report["current_price"] = current_price

        # 2. Xây dựng structured thesis qua ThesisEngine
        is_eligible, structured_payload, message = self.thesis_engine.build_structured_thesis_output(
            ticker=ticker,
            research_report=research_report,
            market_context=market_context,
            valuation_inputs=val_inputs,
            timeline_months=timeline_months,
            seq_num=seq_num,
            custom_catalyst_desc=custom_catalyst_desc,
        )

        if not is_eligible:
            logger.info(f"[InvestmentThesisAgent] Ticker {ticker} không đủ điều kiện sinh thesis: {message}")
            return {
                "data": {
                    "ticker": ticker,
                    "status": "REJECTED" if "REJECT" in message else "WAIT_OR_SKIP",
                    "reason": message,
                },
                "trace": {
                    "thesis_engine": self.thesis_engine.__class__.__name__,
                    "decision": "SKIP_THESIS",
                }
            }

        # 3. Kiểm tra rò rỉ tin tức PEAI qua CatalystValidator
        volume_data_3w = event_data.get("volume_data_3w", [300000.0] * 15)
        price_data_3w = event_data.get("price_data_3w", [current_price * 0.98, current_price * 0.99, current_price])
        peai_status = self.catalyst_validator.check_peai_accumulation(volume_data_3w, price_data_3w, sue_score=2.0)

        structured_payload["input_validation"]["peai_status"] = peai_status

        # 4. Lưu luận điểm đầu tư vào CSDL qua IntelligenceRepository
        try:
            from app.domain.repositories.intelligence_repository import IntelligenceRepository
            intel_repo = IntelligenceRepository()
            intel_repo.save_investment_thesis({
                "thesis_id": structured_payload["thesis_id"],
                "ticker": ticker,
                "catalyst_type": structured_payload["thesis_body"]["catalyst"]["primary_type"],
                "catalyst_description": structured_payload["thesis_body"]["catalyst"]["description"],
                "timeline_months": timeline_months,
                "target_price": structured_payload["thesis_body"]["price_target"]["base_case"],
                "entry_price_estimated": current_price,
                "confirming_signals": structured_payload["input_validation"]["independent_signals"],
                "invalidation_conditions": structured_payload["thesis_body"]["exit_conditions"]["invalidation_triggers"],
                "pre_mortem_scenarios": structured_payload["thesis_body"]["pre_mortem"],
                "status": "PENDING_COUNTER_ANALYSIS",
            })
        except Exception as e:
            logger.warning(f"Không thể lưu thesis {structured_payload['thesis_id']} vào DB: {e}")

        trace = {
            "thesis_engine": self.thesis_engine.__class__.__name__,
            "catalyst_validator": self.catalyst_validator.__class__.__name__,
            "thesis_id": structured_payload["thesis_id"],
            "peai_status": peai_status,
            "status": "TRANSFER_TO_COUNTER_THESIS",
        }

        return {"data": structured_payload, "trace": trace}


investment_thesis_agent = InvestmentThesisAgent()
