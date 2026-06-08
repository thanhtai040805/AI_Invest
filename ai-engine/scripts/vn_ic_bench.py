#!/usr/bin/env python3
"""VN IC Benchmark — compute IC/IR for all 31 VN_FACTORS over 3-year historical data.

Usage:
    python -m scripts.vn_ic_bench [--years 3] [--horizon 5 20] [--min-stocks 30]

Design:
    Loads OHLCV from DB, computes factor values month-end, computes forward
    returns, then Spearman rank IC per factor. Mirrors factor_analysis_core.py
    math but works with DB-stored VN data instead of vnstock panel.
"""
import argparse
import json
import logging
import math
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, ".")

from app.services.pg_pool import DB_URL

logger = logging.getLogger("vn_ic_bench")

TZ_VN = timedelta(hours=7)

# ── Factor definitions (mirrors VN_FACTORS in factor_scores.py) ──────
VN_FACTORS = {
    # momentum
    "MOM_3M":     {"group": "momentum", "direction": 1},
    "MOM_6M":     {"group": "momentum", "direction": 1},
    "COND_MOM":   {"group": "momentum", "direction": 1},
    # liquidity
    "AMIHUD":     {"group": "liquidity", "direction": -1},
    "DVOL_TREND": {"group": "liquidity", "direction": 1},
    # value
    "PE_INV":     {"group": "value", "direction": 1},
    "PB_INV":     {"group": "value", "direction": 1},
    "EARN_YLD":   {"group": "value", "direction": 1},
    "FCF_YLD":    {"group": "value", "direction": 1},
    "EVEBITDA_INV": {"group": "value", "direction": 1},
    "HML_REAL":   {"group": "value", "direction": 1},
    # quality
    "ACCRUAL":    {"group": "quality", "direction": 1},
    "CFO_TO_NI":  {"group": "quality", "direction": 1},
    "ROE_NORM":   {"group": "quality", "direction": 1},
    "GM":         {"group": "quality", "direction": 1},
    "NM":         {"group": "quality", "direction": 1},
    "YOY_REV":    {"group": "quality", "direction": 1},
    "YOY_EARN":   {"group": "quality", "direction": 1},
    "PIOTROSKI_F": {"group": "quality", "direction": 1},
    # earnings surprise
    "EARN_SURP":  {"group": "earnings", "direction": 1},
    # distress
    "ALTMAN_Z":   {"group": "distress", "direction": 1},
    # flow
    "FOREIGN_NET_5D":  {"group": "flow", "direction": 1},
    "FOREIGN_ACCUM":   {"group": "flow", "direction": 1},
    "INSIDER_NET_30D": {"group": "flow", "direction": 1},
    "FOREIGN_ROOM":    {"group": "flow", "direction": -1},
    # behavioral
    "TET_WINDOW":      {"group": "behavioral", "direction": 1},
    "CEILING_STREAK":  {"group": "behavioral", "direction": 1},
    "FORCED_SELLING":  {"group": "behavioral", "direction": 1},
    # risk
    "SIZE":       {"group": "risk", "direction": -1},
    "VOL_20D":    {"group": "risk", "direction": -1},
    "VOL_60D":    {"group": "risk", "direction": -1},
}

_PRICE_FACTORS = {k for k, v in VN_FACTORS.items() if k in {
    "MOM_3M", "MOM_6M", "COND_MOM",
    "AMIHUD", "DVOL_TREND",
    "VOL_20D", "VOL_60D", "SIZE",
    "TET_WINDOW", "CEILING_STREAK", "FORCED_SELLING",
}}
_FUNDAMENTAL_FACTORS = set(VN_FACTORS.keys()) - _PRICE_FACTORS

# ── Helpers ──────────────────────────────────────────────────────────

def _rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, ascending=True, na_option="keep") * 100

def _rank_desc(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, ascending=False, na_option="keep") * 100

_TET_DATES = {
    2020: date(2020, 1, 25), 2021: date(2021, 2, 12),
    2022: date(2022, 2, 1),  2023: date(2023, 1, 22),
    2024: date(2024, 2, 10), 2025: date(2025, 1, 29),
    2026: date(2026, 2, 17), 2027: date(2027, 2, 6),
}

