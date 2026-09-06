"""
Production-Grade Hybrid Stacking Alpha Ranker & Forensic Gate (EXP-016 Core).
Combines:
  1. Layer 0: Beneish M-Score Forensic Filter (M <= -1.78)
  2. Branch 1: LambdaMART Cross-Sectional Ranker (NDCG@5)
  3. Branch 2: Multi-Horizon 3-Day Momentum Ridge Regressor (T+2.5 Holding Momentum)
  4. Branch 3: T+2.5 Survival Gate Classifier (P(No severe drawdown in locked period))
"""

import os
import sys
import logging
import numpy as np
import pandas as pd
import psycopg2
from typing import Dict, List, Tuple, Optional
from sklearn.linear_model import Ridge
import lightgbm as lgb

from app.infrastructure.database.pg_pool import get_conn

logger = logging.getLogger(__name__)

class BeneishMScoreEngine:
    """
    Computes Beneish M-Score for HOSE universe using published financial statements.
    Ensures zero look-ahead bias by strictly matching on published_date.
    """
    def __init__(self):
        self.scores_cache = {}

    def fetch_and_compute_scores(self, tickers: List[str]) -> pd.DataFrame:
        if not tickers:
            return pd.DataFrame()
            
        try:
            with get_conn() as conn:
                query_ratios = """
                    SELECT symbol as ticker, ratio_date, published_date,
                           gross_margin, net_margin, current_ratio, debt_equity,
                           yoy_revenue_growth, yoy_earnings_growth, roe, roa
                    FROM financial_ratios
                    WHERE symbol = ANY(%s)
                    ORDER BY symbol, ratio_date;
                """
                df_r = pd.read_sql(query_ratios, conn, params=(list(tickers),))
        except Exception as e:
            logger.error(f"Error fetching financial ratios: {e}")
            return pd.DataFrame()

        if df_r.empty:
            return pd.DataFrame()

        df_r['published_date'] = pd.to_datetime(df_r['published_date'])
        df_r['ratio_date'] = pd.to_datetime(df_r['ratio_date'])

        # Compute Beneish proxies with safe data cleaning and outlier clipping
        df_r['sgi'] = (1.0 + df_r['yoy_revenue_growth'].fillna(0.0)).clip(0.2, 5.0)

        df_r['prev_gm'] = df_r.groupby('ticker')['gross_margin'].shift(1)
        df_r['gmi'] = (df_r['prev_gm'] / (df_r['gross_margin'] + 1e-6)).fillna(1.0).clip(0.2, 5.0)

        df_r['aqi'] = (1.0 + (df_r['roe'].fillna(0.0) - df_r['roa'].fillna(0.0))).clip(0.5, 3.0)

        df_r['prev_de'] = df_r.groupby('ticker')['debt_equity'].shift(1)
        df_r['lvgi'] = ((1.0 + df_r['debt_equity'].fillna(1.0)) / (1.0 + df_r['prev_de'].fillna(1.0))).clip(0.5, 3.0)

        df_r['dsri'] = (1.0 + 0.5 * df_r['yoy_revenue_growth'].fillna(0.0) - 0.2 * (df_r['current_ratio'].fillna(1.0) - 1.0)).clip(0.5, 3.0)
        df_r['depi'] = 1.0
        df_r['sgai'] = 1.0
        df_r['tata'] = (df_r['roe'].fillna(0.0) - df_r['roa'].fillna(0.0)).clip(-0.5, 0.5)

        # Compute Beneish M-Score
        df_r['beneish_m_score'] = (
            -4.84
            + 0.920 * df_r['dsri']
            + 0.528 * df_r['gmi']
            + 0.404 * df_r['aqi']
            + 0.892 * df_r['sgi']
            + 0.115 * df_r['depi']
            - 0.172 * df_r['sgai']
            + 4.037 * df_r['tata']
            + 0.0327 * df_r['lvgi']
        )

        df_r['is_manipulator'] = (df_r['beneish_m_score'] > -1.78).astype(int)
        return df_r[['ticker', 'published_date', 'beneish_m_score', 'is_manipulator']]

beneish_engine = BeneishMScoreEngine()


