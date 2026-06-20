"""Hard Law Enforcement Engine — TASK-111

Kiểm tra các luật "bất khả xâm phạm" trước khi thực thi lệnh.
Tuân thủ Điều 1, 2, 4 của Investment Constitution.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict

class HardLaw(Enum):
    DIEU_1 = "Điều 1: Luật Tồn Tại (Loss <= 2% NAV)"
    DIEU_2 = "Điều 2: Luật Thanh Khoản (Exit <= 5 phiên)"
    DIEU_4 = "Điều 4: Luật Tập Trung (Stock <= 15%, Sector <= 35% NAV)"

@dataclass
class HardLawCheck:
    passed: bool
    violated_law: Optional[HardLaw] = None
    reason: str = ""

@dataclass
class ProposedOrder:
    ticker: str
    side: str  # "BUY", "SELL"
    quantity: int
    price: float
    stop_loss_price: Optional[float] = None
    sector: str = "Unknown"

@dataclass
class PortfolioState:
    nav: float
    positions: Dict[str, Dict] = field(default_factory=dict) # ticker -> {quantity, current_price, sector}
    sector_exposure: Dict[str, float] = field(default_factory=dict) # sector -> value

class HardLawEngine:
    def check_order(
        self, 
        order: ProposedOrder, 
        portfolio: PortfolioState, 
        adtv20_continuous: float
    ) -> HardLawCheck:
        """Kiểm tra một lệnh đề xuất với các Hard Laws."""
        
        # 1. Kiểm tra Điều 1 (Luật Tồn Tại) - Chỉ áp dụng cho lệnh BUY mới hoặc điều chỉnh SL
        if order.side == "BUY":
            if order.stop_loss_price is None:
                return HardLawCheck(False, HardLaw.DIEU_1, "Lệnh BUY phải có stop_loss_price")
            
            risk_amount = (order.price - order.stop_loss_price) * order.quantity
            if risk_amount > 0.02 * portfolio.nav:
                return HardLawCheck(
                    False, 
                    HardLaw.DIEU_1, 
                    f"Rủi ro vị thế ({risk_amount:,.0f}) vượt 2% NAV ({0.02 * portfolio.nav:,.0f})"
                )

        # 2. Kiểm tra Điều 2 (Luật Thanh Khoản)
        # Giả định: Có thể thoát tối đa 20% ADTV mỗi phiên mà không gây tác động giá lớn.
        # Thoát trong 5 phiên -> max quantity = 5 * 20% ADTV = 100% ADTV.
        total_quantity = order.quantity
        if order.ticker in portfolio.positions:
            total_quantity += portfolio.positions[order.ticker]["quantity"]
            
        if total_quantity > adtv20_continuous:
            return HardLawCheck(
                False, 
                HardLaw.DIEU_2, 
                f"Tổng khối lượng ({total_quantity:,.0f}) vượt ADTV20 ({adtv20_continuous:,.0f})"
            )

        # 3. Kiểm tra Điều 4 (Luật Tập Trung) - Chỉ áp dụng cho lệnh BUY
        if order.side == "BUY":
            order_value = order.price * order.quantity
            
            # Single Stock limit (15%)
            current_stock_value = 0
            if order.ticker in portfolio.positions:
                pos = portfolio.positions[order.ticker]
                current_stock_value = pos["quantity"] * pos["current_price"]
            
            if (current_stock_value + order_value) > 0.15 * portfolio.nav:
                return HardLawCheck(
                    False, 
                    HardLaw.DIEU_4, 
                    f"Tỷ trọng cổ phiếu ({((current_stock_value + order_value)/portfolio.nav)*100:.1f}%) vượt 15% NAV"
                )
            
            # Sector limit (35%)
            current_sector_value = portfolio.sector_exposure.get(order.sector, 0)
            if (current_sector_value + order_value) > 0.35 * portfolio.nav:
                return HardLawCheck(
                    False, 
                    HardLaw.DIEU_4, 
                    f"Tỷ trọng ngành {order.sector} ({((current_sector_value + order_value)/portfolio.nav)*100:.1f}%) vượt 35% NAV"
                )

        return HardLawCheck(True)

hard_law_engine = HardLawEngine()
