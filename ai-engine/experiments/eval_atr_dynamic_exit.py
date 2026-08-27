"""
EXP-014: ATR-Dynamic Exit Policy — Walk-Forward 7-Year Backtest (2020–2026)
============================================================================
Compares FIXED exit rules (current system) vs ATR-scaled ADAPTIVE exits
on the SAME entry signals to isolate the pure impact of exit policy.

Key Innovation:
  - Stop Loss:        -1.5 × ATR₁₄  (adapts to each stock's volatility)
  - Breakeven Lock:   +1.0 × ATR₁₄  (tighter for low-vol, wider for high-vol)
  - Trailing Stop:    High_watermark - 2.0 × ATR₁₄  (ride the trend until it breaks)
  - Take Profit:      +3.0 × ATR₁₄  for Tier A (scaled target)
  - Runner Mode:      Pure trailing for Tier A+ (no cap, ride until trend exhaustion)

Constraint: max_workers <= 3, n_jobs <= 4 (per user machine limits).
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
from app.domain.services.ml.dual_tier_sniper_engine import dual_tier_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────
# ATR Calculation
# ────────────────────────────────────────────────────
def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Compute Average True Range (ATR) as a percentage of close price.
    Returns ATR as a decimal ratio (e.g., 0.032 = 3.2%).
    """
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_abs = true_range.ewm(span=period, adjust=False).mean()

    # Convert to percentage of close price
    atr_pct = atr_abs / (close + 1e-8)
    return atr_pct


# ────────────────────────────────────────────────────
# Trade Simulation: FIXED Exit (Baseline — Current System)
# ────────────────────────────────────────────────────
def simulate_fixed_exit(cand_row, inst):
    """Simulate a trade with FIXED exit rules (current system baseline)."""
    breakeven_active = False
    final_return = 0.0
    exit_reason = "TIME_EXIT"
    exit_day = 7

    for d in range(1, 8):
        high_d = cand_row.get(f'fwd_high_{d}d', np.nan)
        low_d = cand_row.get(f'fwd_low_{d}d', np.nan)

        if pd.isna(high_d) or pd.isna(low_d):
            continue

        # Breakeven trigger at +2.5%
        if high_d >= 0.025:
            breakeven_active = True

        if breakeven_active:
            if low_d <= 0.002:
                return 0.002, "BREAKEVEN_LOCK", d
        else:
            if low_d <= inst.hard_stop_pct:
                return inst.hard_stop_pct, "HARD_STOP", d

        # Take profit for Swing Mode
        if inst.take_profit_pct is not None and high_d >= inst.take_profit_pct:
            return inst.take_profit_pct, "SWING_TP", d

        # Climax for Runner Mode
        if inst.take_profit_pct is None and high_d >= 0.15:
            return 0.15, "CLIMAX_TP", d

    # Time exit at day 5 (forward_ret)
    final_return = cand_row.get('forward_ret', 0.0)
    if pd.isna(final_return):
        final_return = 0.0
    return final_return, "TIME_5D", 5


