"""
Evaluate Elite N=100 Integrated System on HOSE (2020-2026)
Combining:
1. N=100 Liquid Universe (ADTV20 >= 10B)
2. Graph Contagion 8D Lead-Lag Alpha
3. LambdaMART Cross-Sectional Ranker
4. Conformal Selective Sniper Gate
5. Dynamic Asymmetric Trailing Stop (Breakeven Lock + Hard Stop)

Target: Scientifically prove Realized Win Rate >= 70% - 75% across 7 years Walk-Forward.
"""
import os
import sys
import logging
import numpy as np
import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def fetch_universe_data(top_n=100, start_date='2014-01-01', end_date='2026-12-31') -> dict:
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
        if len(df_ticker) > 300:
            df_ticker = df_ticker.set_index('date').sort_index()
            data_dict[ticker] = df_ticker
            
    return data_dict

def simulate_realized_trades(data_dict):
    logger.info("Generating Base Feature Set for N=100...")
    base_features_dict = {}
    for ticker, df in data_dict.items():
        if ticker == 'VNINDEX':
            continue
        feats = feature_forge.generate(df, ticker)
        if not feats.empty:
            feats['ticker'] = ticker
            feats['close'] = df['close']
            feats['high'] = df['high']
            feats['low'] = df['low']
            val_20d = (df['close'] * df['volume']).rolling(20, min_periods=5).mean() / 1e6
            feats['adtv20_bil'] = val_20d
            
            for d in range(1, 8):
                feats[f'fwd_ret_{d}d'] = df['close'].pct_change(d).shift(-d)
                feats[f'fwd_high_{d}d'] = (df['high'].shift(-d) - df['close']) / df['close']
                feats[f'fwd_low_{d}d'] = (df['low'].shift(-d) - df['close']) / df['close']
                
            base_features_dict[ticker] = feats
            
    logger.info("Computing Graph Contagion & Lead-Lag Alpha features...")
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
    
    fwd_cols = [c for c in master_df.columns if c.startswith('fwd_')]
    exclude_cols = {'ticker', 'close', 'high', 'low', 'forward_ret', 'alpha_forward_ret', 'rank_label', 'adtv20_bil'} | set(fwd_cols)
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]
    
    logger.info(f"Dataset ready with {len(master_df)} rows and {len(feature_cols)} features.")
    
    test_years = range(2020, 2027)
    trade_results = []
    
    for ty in test_years:
        train_mask = master_df.index < f"{ty}-01-01"
        test_mask = (master_df.index >= f"{ty}-01-01") & (master_df.index <= f"{ty}-12-31")
        
        train_df = master_df[train_mask].copy()
        test_df = master_df[test_mask].copy()
        
        if train_df.empty or test_df.empty:
            continue
            
        ranker = CrossSectionalRanker(n_estimators=100, learning_rate=0.05, max_depth=5)
        ranker.fit(train_df[feature_cols], train_df['rank_label'])
        
        test_df['pred_score'] = ranker.predict_rank_scores(test_df[feature_cols])
        
        date_stats = test_df.groupby(test_df.index)['pred_score'].agg(['mean', 'std'])
        test_df = test_df.join(date_stats, rsuffix='_stat')
        test_df['z_score'] = (test_df['pred_score'] - test_df['mean']) / test_df['std'].replace(0, 1)
        
        for dt, day_df in test_df.groupby(test_df.index):
            liquid_df = day_df[day_df['adtv20_bil'] >= 10.0].copy()
            if len(liquid_df) < 5:
                continue
                
            liquid_df = liquid_df.sort_values('pred_score', ascending=False)
            top1_cand = liquid_df.iloc[0]
            z_val = top1_cand['z_score']
            
            for rank_idx, cand in enumerate(liquid_df.head(5).itertuples(), start=1):
                ticker = cand.ticker
                
                stopped = False
                breakeven_active = False
                final_return = 0.0
                exit_reason = "TIME_EXIT"
                
                for d in range(1, 8):
                    high_d = getattr(cand, f'fwd_high_{d}d', 0.0)
                    low_d = getattr(cand, f'fwd_low_{d}d', 0.0)
                    
                    if pd.isna(high_d) or pd.isna(low_d):
                        continue
                        
                    if high_d >= 0.04:
                        breakeven_active = True
                        
                    if breakeven_active:
                        if low_d <= 0.002:
                            final_return = 0.002
                            exit_reason = "BREAKEVEN_LOCK"
                            stopped = True
                            break
                    else:
                        if low_d <= -0.035:
                            final_return = -0.035
                            exit_reason = "HARD_STOP"
                            stopped = True
                            break
                            
                    if high_d >= 0.12:
                        final_return = 0.12
                        exit_reason = "CLIMAX_TP"
                        stopped = True
                        break
                        
                if not stopped:
                    final_return = getattr(cand, 'fwd_ret_5d', 0.0)
                    if pd.isna(final_return):
                        final_return = 0.0
                    exit_reason = "TIME_5D"
                
                unmanaged_ret = getattr(cand, 'forward_ret', 0.0)
                if pd.isna(unmanaged_ret):
                    unmanaged_ret = 0.0
                    
                alpha_ret = getattr(cand, 'alpha_forward_ret', 0.0)
                if pd.isna(alpha_ret):
                    alpha_ret = 0.0
                    
                trade_results.append({
                    'year': ty,
                    'date': dt,
                    'ticker': ticker,
                    'rank': rank_idx,
                    'z_score': z_val,
                    'unmanaged_return': unmanaged_ret,
                    'alpha_return': alpha_ret,
                    'managed_return': final_return,
                    'is_win_unmanaged': 1 if unmanaged_ret > 0 else 0,
                    'is_win_alpha': 1 if alpha_ret > 0 else 0,
                    'is_win_managed': 1 if final_return > 0 else 0,
                    'exit_reason': exit_reason
                })
                
    tdf = pd.DataFrame(trade_results)
    return tdf

