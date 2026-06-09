"""Factor Orthogonalization — removes cross-factor multicollinearity.

Three strategies:
  1. CLUSTER_AVG     — detect highly correlated clusters, average within each cluster
  2. WITHIN_GROUP_PCA — PCA within each economic group, keep top components
  3. GRAM_SCHMIDT    — Gram-Schmidt with economic priority ordering (preserves interpretability)

Architecture:
  - ``compute_historical_correlation(panel_df)`` → correlation matrix
  - ``FactorOrthogonalizer.fit(panel_df, group_map)`` → learns transformation
  - ``transform(factor_ranks)`` → applies to single-date cross-section

Edge cases handled:
  - Single-factor groups (no orthogonalization needed)
  - Perfectly correlated factors (std=0 in PCA → skip group)
  - Groups with only 1 component after PCA → pass through
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logger = logging.getLogger(__name__)


class OrthogonalizationMethod(Enum):
    CLUSTER_AVG = "cluster_avg"
    WITHIN_GROUP_PCA = "within_group_pca"
    GRAM_SCHMIDT = "gram_schmidt"


# ── Economic priority ordering (used by Gram-Schmidt) ─────────────
# Earlier factors are preserved; later factors have later effects removed.
ECONOMIC_PRIORITY = [
    # 1. Risk — first-order exposure (orthogonalized)
    "SIZE", "VOL_20D_ORTHO",
    # 2. Value & Growth
    "EVEBITDA_INV", "HML_REAL",
    # 3. Quality & Safety (TTM-based)
    "ROE_NORM", "NM", "GM", "YOY_REV", "PIOTROSKI_F",
    # 4. Money Flow — leading indicator on HOSE
    "FOREIGN_NET_5D",
]

# Group definitions for WITHIN_GROUP_PCA & CLUSTER_AVG
DEFAULT_GROUP_MAP = {
    "risk":        ["SIZE", "VOL_20D_ORTHO"],
    "value":       ["EVEBITDA_INV", "HML_REAL"],
    "quality":     ["ROE_NORM", "NM", "GM", "YOY_REV", "PIOTROSKI_F"],
    "flow":        ["FOREIGN_NET_5D"],
}

# Known high-correlation pairs for fast-lookup (no historical data needed)
KNOWN_HIGH_CORR_PAIRS: list[tuple[str, str]] = [
    ("NM", "GM"),
    ("ROE_NORM", "NM"),
]


@dataclass
class OrthogonalizationResult:
    """Result of fitting orthogonalization."""
    method: OrthogonalizationMethod
    n_factors_in: int
    n_factors_out: int
    merged_clusters: list[list[str]] = field(default_factory=list)
    explained_variance: Optional[float] = None
    pca_components: Optional[dict[str, int]] = field(default_factory=dict)
    priority_order: Optional[list[str]] = None


# ── 1. Correlation helpers ────────────────────────────────────────

def compute_factor_correlation(
    panel_df: pd.DataFrame,
    method: str = "spearman",
) -> pd.DataFrame:
    """Compute pairwise correlation between factors from historical panel.

    Args:
        panel_df: columns = factor_ids, index = (date, symbol) multi-index
                  or a single-date cross-section.
        method: 'spearman' (default) or 'pearson'.

    Returns:
        Correlation matrix (factor_id × factor_id).
    """
    if method == "spearman":
        return panel_df.corr(method="spearman")
    return panel_df.corr(method="pearson")


def detect_collinear_pairs(
    corr_matrix: pd.DataFrame,
    threshold: float = 0.7,
    group_map: Optional[dict[str, list[str]]] = None,
) -> list[tuple[str, str, float]]:
    """Find factor pairs with |correlation| > threshold.

    If group_map is provided, only checks within the same group
    (cross-group correlations are typically structural, not redundant).
    """
    pairs: list[tuple[str, str, float]] = []
    factors = list(corr_matrix.columns)

    # Build set of all factor IDs in each group for faster lookup
    group_set: dict[str, set[str]] = {}
    if group_map:
        for g, members in group_map.items():
            group_set[g] = set(members)

    for i, f1 in enumerate(factors):
        for f2 in factors[i + 1:]:
            if group_map:
                # Only flag if they share a common group
                in_same_group = any(
                    f1 in members and f2 in members
                    for members in group_set.values()
                )
                if not in_same_group:
                    continue
            corr_val = corr_matrix.loc[f1, f2]
            if abs(corr_val) > threshold:
                pairs.append((f1, f2, round(corr_val, 3)))

    return sorted(pairs, key=lambda x: -abs(x[2]))


def build_correlation_clusters(
    pairs: list[tuple[str, str, float]],
) -> list[list[str]]:
    """Build graph-connected clusters from correlated pairs.

    If A↔B and A↔C, cluster = [A, B, C].
    """
    adj: dict[str, set[str]] = {}
    for f1, f2, _ in pairs:
        adj.setdefault(f1, set()).add(f2)
        adj.setdefault(f2, set()).add(f1)

    visited: set[str] = set()
    clusters: list[list[str]] = []
    for node in adj:
        if node in visited:
            continue
        # BFS
        stack = [node]
        cluster: list[str] = []
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            cluster.append(cur)
            for nb in adj.get(cur, set()):
                if nb not in visited:
                    stack.append(nb)
        if len(cluster) > 1:
            clusters.append(sorted(cluster))

    return clusters


# ── 2. Gram-Schmidt orthogonalization ─────────────────────────────

def gram_schmidt_orthogonalize(
    df: pd.DataFrame,
    priority_order: list[str],
) -> pd.DataFrame:
    """Apply Gram-Schmidt in the given priority order.

    Each factor has the effect of all earlier (higher-priority) factors
    regressed out.  The first factor in ``priority_order`` is unchanged.

    Args:
        df: columns = factor_ids (ranks or raw values).
        priority_order: ordered list of factor IDs (earlier = preserved).

    Returns:
        DataFrame with same columns, now orthogonal.
    """
    available = [f for f in priority_order if f in df.columns]
    if not available:
        return df

    result = df[available].copy()
    ortho_basis: list[pd.Series] = []

    for factor in available:
        raw = df[factor].values
        resid = raw.copy()
        # Remove projection onto each already-orthogonalized basis vector
        for basis in ortho_basis:
            b = basis.values
            denom = np.dot(b, b)
            if denom > 1e-12:
                proj = np.dot(raw, b) / denom
                resid = resid - proj * b
        result[factor] = resid
        ortho_basis.append(result[factor])

    # Preserve ordering from priority list, then any remaining columns
    remaining = [c for c in df.columns if c not in available]
    if remaining:
        result = pd.concat([result, df[remaining]], axis=1)

    return result


# ── 3. Within-group PCA ────────────────────────────────────────────

def _pca_transform_group(
    group_data: pd.DataFrame,
    variance_ratio: float = 0.90,
    max_components: Optional[int] = None,
) -> tuple[pd.DataFrame, int, float]:
    """Apply PCA to a group's factor matrix.

    Returns:
        (transformed_df, n_components, explained_variance).
        If only 1 component is used, the column is named '{group}_PC1'.
    """
    if group_data.shape[1] <= 1:
        return group_data, 1, 1.0

    # Center
    centered = group_data - group_data.mean(axis=0)
    centered = centered.fillna(0)

    n_features = centered.shape[1]
    if n_features < 2:
        return group_data, 1, 1.0

    cov = centered.cov().values
    try:
        eigvals, eigvecs = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        return group_data, n_features, 0.0

    # Sort descending
    idx = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]

    # Clip negative eigenvalues
    eigvals = np.maximum(eigvals, 0.0)
    total_var = eigvals.sum()
    if total_var <= 0:
        return group_data, n_features, 0.0

    # Determine number of components
    cum_var = np.cumsum(eigvals) / total_var
    n_components = int(np.searchsorted(cum_var, variance_ratio) + 1)
    if max_components is not None:
        n_components = min(n_components, max_components)

    # Project
    components = eigvecs[:, :n_components]
    projected = centered.values @ components

    col_names = [f"PC{i + 1}" for i in range(n_components)]
    result = pd.DataFrame(
        projected, index=group_data.index, columns=col_names,
    )
    ev_ratio = cum_var[n_components - 1] if n_components <= len(cum_var) else 1.0
    return result, n_components, float(ev_ratio)


def within_group_pca(
    df: pd.DataFrame,
    group_map: dict[str, list[str]],
    variance_ratio: float = 0.90,
) -> tuple[pd.DataFrame, dict[str, int], float]:
    """Apply PCA independently within each factor group.

    Args:
        df: columns = factor_ids (ranks).
        group_map: {group_name: [factor_id, ...]}.
        variance_ratio: cumulative variance to retain per group.

    Returns:
        (transformed_df, {group: n_components}, overall_explained_variance).
    """
    result_parts: list[pd.DataFrame] = []
    comps: dict[str, int] = {}
    weighted_ev = 0.0
    total_dims = 0

    for group_name, factor_ids in group_map.items():
        available = [f for f in factor_ids if f in df.columns]
        if not available:
            continue
        group_data = df[available]
        n_in = group_data.shape[1]
        total_dims += n_in

        if n_in <= 1:
            result_parts.append(group_data)
            comps[group_name] = 1
            weighted_ev += 1.0 * n_in
            continue

        transformed, n_out, explained = _pca_transform_group(
            group_data, variance_ratio=variance_ratio,
        )
        comps[group_name] = n_out
        weighted_ev += explained * n_in
        result_parts.append(transformed)

    overall_ev = weighted_ev / max(total_dims, 1)
    result = pd.concat(result_parts, axis=1)
    return result, comps, overall_ev


# ── 4. Cluster averaging ───────────────────────────────────────────

def cluster_average(
    df: pd.DataFrame,
    clusters: list[list[str]],
    known_pairs: Optional[list[tuple[str, str]]] = None,
) -> pd.DataFrame:
    """Merge highly correlated factors by averaging within each cluster.

    Factors not in any cluster are passed through unchanged.

    Args:
        df: columns = factor_ids (ranks).
        clusters: list of clusters (from ``build_correlation_clusters()``
                  or manually defined).
        known_pairs: if provided, used to build additional clusters.

    Returns:
        DataFrame with factor columns replaced by cluster averages.
    """
    result = df.copy()

    if known_pairs:
        pairs = [(f1, f2, 0.0) for f1, f2 in known_pairs]
        extra_clusters = build_correlation_clusters(pairs)
        all_clusters = clusters + extra_clusters
    else:
        all_clusters = clusters

    used: set[str] = set()
    for cluster in all_clusters:
        available = [f for f in cluster if f in df.columns]
        if len(available) < 2:
            continue
        # Average and replace all columns in cluster
        avg = df[available].mean(axis=1, skipna=True)
        for f in available:
            result[f] = avg
        used.update(available)

    # Remaining (non-merged) factors: flag them as singleton clusters
    logger.info(
        "cluster_average: merged %d factors into %d clusters, "
        "kept %d singletons",
        len(used), len(all_clusters), len(result.columns) - len(used),
    )
    return result


# ── 5. FactorOrthogonalizer (main class) ──────────────────────────

class FactorOrthogonalizer:
    """Fitted orthogonalization pipeline.

    Usage:
        orth = FactorOrthogonalizer(method="within_group_pca")
        orth.fit(historical_panel, group_map)
        orthogonalized = orth.transform(today_ranks)
    """

    def __init__(
        self,
        method: OrthogonalizationMethod | str = OrthogonalizationMethod.CLUSTER_AVG,
        corr_threshold: float = 0.70,
        variance_ratio: float = 0.90,
        known_pairs: Optional[list[tuple[str, str]]] = None,
    ):
        if isinstance(method, str):
            method = OrthogonalizationMethod(method)
        self.method = method
        self.corr_threshold = corr_threshold
        self.variance_ratio = variance_ratio
        self.known_pairs = known_pairs or KNOWN_HIGH_CORR_PAIRS

        self._fitted = False
        self._clusters: list[list[str]] = []
        self._pca_comps: dict[str, int] = {}
        self._pca_ev: float = 0.0
        self._group_map: dict[str, list[str]] = {}
        self._result: Optional[OrthogonalizationResult] = None

    def fit(
        self,
        panel_df: pd.DataFrame,
        group_map: Optional[dict[str, list[str]]] = None,
    ) -> OrthogonalizationResult:
        """Learn the orthogonalization transformation from historical data.

        Args:
            panel_df: historical factor panel. Columns = factor_ids.
                      Can be MultiIndex (date, symbol) or flat.
            group_map: {group_name: [factor_id, ...]}. If None, uses DEFAULT_GROUP_MAP.

        Returns:
            OrthogonalizationResult with metadata.
        """
        self._group_map = group_map or DEFAULT_GROUP_MAP

        if self.method == OrthogonalizationMethod.CLUSTER_AVG:
            result = self._fit_cluster_avg(panel_df)
        elif self.method == OrthogonalizationMethod.WITHIN_GROUP_PCA:
            result = self._fit_within_group_pca(panel_df)
        elif self.method == OrthogonalizationMethod.GRAM_SCHMIDT:
            result = self._fit_gram_schmidt(panel_df)
        else:
            raise ValueError(f"Unknown method: {self.method}")

        self._fitted = True
        self._result = result
        return result

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply the fitted transformation to a single cross-section.

        Args:
            df: columns = factor_ids (ranks for one date).

        Returns:
            DataFrame with same index, orthogonalized factor columns.
        """
        if not self._fitted:
            raise RuntimeError(
                "FactorOrthogonalizer must be fitted before transform. "
                "Call .fit(panel_df) first."
            )

        if self.method == OrthogonalizationMethod.CLUSTER_AVG:
            return cluster_average(df, self._clusters, known_pairs=self.known_pairs)

        if self.method == OrthogonalizationMethod.WITHIN_GROUP_PCA:
            return within_group_pca(
                df, self._group_map, variance_ratio=self.variance_ratio,
            )[0]

        if self.method == OrthogonalizationMethod.GRAM_SCHMIDT:
            return gram_schmidt_orthogonalize(df, ECONOMIC_PRIORITY)

        return df

    def fit_transform(
        self,
        panel_df: pd.DataFrame,
        group_map: Optional[dict[str, list[str]]] = None,
    ) -> pd.DataFrame:
        """Convenience: fit then transform the same panel."""
        self.fit(panel_df, group_map)
        return self.transform(panel_df)

    # ── Private fitting methods ──────────────────────────────────

    def _fit_cluster_avg(self, panel_df: pd.DataFrame) -> OrthogonalizationResult:
        corr = compute_factor_correlation(panel_df)
        pairs = detect_collinear_pairs(
            corr, threshold=self.corr_threshold, group_map=self._group_map,
        )
        # Also add known pairs that might not show up in sample
        known = self.known_pairs or []
        known_filtered = [
            (f1, f2) for f1, f2 in known
            if f1 in panel_df.columns and f2 in panel_df.columns
        ]
        pairs_set = {(f1, f2) for f1, f2, _ in pairs}
        for f1, f2 in known_filtered:
            if (f1, f2) not in pairs_set and (f2, f1) not in pairs_set:
                pairs.append((f1, f2, 0.0))

        self._clusters = build_correlation_clusters(pairs)

        n_merged = sum(len(c) for c in self._clusters)
        n_kept = len(panel_df.columns) - n_merged + len(self._clusters)

        logger.info(
            "CLUSTER_AVG: %d pairs > %.2f → %d clusters, "
            "%d factors → %d effective factors",
            len(pairs), self.corr_threshold,
            len(self._clusters),
            len(panel_df.columns), n_kept,
        )

        return OrthogonalizationResult(
            method=self.method,
            n_factors_in=len(panel_df.columns),
            n_factors_out=n_kept,
            merged_clusters=self._clusters,
        )

    def _fit_within_group_pca(self, panel_df: pd.DataFrame) -> OrthogonalizationResult:
        _, comps, ev = within_group_pca(
            panel_df, self._group_map, variance_ratio=self.variance_ratio,
        )
        self._pca_comps = comps
        self._pca_ev = ev

        n_out = sum(comps.values())
        logger.info(
            "WITHIN_GROUP_PCA: %d factors → %d components (%.1f%% var)",
            len(panel_df.columns), n_out, ev * 100,
        )

        return OrthogonalizationResult(
            method=self.method,
            n_factors_in=len(panel_df.columns),
            n_factors_out=n_out,
            explained_variance=ev,
            pca_components=comps,
        )

    def _fit_gram_schmidt(self, panel_df: pd.DataFrame) -> OrthogonalizationResult:
        available = [f for f in ECONOMIC_PRIORITY if f in panel_df.columns]
        n_available = len(available)

        logger.info(
            "GRAM_SCHMIDT: %d/%d factors in priority order (no dimensionality reduction)",
            n_available, len(panel_df.columns),
        )

        return OrthogonalizationResult(
            method=self.method,
            n_factors_in=len(panel_df.columns),
            n_factors_out=len(panel_df.columns),
            priority_order=available,
        )

    @property
    def result(self) -> Optional[OrthogonalizationResult]:
        return self._result


