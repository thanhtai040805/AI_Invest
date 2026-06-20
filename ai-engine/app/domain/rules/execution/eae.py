"""Execution Adaptation Engine (EAE) — TASK-401

Quản lý việc thực thi lệnh thông minh.
Hỗ trợ:
1. Order Slicing (Chia nhỏ lệnh để giảm slippage).
2. Liquidity awareness (Kiểm tra ADTV và volume hiện tại).
3. Urgency handling (Lệnh khẩn cấp từ Stop-Loss).
"""

import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class OrderSlice:
    ticker: str
    side: str
    quantity: int
    price_type: str  # "LIMIT", "MP", "ATO", "ATC"
    limit_price: Optional[float] = None

class ExecutionAdaptationEngine:
    def __init__(self):
        # AC: Lệnh tối đa mỗi slice không vượt 15% ADTV20 hoặc 10% volume dự kiến phiên
        self.max_participation_rate = 0.10 

    def slice_order(
        self, 
        ticker: str, 
        side: str, 
        total_quantity: int, 
        adtv20: float,
        urgency: str = "NORMAL"
    ) -> List[OrderSlice]:
        """Chia nhỏ một lệnh lớn thành các slices."""
        
        # 1. EMERGENCY handling (Stop-loss) -> Không slice, tống lệnh MP/ATC ngay
        if urgency == "EMERGENCY":
            logger.info(f"EMERGENCY Order for {ticker}: No slicing, executing full quantity.")
            return [OrderSlice(ticker, side, total_quantity, "MP")]

        # 2. Slicing logic for NORMAL/HIGH urgency
        # Tính size tối đa cho mỗi slice dựa trên thanh khoản
        max_slice_size = int(adtv20 * 0.05) # Giả định mỗi slice chiếm 5% ADTV
        if max_slice_size <= 0:
            max_slice_size = total_quantity # Fallback
            
        num_slices = (total_quantity + max_slice_size - 1) // max_slice_size
        
        slices = []
        remaining = total_quantity
        
        for i in range(num_slices):
            slice_qty = min(remaining, max_slice_size)
            slices.append(OrderSlice(
                ticker=ticker,
                side=side,
                quantity=slice_qty,
                price_type="LIMIT" if urgency == "NORMAL" else "MP"
            ))
            remaining -= slice_qty
            
        return slices

    def determine_market_phase(self, current_time: datetime) -> str:
        """Xác định phiên giao dịch (HOSE)."""
        t = current_time.time()
        if t < datetime.strptime("09:00", "%H:%M").time():
            return "PRE_MARKET"
        elif t <= datetime.strptime("09:15", "%H:%M").time():
            return "ATO"
        elif t <= datetime.strptime("11:30", "%H:%M").time():
            return "CONTINUOUS_AM"
        elif t <= datetime.strptime("13:00", "%H:%M").time():
            return "LUNCH_BREAK"
        elif t <= datetime.strptime("14:30", "%H:%M").time():
            return "CONTINUOUS_PM"
        elif t <= datetime.strptime("14:45", "%H:%M").time():
            return "ATC"
        else:
            return "POST_MARKET"

eae_engine = ExecutionAdaptationEngine()