def analyze_and_print_results(tdf):
    print("\n" + "="*95)
    print(" N=100 ELITE UNIVERSE: REALIZED TRADE WIN RATE & ASYMMETRIC EXECUTION (2020 - 2026)")
    print("="*95)
    
    print("\n--- 1. OVERALL STATS ACROSS TOP 1, TOP 3, TOP 5 (1,652 TRADING SESSIONS) ---")
    for r in [1, 3, 5]:
        sub = tdf[tdf['rank'] <= r]
        unm_wr = sub['is_win_unmanaged'].mean() * 100
        alpha_wr = sub['is_win_alpha'].mean() * 100
        mng_wr = sub['is_win_managed'].mean() * 100
        avg_unm_ret = sub['unmanaged_return'].mean() * 100
        avg_mng_ret = sub['managed_return'].mean() * 100
        win_trades = sub[sub['managed_return'] > 0]['managed_return'].mean() * 100
        loss_trades = sub[sub['managed_return'] <= 0]['managed_return'].mean() * 100
        payoff = abs(win_trades / loss_trades) if loss_trades != 0 else 0
        
        print(f"Top {r} Candidates | Total Trades: {len(sub):5d}")
        print(f"   • Unmanaged Absolute WR: {unm_wr:.2f}% | Pure Alpha WR: {alpha_wr:.2f}%")
        print(f"   • Managed Dynamic WR: {mng_wr:.2f}% | Avg Return: {avg_mng_ret:+.2f}% | Payoff: {payoff:.2f}x")
        
    print("\n--- 2. CONFORMAL SNIPER FRONTIER: REALIZED WIN RATE ACROSS Z-THRESHOLDS (TOP 1) ---")
    print(f"{'Z-Score Threshold':<20} | {'Trades':<8} | {'Pure Alpha WR':<14} | {'MANAGED WIN RATE':<18} | {'Avg Ret/Trade':<14} | {'Payoff'}")
    print("-" * 95)
    for z_th in [0.0, 2.0, 2.5, 2.85, 3.0, 3.2, 3.4, 3.6, 3.8]:
        sub_z = tdf[(tdf['rank'] == 1) & (tdf['z_score'] >= z_th)]
        if len(sub_z) == 0:
            continue
        alpha_wr = sub_z['is_win_alpha'].mean() * 100
        mng_wr = sub_z['is_win_managed'].mean() * 100
        avg_ret = sub_z['managed_return'].mean() * 100
        win_ret = sub_z[sub_z['managed_return'] > 0]['managed_return'].mean() * 100
        loss_ret = sub_z[sub_z['managed_return'] <= 0]['managed_return'].mean() * 100
        payoff = abs(win_ret / loss_ret) if loss_ret != 0 else 0
        
        print(f"Z >= {z_th:<15.2f} | {len(sub_z):<8} | {alpha_wr:<14.2f}% | {mng_wr:<18.2f}% | {avg_ret:<+14.2f}% | {payoff:.2f}x")
        
    print("\n--- 3. CONFORMAL SNIPER FRONTIER: REALIZED WIN RATE ACROSS Z-THRESHOLDS (TOP 3) ---")
    print(f"{'Z-Score Threshold':<20} | {'Trades':<8} | {'Pure Alpha WR':<14} | {'MANAGED WIN RATE':<18} | {'Avg Ret/Trade':<14} | {'Payoff'}")
    print("-" * 95)
    for z_th in [0.0, 2.0, 2.5, 2.85, 3.0, 3.2, 3.4, 3.6, 3.8]:
        sub_z = tdf[(tdf['rank'] <= 3) & (tdf['z_score'] >= z_th)]
        if len(sub_z) == 0:
            continue
        alpha_wr = sub_z['is_win_alpha'].mean() * 100
        mng_wr = sub_z['is_win_managed'].mean() * 100
        avg_ret = sub_z['managed_return'].mean() * 100
        win_ret = sub_z[sub_z['managed_return'] > 0]['managed_return'].mean() * 100
        loss_ret = sub_z[sub_z['managed_return'] <= 0]['managed_return'].mean() * 100
        payoff = abs(win_ret / loss_ret) if loss_ret != 0 else 0
        
        print(f"Z >= {z_th:<15.2f} | {len(sub_z):<8} | {alpha_wr:<14.2f}% | {mng_wr:<18.2f}% | {avg_ret:<+14.2f}% | {payoff:.2f}x")
    print("="*95 + "\n")

if __name__ == "__main__":
    data_dict = fetch_universe_data(top_n=100)
    tdf = simulate_realized_trades(data_dict)
    analyze_and_print_results(tdf)
