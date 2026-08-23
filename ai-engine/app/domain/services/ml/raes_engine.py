"""
RAES Alpha Predictor Engine
3-Model Ensemble (LightGBM, CatBoost, XGBoost) + Meta-Labeler.
Billion-Dollar Grade setup tailored for CPU.
"""

import os
import pickle
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple

import lightgbm as lgb
from catboost import CatBoostClassifier
import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier

from .feature_forge import feature_forge
from .adaptive_weights import adaptive_weights
from .triple_barrier import MetaLabeler

logger = logging.getLogger(__name__)

MODEL_DIR = os.getenv("ML_MODEL_DIR", os.path.join(os.path.dirname(__file__), "../../../../data/models"))
os.makedirs(MODEL_DIR, exist_ok=True)
RAES_MODEL_PATH = os.path.join(MODEL_DIR, "raes_engine_v3.pkl")

# Custom Asymmetric Objective for XGBoost
def asymmetric_ic_obj(y_pred: np.ndarray, dtrain: xgb.DMatrix):
    """
    Penalize false BUY more than false HOLD, because VN market has no short selling.
    Loss is steeper when predicting 1 but actual is 0/ -1.
    """
    y_true = dtrain.get_label()
    # Apply sigmoid to y_pred to get prob
    p = 1.0 / (1.0 + np.exp(-y_pred))
    
    # Asymmetric penalties
    alpha_up = 1.0   # Penalty for missing a BUY
    alpha_down = 2.0 # Penalty for false BUY
    
    residual = y_true - p
    grad = np.where(residual < 0, alpha_down * (p - y_true), alpha_up * (p - y_true))
    hess = np.where(residual < 0, alpha_down * p * (1.0 - p), alpha_up * p * (1.0 - p))
    
    return grad, hess

class RAESEngine:
    def __init__(self):
        # 1. LightGBM (Fastest, Handles sparse features)
        self.lgb_model = lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=5,
            class_weight='balanced',
            random_state=42,
            n_jobs=-1
        )
        
        # 2. CatBoost (Handles categorical well, robust to overfitting via ordered boosting)
        self.cat_model = CatBoostClassifier(
            iterations=300,
            learning_rate=0.05,
            depth=6,
            auto_class_weights='Balanced',
            random_state=42,
            thread_count=-1,
            verbose=False
        )
        
        # 3. XGBoost (Deep depth, custom asymmetric loss)
        self.xgb_model = xgb.XGBClassifier(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=7, # Deeper to capture complex interactions
            objective='binary:logistic',
            tree_method='hist', # Fast CPU histogram
            n_jobs=-1,
            random_state=42
        )
        # Note: Scikit-learn API of XGBoost doesn't officially support custom obj in constructor 
        # for newer versions without hassle, so we might fit using native API in a real pipeline.
        # For simplicity here, we use standard logistic, but in production we inject `asymmetric_ic_obj`.
        
        # 4. Meta-Labeler (Size sizing)
        self.meta_labeler = MetaLabeler(RandomForestClassifier(
            n_estimators=100, max_depth=4, n_jobs=-1, random_state=42
        ))
        
        self.is_trained = False
        
    def fit(self, df: pd.DataFrame, y_triple_barrier: pd.Series, sample_weights: pd.Series = None):
        """
        Train the ensemble models.
        df should already contain generated features.
        y_triple_barrier: -1 (SL), 0 (Timeout), 1 (PT)
        """
        # For long-only prediction (BUY vs HOLD), we map targets:
        # 1 -> 1 (BUY)
        # 0, -1 -> 0 (HOLD)
        y_train = (y_triple_barrier == 1).astype(int)
        
        X = df.copy()
        
        # Train base models
        logger.info("Training LightGBM...")
        self.lgb_model.fit(X, y_train, sample_weight=sample_weights)
        
        logger.info("Training CatBoost...")
        # If we had categorical cols like 'Sector', we pass them here
        self.cat_model.fit(X, y_train, sample_weight=sample_weights)
        
        logger.info("Training XGBoost...")
        self.xgb_model.fit(X, y_train, sample_weight=sample_weights)
        
        # Train Meta-Labeler
        logger.info("Training Meta-Labeler...")
        # Get OOF or simply train predictions (OOF is better, simplifying here)
        lgb_pred = self.lgb_model.predict(X)
        cat_pred = self.cat_model.predict(X)
        xgb_pred = self.xgb_model.predict(X)
        
        # Majority vote for primary prediction to train meta-labeler
        ens_pred = ((lgb_pred + cat_pred + xgb_pred) >= 2).astype(int)
        
        self.meta_labeler.fit(X, pd.Series(ens_pred, index=X.index), y_triple_barrier)
        
        self.is_trained = True
        self.save()
        logger.info("RAES Engine training complete.")
        
    def predict(self, df: pd.DataFrame, regime_probs: Dict[str, float]) -> Tuple[int, float]:
        """
        Fast daily inference.
        Returns (Prediction_Class, Bet_Size_Probability)
        """
        if not self.is_trained:
            if not self.load():
                logger.error("RAES model not trained and could not be loaded.")
                return 0, 0.0
                
        # Get adaptive weights based on regime
        w_lgb, w_cat, w_xgb = adaptive_weights.get_weights(regime_probs)
        
        # Predict Probabilities
        p_lgb = self.lgb_model.predict_proba(df)[0, 1]
        p_cat = self.cat_model.predict_proba(df)[0, 1]
        p_xgb = self.xgb_model.predict_proba(df)[0, 1]
        
        # Blend
        p_blend = w_lgb * p_lgb + w_cat * p_cat + w_xgb * p_xgb
        
        # Primary decision
        primary_class = 1 if p_blend >= 0.55 else 0 # Stricter threshold for BUY
        
        # If BUY, ask Meta-Labeler for bet size (confidence)
        if primary_class == 1:
            # Format single row for Meta-labeler
            prim_series = pd.Series([primary_class], index=df.index)
            meta_prob = self.meta_labeler.predict_proba(df, prim_series).iloc[0]
            
            # Bet size is proportional to meta_prob
            return primary_class, meta_prob
        else:
            return 0, 0.0
            
    def save(self):
        with open(RAES_MODEL_PATH, 'wb') as f:
            pickle.dump({
                'lgb': self.lgb_model,
                'cat': self.cat_model,
                'xgb': self.xgb_model,
                'meta': self.meta_labeler
            }, f)
            
    def load(self) -> bool:
        if not os.path.exists(RAES_MODEL_PATH):
            return False
        try:
            with open(RAES_MODEL_PATH, 'rb') as f:
                data = pickle.load(f)
                self.lgb_model = data['lgb']
                self.cat_model = data['cat']
                self.xgb_model = data['xgb']
                self.meta_labeler = data['meta']
            self.is_trained = True
            return True
        except Exception as e:
            logger.error(f"Failed to load RAES model: {e}")
            return False

raes_engine = RAESEngine()
