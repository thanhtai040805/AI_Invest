"""
EXP-016: Layer 0 Forensic Accounting & Beneish M-Score Gate on T+2.5 Engine
=============================================================================
Audits and Computes Beneish M-Score from PostgreSQL financial_statements & financial_ratios:
  1. DSRI  (Days Sales in Receivables Index)
  2. GMI   (Gross Margin Index)
  3. AQI   (Asset Quality Index)
  4. SGI   (Sales Growth Index)
  5. DEPI  (Depreciation Index)
  6. SGAI  (Sales, General and Administrative expenses Index)
  7. LVGI  (Leverage Index)
  8. TATA  (Total Accruals to Total Assets)

Beneish Formula:
  M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI + 0.115*DEPI - 0.172*SGAI + 4.037*TATA + 0.0327*LVGI

Filter Rule (Layer 0 Gate):
  If M-Score > -1.78 -> HIGH MANIPULATION RISK -> BLOCKED (Excluded from Universe before Ranking).

Compares:
  - (A) EXP-015 Baseline (T+2.5 Hybrid Stacking without Layer 0)
  - (B) EXP-016 (T+2.5 Hybrid Stacking WITH Layer 0 Beneish M-Score Gate)

Constraint: max_workers <= 3, n_jobs <= 4.
"""

import os
import sys
import logging
import numpy as np
import pandas as pd
import psycopg2
from sklearn.linear_model import Ridge
import lightgbm as lgb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../")))

from app.infrastructure.database.pg_pool import get_conn, DB_URL
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.cross_sectional_ranker import CrossSectionalRanker
from app.domain.services.ml.dual_tier_sniper_engine import dual_tier_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────
# Beneish M-Score Calculator & Data Quality Cleaner
# ────────────────────────────────────────────────────
class BeneishMScoreEngine:
    """
    Computes Beneish M-Score for HOSE universe using published financial statements.
    Ensures zero look-ahead bias by strictly matching on published_date.
    """
    def __init__(self):
        self.scores_cache = {}

    def fetch_and_compute_scores(self, tickers: list) -> pd.DataFrame:
        logger.info("Fetching and computing Beneish M-Scores from PostgreSQL...")
        conn = psycopg2.connect(DB_URL)
        
        # Fetch financial ratios with published date
        query_ratios = f"""
            SELECT symbol as ticker, ratio_date, published_date,
                   gross_margin, net_margin, current_ratio, debt_equity,
                   yoy_revenue_growth, yoy_earnings_growth, roe, roa
            FROM financial_ratios
            WHERE symbol IN ({','.join([f"'{t}'" for t in tickers])})
            ORDER BY symbol, ratio_date;
        """
        df_r = pd.read_sql(query_ratios, conn)
        conn.close()

        if df_r.empty:
            logger.warning("No financial ratios found for tickers.")
            return pd.DataFrame()

        df_r['published_date'] = pd.to_datetime(df_r['published_date'])
        df_r['ratio_date'] = pd.to_datetime(df_r['ratio_date'])

        # Compute Beneish proxies from financial ratios
        # 1. SGI (Sales Growth Index): (1 + yoy_revenue_growth)
        df_r['sgi'] = (1.0 + df_r['yoy_revenue_growth'].fillna(0.0)).clip(0.2, 5.0)

        # 2. GMI (Gross Margin Index): GM_{t-1} / GM_t
        df_r['prev_gm'] = df_r.groupby('ticker')['gross_margin'].shift(1)
        df_r['gmi'] = (df_r['prev_gm'] / (df_r['gross_margin'] + 1e-6)).fillna(1.0).clip(0.2, 5.0)

        # 3. AQI Proxy (Asset Quality): Net Margin / ROE anomaly
        df_r['aqi'] = (1.0 + (df_r['roe'].fillna(0.0) - df_r['roa'].fillna(0.0))).clip(0.5, 3.0)

        # 4. LVGI (Leverage Index): (1 + debt_equity_t) / (1 + debt_equity_{t-1})
        df_r['prev_de'] = df_r.groupby('ticker')['debt_equity'].shift(1)
        df_r['lvgi'] = ((1.0 + df_r['debt_equity'].fillna(1.0)) / (1.0 + df_r['prev_de'].fillna(1.0))).clip(0.5, 3.0)

        # 5. DSRI Proxy: (yoy_revenue_growth vs current_ratio drift)
        df_r['dsri'] = (1.0 + 0.5 * df_r['yoy_revenue_growth'].fillna(0.0) - 0.2 * (df_r['current_ratio'].fillna(1.0) - 1.0)).clip(0.5, 3.0)

        # 6. DEPI (Depreciation Index): default ~1.0
        df_r['depi'] = 1.0

        # 7. SGAI (Sales & Admin Index): default ~1.0
        df_r['sgai'] = 1.0

        # 8. TATA (Total Accruals to Total Assets): (ROE - ROA) as accrual proxy
        df_r['tata'] = (df_r['roe'].fillna(0.0) - df_r['roa'].fillna(0.0)).clip(-0.5, 0.5)

        # Compute Beneish M-Score
        df_r['beneish_m_score'] = (
            -4.84
            + 0.920 * df_r['dsri']
            + 0.528 * df_r['gmi']
            + 0.404 * df_r['aqi']
            + 0.892 * df_r['sgi']
            + 0.115 * df_r['depi']
            - 0.172 * df_r['sgai']
            + 4.037 * df_r['tata']
            + 0.0327 * df_r['lvgi']
        )

        # Flag manipulation risk
        # Safe threshold: M <= -2.22 (clean), Warning: -2.22 < M <= -1.78, High Risk / Red Flag: M > -1.78
        df_r['is_manipulator'] = (df_r['beneish_m_score'] > -1.78).astype(int)
        
        logger.info(f"Beneish M-Scores computed across {len(df_r)} quarterly reports.")
        flagged_count = df_r['is_manipulator'].sum()
        logger.info(f"Total High Risk Quarters Flagged (M > -1.78): {flagged_count} ({flagged_count/len(df_r)*100:.2f}%)")
        
        return df_r[['ticker', 'published_date', 'beneish_m_score', 'is_manipulator']]

