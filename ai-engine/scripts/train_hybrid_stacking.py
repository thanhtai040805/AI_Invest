"""
Production & Walk-Forward Hybrid Stacking Ranker (EXP-016 Standard)
====================================================================
Features:
  1. Data Warm-up: Historical OHLCV loaded from 2014-01-01 to warm up 120d Sharpe, FracDiff & Graph signals.
  2. Training Sample Scope: Strictly from 2018-01-01 (TRAIN_START) onwards.
  3. Layer 0 Forensic Gate: Quarter-aligned Beneish M-Score (M <= -1.78) blocks financial statement manipulation.
  4. 7-Year Walk-Forward Validation: Expanding window out-of-sample backtest (2020 - 2026).
  5. Production Artifact Export: Fits full clean dataset and saves to data/models/hybrid_stacking_ranker.pkl.

Usage:
  # Run full Walk-Forward tournament (2020-2026) + Export Production Model:
  python ai-engine/scripts/train_hybrid_stacking.py

  # Run fast training only (skip Walk-Forward simulation, directly export model):
  python ai-engine/scripts/train_hybrid_stacking.py --fast
"""

import os
import sys
import logging
import argparse
import numpy as np
import pandas as pd
from typing import Dict, Tuple

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.infrastructure.database.pg_pool import get_conn
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from app.domain.services.ml.dual_tier_sniper_engine import dual_tier_engine
from app.domain.services.ml.hybrid_stacking_ranker import (
    hybrid_stacking_ranker,
    beneish_engine,
    HybridStackingRanker,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TrainHybridStacking")

FEATURE_HISTORY_START = "2014-01-01"
TRAIN_START = "2018-01-01"
EXPORT_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../data/models/hybrid_stacking_ranker.pkl")
)


