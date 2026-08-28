import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class StopLossOrder:
    ticker: str
    quantity: int
    urgency: str # "EMERGENCY", "HIGH", "MEDIUM"
    reason: str
    suggested_action: str # "SELL_ALL", "REDUCE_50_PCT"

class StopLossEngine:
    """
    IOS v5.1 Multi-tier Stop-Loss Rule Engine.
    Lớp 1: Fast Exit (Bearish Rejection + Vol > 1.5x MA20)
    Lớp 2: Structural Exit (Thủng Swing Low)
    Lớp 3: Time Stop (Chi phí cơ hội)
    Lớp 4: Hard Stop (Lỗ >= 2% NAV)
    """
    
    def check_fast_exit(self, candle: Dict[str, Any], ma20_volume: float) -> Optional[str]:
        """Lớp 1: Phát hiện nến từ chối tăng (Bearish Rejection) với Volume đột biến."""
        open_p = candle.get("open", 0)
        close_p = candle.get("close", 0)
        high_p = candle.get("high", 0)
        low_p = candle.get("low", 0)
        volume = candle.get("volume", 0)
        
        if high_p == low_p or volume == 0:
            return None
            
        upper_wick = high_p - max(open_p, close_p)
        candle_range = high_p - low_p
        
        # Râu trên dài > 50% thân nến và Vol > 1.5x MA20
        if (upper_wick / candle_range > 0.5) and (volume > 1.5 * ma20_volume):
            return "Bearish Rejection (Lớp 1): Râu nến trên dài bất thường kèm Volume đột biến > 1.5x MA20."
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
        pnl_pct_nav = (unrealized_pnl / nav) if nav > 0 else 0.0
        
        # 1. Lớp 1: Fast Exit (Cắt sớm theo VSA)
        current_candle = market_data.get("current_candle")
        ma20_vol = market_data.get("ma20_volume", 0)
        if current_candle:
            fast_exit_reason = self.check_fast_exit(current_candle, ma20_vol)
            if fast_exit_reason:
                logger.critical(f"!!! FAST EXIT TRIGGERED for {ticker}: {fast_exit_reason} !!!")
                return StopLossOrder(ticker=ticker, quantity=int(quantity * 0.5), urgency="HIGH", reason=fast_exit_reason, suggested_action="REDUCE_50_PCT")
                
        # 2. Lớp 2: Structural Exit (Bảo vệ Cấu trúc / Swing Low)
        swing_low_price = market_data.get("swing_low_price") or market_data.get("swing_low")
        if swing_low_price and current_price < swing_low_price:
            reason = f"Structural Exit (Lớp 2): Giá đóng cửa ({current_price}) thủng Swing Low ({swing_low_price}). Gãy cấu trúc."
            logger.critical(f"!!! STRUCTURAL STOP TRIGGERED for {ticker}: {reason} !!!")
            return StopLossOrder(ticker=ticker, quantity=quantity, urgency="EMERGENCY", reason=reason, suggested_action="SELL_ALL")
            
        # 3. Lớp 3: Time Stop (Chi phí cơ hội)
        days_held = market_data.get("days_held") or market_data.get("holding_days", 0)
        expected_timeline = market_data.get("expected_timeline_days", 90)
        pnl_pct = ((current_price - entry_price) / entry_price) if entry_price > 0 else 0.0
        
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
