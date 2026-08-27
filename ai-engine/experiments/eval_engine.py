"""
Evaluation Engine (Walk-Forward Backtest) for RAES.
Runs EXP-001 Baseline.
"""

import os
import sys
import logging
import pandas as pd
import numpy as np
from sklearn.metrics import precision_score, recall_score, accuracy_score, brier_score_loss

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.triple_barrier import get_events, get_bins
from app.domain.services.ml.raes_engine import raes_engine
from app.domain.rules.market.hmm_regime_engine import MarketRegimeV2, hmm_engine, HMM_MODEL_PATH
from app.domain.services.ml.adaptive_weights import adaptive_weights

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_universe_data(top_n=150, start_date='2012-01-01', end_date='2026-12-31') -> dict:
    """Fetch OHLCV data for top N liquid tickers."""
    logger.info(f"Fetching Top {top_n} liquid tickers from DB...")
    
    # 1. Find Top N tickers based on ADTV20 over the recent period
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
            logger.info(f"Selected Universe: {tickers}")
            
            # 2. Fetch data for these tickers
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
    
    # Group by ticker
    data_dict = {}
    for ticker in tickers:
        df_ticker = df_data[df_data['ticker'] == ticker].copy()
        if len(df_ticker) > 500: # Ensure enough history
            df_ticker = df_ticker.set_index('date').sort_index()
            data_dict[ticker] = df_ticker
            
    return data_dict

def prepare_dataset(data_dict: dict) -> pd.DataFrame:
    """Generate features and labels for all tickers."""
    logger.info("Generating features and triple barrier labels...")
    all_data = []
    
    for ticker, df in data_dict.items():
        # 1. Features
        features = feature_forge.generate(df, ticker)
        if features.empty:
            continue
            
        # 2. Labels (Triple Barrier)
        # Target = rolling 20d volatility
        target = df['close'].pct_change().rolling(20).std()
        
        # Events: sample when volatility > 1%
        t_events = target[target > 0.01].index
        
        # Triple barrier: PT=2.0, SL=1.0, holding=10 days
        events = get_events(
            close=df['close'],
            t_events=t_events,
            pt_sl=[2.0, 1.0],
            target=target,
            min_ret=0.01,
            t1=10,
            side=pd.Series(1., index=df.index) # Long only
        )
        
        labels = get_bins(events, df['close'])
        
        # 3. Combine
        # 'bin' column will be 1 (Profit), -1 (Loss), 0 (Timeout)
        combined = features.join(labels[['bin']], how='inner').dropna()
        combined['ticker'] = ticker
        
        all_data.append(combined)
        logger.info(f"Processed {ticker}: {len(combined)} samples.")
        
    master_df = pd.concat(all_data)
    return master_df

