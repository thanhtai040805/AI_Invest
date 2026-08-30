"""Capital Degradation Controller (CDC) — Model Risk & Degradation Gatekeeper.

Mục tiêu:
Giám sát sự suy thoái của mô hình định lượng (Information Coefficient - IC Decay)
và sự suy giảm chất lượng thực thi (Slippage Spike) để bảo vệ vốn trước rủi ro mô hình (Model Risk).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class CDCTier(Enum):
    NORMAL = "NORMAL"
    YELLOW = "YELLOW"
    ORANGE = "ORANGE"
    RED = "RED"


@dataclass
class CDCEvaluation:
    tier: CDCTier
    is_cdc_active: bool
    ic_decay_pct: float
    persistence_sessions: int
    slippage_spike_detected: bool
    recommended_min_cash_pct: float
    sizing_scale_factor: float
    action_recommended: str  # "PASS", "REDUCE_SIZE_50", "FREEZE_TRADES"
    reason: str


class CDCController:
    """Bộ kiểm soát suy thoái vốn và quản trị rủi ro suy giảm hiệu quả mô hình."""

    def evaluate_model_health(
        self,
        ic_decay_pct: float = 0.0,
        persistence_sessions: int = 0,
        actual_slippage_pct: float = 0.0,
        baseline_slippage_pct: float = 0.005,
    ) -> CDCEvaluation:
        """
        Đánh giá sức khỏe của hệ thống mô hình định lượng.
        - ic_decay_pct: Mức độ suy giảm hệ số IC (0.0 đến 1.0)
        - persistence_sessions: Số phiên liên tiếp IC bị suy thoái
        - actual_slippage_pct: Tỷ lệ trượt giá thực tế
        - baseline_slippage_pct: Tỷ lệ trượt giá chuẩn (0.5%)
        """
        slippage_spike = (
            actual_slippage_pct > (baseline_slippage_pct * 2.0)
            if baseline_slippage_pct > 0
            else False
        )

        # 1. Tầng RED: IC suy giảm > 50% kéo dài >= 5 phiên HOẶC Trượt giá tăng vọt kèm IC yếu
        if (ic_decay_pct >= 0.50 and persistence_sessions >= 5) or (ic_decay_pct >= 0.60):
            reason = (
                f"CDC RED ACTIVE: Hiệu quả mô hình suy giảm nghiêm trọng (IC Decay: {ic_decay_pct*100:.1f}%, "
                f"Kéo dài {persistence_sessions} phiên liên tiếp). Đóng băng mở vị thế mới và nâng tiền mặt >= 60%."
            )
            logger.critical(f"[CDCController] {reason}")
            return CDCEvaluation(
                tier=CDCTier.RED,
                is_cdc_active=True,
                ic_decay_pct=round(ic_decay_pct, 3),
                persistence_sessions=persistence_sessions,
                slippage_spike_detected=slippage_spike,
                recommended_min_cash_pct=60.0,
                sizing_scale_factor=0.0,
                action_recommended="FREEZE_TRADES",
                reason=reason,
            )

        # 2. Tầng ORANGE: IC suy giảm > 35% kéo dài >= 3 phiên
        if (ic_decay_pct >= 0.35 and persistence_sessions >= 3) or slippage_spike:
            reason = (
                f"CDC ORANGE WARNING: Mô hình có dấu hiệu suy thoái (IC Decay: {ic_decay_pct*100:.1f}%, "
                f"Persistence: {persistence_sessions} phiên"
                + (", Phát hiện trượt giá bất thường)." if slippage_spike else ").")
                + " Giảm 50% quy mô các vị thế mua mới và nâng tiền mặt >= 40%."
            )
            logger.warning(f"[CDCController] {reason}")
            return CDCEvaluation(
                tier=CDCTier.ORANGE,
                is_cdc_active=False,
                ic_decay_pct=round(ic_decay_pct, 3),
                persistence_sessions=persistence_sessions,
                slippage_spike_detected=slippage_spike,
                recommended_min_cash_pct=40.0,
                sizing_scale_factor=0.50,
                action_recommended="REDUCE_SIZE_50",
                reason=reason,
            )

        # 3. Tầng YELLOW: IC suy giảm > 20%
        if ic_decay_pct >= 0.20:
            reason = (
                f"CDC YELLOW ALERT: Cảnh báo sớm hiệu quả mô hình sụt giảm (IC Decay: {ic_decay_pct*100:.1f}%)."
            )
            return CDCEvaluation(
                tier=CDCTier.YELLOW,
                is_cdc_active=False,
                ic_decay_pct=round(ic_decay_pct, 3),
                persistence_sessions=persistence_sessions,
                slippage_spike_detected=slippage_spike,
                recommended_min_cash_pct=25.0,
                sizing_scale_factor=0.80,
                action_recommended="PASS",
                reason=reason,
            )

        # 4. Tầng NORMAL: Mô hình hoạt động tốt
        return CDCEvaluation(
            tier=CDCTier.NORMAL,
            is_cdc_active=False,
            ic_decay_pct=round(ic_decay_pct, 3),
            persistence_sessions=persistence_sessions,
            slippage_spike_detected=False,
            recommended_min_cash_pct=10.0,
            sizing_scale_factor=1.0,
            action_recommended="PASS",
            reason="Mô hình định lượng và chất lượng thực thi lệnh hoạt động ổn định.",
        )


cdc_controller = CDCController()
