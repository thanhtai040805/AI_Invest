import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class CatalystValidator:
    """
    Validates Investment Catalysts based on the 80/20 Rule and Anti-Trap mechanisms.
    """
    VALID_CATALYST_TYPES = [
        "Sự kiện tài chính", # M&A, Thoái vốn, Chuyển sàn
        "Đảo chiều chu kỳ", # Macro_Price_Turnaround
        "Mở rộng công suất" # Capacity Expansion
    ]
    
    def check_peai_accumulation(self, volume_data_3w: List[float], price_data_3w: List[float], sue_score: float) -> str:
        """
        Kiểm tra rò rỉ thông tin trước báo cáo (Pre-Earnings Accumulation Index).
        """
        if not volume_data_3w or not price_data_3w:
            return "BUY" # Thiếu data thì mặc định theo SUE
            
        avg_vol = sum(volume_data_3w) / len(volume_data_3w)
        max_vol = max(volume_data_3w)
        price_runup = (price_data_3w[-1] - price_data_3w[0]) / price_data_3w[0]
        
        # Nếu Vol vọt > 200% trung bình và giá đã chạy > 20% trước tin
        if max_vol > avg_vol * 2.0 and price_runup > 0.20:
            logger.warning("PEAI WARNING: Information leakage detected. Price already ran up > 20%.")
            if sue_score > 1.5:
                return "HOLD" # Tránh bẫy Sell-on-News
                
        return "BUY"

    def check_false_breakout_entry(self, current_candle: Dict[str, float], ma20_vol: float) -> bool:
        """
        Bộ lọc Phá vỡ giả: Từ chối mua nếu xuất hiện nến từ chối (Rejection) kèm Vol lớn.
        current_candle: {"open": x, "high": y, "low": z, "close": w, "volume": v}
        Trả về True nếu là False Breakout (Bẫy).
        """
        c_open, c_high, c_low, c_close = current_candle.get("open", 0), current_candle.get("high", 0), current_candle.get("low", 0), current_candle.get("close", 0)
        c_vol = current_candle.get("volume", 0)
        
        if c_high == c_low or ma20_vol == 0:
            return False
            
        # Xác định nến bị từ chối (rút râu trên dài, đóng cửa ở 1/3 dưới của thân nến)
        candle_range = c_high - c_low
        close_position = (c_close - c_low) / candle_range
        
        is_rejection = close_position <= 0.33 # Đóng cửa ở 1/3 thấp nhất
        is_high_volume = c_vol > (ma20_vol * 1.5)
        
        if is_rejection and is_high_volume:
            logger.warning("FALSE BREAKOUT DETECTED: Rejection candle with > 1.5x Volume. Entry denied.")
            return True
            
        return False
    
    def validate_catalyst(self, catalyst_data: Dict[str, Any], financial_context: Dict[str, Any], market_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Kiểm tra ngòi nổ có đủ điều kiện không.
        """
        if market_data is None:
            market_data = {}
            
        # 0. Check False Breakout Filter (Pre-emptive)
        current_candle = market_data.get("current_candle")
        ma20_vol = market_data.get("ma20_volume", 0)
        if current_candle and self.check_false_breakout_entry(current_candle, ma20_vol):
            return {
                "is_valid": False,
                "reason": "Entry Invalidation: False Breakout (Upthrust) detected with high volume."
            }
            
        c_type = catalyst_data.get("type", "")
        expected_growth = catalyst_data.get("expected_growth_pct", 0.0)
        sue_score = catalyst_data.get("sue_score", 0.0)
        
        # Check PEAI
        peai_action = self.check_peai_accumulation(
            volume_data_3w=market_data.get("volume_3w", []),
            price_data_3w=market_data.get("price_3w", []),
            sue_score=sue_score
        )
        if peai_action == "HOLD":
            return {
                "is_valid": False,
                "reason": "PEAI Warning: Price already ran up significantly before earnings. Action changed to HOLD."
            }
        
        # 1. Bắt buộc tăng trưởng >= 20%
        if expected_growth < 20.0:
            return {
                "is_valid": False,
                "reason": f"Expected growth ({expected_growth}%) is less than 20% threshold."
            }
            
        # 2. Phân loại ngòi nổ hợp lệ
        is_valid_type = False
        for valid_type in self.VALID_CATALYST_TYPES:
            if valid_type.lower() in c_type.lower() or c_type.lower() in valid_type.lower() or c_type == "Macro_Price_Turnaround":
                is_valid_type = True
                break
                
        if not is_valid_type:
            return {
                "is_valid": False,
                "reason": f"Catalyst type '{c_type}' is not in the approved 80/20 list."
            }
            
        # 3. Anti-Trap cho Mở rộng công suất
        if "mở rộng công suất" in c_type.lower() or "capacity" in c_type.lower():
            utilization = financial_context.get("current_utilization", 0.0)
            inv_trend = financial_context.get("inventory_trend", "increasing")
            
            if utilization <= 80.0:
                return {
                    "is_valid": False,
                    "reason": f"Anti-Trap: Cannot expand capacity when current utilization is {utilization}% (<= 80%)."
                }
                
            if inv_trend != "decreasing":
                return {
                    "is_valid": False,
                    "reason": f"Anti-Trap: Cannot expand capacity when inventory is {inv_trend}."
                }
                
        return {
            "is_valid": True,
            "reason": "Catalyst passes all 80/20 rules."
        }

catalyst_validator = CatalystValidator()
