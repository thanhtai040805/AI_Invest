"""
Walk-Forward Conformal Selective & Devil's Advocate Evaluator for HOSE.
Evaluates High-Conviction Selective Alpha across 2020 - 2026 Walk-Forward Test Windows.
Optimized 1-Pass Walk-Forward with Instant Threshold Sweeping.
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
from app.domain.services.ml.ecosystem_lead_lag import ecosystem_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from app.domain.services.ml.conformal_selective_engine import ConformalSelectiveEngine
from app.domain.services.ml.devils_advocate_gate import devils_advocate_gate
from app.domain.rules.market.hmm_regime_engine import MarketRegimeV2, hmm_engine, HMM_MODEL_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_universe_data(top_n=100, start_date='2014-01-01', end_date='2026-12-31') -> dict:
    """Fetch OHLCV data for top N liquid tickers."""
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

def prepare_dataset(data_dict: dict) -> pd.DataFrame:
    base_features_dict = {}
    for ticker, df in data_dict.items():
        if ticker == 'VNINDEX':
            continue
        feats = feature_forge.generate(df, ticker)
        if not feats.empty:
            feats['ticker'] = ticker
            feats['close'] = df['close']
            base_features_dict[ticker] = feats
            
    lead_lag_dict = ecosystem_engine.extract_cross_sectional_signals(data_dict)
    
    combined_list = []
    for ticker, feats in base_features_dict.items():
        ll_feats = lead_lag_dict.get(ticker)
        if ll_feats is not None and not ll_feats.empty:
            merged = pd.concat([feats, ll_feats], axis=1).dropna()
        else:
            merged = feats.dropna()
        combined_list.append(merged)
        
    master_df = pd.concat(combined_list).sort_index()
    master_df = CrossSectionalRanker.compute_forward_alpha_target(master_df, forward_window=5)
    return master_df

def run_conformal_selective_evaluation():
    logger.info("=== STARTING CONFORMAL SELECTIVE & DEVIL'S ADVOCATE EVALUATION (EXP-009) ===")
    
    data_dict = fetch_universe_data(top_n=100)
    if not data_dict:
        return
        
    master_df = prepare_dataset(data_dict)
    exclude_cols = {'ticker', 'close', 'forward_ret', 'alpha_forward_ret', 'rank_label'}
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]
    
    # Pre-calculate Regimes using VNINDEX
    vnindex_df = data_dict.get('VNINDEX')
    regime_probs_df = pd.DataFrame()
    if vnindex_df is not None and not vnindex_df.empty:
        hmm_engine.load(HMM_MODEL_PATH)
        if hmm_engine.is_trained:
            X_vn = hmm_engine._extract_features(vnindex_df)
            hmm_probs = hmm_engine.model.predict_proba(X_vn)
            regime_probs_df = pd.DataFrame(index=vnindex_df.index)
            for i in range(hmm_engine.n_components):
                reg_name = hmm_engine.state_map.get(i, MarketRegimeV2.RANGE_BOUND)
                regime_probs_df[reg_name] = hmm_probs[:, i]
            regime_probs_df = regime_probs_df.fillna(0.33)

    # 1-PASS WALK-FORWARD GENERATION
    test_years = range(2020, 2027)
    
    # Store daily session records: (date, slice_df, scores, regime_bear_prob)
    all_sessions_data = []
    
    for year in test_years:
        logger.info(f"--- Walk-Forward Step: Training fold up to year {year-1}, testing {year} ---")
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
            slice_d = df_test_norm.loc[[d]].copy()
            if len(slice_d) < 10:
                continue
                
            scores = ranker.predict_rank_scores(slice_d[feature_cols])
            
            bear_prob = 0.0
            if not regime_probs_df.empty and d in regime_probs_df.index:
                bear_prob = regime_probs_df.loc[d].get(MarketRegimeV2.BEAR_MARKET, 0.0)
                
            all_sessions_data.append({
                'date': d,
                'year': year,
                'slice_d': slice_d,
                'scores': scores,
                'bear_prob': bear_prob
            })
            
    total_sessions_count = len(all_sessions_data)
    logger.info(f"Generated {total_sessions_count} out-of-sample trading sessions across 2020-2026.")
    
    # 2. INSTANT THRESHOLD SWEEP
    print("\n" + "="*85)
    print(" EXP-009 CONFORMAL SELECTIVE TRADING SWEEP (OOS: 2020 - 2026)")
    print("="*85)
    print(f"{'Alpha Z-Thresh':<14} | {'Active Trades':<13} | {'Abstain %':<10} | {'Win Rate vs Mkt':<16} | {'Avg 5d Alpha':<12} | {'Annualized Alpha':<16}")
    print("-" * 85)
    
    best_wr = 0.0
    
    for z_thresh in [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4]:
        conformal_gate = ConformalSelectiveEngine(min_alpha_zscore=z_thresh, min_score_dispersion=0.30)
        
        all_alpha = []
        all_wins = []
        active_sessions = 0
        
        for sess in all_sessions_data:
            scores = sess['scores']
            slice_d = sess['slice_d']
            bear_prob = sess['bear_prob']
            
            # Step 1: Conformal Confidence Gate
            should_trade, conf_z, reason, top_indices = conformal_gate.evaluate_session_conviction(scores, top_k=5)
            if not should_trade:
                continue # ABSTAIN
                
            # Step 2: Devil's Advocate Risk Check
            top_candidates_df = slice_d.iloc[top_indices].copy()
            approved_candidates = devils_advocate_gate.filter_candidates(top_candidates_df, regime_bear_prob=bear_prob)
            if approved_candidates.empty:
                continue # VETOED
                
            # Step 3: Compute Realized Out-of-Sample Alpha
            avg_top_ret = approved_candidates['forward_ret'].mean()
            median_mkt_ret = slice_d['forward_ret'].median()
            alpha = avg_top_ret - median_mkt_ret
            is_win = 1 if alpha > 0 else 0
            
            all_alpha.append(alpha)
            all_wins.append(is_win)
            active_sessions += 1
            
        if all_wins:
            wr = np.mean(all_wins)
            avg_a = np.mean(all_alpha)
            abstain_pct = (1.0 - (active_sessions / total_sessions_count)) * 100.0 if total_sessions_count > 0 else 0.0
            ann_alpha = avg_a * (active_sessions / 7.0) # Realized annual excess alpha
            
            print(f"Z >= {z_thresh:<9.1f} | {active_sessions:<13} | {abstain_pct:<9.1f}% | {wr:<16.2%} | {avg_a:<+12.3%} | {ann_alpha:<+16.2%}")
            if wr > best_wr:
                best_wr = wr
                
    print("="*85 + "\n")
    logger.info(f"Highest Selective Win Rate Achieved: {best_wr:.2%}")

if __name__ == "__main__":
    run_conformal_selective_evaluation()
