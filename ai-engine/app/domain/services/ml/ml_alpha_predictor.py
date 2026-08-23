"""ML Alpha Predictor Wrapper

This module is a backward-compatible wrapper for the new RAESEngine.
It handles feature generation using FeatureForge, then calls the 
RAES 3-model ensemble for predictions.
"""

import logging
import pandas as pd
from typing import Dict, Tuple

from .raes_engine import raes_engine
from .feature_forge import feature_forge
from app.domain.rules.market.hmm_classifier import hmm_classifier

logger = logging.getLogger(__name__)

class MLAlphaPredictor:
    """
    Wrapper for RAESEngine to maintain interface compatibility with AGENT-03 (Research).
    """
    
    def __init__(self):
        # We rely on the RAES engine loaded state
        pass
        
    def predict_alpha(self, df: pd.DataFrame, ticker: str, current_regime: str = None) -> float:
        """
        Dự đoán Alpha Score (xác suất tăng giá) cho một cổ phiếu.
        
        Args:
            df: OHLCV DataFrame (cần ít nhất 120 phiên lịch sử để tính feature).
            ticker: Mã chứng khoán.
            current_regime: Market Regime hiện tại (nếu None sẽ dùng fallback probabilities).
            
        Returns:
            Alpha Score (0.0 to 1.0). Nếu < 0.5 là kém, > 0.6 là tốt.
        """
        # 1. Feature Engineering (Generates 80+ features)
        features_df = feature_forge.generate(df, ticker)
        
        if features_df.empty:
            logger.warning(f"Feature Forge failed for {ticker} (not enough data). Returning neutral score.")
            return 0.5
            
        # Lấy dòng mới nhất để inference
        latest_features = features_df.iloc[[-1]]
        
        # 2. Get Regime Probabilities
        if current_regime:
            # If a strict regime is passed, we simulate probabilities
            regime_probs = {r: 0.0 for r in hmm_classifier.states}
            regime_probs[current_regime] = 1.0
        else:
            # Fallback uniform
            regime_probs = {r: 1.0/len(hmm_classifier.states) for r in hmm_classifier.states}
            
        # 3. RAES Inference
        try:
            # Returns (Primary_Class [0,1], Bet_Size_Probability [0.0-1.0])
            pred_class, meta_prob = raes_engine.predict(latest_features, regime_probs)
            
            # Map into a single 0-1 continuous score for compatibility
            # If pred_class == 1, score = 0.5 + 0.5 * meta_prob (Scale from 0.5 to 1.0)
            # If pred_class == 0, score = 0.5 - 0.5 * meta_prob (Assuming meta_prob acts as confidence in prediction, but here meta_prob is only returned if class=1 in RAES)
            
            if pred_class == 1:
                final_score = 0.5 + (0.5 * meta_prob)
            else:
                # Without meta-prob for HOLD, we just return a neutral/bearish score
                # We could pull the raw blend prob from RAES if we refactored slightly, 
                # but for now we default to 0.4
                final_score = 0.4
                
            return float(final_score)
            
        except Exception as e:
            logger.error(f"RAES Predict failed for {ticker}: {e}")
            return 0.5

ml_alpha_predictor = MLAlphaPredictor()
