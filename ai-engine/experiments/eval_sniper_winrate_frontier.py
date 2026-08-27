import pandas as pd
import numpy as np
import logging
from app.domain.services.ml.eval_expanded_n150_funnel import fetch_expanded_universe_data, CrossSectionalRanker, feature_forge, graph_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_winrate_frontier():
    data_dict = fetch_expanded_universe_data(top_n=150)
    base_features_dict = {}
    for ticker, df in data_dict.items():
        if ticker == 'VNINDEX': continue
        feats = feature_forge.generate(df, ticker)
        if not feats.empty:
            feats['ticker'] = ticker
            feats['close'] = df['close']
            feats['adtv20_bil'] = (df['close'] * df['volume']).rolling(20, min_periods=5).mean() / 1e6
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

    # Full Walk-Forward 2020 - 2026
    test_years = range(2020, 2027)
    records = []

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
            
            # Filter liquidity
            funnel_slice = eval_slice[eval_slice['adtv20_bil'] >= 10.0]
            if len(funnel_slice) < 10:
                continue
                
            scores_arr = funnel_slice['score'].values
            mean_s = np.mean(scores_arr)
            std_s = np.std(scores_arr) + 1e-8
            top1_s = np.max(scores_arr)
            z_score = (top1_s - mean_s) / std_s
            
            top1 = funnel_slice.sort_values(by='score', ascending=False).head(1)
            top3 = funnel_slice.sort_values(by='score', ascending=False).head(3)
            top5 = funnel_slice.sort_values(by='score', ascending=False).head(5)
            
            records.append({
                'date': d,
                'z_score': z_score,
                'top1_alpha': top1['forward_ret'].mean() - market_median,
                'top3_alpha': top3['forward_ret'].mean() - market_median,
                'top5_alpha': top5['forward_ret'].mean() - market_median,
                'top1_win': 1 if (top1['forward_ret'].mean() - market_median) > 0 else 0,
                'top3_win': 1 if (top3['forward_ret'].mean() - market_median) > 0 else 0,
                'top5_win': 1 if (top5['forward_ret'].mean() - market_median) > 0 else 0,
                'top1_abs_win': 1 if top1['forward_ret'].mean() > 0 else 0,
                'top3_abs_win': 1 if top3['forward_ret'].mean() > 0 else 0,
                'top5_abs_win': 1 if top5['forward_ret'].mean() > 0 else 0,
            })

    df_res = pd.DataFrame(records)
    print("\n" + "="*85)
    print(" CONFORMAL SNIPER WIN RATE FRONTIER ACROSS Z-SCORE THRESHOLDS (2020 - 2026)")
    print("="*85)
    print(f"{'Z-Score Gate':<15} | {'Sessions':<9} | {'Top 1 WR':<10} | {'Top 3 WR':<10} | {'Top 5 WR':<10} | {'Avg 5d Alpha':<12}")
    print("-" * 85)
    
    for z in [0.0, 2.0, 2.5, 2.85, 3.0, 3.2, 3.4, 3.6, 3.8]:
        sub = df_res[df_res['z_score'] >= z]
        if len(sub) > 0:
            print(f"Z >= {z:<10.2f} | {len(sub):<9} | {sub['top1_win'].mean():<10.2%} | {sub['top3_win'].mean():<10.2%} | {sub['top5_win'].mean():<10.2%} | {sub['top5_alpha'].mean():+.3%}")
    print("="*85 + "\n")

if __name__ == '__main__':
    run_winrate_frontier()