def fetch_training_data() -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    """Fetch Top 100 liquid tickers on HOSE with full OHLCV history."""
    logger.info("Fetching Top 100 liquid tickers from DB (EXP-016 Universe)...")
    query_tickers = """
        SELECT ticker, SUM(close * volume_continuous) as total_val
        FROM market_data_daily_calculation
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

            logger.info(f"Loaded {len(tickers)} Universe tickers (Top 100 HOSE + VNINDEX).")

            query_data = f"""
                SELECT ticker, date, open as open, high as high, low as low, close as close, volume_continuous as volume
                FROM market_data_daily_calculation
                WHERE ticker IN ({','.join([f"'{t}'" for t in tickers])})
                AND date >= '{FEATURE_HISTORY_START}' AND date <= '2026-12-31'
                ORDER BY ticker, date;
            """
            df_data = pd.read_sql(query_data, conn)
    except Exception as e:
        logger.error(f"Database query error: {e}")
        raise

    df_data['date'] = pd.to_datetime(df_data['date'])
    data_dict = {}
    for ticker in tickers:
        df_ticker = df_data[df_data['ticker'] == ticker].copy()
        if len(df_ticker) > 300:
            df_ticker = df_ticker.set_index('date').sort_index()
            data_dict[ticker] = df_ticker

    logger.info(
        "Prepared %d valid historical tickers (Warm-up from %s, Training samples from %s).",
        len(data_dict), FEATURE_HISTORY_START, TRAIN_START,
    )
    vnindex_df = data_dict.get('VNINDEX')
    return data_dict, vnindex_df


def simulate_t25_trade(cand_row: pd.Series, inst) -> Tuple[float, str, int]:
    """Simulates realistic HOSE T+2.5 trade execution."""
    breakeven_active = False

    # Day 1 (T+1): Locked
    high_1d = cand_row.get('fwd_high_1d', np.nan)
    if not pd.isna(high_1d) and high_1d >= inst.breakeven_trigger_pct:
        breakeven_active = True

    # Day 2 (T+2): Afternoon Window
    high_2d = cand_row.get('fwd_high_2d', np.nan)
    low_2d = cand_row.get('fwd_low_2d', np.nan)

    if not pd.isna(high_2d) and high_2d >= inst.breakeven_trigger_pct:
        breakeven_active = True

    if not pd.isna(low_2d):
        if breakeven_active:
            if low_2d <= 0.002:
                return 0.002, "T25_BREAKEVEN_D2", 2
        else:
            if low_2d <= inst.hard_stop_pct:
                return min(inst.hard_stop_pct, low_2d), "T25_HARD_STOP_D2", 2

    if not pd.isna(high_2d):
        if inst.take_profit_pct is not None and high_2d >= inst.take_profit_pct:
            return inst.take_profit_pct, "T25_SWING_TP_D2", 2
        if inst.take_profit_pct is None and high_2d >= 0.15:
            return 0.15, "T25_CLIMAX_D2", 2

    # Days 3 to 7: Active Trading
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

    final_ret = cand_row.get('forward_ret', 0.0)
    return (0.0 if pd.isna(final_ret) else final_ret), "T25_TIME_5D", 5


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


def print_walk_forward_report(df_trades: pd.DataFrame, blocked_count: int):
    m = calc_metrics(df_trades)
    if not m:
        logger.warning("No trades recorded during Walk-Forward.")
        return

    print("\n" + "=" * 115)
    print(" EXP-016: WALK-FORWARD TOURNAMENT (2020 - 2026) | T+2.5 REALISTIC HOSE ENGINE")
    print(" Feature Warm-up: 2014-2017 | Training Samples: From 2018-01-01 | Layer 0: Beneish Gate")
    print("=" * 115)
    print(f"\n[LAYER 0 SHIELD AUDIT] Total High-Risk Manipulator Trades Blocked: {blocked_count} trades across 7 years.")
    print("\n+------------------------+-------------------------+")
    print("| Metric                 | EXP-016 Realized Value  |")
    print("+------------------------+-------------------------+")
    print(f"| Total Trades           | {m['total_trades']:>23d} |")
    print(f"| Win Rate               | {m['win_rate']:>22.2f}% |")
    print(f"| Avg Win Return         | {m['avg_win']:>+22.2f}% |")
    print(f"| Avg Loss Return        | {m['avg_loss']:>+22.2f}% |")
    print(f"| Payoff Ratio (R:R)     | {m['payoff']:>22.2f}x |")
    print(f"| Expectancy/Trade       | {m['expectancy']:>+22.3f}% |")
    print(f"| Cumulative PnL         | {m['total_weighted_pnl']:>+22.1f}% |")
    print("+------------------------+-------------------------+")

    # Tier Breakdown
    print("\n--- TIER BREAKDOWN (WITH LAYER 0 BENEISH GATE) ---")
    for tier_name in ['TIER_A_PLUS', 'TIER_A']:
        t_sub = df_trades[df_trades['tier'] == tier_name]
        mt = calc_metrics(t_sub)
        if not mt:
            continue
        print(f"  [{tier_name:<11}] Trades: {mt['total_trades']:4d} | WR: {mt['win_rate']:5.2f}% | Avg Win: {mt['avg_win']:+5.2f}% | Avg Loss: {mt['avg_loss']:+5.2f}% | Payoff: {mt['payoff']:.2f}x | Exp: {mt['expectancy']:+.3f}%")

    # Year-by-Year Breakdown
    print("\n--- YEAR-BY-YEAR WALK-FORWARD PERFORMANCE ---")
    print(f"{'Year':>6} | {'Trades':>8} | {'Win Rate':>10} | {'Payoff':>8} | {'Expectancy':>12} | {'Exit Reasons':<30}")
    print("-" * 90)
    for y in range(2020, 2027):
        y_df = df_trades[df_trades['year'] == y]
        if y_df.empty:
            continue
        my = calc_metrics(y_df)
        reasons = y_df['exit_reason'].value_counts().to_dict()
        top_reasons = ", ".join([f"{k}:{v}" for k, v in list(reasons.items())[:2]])
        print(f"{y:>6} | {my['total_trades']:>8d} | {my['win_rate']:>9.2f}% | {my['payoff']:>7.2f}x | {my['expectancy']:>+11.3f}% | {top_reasons:<30}")
    print("=" * 115 + "\n")


def build_master_dataset(data_dict: Dict[str, pd.DataFrame]):
    """Generates features, lookaheads, graph contagion, and merges Layer 0 Beneish scores."""
    tickers = [t for t in data_dict.keys() if t != 'VNINDEX']

    # 1. Layer 0 Beneish M-Scores
    logger.info("Layer 0: Computing Beneish M-Scores from quarterly financial ratios...")
    df_beneish = beneish_engine.fetch_and_compute_scores(tickers)

    # 2. Base Features & T+2.5 Lookaheads
    logger.info("Generating Base Multi-Factor Features & T+2.5 Lookaheads (Warm-up from 2014)...")
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

    # 3. Graph Contagion & Lead-Lag Signals
    logger.info("Computing Graph Contagion & Lead-Lag Alpha Signals...")
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

    # 4. Cross-Sectional Alpha Target (5-Day Forward Alpha)
    logger.info("Computing Cross-Sectional NDCG@5 Target Labels...")
    master_df = CrossSectionalRanker.compute_forward_alpha_target(master_df, forward_window=5)

    # 5. Merge Beneish scores by point-in-time effective disclosure date (effective_date).
    logger.info("Merging Layer 0 Beneish M-Score by point-in-time effective disclosure date...")
    if not df_beneish.empty:
        df_beneish = df_beneish.sort_values('effective_date')
        merged_beneish_list = []
        for ticker, t_df in master_df.groupby('ticker'):
            b_sub = df_beneish[df_beneish['ticker'] == ticker]
            if not b_sub.empty:
                t_df_reset = t_df.reset_index()
                t_df_reset['date'] = pd.to_datetime(t_df_reset['date']).astype('datetime64[ns]')
                b_sub = b_sub.copy()
                b_sub['effective_date'] = pd.to_datetime(b_sub['effective_date']).astype('datetime64[ns]')
                m_asof = pd.merge_asof(
                    t_df_reset.sort_values('date'),
                    b_sub[['effective_date', 'beneish_m_score', 'is_manipulator']],
                    left_on='date',
                    right_on='effective_date',
                    direction='backward'
                )
                m_asof['is_manipulator'] = m_asof['is_manipulator'].fillna(0).astype(int)
                m_asof['beneish_m_score'] = m_asof['beneish_m_score'].fillna(-2.5)
                merged_beneish_list.append(m_asof.set_index('date'))
            else:
                t_df_copy = t_df.copy()
                t_df_copy['is_manipulator'] = 0
                t_df_copy['beneish_m_score'] = -2.5
                merged_beneish_list.append(t_df_copy)
        master_df = pd.concat(merged_beneish_list).sort_index()
    else:
        master_df['is_manipulator'] = 0
        master_df['beneish_m_score'] = -2.5

    fwd_cols = [c for c in master_df.columns if c.startswith('fwd_')]
    exclude_cols = (
        {'ticker', 'close', 'high', 'low', 'forward_ret', 'alpha_forward_ret',
         'rank_label', 'adtv20_bil', 'published_date', 'ratio_date', 'effective_date', 'beneish_m_score', 'is_manipulator'}
        | set(fwd_cols)
    )
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]

    return master_df, feature_cols


def run_walk_forward_evaluation(master_df: pd.DataFrame, feature_cols: list, vnindex_df: pd.DataFrame):
    """Executes 7-Year Walk-Forward backtest with training samples strictly from 2018-01-01."""
    logger.info("=== [START] Running 7-Year Walk-Forward Tournament (2020 - 2026) ===")
    logger.info(f"Enforcing: Warm-up from {FEATURE_HISTORY_START}, Training samples strictly >= {TRAIN_START}")

    test_years = range(2020, 2027)
    trade_log = []
    blocked_count = 0

    for ty in test_years:
        # Enforce training samples strictly start from TRAIN_START (2018-01-01)
        train_mask = (master_df.index >= TRAIN_START) & (master_df.index < f"{ty}-01-01")
        test_mask = (master_df.index >= f"{ty}-01-01") & (master_df.index <= f"{ty}-12-31")

        train_df = master_df[train_mask].copy()
        test_df = master_df[test_mask].copy()

        if train_df.empty or test_df.empty:
            continue

        # Fit temporary model for this fold
        fold_model = HybridStackingRanker()
        fold_model.fit(train_df, feature_cols)
        fold_res = fold_model.predict_hybrid_scores(test_df)
        test_df['pred_score'] = fold_res['pred_score']

        for dt, day_df in test_df.groupby(test_df.index):
            regime = dual_tier_engine.evaluate_macro_regime(vnindex_df, dt)

            # Baseline trade allocations (without Layer 0 gate) to accurately track blocked trades
            inst_no_gate = dual_tier_engine.generate_trade_allocations(
                candidate_scores=day_df[['ticker', 'pred_score', 'adtv20_bil']],
                regime=regime,
                top_k=3
            )

            # Layer 0 Gate: Clean universe only (is_manipulator == 0)
            clean_day_df = day_df[day_df['is_manipulator'] == 0].copy()
            inst_trades = dual_tier_engine.generate_trade_allocations(
                candidate_scores=clean_day_df[['ticker', 'pred_score', 'adtv20_bil']],
                regime=regime,
                top_k=3
            )

            tickers_no_gate = {i.ticker for i in inst_no_gate}
            tickers_gated = {i.ticker for i in inst_trades}
            blocked_count += len(tickers_no_gate - tickers_gated)

            for inst in inst_trades:
                cand_row = day_df[day_df['ticker'] == inst.ticker].iloc[0]
                ret, reason, days_held = simulate_t25_trade(cand_row, inst)
                trade_log.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker, 'tier': inst.tier,
                    'final_return': ret, 'is_win': 1 if ret > 0 else 0,
                    'weighted_pnl': ret * inst.target_weight_pct, 'exit_reason': reason
                })

    df_trades = pd.DataFrame(trade_log)
    print_walk_forward_report(df_trades, blocked_count)
    return df_trades


def train_and_export(export_path: str = None, run_walk_forward: bool = True):
    logger.info("=== [START] Training Production Hybrid Stacking Ranker (EXP-016 Standard) ===")
    if export_path is None:
        export_path = os.getenv("HYBRID_MODEL_EXPORT_PATH") or EXPORT_DEFAULT_PATH

    data_dict, vnindex_df = fetch_training_data()
    master_df, feature_cols = build_master_dataset(data_dict)

    # 1. Run Walk-Forward Evaluation if requested
    if run_walk_forward:
        run_walk_forward_evaluation(master_df, feature_cols, vnindex_df)

    # 2. Fit Final Production Model on all clean samples from TRAIN_START (2018-01-01) onwards
    clean_master_df = master_df[(master_df['is_manipulator'] == 0) & (master_df.index >= TRAIN_START)].copy()
    logger.info(
        "Fitting Final Production Model on %d clean samples (from %s to present, %d features)...",
        len(clean_master_df), TRAIN_START, len(feature_cols)
    )
    hybrid_stacking_ranker.fit(clean_master_df, feature_cols)

    # 3. Export Artifact
    os.makedirs(os.path.dirname(export_path), exist_ok=True)
    hybrid_stacking_ranker.save_model(export_path)
    file_size_kb = os.path.getsize(export_path) / 1024
    logger.info(f"=== [SUCCESS] Production Model Exported to {export_path} ({file_size_kb:.2f} KB) ===")
    return hybrid_stacking_ranker


def main():
    parser = argparse.ArgumentParser(description="EXP-016 Production Trainer & Walk-Forward Evaluator")
    parser.add_argument("--fast", action="store_true", help="Skip Walk-Forward evaluation and export production model directly")
    args = parser.parse_args()

    train_and_export(run_walk_forward=not args.fast)


if __name__ == "__main__":
    main()
