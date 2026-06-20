"""Stop-Loss Engine — TASK-113

Giám sát P&L thời gian thực và kích hoạt lệnh dừng lỗ khẩn cấp khi vi phạm Hard Law Điều 1.
Trigger: unrealized_pnl_pct_nav <= -2%.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

@dataclass
class StopLossOrder:
    ticker: str
    quantity: int
    urgency: str = "EMERGENCY"
    reason: str = ""

class StopLossEngine:
    def check_position(
        self, 
        ticker: str, 
        quantity: int, 
        entry_price: float, 
        current_price: float, 
        nav: float
    ) -> Optional[StopLossOrder]:
        """Kiểm tra một vị thế và trả về lệnh dừng lỗ nếu vi phạm."""
        if quantity <= 0:
            return None
            
        unrealized_pnl = (current_price - entry_price) * quantity
        pnl_pct_nav = unrealized_pnl / nav
        
        # Hard Law Điều 1: Tổn thất không quá 2% NAV
        if pnl_pct_nav <= -0.02:
            reason = f"Vi phạm Hard Law Điều 1: Lỗ {pnl_pct_nav*100:.2f}% NAV (Ngưỡng -2%)"
            logger.critical(f"!!! STOP LOSS TRIGGERED for {ticker}: {reason} !!!")
            
            return StopLossOrder(
                ticker=ticker,
                quantity=quantity,
                urgency="EMERGENCY",
                reason=reason
            )
            
        return None

stop_loss_engine = StopLossEngine()