# ------------------------------------------------
# Trade Simulation: HYBRID Exit v2 (Fixed Stop + ATR Profit)
# ------------------------------------------------
def simulate_hybrid_exit(cand_row, inst, atr_pct: float):
    """
    HYBRID Exit v2: Keep FIXED tight stop-loss, use ATR-scaled PROFIT targets.
    
    Risk side (FIXED - unchanged from current system):
        Hard Stop:      -3.0% (Tier A) / -3.5% (Tier A+) -- FIXED, no ATR
        Breakeven Lock: +2.5% triggers stop move to +0.2% -- FIXED
    
    Profit side (ATR-SCALED - new):
        Take Profit (Tier A):  max(+6.0%, +2.5 * ATR14)  -- expands in high-vol
        Trailing (Tier A+):    high_watermark - 1.5 * ATR14, floor at +0.5%
    """
    atr_clamped = np.clip(atr_pct, 0.010, 0.050)
    
    # Risk side: FIXED hard stop
    hard_stop = inst.hard_stop_pct  # -3.0% or -3.5%
    breakeven_trigger = 0.035       # Raised from +2.5% to +3.5% to allow breathing room
    
    # Profit side: ATR-SCALED
    trailing_distance = 2.0 * atr_clamped  # 2.0 ATR buffer for natural volatility
    
    if inst.take_profit_pct is not None:
        # Tier A: ATR-scaled take profit, floor at +7.0%
        take_profit = max(0.070, 2.5 * atr_clamped)
    else:
        take_profit = None  # Runner mode
    
    breakeven_active = False
    high_watermark = 0.0
    
    for d in range(1, 8):
        high_d = cand_row.get(f'fwd_high_{d}d', np.nan)
        low_d = cand_row.get(f'fwd_low_{d}d', np.nan)
        
        if pd.isna(high_d) or pd.isna(low_d):
            continue
        
        # Update high watermark
        if high_d > high_watermark:
            high_watermark = high_d
        
        # Breakeven trigger (at +3.5%)
        if high_watermark >= breakeven_trigger:
            breakeven_active = True
        
        if breakeven_active:
            # Runner Mode (Tier A+): ATR-trailing from high watermark
            if take_profit is None:
                trailing_stop_level = high_watermark - trailing_distance
                trailing_stop_level = max(trailing_stop_level, 0.010)  # Lock at least +1.0%
                
                if low_d <= trailing_stop_level:
                    exit_ret = max(trailing_stop_level, 0.010)
                    return exit_ret, "HYBRID_TRAILING", d
            else:
                # Tier A: lock +1.0% profit once triggered
                if low_d <= 0.010:
                    return 0.010, "HYBRID_BE_LOCK", d
        else:
            # Hard stop: FIXED
            if low_d <= hard_stop:
                return hard_stop, "HYBRID_HARD_STOP", d
        
        # Take profit (ATR-scaled for Tier A)
        if take_profit is not None and high_d >= take_profit:
            return take_profit, "HYBRID_ATR_TP", d
        
        # Climax exit for Runner Mode
        if take_profit is None and high_d >= 0.20:
            return 0.20, "HYBRID_CLIMAX", d
    
    # Time exit
    final_return = cand_row.get('forward_ret', 0.0)
    if pd.isna(final_return):
        final_return = 0.0
    return final_return, "HYBRID_TIME_5D", 5

