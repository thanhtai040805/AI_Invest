"""
Composite Stock Score (CSS) Weights Engine
Adjusts factor weights based on the current market regime from HMM.
"""

from typing import Dict
from app.domain.rules.market.hmm_regime_engine import MarketRegimeV2

class CSSScoring:
    """
    Provides factor weights (F1-F6 + Alpha) for AGENT-03 based on Regime.
    """
    def __init__(self):
        # Weights: (Value, Growth, Momentum, Quality, Volatility, Alpha_Predictor)
        self.regime_weights = {
            MarketRegimeV2.BULL_MOMENTUM: (0.1, 0.2, 0.4, 0.1, 0.0, 0.2),
            MarketRegimeV2.BULL_DISTRIBUTION: (0.2, 0.1, 0.1, 0.3, 0.1, 0.2),
            MarketRegimeV2.RANGE_BOUND: (0.3, 0.1, 0.1, 0.3, 0.0, 0.2),
            MarketRegimeV2.BEAR_PANIC: (0.1, 0.0, 0.0, 0.4, 0.3, 0.2),
            MarketRegimeV2.BEAR_GRINDING: (0.4, 0.1, 0.0, 0.3, 0.0, 0.2),
            MarketRegimeV2.RECOVERY_EARLY: (0.3, 0.3, 0.2, 0.0, 0.0, 0.2),
        }
        
    def get_weights(self, regime: str) -> Dict[str, float]:
        w = self.regime_weights.get(regime, (0.2, 0.2, 0.2, 0.2, 0.0, 0.2))
        return {
            "Value": w[0],
            "Growth": w[1],
            "Momentum": w[2],
            "Quality": w[3],
            "Low_Volatility": w[4],
            "ML_Alpha": w[5]
        }

css_scoring = CSSScoring()