def _tet_signal(d: date) -> float:
    tet = _TET_DATES.get(d.year)
    if tet is None:
        return 0.0
    days_to = (tet - d).days
    if 5 <= days_to <= 20:
        return 1.0
    if -10 <= days_to < 0:
        return -0.5
    return 0.0

def _vnindex_regime(cur, dt: date) -> float:
    """Get VNINDEX 1m return sign for conditional momentum."""
    try:
        cur.execute(
            "SELECT value FROM macro_indicators WHERE indicator_name='vnindex_return_1m' AND indicator_date=%s LIMIT 1",
            (dt,),
        )
        row = cur.fetchone()
        if row:
            return 1.0 if float(row[0]) > 0 else -1.0
    except Exception:
        pass
    return 0.0


# ── Data loader ─────────────────────────────────────────────────────

def load_all_ohlcv(cur, symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    """Load OHLCV for all symbols. Returns {symbol: DataFrame}."""
    cur.execute(
        """SELECT symbol, time::date as dt, adj_close, close, open, high, low, volume
           FROM ohlcv
           WHERE symbol = ANY(%s)
             AND time::date >= %s AND time::date <= %s
           ORDER BY symbol, time""",
        (symbols, start, end),
    )
    out: dict[str, list[dict]] = {}
    for sym, dt, ac, cl, op, hi, lo, vol in cur.fetchall():
        c = float(ac or cl or 0)
        out.setdefault(sym, []).append({
            "date": dt, "close": c, "open": float(op or 0),
            "high": float(hi or 0), "low": float(lo or 0),
            "volume": int(vol or 0),
        })
    result = {}
    for sym, rows in out.items():
        df = pd.DataFrame(rows).set_index("date").sort_index()
        df.index = pd.to_datetime(df.index)
        if len(df) > 20:
            result[sym] = df
    return result


def load_fundamentals(cur, symbols: list[str], as_of: date) -> dict[str, dict]:
    """Load latest financial_ratios as of a given date."""
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, pe, pb, roe, roa, debt_equity,
                  gross_margin, net_margin, fcf_yield, ev_ebitda,
                  yoy_revenue_growth, yoy_earnings_growth
           FROM financial_ratios
           WHERE symbol = ANY(%s) AND ratio_date <= %s
           ORDER BY symbol, ratio_date DESC""",
        (symbols, as_of),
    )
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        out[r[0]] = {
            "pe": r[1], "pb": r[2], "roe": r[3], "roa": r[4],
            "de": r[5], "gm": r[6], "nm": r[7], "fcf_y": r[8],
            "ev_eb": r[9], "yoy_rev": r[10], "yoy_earn": r[11],
        }
    return out


def load_foreign_flow(cur, symbols: list[str], as_of: date) -> dict[str, dict]:
    """Load latest foreign_flow stats as of a given date."""
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, net_value, room_remaining, room_limit
           FROM foreign_flow
           WHERE symbol = ANY(%s) AND trade_date <= %s
           ORDER BY symbol, trade_date DESC""",
        (symbols, as_of),
    )
    out: dict[str, dict] = {}
    for sym, nv, rr, rl in cur.fetchall():
        out[sym] = {"net_value": float(nv or 0), "room_remaining": float(rr or 0), "room_limit": float(rl or 0)}
    return out


def load_insider_30d(cur, symbols: list[str], as_of: date) -> dict[str, float]:
    """Load insider net buy ratio over last 30 days."""
    cutoff = as_of - timedelta(days=30)
    cur.execute(
        """SELECT symbol,
                  SUM(CASE WHEN trade_type IN ('Mua','Đăng ký mua') THEN quantity ELSE 0 END) as buy,
                  SUM(CASE WHEN trade_type IN ('Bán','Đăng ký bán') THEN quantity ELSE 0 END) as sell
           FROM insider_trades
           WHERE trade_date >= %s AND trade_date <= %s AND symbol = ANY(%s)
           GROUP BY symbol""",
        (cutoff, as_of, symbols),
    )
    return {r[0]: float((r[1] or 0) - (r[2] or 0)) for r in cur.fetchall()}