# ────────────────────────────────────────────────────
# Trade Simulation: ATR-DYNAMIC Exit (New System)
# ────────────────────────────────────────────────────
def simulate_atr_dynamic_exit(cand_row, inst, atr_pct: float):
    """
    Simulate a trade with ATR-scaled ADAPTIVE exit rules.

    Parameters
    ----------
    cand_row : pd.Series — row with fwd_high_Xd, fwd_low_Xd, fwd_ret_Xd
    inst : TradeInstruction — trade instruction from Dual-Tier engine
    atr_pct : float — ATR₁₄ as decimal (e.g. 0.032 = 3.2%)

    Exit Rules (ATR-Scaled):
        Hard Stop:      -1.5 × ATR₁₄  (floor at -5.0%, cap at -1.5%)
        Breakeven Lock: when high reaches +1.0 × ATR₁₄ → move stop to +0.2%
        Trailing Stop:  high_watermark - 2.0 × ATR₁₄ (Tier A+ only)
        Take Profit:    +3.0 × ATR₁₄  (Tier A Swing Lock, floor at +3.5%)
    """
    # Clamp ATR to reasonable bounds (1.0% – 5.0%)
    atr_clamped = np.clip(atr_pct, 0.010, 0.050)

    # Dynamic thresholds
    hard_stop = -1.5 * atr_clamped
    hard_stop = max(hard_stop, -0.050)   # Never wider than -5.0%
    hard_stop = min(hard_stop, -0.015)   # Never tighter than -1.5%

    breakeven_trigger = 1.0 * atr_clamped
    breakeven_trigger = max(breakeven_trigger, 0.015)  # Min +1.5%

    trailing_distance = 2.0 * atr_clamped

    # Tier A: scaled take profit
    if inst.take_profit_pct is not None:
        take_profit = 3.0 * atr_clamped
        take_profit = max(take_profit, 0.035)  # Floor at +3.5%
    else:
        take_profit = None  # Runner mode — pure trailing

    breakeven_active = False
    high_watermark = 0.0

    for d in range(1, 8):
        high_d = cand_row.get(f'fwd_high_{d}d', np.nan)
        low_d = cand_row.get(f'fwd_low_{d}d', np.nan)

        if pd.isna(high_d) or pd.isna(low_d):
            continue

        # Update high watermark
        if high_d > high_watermark:
            high_watermark = high_d

        # === Breakeven trigger ===
        if high_watermark >= breakeven_trigger:
            breakeven_active = True

        # === Check exits ===
        if breakeven_active:
            # Trailing stop from high watermark (for Runner Mode / Tier A+)
            if take_profit is None:
                trailing_stop_level = high_watermark - trailing_distance
                trailing_stop_level = max(trailing_stop_level, 0.002)  # Never below breakeven

                if low_d <= trailing_stop_level:
                    exit_ret = max(trailing_stop_level, 0.002)
                    return exit_ret, "ATR_TRAILING_STOP", d
            else:
                # For Tier A: simple breakeven lock
                if low_d <= 0.002:
                    return 0.002, "ATR_BREAKEVEN_LOCK", d
        else:
            # Hard stop (ATR-scaled)
            if low_d <= hard_stop:
                return hard_stop, "ATR_HARD_STOP", d

        # Take profit trigger (Tier A)
        if take_profit is not None and high_d >= take_profit:
            return take_profit, "ATR_SWING_TP", d

        # Runner mode: no cap, let trailing handle it (already handled above)

    # Time exit at day 5
    final_return = cand_row.get('forward_ret', 0.0)
    if pd.isna(final_return):
        final_return = 0.0
    return final_return, "ATR_TIME_5D", 5


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
def run_atr_exit_backtest():
    data_dict = fetch_data()
    vnindex_df = data_dict.get('VNINDEX')

    # ── Pre-compute ATR₁₄ for each ticker ──
    logger.info("Computing ATR₁₄ for all tickers...")
    atr_dict = {}
    for ticker, df in data_dict.items():
        if ticker == 'VNINDEX':
            continue
        atr_dict[ticker] = compute_atr(df, period=14)

    # ── Generate features ──
    logger.info("Generating Base Feature Set (50 features + Graph Contagion)...")
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

            # Inject ATR₁₄ into feature frame for later use in exit simulation
            if ticker in atr_dict:
                feats['atr_14_pct'] = atr_dict[ticker]

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
         'rank_label', 'adtv20_bil', 'atr_14_pct'}
        | set(fwd_cols)
    )
    feature_cols = [c for c in master_df.columns if c not in exclude_cols]

    test_years = range(2020, 2027)
    trade_log_fixed = []
    trade_log_atr = []
    trade_log_hybrid = []

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

        for dt, day_df in test_df.groupby(test_df.index):
            regime = dual_tier_engine.evaluate_macro_regime(vnindex_df, dt)
            instructions = dual_tier_engine.generate_trade_allocations(
                candidate_scores=day_df[['ticker', 'pred_score', 'adtv20_bil']],
                regime=regime,
                top_k=3
            )

            for inst in instructions:
                cand_row = day_df[day_df['ticker'] == inst.ticker].iloc[0]

                # ── Method A: FIXED Exit (Baseline) ──
                ret_fixed, reason_fixed, day_fixed = simulate_fixed_exit(cand_row, inst)
                trade_log_fixed.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker,
                    'tier': inst.tier, 'regime': regime,
                    'z_score': inst.z_score,
                    'weight': inst.target_weight_pct,
                    'final_return': ret_fixed,
                    'is_win': 1 if ret_fixed > 0 else 0,
                    'weighted_pnl': ret_fixed * inst.target_weight_pct,
                    'exit_reason': reason_fixed,
                    'exit_day': day_fixed,
                })

                # ── Method B: ATR-DYNAMIC Exit (New) ──
                atr_val = cand_row.get('atr_14_pct', 0.025)
                if pd.isna(atr_val):
                    atr_val = 0.025  # Fallback to ~2.5% if missing
                ret_atr, reason_atr, day_atr = simulate_atr_dynamic_exit(cand_row, inst, atr_val)
                trade_log_atr.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker,
                    'tier': inst.tier, 'regime': regime,
                    'z_score': inst.z_score,
                    'weight': inst.target_weight_pct,
                    'final_return': ret_atr,
                    'is_win': 1 if ret_atr > 0 else 0,
                    'weighted_pnl': ret_atr * inst.target_weight_pct,
                    'exit_reason': reason_atr,
                    'exit_day': day_atr,
                    'atr_14_pct': atr_val,
                })

                # -- Method C: HYBRID Exit v2 (Fixed Stop + ATR Profit) --
                ret_hyb, reason_hyb, day_hyb = simulate_hybrid_exit(cand_row, inst, atr_val)
                trade_log_hybrid.append({
                    'year': ty, 'date': dt, 'ticker': inst.ticker,
                    'tier': inst.tier, 'regime': regime,
                    'z_score': inst.z_score,
                    'weight': inst.target_weight_pct,
                    'final_return': ret_hyb,
                    'is_win': 1 if ret_hyb > 0 else 0,
                    'weighted_pnl': ret_hyb * inst.target_weight_pct,
                    'exit_reason': reason_hyb,
                    'exit_day': day_hyb,
                    'atr_14_pct': atr_val,
                })

    df_fixed = pd.DataFrame(trade_log_fixed)
    df_atr = pd.DataFrame(trade_log_atr)
    df_hybrid = pd.DataFrame(trade_log_hybrid)
    return df_fixed, df_atr, df_hybrid