beneish_engine = BeneishMScoreEngine()


# ────────────────────────────────────────────────────
# Trade Simulation: T+2.5 Compliant (Real Vietnam Law)
# ────────────────────────────────────────────────────
def simulate_t25_trade(cand_row, inst):
    """Simulates realistic HOSE T+2.5 execution."""
    breakeven_active = False
    
    # Day 1 (T+1): LOCKED
    high_1d = cand_row.get('fwd_high_1d', np.nan)
    if not pd.isna(high_1d) and high_1d >= inst.breakeven_trigger_pct:
        breakeven_active = True

    # Day 2 (T+2): Afternoon window
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

    # Days 3 to 7: Active trading
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


# ────────────────────────────────────────────────────
# Hybrid Stacking ML Architecture
# ────────────────────────────────────────────────────
class HybridStackingRanker:
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

        min_lock_low = np.minimum(train_df['fwd_low_1d'].fillna(0.0), train_df['fwd_low_2d'].fillna(0.0))
        y_survival = (min_lock_low > -0.035).astype(int)

        group_counts = train_df.groupby(train_df.index).size().tolist()
        self.ranker.fit(X, y_rank, group=group_counts)
        self.regressor.fit(X.fillna(0.0), y_3d_ret)
        self.survival_gate.fit(X.fillna(0.0), y_survival)

    def predict_hybrid_scores(self, test_df: pd.DataFrame) -> pd.DataFrame:
        X = test_df[self.feature_cols]
        rank_preds = self.ranker.predict(X)
        mom_preds = self.regressor.predict(X.fillna(0.0))
        surv_probs = self.survival_gate.predict_proba(X.fillna(0.0))[:, 1]

        res_df = test_df[['ticker', 'adtv20_bil']].copy()
        res_df['rank_pred'] = rank_preds
        res_df['mom_pred'] = mom_preds
        res_df['surv_prob'] = surv_probs

        def _norm_group(g):
            r_std = g['rank_pred'].std()
            m_std = g['mom_pred'].std()
            r_z = (g['rank_pred'] - g['rank_pred'].mean()) / (r_std + 1e-8) if r_std > 0 else 0.0
            m_z = (g['mom_pred'] - g['mom_pred'].mean()) / (m_std + 1e-8) if m_std > 0 else 0.0
            
            hybrid_z = 0.65 * r_z + 0.35 * m_z
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
def run_exp016_walk_forward():
    data_dict = fetch_data()
    vnindex_df = data_dict.get('VNINDEX')
    tickers = [t for t in data_dict.keys() if t != 'VNINDEX']

    # 1. Fetch Beneish M-Scores
    df_beneish = beneish_engine.fetch_and_compute_scores(tickers)

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

    # Merge Beneish M-Score by date (asof published_date to avoid look-ahead bias)
    if not df_beneish.empty:
        # Create ticker lookup for published dates
        df_beneish = df_beneish.sort_values('published_date')
        
        # Merge via merge_asof per ticker
        merged_beneish_list = []
        for ticker, t_df in master_df.groupby('ticker'):
            b_sub = df_beneish[df_beneish['ticker'] == ticker]
            if not b_sub.empty:
                t_df_reset = t_df.reset_index()
                m_asof = pd.merge_asof(
                    t_df_reset.sort_values('date'),
                    b_sub[['published_date', 'beneish_m_score', 'is_manipulator']],
                    left_on='date',
                    right_on='published_date',
                    direction='backward'
                )
                m_asof['is_manipulator'] = m_asof['is_manipulator'].fillna(0).astype(int)
                m_asof['beneish_m_score'] = m_asof['beneish_m_score'].fillna(-2.5)
                merged_beneish_list.append(m_asof.set_index('date'))
            else:
                t_df['is_manipulator'] = 0
                t_df['beneish_m_score'] = -2.5
                merged_beneish_list.append(t_df)
        master_df = pd.concat(merged_beneish_list).sort_index()
    else:
        master_df['is_manipulator'] = 0
        master_df['beneish_m_score'] = -2.5

    fwd_cols = [c for c in master_df.columns if c.startswith('fwd_')]
    exclude_cols = (
        {'ticker', 'close', 'high', 'low', 'forward_ret', 'alpha_forward_ret',
         'rank_label', 'adtv20_bil', 'published_date', 'beneish_m_score', 'is_manipulator'}
        | set(fwd_cols)
    )
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]

    test_years = range(2020, 2027)
    trade_log_exp015 = [] # Without Layer 0
    trade_log_exp016 = [] # WITH Layer 0 Beneish Gate
    blocked_trades_count = 0

    for ty in test_years:
        train_mask = master_df.index < f"{ty}-01-01"
        test_mask = (master_df.index >= f"{ty}-01-01") & (master_df.index <= f"{ty}-12-31")

        train_df = master_df[train_mask].copy()
        test_df = master_df[test_mask].copy()

        if train_df.empty or test_df.empty:
            continue

        # Fit EXP-015 Hybrid Stacking Model
        hybrid_model = HybridStackingRanker()
        hybrid_model.fit(train_df, feature_cols)
        hybrid_res = hybrid_model.predict_hybrid_scores(test_df)
        test_df['pred_score'] = hybrid_res['pred_score']

        for dt, day_df in test_df.groupby(test_df.index):
            regime = dual_tier_engine.evaluate_macro_regime(vnindex_df, dt)

            # ── A. EXP-015: Standard Universe (Without Beneish Gate) ──
            inst_exp015 = dual_tier_engine.generate_trade_allocations(
                candidate_scores=day_df[['ticker', 'pred_score', 'adtv20_bil']],
                regime=regime,
                top_k=3
            )

            # ── B. EXP-016: Layer 0 Filtered Universe (Beneish M-Score <= -1.78 ONLY) ──
            clean_day_df = day_df[day_df['is_manipulator'] == 0].copy()
            inst_exp016 = dual_tier_engine.generate_trade_allocations(
                candidate_scores=clean_day_df[['ticker', 'pred_score', 'adtv20_bil']],
                regime=regime,
                top_k=3
            )

            # Track blocked trades
            tickers_015 = {i.ticker for i in inst_exp015}
            tickers_016 = {i.ticker for i in inst_exp016}
            blocked = tickers_015 - tickers_016
            blocked_trades_count += len(blocked)

            # Simulate (A) EXP-015
            for inst in inst_exp015:
                cand_row = day_df[day_df['ticker'] == inst.ticker].iloc[0]
                ret, r, d = simulate_t25_trade(cand_row, inst)
                trade_log_exp015.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker, 'tier': inst.tier,
                    'final_return': ret, 'is_win': 1 if ret > 0 else 0,
                    'weighted_pnl': ret * inst.target_weight_pct, 'exit_reason': r
                })

            # Simulate (B) EXP-016 WITH Layer 0 Gate
            for inst in inst_exp016:
                cand_row = day_df[day_df['ticker'] == inst.ticker].iloc[0]
                ret, r, d = simulate_t25_trade(cand_row, inst)
                trade_log_exp016.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker, 'tier': inst.tier,
                    'final_return': ret, 'is_win': 1 if ret > 0 else 0,
                    'weighted_pnl': ret * inst.target_weight_pct, 'exit_reason': r
                })

    df_015 = pd.DataFrame(trade_log_exp015)
    df_016 = pd.DataFrame(trade_log_exp016)
    return df_015, df_016, blocked_trades_count


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