def run_evaluation():
    logger.info("=== Starting RAES Evaluation Engine (EXP-002) ===")
    
    # 1. Load Data
    data_dict = fetch_universe_data() # Uses default top_n=150
    if not data_dict:
        logger.error("No data fetched.")
        return
        
    # 2. Prepare dataset
    master_df = prepare_dataset(data_dict)
    master_df = master_df.sort_index()
    
    logger.info(f"Total valid samples: {len(master_df)}")
    
    # 3. Walk-Forward Expanding Window Evaluation (EXP-007)
    test_years = range(2020, 2027)
    
    all_y_test_binary = []
    all_primary_pred = []
    all_meta_probs = []
    all_mom = []
    all_vol = []
    all_vol_75th = []
    
    # WORLD MODEL UPDATE (EXP-006): Calculate 75th percentile locally per ticker CAUSALLY
    master_vol = master_df[['ticker', 'ret_vol_20d']].copy()
    master_vol_75th = master_vol.groupby('ticker')['ret_vol_20d'].transform(
        lambda x: x.rolling(window=252, min_periods=20).quantile(0.75)
    )
    
    # Pre-calculate Regimes using VNINDEX
    vnindex_df = data_dict.get('VNINDEX')
    regime_probs_df = pd.DataFrame()
    if vnindex_df is not None and not vnindex_df.empty:
        logger.info("Calculating HMM Regime probabilities for VNINDEX...")
        hmm_engine.load(HMM_MODEL_PATH)
        if hmm_engine.is_trained:
            # Re-extract features properly with rolling windows
            X_vn = hmm_engine._extract_features(vnindex_df)
            hmm_probs = hmm_engine.model.predict_proba(X_vn)
            
            regime_probs_df = pd.DataFrame(index=vnindex_df.index, columns=[MarketRegimeV2.BULL_MARKET, MarketRegimeV2.RANGE_BOUND, MarketRegimeV2.BEAR_MARKET])
            for i in range(hmm_engine.n_components):
                reg_name = hmm_engine.state_map.get(i, MarketRegimeV2.RANGE_BOUND)
                if regime_probs_df[reg_name].isna().all():
                    regime_probs_df[reg_name] = hmm_probs[:, i]
                else:
                    regime_probs_df[reg_name] += hmm_probs[:, i]
            regime_probs_df = regime_probs_df.fillna(0.33)
        
    for year in test_years:
        logger.info(f"--- Walk-Forward Step: Testing Year {year} ---")
        # 15 days embargo to prevent Triple Barrier lookahead leakage
        train_end_date = pd.to_datetime(f"{year-1}-12-15")
        test_start_date = pd.to_datetime(f"{year}-01-01")
        test_end_date = pd.to_datetime(f"{year}-12-31")
        
        train_mask = master_df.index <= train_end_date
        test_mask = (master_df.index >= test_start_date) & (master_df.index <= test_end_date)
        
        df_train = master_df[train_mask]
        df_test = master_df[test_mask]
        
        if df_test.empty:
            continue
            
        y_train = df_train['bin']
        X_train = df_train.drop(columns=['bin', 'ticker'])
        
        y_test = df_test['bin']
        X_test = df_test.drop(columns=['bin', 'ticker'])
        
        logger.info(f"Training on {len(X_train)} samples (up to {train_end_date.date()})")
        logger.info(f"Testing on {len(X_test)} samples ({year})")
        
        # 4. Train RAES Engine
        raes_engine.fit(X_train, y_train)
        
        # 5. Predict on Test Set
        # Map dynamic regimes to test dates
        test_dates = X_test.index
        w_lgb, w_cat, w_xgb = np.zeros(len(X_test)), np.zeros(len(X_test)), np.zeros(len(X_test))
        
        if not regime_probs_df.empty:
            # Reindex regime_probs to match test dates (which may have multiple tickers per date)
            matched_regimes = regime_probs_df.reindex(test_dates).fillna(0.33)
            # Create a dict of Series for adaptive_weights to process (it can handle Series)
            regime_dict = {
                MarketRegimeV2.BULL_MARKET: matched_regimes[MarketRegimeV2.BULL_MARKET].values,
                MarketRegimeV2.RANGE_BOUND: matched_regimes[MarketRegimeV2.RANGE_BOUND].values,
                MarketRegimeV2.BEAR_MARKET: matched_regimes[MarketRegimeV2.BEAR_MARKET].values
            }
            # Unpack Series
            w_l, w_c, w_x = adaptive_weights.get_weights(regime_dict)
            w_lgb, w_cat, w_xgb = w_l, w_c, w_x
        else:
            w_lgb.fill(0.33)
            w_cat.fill(0.33)
            w_xgb.fill(0.34)
            
        p_lgb = raes_engine.lgb_model.predict_proba(X_test)[:, 1]
        p_cat = raes_engine.cat_model.predict_proba(X_test)[:, 1]
        p_xgb = raes_engine.xgb_model.predict_proba(X_test)[:, 1]
        
        p_blend = (w_lgb * p_lgb) + (w_cat * p_cat) + (w_xgb * p_xgb)
        
        # Store base probabilities to compute sweep metrics at the end
        meta_probs_year_1 = raes_engine.meta_labeler.predict_proba(X_test, pd.Series(1, index=X_test.index))
        
        all_y_test_binary.extend((y_test == 1).astype(int).tolist())
        all_primary_pred.extend(p_blend.tolist())  # store continuous p_blend instead of binary
        all_meta_probs.extend(meta_probs_year_1.tolist())
        
        # Store dynamic brake inputs
        for mom, vol in zip(X_test['mom_20d'], X_test['ret_vol_20d']):
            all_mom.append(mom)
            all_vol.append(vol)
        all_vol_75th.extend(master_vol_75th[test_mask].tolist())
        
        # Free up RAM
        import gc
        gc.collect()
        
    # 6. Calculate Aggregate Metrics for different thresholds (Sweep)
    y_test_binary = pd.Series(all_y_test_binary)
    p_blend_series = np.array(all_primary_pred)
    meta_probs_1_series = pd.Series(all_meta_probs)
    
    mom_20d_series = np.array(all_mom)
    vol_20d_series = np.array(all_vol)
    vol_75th_series = np.array(all_vol_75th)
    
    print("\n" + "="*60)
    print(" EXP-007 METRICS SWEEP (WALK-FORWARD | OOS: 2020-2026)")
    print("="*60)
    print(f"{'Threshold':<10} | {'Signals':<8} | {'Precision':<10} | {'Expectancy':<12}")
    print("-" * 60)
    
    best_expectancy = -999.0
    best_thresh = 0.60
    
    for thresh in [0.45, 0.50, 0.55, 0.60, 0.65]:
        primary_pred = (p_blend_series >= thresh).astype(int)
        
        # Dynamic Trend Alignment Brake
        dynamic_threshold = np.full(len(p_blend_series), 0.30)
        dynamic_threshold[mom_20d_series < 0] = 0.60
        dynamic_threshold[(mom_20d_series >= 0) & (mom_20d_series < 0.02)] = 0.45
        dynamic_threshold[vol_20d_series > vol_75th_series] = 1.0
        
        final_pred = primary_pred & (meta_probs_1_series >= dynamic_threshold).astype(int)
        
        precision = precision_score(y_test_binary, final_pred, zero_division=0)
        false_buy_rate = 1.0 - precision if sum(final_pred) > 0 else 0.0
        expectancy = (precision * 2.0) - (false_buy_rate * 1.0)
        
        print(f"{thresh:<10.2f} | {sum(final_pred):<8} | {precision:<10.2%} | {expectancy:+.3f} R")
        
        if expectancy > best_expectancy:
            best_expectancy = expectancy
            best_thresh = thresh
            
    print("="*60 + "\n")
    logger.info(f"Optimal Threshold from Sweep: {best_thresh} with Expectancy {best_expectancy:.3f} R")

if __name__ == "__main__":
    run_evaluation()
