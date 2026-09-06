"""Engine 5: Dynamic Allocation Engine (Cash Target & Dynamic N Positions)

Chức năng:
- Thiết lập hàng rào phòng ngự bằng Tiền mặt chủ động (Active Cash Cushion):
    - Bull Market: Giữ tối thiểu 10% tiền mặt (90% cổ phiếu tối đa).
    - Choppy / Range Bound: Giữ tối thiểu 30% tiền mặt (70% cổ phiếu tối đa).
    - Bear Market: Giữ tối thiểu 60% tiền mặt (40% cổ phiếu tối đa).
- Co giãn số lượng vị thế mục tiêu (Dynamic N):
    - Bull: 12 - 18 vị thế để đón sóng luân chuyển dòng tiền ngành.
    - Choppy: 8 - 12 vị thế.
    - Bear: 4 - 6 vị thế để dễ dàng phòng thủ và quản trị rủi ro thanh khoản.
- Ràng buộc tổng tỷ trọng: Tổng danh mục cổ phiếu sau khi giải ngân không được vượt quá (1.0 - min_cash_target).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class DynamicAllocationResult:
    min_cash_target: float
    max_equity_exposure: float
    dynamic_max_positions: int
    dynamic_min_positions: int
    cash_balance: float
    cash_after: float
    adjusted_target: float
    allocation_reasons: List[str] = field(default_factory=list)


class DynamicAllocationEngine:
    def __init__(self):
        pass

    def evaluate_allocation(
        self,
        portfolio_target: float,
        ticker: str,
        regime_str: str,
        total_nav: float,
        cash_balance: float,
        existing_positions: List[Dict[str, Any]],
        cio_cash_target_override: float = 0.0,
        drawdown_tier: str = "GREEN",
    ) -> DynamicAllocationResult:
        reasons = []
        regime_upper = str(regime_str).upper()
        tier_upper = str(drawdown_tier).upper().strip()

        # 1. Xác định Đệm Tiền Mặt Tối Thiểu (Min Cash Target) & Số Lượng Vị Thế N
        if "BEAR" in regime_upper:
            base_min_cash = 0.60
            min_n = 4
            max_n = 6
        elif "CHOPPY" in regime_upper or "RANGE_BOUND" in regime_upper:
            base_min_cash = 0.30
            min_n = 8
            max_n = 12
        else:
            base_min_cash = 0.10
            min_n = 12
            max_n = 18

        # 1.1 Ràng buộc tối thượng: Drawdown Recovery Protocol (Tầng YELLOW/ORANGE/RED)
        if tier_upper == "RED":
            base_min_cash = max(base_min_cash, 0.75)
            min_n, max_n = 2, 4
            reasons.append("Kích hoạt Drawdown Protocol Tầng RED: Đóng băng mở mới, nâng đệm tiền mặt tối thiểu >= 75% NAV.")
        elif tier_upper == "ORANGE":
            base_min_cash = max(base_min_cash, 0.50)
            min_n, max_n = 4, 8
            reasons.append("Kích hoạt Drawdown Protocol Tầng ORANGE: Nâng đệm tiền mặt tối thiểu >= 50% NAV.")
        elif tier_upper == "YELLOW":
            base_min_cash = max(base_min_cash, 0.25)
            min_n, max_n = 6, 10
            reasons.append("Kích hoạt Drawdown Protocol Tầng YELLOW: Nâng đệm tiền mặt tối thiểu >= 25% NAV.")

        # Cho phép CIO override nếu có chỉ thị vĩ mô đặc biệt (trừ khi đang ở RED Drawdown)
        if cio_cash_target_override > 0 and tier_upper != "RED":
            min_cash = max(base_min_cash, cio_cash_target_override / 100.0 if cio_cash_target_override > 1.0 else cio_cash_target_override)
            reasons.append(f"Áp dụng Cash Target từ CIO/Chế độ thị trường: {min_cash*100:.1f}%.")
        else:
            min_cash = base_min_cash

        max_equity = 1.0 - min_cash

        # 2. Tính tổng tỷ trọng cổ phiếu hiện tại (trừ mã hiện tại nếu có)
        existing_other_stocks_val = sum(
            float(p.get("market_value", 0.0))
            for p in existing_positions
            if p.get("ticker", p.get("symbol", "")) != ticker
        )
        existing_equity_pct = (existing_other_stocks_val / total_nav) if total_nav > 0 else 0.0

        # Sức chứa phân bổ cổ phiếu còn lại
        remaining_equity_budget = max(0.0, max_equity - existing_equity_pct)

        # 3. Ràng buộc theo tiền mặt thực tế trong tài khoản
        available_cash_pct = (cash_balance / total_nav) if total_nav > 0 else 0.0
        # Số tiền mặt có thể giải ngân mà vẫn đảm bảo đệm tiền mặt min_cash
        usable_cash_pct = max(0.0, available_cash_pct - min_cash)

        adjusted_target = portfolio_target
        if adjusted_target > remaining_equity_budget:
            reasons.append(
                f"Giới hạn tổng đòn bẩy cổ phiếu (Max {max_equity*100:.0f}% NAV): Giảm target từ {adjusted_target*100:.1f}% xuống {remaining_equity_budget*100:.1f}%."
            )
            adjusted_target = remaining_equity_budget

        # Kiểm tra tiền mặt khả dụng (chỉ áp dụng khi gia tăng tỷ trọng)
        existing_this_stock = next((p for p in existing_positions if p["ticker"] == ticker), None)
        current_weight = (
            float(existing_this_stock.get("market_value", 0.0)) / total_nav
            if existing_this_stock and total_nav > 0
            else 0.0
        )
        # Trong tầng RED Drawdown: Cấm mở mới vị thế (chỉ cho phép nắm giữ hoặc giảm tỷ trọng)
        if tier_upper == "RED" and current_weight == 0.0:
            reasons.append("Tầng RED Drawdown cấm mở mới vị thế: Ép target về 0.0% NAV.")
            adjusted_target = 0.0

        incremental_target = max(0.0, adjusted_target - current_weight)

        if incremental_target > usable_cash_pct:
            reasons.append(
                f"Thiếu tiền mặt để duy trì đệm an toàn ({min_cash*100:.0f}%): Cắt giảm tỷ trọng tăng thêm từ {incremental_target*100:.1f}% xuống {usable_cash_pct*100:.1f}%."
            )
            adjusted_target = current_weight + usable_cash_pct

        adjusted_target = round(max(0.0, adjusted_target), 4)
        net_change_vnd = (adjusted_target - current_weight) * total_nav
        projected_cash = max(0.0, cash_balance - net_change_vnd)
        projected_cash_pct = (projected_cash / total_nav) if total_nav > 0 else 0.0

        return DynamicAllocationResult(
            min_cash_target=min_cash,
            max_equity_exposure=max_equity,
            dynamic_max_positions=max_n,
            dynamic_min_positions=min_n,
            cash_balance=cash_balance,
            cash_after=round(projected_cash_pct, 4),
            adjusted_target=adjusted_target,
            allocation_reasons=reasons,
        )
