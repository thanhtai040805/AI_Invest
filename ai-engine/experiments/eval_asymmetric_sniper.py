"""
Comprehensive Walk-Forward Backtest (2020-2026):
Asymmetric Trailing Stop (Let Winners Run) + Ultra-Selective Sniper Engine vs Fixed Horizon.
"""

import os
import sys
import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.ecosystem_lead_lag import ecosystem_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from app.domain.services.ml.asymmetric_trailing_engine import AsymmetricTrailingEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_asymmetric_sniper_backtest():
    # 1. Fetch Top 100 HOSE tickers + VNINDEX
    query_tickers = """
        SELECT ticker, SUM(close_adj * volume_continuous) as total_val
        FROM market_data_daily
        WHERE date >= '2020-01-01'
        GROUP BY ticker
        ORDER BY total_val DESC
        LIMIT 100;
    """
    with get_conn() as conn:
        df_tickers = pd.read_sql(query_tickers, conn)
        tickers = df_tickers['ticker'].tolist()
        if 'VNINDEX' not in tickers:
            tickers.append('VNINDEX')
        query_data = f"""
            SELECT ticker, date, open_adj as open, high_adj as high, low_adj as low, close_adj as close, volume_continuous as volume
            FROM market_data_daily
            WHERE ticker IN ({','.join([f"'{t}'" for t in tickers])})
            AND date >= '2014-01-01'
            ORDER BY ticker, date;
        """
        df_data = pd.read_sql(query_data, conn)
        
    df_data['date'] = pd.to_datetime(df_data['date'])
    data_dict = {t: df_data[df_data['ticker'] == t].set_index('date').sort_index() for t in tickers if len(df_data[df_data['ticker'] == t]) > 500}
    
    # 2. Build master dataset
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
        merged = pd.concat([feats, ll_feats], axis=1).dropna() if ll_feats is not None else feats.dropna()
        combined_list.append(merged)
        
    master_df = pd.concat(combined_list).sort_index()
    master_df = CrossSectionalRanker.compute_forward_alpha_target(master_df, forward_window=5)
    
    exclude_cols = {'ticker', 'close', 'forward_ret', 'alpha_forward_ret', 'rank_label'}
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]
    
    # 3. Walk-Forward Simulation (2020 - 2026)
    test_years = range(2020, 2027)
    
    # Storage for different execution policies
    trades_fixed_5d = []
    trades_asym_all = []
    trades_asym_sniper = [] # Z >= 2.70
    
    trailing_engine = AsymmetricTrailingEngine()
    
    for year in test_years:
        train_end_date = pd.to_datetime(f"{year-1}-12-20")
        test_start_date = pd.to_datetime(f"{year}-01-01")
        test_end_date = pd.to_datetime(f"{year}-12-31")
        
        df_train = master_df[master_df.index <= train_end_date].copy()
        df_test = master_df[(master_df.index >= test_start_date) & (master_df.index <= test_end_date)].copy()
        
        if df_test.empty or len(df_train) < 1000:
            continue
            
        df_train_norm = CrossSectionalRanker.cross_sectional_zscore(df_train, feature_cols)
        df_test_norm = CrossSectionalRanker.cross_sectional_zscore(df_test, feature_cols)
        
        ranker = CrossSectionalRanker(n_estimators=150, learning_rate=0.05, max_depth=5)
        ranker.fit(df_train_norm[feature_cols], df_train_norm['rank_label'])
        
        test_dates = df_test_norm.index.unique().sort_values()
        
        for d in test_dates:
            slice_d = df_test_norm.loc[[d]]
            if len(slice_d) < 10:
                continue
                
            scores = ranker.predict_rank_scores(slice_d[feature_cols]).values
            sorted_indices = np.argsort(scores)[::-1]
            
            top5_mean_score = np.mean(scores[sorted_indices[:5]])
            mean_score = np.mean(scores)
            std_score = np.std(scores) + 1e-8
            conviction_z = (top5_mean_score - mean_score) / std_score
            
            top5_tickers = slice_d.iloc[sorted_indices[:5]]['ticker'].tolist()
            
            # Policy 1: Fixed 5d Horizon for Top 5
            for t in top5_tickers:
                ret_5d = slice_d[slice_d['ticker'] == t]['forward_ret'].values[0]
                trades_fixed_5d.append({'ret': ret_5d, 'year': year, 'z': conviction_z})
                
            # Policy 2 & 3: Asymmetric Trailing Stop
            for t in top5_tickers:
                if t not in data_dict:
                    continue
                tr = trailing_engine.simulate_trade_path(data_dict[t], d, t)
                if tr is not None:
                    trades_asym_all.append({
                        'ret': tr.return_pct,
                        'days': tr.holding_days,
                        'reason': tr.exit_reason,
                        'year': year,
                        'z': conviction_z
                    })
                    # Sniper filter: Only trade when Conviction Z >= 2.65
                    if conviction_z >= 2.65:
                        trades_asym_sniper.append({
                            'ret': tr.return_pct,
                            'days': tr.holding_days,
                            'reason': tr.exit_reason,
                            'year': year,
                            'z': conviction_z
                        })
                        
    # 4. Comparative Quantitative Analysis
    def _analyze_policy(trades_list, name):
        df_tr = pd.DataFrame(trades_list)
        if df_tr.empty:
            return
        total_trades = len(df_tr)
        win_trades = df_tr[df_tr['ret'] > 0]
        loss_trades = df_tr[df_tr['ret'] <= 0]
        
        win_rate = len(win_trades) / total_trades if total_trades > 0 else 0.0
        avg_win = win_trades['ret'].mean() if not win_trades.empty else 0.0
        avg_loss = abs(loss_trades['ret'].mean()) if not loss_trades.empty else 1e-6
        payoff = avg_win / avg_loss if avg_loss > 0 else 0.0
        
        avg_ret = df_tr['ret'].mean()
        avg_days = df_tr['days'].mean() if 'days' in df_tr.columns else 5.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        
        return {
            'Name': name,
            'Trades': total_trades,
            'Win Rate': win_rate,
            'Avg Win': avg_win,
            'Avg Loss': avg_loss,
            'Payoff (W/L)': payoff,
            'Avg Return/Trade': avg_ret,
            'Avg Holding Days': avg_days,
            'Expectancy/Trade': expectancy
        }
        
    res1 = _analyze_policy(trades_fixed_5d, "1. Fixed 5-Day Horizon (EXP-008 Baseline)")
    res2 = _analyze_policy(trades_asym_all, "2. Asymmetric Trailing Stop (All Sessions)")
    res3 = _analyze_policy(trades_asym_sniper, "3. Ultra-Selective Sniper (Z >= 2.65 + Trailing)")
    
    summary_df = pd.DataFrame([res1, res2, res3])
    
    print("\n" + "="*100)
    print(" EXP-010 QUANTITATIVE VERIFICATION: ASYMMETRIC TRAILING STOP & SNIPER ENGINE (2020 - 2026)")
    print("="*100)
    print(f"{'Policy Name':<42} | {'Trades':<8} | {'Win Rate':<10} | {'Payoff (W/L)':<12} | {'Avg Win':<10} | {'Avg Loss':<10} | {'Expectancy':<12}")
    print("-" * 100)
    for _, row in summary_df.iterrows():
        print(f"{row['Name']:<42} | {row['Trades']:<8} | {row['Win Rate']:<9.2%} | {row['Payoff (W/L)']:<11.2f}x | {row['Avg Win']:<+9.2%} | {row['Avg Loss']:<-9.2%} | {row['Expectancy/Trade']:<+11.3%}")
    print("="*100 + "\n")

if __name__ == "__main__":
    run_asymmetric_sniper_backtest()