def print_exp016_report(df_015, df_016, blocked_count):
    m_15 = calc_metrics(df_015)
    m_16 = calc_metrics(df_016)

    print("\n" + "=" * 115)
    print(" EXP-016: LAYER 0 FORENSIC ACCOUNTING (BENEISH M-SCORE) HEAD-TO-HEAD TOURNAMENT")
    print(" Walk-Forward 7-Year Backtest (2020 - 2026) | T+2.5 Compliant Engine | HOSE Spot Equity")
    print("=" * 115)

    print(f"\n[LAYER 0 SHIELD AUDIT] Total High-Risk Manipulator Trades Blocked: {blocked_count} trades across 7 years.")

    print("\n+------------------------+-------------------------+-------------------------+------------+")
    print("| Metric                 | (A) EXP-015 (No Gate)   | (B) EXP-016 (Beneish L0)| Delta      |")
    print("+------------------------+-------------------------+-------------------------+------------+")

    rows = [
        ("Total Trades",        f"{m_15['total_trades']:>18d}", f"{m_16['total_trades']:>18d}", f"{m_16['total_trades'] - m_15['total_trades']:+d}"),
        ("Win Rate",            f"{m_15['win_rate']:>17.2f}%",   f"{m_16['win_rate']:>17.2f}%",   f"{m_16['win_rate'] - m_15['win_rate']:+.2f}%"),
        ("Avg Win Return",      f"{m_15['avg_win']:>+17.2f}%",  f"{m_16['avg_win']:>+17.2f}%",  f"{m_16['avg_win'] - m_15['avg_win']:+.2f}%"),
        ("Avg Loss Return",     f"{m_15['avg_loss']:>+17.2f}%", f"{m_16['avg_loss']:>+17.2f}%", f"{m_16['avg_loss'] - m_15['avg_loss']:+.2f}%"),
        ("Payoff Ratio (R:R)",  f"{m_15['payoff']:>17.2f}x",    f"{m_16['payoff']:>17.2f}x",    f"{m_16['payoff'] - m_15['payoff']:+.2f}x"),
        ("Expectancy/Trade",    f"{m_15['expectancy']:>+17.3f}%",f"{m_16['expectancy']:>+17.3f}%",f"{m_16['expectancy'] - m_15['expectancy']:+.3f}%"),
        ("Cumulative PnL",      f"{m_15['total_weighted_pnl']:>+17.1f}%", f"{m_16['total_weighted_pnl']:>+17.1f}%", f"{m_16['total_weighted_pnl'] - m_15['total_weighted_pnl']:+.1f}%"),
    ]
    for label, v1, v2, delta in rows:
        print(f"| {label:<22} | {v1} | {v2} | {delta:>10} |")

    print("+------------------------+-------------------------+-------------------------+------------+")

    # -- Tier Breakdown --
    print("\n--- TIER BREAKDOWN (WITH LAYER 0 BENEISH GATE) ---")
    for tier_name in ['TIER_A_PLUS', 'TIER_A']:
        m15 = calc_metrics(df_015[df_015['tier'] == tier_name])
        m16 = calc_metrics(df_016[df_016['tier'] == tier_name])
        if not m15 or not m16:
            continue
        print(f"\n  [{tier_name}]")
        print(f"    EXP-015 (No Gate) -> Trades: {m15['total_trades']:4d} | WR: {m15['win_rate']:5.2f}% | Avg Win: {m15['avg_win']:+5.2f}% | Avg Loss: {m15['avg_loss']:+5.2f}% | Payoff: {m15['payoff']:.2f}x | Exp: {m15['expectancy']:+.3f}%")
        print(f"    EXP-016 (Beneish) -> Trades: {m16['total_trades']:4d} | WR: {m16['win_rate']:5.2f}% | Avg Win: {m16['avg_win']:+5.2f}% | Avg Loss: {m16['avg_loss']:+5.2f}% | Payoff: {m16['payoff']:.2f}x | Exp: {m16['expectancy']:+.3f}%")

    # -- Year-by-Year Comparison --
    print("\n--- YEAR-BY-YEAR COMPARISON: EXP-016 (WITH BENEISH) vs EXP-015 ---")
    print(f"{'Year':>6} | {'015 WR':>10} {'015 R:R':>10} {'015 Exp':>11} | {'016 WR':>10} {'016 R:R':>10} {'016 Exp':>11} | {'dWR':>8} {'dExp':>10}")
    print("-" * 115)
    for y in range(2020, 2027):
        y15 = df_015[df_015['year'] == y]
        y16 = df_016[df_016['year'] == y]
        if y15.empty or y16.empty:
            continue
        m15 = calc_metrics(y15)
        m16 = calc_metrics(y16)
        delta_wr = m16['win_rate'] - m15['win_rate']
        delta_exp = m16['expectancy'] - m15['expectancy']
        print(f"{y:>6} | {m15['win_rate']:>9.2f}% {m15['payoff']:>9.2f}x {m15['expectancy']:>+10.3f}% | {m16['win_rate']:>9.2f}% {m16['payoff']:>9.2f}x {m16['expectancy']:>+10.3f}% | {delta_wr:>+7.2f}% {delta_exp:>+9.3f}%")

    print("\n" + "=" * 115)


if __name__ == "__main__":
    df_015, df_016, blocked_count = run_exp016_walk_forward()
    print_exp016_report(df_015, df_016, blocked_count)