# ────────────────────────────────────────────────────
# Performance Report Printer
# ────────────────────────────────────────────────────
def calc_metrics(tdf: pd.DataFrame) -> dict:
    """Calculate summary metrics for a trade log DataFrame."""
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
    total_weighted_pnl = tdf['weighted_pnl'].sum() * 100  # In percentage points
    return {
        'total_trades': total,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'payoff': payoff,
        'avg_ret': avg_ret,
        'expectancy': expectancy,
        'total_weighted_pnl': total_weighted_pnl,
    }


def print_head_to_head(df_fixed: pd.DataFrame, df_atr: pd.DataFrame, df_hybrid: pd.DataFrame):
    """Print 3-way side-by-side comparison: Fixed vs ATR-Pure vs HYBRID v2."""
    m_f = calc_metrics(df_fixed)
    m_a = calc_metrics(df_atr)
    m_h = calc_metrics(df_hybrid)

    print("\n" + "=" * 115)
    print(" EXP-014: 3-WAY EXIT POLICY TOURNAMENT -- FIXED vs ATR-PURE vs HYBRID v2")
    print(" Walk-Forward 7-Year Backtest (2020 - 2026) | Same Entry Signals | HOSE Spot Equity")
    print("=" * 115)

    # -- Overall Comparison --
    print("\n+------------------------+-------------------+-------------------+-------------------+------------+")
    print("| Metric                 | (A) FIXED Exit    | (B) ATR-Pure      | (C) HYBRID v2     | Delta (C-A)|")
    print("+------------------------+-------------------+-------------------+-------------------+------------+")

    rows = [
        ("Total Trades",        f"{m_f['total_trades']:>17d}", f"{m_a['total_trades']:>17d}", f"{m_h['total_trades']:>17d}", "--"),
        ("Win Rate",            f"{m_f['win_rate']:>16.2f}%",   f"{m_a['win_rate']:>16.2f}%",   f"{m_h['win_rate']:>16.2f}%",   f"{m_h['win_rate'] - m_f['win_rate']:+.2f}%"),
        ("Avg Win Return",      f"{m_f['avg_win']:>+16.2f}%",  f"{m_a['avg_win']:>+16.2f}%",  f"{m_h['avg_win']:>+16.2f}%",  f"{m_h['avg_win'] - m_f['avg_win']:+.2f}%"),
        ("Avg Loss Return",     f"{m_f['avg_loss']:>+16.2f}%", f"{m_a['avg_loss']:>+16.2f}%", f"{m_h['avg_loss']:>+16.2f}%", f"{m_h['avg_loss'] - m_f['avg_loss']:+.2f}%"),
        ("Payoff Ratio (R:R)",  f"{m_f['payoff']:>16.2f}x",    f"{m_a['payoff']:>16.2f}x",    f"{m_h['payoff']:>16.2f}x",    f"{m_h['payoff'] - m_f['payoff']:+.2f}x"),
        ("Expectancy/Trade",    f"{m_f['expectancy']:>+16.3f}%",f"{m_a['expectancy']:>+16.3f}%",f"{m_h['expectancy']:>+16.3f}%",f"{m_h['expectancy'] - m_f['expectancy']:+.3f}%"),
        ("Cumulative PnL",      f"{m_f['total_weighted_pnl']:>+16.1f}%", f"{m_a['total_weighted_pnl']:>+16.1f}%", f"{m_h['total_weighted_pnl']:>+16.1f}%", f"{m_h['total_weighted_pnl'] - m_f['total_weighted_pnl']:+.1f}%"),
    ]
    for label, v1, v2, v3, delta in rows:
        print(f"| {label:<22} | {v1} | {v2} | {v3} | {delta:>10} |")

    print("+------------------------+-------------------+-------------------+-------------------+------------+")

    # -- Tier Breakdown --
    print("\n--- TIER BREAKDOWN (HYBRID v2 vs FIXED) ---")
    for tier_name in ['TIER_A_PLUS', 'TIER_A']:
        mf = calc_metrics(df_fixed[df_fixed['tier'] == tier_name])
        mh = calc_metrics(df_hybrid[df_hybrid['tier'] == tier_name])
        if not mf or not mh:
            continue
        print(f"\n  [{tier_name}]")
        print(f"    FIXED  -> Trades: {mf['total_trades']:4d} | WR: {mf['win_rate']:5.2f}% | Avg Win: {mf['avg_win']:+5.2f}% | Avg Loss: {mf['avg_loss']:+5.2f}% | Payoff: {mf['payoff']:.2f}x | Exp: {mf['expectancy']:+.3f}%")
        print(f"    HYBRID -> Trades: {mh['total_trades']:4d} | WR: {mh['win_rate']:5.2f}% | Avg Win: {mh['avg_win']:+5.2f}% | Avg Loss: {mh['avg_loss']:+5.2f}% | Payoff: {mh['payoff']:.2f}x | Exp: {mh['expectancy']:+.3f}%")

    # -- Year-by-Year Comparison (HYBRID v2 vs FIXED) --
    print("\n--- YEAR-BY-YEAR COMPARISON (HYBRID v2 vs FIXED) ---")
    print(f"{'Year':>6} | {'FIXED WR':>10} {'FIXED R:R':>10} {'FIXED Exp':>10} | {'HYB WR':>10} {'HYB R:R':>10} {'HYB Exp':>10} | {'dWR':>8} {'dR:R':>8} {'dExp':>10}")
    print("-" * 115)
    for y in range(2020, 2027):
        yf = df_fixed[df_fixed['year'] == y]
        yh = df_hybrid[df_hybrid['year'] == y]
        if yf.empty or yh.empty:
            continue
        mf = calc_metrics(yf)
        mh = calc_metrics(yh)
        delta_wr = mh['win_rate'] - mf['win_rate']
        delta_rr = mh['payoff'] - mf['payoff']
        delta_exp = mh['expectancy'] - mf['expectancy']
        print(f"{y:>6} | {mf['win_rate']:>9.2f}% {mf['payoff']:>9.2f}x {mf['expectancy']:>+9.3f}% | {mh['win_rate']:>9.2f}% {mh['payoff']:>9.2f}x {mh['expectancy']:>+9.3f}% | {delta_wr:>+7.2f}% {delta_rr:>+7.2f}x {delta_exp:>+9.3f}%")

    # -- Exit Reason Distribution (HYBRID system) --
    print("\n--- HYBRID v2 EXIT REASON DISTRIBUTION ---")
    if 'exit_reason' in df_hybrid.columns:
        reason_counts = df_hybrid['exit_reason'].value_counts()
        reason_wr = df_hybrid.groupby('exit_reason')['is_win'].mean() * 100
        reason_ret = df_hybrid.groupby('exit_reason')['final_return'].mean() * 100
        for reason in reason_counts.index:
            cnt = reason_counts[reason]
            pct = cnt / len(df_hybrid) * 100
            wr = reason_wr.get(reason, 0)
            ret = reason_ret.get(reason, 0)
            print(f"  {reason:<22} | Count: {cnt:4d} ({pct:5.1f}%) | WR: {wr:5.1f}% | Avg Ret: {ret:+5.2f}%")

    print("\n" + "=" * 115)


if __name__ == "__main__":
    df_fixed, df_atr, df_hybrid = run_atr_exit_backtest()
    print_head_to_head(df_fixed, df_atr, df_hybrid)

