import logging
from typing import Dict, Any

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
    
    def validate_catalyst(self, catalyst_data: Dict[str, Any], financial_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Kiểm tra ngòi nổ có đủ điều kiện không.
        catalyst_data: { "type": "...", "expected_growth_pct": 25.0 }
        financial_context: { "current_utilization": 85.0, "inventory_trend": "decreasing" }
        """
        c_type = catalyst_data.get("type", "")
        expected_growth = catalyst_data.get("expected_growth_pct", 0.0)
        
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
