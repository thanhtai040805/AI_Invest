"""
Cross-Sectional Alpha Ranking Engine for HOSE.
Transforms tabular feature space into relative Cross-Sectional Z-Scores 
and optimizes Top Decile (Top 5 Alpha Outperformers) via LambdaMART / LightGBM Ranker.
"""

import os
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
import lightgbm as lgb
from sklearn.metrics import ndcg_score

logger = logging.getLogger(__name__)

class CrossSectionalRanker:
    """
    WorldQuant / Two Sigma-style Cross-Sectional Ranking Engine.
    Evaluates all stocks relative to each other on each date t to isolate pure Alpha.
    """
    
    def __init__(self, n_estimators: int = 150, learning_rate: float = 0.05, max_depth: int = 5):
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.model = lgb.LGBMRanker(
            objective='lambdarank',
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            random_state=42,
            n_jobs=4,
            importance_type='gain'
        )
        self.feature_names: List[str] = []
        self.is_fitted = False

    @staticmethod
    def cross_sectional_zscore(df_all: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
        """
        Standardizes features cross-sectionally per trading date:
        Z_{i, t} = (F_{i, t} - Mean_t(F)) / (Std_t(F) + eps)
        Removes market-wide macro drift and isolates relative stock differences.
        """
        df_ranked = df_all.copy()
        
        # Group by Date index
        def _zscore_date_group(group: pd.DataFrame):
            for col in feature_cols:
                std = group[col].std()
                if std > 1e-8:
                    group[col] = (group[col] - group[col].mean()) / std
                else:
                    group[col] = 0.0
            return group

        # Apply strictly per date
        df_ranked = df_ranked.groupby(level=0, group_keys=False).apply(_zscore_date_group)
        return df_ranked

    @staticmethod
    def compute_forward_alpha_target(df_all: pd.DataFrame, forward_window: int = 5) -> pd.DataFrame:
        """
        Computes forward relative return vs cross-sectional median:
        Alpha_Return = Stock_Forward_Ret - Market_Median_Forward_Ret
        Maps into 5 discrete relevance tiers (0 to 4) for LambdaMART ranking.
        """
        df_res = df_all.copy()
        
        # For each ticker, compute forward window return
        if 'forward_ret' not in df_res.columns:
            # Assumes df has 'close' and is multi-indexed or grouped by ticker
            df_res['forward_ret'] = df_res.groupby('ticker')['close'].transform(
                lambda x: x.pct_change(forward_window).shift(-forward_window)
            )
            
        # Drop rows where forward return cannot be calculated (end of sample)
        df_res = df_res.dropna(subset=['forward_ret'])
        
        # Compute cross-sectional median forward return per date
        date_medians = df_res.groupby(level=0)['forward_ret'].transform('median')
        df_res['alpha_forward_ret'] = df_res['forward_ret'] - date_medians
        
        # Quantize into 5 relevance grades per date:
        # Grade 4 = Top 20% (Strong Outperform)
        # Grade 3 = Top 20-40%
        # Grade 2 = Middle 40-60%
        # Grade 1 = Bottom 20-40%
        # Grade 0 = Bottom 20% (Underperform)
        def _quantize_relevance(group: pd.DataFrame):
            try:
                group['rank_label'] = pd.qcut(
                    group['alpha_forward_ret'], 
                    q=5, 
                    labels=[0, 1, 2, 3, 4], 
                    duplicates='drop'
                ).astype(int)
            except Exception:
                group['rank_label'] = 2
            return group

        df_res = df_res.groupby(level=0, group_keys=False).apply(_quantize_relevance)
        return df_res

    def fit(self, X: pd.DataFrame, y: pd.Series):
        """
        Trains the LambdaMART ranker using group query lengths (number of stocks per date).
        """
        # Count number of stocks for each trading date in chronological order
        date_counts = X.groupby(level=0).size().values
        self.feature_names = X.columns.tolist()
        
        logger.info(f"Training LambdaMART Ranker on {len(X)} samples across {len(date_counts)} dates...")
        self.model.fit(
            X.values, 
            y.values, 
            group=date_counts,
            eval_metric=['ndcg@5', 'ndcg@10']
        )
        self.is_fitted = True
        logger.info("LambdaMART Ranker training complete.")

    def predict_rank_scores(self, X: pd.DataFrame) -> pd.Series:
        """
        Generates relative cross-sectional ranking scores for candidate stocks.
        Higher score = Higher probability of outperforming the cross-sectional median.
        """
        if not self.is_fitted:
            raise RuntimeError("Ranker model must be fitted before predict.")
        scores = self.model.predict(X[self.feature_names].values)
        return pd.Series(scores, index=X.index)

    def select_top_k(self, X_date: pd.DataFrame, top_k: int = 5) -> List[str]:
        """
        Given the universe feature matrix on a single date, returns the Top K ticker symbols.
        """
        scores = self.predict_rank_scores(X_date)
        # Assuming X_date has a 'ticker' column
        if 'ticker' in X_date.columns:
            scores_df = pd.DataFrame({'ticker': X_date['ticker'].values, 'score': scores.values})
            top_tickers = scores_df.sort_values(by='score', ascending=False).head(top_k)['ticker'].tolist()
            return top_tickers
        return []

    def get_feature_importances(self) -> pd.DataFrame:
        """Returns gain-based feature importances."""
        if not self.is_fitted:
            return pd.DataFrame()
        return pd.DataFrame({
            'feature': self.feature_names,
            'importance_gain': self.model.feature_importances_
        }).sort_values(by='importance_gain', ascending=False)


cross_sectional_ranker = CrossSectionalRanker()
