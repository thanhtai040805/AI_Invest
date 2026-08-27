"""
Walk-Forward Evaluation of EXP-011: Graph Intelligence & Ecosystem Contagion Alpha Engine.
Evaluates the enhancement of Directed Lead-Lag Graph Shocks on HOSE Top 100 Universe (2020 - 2026).
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
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_universe_data(top_n=100, start_date='2014-01-01', end_date='2026-12-31') -> dict:
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

def prepare_graph_enhanced_dataset(data_dict: dict) -> pd.DataFrame:
    """
    Builds dataset with Graph Contagion & Lead-Lag Alpha features.
    """
    logger.info("1. Generating base technical and fundamental features...")
    base_features_dict = {}
    for ticker, df in data_dict.items():
        if ticker == 'VNINDEX':
            continue
        feats = feature_forge.generate(df, ticker)
        if not feats.empty:
            feats['ticker'] = ticker
            feats['close'] = df['close']
            base_features_dict[ticker] = feats
            
    logger.info("2. Generating Graph Contagion & Lead-Lag Propagation features...")
    graph_dict = graph_engine.extract_graph_contagion_signals(data_dict)
    
    combined_list = []
    for ticker, feats in base_features_dict.items():
        g_feats = graph_dict.get(ticker)
        if g_feats is not None and not g_feats.empty:
            merged = pd.concat([feats, g_feats], axis=1).dropna()
        else:
            merged = feats.dropna()
        combined_list.append(merged)
        
    master_df = pd.concat(combined_list).sort_index()
    
    logger.info("3. Computing forward relative returns and rank relevance labels...")
    master_df = CrossSectionalRanker.compute_forward_alpha_target(master_df, forward_window=5)
    
    return master_df

def run_graph_contagion_evaluation():
    logger.info("=== STARTING EXP-011: GRAPH CONTAGION ALPHA EVALUATION (2020 - 2026) ===")
    
    # 1. Load Data
    data_dict = fetch_universe_data(top_n=100)
    if not data_dict:
        logger.error("Failed to load universe data.")
        return
        
    # 2. Build Dataset
    master_df = prepare_graph_enhanced_dataset(data_dict)
    logger.info(f"Total valid cross-sectional rows: {len(master_df):,}")
    
    exclude_cols = {'ticker', 'close', 'forward_ret', 'alpha_forward_ret', 'rank_label'}
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]
    logger.info(f"Total Cross-Sectional & Graph Features: {len(feature_cols)} features")
    
    # 3. Walk-Forward Expanding Window Evaluation (2020 - 2026)
    test_years = range(2020, 2027)
    
    all_top5_returns = []
    all_median_returns = []
    all_top5_alpha = []
    all_top5_wins = []
    
    yearly_stats = {}
    
    for year in test_years:
        logger.info(f"\n--- Walk-Forward Step: Testing Out-of-Sample Year {year} ---")
        
        train_end_date = pd.to_datetime(f"{year-1}-12-20")
        test_start_date = pd.to_datetime(f"{year}-01-01")
        test_end_date = pd.to_datetime(f"{year}-12-31")
        
        df_train = master_df[master_df.index <= train_end_date].copy()
        df_test = master_df[(master_df.index >= test_start_date) & (master_df.index <= test_end_date)].copy()
        
        if df_test.empty or len(df_train) < 1000:
            continue
            
        df_train_norm = CrossSectionalRanker.cross_sectional_zscore(df_train, feature_cols)
        df_test_norm = CrossSectionalRanker.cross_sectional_zscore(df_test, feature_cols)
        
        ranker = CrossSectionalRanker(n_estimators=100, learning_rate=0.05, max_depth=5)
        ranker.fit(df_train_norm[feature_cols], df_train_norm['rank_label'])
        
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
            year_avg_alpha = np.mean(year_top5_alpha)
            yearly_stats[year] = {"win_rate": year_winrate, "avg_alpha": year_avg_alpha}
            logger.info(f"Year {year} OOS Results: Win Rate = {year_winrate:.2%} | Avg Alpha/Trade = {year_avg_alpha:+.3%}")
            
    # 4. Final Aggregated Metrics Across Entire 2020-2026 Test Window
    print("\n" + "="*80)
    print(" EXP-011 GRAPH CONTAGION & LEAD-LAG ALPHA RESULTS (WALK-FORWARD OOS 2020-2026)")
    print("="*80)
    
    total_trades = len(all_top5_wins)
    overall_winrate = np.mean(all_top5_wins)
    avg_alpha_per_trade = np.mean(all_top5_alpha)
    annualized_alpha = avg_alpha_per_trade * 50
    
    alpha_series = pd.Series(all_top5_alpha)
    alpha_sharpe = (alpha_series.mean() / (alpha_series.std() + 1e-8)) * np.sqrt(50)
    
    print(f"Total Trading Sessions Evaluated  : {total_trades:,}")
    print(f"Top 5 vs Market Median Win Rate   : {overall_winrate:.2%}  (vs EXP-008 Baseline: 62.83%)")
    print(f"Average Excess Alpha per 5-Day    : {avg_alpha_per_trade:+.3%} (vs EXP-008 Baseline: +1.067%)")
    print(f"Annualized Pure Alpha (vs Market) : {annualized_alpha:+.2%} (vs EXP-008 Baseline: +53.34%)")
    print(f"Alpha Information Ratio (Sharpe)  : {alpha_sharpe:.2f}    (vs EXP-008 Baseline: 2.04)")
    print("="*80)
    
    print("\nYEAR-BY-YEAR WALK-FORWARD BREAKDOWN:")
    for y, s in yearly_stats.items():
        print(f"  Year {y}: Win Rate = {s['win_rate']:.2%} | Avg Alpha/5d = {s['avg_alpha']:+.3%}")
    print("="*80 + "\n")
    
    importances = ranker.get_feature_importances()
    print("TOP 15 ALPHA DRIVERS (INCLUDING GRAPH CONTAGION FEATURES):")
    print(importances.head(15).to_string(index=False))
    print("="*80 + "\n")

if __name__ == "__main__":
    run_graph_contagion_evaluation()
