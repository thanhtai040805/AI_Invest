"""Hierarchical Risk Parity (HRP) Optimizer.

Applies Ledoit-Wolf shrinkage to the covariance matrix and allocates target weights
using Marcos Lopez de Prado's Hierarchical Risk Parity (HRP) algorithm.
"""

from typing import Any, Dict, List
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from sklearn.covariance import ledoit_wolf

from backtest.optimizers.base import BaseOptimizer


class HRPOptimizer(BaseOptimizer):
    """Hierarchical Risk Parity portfolio optimizer."""

    def _build_context(self, window: pd.DataFrame, active: List[str]) -> Dict[str, Any] | None:
        """Override to compute Ledoit-Wolf shrunk covariance matrix."""
        vals = window.values
        if np.isnan(vals).any():
            df_filled = window.ffill().bfill()
            if df_filled.isna().any().any():
                return None
            vals = df_filled.values

        try:
            # sklearn ledoit_wolf expects shape (n_samples, n_features)
            shrunk_cov, _ = ledoit_wolf(vals)
        except Exception:
            # Fallback to standard covariance
            shrunk_cov = window.ffill().bfill().cov().values
            if np.isnan(shrunk_cov).any():
                return None

        return {"cov": shrunk_cov, "active": active}

    def _calc_weights(self, ctx: Dict[str, Any]) -> np.ndarray:
        """Compute target weights using HRP algorithm."""
        cov = ctx["cov"]
        n = cov.shape[0]
        if n == 0:
            return self._equal_weight(0)
        if n == 1:
            return np.ones(1)

        # 1. Compute correlation matrix
        vols = np.sqrt(np.diag(cov))
        vols = np.maximum(vols, 1e-8)
        inv_vols = 1.0 / vols
        corr = cov * np.outer(inv_vols, inv_vols)
        corr = np.clip(corr, -1.0, 1.0)

        # 2. Quasi-diagonalization
        # Lopez de Prado column distance
        d_cols = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                d_cols[i, j] = np.sqrt(np.sum((corr[:, i] - corr[:, j]) ** 2))

        try:
            condensed = squareform(d_cols)
            link = linkage(condensed, method="single")
            sort_ix = leaves_list(link).tolist()
        except Exception:
            return self._equal_weight(n)

        # 3. Recursive Bisection
        w = np.ones(n)

        def get_cluster_var(cov_mat: np.ndarray, cluster_indices: List[int]) -> float:
            sub_cov = cov_mat[np.ix_(cluster_indices, cluster_indices)]
            sub_vols = np.diag(sub_cov)
            sub_vols = np.maximum(sub_vols, 1e-8)
            inv_vars = 1.0 / sub_vols
            w_inv_var = inv_vars / np.sum(inv_vars)
            return float(w_inv_var @ sub_cov @ w_inv_var)

        clusters = [sort_ix]
        while len(clusters) > 0:
            curr = clusters.pop(0)
            if len(curr) <= 1:
                continue

            mid = len(curr) // 2
            c1 = curr[:mid]
            c2 = curr[mid:]

            var_c1 = get_cluster_var(cov, c1)
            var_c2 = get_cluster_var(cov, c2)

            alpha = 1.0 - var_c1 / (var_c1 + var_c2 + 1e-12)
            alpha = np.clip(alpha, 0.0, 1.0)

            for idx in c1:
                w[idx] *= alpha
            for idx in c2:
                w[idx] *= (1.0 - alpha)

            clusters.append(c1)
            clusters.append(c2)

        return self._normalize(w)


def optimize(
    ret: pd.DataFrame,
    pos: pd.DataFrame,
    dates: pd.DatetimeIndex,
    lookback: int = 60,
) -> pd.DataFrame:
    """Module-level entry: HRP-adjusted positions."""
    return HRPOptimizer(lookback=lookback).optimize(ret, pos, dates)
