"""
Walk-Forward Cross-Sectional Alpha Evaluation Engine for HOSE.
Evaluates Top Decile / Top 5 Outperformers against Market Median (OOS: 2020 - 2026).
"""

import os
import sys
import logging
import numpy as np
import pandas as pd

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.ecosystem_lead_lag import ecosystem_engine, SECTOR_MAP
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from app.domain.services.ml.regime_kelly_sizer import regime_kelly_sizer
from app.domain.rules.market.hmm_regime_engine import MarketRegimeV2, hmm_engine, HMM_MODEL_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_universe_data(top_n=120, start_date='2014-01-01', end_date='2026-12-31') -> dict:
    """Fetch OHLCV data for top N liquid tickers."""
    logger.info(f"Fetching Top {top_n} liquid tickers from DB...")
    
    query_tickers = f"""
        SELECT ticker, SUM(close_adj * volume_continuous) as total_val
        FROM market_data_daily
        WHERE date >= '2020-01-01'
        GROUP BY ticker
        ORDER BY total_val DESC
        LIMIT {top_n};
    """
    
    try:
        with get_conn() as conn:
            df_tickers = pd.read_sql(query_tickers, conn)
            tickers = df_tickers['ticker'].tolist()
            if 'VNINDEX' not in tickers:
                tickers.append('VNINDEX')
            logger.info(f"Selected Universe of {len(tickers)} tickers.")
            
            query_data = f"""
                SELECT ticker, date, open_adj as open, high_adj as high, low_adj as low, close_adj as close, volume_continuous as volume
                FROM market_data_daily
                WHERE ticker IN ({','.join([f"'{t}'" for t in tickers])})
                AND date >= '{start_date}' AND date <= '{end_date}'
                ORDER BY ticker, date;
            """
            df_data = pd.read_sql(query_data, conn)
    except Exception as e:
        logger.error(f"Database error: {e}")
        return {}
        
    df_data['date'] = pd.to_datetime(df_data['date'])
    
    data_dict = {}
    for ticker in tickers:
        df_ticker = df_data[df_data['ticker'] == ticker].copy()
        if len(df_ticker) > 500:
            df_ticker = df_ticker.set_index('date').sort_index()
            data_dict[ticker] = df_ticker
            
    return data_dict

def prepare_cross_sectional_dataset(data_dict: dict) -> pd.DataFrame:
    """
    Builds the master cross-sectional dataset:
    1. Extracts individual features from FeatureForge.
    2. Extracts ecosystem/sector lead-lag signals.
    3. Merges into a unified multi-ticker DataFrame.
    4. Computes forward relative returns and rank relevance labels.
    """
    logger.info("1. Generating base technical/fundamental features...")
    base_features_dict = {}
    for ticker, df in data_dict.items():
        if ticker == 'VNINDEX':
            continue
        feats = feature_forge.generate(df, ticker)
        if not feats.empty:
            feats['ticker'] = ticker
            feats['close'] = df['close']
            base_features_dict[ticker] = feats
            
    logger.info("2. Generating Sector & Ecosystem Lead-Lag features...")
    lead_lag_dict = ecosystem_engine.extract_cross_sectional_signals(data_dict)
    
    # Merge base features and lead-lag features per ticker
    combined_list = []
    for ticker, feats in base_features_dict.items():
        ll_feats = lead_lag_dict.get(ticker)
        if ll_feats is not None and not ll_feats.empty:
            merged = pd.concat([feats, ll_feats], axis=1).dropna()
        else:
            merged = feats.dropna()
        combined_list.append(merged)
        
    master_df = pd.concat(combined_list).sort_index()
    
    logger.info("3. Computing forward relative returns and relevance targets...")
    master_df = CrossSectionalRanker.compute_forward_alpha_target(master_df, forward_window=5)
    
    return master_df

