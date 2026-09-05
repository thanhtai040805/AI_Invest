"""Engine 7: Rebalancing Engine (Deadband >= 2%, T+2.5 Settlement Split & Campaign Memory)

Chức năng:
- So sánh Current Weight vs Executable Target:
- Bộ lọc Deadband (Ngưỡng chênh lệch tối thiểu >= 2.0% NAV):
    - Nếu |drift| < 2.0%: Ra quyết định HOLD, không phát sinh lệnh để chống cạm bẫy bào mòn phí và thuế (Churning Trap).
    - Nếu |drift| >= 2.0%: Cho phép kích hoạt REBALANCE / BUY / SELL.
- Ràng buộc Hàng Khả Dụng T+2.5 (Available vs Locked Shares):
    - Khi bán, khối lượng khớp tối đa không vượt quá available_shares.
    - Nếu available_shares < 100: Hoãn bán, đưa về HOLD_T25_SETTLEMENT_PENDING để tránh bán khống trái luật HOSE.
- Bộ nhớ Chiến dịch Đa phiên (Execution Horizon Campaign Management):
    - Quản lý trạng thái chiến dịch tích lũy/thoái vốn trong portfolio_campaigns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RebalanceDecision:
    action: str  # "BUY", "SELL", "HOLD", "REBALANCE", "HOLD_T25_SETTLEMENT_PENDING"
    sub_action: str  # "NEW_BUY", "REBALANCE_BUY", "REBALANCE_SELL", "FULL_SELL", "HOLD_DEADBAND", "HOLD_T25"
    ticker: str
    current_weight: float
    current_shares: int
    available_shares: int
    locked_t25_shares: int
    target_weight: float
    executable_target: float
    incremental_weight: float
    incremental_shares: int
    target_shares: int
    deadband_passed: bool
    rebalance_drift_pct: float
    campaign_info: Optional[Dict[str, Any]] = None
    rebalance_reasons: List[str] = field(default_factory=list)


class RebalancingEngine:
    def __init__(self, deadband_threshold_pct: float = 0.02):
        self.deadband_threshold_pct = deadband_threshold_pct

    def evaluate_rebalance(
        self,
        ticker: str,
        current_weight: float,
        current_shares: int,
        available_shares: int,
        locked_t25_shares: int,
        portfolio_target: float,
        executable_target: float,
        executable_shares: int,
        target_shares: int,
        incremental_shares: int,
        price: float,
        total_nav: float,
        active_campaign: Optional[Dict[str, Any]] = None,
    ) -> RebalanceDecision:
        ticker_clean = str(ticker).upper().strip()
        reasons: List[str] = []

        drift = executable_target - current_weight
        abs_drift = abs(drift)
        drift_shares = incremental_shares

        # 1. Kiểm tra Bộ lọc Deadband (>= 2.0% NAV)
        # Ngoại lệ: Nếu current_weight == 0 và muốn mua mới, hoặc target == 0 và muốn bán hết, không áp deadband
        is_new_entry = (current_weight == 0.0 and executable_target > 0.0)
        is_full_exit = (current_weight > 0.0 and portfolio_target == 0.0)

        if not is_new_entry and not is_full_exit and abs_drift < self.deadband_threshold_pct:
            reasons.append(
                f"Độ lệch tỷ trọng ({abs_drift*100:.2f}%) nằm trong vùng Deadband an toàn (< {self.deadband_threshold_pct*100:.1f}% NAV). Giữ nguyên vị thế để chống cạm bẫy bào mòn phí và thuế."
            )
            return RebalanceDecision(
                action="HOLD",
                sub_action="HOLD_DEADBAND",
                ticker=ticker_clean,
                current_weight=current_weight,
                current_shares=current_shares,
                available_shares=available_shares,
                locked_t25_shares=locked_t25_shares,
                target_weight=portfolio_target,
                executable_target=current_weight,
                incremental_weight=0.0,
                incremental_shares=0,
                target_shares=current_shares,
                deadband_passed=False,
                rebalance_drift_pct=round(drift, 4),
                campaign_info=active_campaign,
                rebalance_reasons=reasons,
            )

        deadband_passed = True
        final_incremental_shares = drift_shares
        final_executable_target = executable_target

        # 2. Xử lý Chiều Mua (BUY / REBALANCE_BUY)
        if drift_shares > 0:
            if current_weight == 0.0:
                action = "BUY"
                sub_action = "NEW_BUY"
                reasons.append(f"Mở mới vị thế {ticker_clean} tỷ trọng mục tiêu {executable_target*100:.1f}% NAV.")
            else:
                action = "REBALANCE"
                sub_action = "REBALANCE_BUY"
                reasons.append(f"Tăng tỷ trọng {ticker_clean} từ {current_weight*100:.1f}% lên {executable_target*100:.1f}% NAV.")

        # 3. Xử lý Chiều Bán (SELL / REBALANCE_SELL) & Ràng Buộc T+2.5
        elif drift_shares < 0:
            desired_sell_shares = abs(drift_shares)

            # Ràng buộc hàng khả dụng T+2.5
            if available_shares < 100:
                action = "HOLD_T25_SETTLEMENT_PENDING"
                sub_action = "HOLD_T25"
                final_incremental_shares = 0
                final_executable_target = current_weight
                reasons.append(
                    f"Cần bán {desired_sell_shares:,} cổ nhưng tài khoản chỉ có {available_shares:,} cổ khả dụng ({locked_t25_shares:,} cổ đang kẹt chu kỳ T+2.5). Hoãn bán để tuân thủ luật chứng khoán HOSE."
                )
            else:
                actual_sell_shares = min(desired_sell_shares, available_shares) // 100 * 100
                final_incremental_shares = -actual_sell_shares
                final_shares_post = current_shares - actual_sell_shares
                final_executable_target = round((final_shares_post * price) / total_nav, 4) if total_nav > 0 else 0.0

                if actual_sell_shares < desired_sell_shares:
                    reasons.append(
                        f"Chỉ bán được {actual_sell_shares:,} cổ khả dụng; còn lại {desired_sell_shares - actual_sell_shares:,} cổ đang kẹt T+2.5 sẽ được xử lý sau khi tiền/hàng về."
                    )

                if portfolio_target == 0.0 and final_shares_post == 0:
                    action = "SELL"
                    sub_action = "FULL_SELL"
                    reasons.append(f"Thoát toàn bộ vị thế {ticker_clean}.")
                else:
                    action = "REBALANCE"
                    sub_action = "REBALANCE_SELL"
                    reasons.append(f"Hạ tỷ trọng {ticker_clean} từ {current_weight*100:.1f}% xuống {final_executable_target*100:.1f}% NAV.")
        else:
            action = "HOLD"
            sub_action = "HOLD_MATCHED"
            final_incremental_shares = 0
            reasons.append(f"Tỷ trọng hiện tại đã tiệm cận mục tiêu ({current_weight*100:.1f}%).")

        # 4. Quản lý Bộ nhớ Chiến dịch Đa phiên (portfolio_campaigns)
        campaign_record = None
        # Nếu chưa đạt tới portfolio_target trọn vẹn do rào cản thanh khoản
        if portfolio_target > final_executable_target and action in ("BUY", "REBALANCE"):
            remaining_w = round(portfolio_target - final_executable_target, 4)
            campaign_record = {
                "ticker": ticker_clean,
                "direction": "ACCUMULATION",
                "final_target_weight": portfolio_target,
                "current_weight": final_executable_target,
                "session_incremental_weight": round(abs(final_incremental_shares * price / total_nav), 4),
                "remaining_weight": remaining_w,
                "target_shares": target_shares,
                "accumulated_shares": current_shares + max(0, final_incremental_shares),
                "status": "IN_PROGRESS",
            }
            reasons.append(f"Khởi tạo/Cập nhật chiến dịch tích lũy đa phiên: Còn lại {remaining_w*100:.1f}% NAV.")
        elif portfolio_target < final_executable_target and action in ("SELL", "REBALANCE"):
            remaining_w = round(final_executable_target - portfolio_target, 4)
            campaign_record = {
                "ticker": ticker_clean,
                "direction": "DISTRIBUTION",
                "final_target_weight": portfolio_target,
                "current_weight": final_executable_target,
                "session_incremental_weight": round(abs(final_incremental_shares * price / total_nav), 4),
                "remaining_weight": remaining_w,
                "target_shares": target_shares,
                "accumulated_shares": current_shares - abs(final_incremental_shares),
                "status": "IN_PROGRESS",
            }
            reasons.append(f"Khởi tạo/Cập nhật chiến dịch thoái vốn đa phiên: Còn lại {remaining_w*100:.1f}% NAV.")
        elif active_campaign and abs(final_executable_target - portfolio_target) < 0.005:
            # Đã hoàn thành chiến dịch
            campaign_record = dict(active_campaign)
            campaign_record["status"] = "COMPLETED"
            reasons.append("Chiến dịch đa phiên đã hoàn thành mục tiêu giải ngân.")

        incremental_w = round((final_incremental_shares * price) / total_nav, 4) if total_nav > 0 else 0.0

        return RebalanceDecision(
            action=action,
            sub_action=sub_action,
            ticker=ticker_clean,
            current_weight=round(current_weight, 4),
            current_shares=current_shares,
            available_shares=available_shares,
            locked_t25_shares=locked_t25_shares,
            target_weight=portfolio_target,
            executable_target=final_executable_target,
            incremental_weight=incremental_w,
            incremental_shares=final_incremental_shares,
            target_shares=current_shares + final_incremental_shares,
            deadband_passed=deadband_passed,
            rebalance_drift_pct=round(drift, 4),
            campaign_info=campaign_record,
            rebalance_reasons=reasons,
        )
