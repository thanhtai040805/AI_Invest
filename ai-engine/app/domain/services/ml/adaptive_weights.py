"""
Adaptive Regime-Conditional Blending Weights
Adjusts ensemble model weights based on current market regime.
"""

from typing import Dict, Tuple
from app.domain.rules.market.hmm_regime_engine import MarketRegimeV2

class AdaptiveWeights:
    """
    Provides blending weights for LightGBM, CatBoost, and XGBoost
    conditional on the current market regime.
    
    Weights should sum to 1.0.
    """
    def __init__(self):
        # Baseline Equal Weights
        self.baseline = (0.33, 0.33, 0.34)
        
        # Mapping: Regime -> (w_lgb, w_cat, w_xgb)
        # LGBM: Good with momentum and sparse anomalies.
        # CatBoost: Excellent with categorical (sectors) and distribution tracking.
        # XGBoost: Deep depth, good for complex interactions in panic/grinding.
        self.regime_weights = {
            MarketRegimeV2.BULL_MOMENTUM: (0.50, 0.20, 0.30),
            MarketRegimeV2.BULL_DISTRIBUTION: (0.20, 0.60, 0.20),
            MarketRegimeV2.RANGE_BOUND: (0.30, 0.40, 0.30),
            MarketRegimeV2.BEAR_GRINDING: (0.10, 0.30, 0.60),
            MarketRegimeV2.BEAR_PANIC: (0.20, 0.10, 0.70),
            MarketRegimeV2.RECOVERY_EARLY: (0.40, 0.30, 0.30),
        }
        
    def get_weights(self, regime_probs: Dict[str, float]) -> Tuple[float, float, float]:
        """
        Calculate blended weights by taking the expectation over regime probabilities.
        """
        w_lgb = 0.0
        w_cat = 0.0
        w_xgb = 0.0
        
        for regime, prob in regime_probs.items():
            w = self.regime_weights.get(regime, self.baseline)
            w_lgb += w[0] * prob
            w_cat += w[1] * prob
            w_xgb += w[2] * prob
            
        # Normalize just in case
        total = w_lgb + w_cat + w_xgb
        if total == 0:
            return self.baseline
            
        return (w_lgb / total, w_cat / total, w_xgb / total)

adaptive_weights = AdaptiveWeights()
