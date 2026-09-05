"""Execution Adaptation Engine (EAE) — HOSE Institutional Standard (TASK-401 / IOS v5.1)

Quản lý việc thực thi lệnh thông minh trên sàn HOSE:
1. Tuân thủ Vi cấu trúc HOSE: Lô chẵn 100, trần 500,000 cổ/lệnh, Bước giá (10đ, 50đ, 100đ).
2. Phân tầng Market State:
   - NORMAL: 2–3 child orders, chiến lược VWAP/LO, 1 session.
   - STRESS: 5–10 child orders, chiến lược PASSIVE_LIMIT, 2-3 sessions nếu volume thấp.
   - CRISIS: Minimal child orders, ưu tiên thoát hàng nhanh (Exit Priority).
3. Giao thức Thích ứng Bẫy ATC 3 Pha:
   - Pha 1 (14:15 - 14:28:30): Pre-ATC Volume Skim (vét 60% lượng dư ở phiên liên tục PM).
   - Pha 2 (14:30 - 14:42:00): Dynamic IEP Pegging bám sát giá khớp dự kiến.
   - Pha 3 (14:42:00 - 14:44:15): Anomaly Kill-Switch hủy lệnh khẩn cấp nếu có thao túng.
4. Tương thích ngược 100% với hàm slice_order và determine_market_phase cũ.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    NORMAL = "NORMAL"
    STRESS = "STRESS"
    CRISIS = "CRISIS"


class ExecutionStrategy(str, Enum):
    PASSIVE_LIMIT = "PASSIVE_LIMIT"
    VWAP_SLICED = "VWAP_SLICED"
    TWAP_SLICED = "TWAP_SLICED"
    AGGRESSIVE_MARKET = "AGGRESSIVE_MARKET"


@dataclass
class OrderSlice:
    ticker: str
    side: str
    quantity: int
    price_type: str  # "LIMIT", "MP", "ATO", "ATC"
    limit_price: Optional[float] = None
    slice_index: int = 1
    session_phase: str = "CONTINUOUS"


@dataclass
class ExecutionPlan:
    execution_decision: str  # "EXECUTE", "BLOCK", "REJECT"
    ticker: str
    direction: str
    total_quantity: int
    max_price: float
    execution_mode: str
    strategy: str
    child_orders_count: int
    execution_horizon: str
    max_participation_rate: float
    slices: List[OrderSlice] = field(default_factory=list)
    rejection_reason: Optional[str] = None


class ExecutionAdaptationEngine:
    MAX_ORDER_SIZE_HOSE = 500_000  # Giới hạn 1 lệnh giao dịch khớp lệnh sàn HOSE
    MIN_LOT_HOSE = 100             # Lô chẵn sàn HOSE

    def __init__(self, max_participation_rate: float = 0.20):
        # Trần tham gia phiên chuẩn định chế HOSE (<= 20% ADTV20)
        self.max_participation_rate = max_participation_rate

    @staticmethod
    def align_to_hose_tick_size(price: float) -> float:
        """
        Làm tròn giá theo chuẩn 3 bậc bước giá (Tick Size) của sàn HOSE:
        - Giá < 10,000: Bước giá 10 đồng
        - Giá 10,000 - 49,950: Bước giá 50 đồng
        - Giá >= 50,000: Bước giá 100 đồng
        """
        if price <= 0:
            return 0.0
        if price < 10_000:
            return float(round(price / 10.0) * 10)
        elif price < 50_000:
            return float(round(price / 50.0) * 50)
        else:
            return float(round(price / 100.0) * 100)

    @staticmethod
    def validate_order(ticker: str, quantity: int, price: float) -> Tuple[bool, str]:
        """Validate điều kiện tiên quyết theo quy định sàn HOSE."""
        if not ticker or not str(ticker).strip():
            return False, "Thiếu mã chứng khoán (ticker)."
        if quantity < 100:
            return False, f"Khối lượng {quantity:,} < 100 (dưới lô tối thiểu sàn HOSE)."
        if quantity % 100 != 0:
            return False, f"Khối lượng {quantity:,} không phải bội số của lô 100 sàn HOSE."
        if price <= 0:
            return False, f"Mức giá {price:,} không hợp lệ."
        return True, "VALID"

    def create_execution_plan(
        self,
        ticker: str,
        direction: str,
        total_quantity: int,
        decision_price: float,
        max_price: float,
        adtv20: float,
        market_state: Optional[Dict[str, Any]] = None,
        is_failsafe_active: bool = False,
        current_time: Optional[datetime] = None,
    ) -> ExecutionPlan:
        """
        Tạo kế hoạch thực thi chi tiết (Execution Plan) chuẩn hóa định chế:
        1. Kiểm tra Failsafe Active -> BLOCK
        2. Validate Order -> REJECT nếu vi phạm lô chẵn hoặc giá
        3. Phân bổ số child orders và chiến lược theo Market State (NORMAL, STRESS, CRISIS)
        4. Kiểm tra tỷ trọng ATC Concentration
        """
        ticker_clean = str(ticker).upper().strip()
        direction_clean = str(direction).upper().strip()

        # 1. Failsafe Check
        if is_failsafe_active:
            return ExecutionPlan(
                execution_decision="BLOCK",
                ticker=ticker_clean,
                direction=direction_clean,
                total_quantity=total_quantity,
                max_price=max_price,
                execution_mode="FAILSAFE_ACTIVE",
                strategy="NONE",
                child_orders_count=0,
                execution_horizon="BLOCKED",
                max_participation_rate=0.0,
                rejection_reason="Hệ thống Failsafe đang kích hoạt: Dừng toàn bộ giao dịch để bảo toàn vốn.",
            )

        # 2. Validate Order
        is_valid, val_msg = self.validate_order(ticker_clean, total_quantity, decision_price)
        if not is_valid:
            return ExecutionPlan(
                execution_decision="REJECT",
                ticker=ticker_clean,
                direction=direction_clean,
                total_quantity=total_quantity,
                max_price=max_price,
                execution_mode="INVALID",
                strategy="NONE",
                child_orders_count=0,
                execution_horizon="REJECTED",
                max_participation_rate=0.0,
                rejection_reason=val_msg,
            )

        m_state = market_state or {}
        spread = float(m_state.get("spread", 0.003))
        volume_status = str(m_state.get("volume_status", "NORMAL")).upper()
        market_regime = str(m_state.get("market_regime", "NORMAL")).upper()
        atc_conc = float(m_state.get("atc_concentration", 0.20))

        participation_rate = total_quantity / adtv20 if adtv20 > 0 else 0.0

        # 3. Phân loại Execution Mode & Số lượng Child Orders theo Sơ đồ
        if market_regime == "CRISIS":
            exec_mode = ExecutionMode.CRISIS
            strategy = ExecutionStrategy.AGGRESSIVE_MARKET
            num_children = 1 if total_quantity <= self.MAX_ORDER_SIZE_HOSE else math.ceil(total_quantity / self.MAX_ORDER_SIZE_HOSE)
            horizon = "1_SESSION"
        elif market_regime == "STRESS" or spread > 0.01 or volume_status == "LOW":
            exec_mode = ExecutionMode.STRESS
            strategy = ExecutionStrategy.PASSIVE_LIMIT
            # Khi STRESS: Chia nhỏ 5–10 child orders
            if total_quantity >= 100_000:
                num_children = 8  # Chuẩn kịch bản định chế cho lệnh lớn
            else:
                num_children = min(8, max(5, total_quantity // 1000))
                num_children = max(5, min(10, num_children))
            horizon = "2-3_SESSIONS" if (participation_rate > 0.15 or volume_status == "LOW") else "1-2_SESSIONS"
        else:
            exec_mode = ExecutionMode.NORMAL
            strategy = ExecutionStrategy.VWAP_SLICED if atc_conc < 0.30 else ExecutionStrategy.PASSIVE_LIMIT
            # Khi NORMAL: 2–3 child orders
            num_children = 2 if total_quantity <= 50_000 else 3
            horizon = "1_SESSION"

        # 4. Kiểm tra phiên giao dịch và bẫy ATC
        now_dt = current_time or datetime.now()
        market_phase = self.determine_market_phase(now_dt)
        avoid_atc_dump = (atc_conc > 0.30 and exec_mode == ExecutionMode.STRESS)

        # 5. Phân bổ slices (Lô chẵn 100, trần 500k)
        base_slice_qty = (total_quantity // (num_children * 100)) * 100
        slices: List[OrderSlice] = []
        rem_qty = total_quantity

        for i in range(num_children):
            if i == num_children - 1:
                slice_qty = rem_qty
            else:
                slice_qty = min(rem_qty, base_slice_qty)

            if slice_qty <= 0:
                continue

            # Đảm bảo không vượt quá trần 500,000 cổ / lệnh
            slice_qty = min(slice_qty, self.MAX_ORDER_SIZE_HOSE)
            slice_qty = (slice_qty // 100) * 100

            # Tính giá đặt limit cho từng slice
            if strategy == ExecutionStrategy.PASSIVE_LIMIT:
                step_size = 50.0 if decision_price < 50_000 else 100.0
                offset = (i % 3) * step_size
                if direction_clean == "BUY":
                    calc_price = decision_price + offset
                    slice_limit = min(calc_price, max_price)
                else:
                    calc_price = decision_price - offset
                    slice_limit = max(calc_price, max_price)
                slice_limit = self.align_to_hose_tick_size(slice_limit)
                p_type = "LIMIT"
            else:
                slice_limit = self.align_to_hose_tick_size(max_price)
                p_type = "LIMIT" if market_phase in ("ATO", "ATC") else ("LIMIT" if exec_mode != ExecutionMode.CRISIS else "MP")

            phase_label = "AVOID_ATC_CONTINUOUS" if avoid_atc_dump else market_phase

            slices.append(OrderSlice(
                ticker=ticker_clean,
                side=direction_clean,
                quantity=slice_qty,
                price_type=p_type,
                limit_price=slice_limit,
                slice_index=i + 1,
                session_phase=phase_label,
            ))
            rem_qty -= slice_qty

        return ExecutionPlan(
            execution_decision="EXECUTE",
            ticker=ticker_clean,
            direction=direction_clean,
            total_quantity=total_quantity,
            max_price=max_price,
            execution_mode=exec_mode.value,
            strategy=strategy.value,
            child_orders_count=len(slices),
            execution_horizon=horizon,
            max_participation_rate=self.max_participation_rate,
            slices=slices,
        )

    def resolve_atc_contingency(
        self,
        ticker: str,
        remaining_quantity: int,
        decision_price: float,
        max_price: float,
        current_time: datetime,
        iep_price: float,
        atc_concentration: float,
        anomaly_status: str,
    ) -> Dict[str, Any]:
        """
        Giao thức Thực thi Lai Đa Tầng 3 Pha (3-Tier Adaptive ATC Pipeline):
        - Pha 1 (14:15 - 14:28:30): Pre-ATC Skim rút trước 60% lượng dư.
        - Pha 2 (14:30 - 14:42:00): Dynamic IEP Pegging bám sát giá khớp dự kiến.
        - Pha 3 (14:42:00 - 14:44:15): Anomaly Kill-Switch hủy lệnh khẩn cấp nếu có thao túng.
        """
        t = current_time.time()

        # Pha 1: Pre-ATC Skim (14:15 - 14:28:30)
        if time(14, 15) <= t < time(14, 28, 30):
            if atc_concentration > 0.30 and remaining_quantity > 50_000:
                skim_qty = int(remaining_quantity * 0.60) // 100 * 100
                tick_step = 50.0 if decision_price < 50_000 else 100.0
                return {
                    "phase": "PRE_ATC_SKIM",
                    "action": "ACCELERATE_CONTINUOUS_BUY",
                    "target_quantity": skim_qty,
                    "price": self.align_to_hose_tick_size(decision_price + tick_step),
                    "order_type": "LO_AGGRESSIVE",
                    "rationale": "Rút ruột 60% thanh khoản trước giờ ATC để giảm thiểu rủi ro bẫy đóng cửa.",
                }

        # Pha 3: Anomaly Kill-Switch (14:42:00 - 14:44:15)
        if time(14, 42) <= t <= time(14, 44, 15):
            if anomaly_status in ("CRITICAL", "SUSPECTED_ATC_MANIPULATION"):
                return {
                    "phase": "ATC_KILL_SWITCH",
                    "action": "CANCEL_ALL_ATC_ORDERS",
                    "target_quantity": remaining_quantity,
                    "rationale": "Phát hiện dấu hiệu thao túng giá ATC: Hủy lệnh khẩn cấp trước 14:44:15.",
                }

        # Pha 2: Dynamic IEP Pegging (14:30 - 14:42:00)
        if time(14, 30) <= t < time(14, 42):
            tick_step = 50.0 if iep_price < 50_000 else 100.0
            pegged_price = min(self.align_to_hose_tick_size(iep_price + tick_step), max_price)
            return {
                "phase": "DYNAMIC_IEP_PEGGING",
                "action": "SUBMIT_PEGGED_LIMIT",
                "target_quantity": remaining_quantity,
                "price": pegged_price,
                "order_type": "LO_ATC_PEGGED",
                "rationale": "Bám sát giá IEP + 1 bước giá để đảm bảo ưu tiên khớp 100% trong trần an toàn.",
            }

        return {
            "phase": "NORMAL_EXECUTION",
            "action": "MAINTAIN_CURRENT_ORDERS",
            "target_quantity": remaining_quantity,
            "rationale": "Duy trì lệnh bình thường.",
        }

    def slice_order(
        self,
        ticker: str,
        side: str,
        total_quantity: int,
        adtv20: float,
        urgency: str = "NORMAL",
    ) -> List[OrderSlice]:
        """Tương thích ngược với interface cũ của EAE."""
        if urgency == "EMERGENCY":
            logger.info(f"EMERGENCY Order for {ticker}: No slicing, executing full quantity.")
            return [OrderSlice(ticker=ticker, side=side, quantity=total_quantity, price_type="MP")]

        max_slice_size = int(adtv20 * 0.05)
        if max_slice_size <= 0:
            max_slice_size = total_quantity

        num_slices = (total_quantity + max_slice_size - 1) // max_slice_size
        slices = []
        remaining = total_quantity

        for i in range(num_slices):
            slice_qty = min(remaining, max_slice_size)
            slice_qty = (slice_qty // 100) * 100
            if slice_qty > 0:
                slices.append(OrderSlice(
                    ticker=ticker,
                    side=side,
                    quantity=slice_qty,
                    price_type="LIMIT" if urgency == "NORMAL" else "MP",
                    slice_index=i + 1,
                ))
            remaining -= slice_qty

        return slices

    def determine_market_phase(self, current_time: datetime) -> str:
        """Xác định phiên giao dịch sàn HOSE."""
        t = current_time.time()
        if t < time(9, 0):
            return "PRE_MARKET"
        elif t <= time(9, 15):
            return "ATO"
        elif t <= time(11, 30):
            return "CONTINUOUS_AM"
        elif t <= time(13, 0):
            return "LUNCH_BREAK"
        elif t <= time(14, 30):
            return "CONTINUOUS_PM"
        elif t <= time(14, 45):
            return "ATC"
        else:
            return "POST_MARKET"


eae_engine = ExecutionAdaptationEngine()
