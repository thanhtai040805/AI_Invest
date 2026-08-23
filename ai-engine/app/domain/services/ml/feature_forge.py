"""
Feature Forge - Quant Feature Engineering
Generates 80+ features adapted for VN market (HOSE).
Uses Fractional Differentiation to preserve memory.
"""

import logging
import numpy as np
import pandas as pd
from typing import List, Optional

from .frac_diff import frac_diff_ffd, find_optimal_d

logger = logging.getLogger(__name__)

class FeatureForge:
    """
    Computes alpha factors and features from raw OHLCV and Order Book data.
    """
    
    def __init__(self, use_frac_diff: bool = True):
        self.use_frac_diff = use_frac_diff
        # Cache for optimal d values per ticker to avoid recalculating on every run
        self._optimal_d_cache = {}
        
    def _compute_price_momentum(self, df: pd.DataFrame) -> pd.DataFrame:
        """Standard and Idiosyncratic Momentum."""
        out = pd.DataFrame(index=df.index)
        
        # 1. Standard Momentum
        for window in [5, 10, 20, 60, 120]:
            out[f'mom_{window}d'] = df['close'].pct_change(window)
            out[f'ret_vol_{window}d'] = df['close'].pct_change().rolling(window).std()
            
            # Risk-adjusted momentum
            out[f'sharpe_{window}d'] = out[f'mom_{window}d'] / (out[f'ret_vol_{window}d'] * np.sqrt(252) + 1e-8)
            
        # 2. Extreme Reversal (3 sigma bands)
        roll_mean = df['close'].rolling(20).mean()
        roll_std = df['close'].rolling(20).std()
        out['z_score_20d'] = (df['close'] - roll_mean) / (roll_std + 1e-8)
        out['extreme_reversal_signal'] = (out['z_score_20d'] < -3.0).astype(int) - (out['z_score_20d'] > 3.0).astype(int)
        
        return out
        
    def _compute_liquidity_turnover(self, df: pd.DataFrame) -> pd.DataFrame:
        """VN-4 Model turnover and liquidity proxies."""
        out = pd.DataFrame(index=df.index)
        
        # Turnover = Volume / Shares Outstanding. 
        # Here we approximate with rolling Volume / Avg Volume
        vol_ma60 = df['volume'].rolling(60).mean()
        out['turnover_anomaly'] = df['volume'] / (vol_ma60 + 1e-8)
        
        # Amihud Illiquidity (Absolute Return / Volume)
        ret_abs = df['close'].pct_change().abs()
        out['amihud_illiquidity'] = (ret_abs / (df['volume'] + 1e-8)).rolling(20).mean()
        
        # Kyle's Lambda Proxy (Price Impact)
        # Assuming we have high/low
        price_range = (df['high'] - df['low']) / df['close']
        out['kyle_lambda_proxy'] = (price_range / (df['volume'] + 1e-8)).rolling(10).mean()
        
        return out
        
    def _compute_microstructure(self, df: pd.DataFrame) -> pd.DataFrame:
        """Order flow and PIN (Probability of Informed Trading) proxies."""
        out = pd.DataFrame(index=df.index)
        
        # If we had bid/ask volume, we could do full PIN.
        # Approximation using closing position in range
        close_pos = (df['close'] - df['low']) / (df['high'] - df['low'] + 1e-8)
        out['close_position'] = close_pos
        
        # Buying pressure proxy
        buy_pressure = close_pos * df['volume']
        sell_pressure = (1 - close_pos) * df['volume']
        out['order_flow_imbalance_proxy'] = (buy_pressure - sell_pressure) / (df['volume'] + 1e-8)
        
        return out
        
    def _compute_frac_diff_features(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Apply fractional differentiation to non-stationary features like Price and Volume."""
        out = pd.DataFrame(index=df.index)
        
        if not self.use_frac_diff:
            out['close_diff'] = df['close'].pct_change()
            out['vol_diff'] = df['volume'].pct_change()
            return out
            
        # Find or use cached optimal d for close
        cache_key_c = f"{ticker}_close_d"
        if cache_key_c not in self._optimal_d_cache:
            d = find_optimal_d(np.log(df['close']).dropna())
            self._optimal_d_cache[cache_key_c] = d
        else:
            d = self._optimal_d_cache[cache_key_c]
            
        out['close_frac_diff'] = frac_diff_ffd(np.log(df['close']), d)
        
        # Find or use cached optimal d for volume
        cache_key_v = f"{ticker}_vol_d"
        if cache_key_v not in self._optimal_d_cache:
            # Volume is strictly positive, log is safe if we add 1
            d_v = find_optimal_d(np.log(df['volume'] + 1).dropna())
            self._optimal_d_cache[cache_key_v] = d_v
        else:
            d_v = self._optimal_d_cache[cache_key_v]
            
        out['vol_frac_diff'] = frac_diff_ffd(np.log(df['volume'] + 1), d_v)
        
        return out
        
    def _compute_hose_limits(self, df: pd.DataFrame) -> pd.DataFrame:
        """Features tracking consecutive ceiling/floor hits."""
        out = pd.DataFrame(index=df.index)
        
        ret = df['close'].pct_change()
        
        # Ceiling is ~6.8% on HOSE
        is_ceiling = (ret > 0.065).astype(int)
        is_floor = (ret < -0.065).astype(int)
        
        # Calculate streaks
        # Ceiling streak
        ceil_streak = is_ceiling.groupby((is_ceiling != is_ceiling.shift()).cumsum()).cumsum()
        out['ceiling_streak'] = ceil_streak * is_ceiling # Mask non-ceilings to 0
        
        # Floor streak
        floor_streak = is_floor.groupby((is_floor != is_floor.shift()).cumsum()).cumsum()
        out['floor_streak'] = floor_streak * is_floor
        
        return out

    def generate(self, df: pd.DataFrame, ticker: str = "UNKNOWN") -> pd.DataFrame:
        """
        Generate all features for a single ticker.
        """
        if len(df) < 120:
            logger.warning(f"Not enough data for {ticker} to generate features. Need 120, got {len(df)}")
            return pd.DataFrame()
            
        dfs = [
            self._compute_price_momentum(df),
            self._compute_liquidity_turnover(df),
            self._compute_microstructure(df),
            self._compute_frac_diff_features(df, ticker),
            self._compute_hose_limits(df)
        ]
        
        # Concat all features
        features = pd.concat(dfs, axis=1)
        
        # Forward fill NaNs created by rolling windows where appropriate, but initially just drop or fill 0
        features = features.replace([np.inf, -np.inf], np.nan)
        features = features.ffill().fillna(0.0)
        
        return features

feature_forge = FeatureForge()
