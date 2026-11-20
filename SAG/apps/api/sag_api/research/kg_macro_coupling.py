"""Engine 2: Quantitative Macro & Cross-Sector Coupling Engine (Zero Hardcode, 0 LLM).

Productionized from verified Stage 1 `block2_macro_kg_engine.py`.
Implemented strictly in pure `numpy` + `pandas` + `math` (zero `scipy`, `statsmodels`, or `scikit-learn`).

Key statistical & PIT guarantees:
- Full calendar `QE` grids before `.diff(1)` / `.shift(lag)` so missing quarters never warp lag.
- Full-lag Benjamini-Hochberg FDR across all `(metric x indicator x lag)` hypotheses per entity.
- Newey-West HAC covariance (`maxlags=4` weekly, `maxlags=5` 5-day rolling) with exact Student-t p-values
  computed via regularized incomplete beta function `I_x(a, b)`.
- Fail-closed non-overlapping tercile sign stability + half-sample regime-flip guard.
- Audited Cross-Check directional tiers: `CONFIRMED_BOTH`, `QUANT_DOC_NEUTRAL`, `CONFLICT_FLAG`,
  `QUANT_ONLY`, `DOC_ONLY`.
- Defense-in-depth strict PIT filtering (`<= as_of_date`) in Python as well as SQL.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

_MAX_VALID_TO = date(9999, 12, 31)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for incomplete beta function (Lentz's method)."""
    max_iter = 200
    eps = 3.0e-14
    fpmin = 1.0e-300

    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """Compute regularized incomplete beta function I_x(a, b) in pure Python math."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def two_sided_t_pvalue(t_stat: float, df: int) -> float:
    """Exact two-sided Student-t p-value for `|T| >= |t_stat|` with `df` degrees of freedom."""
    if df <= 0 or not math.isfinite(t_stat):
        return float("nan")
    if t_stat == 0.0:
        return 1.0
    x = df / (df + t_stat * t_stat)
    p = regularized_incomplete_beta(0.5 * df, 0.5, x)
    return max(0.0, min(1.0, p))


def _average_ranks(arr: np.ndarray) -> np.ndarray:
    """Compute tie-averaged ranks matching scipy.stats.rankdata(method='average')."""
    return pd.Series(arr).rank(method="average").to_numpy(dtype=np.float64)


def pearson_corr_pvalue(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Compute Pearson correlation and two-sided t-test p-value in pure numpy + math."""
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")
    xm = x - x.mean()
    ym = y - y.mean()
    denom = float(math.sqrt(float(np.dot(xm, xm)) * float(np.dot(ym, ym))))
    if denom < 1e-12:
        return float("nan"), float("nan")
    r = max(-1.0, min(1.0, float(np.dot(xm, ym)) / denom))
    if abs(r) >= 1.0 - 1e-15:
        return r, 0.0
    t_val = r * math.sqrt((n - 2) / max(1e-15, 1.0 - r * r))
    return r, two_sided_t_pvalue(t_val, n - 2)


