"""
Quantitative Verification: Two-Stage Quality Funnel & Conformal Sniper on Expanded N=150 Universe.
Compares Naive N=150 Trading vs Two-Stage Funnel + Conformal Sniper Gate (2020 - 2026).
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
from app.domain.services.ml.conformal_selective_engine import conformal_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_expanded_universe_data(top_n=150, start_date='2014-01-01', end_date='2026-12-31') -> dict:
    """Fetch OHLCV data for top N=150 liquid tickers."""
    logger.info(f"Fetching Expanded Top {top_n} liquid tickers from DB...")
    
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
            logger.info(f"Selected Expanded Universe of {len(tickers)} tickers.")
            
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
        if len(df_ticker) > 300:
            df_ticker = df_ticker.set_index('date').sort_index()
            data_dict[ticker] = df_ticker
            
    return data_dict

def run_funnel_sniper_experiment():
    logger.info("=== STARTING EXPERIMENT ON EXPANDED UNIVERSE N=150 ===")
    
    # 1. Fetch Data
    data_dict = fetch_expanded_universe_data(top_n=150)
    if not data_dict:
        return
        
    # 2. Build Dataset
    logger.info("Generating features for N=150...")
    base_features_dict = {}
    for ticker, df in data_dict.items():
        if ticker == 'VNINDEX':
            continue
        feats = feature_forge.generate(df, ticker)
        if not feats.empty:
            feats['ticker'] = ticker
            feats['close'] = df['close']
            # Calculate ADTV20 in billions VND for Stage 1 Quality Funnel (close is in thousands VND)
            val_20d = (df['close'] * df['volume']).rolling(20, min_periods=5).mean() / 1e6
            feats['adtv20_bil'] = val_20d
            base_features_dict[ticker] = feats
            
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
    master_df = CrossSectionalRanker.compute_forward_alpha_target(master_df, forward_window=5)
    
    exclude_cols = {'ticker', 'close', 'forward_ret', 'alpha_forward_ret', 'rank_label', 'adtv20_bil'}
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]
    
    # 3. Walk-Forward Expanding Window Evaluation (2020 - 2026)
    test_years = range(2020, 2027)
    
    # Policies to compare:
    # 1. Naive N=150 (No Funnel, No Conformal Gate - Trade Every Day)
    # 2. Stage 1 Quality Funnel Only (ADTV20 >= 10 Bil VND - Trade Every Day)
    # 3. Two-Stage Funnel + High Conviction Conformal Sniper (Z >= 2.85 sigma)
    
    p1_wins, p1_alpha = [], []
    p2_wins, p2_alpha = [], []
    p3_wins, p3_alpha = [], []
    
    for year in test_years:
        logger.info(f"\n--- Testing Year {year} on Expanded N=150 Universe ---")
        
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
        
        for d in test_dates:
            slice_d = df_test_norm.loc[[d]]
            if len(slice_d) < 20:
                continue
                
            scores = ranker.predict_rank_scores(slice_d[feature_cols])
            eval_slice = slice_d[['ticker', 'forward_ret', 'alpha_forward_ret', 'adtv20_bil']].copy()
            eval_slice['score'] = scores.values
            
            market_median = eval_slice['forward_ret'].median()
            
            # --- POLICY 1: Naive N=150 ---
            top5_p1 = eval_slice.sort_values(by='score', ascending=False).head(5)
            alpha_p1 = top5_p1['forward_ret'].mean() - market_median
            p1_wins.append(1 if alpha_p1 > 0 else 0)
            p1_alpha.append(alpha_p1)
            
            # --- POLICY 2: Stage 1 Quality Funnel (Filter ADTV20 >= 10 Bil) ---
            funnel_slice = eval_slice[eval_slice['adtv20_bil'] >= 10.0]
            if len(funnel_slice) >= 5:
                top5_p2 = funnel_slice.sort_values(by='score', ascending=False).head(5)
                alpha_p2 = top5_p2['forward_ret'].mean() - market_median
                p2_wins.append(1 if alpha_p2 > 0 else 0)
                p2_alpha.append(alpha_p2)
                
            # --- POLICY 3: Two-Stage Funnel + Conformal Sniper (Z >= 2.85 sigma) ---
            if len(funnel_slice) >= 10:
                scores_arr = funnel_slice['score'].values
                mean_s = np.mean(scores_arr)
                std_s = np.std(scores_arr) + 1e-8
                top1_s = np.max(scores_arr)
                z_score = (top1_s - mean_s) / std_s
                if z_score >= 2.85:
                    top5_p3 = funnel_slice.sort_values(by='score', ascending=False).head(5)
                    alpha_p3 = top5_p3['forward_ret'].mean() - market_median
                    p3_wins.append(1 if alpha_p3 > 0 else 0)
                    p3_alpha.append(alpha_p3)
                    
    # 4. Print Summary Table
    print("\n" + "="*85)
    print(" EXPANDED UNIVERSE N=150 WALK-FORWARD RESULTS (2020 - 2026)")
    print("="*85)
    print(f"{'Strategy Architecture':<45} | {'Trades':<8} | {'Win Rate':<10} | {'Avg 5d Alpha':<12} | {'Ann. Alpha':<10}")
    print("-" * 85)
    
    print(f"{'1. Naive N=150 (No Filter, Trade All)':<45} | {len(p1_wins):<8} | {np.mean(p1_wins):.2%}    | {np.mean(p1_alpha):+.3%}      | {np.mean(p1_alpha)*50:+.2%}")
    print(f"{'2. Stage 1 Quality Funnel (ADTV >= 10 Bil)':<45} | {len(p2_wins):<8} | {np.mean(p2_wins):.2%}    | {np.mean(p2_alpha):+.3%}      | {np.mean(p2_alpha)*50:+.2%}")
    print(f"{'3. Two-Stage Funnel + Sniper (Z >= 2.85 sigma)':<45} | {len(p3_wins):<8} | {np.mean(p3_wins):.2%}    | {np.mean(p3_alpha):+.3%}      | {np.mean(p3_alpha)*50:+.2%}")
    print("="*85 + "\n")

if __name__ == "__main__":
    run_funnel_sniper_experiment()