def load_stock_meta(cur, symbols: list[str]) -> dict[str, dict]:
    """Load market_cap, ceiling, floor, industry from stocks table."""
    cur.execute(
        "SELECT symbol, market_cap, ceiling, floor, industry FROM stocks WHERE symbol = ANY(%s)",
        (symbols,),
    )
    out: dict[str, dict] = {}
    for sym, mc, ceil, flr, ind in cur.fetchall():
        out[sym] = {"mcap": float(mc) if mc else None, "ceiling": float(ceil) if ceil else None,
                    "floor": float(flr) if flr else None, "industry": ind}
    return out


# ── Factor computation for a single date ────────────────────────────

def compute_factors_for_date(
    dt: date,
    ohlcv_all: dict[str, pd.DataFrame],
    fundamentals: dict[str, dict],
    meta: dict[str, dict],
    foreign: dict[str, dict],
    insider_net: dict[str, float],
    regime: float,
    rate_5d: dict[str, float],
) -> pd.DataFrame:
    """Compute all factor ranks for a single date. Returns wide DataFrame (factors × symbols)."""
    results: dict[str, dict[str, float]] = {}

    for sym, df in ohlcv_all.items():
        # Get 400 days of history ending at dt
        hist = df[df.index <= pd.Timestamp(dt)].tail(400)
        if len(hist) < 20:
            continue

        closes = hist["close"].values
        volumes = hist["volume"].values
        opens = hist["open"].values
        highs = hist["high"].values
        lows = hist["low"].values
        c0 = closes[-1]
        mcap = meta.get(sym, {}).get("mcap")
        ceiling = meta.get(sym, {}).get("ceiling")
        floor = meta.get(sym, {}).get("floor")

        row: dict[str, float] = {}

        # ── Price-based factors ─────────────────────────────────
        n = len(closes)
        if n >= 60:
            c20 = closes[-21] if n >= 21 else None  # t-20 (1 month ago)
            c60 = closes[-61] if n >= 61 else None
            if c0 > 0 and c20 and c20 > 0:
                mom3 = c0 / c20 - 1
                row["MOM_3M"] = mom3
                row["COND_MOM"] = mom3 * (1.0 + 0.5 * regime)
            if c0 > 0 and c60 and c60 > 0:
                row["MOM_6M"] = c0 / c60 - 1

        # AMIHUD
        if n >= 21:
            rets = abs(np.diff(closes[-21:]) / closes[-21:-1])
            dv = closes[-20:] * volumes[-20:]
            illiq = np.nanmean(rets / dv) if dv.sum() > 0 else np.nan
            row["AMIHUD"] = float(illiq) if not np.isnan(illiq) else np.nan

        # DVOL_TREND
        if n >= 20:
            dvol = closes[-20:] * volumes[-20:]
            dvol_5d = np.mean(dvol[-5:])
            dvol_20d = np.mean(dvol)
            row["DVOL_TREND"] = dvol_5d / dvol_20d - 1 if dvol_20d > 0 else np.nan

        # Volatility
        if n >= 21:
            ret_20d = np.diff(closes[-21:]) / closes[-21:-1]
            row["VOL_20D"] = float(np.std(ret_20d) * np.sqrt(252))
        if n >= 61:
            ret_60d = np.diff(closes[-61:]) / closes[-61:-1]
            row["VOL_60D"] = float(np.std(ret_60d) * np.sqrt(252))

        # SIZE
        if mcap and mcap > 0:
            row["SIZE"] = np.log(mcap)

        # CEILING_STREAK
        if ceiling and n >= 10:
            ceil_hits = sum(1 for i in range(min(10, n)) if closes[-(i+1)] >= ceiling)
            row["CEILING_STREAK"] = ceil_hits / 10.0

        # FORCED_SELLING
        if floor and n >= 10:
            floor_hits = sum(1 for i in range(5) if closes[-(i+1)] <= floor)
            vol_5d = np.mean(volumes[-5:])
            vol_20d = np.mean(volumes[-20:]) if n >= 20 else 1
            row["FORCED_SELLING"] = 1.0 if floor_hits >= 2 and (vol_5d / max(vol_20d, 1)) > 3 else 0.0

        # TET_WINDOW
        row["TET_WINDOW"] = _tet_signal(dt)

        # ── Fundamental factors ─────────────────────────────────
        fin = fundamentals.get(sym, {})
        pe = fin.get("pe")
        pb = fin.get("pb")

        # PE_INV, PB_INV, EARN_YLD
        row["PE_INV"] = 1.0 / pe if pe and pe > 0 else np.nan
        row["PB_INV"] = 1.0 / pb if pb and pb > 0 else np.nan
        row["EARN_YLD"] = fin.get("fcf_y") or np.nan
        row["FCF_YLD"] = fin.get("fcf_y") or np.nan

        eveb = fin.get("ev_eb")
        row["EVEBITDA_INV"] = 1.0 / eveb if eveb and eveb > 0 else np.nan

        # HML_REAL
        # Approximate book value from PE and market cap...
        # Actually, we need book_value from financial_statements which is complex
        # For IC bench, use PB inverse as proxy
        row["HML_REAL"] = row.get("PB_INV", np.nan)

        # Quality
        roe = fin.get("roe")
        row["ROE_NORM"] = roe / 100.0 if roe is not None else np.nan
        row["GM"] = fin.get("gm") or np.nan
        row["NM"] = fin.get("nm") or np.nan
        row["YOY_REV"] = fin.get("yoy_rev") or np.nan
        row["YOY_EARN"] = fin.get("yoy_earn") or np.nan

        # PIOTROSKI_F (simplified: binary signals from available data)
        pf = 0
        if roe is not None and roe > 0:
            pf += 1
        if mcap and mcap > 0:
            pf += 1
        row["PIOTROSKI_F"] = float(pf)

        # EARN_SURP
        row["EARN_SURP"] = fin.get("yoy_earn") or np.nan

        # ACCRUAL, CFO_TO_NI — require financial_statements, skip for IC bench
        # ALTMAN_Z — requires full statements

        # Foreign flow
        ff = foreign.get(sym, {})
        if mcap and mcap > 0:
            row["FOREIGN_NET_5D"] = ff.get("net_value", 0) / mcap
        room_rem = ff.get("room_remaining", 0)
        room_lim = ff.get("room_limit", 0)
        if room_lim > 0:
            room_pct = room_rem / room_lim
            row["FOREIGN_ROOM"] = -1.0 if room_pct < 0.05 else (0.5 if room_pct > 0.30 else 0.0)

        # Insider
        ins = insider_net.get(sym, 0)
        if mcap and mcap > 0 and c0 > 0:
            est_shares = mcap / c0
            row["INSIDER_NET_30D"] = ins / est_shares if est_shares > 0 else np.nan

        # FOREIGN_ACCUM — need multi-day data, skip for simplicity
        # ACCRUAL, ALTMAN_Z — need financial_statements JSONB

        results[sym] = row

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results).T
    # Rank each factor cross-sectionally
    ranked = pd.DataFrame(index=df.index)
    for factor_id in VN_FACTORS:
        if factor_id not in df.columns:
            continue
        direction = VN_FACTORS[factor_id]["direction"]
        raw = df[factor_id].dropna()
        if len(raw) < 5:
            continue
        if direction == -1:
            ranked[factor_id] = _rank_desc(raw)
        else:
            ranked[factor_id] = _rank(raw)

    return ranked


