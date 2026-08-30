"""Drawdown & Phased Recovery Protocol — Vietnamized Institutional Risk Engine.

Mục tiêu:
Triển khai nguyên tắc thể chế: "De-risk Nhanh — Re-risk Chậm".
- Khi NAV sụt giảm từ đỉnh -> Ngay lập tức cắt giảm tỷ trọng, nâng trần tiền mặt.
- Khi NAV hồi phục -> KHÔNG tự động bung sức mua ngay; bắt buộc quan sát tối thiểu 2-3 phiên
  kèm điều kiện Tail Risk và Độ rộng thị trường an toàn trước khi tăng từng bước +5% exposure.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class DrawdownTier(Enum):
    GREEN = "GREEN"  # DD <= 3%: Bình thường
    ALERT = "ALERT"  # DD > 3%: Cảnh giác, quan sát chặt
    YELLOW = "YELLOW"  # DD > 5%: Giảm 25% exposure
    ORANGE = "ORANGE"  # DD > 10%: Giảm 50% exposure
    RED = "RED"  # DD > 15%: Giảm 75% exposure + Đóng băng mua mới


@dataclass
class DrawdownEvaluation:
    current_drawdown_pct: float
    peak_nav: float
    current_nav: float
    tier: DrawdownTier
    exposure_scale_factor: float  # 1.0 (Full), 0.75 (-25%), 0.50 (-50%), 0.25 (-75%)
    min_cash_target_pct: float
    re_risking_state: str
    action_description: str


class DrawdownRecoveryProtocol:
    """Quản trị chu kỳ sụt giảm vốn và cơ chế hồi phục thận trọng theo từng nấc thang."""

    def evaluate_drawdown(
        self,
        current_nav: float,
        peak_nav: float,
        observation_days_below_threshold: int = 0,
        tail_risk_safe: bool = True,
        breadth_healthy: bool = True,
    ) -> DrawdownEvaluation:
        """
        Đánh giá độ sụt giảm từ đỉnh NAV và xác định hệ số co giãn vị thế (exposure_scale_factor).
        """
        if peak_nav <= 0 or current_nav <= 0:
            return DrawdownEvaluation(
                current_drawdown_pct=0.0,
                peak_nav=peak_nav,
                current_nav=current_nav,
                tier=DrawdownTier.GREEN,
                exposure_scale_factor=1.0,
                min_cash_target_pct=10.0,
                re_risking_state="NORMAL",
                action_description="Dữ liệu NAV chưa khởi tạo, hoạt động ở mức cơ sở.",
            )

        drawdown_pct = max(0.0, (peak_nav - current_nav) / peak_nav * 100.0)

        # 1. Tầng RED: DD > 15% (Khẩn cấp phòng vệ)
        if drawdown_pct >= 15.0:
            return DrawdownEvaluation(
                current_drawdown_pct=round(drawdown_pct, 2),
                peak_nav=peak_nav,
                current_nav=current_nav,
                tier=DrawdownTier.RED,
                exposure_scale_factor=0.25,
                min_cash_target_pct=75.0,
                re_risking_state="DEFENSE_LOCKDOWN",
                action_description="EMERGENCY DEFENSE: Nâng tiền mặt >= 75%, giảm 75% quy mô vị thế, đóng băng mở mới.",
            )

        # 2. Tầng ORANGE: DD > 10%
        if drawdown_pct >= 10.0:
            return DrawdownEvaluation(
                current_drawdown_pct=round(drawdown_pct, 2),
                peak_nav=peak_nav,
                current_nav=current_nav,
                tier=DrawdownTier.ORANGE,
                exposure_scale_factor=0.50,
                min_cash_target_pct=50.0,
                re_risking_state="HIGH_DEFENSE",
                action_description="DEFENSIVE: Nâng tiền mặt >= 50%, giảm 50% quy mô các lệnh mua mới.",
            )

        # 3. Tầng YELLOW: DD > 5%
        if drawdown_pct >= 5.0:
            return DrawdownEvaluation(
                current_drawdown_pct=round(drawdown_pct, 2),
                peak_nav=peak_nav,
                current_nav=current_nav,
                tier=DrawdownTier.YELLOW,
                exposure_scale_factor=0.75,
                min_cash_target_pct=25.0,
                re_risking_state="CAUTION",
                action_description="CAUTION: Nâng tiền mặt >= 25%, giảm 25% quy mô các lệnh mua mới.",
            )

        # 4. Tầng ALERT: DD > 3%
        if drawdown_pct >= 3.0:
            return DrawdownEvaluation(
                current_drawdown_pct=round(drawdown_pct, 2),
                peak_nav=peak_nav,
                current_nav=current_nav,
                tier=DrawdownTier.ALERT,
                exposure_scale_factor=0.90,
                min_cash_target_pct=15.0,
                re_risking_state="MONITORING",
                action_description="ALERT: Drawdown chạm ngưỡng cảnh giác > 3%, theo dõi sát sao rủi ro vị thế.",
            )

        # 5. Tầng GREEN: DD < 3% -> Kiểm tra điều kiện Re-risking
        # Nếu trước đó từng bị Drawdown, chỉ cho phép re-risking 100% khi đã quan sát >= 2 phiên xác nhận
        if observation_days_below_threshold >= 2 and tail_risk_safe and breadth_healthy:
            re_risk_state = "CONFIRMED_RE_RISK_FULL"
            scale_factor = 1.0
            min_cash = 10.0
            desc = "NORMAL: Danh mục hoạt động bình thường, tái cấp vốn 100% sức mua."
        else:
            re_risk_state = f"OBSERVATION_DAY_{observation_days_below_threshold}"
            scale_factor = 0.95  # Tăng thận trọng từng bước
            min_cash = 15.0
            desc = f"RE_RISK_STEPWISE: Đang quan sát phiên thứ {observation_days_below_threshold}, nâng nhẹ hạn mức."

        return DrawdownEvaluation(
            current_drawdown_pct=round(drawdown_pct, 2),
            peak_nav=peak_nav,
            current_nav=current_nav,
            tier=DrawdownTier.GREEN,
            exposure_scale_factor=scale_factor,
            min_cash_target_pct=min_cash,
            re_risking_state=re_risk_state,
            action_description=desc,
        )


drawdown_recovery_protocol = DrawdownRecoveryProtocol()