# ── 6. Convenience functions for quick diagnostics ─────────────────

def diagnose_multicollinearity(
    panel_df: pd.DataFrame,
    group_map: Optional[dict[str, list[str]]] = None,
    threshold: float = 0.7,
) -> dict[str, Any]:
    """One-shot diagnostic: detect problematic correlations in factor set.

    Returns:
        {
            "n_factors": int,
            "high_corr_pairs": [(f1, f2, corr), ...],
            "clusters": [[f1, f2, ...], ...],
            "mean_abs_corr": float,
            "max_abs_corr": float,
        }
    """
    corr = compute_factor_correlation(panel_df)
    pairs = detect_collinear_pairs(corr, threshold=threshold, group_map=group_map)
    clusters = build_correlation_clusters(pairs)

    # Full matrix stats (absolute values, upper triangle only)
    triu = corr.where(
        np.triu(np.ones(corr.shape), k=1).astype(bool)
    )
    abs_vals = triu.abs().stack().dropna()

    return {
        "n_factors": len(panel_df.columns),
        "high_corr_pairs": pairs,
        "n_high_corr_pairs": len(pairs),
        "clusters": clusters,
        "mean_abs_corr": float(abs_vals.mean()) if len(abs_vals) > 0 else 0.0,
        "max_abs_corr": float(abs_vals.max()) if len(abs_vals) > 0 else 0.0,
    }