# ── IC computation ──────────────────────────────────────────────────

def compute_ic_series(factor_df: pd.Series, forward_returns: pd.Series) -> pd.Series:
    """Daily Spearman rank IC for one factor."""
    valid = factor_df.notna() & forward_returns.notna()
    n = valid.sum()
    if n < 5:
        return pd.Series(dtype=float)
    return pd.Series({
        "ic": forward_returns[valid].rank().corr(factor_df[valid].rank(), method="pearson"),
        "n": n,
    })


def t_stat(ic_mean: float, ic_std: float, n: int) -> float:
    if not (n > 0 and ic_std > 0 and math.isfinite(ic_std)):
        return 0.0
    return ic_mean / (ic_std / math.sqrt(n))


def categorise(ic_mean: float, ic_positive_ratio: float, ic_std: float, n: int) -> str:
    t = t_stat(ic_mean, ic_std, n)
    if ic_mean > 0.02 and ic_positive_ratio >= 0.55 and abs(t) > 2:
        return "alive"
    if ic_mean < -0.02 and abs(t) > 2:
        return "reversed"
    return "dead"


# ── Main benchmark ──────────────────────────────────────────────────

def run_benchmark(
    years: int = 3,
    horizons: tuple[int, ...] = (5, 20),
    min_stocks: int = 30,
    step_days: int = 21,
) -> dict:
    """Run IC benchmark for all VN_FACTORS over historical data.

    Args:
        years: Number of years of history.
        horizons: Forward return horizons in trading days.
        min_stocks: Minimum number of stocks with data to include a date.
        step_days: Step size between evaluation dates (21 ≈ monthly).

    Returns:
        Dict with per-factor IC stats.
    """
    start = time.monotonic()
    end_date = date.today()
    start_date = end_date - timedelta(days=int(years * 365.25) + max(horizons) + 30)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        # Load all symbols
        cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        all_symbols = [r[0] for r in cur.fetchall()]
        logger.info("Universe: %d HOSE symbols", len(all_symbols))

        # Load all OHLCV
        ohlcv_all = load_all_ohlcv(cur, all_symbols, start_date, end_date)
        logger.info("OHLCV loaded: %d symbols with data", len(ohlcv_all))

        # Load stock metadata
        meta = load_stock_meta(cur, list(ohlcv_all.keys()))
        logger.info("Stock meta loaded: %d symbols", len(meta))

        # Generate evaluation dates (monthly)
        eval_dates = []
        d = start_date + timedelta(days=400)  # need 400 days warmup
        while d <= end_date:
            if d.weekday() < 5:  # avoid weekends
                eval_dates.append(d)
            d += timedelta(days=step_days)
        logger.info("Evaluation dates: %d dates from %s to %s",
                    len(eval_dates), eval_dates[0], eval_dates[-1])

        # Collect IC per factor per date
        ic_records: dict[str, list[dict]] = defaultdict(list)

        for idx, dt in enumerate(eval_dates):
            if (idx + 1) % 20 == 0:
                logger.info("  Progress: %d/%d dates", idx + 1, len(eval_dates))

            # Load date-specific data
            fundamentals = load_fundamentals(cur, list(ohlcv_all.keys()), dt)
            foreign = load_foreign_flow(cur, list(ohlcv_all.keys()), dt)
            insider_net = load_insider_30d(cur, list(ohlcv_all.keys()), dt)
            regime = _vnindex_regime(cur, dt)

            # Compute factor ranks
            factor_ranks = compute_factors_for_date(
                dt, ohlcv_all, fundamentals, meta, foreign, insider_net, regime, {},
            )
            if factor_ranks.empty or len(factor_ranks) < min_stocks:
                continue

            # Compute forward returns for each horizon
            for horizon in horizons:
                # Get forward close price
                fwd = {}
                for sym in factor_ranks.index:
                    df = ohlcv_all.get(sym)
                    if df is None:
                        continue
                    # Find close at dt
                    hist = df[df.index <= pd.Timestamp(dt)]
                    if len(hist) < 1:
                        continue
                    cur_close = hist["close"].iloc[-1]
                    # Find close at dt + horizon
                    fwd_hist = df[df.index > pd.Timestamp(dt)]
                    if len(fwd_hist) < horizon:
                        continue
                    fwd_close = fwd_hist["close"].iloc[horizon - 1] if len(fwd_hist) >= horizon else None
                    if cur_close > 0 and fwd_close is not None and fwd_close > 0:
                        fwd[sym] = fwd_close / cur_close - 1

                if len(fwd) < min_stocks:
                    continue

                fwd_s = pd.Series(fwd, name="fwd_ret")
                # Compute IC for each factor
                for factor_id in factor_ranks.columns:
                    factor_s = factor_ranks[factor_id]
                    common = factor_s.dropna().index.intersection(fwd_s.dropna().index)
                    if len(common) < min_stocks:
                        continue
                    ic_val = fwd_s[common].rank().corr(factor_s[common].rank(), method="pearson")
                    if not np.isnan(ic_val):
                        ic_records[factor_id].append({
                            "date": dt, "horizon": horizon, "ic": ic_val, "n": len(common),
                        })

        # Aggregate results
        results = []
        for factor_id in sorted(VN_FACTORS.keys()):
            recs = ic_records.get(factor_id, [])
            if not recs:
                continue
            for horizon in horizons:
                horizon_recs = [r for r in recs if r["horizon"] == horizon]
                if len(horizon_recs) < 10:
                    continue
                ic_values = [r["ic"] for r in horizon_recs]
                ic_mean = float(np.mean(ic_values))
                ic_std = float(np.std(ic_values, ddof=1)) if len(ic_values) > 1 else 0.0
                ic_pos = sum(1 for v in ic_values if v > 0) / len(ic_values)
                n = len(ic_values)
                category = categorise(ic_mean, ic_pos, ic_std, n)

                meta_info = VN_FACTORS[factor_id]
                results.append({
                    "factor_id": factor_id,
                    "group": meta_info["group"],
                    "horizon": f"{horizon}d",
                    "ic_mean": round(ic_mean, 4),
                    "ic_std": round(ic_std, 4),
                    "ir": round(ic_mean / ic_std, 4) if ic_std > 0 else 0.0,
                    "ic_positive_ratio": round(ic_pos, 3),
                    "n_dates": n,
                    "category": category,
                })

        df_results = pd.DataFrame(results)
        duration = round(time.monotonic() - start, 1)

        # Summary
        summary = {
            "status": "ok",
            "duration_seconds": duration,
            "n_factors": len(VN_FACTORS),
            "n_dates": len(eval_dates),
            "n_stocks": len(ohlcv_all),
            "period": f"{eval_dates[0]} to {eval_dates[-1]}",
            "categories": df_results.groupby("category").size().to_dict() if not df_results.empty else {},
            "by_group": {},
            "results": results,
        }

        # Per-group breakdown
        by_group = df_results.groupby("group") if not df_results.empty else []
        for group, grp in df_results.groupby("group"):
            alive = len(grp[grp["category"] == "alive"])
            dead = len(grp[grp["category"] == "dead"])
            reversed_ = len(grp[grp["category"] == "reversed"])
            summary["by_group"][group] = {
                "alive": int(alive), "dead": int(dead), "reversed": int(reversed_),
            }

        # Top alive factors
        alive_df = df_results[df_results["category"] == "alive"].sort_values("ir", ascending=False)
        summary["top_alive"] = alive_df.head(10).to_dict("records") if not alive_df.empty else []

        # Print report
        print(f"\n{'='*70}")
        print(f"VN IC BENCHMARK RESULTS")
        print(f"{'='*70}")
        print(f"Period: {eval_dates[0]} to {eval_dates[-1]} ({len(eval_dates)} evaluation dates)")
        print(f"Universe: {len(ohlcv_all)} stocks")
        print(f"Duration: {duration}s")
        print(f"\nCategory breakdown:")
        for cat, cnt in (summary.get("categories") or {}).items():
            print(f"  {cat}: {cnt}")
        print(f"\nBy group:")
        for group, counts in sorted(summary["by_group"].items()):
            print(f"  {group:>12s}: alive={counts['alive']}  dead={counts['dead']}  reversed={counts['reversed']}")
        print(f"\nTop alive factors by IR:")
        for r in summary["top_alive"]:
            print(f"  {r['factor_id']:>15s}  IR={r['ir']:.3f}  IC={r['ic_mean']:.4f}  pos={r['ic_positive_ratio']:.2f}  [{r['horizon']}]")
        print(f"\nAll factors:")
        print(df_results.to_string(index=False))
        print(f"{'='*70}\n")

        return summary

    finally:
        cur.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="VN IC Benchmark")
    parser.add_argument("--years", type=int, default=3, help="Years of history (default: 3)")
    parser.add_argument("--horizon", type=int, nargs="+", default=[5, 20], help="Forward horizons in days (default: 5 20)")
    parser.add_argument("--min-stocks", type=int, default=30, help="Min stocks per date (default: 30)")
    parser.add_argument("--step", type=int, default=21, help="Days between eval dates (default: 21)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(levelname)s [%(name)s] %(message)s")

    run_benchmark(
        years=args.years,
        horizons=tuple(args.horizon),
        min_stocks=args.min_stocks,
        step_days=args.step,
    )


if __name__ == "__main__":
    main()
