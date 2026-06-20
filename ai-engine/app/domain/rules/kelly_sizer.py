"""Kelly Position Sizer — TASK-312

Tính toán quy mô vị thế tối ưu dựa trên công thức Kelly.
Áp dụng Quarter Kelly (1/4 Kelly) làm baseline an toàn.
Tự động điều chỉnh theo Market Regime.
"""

import logging
from typing import Dict, Any, Optional
from app.domain.rules.market.hmm_classifier import MarketRegime

logger = logging.getLogger(__name__)

class KellyPositionSizer:
    def __init__(self, baseline_kelly_fraction: float = 0.25):
        """
        Args:
            baseline_kelly_fraction: Mức fractional Kelly (VD: 0.25 = Quarter Kelly).
        """
        self.baseline_fraction = baseline_kelly_fraction

    def calculate_position_size(
        self, 
        prob_win: float, 
        win_loss_ratio: float, 
        regime: MarketRegime,
        nav: float
    ) -> float:
        """
        Tính toán quy mô vị thế (VND).
        
        Formula: Kelly % = W - (1 - W) / R
        Trong đó: W = xác suất thắng, R = tỷ lệ Win/Loss.
        """
        if win_loss_ratio <= 0:
            return 0.0
            
        full_kelly = prob_win - (1 - prob_win) / win_loss_ratio
        
        if full_kelly <= 0:
            return 0.0
            
        # 1. Apply baseline fractional Kelly (e.g. 1/4 Kelly)
        target_pct = full_kelly * self.baseline_fraction
        
        # 2. Regime-based Scaling (IOS DEC-12 reference for VN30F but applicable to sizing)
        # Bull Trending: Full Quarter Kelly
        # Bull Choppy: 0.75x scaling
        # Bear Bounce: 0.5x scaling
        # Bear Trending: 0.25x scaling (hoặc switch sang 1/8 Kelly)
        
        regime_multiplier = 1.0
        if regime == MarketRegime.BULL_CHOPPY:
            regime_multiplier = 0.75
        elif regime == MarketRegime.BEAR_BOUNCE:
            regime_multiplier = 0.5
        elif regime == MarketRegime.BEAR_TRENDING:
            regime_multiplier = 0.25 # Rất thận trọng
            
        final_target_pct = target_pct * regime_multiplier
        
        # 3. Cap at Hard Law limits (Security single stock limit = 15%)
        final_target_pct = min(final_target_pct, 0.15)
        
        return final_target_pct * nav

kelly_sizer = KellyPositionSizer()