def spearman_corr_pvalue(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Compute Spearman rank correlation (tie-averaged) and two-sided t p-value."""
    n = len(x)
    if n < 3:
        return float("nan"), float("nan")
    rx = _average_ranks(x)
    ry = _average_ranks(y)
    return pearson_corr_pvalue(rx, ry)


def ols_newey_west_hac(
    y: np.ndarray,
    x: np.ndarray,
    *,
    maxlags: int = 4,
) -> tuple[float, float, float]:
    """Fit `y = alpha + beta * x + eps` and return `(beta, t_stat_hac, p_value_hac)`.

    Uses Bartlett kernel Newey-West HAC covariance matrix without statsmodels.
    Falls back to White HC0 if finite-sample Bartlett sum produces non-positive variance.
    """
    n = len(y)
    if n < 5 or len(x) != n:
        return float("nan"), float("nan"), float("nan")

    X = np.column_stack([np.ones(n, dtype=np.float64), x.astype(np.float64)])
    yv = y.astype(np.float64)
    XtX = X.T @ X
    try:
        XtX_inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(XtX)

    params = XtX_inv @ (X.T @ yv)
    resid = yv - X @ params
    scores = X * resid[:, None]  # (n, 2)

    S = scores.T @ scores
    lags = min(maxlags, max(0, n - 2))
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        gamma = scores[lag:].T @ scores[:-lag]
        S = S + weight * (gamma + gamma.T)

    cov_hac = XtX_inv @ S @ XtX_inv
    var_beta = float(cov_hac[1, 1])
    if not math.isfinite(var_beta) or var_beta <= 1e-18:
        cov_hc0 = XtX_inv @ (scores.T @ scores) @ XtX_inv
        var_beta = float(cov_hc0[1, 1])
    if not math.isfinite(var_beta) or var_beta <= 1e-18:
        return float("nan"), float("nan"), float("nan")

    beta = float(params[1])
    se = math.sqrt(var_beta)
    t_stat = beta / se
    p_val = two_sided_t_pvalue(t_stat, n - 2)
    return beta, t_stat, p_val


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Standard Benjamini-Hochberg FDR q-values preserving input order."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    q_sorted = [1.0] * m
    prev = 1.0
    for rank_idx in range(m - 1, -1, -1):
        rank = rank_idx + 1
        p = float(indexed[rank_idx][1])
        val = min(prev, p * m / rank)
        val = max(0.0, min(1.0, val))
        q_sorted[rank_idx] = val
        prev = val
    out = [1.0] * m
    for orig_idx, q_val in zip((idx for idx, _ in indexed), q_sorted, strict=True):
        out[orig_idx] = q_val
    return out


def _fast_corr(a: np.ndarray, b: np.ndarray, spearman: bool) -> float:
    if spearman:
        a = _average_ranks(a)
        b = _average_ranks(b)
    am = a - a.mean()
    bm = b - b.mean()
    denom = float(math.sqrt(float(np.dot(am, am)) * float(np.dot(bm, bm))))
    if denom < 1e-12:
        return float("nan")
    return float(np.dot(am, bm) / denom)


def compute_sign_stability(
    x: np.ndarray,
    y: np.ndarray,
    full_sign: int,
    method: str = "ols",
    min_sub_n: int = 4,
    min_sub_abs_corr: float = 0.10,
) -> float:
    """Sign consistency across 3 chronological sub-windows (fail-closed when sample too short).

    Uses non-overlapping terciles whenever `n >= 3 * min_sub_n`,
    otherwise 3 overlapping 55% windows (failing closed to 0.0 if `w >= n`).
    Enforces a non-overlapping half-sample regime-flip guard whenever `n >= 10`.
    """
    n = len(x)
    if full_sign == 0 or n < max(6, min_sub_n):
        return 0.0
    if n >= 3 * min_sub_n:
        t1 = n // 3
        t2 = (2 * n) // 3
        slices = [(0, t1), (t1, t2), (t2, n)]
    else:
        w = max(min_sub_n, math.ceil(n * 0.55))
        if w >= n:
            return 0.0
        mid_start = max(0, (n - w) // 2)
        slices = [(0, w), (mid_start, mid_start + w), (n - w, n)]

    consistent = 0
    valid = 0
    for a, b in slices:
        xs, ys = x[a:b], y[a:b]
        if len(xs) < 4 or np.std(xs) < 1e-12 or np.std(ys) < 1e-12:
            continue
        valid += 1
        corr = _fast_corr(xs, ys, spearman=(method == "spearman"))
        if math.isfinite(corr) and abs(corr) >= min_sub_abs_corr:
            w_sign = 1 if corr > 0 else -1
            if w_sign == full_sign:
                consistent += 1

    base_stability = (consistent / valid) if valid >= 2 else 0.0

    if n >= 10:
        half = n // 2
        for a, b in ((0, half), (half, n)):
            xh, yh = x[a:b], y[a:b]
            if len(xh) < 4 or np.std(xh) < 1e-12 or np.std(yh) < 1e-12:
                continue
            ch = _fast_corr(xh, yh, spearman=(method == "spearman"))
            if math.isfinite(ch) and (ch * full_sign) <= -0.25:
                base_stability = min(base_stability, 1.0 / 3.0)
                break

    return base_stability


def make_stationary_series(series: pd.Series, unit: str | None) -> tuple[pd.Series, str]:
    """Transform a macro series to stationary changes without hardcoding indicator names."""
    s = series.dropna().sort_index()
    u = (unit or "").lower()
    if "%" in u or (s <= 0).any():
        return s.diff().dropna(), "diff"
    return s.pct_change().replace([np.inf, -np.inf], np.nan).dropna(), "pct_change"


def _filter_macro_pit(macro_df: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    if macro_df.empty:
        return macro_df
    df = macro_df.copy()
    df["indicator_date"] = pd.to_datetime(df["indicator_date"])
    cutoff = pd.Timestamp(as_of_date)
    return df[df["indicator_date"] <= cutoff]


def _filter_ratios_pit(ratios_df: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    if ratios_df.empty:
        return ratios_df
    df = ratios_df.copy()
    df["ratio_date"] = pd.to_datetime(df["ratio_date"])
    cutoff = pd.Timestamp(as_of_date)
    df = df[df["ratio_date"] <= cutoff]
    if "published_date" in df.columns:
        pub = pd.to_datetime(df["published_date"], errors="coerce")
        # Trust published_date if within statutory window [ratio_date, ratio_date+180d]; else +45d
        valid_pub = (
            pub.notna()
            & (pub >= df["ratio_date"])
            & (pub <= df["ratio_date"] + pd.Timedelta(days=180))
        )
        eff_pub = pub.where(valid_pub, df["ratio_date"] + pd.Timedelta(days=45))
        df = df[eff_pub <= cutoff]
    return df


def _filter_market_pit(mkt_df: pd.DataFrame, as_of_date: date) -> pd.DataFrame:
    if mkt_df.empty:
        return mkt_df
    df = mkt_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    cutoff = pd.Timestamp(as_of_date)
    return df[df["date"] <= cutoff]


def run_fundamental_macro_channel(
    stocks_df: pd.DataFrame,
    macro_df: pd.DataFrame,
    ratios_df: pd.DataFrame,
    as_of_date: date,
    min_quarters: int = 12,
    max_quarters: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Channel 1: Quarterly Macro -> Fundamental (GPM, RevYoY, NPM) with lags 0, 1, 2."""
    macro_pit = _filter_macro_pit(macro_df, as_of_date)
    ratios_pit = _filter_ratios_pit(ratios_df, as_of_date)
    if macro_pit.empty or ratios_pit.empty or stocks_df.empty:
        return [], {}

    macro_q: dict[str, pd.Series] = {}
    macro_transforms: dict[str, str] = {}
    for ind_name, grp in macro_pit.groupby("indicator_name"):
        unit = grp["unit"].dropna().iloc[0] if "unit" in grp and grp["unit"].notna().any() else ""
        s = grp.set_index("indicator_date")["value"].sort_index()
        # Full calendar QE grid before diff/shift so missing quarters never warp lag
        q_grid = s.resample("QE").last()
        u = str(unit or "").lower()
        if "%" in u or (q_grid.dropna() <= 0).any():
            dq_grid, transform = q_grid.diff(1), "diff"
        else:
            dq_grid, transform = (
                q_grid.pct_change(1, fill_method=None).replace([np.inf, -np.inf], np.nan),
                "pct_change",
            )
        dq_valid = dq_grid.dropna()
        if len(dq_valid) >= min_quarters and float(dq_valid.std()) > 1e-12:
            macro_q[str(ind_name)] = dq_grid
            macro_transforms[str(ind_name)] = transform

    if not macro_q:
        return [], {}

    rdf = ratios_pit.copy()
    rdf["q_end"] = rdf["ratio_date"].dt.to_period("Q").dt.end_time.dt.normalize()
    rdf = rdf.sort_values(["symbol", "q_end"]).drop_duplicates(["symbol", "q_end"], keep="last")

    sector_map = dict(zip(stocks_df["symbol"], stocks_df["sector"], strict=True))
    rdf["sector"] = rdf["symbol"].map(sector_map)

    target_cols = [("gross_margin", "GPM"), ("yoy_revenue_growth", "REV_YOY"), ("net_margin", "NPM")]
    raw_tests: list[dict[str, Any]] = []

    def test_entity_series(entity_node_id: str, entity_level: str, sub: pd.DataFrame) -> None:
        sub = sub.set_index("q_end").sort_index()
        if len(sub) < min_quarters:
            return
        full_q_idx = pd.date_range(sub.index.min(), sub.index.max(), freq="QE")
        sub_grid = sub.reindex(full_q_idx)
        for col, metric_label in target_cols:
            if col not in sub_grid.columns:
                continue
            y = sub_grid[col].diff(1).dropna()
            if len(y) > max_quarters:
                y = y.iloc[-max_quarters:]
            if len(y) < min_quarters or float(y.std()) < 1e-12:
                continue
            for ind_name, dm_grid in macro_q.items():
                # Test ALL 3 lags inside BH-FDR (no lag cherry-picking!)
                for lag in (0, 1, 2):
                    dm_lag = dm_grid.shift(lag).dropna()
                    aligned = pd.concat([y.rename("y"), dm_lag.rename("x")], axis=1, sort=True).dropna()
                    if len(aligned) > max_quarters:
                        aligned = aligned.iloc[-max_quarters:]
                    if len(aligned) < min_quarters:
                        continue
                    xv = aligned["x"].to_numpy(dtype=float)
                    yv = aligned["y"].to_numpy(dtype=float)
                    if np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
                        continue
                    rho, p_val = spearman_corr_pvalue(xv, yv)
                    if not math.isfinite(rho) or not math.isfinite(p_val):
                        continue
                    xz = (xv - xv.mean()) / (xv.std() + 1e-12)
                    yz = (yv - yv.mean()) / (yv.std() + 1e-12)
                    beta = float(np.dot(xz, yz) / len(xz))
                    sign = 1 if rho > 0 else (-1 if rho < 0 else 0)
                    stab = compute_sign_stability(xv, yv, sign, method="spearman", min_sub_n=4)
                    raw_tests.append(
                        {
                            "entity_node_id": entity_node_id,
                            "entity_level": entity_level,
                            "indicator_name": ind_name,
                            "channel": "FUNDAMENTAL",
                            "target_metric": metric_label,
                            "lag_periods": lag,
                            "lag_days": lag * 90,
                            "n_obs": len(aligned),
                            "beta_value": round(beta, 4),
                            "corr_value": round(float(rho), 4),
                            "p_value": float(p_val),
                            "direction_sign": sign,
                            "sign_stability": round(stab, 3),
                            "tercile_stable": bool(stab >= (2.0 / 3.0 - 1e-9)),
                            "window_start": str(aligned.index.min().date()),
                            "window_end": str(aligned.index.max().date()),
                            "as_of_date": str(as_of_date),
                        }
                    )

    for sym, grp in rdf.groupby("symbol"):
        test_entity_series(f"TICKER:{sym}", "COMPANY", grp)

    sector_counts = stocks_df.dropna(subset=["sector"]).groupby("sector")["symbol"].nunique()
    valid_sectors = set(sector_counts[sector_counts >= 3].index)
    for sec, grp in rdf[rdf["sector"].isin(valid_sectors)].groupby("sector"):
        sec_med = grp.groupby("q_end")[[c for c, _ in target_cols if c in grp.columns]].median().reset_index()
        test_entity_series(f"SECTOR:{sec}", "SECTOR", sec_med)

    best_passed: dict[tuple[str, str, str], dict[str, Any]] = {}
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in raw_tests:
        by_entity[r["entity_node_id"]].append(r)

    for subset in by_entity.values():
        q_vals = benjamini_hochberg([r["p_value"] for r in subset])
        for rec, qv in zip(subset, q_vals, strict=True):
            rec["fdr_q"] = round(float(qv), 5)
            rec["p_value"] = round(float(rec["p_value"]), 5)
            if (
                abs(rec["corr_value"]) >= 0.25
                and rec["p_value"] < 0.05
                and rec["fdr_q"] <= 0.10
                and rec["tercile_stable"]
            ):
                k_best = (rec["entity_node_id"], rec["target_metric"], rec["indicator_name"])
                prev = best_passed.get(k_best)
                if prev is None or rec["p_value"] < prev["p_value"]:
                    best_passed[k_best] = rec

    return list(best_passed.values()), macro_transforms


def run_market_excess_macro_channel(
    stocks_df: pd.DataFrame,
    macro_df: pd.DataFrame,
    mkt_df: pd.DataFrame,
    as_of_date: date,
    min_weeks: int = 52,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]], list[str]]:
    """Channel 2: Weekly Market Excess Return vs Macro Changes with Newey-West HAC."""
    macro_pit = _filter_macro_pit(macro_df, as_of_date)
    mkt_pit = _filter_market_pit(mkt_df, as_of_date)
    if macro_pit.empty or mkt_pit.empty or stocks_df.empty:
        return [], {}, []

    pivot = mkt_pit.pivot_table(index="date", columns="ticker", values="close_adj", aggfunc="last").sort_index()
    if "VNINDEX" not in pivot.columns:
        return [], {}, []

    weekly_px = pivot.resample("W-FRI").last()
    weekly_ret = weekly_px.pct_change(fill_method=None).clip(-0.30, 0.30)
    mkt_ret = weekly_ret["VNINDEX"].dropna()
    if len(mkt_ret) < min_weeks:
        return [], {}, []

    macro_w: dict[str, pd.Series] = {}
    for ind_name, grp in macro_pit.groupby("indicator_name"):
        unit = grp["unit"].dropna().iloc[0] if "unit" in grp and grp["unit"].notna().any() else ""
        s = grp.set_index("indicator_date")["value"].sort_index()
        w_native = s.resample("W-FRI").last().dropna()
        if len(w_native.reindex(mkt_ret.index).dropna()) < min_weeks:
            continue
        w = s.resample("W-FRI").last().ffill(limit=1)
        dw, _ = make_stationary_series(w, str(unit))
        dw = dw.reindex(mkt_ret.index).dropna()
        nonzero_ratio = float((dw.abs() > 1e-12).mean()) if len(dw) else 0.0
        if len(dw) >= min_weeks and float(dw.std()) > 1e-12 and nonzero_ratio >= 0.50:
            macro_w[str(ind_name)] = dw

    macro_cols = sorted(macro_w.keys())
    if not macro_cols:
        return [], {}, []
    macro_panel = pd.DataFrame({k: macro_w[k] for k in macro_cols}).dropna(how="all")
    macro_z = (macro_panel - macro_panel.mean()) / (macro_panel.std() + 1e-12)

    excess_by_ticker: dict[str, pd.Series] = {}
    sector_map = dict(zip(stocks_df["symbol"], stocks_df["sector"], strict=True))
    for sym in stocks_df["symbol"]:
        if sym not in weekly_ret.columns:
            continue
        pair = pd.concat([weekly_ret[sym].rename("r"), mkt_ret.rename("m")], axis=1).dropna()
        if len(pair) < min_weeks:
            continue
        cov = np.cov(pair["r"], pair["m"], ddof=1)
        beta_mkt = cov[0, 1] / (cov[1, 1] + 1e-12)
        alpha = pair["r"].mean() - beta_mkt * pair["m"].mean()
        excess_by_ticker[str(sym)] = pair["r"] - (alpha + beta_mkt * pair["m"])

    excess_by_sector: dict[str, pd.Series] = {}
    sector_buckets: dict[str, list[pd.Series]] = defaultdict(list)
    for sym, ex in excess_by_ticker.items():
        sec = sector_map.get(sym)
        if sec:
            sector_buckets[str(sec)].append(ex.rename(sym))
    for sec, series_list in sector_buckets.items():
        if len(series_list) >= 3:
            sec_df = pd.concat(series_list, axis=1)
            sec_mean = sec_df.mean(axis=1).dropna()
            if len(sec_mean) >= min_weeks:
                excess_by_sector[sec] = sec_mean

    raw_tests: list[dict[str, Any]] = []
    macro_beta_matrix: dict[str, dict[str, float]] = {}

    def run_hac_for_entity(entity_node_id: str, entity_level: str, ex_ret: pd.Series) -> None:
        beta_vec: dict[str, float] = {}
        for ind_name in macro_cols:
            aligned = pd.concat([ex_ret.rename("y"), macro_z[ind_name].rename("x")], axis=1).dropna()
            if len(aligned) < min_weeks:
                continue
            xv = aligned["x"].to_numpy(dtype=float)
            yv = aligned["y"].to_numpy(dtype=float)
            if np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
                continue
            beta, t_stat, p_val = ols_newey_west_hac(yv, xv, maxlags=4)
            if not (math.isfinite(beta) and math.isfinite(t_stat) and math.isfinite(p_val)):
                continue
            beta_std = beta / (float(np.std(yv)) + 1e-12)
            beta_vec[ind_name] = round(float(beta_std), 4)
            sign = 1 if beta > 0 else (-1 if beta < 0 else 0)
            stab = compute_sign_stability(xv, yv, sign, method="ols", min_sub_n=max(4, min(20, len(xv) // 3)))
            rho = float(np.corrcoef(xv, yv)[0, 1])
            raw_tests.append(
                {
                    "entity_node_id": entity_node_id,
                    "entity_level": entity_level,
                    "indicator_name": ind_name,
                    "channel": "MARKET_EXCESS",
                    "target_metric": "EXCESS_RET",
                    "lag_periods": 0,
                    "lag_days": 0,
                    "n_obs": len(aligned),
                    "beta_value": round(beta_std, 4),
                    "corr_value": round(rho, 4),
                    "t_stat_hac": round(t_stat, 3),
                    "p_value": p_val,
                    "direction_sign": sign,
                    "sign_stability": round(stab, 3),
                    "tercile_stable": bool(stab >= (2.0 / 3.0 - 1e-9)),
                    "window_start": str(aligned.index.min().date()),
                    "window_end": str(aligned.index.max().date()),
                    "as_of_date": str(as_of_date),
                }
            )
        if entity_level == "COMPANY":
            sym = entity_node_id.split(":", 1)[1]
            macro_beta_matrix[sym] = beta_vec

    for sym, ex in excess_by_ticker.items():
        run_hac_for_entity(f"TICKER:{sym}", "COMPANY", ex)
    for sec, ex in excess_by_sector.items():
        run_hac_for_entity(f"SECTOR:{sec}", "SECTOR", ex)

    passed: list[dict[str, Any]] = []
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in raw_tests:
        by_entity[r["entity_node_id"]].append(r)

    for subset in by_entity.values():
        q_vals = benjamini_hochberg([r["p_value"] for r in subset])
        for rec, qv in zip(subset, q_vals, strict=True):
            rec["fdr_q"] = round(float(qv), 5)
            rec["p_value"] = round(float(rec["p_value"]), 5)
            if (
                abs(rec["t_stat_hac"]) >= 2.0
                and rec["p_value"] < 0.05
                and rec["fdr_q"] <= 0.10
                and rec["tercile_stable"]
            ):
                passed.append(rec)

    return passed, macro_beta_matrix, macro_cols


def run_cross_sector_coupling(
    stocks_df: pd.DataFrame,
    ratios_df: pd.DataFrame,
    mkt_df: pd.DataFrame,
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Algorithm 2.3: Cross-Sector Lead-Lag & Fundamental Co-Movement (Zero-Hardcode)."""
    ratios_pit = _filter_ratios_pit(ratios_df, as_of_date)
    mkt_pit = _filter_market_pit(mkt_df, as_of_date)
    if stocks_df.empty:
        return []

    sector_map = dict(zip(stocks_df["symbol"], stocks_df["sector"], strict=True))
    sector_counts = stocks_df.dropna(subset=["sector"]).groupby("sector")["symbol"].nunique()
    valid_sectors = sorted(sector_counts[sector_counts >= 3].index.tolist())
    if len(valid_sectors) < 2:
        return []

    sec_fund: dict[str, dict[str, pd.Series]] = defaultdict(dict)
    if not ratios_pit.empty:
        rdf = ratios_pit.copy()
        rdf["q_end"] = rdf["ratio_date"].dt.to_period("Q").dt.end_time.dt.normalize()
        rdf["sector"] = rdf["symbol"].map(sector_map)
        rdf = rdf[rdf["sector"].isin(valid_sectors)]

        for sec, grp in rdf.groupby("sector"):
            cols = [c for c in ("gross_margin", "yoy_revenue_growth") if c in grp.columns]
            if not cols:
                continue
            med = grp.groupby("q_end")[cols].median().sort_index()
            if len(med) < 12:
                continue
            med_grid = med.reindex(pd.date_range(med.index.min(), med.index.max(), freq="QE"))
            for col, label in [("gross_margin", "GPM"), ("yoy_revenue_growth", "REV_YOY")]:
                if col not in med_grid.columns:
                    continue
                ds_grid = med_grid[col].diff(1)
                if len(ds_grid.dropna()) >= 12 and float(ds_grid.dropna().std()) > 1e-12:
                    sec_fund[str(sec)][label] = ds_grid

    sec_mkt_ex: dict[str, pd.Series] = {}
    if not mkt_pit.empty:
        pivot = mkt_pit.pivot_table(index="date", columns="ticker", values="close_adj", aggfunc="last").sort_index()
        if "VNINDEX" in pivot.columns:
            d_ret = pivot.pct_change(fill_method=None).clip(-0.15, 0.15)
            mkt_d = d_ret["VNINDEX"].dropna()
            for sec in valid_sectors:
                syms = [s for s in stocks_df.loc[stocks_df["sector"] == sec, "symbol"] if s in d_ret.columns]
                if len(syms) < 3:
                    continue
                sec_r = d_ret[syms].mean(axis=1)
                pair = pd.concat([sec_r.rename("s"), mkt_d.rename("m")], axis=1).dropna()
                if len(pair) < 200:
                    continue
                cov = np.cov(pair["s"], pair["m"], ddof=1)
                b = cov[0, 1] / (cov[1, 1] + 1e-12)
                sec_mkt_ex[sec] = pair["s"] - b * pair["m"]

    candidates: list[dict[str, Any]] = []

    for metric in ("GPM", "REV_YOY"):
        for i, sa in enumerate(valid_sectors):
            if metric not in sec_fund[sa]:
                continue
            for j, sb in enumerate(valid_sectors):
                if i == j or metric not in sec_fund[sb]:
                    continue
                for lag in (0, 1, 2):
                    if lag == 0 and i > j:
                        continue
                    aligned = pd.concat(
                        [sec_fund[sa][metric].shift(lag).rename("a"), sec_fund[sb][metric].rename("b")],
                        axis=1,
                    ).dropna()
                    if len(aligned) < 12:
                        continue
                    av, bv = aligned["a"].to_numpy(), aligned["b"].to_numpy()
                    if np.std(av) < 1e-12 or np.std(bv) < 1e-12:
                        continue
                    rho, p_val = spearman_corr_pvalue(av, bv)
                    if not math.isfinite(rho) or not math.isfinite(p_val):
                        continue
                    sign = 1 if rho > 0 else -1
                    stab = compute_sign_stability(av, bv, sign, method="spearman", min_sub_n=4)
                    src_id = f"SECTOR:{sa}"
                    dst_id = f"SECTOR:{sb}"
                    rel_type = "SECTOR_CO_MOVES" if lag == 0 else "SECTOR_LEADS"
                    candidates.append(
                        {
                            "edge_id": str(
                                uuid.uuid5(
                                    uuid.NAMESPACE_DNS,
                                    f"{src_id}:{dst_id}:{rel_type}:{metric}:{lag}",
                                )
                            ),
                            "src_node_id": src_id,
                            "dst_node_id": dst_id,
                            "source_node_id": src_id,
                            "target_node_id": dst_id,
                            "relation_type": rel_type,
                            "layer": "VALUE_CHAIN",
                            "direction": "UNDIRECTED" if lag == 0 else "DIRECTED",
                            "channel": f"FUNDAMENTAL_{metric}",
                            "lag_days": lag * 90,
                            "weight": round(abs(float(rho)), 4),
                            "corr_value": round(float(rho), 4),
                            "direction_sign": sign,
                            "p_value": float(p_val),
                            "sign_stability": round(stab, 3),
                            "tercile_stable": bool(stab >= (2.0 / 3.0 - 1e-9)),
                            "n_obs": len(aligned),
                            "verification_tier": "QUANT_ONLY",
                            "effective_from": str(as_of_date),
                            "valid_to": _MAX_VALID_TO.isoformat(),
                        }
                    )

    for i, sa in enumerate(valid_sectors):
        if sa not in sec_mkt_ex:
            continue
        for j, sb in enumerate(valid_sectors):
            if i == j or sb not in sec_mkt_ex:
                continue
            for lag in (0, 5, 20):
                if lag == 0 and i > j:
                    continue
                sa_s = sec_mkt_ex[sa] if lag == 0 else sec_mkt_ex[sa].rolling(5).sum()
                sb_s = sec_mkt_ex[sb] if lag == 0 else sec_mkt_ex[sb].rolling(5).sum()
                aligned = pd.concat([sa_s.shift(lag).rename("a"), sb_s.rename("b")], axis=1).dropna()
                if len(aligned) < 150:
                    continue
                av, bv = aligned["a"].to_numpy(dtype=float), aligned["b"].to_numpy(dtype=float)
                if np.std(av) < 1e-12 or np.std(bv) < 1e-12:
                    continue
                if lag == 0:
                    rho, p_val = pearson_corr_pvalue(av, bv)
                else:
                    rho, _ = pearson_corr_pvalue(av, bv)
                    _, _, p_val = ols_newey_west_hac(bv, av, maxlags=5)
                if not math.isfinite(rho) or not math.isfinite(p_val):
                    continue
                sign = 1 if rho > 0 else -1
                stab = compute_sign_stability(av, bv, sign, method="ols", min_sub_n=50)
                src_id = f"SECTOR:{sa}"
                dst_id = f"SECTOR:{sb}"
                rel_type = "SECTOR_CO_MOVES" if lag == 0 else "SECTOR_LEADS"
                candidates.append(
                    {
                        "edge_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{src_id}:{dst_id}:{rel_type}:MKT:{lag}")),
                        "src_node_id": src_id,
                        "dst_node_id": dst_id,
                        "source_node_id": src_id,
                        "target_node_id": dst_id,
                        "relation_type": rel_type,
                        "layer": "VALUE_CHAIN",
                        "direction": "UNDIRECTED" if lag == 0 else "DIRECTED",
                        "channel": "MARKET_EXCESS",
                        "lag_days": lag,
                        "weight": round(abs(float(rho)), 4),
                        "corr_value": round(float(rho), 4),
                        "direction_sign": sign,
                        "p_value": float(p_val),
                        "sign_stability": round(stab, 3),
                        "tercile_stable": bool(stab >= (2.0 / 3.0 - 1e-9)),
                        "n_obs": len(aligned),
                        "verification_tier": "QUANT_ONLY",
                        "effective_from": str(as_of_date),
                        "valid_to": _MAX_VALID_TO.isoformat(),
                    }
                )

    q_vals = benjamini_hochberg([c["p_value"] for c in candidates])
    passed: list[dict[str, Any]] = []
    for c, qv in zip(candidates, q_vals, strict=True):
        c["fdr_q"] = round(float(qv), 5)
        c["fdr_q_value"] = c["fdr_q"]
        c["p_value"] = round(float(c["p_value"]), 5)
        if (
            abs(c["corr_value"]) >= 0.35
            and c["p_value"] < 0.05
            and c["fdr_q"] <= 0.10
            and c["tercile_stable"]
        ):
            passed.append(c)

    passed.sort(key=lambda x: (-x["weight"], x["p_value"]))
    return passed


def reconcile_macro_with_audited_docs(
    quant_snapshots: Sequence[Mapping[str, Any]],
    doc_disclosures: Mapping[str, Mapping[str, Mapping[str, Any]]],
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Algorithm 2.2: Audited Cross-Check -> CONFIRMED_BOTH | QUANT_DOC_NEUTRAL | CONFLICT_FLAG | QUANT_ONLY | DOC_ONLY.

    Enforces strict PIT (`effective_from <= as_of_date`) on doc_disclosures.
    """
    reconciled: list[dict[str, Any]] = []
    seen_doc_pairs: set[tuple[str, str]] = set()

    for snap in quant_snapshots:
        rec = dict(snap)
        ent = str(rec.get("entity_node_id") or "")
        ind = str(rec.get("indicator_name") or "")
        rec.setdefault("effective_from", str(as_of_date))
        rec.setdefault("valid_to", _MAX_VALID_TO.isoformat())

        if ent.startswith("TICKER:"):
            sym = ent.split(":", 1)[1]
            disc = doc_disclosures.get(sym, {}).get(ind)
            if disc is not None:
                eff_raw = disc.get("effective_from")
                if eff_raw:
                    eff_d = date.fromisoformat(str(eff_raw)[:10]) if not isinstance(eff_raw, date) else eff_raw
                    if eff_d > as_of_date:
                        disc = None
            if disc is not None:
                seen_doc_pairs.add((sym, ind))
                stated = int(disc.get("stated_sign", 0))
                qsign = int(rec.get("direction_sign", 0))
                if stated != 0 and stated == qsign:
                    rec["verification_tier"] = "CONFIRMED_BOTH"
                    rec["doc_stance"] = "SAME_DIRECTION"
                elif stated == 0:
                    rec["verification_tier"] = "QUANT_DOC_NEUTRAL"
                    rec["doc_stance"] = "NEUTRAL"
                else:
                    rec["verification_tier"] = "CONFLICT_FLAG"
                    rec["doc_stance"] = "OPPOSITE_DIRECTION"
                rec["audited_note_ref"] = {
                    "page_no": disc.get("page_no"),
                    "sub_doc_slot": disc.get("sub_doc_slot"),
                    "document_version_id": disc.get("document_version_id"),
                    "stated_sign": stated,
                    "has_quant_sensitivity": bool(disc.get("has_quant_sensitivity")),
                    "quote": disc.get("quote"),
                    "quote_hash": disc.get("quote_hash"),
                }
            else:
                rec["verification_tier"] = "QUANT_ONLY"
                rec["doc_stance"] = None
                rec["audited_note_ref"] = None
        else:
            rec["verification_tier"] = "QUANT_ONLY"
            rec["doc_stance"] = None
            rec["audited_note_ref"] = None
        reconciled.append(rec)

    for sym, ind_map in doc_disclosures.items():
        for ind, disc in ind_map.items():
            if (sym, ind) in seen_doc_pairs:
                continue
            eff_raw = disc.get("effective_from")
            eff_str = str(eff_raw)[:10] if eff_raw else str(as_of_date)
            if date.fromisoformat(eff_str) > as_of_date:
                continue
            reconciled.append(
                {
                    "entity_node_id": f"TICKER:{sym}",
                    "entity_level": "COMPANY",
                    "indicator_name": ind,
                    "channel": "AUDITED_NOTE",
                    "target_metric": "PBT_IMPACT",
                    "lag_periods": 0,
                    "lag_days": 0,
                    "n_obs": 0,
                    "beta_value": 0.0,
                    "corr_value": None,
                    "p_value": None,
                    "fdr_q": None,
                    "direction_sign": int(disc.get("stated_sign", 0)),
                    "sign_stability": None,
                    "tercile_stable": False,
                    "doc_stance": "DOC_DISCLOSED",
                    "verification_tier": "DOC_ONLY",
                    "audited_note_ref": {
                        "page_no": disc.get("page_no"),
                        "sub_doc_slot": disc.get("sub_doc_slot"),
                        "document_version_id": disc.get("document_version_id"),
                        "stated_sign": int(disc.get("stated_sign", 0)),
                        "has_quant_sensitivity": bool(disc.get("has_quant_sensitivity")),
                        "quote": disc.get("quote"),
                        "quote_hash": disc.get("quote_hash"),
                    },
                    "effective_from": eff_str,
                    "valid_to": _MAX_VALID_TO.isoformat(),
                    "as_of_date": str(as_of_date),
                }
            )
    return reconciled
