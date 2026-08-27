"""
EXP-015: T+2.5 Compliant Execution Engine & Hybrid Stacking ML Ranker
========================================================================
Realistic Vietnam Stock Market (HOSE) Simulation:
  - T+0: Entry at Day T Close/Open.
  - T+1: POSITION LOCKED 100%. No selling allowed under any circumstance!
  - T+2: Shares settle in afternoon (~13:00). First exit window opens.
  - T+3 to T+7: Full trading flexibility (Breakeven Lock +2.5% -> +0.2%, Hard Stop -3.5%, TP).

Machine Learning Innovation (Hybrid Stacking Ensemble):
  1. Branch 1: LambdaMART Ranker (LightGBM Ranker) -> Cross-sectional ordering.
  2. Branch 2: Multi-Horizon 3-Day Momentum Regressor -> Predicts 3-day holding momentum (T -> T+2.5).
  3. Branch 3: T+2.5 Survival Gate (Classifier) -> Predicts P(Drawdown in T+1..T+2 > -3.5%).
     Filters out stocks prone to immediate post-entry limit-down traps!

Constraint: max_workers <= 3, n_jobs <= 4 (per user machine limits).
"""

import os
import sys
import logging
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
import lightgbm as lgb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from app.domain.services.ml.dual_tier_sniper_engine import dual_tier_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────
# Trade Simulation: T+1 Naive (Theoretical Baseline)
# ────────────────────────────────────────────────────
def simulate_naive_t1_trade(cand_row, inst):
    """Simulates trade assuming immediate Day T+1 exit capability (Theoretical)."""
    breakeven_active = False
    for d in range(1, 8):
        high_d = cand_row.get(f'fwd_high_{d}d', np.nan)
        low_d = cand_row.get(f'fwd_low_{d}d', np.nan)
        if pd.isna(high_d) or pd.isna(low_d):
            continue

        if high_d >= inst.breakeven_trigger_pct:
            breakeven_active = True

        if breakeven_active:
            if low_d <= 0.002:
                return 0.002, "NAIVE_BREAKEVEN", d
        else:
            if low_d <= inst.hard_stop_pct:
                return inst.hard_stop_pct, "NAIVE_STOP", d

        if inst.take_profit_pct is not None and high_d >= inst.take_profit_pct:
            return inst.take_profit_pct, "NAIVE_TP", d

        if inst.take_profit_pct is None and high_d >= 0.15:
            return 0.15, "NAIVE_CLIMAX", d

    final_ret = cand_row.get('forward_ret', 0.0)
    return (0.0 if pd.isna(final_ret) else final_ret), "NAIVE_TIME_5D", 5


