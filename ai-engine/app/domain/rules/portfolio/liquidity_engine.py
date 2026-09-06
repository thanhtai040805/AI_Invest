"""Engine 6: Liquidity Engine (HOSE Compliance, Market Impact & Execution Horizon)

Chức năng:
- Đảm bảo tuân thủ Điều 2 Hiến pháp Đầu tư sàn HOSE:
    1. Trần quy mô lệnh đơn phiên: <= 15% ADTV20 (tránh đẩy giá / trượt giá lớn).
    2. Trần sức chứa vị thế tích lũy: <= 25% ADTV20.
- Xác định Execution Horizon (Số phiên cần thiết để giải ngân / thoái vốn trọn vẹn).
- Tính toán executable_target (Tỷ trọng tối đa thị trường cho phép hấp thụ trong phiên nay mà không làm méo mó giá).
- Làm tròn theo chuẩn lô 100 cổ phiếu sàn HOSE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LiquidityResult:
    adtv20: float
    portfolio_target: float
    executable_target: float
    target_shares: int
    executable_shares: int
    incremental_shares: int
    execution_horizon_days: int
    participation_rate_pct: float
    market_impact_bps: float
    is_liquidity_constrained: bool
    liquidity_reasons: List[str] = field(default_factory=list)


class LiquidityEngine:
    def __init__(
        self,
        max_session_participation_pct: float = 0.15,
        max_cumulative_capacity_pct: float = 0.25,
    ):
        self.max_session_participation_pct = max_session_participation_pct
        self.max_cumulative_capacity_pct = max_cumulative_capacity_pct

    def evaluate_liquidity(
        self,
        ticker: str,
        portfolio_target: float,
        price: float,
        total_nav: float,
        current_shares: int,
        adtv20: float,
    ) -> LiquidityResult:
        reasons = []

        if price <= 0:
            raise ValueError(f"[LiquidityEngine] Giá cổ phiếu {ticker} không hợp lệ ({price}).")

        if adtv20 <= 0:
            raise ValueError(f"[LiquidityEngine] Thiếu dữ liệu thanh khoản ADTV20 cho mã {ticker}. Từ chối dùng mock.")

        # 1. Tính toán số lượng cổ phiếu mục tiêu lý thuyết
        theoretical_value = portfolio_target * total_nav
        raw_target_shares = int(theoretical_value / price) // 100 * 100

        # 2. Kiểm tra Trần sức chứa tích lũy (Điều 2: Cumulative <= 25% ADTV20)
        max_cumulative_shares = int(adtv20 * self.max_cumulative_capacity_pct) // 100 * 100
        if raw_target_shares > max_cumulative_shares:
            reasons.append(
                f"Vượt trần sức chứa vị thế 25% ADTV20 ({max_cumulative_shares:,} cổ): Cắt giảm từ {raw_target_shares:,} cổ."
            )
            final_target_shares = max_cumulative_shares
        else:
            final_target_shares = raw_target_shares

        # 3. Tính toán Khối lượng cần mua/bán thêm (Delta Shares)
        delta_shares = final_target_shares - current_shares

        # 4. Áp trần quy mô giao dịch đơn phiên (Điều 2: Single session <= 20% ADTV20)
        max_session_shares = int(adtv20 * self.max_session_participation_pct) // 100 * 100
        max_session_shares = max(100, max_session_shares) if adtv20 >= 100 else 0

        is_constrained = False
        if delta_shares > 0:
            # Chiều MUA (Accumulation)
            if delta_shares > max_session_shares:
                is_constrained = True
                exec_incremental = max_session_shares
                reasons.append(
                    f"Chạm trần thanh khoản phiên (20% ADTV20 = {max_session_shares:,} cổ): Chia nhỏ giải ngân đa phiên."
                )
            else:
                exec_incremental = delta_shares

            executable_shares = current_shares + exec_incremental
            horizon_days = max(1, (delta_shares + max_session_shares - 1) // max_session_shares) if max_session_shares > 0 else 1
        elif delta_shares < 0:
            # Chiều BÁN (Distribution / Rebalance Sell)
            sell_desired = abs(delta_shares)
            if sell_desired > max_session_shares:
                is_constrained = True
                exec_incremental = -max_session_shares
                reasons.append(
                    f"Chạm trần thanh khoản phiên khi bán (20% ADTV20 = {max_session_shares:,} cổ): Thoát hàng đa phiên."
                )
            else:
                exec_incremental = delta_shares

            executable_shares = current_shares + exec_incremental
            horizon_days = max(1, (sell_desired + max_session_shares - 1) // max_session_shares) if max_session_shares > 0 else 1
        else:
            exec_incremental = 0
            executable_shares = current_shares
            horizon_days = 1

        executable_shares = (executable_shares // 100) * 100
        exec_incremental = (exec_incremental // 100) * 100
        executable_target_weight = round((executable_shares * price) / total_nav, 4) if total_nav > 0 else 0.0

        # Ước lượng trượt giá Market Impact
        part_rate = abs(exec_incremental) / adtv20 if adtv20 > 0 else 0.0
        market_impact_bps = round(part_rate * 40.0 + 5.0, 1)  # Mô hình trượt giá tuyến tính cơ bản cho HOSE

        return LiquidityResult(
            adtv20=adtv20,
            portfolio_target=portfolio_target,
            executable_target=executable_target_weight,
            target_shares=final_target_shares,
            executable_shares=executable_shares,
            incremental_shares=exec_incremental,
            execution_horizon_days=horizon_days,
            participation_rate_pct=round(part_rate * 100.0, 2),
            market_impact_bps=market_impact_bps,
            is_liquidity_constrained=is_constrained,
            liquidity_reasons=reasons,
        )
