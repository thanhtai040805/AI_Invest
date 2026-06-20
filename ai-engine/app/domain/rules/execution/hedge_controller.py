"""VN30F Hedge Controller — TASK-402

Quản lý việc phòng vệ danh mục (Hedging) bằng hợp đồng tương lai VN30.
Trigger: HMM Bear Trending (>80% prob) hoặc Market Breadth < 15%.
Hành động: Đề xuất lệnh Short VN30F để offset rủi ro giảm điểm.
"""

import logging
from typing import Dict, Any, Optional
from app.domain.rules.market.hmm_classifier import MarketRegime

logger = logging.getLogger(__name__)

class VNHedgeController:
    def __init__(self, contract_multiplier: int = 100_000):
        # VN30F multiplier is 100,000 VND per point
        self.multiplier = contract_multiplier

    def calculate_hedge_requirement(
        self, 
        portfolio_value: float, 
        vn30_index: float,
        hmm_bear_prob: float,
        market_breadth: float,
        regime: MarketRegime
    ) -> Dict[str, Any]:
        """
        Tính toán số lượng hợp đồng VN30F cần Short để phòng vệ.
        """
        # CDC ACTIVE triggers
        cdc_active = (market_breadth < 15.0) or (regime == MarketRegime.BEAR_TRENDING and hmm_bear_prob > 0.8)
        
        if not cdc_active:
            return {"short_contracts": 0, "cdc_status": "INACTIVE"}
            
        # Tính Hedge Ratio (Đơn giản hóa: 100% hedge nếu CDC Active)
        hedge_value = portfolio_value 
        
        # Số lượng hợp đồng = Giá trị danh mục / (VN30 Index * Multiplier)
        num_contracts = int(hedge_value / (vn30_index * self.multiplier))
        
        logger.warning(f"!!! CDC ACTIVE !!! Hedge requirement: {num_contracts} contracts of VN30F")
        
        return {
            "short_contracts": num_contracts,
            "cdc_status": "ACTIVE",
            "reason": "Market Breadth < 15% or High Bear Probability"
        }

hedge_controller = VNHedgeController()
