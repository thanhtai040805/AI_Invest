"""T+2.5 Exposure & Liquidity Lock Manager — Vietnamized Institutional Risk Engine.

Mục tiêu:
Khống chế và bảo vệ danh mục trước độ trễ thanh toán chu kỳ T+2.5 của sàn HOSE/HNX.
Cổ phiếu mua ở phiên T phải qua T+1 và sáng T+2 mới về tài khoản (chiều T+2 mới được bán).
Do đó danh mục chịu rủi ro phơi nhiễm đóng băng thanh khoản trọn vẹn 2.5 phiên giao dịch.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class T25CapacityCheck:
    passed: bool
    current_locked_t25_pct: float
    projected_locked_t25_pct: float
    max_allowed_locked_pct: float
    max_safe_shares: int
    max_safe_vnd: float
    reason: str


class T25ExposureManager:
    """Quản trị rủi ro kẹt vốn chu kỳ T+2.5 và tính toán đệm rủi ro khoảng trống giá (Gap Risk)."""

    def __init__(
        self,
        max_locked_t25_pct: float = 35.0,  # Tối đa 35% NAV là hàng đang chờ về T+2.5
        daily_floor_limit_pct: float = 0.07,  # Biên độ sàn HOSE là 7%
        hard_stop_loss_nav_pct: float = 0.02,  # Rủi ro tối đa mỗi vị thế <= 2% NAV
    ):
        self.max_locked_t25_pct = max_locked_t25_pct
        self.daily_floor_limit_pct = daily_floor_limit_pct
        self.hard_stop_loss_nav_pct = hard_stop_loss_nav_pct
        # 2 phiên sàn liên tiếp trong thời gian chờ T+2.5: (1 - 0.07)^2 - 1 = -13.51%
        self.two_session_floor_loss_pct = 1.0 - (1.0 - daily_floor_limit_pct) ** 2

    def check_t25_capacity(
        self,
        nav: float,
        locked_t25_value: float,
        proposed_order_value: float,
        price: float,
        stop_loss_price: Optional[float] = None,
    ) -> T25CapacityCheck:
        """
        Kiểm tra sức chứa hàng kẹt T+2.5 và tính toán quy mô an toàn phòng ngừa 2 phiên sàn.
        - nav: Tổng giá trị tài sản ròng
        - locked_t25_value: Tổng giá trị các cổ phiếu đã mua nhưng chưa về tài khoản (T+0, T+1, sáng T+2)
        - proposed_order_value: Giá trị lệnh mua đề xuất (Price * Quantity)
        - price: Giá mua dự kiến
        - stop_loss_price: Giá cắt lỗ đề xuất
        """
        if nav <= 0 or price <= 0:
            return T25CapacityCheck(
                passed=False,
                current_locked_t25_pct=0.0,
                projected_locked_t25_pct=0.0,
                max_allowed_locked_pct=self.max_locked_t25_pct,
                max_safe_shares=0,
                max_safe_vnd=0.0,
                reason="NAV hoặc Giá cổ phiếu không hợp lệ.",
            )

        cur_locked_pct = (locked_t25_value / nav) * 100.0
        proj_locked_pct = ((locked_t25_value + proposed_order_value) / nav) * 100.0

        # 1. Kiểm tra trần tổng hàng kẹt T+2.5 (Không để vượt quá 35% NAV)
        if proj_locked_pct > self.max_locked_t25_pct:
            reason = (
                f"Vi phạm Trần Hàng Kẹt T+2.5: Tỷ lệ hàng chưa về dự kiến ({proj_locked_pct:.1f}% NAV) "
                f"vượt trần an toàn ({self.max_locked_t25_pct:.1f}% NAV). Chặn mở mới để bảo tồn thanh khoản."
            )
            logger.warning(f"[T25ExposureManager] {reason}")
            return T25CapacityCheck(
                passed=False,
                current_locked_t25_pct=round(cur_locked_pct, 2),
                projected_locked_t25_pct=round(proj_locked_pct, 2),
                max_allowed_locked_pct=self.max_locked_t25_pct,
                max_safe_shares=0,
                max_safe_vnd=0.0,
                reason=reason,
            )

        # 2. Tính toán quy mô vị thế an toàn gánh chịu rủi ro 2 phiên sàn T+2.5
        # Loss per share = max(Price - StopLoss, 2 phiên sàn = 13.51% * Price)
        if stop_loss_price and stop_loss_price < price:
            stop_loss_pct = (price - stop_loss_price) / price
        else:
            stop_loss_pct = 0.07  # Mặc định 7% nếu không truyền

        effective_downside_pct = max(stop_loss_pct, self.two_session_floor_loss_pct)

        # Max allowed VND so that (Max_VND * effective_downside_pct) <= 2% NAV
        max_safe_vnd = (self.hard_stop_loss_nav_pct * nav) / effective_downside_pct
        max_safe_vnd = min(max_safe_vnd, nav * 0.15)  # Không vượt quá 15% Single Stock Limit

        # Làm tròn theo lô 100 cổ phiếu sàn HOSE
        max_safe_shares = int(max_safe_vnd / price) // 100 * 100

        # Kiểm tra xem lệnh đề xuất có vượt quá quy mô an toàn T+2.5 không
        if proposed_order_value > (max_safe_vnd * 1.05):  # Ngưỡng lệch 5%
            reason = (
                f"Quy mô đề xuất ({proposed_order_value:,.0f} VND) vượt trần an toàn T+2.5 ({max_safe_vnd:,.0f} VND) "
                f"khi tính đến rủi ro kẹt 2 cây sàn ({self.two_session_floor_loss_pct*100:.2f}%). Cần hạ quy mô (REDUCE)."
            )
            return T25CapacityCheck(
                passed=False,
                current_locked_t25_pct=round(cur_locked_pct, 2),
                projected_locked_t25_pct=round(proj_locked_pct, 2),
                max_allowed_locked_pct=self.max_locked_t25_pct,
                max_safe_shares=max_safe_shares,
                max_safe_vnd=round(max_safe_vnd, 2),
                reason=reason,
            )

        return T25CapacityCheck(
            passed=True,
            current_locked_t25_pct=round(cur_locked_pct, 2),
            projected_locked_t25_pct=round(proj_locked_pct, 2),
            max_allowed_locked_pct=self.max_locked_t25_pct,
            max_safe_shares=max_safe_shares,
            max_safe_vnd=round(max_safe_vnd, 2),
            reason="Thỏa mãn trần tỷ trọng kẹt hàng T+2.5 và khoảng đệm rủi ro 2 phiên sàn.",
        )


t25_exposure_manager = T25ExposureManager()
