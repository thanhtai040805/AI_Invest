"""Stop-Loss Engine — TASK-113

Giám sát P&L thời gian thực và kích hoạt lệnh dừng lỗ khẩn cấp khi vi phạm Hard Law Điều 1 hoặc Cấu trúc giá.
Triggers: Fast Exit (Rejection), Structural Exit (Swing Low), Time Stop, Hard Law (-2% NAV).
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

@dataclass
class StopLossOrder:
    ticker: str
    quantity: int
    urgency: str = "EMERGENCY"
    reason: str = ""
    suggested_action: str = "SELL_ALL"

class StopLossEngine:
    
    def check_fast_exit(self, current_candle: Dict[str, float], ma20_vol: float) -> Optional[str]:
        """Lớp 1: Cắt sớm theo VSA (False Breakout / Rejection)"""
        c_open, c_high, c_low, c_close = current_candle.get("open", 0), current_candle.get("high", 0), current_candle.get("low", 0), current_candle.get("close", 0)
        c_vol = current_candle.get("volume", 0)
        
        if c_high == c_low or ma20_vol == 0:
            return None
            
        candle_range = c_high - c_low
        close_position = (c_close - c_low) / candle_range
        
        is_rejection = close_position <= 0.33
        is_high_volume = c_vol > (ma20_vol * 1.5)
        
        if is_rejection and is_high_volume and c_close < c_open:
            return "Fast Exit (Lớp 1): Bearish Rejection kèm Vol > 1.5x. Khóa lãi/Cắt lỗ sớm."
        return None

    def check_position(
        self, 
        ticker: str, 
        quantity: int, 
        entry_price: float, 
        current_price: float, 
        nav: float,
        market_data: Dict[str, Any] = None
    ) -> Optional[StopLossOrder]:
        """Kiểm tra một vị thế với hệ thống phòng thủ đa lớp."""
        if quantity <= 0:
            return None
            
        if market_data is None:
            market_data = {}
            
        unrealized_pnl = (current_price - entry_price) * quantity
        pnl_pct_nav = unrealized_pnl / nav
        
        # 1. Lớp 1: Fast Exit (Cắt sớm theo VSA)
        current_candle = market_data.get("current_candle")
        ma20_vol = market_data.get("ma20_volume", 0)
        if current_candle:
            fast_exit_reason = self.check_fast_exit(current_candle, ma20_vol)
            if fast_exit_reason:
                logger.critical(f"!!! FAST EXIT TRIGGERED for {ticker}: {fast_exit_reason} !!!")
                return StopLossOrder(ticker=ticker, quantity=int(quantity * 0.5), urgency="HIGH", reason=fast_exit_reason, suggested_action="REDUCE_50_PCT")
                
        # 2. Lớp 2: Structural Exit (Bảo vệ Cấu trúc / Swing Low)
        swing_low_price = market_data.get("swing_low_price")
        if swing_low_price and current_price < swing_low_price:
            reason = f"Structural Exit (Lớp 2): Giá đóng cửa ({current_price}) thủng Swing Low ({swing_low_price}). Gãy cấu trúc."
            logger.critical(f"!!! STRUCTURAL STOP TRIGGERED for {ticker}: {reason} !!!")
            return StopLossOrder(ticker=ticker, quantity=quantity, urgency="EMERGENCY", reason=reason, suggested_action="SELL_ALL")
            
        # 3. Lớp 3: Time Stop (Chi phí cơ hội)
        days_held = market_data.get("days_held", 0)
        expected_timeline = market_data.get("expected_timeline_days", 90)
        pnl_pct = (current_price - entry_price) / entry_price
        
        if days_held > (expected_timeline * 0.5) and pnl_pct < 0.02:
            reason = f"Time Stop (Lớp 3): Nắm giữ {days_held} ngày (> 50% timeline) nhưng lãi < 2%. Cảnh báo chôn vốn."
            logger.warning(f"TIME STOP WARNING for {ticker}: {reason}")
            return StopLossOrder(ticker=ticker, quantity=int(quantity * 0.5), urgency="MEDIUM", reason=reason, suggested_action="REDUCE_50_PCT")
        
        # 4. Hard Law Điều 1 (Legacy Fallback)
        if pnl_pct_nav <= -0.02:
            reason = f"Vi phạm Hard Law Điều 1: Lỗ {pnl_pct_nav*100:.2f}% NAV (Ngưỡng -2%)"
            logger.critical(f"!!! HARD STOP LOSS TRIGGERED for {ticker}: {reason} !!!")
            return StopLossOrder(ticker=ticker, quantity=quantity, urgency="EMERGENCY", reason=reason, suggested_action="SELL_ALL")
            
        return None

stop_loss_engine = StopLossEngine()