def run_cross_sectional_evaluation():
    logger.info("=== STARTING CROSS-SECTIONAL RANKING EVALUATION (EXP-008) ===")
    
    # 1. Load Data
    data_dict = fetch_universe_data(top_n=100)
    if not data_dict:
        logger.error("Failed to load universe data.")
        return
        
    # 2. Build Dataset
    master_df = prepare_cross_sectional_dataset(data_dict)
    logger.info(f"Total valid cross-sectional rows: {len(master_df):,}")
    
    # Identify feature columns (exclude meta / target columns)
    exclude_cols = {'ticker', 'close', 'forward_ret', 'alpha_forward_ret', 'rank_label'}
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]
    logger.info(f"Total Cross-Sectional Features: {len(feature_cols)} features")
    
    # 3. Walk-Forward Expanding Window Evaluation (2020 - 2026)
    test_years = range(2020, 2027)
    
    all_top5_returns = []
    all_median_returns = []
    all_top5_alpha = []
    all_top5_wins = [] # 1 if Top 5 average > Market Median, else 0
    
    for year in test_years:
        logger.info(f"\n--- Walk-Forward Step: Testing Out-of-Sample Year {year} ---")
        
        # 10 days embargo to prevent overlap leakage with 5d forward returns
        train_end_date = pd.to_datetime(f"{year-1}-12-20")
        test_start_date = pd.to_datetime(f"{year}-01-01")
        test_end_date = pd.to_datetime(f"{year}-12-31")
        
        df_train = master_df[master_df.index <= train_end_date].copy()
        df_test = master_df[(master_df.index >= test_start_date) & (master_df.index <= test_end_date)].copy()
        
        if df_test.empty or len(df_train) < 1000:
            continue
            
        # Cross-sectional Z-score normalization (Done strictly per slice)
        df_train_norm = CrossSectionalRanker.cross_sectional_zscore(df_train, feature_cols)
        df_test_norm = CrossSectionalRanker.cross_sectional_zscore(df_test, feature_cols)
        
        # Train LambdaMART ranker
        ranker = CrossSectionalRanker(n_estimators=100, learning_rate=0.05, max_depth=5)
        ranker.fit(df_train_norm[feature_cols], df_train_norm['rank_label'])
        
        # Test day-by-day in test year
        test_dates = df_test_norm.index.unique().sort_values()
        year_top5_alpha = []
        year_top5_wins = []
        
        for d in test_dates:
            slice_d = df_test_norm.loc[[d]]
            if len(slice_d) < 10:
                continue
                
            scores = ranker.predict_rank_scores(slice_d[feature_cols])
            slice_d_eval = slice_d[['ticker', 'forward_ret', 'alpha_forward_ret']].copy()
            slice_d_eval['score'] = scores.values
            
            # Select Top 5 highest ranked stocks
            top5 = slice_d_eval.sort_values(by='score', ascending=False).head(5)
            
            avg_top5_ret = top5['forward_ret'].mean()
            median_market_ret = slice_d_eval['forward_ret'].median()
            alpha = avg_top5_ret - median_market_ret
            
            is_win = 1 if alpha > 0 else 0
            
            all_top5_returns.append(avg_top5_ret)
            all_median_returns.append(median_market_ret)
            all_top5_alpha.append(alpha)
            all_top5_wins.append(is_win)
            
            year_top5_alpha.append(alpha)
            year_top5_wins.append(is_win)
            
        if year_top5_wins:
            year_winrate = np.mean(year_top5_wins)
            year_cum_alpha = np.mean(year_top5_alpha) * len(year_top5_alpha)
            logger.info(f"Year {year} OOS Results: Winrate vs Market = {year_winrate:.2%} | Avg Alpha/Trade = {np.mean(year_top5_alpha):+.3%} | Cumulative 5d Alpha = {year_cum_alpha:+.2%}")
            
    # 4. Final Aggregated Metrics Across Entire 2020-2026 Test Window
    print("\n" + "="*70)
    print(" EXP-008 CROSS-SECTIONAL RANKING RESULTS (WALK-FORWARD OOS 2020-2026)")
    print("="*70)
    
    total_trades = len(all_top5_wins)
    overall_winrate = np.mean(all_top5_wins)
    avg_alpha_per_trade = np.mean(all_top5_alpha)
    annualized_alpha = avg_alpha_per_trade * 50 # Approx 50 independent 5-day periods/year
    
    # Calculate Sharpe of Alpha
    alpha_series = pd.Series(all_top5_alpha)
    alpha_sharpe = (alpha_series.mean() / (alpha_series.std() + 1e-8)) * np.sqrt(50)
    
    print(f"Total Trading Sessions Evaluated: {total_trades:,}")
    print(f"Top 5 vs Market Median Win Rate : {overall_winrate:.2%}")
    print(f"Average Excess Alpha per 5-Day  : {avg_alpha_per_trade:+.3%}")
    print(f"Annualized Pure Alpha (vs Beta) : {annualized_alpha:+.2%}")
    print(f"Alpha Information Ratio (Sharpe): {alpha_sharpe:.2f}")
    print("="*70 + "\n")
    
    # Print Top 10 Most Important Features
    importances = ranker.get_feature_importances()
    print("TOP 10 CROSS-SECTIONAL ALPHA DRIVERS:")
    print(importances.head(10).to_string(index=False))
    print("="*70 + "\n")

if __name__ == "__main__":
    run_cross_sectional_evaluation()
