"""CSS Scoring Engine — TASK-215

Tổng hợp điểm từ 6 nhóm Factor thành Composite Sentiment Score (CSS).
Áp dụng trọng số theo Market Regime.
Phân loại Conviction Level (A+, A, B, C, D).
"""

import logging
import pandas as pd
from typing import List, Dict, Any, Optional
from enum import Enum
from app.domain.rules.market.hmm_classifier import MarketRegime

logger = logging.getLogger(__name__)

class ConvictionLevel(Enum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"

class CSSScoringEngine:
    def __init__(self):
        # REGIME_WEIGHTS định nghĩa trong IOS/Blueprint
        self.regime_weights = {
            MarketRegime.BULL_TRENDING: {
                "f1_value": 0.1, "f2_quality": 0.2, "f3_momentum": 0.4, 
                "f4_sentiment": 0.2, "f6_altdata": 0.1
            },
            MarketRegime.BEAR_TRENDING: {
                "f1_value": 0.4, "f2_quality": 0.3, "f3_momentum": 0.1, 
                "f4_sentiment": 0.1, "f6_altdata": 0.1
            },
            # Default weights if not specified
            "DEFAULT": {
                "f1_value": 0.2, "f2_quality": 0.2, "f3_momentum": 0.2, 
                "f4_sentiment": 0.2, "f6_altdata": 0.2
            }
        }

    def calculate_css(self, factor_scores: pd.DataFrame, regime: MarketRegime) -> pd.DataFrame:
        """Tính CSS dựa trên trọng số regime."""
        weights = self.regime_weights.get(regime, self.regime_weights["DEFAULT"])
        
        # Đảm bảo các cột điểm tồn tại, nếu không coi là 50 (neutral)
        for f in weights.keys():
            if f not in factor_scores.columns:
                factor_scores[f] = 50.0
                
        # Tính weighted average CSS
        factor_scores['css'] = sum(factor_scores[f] * w for f, w in weights.items())
        
        # IOS DEC-11: Bear Trending -> CSS * 0.5
        if regime == MarketRegime.BEAR_TRENDING:
            factor_scores['css'] *= 0.5
            
        # Xác định Conviction Level
        factor_scores['conviction'] = factor_scores['css'].apply(self._get_conviction)
        
        return factor_scores

    def _get_conviction(self, css: float) -> str:
        """Phân loại Conviction Level theo ngưỡng trong Spec."""
        if css >= 85: return ConvictionLevel.A_PLUS.value
        if css >= 75: return ConvictionLevel.A.value
        if css >= 60: return ConvictionLevel.B.value
        if css >= 45: return ConvictionLevel.C.value
        return ConvictionLevel.D.value

css_scoring_engine = CSSScoringEngine()
