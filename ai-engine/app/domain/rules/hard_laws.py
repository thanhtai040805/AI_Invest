"""Hard Law Enforcement Engine — TASK-111

Kiểm tra các luật "bất khả xâm phạm" trước khi thực thi lệnh.
Tuân thủ Điều 1, 2, 4 của Investment Constitution.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict

class HardLaw(Enum):
    DIEU_1 = "Điều 1: Luật Tồn Tại (Rủi ro T+2.5 Floor Gap <= 2% NAV)"
    DIEU_2 = "Điều 2: Luật Thanh Khoản (Lệnh <= 15% ADTV20, Vị thế <= 25% ADTV20)"
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
    locked_t25_value: float = 0.0

class HardLawEngine:
    def check_order(
        self, 
        order: ProposedOrder, 
        portfolio: PortfolioState, 
        adtv20_continuous: float,
        risk_limits: Optional[Dict[str, float]] = None,
    ) -> HardLawCheck:
        """Kiểm tra một lệnh đề xuất với các Hard Laws thể chế chuẩn sàn HOSE."""
        # Nạp hạn mức động nếu có (fallback chuẩn IOS v5.1: 2% NAV risk, 15% Single Stock, 35% Sector)
        max_stop_loss_pct = (float(risk_limits.get("hard_stop_loss_pct", 2.0)) / 100.0) if risk_limits else 0.02
        max_stock_pct = (float(risk_limits.get("max_single_stock_pct", 15.0)) / 100.0) if risk_limits else 0.15
        max_sector_pct = (float(risk_limits.get("max_sector_pct", 35.0)) / 100.0) if risk_limits else 0.35
        
        # 1. Kiểm tra Điều 1 (Luật Tồn Tại & Rủi ro kẹt hàng T+2.5) - Chỉ áp dụng cho lệnh BUY
        if order.side == "BUY":
            if order.stop_loss_price is None:
                return HardLawCheck(False, HardLaw.DIEU_1, "Lệnh BUY bắt buộc phải có stop_loss_price xác định.")
            
            # Tính toán tổn thất lớn nhất giữa Stop-loss chỉ định và 2 cây sàn liên tiếp T+2.5 (13.51%)
            stop_loss_pct = (order.price - order.stop_loss_price) / order.price if order.price > 0 else 0.07
            effective_downside_pct = max(stop_loss_pct, 0.1351)
            
            risk_amount = (order.price * effective_downside_pct) * order.quantity
            max_allowed_risk = max_stop_loss_pct * portfolio.nav
            
            if risk_amount > max_allowed_risk:
                return HardLawCheck(
                    False, 
                    HardLaw.DIEU_1, 
                    f"Rủi ro vị thế tính theo T+2.5 Floor Gap ({risk_amount:,.0f} VND) vượt trần {max_stop_loss_pct*100:g}% NAV ({max_allowed_risk:,.0f} VND)."
                )

        # 2. Kiểm tra Điều 2 (Luật Thanh Khoản: Lệnh phiên <= 15% ADTV20, Vị thế <= 25% ADTV20)
        if adtv20_continuous > 0:
            # Kiểm tra quy mô lệnh đơn phiên
            if order.quantity > 0.15 * adtv20_continuous:
                return HardLawCheck(
                    False,
                    HardLaw.DIEU_2,
                    f"Khối lượng lệnh ({order.quantity:,.0f}) vượt 15% ADTV20 ({0.15 * adtv20_continuous:,.0f}). Tránh gây trượt giá lớn."
                )

            # Kiểm tra tổng quy mô vị thế tích lũy
            total_quantity = order.quantity
            if order.ticker in portfolio.positions:
                pos = portfolio.positions[order.ticker]
                pos_qty = int(pos.get("quantity", pos.get("shares", 0))) if isinstance(pos, dict) else 0
                total_quantity += pos_qty
                
            if total_quantity > 0.25 * adtv20_continuous:
                return HardLawCheck(
                    False, 
                    HardLaw.DIEU_2, 
                    f"Tổng khối lượng tích lũy ({total_quantity:,.0f}) vượt trần sức chứa 25% ADTV20 ({0.25 * adtv20_continuous:,.0f})."
                )

        # 3. Kiểm tra Điều 4 (Luật Tập Trung) - Chỉ áp dụng cho lệnh BUY
        if order.side == "BUY":
            order_value = order.price * order.quantity
            
            # Single Stock limit (mặc định 15% NAV)
            current_stock_value = 0.0
            if order.ticker in portfolio.positions:
                pos = portfolio.positions[order.ticker]
                if isinstance(pos, dict):
                    pos_qty = int(pos.get("quantity", pos.get("shares", 0)))
                    pos_pr = float(pos.get("current_price", pos.get("price", pos.get("average_price", order.price))))
                    current_stock_value = pos_qty * pos_pr
            
            if (current_stock_value + order_value) > max_stock_pct * portfolio.nav:
                return HardLawCheck(
                    False, 
                    HardLaw.DIEU_4, 
                    f"Tỷ trọng cổ phiếu {order.ticker} ({((current_stock_value + order_value)/portfolio.nav)*100:.1f}%) vượt trần {max_stock_pct*100:g}% NAV."
                )
            
            # Sector limit (mặc định 35% NAV)
            current_sector_value = portfolio.sector_exposure.get(order.sector, 0.0)
            if (current_sector_value + order_value) > max_sector_pct * portfolio.nav:
                return HardLawCheck(
                    False, 
                    HardLaw.DIEU_4, 
                    f"Tỷ trọng ngành {order.sector} ({((current_sector_value + order_value)/portfolio.nav)*100:.1f}%) vượt trần {max_sector_pct*100:g}% NAV."
                )

        return HardLawCheck(True, reason="Thỏa mãn 100% Hard Laws thể chế.")

hard_law_engine = HardLawEngine()

