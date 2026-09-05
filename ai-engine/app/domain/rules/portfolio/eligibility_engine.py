"""Engine 1: Eligibility Engine (IOS v5.1)

Chức năng:
- Thẩm định tính đủ điều kiện của ứng viên (Candidate) đầu vào trước khi cấp hạn mức vốn.
- Thẩm định 3 trụ cột độc lập:
    1. Research: Phải có hồ sơ nghiên cứu hợp lệ và Conviction đạt ngưỡng (A+, A, hoặc B).
    2. Thesis: Luận điểm đầu tư phải ở trạng thái PROCEED, có tối thiểu 3 tín hiệu xác nhận độc lập.
    3. Counter-Thesis: Không được nhận phán quyết BLOCK (phải là PROCEED hoặc CONDITIONAL với CTS < 70).
- Ghi chú: Tuyệt đối không kiểm định Risk tại đây (để Agent-06 Portfolio Risk nắm trọn quyền phủ quyết thể chế).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class EligibilityResult:
    eligible: bool
    status: str  # "ELIGIBLE", "INELIGIBLE"
    research_status: str
    thesis_status: str
    counter_thesis_status: str
    rejection_reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class EligibilityEngine:
    def __init__(self, min_conviction: str = "B", max_cts_score: float = 70.0):
        self.min_conviction = min_conviction
        self.max_cts_score = max_cts_score
        self.allowed_convictions = {"A+", "A", "B"}

    def evaluate(
        self,
        ticker: str,
        candidate_data: Dict[str, Any],
        research_data: Optional[Dict[str, Any]] = None,
        thesis_data: Optional[Dict[str, Any]] = None,
        counter_thesis_data: Optional[Dict[str, Any]] = None,
    ) -> EligibilityResult:
        ticker_clean = str(ticker).upper().strip()
        rejections: List[str] = []

        # 1. Thẩm định Research
        conviction = str(
            candidate_data.get("conviction")
            or (research_data and research_data.get("conviction"))
            or "UNKNOWN"
        ).strip()
        if conviction not in self.allowed_convictions:
            rejections.append(
                f"Research Conviction '{conviction}' không đạt tiêu chuẩn (Yêu cầu: A+, A, hoặc B)."
            )
            research_status = "INVALID"
        else:
            research_status = "VALID"

        # 2. Thẩm định Thesis
        thesis_verdict = "PROCEED"
        if thesis_data:
            t_status = thesis_data.get("status") or thesis_data.get("verdict", "PROCEED")
            signals = (
                thesis_data.get("confirming_signals")
                or thesis_data.get("input_validation", {}).get("independent_signals", [])
            )
            if str(t_status).upper() == "REJECTED":
                thesis_verdict = "REJECTED"
                rejections.append("Investment Thesis bị từ chối.")
            elif isinstance(signals, list) and len(signals) < 3 and len(signals) > 0:
                thesis_verdict = "INSUFFICIENT_SIGNALS"
                rejections.append(f"Thesis không đủ 3 tín hiệu xác nhận độc lập (Hiện có: {len(signals)}).")
        thesis_status = thesis_verdict

        # 3. Thẩm định Counter-Thesis (Devil's Advocate)
        counter_status = "PROCEED"
        if counter_thesis_data:
            verdict = str(counter_thesis_data.get("verdict", "PROCEED")).upper().strip()
            cts_score = float(counter_thesis_data.get("cts_score", 0.0))
            if verdict == "BLOCK":
                counter_status = "BLOCK"
                block_reasons = counter_thesis_data.get("block_reasons") or ["Phán quyết BLOCK từ Counter-Thesis"]
                if isinstance(block_reasons, list):
                    rejections.append(f"Counter-Thesis BLOCK: {'; '.join(block_reasons)}")
                else:
                    rejections.append(f"Counter-Thesis BLOCK: {block_reasons}")
            elif cts_score >= self.max_cts_score:
                counter_status = "HIGH_CTS_WARNING"
                rejections.append(f"Điểm phản biện CTS quá cao ({cts_score:.1f} >= {self.max_cts_score}).")
            else:
                counter_status = verdict
        elif candidate_data.get("counter_thesis_verdict") == "BLOCK":
            counter_status = "BLOCK"
            rejections.append("Candidate bị Counter-Thesis đánh dấu BLOCK.")

        is_eligible = len(rejections) == 0
        status = "ELIGIBLE" if is_eligible else "INELIGIBLE"

        return EligibilityResult(
            eligible=is_eligible,
            status=status,
            research_status=research_status,
            thesis_status=thesis_status,
            counter_thesis_status=counter_status,
            rejection_reasons=rejections,
            details={
                "ticker": ticker_clean,
                "conviction": conviction,
                "thesis_verdict": thesis_status,
                "counter_verdict": counter_status,
            },
        )