class HybridStackingRanker:
    """
    3-Branch Ensemble Architecture for HOSE T+2.5 Alpha:
      - Branch 1: LambdaMART Ranker (NDCG@5 optimization)
      - Branch 2: 3-Day Momentum Ridge Regressor (T+2.5 holding momentum)
      - Branch 3: T+2.5 Survival Gate Classifier (P(No severe drawdown in locked period))
    """
    def __init__(self, n_estimators: int = 100, learning_rate: float = 0.05, max_depth: int = 5):
        self.ranker = lgb.LGBMRanker(
            objective='lambdarank',
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            max_depth=max_depth,
            random_state=42,
            n_jobs=4,
            importance_type='gain'
        )
        self.regressor = Ridge(alpha=10.0)
        self.survival_gate = lgb.LGBMClassifier(
            n_estimators=60,
            learning_rate=learning_rate,
            max_depth=4,
            random_state=42,
            n_jobs=4
        )
        self.feature_cols: List[str] = []
        self.is_fitted: bool = False

    def fit(self, train_df: pd.DataFrame, feature_cols: List[str]):
        self.feature_cols = feature_cols
        X = train_df[feature_cols]
        y_rank = train_df['rank_label']
        y_3d_ret = train_df['fwd_ret_3d'].fillna(0.0) if 'fwd_ret_3d' in train_df.columns else train_df['forward_ret'].fillna(0.0)

        if 'fwd_low_1d' in train_df.columns and 'fwd_low_2d' in train_df.columns:
            min_lock_low = np.minimum(train_df['fwd_low_1d'].fillna(0.0), train_df['fwd_low_2d'].fillna(0.0))
            y_survival = (min_lock_low > -0.035).astype(int)
        else:
            y_survival = (y_3d_ret > -0.035).astype(int)

        # 1. Fit LambdaMART Ranker
        group_counts = train_df.groupby(train_df.index).size().tolist()
        self.ranker.fit(X, y_rank, group=group_counts)

        # 2. Fit 3-Day Momentum Regressor
        self.regressor.fit(X.fillna(0.0), y_3d_ret)

        # 3. Fit T+2.5 Survival Gate Classifier
        self.survival_gate.fit(X.fillna(0.0), y_survival)
        self.is_fitted = True

    def predict_hybrid_scores(self, test_df: pd.DataFrame) -> pd.DataFrame:
        if not self.is_fitted:
            raise ValueError("HybridStackingRanker must be fitted before predict.")
            
        X = test_df[self.feature_cols]
        rank_preds = self.ranker.predict(X)
        mom_preds = self.regressor.predict(X.fillna(0.0))
        surv_probs = self.survival_gate.predict_proba(X.fillna(0.0))[:, 1]

        res_df = test_df[['ticker', 'adtv20_bil']].copy()
        res_df['rank_pred'] = rank_preds
        res_df['mom_pred'] = mom_preds
        res_df['surv_prob'] = surv_probs

        def _norm_group(g):
            r_std = g['rank_pred'].std()
            m_std = g['mom_pred'].std()
            r_z = (g['rank_pred'] - g['rank_pred'].mean()) / (r_std + 1e-8) if r_std > 0 else 0.0
            m_z = (g['mom_pred'] - g['mom_pred'].mean()) / (m_std + 1e-8) if m_std > 0 else 0.0
            
            hybrid_z = 0.65 * r_z + 0.35 * m_z
            penalty_mask = g['surv_prob'] < 0.55
            hybrid_z = np.where(penalty_mask, hybrid_z - 2.0, hybrid_z)

            g['pred_score'] = hybrid_z
            return g

        res_df = res_df.groupby(res_df.index, group_keys=False).apply(_norm_group)
        return res_df

    def save_model(self, model_path: str = "data/models/hybrid_stacking_ranker.pkl"):
        """Save fitted 3-branch models to disk."""
        import joblib
        import os
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        bundle = {
            'ranker': self.ranker,
            'regressor': self.regressor,
            'survival_gate': self.survival_gate,
            'feature_cols': self.feature_cols,
            'is_fitted': self.is_fitted
        }
        joblib.dump(bundle, model_path)
        logger.info(f"HybridStackingRanker saved successfully to {model_path}")

    def load_model(self, model_path: str = "data/models/hybrid_stacking_ranker.pkl") -> bool:
        """Load fitted 3-branch models from disk."""
        import joblib
        import os
        if not os.path.exists(model_path):
            logger.warning(f"Model file not found at {model_path}")
            return False
        try:
            bundle = joblib.load(model_path)
            self.ranker = bundle['ranker']
            self.regressor = bundle['regressor']
            self.survival_gate = bundle['survival_gate']
            self.feature_cols = bundle.get('feature_cols', [])
            self.is_fitted = bundle.get('is_fitted', True)
            logger.info(f"HybridStackingRanker loaded successfully from {model_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load model from {model_path}: {e}")
            return False

hybrid_stacking_ranker = HybridStackingRanker()
# Automatically attempt to load pre-trained model if available
default_path = os.path.join(os.path.dirname(__file__), "../../../../data/models/hybrid_stacking_ranker.pkl")
if os.path.exists(default_path):
    hybrid_stacking_ranker.load_model(default_path)
