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
        # Cache for VN-Index data
        self._vnindex_df = None
        
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
        out['extreme_reversal_signal'] = (out['z_score_20d'] < -2.0).astype(int) - (out['z_score_20d'] > 2.0).astype(int)
        
        # 3. MACD
        ema_12 = df['close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['close'].ewm(span=26, adjust=False).mean()
        out['macd'] = ema_12 - ema_26
        out['macd_signal'] = out['macd'].ewm(span=9, adjust=False).mean()
        out['macd_hist'] = out['macd'] - out['macd_signal']
        
        # 4. RSI (14d)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-8)
        out['rsi_14d'] = 100 - (100 / (1 + rs))
        
        return out
        
    def _compute_liquidity_turnover(self, df: pd.DataFrame) -> pd.DataFrame:
        """VN-4 Model turnover and liquidity proxies."""
        out = pd.DataFrame(index=df.index)
        
        # Turnover = Volume / Shares Outstanding. 
        # Here we approximate with rolling Volume / Avg Volume
        vol_ma60 = df['volume'].rolling(60).mean()
        out['turnover_anomaly'] = df['volume'] / (vol_ma60 + 1e-8)
        
        # Volume Profile Proxy (VWAP Anomaly)
        # Using Typical Price = (High + Low + Close) / 3
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap_20d = (typical_price * df['volume']).rolling(20).sum() / (df['volume'].rolling(20).sum() + 1e-8)
        out['price_to_vwap_20d'] = df['close'] / (vwap_20d + 1e-8)
        
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
        """
        Features tracking consecutive ceiling/floor hits on HOSE.
        HOSE price band is ±7% from previous closing price, rounded to tick size.
        Post-KRX tick sizes:
          - < 10,000 VND: 10 VND
          - 10,000 - 49,950 VND: 50 VND
          - >= 50,000 VND: 100 VND
        """
        out = pd.DataFrame(index=df.index)
        
        prev_close = df['close'].shift(1)
        ret = df['close'].pct_change()
        
        # Use percentage thresholds for ceiling/floor because adjusted prices
        # invalidate the exact absolute price tick calculations.
        # HOSE is 7%, HNX is 10%, UPCOM is 15%. We use 6.8% to catch rounding.
        is_ceiling = (ret >= 0.068).astype(int)
        is_floor = (ret <= -0.068).astype(int)
        
        # Calculate streaks
        ceil_streak = is_ceiling.groupby((is_ceiling != is_ceiling.shift()).cumsum()).cumsum()
        out['ceiling_streak'] = ceil_streak * is_ceiling  # Mask non-ceilings to 0
        
        floor_streak = is_floor.groupby((is_floor != is_floor.shift()).cumsum()).cumsum()
        out['floor_streak'] = floor_streak * is_floor
        
        return out

    def _compute_fundamental_flow(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Fetch and inject Foreign Flow, Insider Trades, and Financial Ratios."""
        import psycopg2
        from app.infrastructure.database.pg_pool import DB_URL
        
        out = pd.DataFrame(index=df.index)
        if ticker == "UNKNOWN":
            return out
            
        try:
            conn = psycopg2.connect(DB_URL)
            
            # 1. Foreign Flow
            q_ff = f"SELECT trade_date as date, net_volume FROM foreign_flow WHERE symbol = '{ticker}'"
            ff_df = pd.read_sql(q_ff, conn)
            if not ff_df.empty:
                ff_df['date'] = pd.to_datetime(ff_df['date'])
                ff_df = ff_df.set_index('date').sort_index()
                # Join and calculate rolling ratio
                temp = df[['volume']].join(ff_df, how='left').fillna(0)
                net_vol_20d = temp['net_volume'].rolling(20, min_periods=1).sum()
                total_vol_20d = temp['volume'].rolling(20, min_periods=1).sum()
                out['foreign_flow_ratio_20d'] = (net_vol_20d / (total_vol_20d + 1e-8)).fillna(0)
            else:
                out['foreign_flow_ratio_20d'] = 0.0

            # 2. Insider Trades (Net shares bought in last 90 days)
            q_in = f"SELECT trade_date as date, trade_type, quantity FROM insider_trades WHERE symbol = '{ticker}'"
            in_df = pd.read_sql(q_in, conn)
            if not in_df.empty:
                in_df['date'] = pd.to_datetime(in_df['date'])
                # MUA or BUY
                in_df['net_qty'] = in_df.apply(lambda row: row['quantity'] if str(row['trade_type']).upper() in ['MUA', 'BUY'] else -row['quantity'], axis=1)
                daily_in = in_df.groupby('date')['net_qty'].sum()
                temp_in = pd.DataFrame(index=df.index).join(daily_in, how='left').fillna(0)
                net_90d = temp_in['net_qty'].rolling(90, min_periods=1).sum()
                vol_90d = df['volume'].rolling(90, min_periods=1).sum()
                out['insider_net_90d'] = (net_90d / (vol_90d + 1e-8)).fillna(0)
                # Sign function to make it a clear signal (-1, 0, 1)
                out['insider_signal'] = np.sign(net_90d)
            else:
                out['insider_net_90d'] = 0.0
                out['insider_signal'] = 0.0

            # 3. Financial Ratios
            q_fin = f"SELECT published_date as date, pe, pb, roe FROM financial_ratios WHERE symbol = '{ticker}' AND published_date IS NOT NULL"
            fin_df = pd.read_sql(q_fin, conn)
            if not fin_df.empty:
                fin_df['date'] = pd.to_datetime(fin_df['date'])
                fin_df = fin_df.set_index('date').sort_index()
                # Merge ASOF with aligned datetime types
                left_df = pd.DataFrame({'date': pd.to_datetime(df.index)}).sort_values('date')
                merged = pd.merge_asof(
                    left_df, 
                    fin_df.reset_index(), 
                    on='date', 
                    direction='backward'
                ).set_index('date')
                merged.index = df.index
                out['pe'] = merged['pe']
                out['pb'] = merged['pb']
                out['roe'] = merged['roe']
            else:
                out['pe'] = np.nan
                out['pb'] = np.nan
                out['roe'] = np.nan
                
            conn.close()
        except Exception as e:
            logger.error(f"Error fetching fundamentals for {ticker}: {e}")
            out['foreign_flow_ratio_20d'] = 0.0
            out['insider_net_90d'] = 0.0
            out['insider_signal'] = 0.0
            out['pe'] = np.nan
            out['pb'] = np.nan
            out['roe'] = np.nan

        return out
        
    def _compute_relative_strength(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Calculate RS and Correlation against VN-Index"""
        import psycopg2
        from app.infrastructure.database.pg_pool import DB_URL
        
        out = pd.DataFrame(index=df.index)
        
        if self._vnindex_df is None:
            try:
                conn = psycopg2.connect(DB_URL)
                q_vn = "SELECT date, close_adj FROM market_data_daily WHERE ticker='VNINDEX'"
                vn_df = pd.read_sql(q_vn, conn)
                conn.close()
                if not vn_df.empty:
                    vn_df['date'] = pd.to_datetime(vn_df['date'])
                    self._vnindex_df = vn_df.set_index('date').sort_index()
            except Exception as e:
                logger.error(f"Failed to fetch VNINDEX for relative strength: {e}")
                self._vnindex_df = pd.DataFrame()
                
        if self._vnindex_df is not None and not self._vnindex_df.empty:
            # Join VNINDEX close
            temp = df[['close']].join(self._vnindex_df['close_adj'].rename('vn_close'), how='left').ffill()
            ticker_ret = temp['close'].pct_change()
            vn_ret = temp['vn_close'].pct_change()
            
            # RS: (1 + ticker_ret) / (1 + vn_ret) over N days
            for window in [10, 20, 60]:
                ticker_cum = (1 + ticker_ret).rolling(window).apply(np.prod, raw=True) - 1
                vn_cum = (1 + vn_ret).rolling(window).apply(np.prod, raw=True) - 1
                out[f'rs_vnindex_{window}d'] = ticker_cum - vn_cum
                
                # Correlation
                out[f'corr_vnindex_{window}d'] = ticker_ret.rolling(window).corr(vn_ret)
        else:
            for window in [10, 20, 60]:
                out[f'rs_vnindex_{window}d'] = 0.0
                out[f'corr_vnindex_{window}d'] = 0.0
                
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
            self._compute_hose_limits(df),
            self._compute_fundamental_flow(df, ticker),
            self._compute_relative_strength(df, ticker)
        ]
        
        # Concat all features
        features = pd.concat(dfs, axis=1)
        
        # Forward fill NaNs created by rolling windows, but keep fundamental NaNs instead of zero-filling
        features = features.replace([np.inf, -np.inf], np.nan)
        features = features.ffill()
        
        return features

feature_forge = FeatureForge()
