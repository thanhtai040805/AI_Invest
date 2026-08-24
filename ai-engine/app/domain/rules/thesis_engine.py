import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ThesisEngine:
    """
    AGENT-04: THESIS AGENT
    Xây dựng luận điểm đầu tư có cấu trúc, bao gồm định giá động và quyền phủ quyết.
    """
    
    def calculate_adaptive_target_price(self, 
                                        timeline_months: int, 
                                        pe_comp_price: float, 
                                        ev_ebitda_comp_price: float, 
                                        dcf_price: float, 
                                        regime_label: str) -> float:
        """
        Tính toán Target Price động, loại bỏ DCF cho timeline ngắn và thêm Regime Premium.
        """
        # Nếu timeline <= 3 tháng, loại bỏ hoàn toàn DCF
        if timeline_months <= 3:
            base_price = (pe_comp_price * 0.5) + (ev_ebitda_comp_price * 0.5)
        else:
            base_price = (pe_comp_price * 0.35) + (ev_ebitda_comp_price * 0.35) + (dcf_price * 0.3)
            
        # Thêm Regime Momentum Premium
        premium = 0.0
        if regime_label in ["BULL", "LIQUIDITY_EXPANSION"]:
            premium = 0.15  # Tăng thêm 15% target nếu Vĩ mô bùng nổ
            logger.info(f"Applying Regime Premium (+15%) to target price due to {regime_label}")
            
        target_price = base_price * (1 + premium)
        return round(target_price, 2)
        
    def evaluate_idiosyncratic_veto(self, 
                                    moat_score: float, 
                                    micro_score: float, 
                                    macro_score: float) -> bool:
        """
        Quyền phủ quyết: Nếu Cơ bản và Dòng tiền cực kỳ xuất sắc, phớt lờ Vĩ mô.
        (moat_score: Agent 1, micro_score: Agent 2, macro_score: Agent 3. Thang điểm 0-100)
        Trả về True nếu kích hoạt Veto.
        """
        # Giả sử điểm số từ 0-100
        if moat_score > 90 and micro_score > 90:
            logger.warning("IDIOSYNCRATIC VETO ACTIVATED: Exceptional Moat and Micro scores overriding Macro!")
            return True
        return False
        
    def generate_thesis(self, ticker: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Tổng hợp Investment Thesis.
        """
        timeline = data.get("timeline_months", 3)
        regime = data.get("regime_label", "SIDEWAYS")
        
        target_price = self.calculate_adaptive_target_price(
            timeline_months=timeline,
            pe_comp_price=data.get("pe_price", 0),
            ev_ebitda_comp_price=data.get("ev_ebitda_price", 0),
            dcf_price=data.get("dcf_price", 0),
            regime_label=regime
        )
        
        # Đánh giá Veto
        veto_active = self.evaluate_idiosyncratic_veto(
            moat_score=data.get("moat_score", 0),
            micro_score=data.get("micro_score", 0),
            macro_score=data.get("macro_score", 0)
        )
        
        # Logic đơn giản để ra quyết định
        is_valid = False
        if veto_active:
            is_valid = True
        else:
            # Rule bình thường (Cần Macro ok)
            if data.get("macro_score", 0) > 50 and data.get("moat_score", 0) > 50 and data.get("micro_score", 0) > 50:
                is_valid = True
                
        return {
            "ticker": ticker,
            "is_valid": is_valid,
            "veto_activated": veto_active,
            "target_price": target_price,
            "timeline_months": timeline,
            "status": "APPROVED" if is_valid else "REJECTED"
        }

thesis_engine = ThesisEngine()