# ────────────────────────────────────────────────────
# Trade Simulation: T+2.5 Compliant (Real Vietnam Law)
# ────────────────────────────────────────────────────
def simulate_t25_compliant_trade(cand_row, inst):
    """
    Simulates REALISTIC HOSE Trade Execution under T+2.5 Settlement:
      - Day 1 (T+1): LOCKED. Cannot sell even if price hits floor!
      - Day 2 (T+2): Afternoon session (13:00) shares available.
                     Exit checked on day 2 low/high.
      - Day 3-7 (T+3..7): Fully active Stop Loss / Breakeven / TP.
    """
    breakeven_active = False
    
    # ── Day 1 (T+1): Position LOCKED. No exit possible. ──
    # Check if Day 1 had high >= breakeven trigger to arm the shield for Day 2!
    high_1d = cand_row.get('fwd_high_1d', np.nan)
    if not pd.isna(high_1d) and high_1d >= inst.breakeven_trigger_pct:
        breakeven_active = True

    # ── Day 2 (T+2): Afternoon settlement. First possible exit window. ──
    high_2d = cand_row.get('fwd_high_2d', np.nan)
    low_2d = cand_row.get('fwd_low_2d', np.nan)
    ret_2d = cand_row.get('fwd_ret_2d', np.nan)

    if not pd.isna(high_2d) and high_2d >= inst.breakeven_trigger_pct:
        breakeven_active = True

    if not pd.isna(low_2d):
        if breakeven_active:
            if low_2d <= 0.002:
                # Exited at breakeven lock in afternoon T+2
                return 0.002, "T25_BREAKEVEN_D2", 2
        else:
            # If price crashed below hard stop during T+1 or T+2, we can ONLY sell at T+2 low/close!
            if low_2d <= inst.hard_stop_pct:
                exit_ret = min(inst.hard_stop_pct, low_2d) # Might suffer slippage below -3.5% if gap down
                return exit_ret, "T25_HARD_STOP_D2", 2

    # Check Take Profit on Day 2
    if not pd.isna(high_2d):
        if inst.take_profit_pct is not None and high_2d >= inst.take_profit_pct:
            return inst.take_profit_pct, "T25_SWING_TP_D2", 2
        if inst.take_profit_pct is None and high_2d >= 0.15:
            return 0.15, "T25_CLIMAX_D2", 2

    # ── Day 3 to Day 7 (T+3..T+7): Free Trading ──
    for d in range(3, 8):
        high_d = cand_row.get(f'fwd_high_{d}d', np.nan)
        low_d = cand_row.get(f'fwd_low_{d}d', np.nan)
        if pd.isna(high_d) or pd.isna(low_d):
            continue

        if high_d >= inst.breakeven_trigger_pct:
            breakeven_active = True

        if breakeven_active:
            if low_d <= 0.002:
                return 0.002, "T25_BREAKEVEN", d
        else:
            if low_d <= inst.hard_stop_pct:
                return inst.hard_stop_pct, "T25_HARD_STOP", d

        if inst.take_profit_pct is not None and high_d >= inst.take_profit_pct:
            return inst.take_profit_pct, "T25_SWING_TP", d

        if inst.take_profit_pct is None and high_d >= 0.15:
            return 0.15, "T25_CLIMAX", d

    # Time exit at day 5
    final_ret = cand_row.get('forward_ret', 0.0)
    return (0.0 if pd.isna(final_ret) else final_ret), "T25_TIME_5D", 5


# ────────────────────────────────────────────────────
# Hybrid Stacking ML Model Architecture
# ────────────────────────────────────────────────────
class HybridStackingRanker:
    """
    3-Branch Ensemble Architecture:
      - Branch 1: LambdaMART Ranker (NDCG@5 optimization)
      - Branch 2: 3-Day Momentum Ridge Regressor (T+2.5 holding momentum)
      - Branch 3: T+2.5 Survival Gate Classifier (P(No severe drawdown in locked period))
    """
    def __init__(self):
        self.ranker = lgb.LGBMRanker(
            objective='lambdarank',
            n_estimators=100,
            learning_rate=0.05,
            max_depth=5,
            random_state=42,
            n_jobs=4,
            importance_type='gain'
        )
        self.regressor = Ridge(alpha=10.0)
        self.survival_gate = lgb.LGBMClassifier(
            n_estimators=60,
            learning_rate=0.05,
            max_depth=4,
            random_state=42,
            n_jobs=4
        )
        self.feature_cols = []

    def fit(self, train_df: pd.DataFrame, feature_cols: list):
        self.feature_cols = feature_cols
        X = train_df[feature_cols]
        y_rank = train_df['rank_label']
        y_3d_ret = train_df['fwd_ret_3d'].fillna(0.0)

        # Survival Label: 1 if min(low_1d, low_2d) > -0.035 else 0
        min_lock_low = np.minimum(train_df['fwd_low_1d'].fillna(0.0), train_df['fwd_low_2d'].fillna(0.0))
        y_survival = (min_lock_low > -0.035).astype(int)

        # 1. Fit LambdaMART Ranker
        group_counts = train_df.groupby(train_df.index).size().tolist()
        self.ranker.fit(X, y_rank, group=group_counts)

        # 2. Fit 3-Day Momentum Regressor
        self.regressor.fit(X.fillna(0.0), y_3d_ret)

        # 3. Fit T+2.5 Survival Gate Classifier
        self.survival_gate.fit(X.fillna(0.0), y_survival)

    def predict_hybrid_scores(self, test_df: pd.DataFrame) -> pd.DataFrame:
        X = test_df[self.feature_cols]
        
        # Raw predictions
        rank_preds = self.ranker.predict(X)
        mom_preds = self.regressor.predict(X.fillna(0.0))
        surv_probs = self.survival_gate.predict_proba(X.fillna(0.0))[:, 1]

        res_df = test_df[['ticker', 'adtv20_bil']].copy()
        res_df['rank_pred'] = rank_preds
        res_df['mom_pred'] = mom_preds
        res_df['surv_prob'] = surv_probs

        # Cross-sectional Z-score normalization per date
        def _norm_group(g):
            r_std = g['rank_pred'].std()
            m_std = g['mom_pred'].std()
            r_z = (g['rank_pred'] - g['rank_pred'].mean()) / (r_std + 1e-8) if r_std > 0 else 0.0
            m_z = (g['mom_pred'] - g['mom_pred'].mean()) / (m_std + 1e-8) if m_std > 0 else 0.0
            
            # Hybrid Score: 65% LambdaMART Rank + 35% Multi-day Momentum
            hybrid_z = 0.65 * r_z + 0.35 * m_z

            # Survival Gate Penalty: If survival probability < 55%, penalize score heavily!
            penalty_mask = g['surv_prob'] < 0.55
            hybrid_z = np.where(penalty_mask, hybrid_z - 2.0, hybrid_z)

            g['pred_score'] = hybrid_z
            return g

        res_df = res_df.groupby(res_df.index, group_keys=False).apply(_norm_group)
        return res_df


