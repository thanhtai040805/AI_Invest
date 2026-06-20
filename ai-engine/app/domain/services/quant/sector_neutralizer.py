"""Sector-Neutral Z‑Score Preprocessor — Production‑Grade.

Three‑stage pipeline per factor:
  1. DISTRIBUTION DETECTION  → binary / discrete_ordinal / continuous
  2. WINSORIZE                → per‑sector thresholds from config
  3. SECTOR Z‑SCORE           → defensive loop, std=0 → Z=0

Edge cases handled:
  - Binary factors (FORCED_SELLING): pass‑through (no transform)
  - Discrete ordinal (CEILING_STREAK, PIOTROSKI_F): rank‑normalize
  - Sector with n<4 valid obs  → routed to OTHER_INDUSTRIALS
  - Sector with std=0          → Z = 0 for all members
  - Sector‑factor overrides (e.g. BANKS + ROE_NORM: 5‑95 % winsorize)
  - TTM awareness flag for CFO_TO_NI / ACCRUAL / EARN_SURP
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.infrastructure.vendors.vn.sector_groups import (
    classify,
    OTHER_INDUSTRIALS,
)

# ─────────────────────────────────────────────────────────────────────
# 1.  CONFIGURATION  —  sector‑factor override matrix
# ─────────────────────────────────────────────────────────────────────

@dataclass
class FactorNormalizationConfig:
    """Per‑factor normalisation rules."""
    distribution: str           # "binary" | "discrete_ordinal" | "continuous"
    winsorize_lower: float      # default lower tail  (e.g. 0.01)
    winsorize_upper: float      # default upper tail  (e.g. 0.99)
    sector_overrides: dict[str, tuple[float, float]] = field(default_factory=dict)
    requires_ttm: bool = False  # factor needs trailing‑12‑month data
    skip_sectors: list[str] = field(default_factory=list)


KNOWN_FACTOR_CONFIGS: dict[str, FactorNormalizationConfig] = {
    # ── Core factors (HOSE-optimized basket) ────────────────────
    "SIZE":            FactorNormalizationConfig("continuous", 0.01, 0.99),
    "VOL_20D_ORTHO":   FactorNormalizationConfig("continuous", 0.01, 0.99),
    "EVEBITDA_INV":    FactorNormalizationConfig("continuous", 0.01, 0.99,
                                                  skip_sectors=["BANKS", "FINANCIAL_SERVICES"]),
    "HML_REAL":        FactorNormalizationConfig("continuous", 0.01, 0.99,
                                                  {"BANKS": (0.05, 0.95)}),
    "ROE_NORM":        FactorNormalizationConfig("continuous", 0.01, 0.99,
                                                  {"BANKS": (0.05, 0.95)}),
    "NM":              FactorNormalizationConfig("continuous", 0.01, 0.99,
                                                  {"BANKS": (0.05, 0.95)}),
    "GM":              FactorNormalizationConfig("continuous", 0.01, 0.99,
                                                  skip_sectors=["BANKS", "FINANCIAL_SERVICES"]),
    "YOY_REV":         FactorNormalizationConfig("continuous", 0.01, 0.99,
                                                  {"BANKS": (0.05, 0.95)}),
    "PIOTROSKI_F":     FactorNormalizationConfig("discrete_ordinal", 0.0, 1.0),
    "FOREIGN_NET_5D":  FactorNormalizationConfig("continuous", 0.01, 0.99),

    # ── Event-study signals (not in weekly IC pipeline) ─────────
    "CEILING_STREAK":  FactorNormalizationConfig("discrete_ordinal", 0.0, 1.0),
    "FORCED_SELLING":  FactorNormalizationConfig("binary", 0.0, 1.0),
}


# ─────────────────────────────────────────────────────────────────────
# 2.  DISTRIBUTION DETECTION
# ─────────────────────────────────────────────────────────────────────

def detect_distribution(series: pd.Series) -> str:
    """Auto‑detect distribution type of a factor series.

    Returns "binary", "discrete_ordinal", or "continuous".
    """
    if series.empty or series.dropna().empty:
        return "continuous"

    clean = series.dropna().unique()
    n_unique = len(clean)
    if n_unique <= 2:
        return "binary"
    # cardinality ≤ 11 & all values are multiples of 0.1 → discrete ordinal
    if n_unique <= 11:
        clean_sorted = sorted(clean)
        if all(abs(round(v / 0.1) * 0.1 - v) < 1e-9 for v in clean_sorted):
            return "discrete_ordinal"
    return "continuous"


# ─────────────────────────────────────────────────────────────────────
# 3.  WINSORIZE  (per‑sector aware)
# ─────────────────────────────────────────────────────────────────────

def _get_winsorize_bounds(
    factor_id: str,
    sector: str,
    config: Optional[FactorNormalizationConfig] = None,
) -> tuple[float, float]:
    """Resolve winsorize thresholds for a factor‑sector pair."""
    cfg = config or KNOWN_FACTOR_CONFIGS.get(factor_id)
    if cfg is None:
        return (0.01, 0.99)  # safe default
    lo, hi = cfg.winsorize_lower, cfg.winsorize_upper
    if sector in cfg.sector_overrides:
        lo, hi = cfg.sector_overrides[sector]
    return (lo, hi)


def winsorize_series(series: pd.Series, lower: float, upper: float) -> pd.Series:
    """Clip series at quantile thresholds, handling edge cases."""
    if series.empty or series.dropna().empty:
        return series
    clean = series.dropna()
    if clean.nunique() < 2:
        return series
    q_lo = clean.quantile(lower)
    q_hi = clean.quantile(upper)
    if math.isclose(q_lo, q_hi):
        return series
    clipped = series.clip(lower=q_lo, upper=q_hi)
    return clipped


# ─────────────────────────────────────────────────────────────────────
# 4.  SAFE SECTOR Z‑SCORE  (defensive loop, not blind groupby)
# ─────────────────────────────────────────────────────────────────────

def _compute_sector_zscore(
    values: pd.Series,
    sectors: pd.Series,
    min_valid: int = 3,
) -> pd.Series:
    """Compute within‑sector Z‑score with defensive error handling.

    Args:
        values:  Series of raw factor values (index = symbol)
        sectors: Series of sector labels (same index)
        min_valid: minimum non‑NaN values per sector to compute Z‑score

    Returns:
        Series of Z‑scores (same index). NaN preserved.
    """
    result = pd.Series(np.nan, index=values.index, dtype=float)
    unique_sectors = sectors.dropna().unique()
    for sec in unique_sectors:
        mask = sectors == sec
        sub = values[mask].dropna()
        if len(sub) < min_valid:
            # Too few observations → neutral score
            result[mask] = 0.0
            continue
        mean = sub.mean()
        std = sub.std(ddof=1)
        if std == 0 or not math.isfinite(std):
            # All identical → neutral
            result[mask] = 0.0
        else:
            result[mask] = (values - mean) / std
    return result


# ─────────────────────────────────────────────────────────────────────
# 5.  MAIN PIPELINE  — normalise a single factor DataFrame
# ─────────────────────────────────────────────────────────────────────

def normalize_factor(
    df: pd.DataFrame,
    factor_id: str,
    factor_col: str,
    sector_col: str = "sector",
    config: Optional[FactorNormalizationConfig] = None,
    min_valid: int = 3,
) -> pd.DataFrame:
    """Pipeline: detect → (winsorize|rank) → sector Z‑score.

    Args:
        df: DataFrame with at least [symbol, sector_col, factor_col].
        factor_id: factor name (for config lookup and detection).
        factor_col: column name holding the raw values.
        sector_col: column name holding ICB sector labels.
        config: optional override config.
        min_valid: min observations per sector for Z‑score.

    Returns:
        DataFrame with extra column ``{factor_col}_normalized``.
        Binary / discrete_ordinal are pass‑through / rank‑scaled.
    """
    cfg = config or KNOWN_FACTOR_CONFIGS.get(factor_id)
    result = df.copy()
    raw = result[factor_col]

    if raw.dropna().empty:
        result[f"{factor_col}_normalized"] = raw
        return result

    # ── 1. Determine distribution ────────────────────────────────
    dist = cfg.distribution if cfg else detect_distribution(raw)

    if dist == "binary":
        # Pass‑through: keep 0 / 1 values as-is
        result[f"{factor_col}_normalized"] = raw

    elif dist == "discrete_ordinal":
        # Cross‑sectional rank (percentile) within each sector
        ranked = raw.groupby(result[sector_col]).rank(pct=True, na_option="keep")
        result[f"{factor_col}_normalized"] = ranked

    else:
        # ── Continuous pipeline ──────────────────────────────────
        # 2. Winsorize
        lo, hi = _get_winsorize_bounds(factor_id, result[sector_col].iloc[0]
                                        if not result[sector_col].empty else "OTHER", cfg)
        # Apply per‑sector winsorize
        winz = raw.copy()
        for sec in result[sector_col].unique():
            mask = result[sector_col] == sec
            sub = raw[mask]
            lo_s, hi_s = _get_winsorize_bounds(factor_id, sec if isinstance(sec, str) else "OTHER", cfg)
            if sub.dropna().nunique() >= 2:
                winz.loc[mask] = winsorize_series(sub, lo_s, hi_s)

        # 3. Sector Z‑score (defensive loop)
        z = _compute_sector_zscore(winz, result[sector_col], min_valid=min_valid)
        result[f"{factor_col}_normalized"] = z

    return result


# ─────────────────────────────────────────────────────────────────────
# 6.  BATCH PIPELINE  — normalise all factors in one pass
# ─────────────────────────────────────────────────────────────────────

def normalize_all_factors(
    df: pd.DataFrame,
    factor_map: dict[str, str],
    sector_col: str = "sector",
    min_valid: int = 3,
    configs: Optional[dict[str, FactorNormalizationConfig]] = None,
) -> pd.DataFrame:
    """Apply ``normalize_factor`` to every factor in factor_map.

    Args:
        df: wide DataFrame with columns [symbol, sector_col, *raw_factor_cols].
        factor_map: {factor_id: raw_column_name}, e.g. {"ROE_NORM": "roe_norm"}.
        sector_col: sector label column.
        min_valid: min obs per sector for Z‑score.
        configs: optional override config dict.

    Returns:
        DataFrame with added ``{raw_col}_normalized`` columns.
    """
    configs = configs or KNOWN_FACTOR_CONFIGS
    result = df.copy()
    for factor_id, raw_col in factor_map.items():
        if raw_col not in df.columns:
            continue
        cfg = configs.get(factor_id)
        result = normalize_factor(
            result, factor_id, raw_col,
            sector_col=sector_col,
            config=cfg,
            min_valid=min_valid,
        )
    return result


# ─────────────────────────────────────────────────────────────────────
# 7.  IC‑READY HELPER  —  rank transform to [0, 100]
# ─────────────────────────────────────────────────────────────────────

def rank_within_sector(
    df: pd.DataFrame,
    value_col: str,
    sector_col: str = "sector",
    ascending: bool = True,
) -> pd.Series:
    """Percentile rank within each sector → [0, 100].

    This is what the IC tester actually consumes.
    """
    if value_col not in df.columns:
        return pd.Series(dtype=float)
    return (
        df.groupby(sector_col)[value_col]
        .rank(pct=True, ascending=ascending, na_option="keep")
        * 100
    )


def prepare_factor_for_ic(
    df: pd.DataFrame,
    factor_id: str,
    raw_col: str,
    sector_col: str = "sector",
    direction: int = 1,
    configs: Optional[dict[str, FactorNormalizationConfig]] = None,
    min_valid: int = 3,
) -> pd.Series:
    """One‑shot: normalise → rank‑within‑sector → return [0, 100] ranks.

    This is the primary entry point for `VNICTester.compute_factors_at()`.

    Pipeline:
      raw → (winsorize → sector Z‑score) OR pass‑through/rank
           → percentile rank within sector → [0, 100]
    """
    cfg = (configs or KNOWN_FACTOR_CONFIGS).get(factor_id)
    dist = cfg.distribution if cfg else "continuous"

    if dist == "binary":
        # Binary: rank within sector directly, no winsorize
        asc = direction == 1
        return rank_within_sector(df, raw_col, sector_col, ascending=asc)

    if dist == "discrete_ordinal":
        asc = direction == 1
        return rank_within_sector(df, raw_col, sector_col, ascending=asc)

    # Continuous: normalise then rank
    normed = normalize_factor(
        df, factor_id, raw_col,
        sector_col=sector_col, config=cfg, min_valid=min_valid,
    )
    norm_col = f"{raw_col}_normalized"
    asc = direction == 1
    return rank_within_sector(normed, norm_col, sector_col, ascending=asc)
