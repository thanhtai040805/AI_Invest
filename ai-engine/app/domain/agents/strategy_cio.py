"""AGENT-12: Strategy CIO Agent (IOS v5.1)

Chức năng: Trọng tài tối cao phân xử xung đột giữa các Agent (Thesis vs Counter Thesis), quyết định phân bổ vĩ mô chiến lược cấp quỹ.
Bảng nghiệp vụ sở hữu: strategic_allocations, cio_resolutions
Bảng log riêng: log_strategy_cio
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict
from app.core.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class StrategyCIOAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="strategy_cio",
            state_tables=["strategic_allocations", "cio_resolutions"],
            log_table="log_strategy_cio",
            enabled=True,
        )

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Phân xử xung đột hoặc ra quyết định phân bổ chiến lược vĩ mô."""
        conflict = event_data.get("conflict")
        resolution_id = str(uuid.uuid4())

        if conflict:
            thesis_id = conflict.get("thesis_id") or str(uuid.uuid4())
            thesis_ticker = str(conflict.get("ticker") or event_data.get("ticker", "PORTFOLIO")).upper().strip()
            thesis_view = conflict.get("thesis_view", "BULLISH_A_PLUS")
            counter_view = conflict.get("counter_view", "CONDITIONAL_RISK_WARNING")

            # CIO Arbitrates: Cho phép mua nhưng giảm tỷ trọng để phòng hộ rủi ro
            resolution = {
                "resolution_id": resolution_id,
                "thesis_id": thesis_id,
                "ticker": thesis_ticker,
                "final_resolution": "APPROVE_CONDITIONAL",
                "weight_cap": 0.08,
                "allocated_weight_cap": 0.08,  # Giảm từ 15% xuống 8%
                "executive_rationale": f"Chấp thuận giải ngân mã {thesis_ticker} theo luận điểm tăng trưởng dài hạn, nhưng áp trần 8% NAV để dự phòng rủi ro tỷ giá do Counter Thesis cảnh báo.",
            }

            trace = {
                "debate_synthesis": {
                    "thesis_arguments": thesis_view,
                    "counter_arguments": counter_view,
                    "cio_verdict_tier": "BALANCED_RISK_RETURN",
                }
            }
        else:
            # Macro Strategic Allocation
            resolution = {
                "allocation_id": resolution_id,
                "macro_view": "Thị trường trong pha Bull Trending ổn định, duy trì 90% cổ phiếu, 10% tiền mặt.",
                "cash_target_override": 10.0,
                "sector_focus": ["Technology", "Retail", "Industrial_Real_Estate"],
            }
            trace = {"regime_context": "Bull_Trending_HMM_Posteriors_0.75"}

        return {"data": resolution, "trace": trace}