# ────────────────────────────────────────────────────
# Data Fetch
# ────────────────────────────────────────────────────
def fetch_data():
    logger.info("Fetching Top 100 liquid tickers from DB...")
    query_tickers = """
        SELECT ticker, SUM(close_adj * volume_continuous) as total_val
        FROM market_data_daily
        WHERE date >= '2020-01-01'
        GROUP BY ticker
        ORDER BY total_val DESC
        LIMIT 100;
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
                AND date >= '2014-01-01' AND date <= '2026-12-31'
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


# ────────────────────────────────────────────────────
# Main Walk-Forward Backtest
# ────────────────────────────────────────────────────
def run_t25_walk_forward():
    data_dict = fetch_data()
    vnindex_df = data_dict.get('VNINDEX')

    logger.info("Generating Base Feature Set...")
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

    logger.info("Computing Graph Contagion & Lead-Lag Alpha...")
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
    exclude_cols = (
        {'ticker', 'close', 'high', 'low', 'forward_ret', 'alpha_forward_ret',
         'rank_label', 'adtv20_bil'}
        | set(fwd_cols)
    )
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]

    test_years = range(2020, 2027)
    trade_log_naive = []
    trade_log_t25_single = []
    trade_log_t25_hybrid = []

    for ty in test_years:
        train_mask = master_df.index < f"{ty}-01-01"
        test_mask = (master_df.index >= f"{ty}-01-01") & (master_df.index <= f"{ty}-12-31")

        train_df = master_df[train_mask].copy()
        test_df = master_df[test_mask].copy()

        if train_df.empty or test_df.empty:
            continue

        # 1. Fit Single LambdaMART
        single_ranker = CrossSectionalRanker(n_estimators=100, learning_rate=0.05, max_depth=5)
        single_ranker.fit(train_df[feature_cols], train_df['rank_label'])
        test_df['pred_score_single'] = single_ranker.predict_rank_scores(test_df[feature_cols])

        # 2. Fit EXP-015 Hybrid Stacking Model
        hybrid_model = HybridStackingRanker()
        hybrid_model.fit(train_df, feature_cols)
        hybrid_res = hybrid_model.predict_hybrid_scores(test_df)
        test_df['pred_score_hybrid'] = hybrid_res['pred_score']

        for dt, day_df in test_df.groupby(test_df.index):
            regime = dual_tier_engine.evaluate_macro_regime(vnindex_df, dt)

            # ── A. Instructions for Single LambdaMART ──
            inst_single = dual_tier_engine.generate_trade_allocations(
                candidate_scores=day_df[['ticker', 'pred_score_single', 'adtv20_bil']].rename(columns={'pred_score_single': 'pred_score'}),
                regime=regime,
                top_k=3
            )

            # ── B. Instructions for EXP-015 Hybrid Stacking ──
            inst_hybrid = dual_tier_engine.generate_trade_allocations(
                candidate_scores=day_df[['ticker', 'pred_score_hybrid', 'adtv20_bil']].rename(columns={'pred_score_hybrid': 'pred_score'}),
                regime=regime,
                top_k=3
            )

            # Simulate Single LambdaMART under Naive T+1 and Realistic T+2.5
            for inst in inst_single:
                cand_row = day_df[day_df['ticker'] == inst.ticker].iloc[0]

                # (A) Naive T+1
                ret_n, r_n, d_n = simulate_naive_t1_trade(cand_row, inst)
                trade_log_naive.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker, 'tier': inst.tier,
                    'final_return': ret_n, 'is_win': 1 if ret_n > 0 else 0,
                    'weighted_pnl': ret_n * inst.target_weight_pct, 'exit_reason': r_n
                })

                # (B) Real T+2.5 on Single Model
                ret_t, r_t, d_t = simulate_t25_compliant_trade(cand_row, inst)
                trade_log_t25_single.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker, 'tier': inst.tier,
                    'final_return': ret_t, 'is_win': 1 if ret_t > 0 else 0,
                    'weighted_pnl': ret_t * inst.target_weight_pct, 'exit_reason': r_t
                })

            # Simulate (C) Real T+2.5 on EXP-015 Hybrid Stacking Model
            for inst in inst_hybrid:
                cand_row = day_df[day_df['ticker'] == inst.ticker].iloc[0]
                ret_h, r_h, d_h = simulate_t25_compliant_trade(cand_row, inst)
                trade_log_t25_hybrid.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker, 'tier': inst.tier,
                    'final_return': ret_h, 'is_win': 1 if ret_h > 0 else 0,
                    'weighted_pnl': ret_h * inst.target_weight_pct, 'exit_reason': r_h
                })

    df_naive = pd.DataFrame(trade_log_naive)
    df_t25_single = pd.DataFrame(trade_log_t25_single)
    df_t25_hybrid = pd.DataFrame(trade_log_t25_hybrid)
    return df_naive, df_t25_single, df_t25_hybrid


# ────────────────────────────────────────────────────
# Performance Reporting
# ────────────────────────────────────────────────────
def calc_metrics(tdf: pd.DataFrame) -> dict:
    total = len(tdf)
    if total == 0:
        return {}
    wins = tdf[tdf['is_win'] == 1]
    losses = tdf[tdf['is_win'] == 0]
    win_rate = len(wins) / total * 100
    avg_win = wins['final_return'].mean() * 100 if len(wins) > 0 else 0
    avg_loss = losses['final_return'].mean() * 100 if len(losses) > 0 else 0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    avg_ret = tdf['final_return'].mean() * 100
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
    total_weighted_pnl = tdf['weighted_pnl'].sum() * 100
    return {
        'total_trades': total, 'win_rate': win_rate, 'avg_win': avg_win,
        'avg_loss': avg_loss, 'payoff': payoff, 'avg_ret': avg_ret,
        'expectancy': expectancy, 'total_weighted_pnl': total_weighted_pnl
    }


def print_t25_tournament(df_naive, df_t25_single, df_t25_hybrid):
    m_n = calc_metrics(df_naive)
    m_s = calc_metrics(df_t25_single)
    m_h = calc_metrics(df_t25_hybrid)

    print("\n" + "=" * 120)
    print(" EXP-015: REAL-WORLD T+2.5 TOURNAMENT -- NAIVE T+1 vs REAL T+2.5 vs HYBRID STACKING ML")
    print(" Walk-Forward 7-Year Backtest (2020 - 2026) | Full HOSE Settlement Compliance (Spot Equity)")
    print("=" * 120)

    print("\n+------------------------+-------------------+-------------------+-------------------+------------+")
    print("| Metric                 | (A) Naive T+1     | (B) Real T+2.5    | (C) EXP-015 Hybrid| Delta (C-B)|")
    print("+------------------------+-------------------+-------------------+-------------------+------------+")

    rows = [
        ("Total Trades",        f"{m_n['total_trades']:>17d}", f"{m_s['total_trades']:>17d}", f"{m_h['total_trades']:>17d}", "--"),
        ("Win Rate",            f"{m_n['win_rate']:>16.2f}%",   f"{m_s['win_rate']:>16.2f}%",   f"{m_h['win_rate']:>16.2f}%",   f"{m_h['win_rate'] - m_s['win_rate']:+.2f}%"),
        ("Avg Win Return",      f"{m_n['avg_win']:>+16.2f}%",  f"{m_s['avg_win']:>+16.2f}%",  f"{m_h['avg_win']:>+16.2f}%",  f"{m_h['avg_win'] - m_s['avg_win']:+.2f}%"),
        ("Avg Loss Return",     f"{m_n['avg_loss']:>+16.2f}%", f"{m_s['avg_loss']:>+16.2f}%", f"{m_h['avg_loss']:>+16.2f}%", f"{m_h['avg_loss'] - m_s['avg_loss']:+.2f}%"),
        ("Payoff Ratio (R:R)",  f"{m_n['payoff']:>16.2f}x",    f"{m_s['payoff']:>16.2f}x",    f"{m_h['payoff']:>16.2f}x",    f"{m_h['payoff'] - m_s['payoff']:+.2f}x"),
        ("Expectancy/Trade",    f"{m_n['expectancy']:>+16.3f}%",f"{m_s['expectancy']:>+16.3f}%",f"{m_h['expectancy']:>+16.3f}%",f"{m_h['expectancy'] - m_s['expectancy']:+.3f}%"),
        ("Cumulative PnL",      f"{m_n['total_weighted_pnl']:>+16.1f}%", f"{m_s['total_weighted_pnl']:>+16.1f}%", f"{m_h['total_weighted_pnl']:>+16.1f}%", f"{m_h['total_weighted_pnl'] - m_s['total_weighted_pnl']:+.1f}%"),
    ]
    for label, v1, v2, v3, delta in rows:
        print(f"| {label:<22} | {v1} | {v2} | {v3} | {delta:>10} |")

    print("+------------------------+-------------------+-------------------+-------------------+------------+")

    # -- Tier Breakdown (Hybrid vs Real T+2.5) --
    print("\n--- TIER BREAKDOWN: HYBRID STACKING (T+2.5 COMPLIANT) ---")
    for tier_name in ['TIER_A_PLUS', 'TIER_A']:
        ms = calc_metrics(df_t25_single[df_t25_single['tier'] == tier_name])
        mh = calc_metrics(df_t25_hybrid[df_t25_hybrid['tier'] == tier_name])
        if not ms or not mh:
            continue
        print(f"\n  [{tier_name}]")
        print(f"    Single GBDT (T+2.5) -> Trades: {ms['total_trades']:4d} | WR: {ms['win_rate']:5.2f}% | Avg Win: {ms['avg_win']:+5.2f}% | Avg Loss: {ms['avg_loss']:+5.2f}% | Payoff: {ms['payoff']:.2f}x | Exp: {ms['expectancy']:+.3f}%")
        print(f"    Hybrid ML   (T+2.5) -> Trades: {mh['total_trades']:4d} | WR: {mh['win_rate']:5.2f}% | Avg Win: {mh['avg_win']:+5.2f}% | Avg Loss: {mh['avg_loss']:+5.2f}% | Payoff: {mh['payoff']:.2f}x | Exp: {mh['expectancy']:+.3f}%")

    # -- Year-by-Year Comparison --
    print("\n--- YEAR-BY-YEAR COMPARISON: HYBRID STACKING (T+2.5) vs SINGLE (T+2.5) ---")
    print(f"{'Year':>6} | {'Single WR':>10} {'Single R:R':>10} {'Single Exp':>11} | {'Hybrid WR':>10} {'Hybrid R:R':>10} {'Hybrid Exp':>11} | {'dWR':>8} {'dExp':>10}")
    print("-" * 118)
    for y in range(2020, 2027):
        ys = df_t25_single[df_t25_single['year'] == y]
        yh = df_t25_hybrid[df_t25_hybrid['year'] == y]
        if ys.empty or yh.empty:
            continue
        ms = calc_metrics(ys)
        mh = calc_metrics(yh)
        delta_wr = mh['win_rate'] - ms['win_rate']
        delta_exp = mh['expectancy'] - ms['expectancy']
        print(f"{y:>6} | {ms['win_rate']:>9.2f}% {ms['payoff']:>9.2f}x {ms['expectancy']:>+10.3f}% | {mh['win_rate']:>9.2f}% {mh['payoff']:>9.2f}x {mh['expectancy']:>+10.3f}% | {delta_wr:>+7.2f}% {delta_exp:>+9.3f}%")

    print("\n" + "=" * 120)


if __name__ == "__main__":
    df_naive, df_t25_single, df_t25_hybrid = run_t25_walk_forward()
    print_t25_tournament(df_naive, df_t25_single, df_t25_hybrid)
