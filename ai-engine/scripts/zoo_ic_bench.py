#!/usr/bin/env python3
"""Zoo IC benchmark — IC test for all GTJA191 + Alpha101 factors on VN market.

Loads from DB, runs each zoo alpha, computes IC with VN-specific methodology.
"""
import math, time, logging, warnings, sys, importlib
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from scipy.stats import spearmanr, t as t_dist

sys.path.insert(0, ".")
from app.services.pg_pool import DB_URL
from app.brain.quant.factors.registry import get_default_registry

logging.basicConfig(level=logging.WARNING)
warnings.filterwarnings("ignore")

VN_CONSTRAINTS = {"min_stocks": 20, "min_value_bn": 5.0, "holding": 20, "min_dates": 10}

def load_db_panel(start, end):
    """Load OHLCV from DB into wide-format panel dict for zoo alphas."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol, time::date as dt, open, high, low, close, volume "
        "FROM ohlcv WHERE time::date >= %s AND time::date <= %s ORDER BY symbol, time",
        (start, end))
    records = defaultdict(list)
    for sym, dt, op, hi, lo, cl, vol in cur.fetchall():
        records[sym].append({"date": dt, "open": float(op or 0), "high": float(hi or 0),
                             "low": float(lo or 0), "close": float(cl or 0), "volume": float(vol or 0)})
    cur.close(); conn.close()

    # Convert to series dict
    cols = {"open": {}, "high": {}, "low": {}, "close": {}, "volume": {}}
    for sym, rows in records.items():
        df = pd.DataFrame(rows).set_index("date").sort_index()
        df.index = pd.to_datetime(df.index)
        if len(df) < 60: continue
        for c in cols:
            cols[c][sym] = df[c]

    # Build wide DataFrames — use intersection of symbols that span full range
    # Filter to symbols with data covering 80%+ of the date range
    all_dates_union = sorted(set.union(*[set(s.index) for s in cols["close"].values()]))
    cutoff = len(all_dates_union) * 0.8
    filtered_symbols = [sym for sym, s in cols["close"].items() if len(s) >= cutoff]
    if len(filtered_symbols) < 100:
        filtered_symbols = sorted(cols["close"].keys(), key=lambda s: len(cols["close"][s]), reverse=True)[:200]
    
    # Use dates common to all filtered symbols
    common_dates = sorted(set.intersection(*[set(cols["close"][sym].index) for sym in filtered_symbols]))
    if len(common_dates) < 100:
        # Fallback: just use the most recent 2 years
        common_dates = [d for d in all_dates_union if d >= pd.Timestamp("2023-06-01")]
    
    panel = {}
    for c in cols:
        panel[c] = pd.DataFrame({sym: cols[c][sym] for sym in filtered_symbols}, index=common_dates)
    panel["amount"] = panel["close"] * panel["volume"]
    panel["vwap"] = (panel["open"] + panel["high"] + panel["low"] + panel["close"]) / 4.0
    return panel, list(cols["close"].keys())

def compute_ic_for_alpha(alpha_id, panel, eval_dates):
    """Compute IC for one zoo alpha with VN corrections."""
    # Determine zoo and number
    if alpha_id.startswith("gtja191_"):
        num = alpha_id.split("_")[1]
        mod_path = f"app.brain.quant.factors.zoo.gtja191.alpha_{num.zfill(3)}"
    elif alpha_id.startswith("alpha101_"):
        num = alpha_id.split("_")[1]
        mod_path = f"app.brain.quant.factors.zoo.alpha101.alpha_{num.zfill(3)}"
    else:
        return None

    try:
        mod = importlib.import_module(mod_path)
    except (ImportError, ModuleNotFoundError):
        return None

    if not hasattr(mod, 'compute'):
        return None

    try:
        alpha_vals = mod.compute(panel)
    except Exception:
        return None

    if alpha_vals is None or alpha_vals.empty:
        return None

    # IC computation
    closes = panel["close"]
    volumes = panel["volume"]
    daily_value = closes * volumes
    avg_value_20d = daily_value.rolling(20, min_periods=20).mean()

    ics = []
    for dt in eval_dates:
        dts = pd.Timestamp(dt)
        if dts not in alpha_vals.index or dts not in closes.index:
            continue

        # Factor values
        factor = alpha_vals.loc[dts].dropna()
        if len(factor) < VN_CONSTRAINTS["min_stocks"]:
            continue

        # Liquidity filter
        if dts in avg_value_20d.index:
            liq = avg_value_20d.loc[dts].dropna()
            liq_ok = liq[liq >= VN_CONSTRAINTS["min_value_bn"] * 1e6]
            factor = factor[factor.index.intersection(liq_ok.index)]

        if len(factor) < VN_CONSTRAINTS["min_stocks"]:
            continue

        # Forward returns: T+1 entry
        loc = closes.index.get_loc(dts)
        if loc + 1 + VN_CONSTRAINTS["holding"] >= len(closes):
            continue
        entry = closes.iloc[loc + 1]
        exit_ = closes.iloc[loc + 1 + VN_CONSTRAINTS["holding"]]

        common = factor.index.intersection(entry.index).intersection(exit_.index)
        if len(common) < VN_CONSTRAINTS["min_stocks"]:
            continue

        fwd = (exit_[common] / entry[common] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        factor_v = factor[common].dropna()
        common2 = factor_v.index.intersection(fwd.index)
        if len(common2) < VN_CONSTRAINTS["min_stocks"]:
            continue

        # Winsorize returns
        r = fwd[common2].clip(lower=fwd[common2].quantile(0.01), upper=fwd[common2].quantile(0.99))
        f = factor_v[common2]
        if len(np.unique(f)) < 2 or len(np.unique(r)) < 2:
            continue

        ic, _ = spearmanr(f, r)
        if not np.isnan(ic):
            ics.append(ic)

    return ics

def main():
    print("Initializing...")
    reg = get_default_registry()
    all_ids = reg.list()
    zoo_ids = [a for a in all_ids if a.startswith(("gtja191_", "alpha101_"))]
    print(f"Zoo alphas to test: {len(zoo_ids)}")

    start = time.monotonic()
    end_date = date.today()
    start_date = end_date - timedelta(days=int(3 * 365.25 + 400 + 60))

    print(f"Loading DB panel {start_date}..{end_date}...")
    panel, symbols = load_db_panel(start_date, end_date)
    print(f"  {len(symbols)} stocks, {len(panel['close'])} dates")

    closes = panel["close"]
    eval_dates = sorted(closes.index[::5])[60:]
    print(f"  {len(eval_dates)} eval dates: {eval_dates[0].date()} .. {eval_dates[-1].date()}")

    # Run IC for each alpha
    results = []
    for idx, aid in enumerate(zoo_ids):
        if (idx + 1) % 30 == 0:
            elapsed = time.monotonic() - start
            print(f"  [{idx+1}/{len(zoo_ids)}] {elapsed:.0f}s")
        ics = compute_ic_for_alpha(aid, panel, eval_dates)
        if ics is None or len(ics) < VN_CONSTRAINTS["min_dates"]:
            results.append({"alpha": aid, "n": len(ics or []), "ic": 0, "ir": 0,
                           "pos": 0, "t": 0, "pval": 1, "verdict": "NO_DATA"})
            continue

        ic_arr = np.array(ics)
        ic_m = float(np.mean(ic_arr))
        ic_s = float(np.std(ic_arr, ddof=1)) if len(ics) > 1 else 0
        pos = float(np.mean(ic_arr > 0))
        t_s = ic_m / (ic_s / math.sqrt(len(ics))) if ic_s > 0 else 0
        pv = 2 * (1 - t_dist.cdf(abs(t_s), df=len(ics) - 1)) if ic_s > 0 else 1.0

        if ic_m > 0.02 and pos >= 0.55 and abs(t_s) > 2:
            v = "ALIVE"
        elif ic_m < -0.02 and abs(t_s) > 2 and pos < 0.45:
            v = "REVERSED"
        elif abs(ic_m) > 0.01:
            v = "MARGINAL"
        else:
            v = "DEAD"

        results.append({"alpha": aid, "n": len(ics), "ic": round(ic_m, 4),
                        "ir": round(ic_m / ic_s, 3) if ic_s > 0 else 0,
                        "pos": round(pos, 2), "t": round(t_s, 2),
                        "pval": round(pv, 4), "verdict": v})

    # BH correction
    m = len(results)
    pvals = [r["pval"] for r in results]
    sorted_idx = sorted(range(m), key=lambda i: pvals[i])
    corrected = [0.0] * m
    prev_bh = 0.0
    for rank, idx in enumerate(sorted_idx):
        bh = pvals[idx] * m / (rank + 1)
        bh = min(bh, 1.0)
        corrected[idx] = max(bh, prev_bh)
        prev_bh = corrected[idx]
    for i, r in enumerate(results):
        r["p_adj"] = round(corrected[i], 4)

    # Print
    duration = round(time.monotonic() - start, 1)
    print(f"\n{'='*80}")
    print(f"ZOOM IC BENCHMARK — GTJA191 + Alpha101 on VN Market")
    print(f"Period: {eval_dates[0].date()} .. {eval_dates[-1].date()} ({len(eval_dates)} dates)")
    print(f"Duration: {duration}s  Universe: {len(symbols)} stocks")
    print(f"{'='*80}")
    print(f"{'ALPHA':>18s} {'IC':>7s} {'IR':>6s} {'POS':>5s} {'T':>6s} {'P_ADJ':>6s} {'VERDICT':>10s}")
    print("-" * 65)

    cats = {"ALIVE": [], "REVERSED": [], "MARGINAL": [], "DEAD": [], "NO_DATA": []}
    for r in results:
        cat = r["verdict"] if r["verdict"] in cats else "DEAD"
        cats[cat].append(r)
        if cat in ("ALIVE", "REVERSED", "MARGINAL"):
            print(f"{r['alpha']:>18s} {r['ic']:>7.4f} {r['ir']:>6.3f} {r['pos']:>5.2f} {r['t']:>6.2f} {r['p_adj']:>6.4f} {cat:>10s}")

    print(f"\n{'='*80}")
    print("SUMMARY")
    for cat in ["ALIVE", "REVERSED", "MARGINAL", "DEAD", "NO_DATA"]:
        if cats[cat]:
            n_zoo = defaultdict(int)
            for r in cats[cat]:
                prefix = "gtja" if r["alpha"].startswith("gtja") else "a101"
                n_zoo[prefix] += 1
            zoo_detail = " ".join(f"{k}={v}" for k, v in sorted(n_zoo.items()))
            print(f"  {cat:>10s}: {len(cats[cat]):>3d}  [{zoo_detail}]")
    print(f"{'='*80}")

    # Save
    import json
    with open("zoo_ic_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Results saved to zoo_ic_results.json")

if __name__ == "__main__":
    main()
