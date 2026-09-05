"""Governance Change Engine — Model Risk Management & Change Control (IOS v5.1).

Nhiệm vụ thể chế:
1. Thẩm định mọi Yêu cầu Thay đổi (Change Request - CR) thuật toán, trọng số factor hoặc tham số Kelly.
2. Đánh giá Tác động Danh mục (Impact Analysis: Turnover Shock, Concentration Drift).
3. Cổng Thẩm định Ngoài Mẫu (Out-of-Sample Walk-Forward Validation Gatekeeper).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class ChangeStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATE_TO_CIO = "ESCALATE_TO_CIO"


@dataclass
class ChangeRequest:
    cr_id: str
    initiator_agent: str
    target_component: str  # "rl_factor_weights", "kelly_win_rate_matrix", "policy_parameters"
    proposed_changes: Dict[str, Any]
    current_state: Dict[str, Any] = field(default_factory=dict)
    oos_returns: List[float] = field(default_factory=list)
    rationale: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ChangeEvaluationResult:
    approved: bool
    status: ChangeStatus
    cr_id: str
    annualized_sharpe: float
    max_drawdown: float
    weight_turnover_delta: float
    reason: str
    requires_cio_resolution: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


class GovernanceChangeEngine:
    """
    Trụ Cột 3 (CHANGE): Kiểm soát Thay Đổi & Rủi ro Mô hình Học máy.
    """

    def __init__(
        self,
        min_sharpe: float = 1.2,
        max_drawdown_limit: float = 0.10,
        max_weight_turnover_threshold: float = 0.30,
        min_oos_sample_size: int = 20,
    ):
        self.min_sharpe = min_sharpe
        self.max_drawdown_limit = max_drawdown_limit
        self.max_weight_turnover_threshold = max_weight_turnover_threshold
        self.min_oos_sample_size = min_oos_sample_size

    def analyze_impact(self, proposed_changes: Dict[str, Any], current_state: Dict[str, Any]) -> float:
        """
        Đo lường mức độ xáo trộn trọng số phân bổ vốn (Weight Turnover Delta).
        Tổng độ lệch tuyệt đối giữa trọng số cũ và mới.
        """
        total_delta = 0.0
        keys = set(proposed_changes.keys()).union(set(current_state.keys()))

        for k in keys:
            v_new = proposed_changes.get(k, 0.0)
            v_old = current_state.get(k, 0.0)
            if isinstance(v_new, (int, float)) and isinstance(v_old, (int, float)):
                total_delta += abs(float(v_new) - float(v_old))

        return round(total_delta, 4)

    def validate_oos_returns(self, oos_returns: List[float]) -> Tuple[bool, float, float, str]:
        """
        Kiểm tra lợi nhuận ngoài mẫu (Walk-Forward Out-Of-Sample):
        - Tối thiểu 20 phiên thực tế.
        - Sharpe thường niên hóa >= 1.2.
        - Max Drawdown <= 10%.
        """
        if len(oos_returns) < self.min_oos_sample_size:
            return (
                False,
                0.0,
                0.0,
                f"INSUFFICIENT_SAMPLE: Dữ liệu OOS chỉ có {len(oos_returns)} phiên (< {self.min_oos_sample_size} phiên chuẩn).",
            )

        arr = np.array(oos_returns, dtype=float)
        mean_ret = float(np.mean(arr))
        std_ret = float(np.std(arr)) if np.std(arr) > 0 else 1e-4
        sharpe = float((mean_ret / std_ret) * np.sqrt(245))

        cum_ret = np.cumprod(1.0 + arr)
        peak = np.maximum.accumulate(cum_ret)
        drawdown = (cum_ret - peak) / peak
        max_dd = abs(float(np.min(drawdown)))

        if sharpe < self.min_sharpe:
            return (
                False,
                sharpe,
                max_dd,
                f"OOS_SHARPE_BELOW_GATE: Sharpe ngoài mẫu ({sharpe:.2f}) không đạt ngưỡng tối thiểu {self.min_sharpe:.2f}.",
            )

        if max_dd > self.max_drawdown_limit:
            return (
                False,
                sharpe,
                max_dd,
                f"OOS_MAX_DRAWDOWN_EXCEEDED: Max drawdown ngoài mẫu ({max_dd*100:.1f}%) vượt trần {self.max_drawdown_limit*100:.0f}%.",
            )

        return True, sharpe, max_dd, "OOS Validation thỏa mãn tiêu chuẩn lượng hóa."

    def evaluate_change_request(self, cr: ChangeRequest) -> ChangeEvaluationResult:
        """
        Quy trình thẩm định toàn diện một Đề xuất Thay đổi (Change Request Evaluation).
        """
        # 1. Kiểm tra quyền phát đề xuất
        if cr.initiator_agent not in ("reinforcement_learning", "strategy_cio", "system_governance"):
            return ChangeEvaluationResult(
                approved=False,
                status=ChangeStatus.REJECTED,
                cr_id=cr.cr_id,
                annualized_sharpe=0.0,
                max_drawdown=0.0,
                weight_turnover_delta=0.0,
                reason=f"Agent '{cr.initiator_agent}' không có thẩm quyền phát sinh Change Request.",
            )

        # 2. Phân tích Tác động (Impact Analysis)
        turnover_delta = self.analyze_impact(cr.proposed_changes, cr.current_state)

        # 3. Kiểm định Ngoài mẫu (OOS Validation)
        oos_ok, sharpe, max_dd, oos_msg = self.validate_oos_returns(cr.oos_returns)

        # 4. Phân loại Phán quyết
        # Nếu xáo trộn danh mục quá lớn (> 30%) nhưng OOS tốt -> Cần CIO phê duyệt thủ công
        if turnover_delta > self.max_weight_turnover_threshold:
            return ChangeEvaluationResult(
                approved=False,
                status=ChangeStatus.ESCALATE_TO_CIO,
                cr_id=cr.cr_id,
                annualized_sharpe=round(sharpe, 2),
                max_drawdown=round(max_dd, 4),
                weight_turnover_delta=turnover_delta,
                reason=f"TURNOVER_SHOCK: Thay đổi làm xáo trộn danh mục ({turnover_delta*100:.1f}% > {self.max_weight_turnover_threshold*100:.0f}%). Yêu cầu Strategy CIO phán quyết.",
                requires_cio_resolution=True,
                details={"turnover_delta": turnover_delta, "oos_sharpe": sharpe, "oos_max_dd": max_dd},
            )

        if not oos_ok:
            return ChangeEvaluationResult(
                approved=False,
                status=ChangeStatus.REJECTED,
                cr_id=cr.cr_id,
                annualized_sharpe=round(sharpe, 2),
                max_drawdown=round(max_dd, 4),
                weight_turnover_delta=turnover_delta,
                reason=oos_msg,
                requires_cio_resolution=False,
                details={"oos_reason": oos_msg},
            )

        return ChangeEvaluationResult(
            approved=True,
            status=ChangeStatus.APPROVED,
            cr_id=cr.cr_id,
            annualized_sharpe=round(sharpe, 2),
            max_drawdown=round(max_dd, 4),
            weight_turnover_delta=turnover_delta,
            reason=f"Chấp thuận thay đổi (Sharpe={sharpe:.2f}, MaxDD={max_dd*100:.1f}%, Turnover Delta={turnover_delta*100:.1f}%).",
            requires_cio_resolution=False,
            details={"approved_changes": cr.proposed_changes},
        )
