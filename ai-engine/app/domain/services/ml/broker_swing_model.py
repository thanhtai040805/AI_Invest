"""A temporally separated market / setup / stopping / allocation challenger.

The stopping model estimates conditional HOLD value against a sale at the next
close, using backward fitted regression, never the highest future price. Upper
layers learn the frozen lower policy on later blocks. This module is offline;
loading it does not replace a runtime model or connect to a broker/database.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler


KEY = ["date", "ticker"]
EXPERTS = ("breakout", "pullback", "reversal")
CAUSAL_MARKET = {*(f"market_return_{n}d" for n in (1, 3, 5, 10, 20, 60)),
                 "market_volatility_20d", "market_ma20_distance", "breadth_positive_1d", "breadth_above_ma20"}
CAUSAL_SETUP = {*(f"return_{n}d" for n in (1, 3, 5, 10, 20, 60)),
                *(f"volatility_{n}d" for n in (5, 20, 60)),
                *(f"relative_strength_{n}d" for n in (3, 5, 20, 60)),
                "atr_14_pct", "range_pct", "close_location", "overnight_gap", "intraday_return",
                "ma20_distance", "breakout_20d", "distance_from_low_20d", "reversal_3d", "beta_60d"}
CAUSAL_LIQUIDITY = {"volume_ratio_20d", "turnover_ratio_20d", "continuous_volume_share",
                    "log_adtv20_vnd", "liquidity_rank_pct", "foreign_flow_ratio", "foreign_flow_ratio_5d"}
CAUSAL_STATE = {"holding_sessions", "unrealized_net_return", "max_close_net_return", "drawdown_from_peak",
                "entry_relative_strength_20d", "entry_atr_14_pct", *(f"entry_expert_{n}" for n in EXPERTS)}


class _Head:
    """One fitted preprocessing boundary for a particular supervised task."""

    def __init__(self, columns, kind, trees, jobs):
        self.columns = list(columns)
        self.kind = kind
        self.imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        self.scaler = StandardScaler()
        self.trees, self.jobs = trees, jobs
        self.constant = None

    def values(self, frame):
        selected = frame[self.columns]
        if any(not pd.api.types.is_numeric_dtype(selected[c]) for c in self.columns):
            raise ValueError("Model features must be numeric")
        values = selected.to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError("Infinite features are not allowed")
        return values

    def fit(self, frame, target):
        target = np.asarray(target, dtype=float)
        if not len(frame) or len(target) != len(frame) or not np.isfinite(target).all():
            raise ValueError("Every training row needs a finite target")
        values = self.scaler.fit_transform(self.imputer.fit_transform(self.values(frame)))
        if self.kind == "probability":
            if np.unique(target).size < 2:
                self.constant = float(target[0])
                return self
            self.estimator = LogisticRegression(C=0.2, max_iter=1000)
        elif self.kind == "ridge":
            self.estimator = Ridge(alpha=30.0)
        else:
            params = dict(n_estimators=self.trees, learning_rate=0.04, num_leaves=7,
                          max_depth=3, min_child_samples=30, reg_lambda=10.0,
                          reg_alpha=0.2, n_jobs=self.jobs, verbosity=-1,
                          random_state=42, deterministic=True, force_col_wise=True)
            self.estimator = lgb.LGBMRegressor(
                objective="quantile" if self.kind == "q10" else "regression",
                **({"alpha": 0.10} if self.kind == "q10" else {}), **params)
        # Passing named frames preserves LightGBM's feature-name contract.
        self.estimator.fit(pd.DataFrame(values, columns=self.columns), target)
        return self

    def predict(self, frame):
        if not len(frame):
            return np.array([], dtype=float)
        values = self.scaler.transform(self.imputer.transform(self.values(frame)))
        if self.constant is not None:
            return np.full(len(frame), self.constant)
        named = pd.DataFrame(values, columns=self.columns)
        return (self.estimator.predict_proba(named)[:, 1] if self.kind == "probability"
                else self.estimator.predict(named))


class BrokerSwingModel:
    """Distinct specialist roles, trained across four purged historical stages.

    A: older 24 months, fit market context and conditional stopping.
    B: next six months, fit event specialists to frozen A-policy outcomes.
    C: next three months, fit arbitration from frozen A/B forecasts.
    D: final three months, calibrate frozen A/B/C on later policy outcomes.
    All labels must finish strictly before the next stage or prediction start.
    """

    ARTIFACT_VERSION = 1

    def __init__(self, n_jobs=4, n_estimators=100, min_rows=80):
        if n_jobs == 0 or n_estimators < 1 or min_rows < 2:
            raise ValueError("Invalid model capacity or minimum sample count")
        self.n_jobs, self.n_estimators, self.min_rows = n_jobs, n_estimators, min_rows
        self.is_fitted = False
        self.model_version = None

    def _head(self, columns, kind):
        return _Head(columns, kind, self.n_estimators, self.n_jobs)

    @staticmethod
    def _key(frame):
        return pd.MultiIndex.from_frame(frame[KEY])

    @staticmethod
    def _candidates(entries, metadata, labeled=False):
        events = list(metadata["expert_columns"].values())
        mask = entries["universe_eligible"] & entries[events].any(axis=1)
        if labeled:
            mask &= entries["label_status"].eq("ok")
        return entries.loc[mask].copy()

    def _validate_contract(self, entries, states, metadata):
        if metadata["max_holding_sessions"] != 7:
            raise ValueError("This broker policy requires a predeclared seven-session limit")
        if set(metadata["feature_groups"]) != {"market", "setup", "liquidity"}:
            raise ValueError("Only the three declared causal feature groups are allowed")
        for group, allowed in (("market", CAUSAL_MARKET), ("setup", CAUSAL_SETUP), ("liquidity", CAUSAL_LIQUIDITY)):
            if not set(metadata["feature_groups"][group]).issubset(allowed):
                raise ValueError("Future or undeclared columns cannot be model features")
        declared = [c for group in metadata["feature_groups"].values() for c in group]
        if (set(metadata["entry_feature_columns"]) != set(declared)
                or len(declared) != len(set(declared))
                or not set(metadata["exit_feature_columns"]).issubset(set(declared) | CAUSAL_STATE)):
            raise ValueError("Model features must match the causal feature contract")
        if metadata["expert_columns"] != {n: f"expert_{n}" for n in EXPERTS}:
            raise ValueError("Unexpected expert event contract")
        if entries.duplicated(KEY).any():
            raise ValueError("Duplicate entry keys")
        if states.duplicated(["entry_decision_date", "ticker", "holding_sessions"]).any():
            raise ValueError("Duplicate position state keys")
        if entries["date"].isna().any() or states["date"].isna().any():
            raise ValueError("Decision dates cannot be missing")
        labeled = entries.loc[entries["label_status"].eq("ok")]
        if labeled["label_end_date"].isna().any() or labeled["label_end_date"].le(labeled["date"]).any():
            raise ValueError("Completed labels must end after their entry decision")

    def _fit_stopping(self, entries, states, start, end):
        keys = self._key(entries)
        stock_states = states.rename(columns={"date": "state_date", "entry_decision_date": "date"})
        stock_states = stock_states.loc[self._key(stock_states).isin(keys)]
        valid = stock_states.loc[stock_states["state_feature_valid"]]
        complete_keys = valid.groupby(KEY)["holding_sessions"].nunique()
        complete_keys = complete_keys.index[complete_keys.eq(5)]
        entries = entries.loc[keys.isin(complete_keys)]
        if len(entries) < self.min_rows:
            raise ValueError("Insufficient fully observed stopping paths in stage A")
        self.exit_heads = {}
        self.stopping_stages = []
        for step, age in enumerate(range(5, 1, -1)):
            left, right = start + pd.DateOffset(months=6 * step), start + pd.DateOffset(months=6 * (step + 1))
            if right > end:
                raise ValueError("Stopping stages exceed stage A")
            paths = entries.loc[entries["date"].ge(left) & entries["date"].lt(right)
                                & entries["label_end_date"].lt(right)]
            if len(paths) < self.min_rows:
                raise ValueError(f"Insufficient later-block stopping paths for age {age}: {len(paths)}")
            # The already fitted later-age heads are frozen and strictly older
            # than these paths. Their rollout is a supervised target, without
            # predicting the paths on which those heads were fitted.
            downstream = self.policy_outcomes(paths, states).set_index(KEY)["policy_net_return"]
            block = valid.loc[valid["holding_sessions"].eq(age) & self._key(valid).isin(downstream.index)]
            block_keys = self._key(block)
            advantage = downstream.reindex(block_keys).to_numpy() - block["sell_next_close_net_return"].to_numpy()
            head = self._head(self.exit_columns, "mean").fit(block, advantage)
            self.exit_heads[age] = head
            self.stopping_stages.append({"holding_sessions": age, "paths": len(paths),
                                         "unique_dates": int(paths["date"].nunique()),
                                         "starts": left.date().isoformat(), "ends_exclusive": right.date().isoformat(),
                                         "latest_label_end": paths["label_end_date"].max().date().isoformat()})
        return len(entries)

    def _stopping_values(self, states):
        values = np.full(len(states), np.nan)
        valid = states["state_feature_valid"].to_numpy(dtype=bool)
        for age, head in self.exit_heads.items():
            mask = states["holding_sessions"].eq(age).to_numpy() & valid
            values[mask] = head.predict(states.loc[mask])
        values[states["holding_sessions"].eq(6).to_numpy() & valid] = 0.0
        return values

    def policy_outcomes(self, entries, states):
        """Apply the frozen stopping policy, then read its chosen future label.

        Used only for completed supervised/evaluation paths. No future value
        influences stopping predictions. Missing current state means HOLD until
        another valid decision or the precommitted limit, as in the ledger.
        """
        usable = entries.loc[entries["label_status"].eq("ok")].copy()
        decisions = states.rename(columns={"date": "state_date", "entry_decision_date": "date"}).copy()
        decisions = decisions.loc[self._key(decisions).isin(self._key(usable))]
        decisions["advantage"] = self._stopping_values(decisions)
        selling = decisions.loc[decisions["advantage"].le(0)].groupby(KEY)["holding_sessions"].min()
        ages = selling.reindex(self._key(usable)).add(1).fillna(7).to_numpy(dtype=int)
        paths = usable[[f"net_return_h{age}" for age in range(3, 8)]].to_numpy()
        outcomes = paths[np.arange(len(usable)), ages - 3]
        return pd.DataFrame({"date": usable["date"].to_numpy(), "ticker": usable["ticker"].to_numpy(),
                             "policy_net_return": outcomes, "policy_holding_sessions": ages})

    def _market_predictions(self, frame):
        return pd.DataFrame({"market_risk_probability": self.market_risk.predict(frame),
                             "market_opportunity_score": self.market_opportunity.predict(frame)}, index=frame.index)

    def _lower_predictions(self, frame):
        result = self._market_predictions(frame)
        rates = []
        for name in EXPERTS:
            active = frame[self.dataset_metadata["expert_columns"][name]].to_numpy(dtype=bool, copy=True)
            if name not in self.expert_heads:
                active[:] = False
            result[f"{name}_active"] = active.astype(float)
            for role in ("mean", "probability", "q10", "duration"):
                values = np.zeros(len(frame))
                if active.any():
                    values[active] = self.expert_heads[name][role].predict(frame.loc[active])
                if role == "duration":
                    values[active] = np.clip(values[active], 3, 7)
                result[f"{name}_{role}"] = values
            rates.append(np.where(active, result[f"{name}_mean"] / np.maximum(3, result[f"{name}_duration"]), -np.inf))
        best = np.argmax(np.array(rates), axis=0) if len(frame) else np.array([], dtype=int)
        result["selected_expert"] = np.array(EXPERTS)[best]
        result["any_expert_active"] = result[[f"{name}_active" for name in EXPERTS]].any(axis=1)
        for name in self.dataset_metadata["feature_groups"]["liquidity"]:
            result[name] = frame[name]
        for name in EXPERTS:
            result[f"{name}_context_edge"] = result[f"{name}_mean"] * (1 - result["market_risk_probability"])
        return result

    def fit(self, entries, states, metadata, prediction_start):
        self.is_fitted = False
        self._validate_contract(entries, states, metadata)
        self.dataset_metadata = metadata
        self.entry_columns = metadata["entry_feature_columns"]
        self.exit_columns = metadata["exit_feature_columns"]
        start = pd.Timestamp(prediction_start).normalize()
        boundaries = [start - pd.DateOffset(months=m) for m in (36, 12, 6, 3, 0)]
        all_usable = entries.loc[entries["universe_eligible"] & entries["label_status"].eq("ok")]
        blocks = [all_usable.loc[all_usable["date"].ge(left) & all_usable["date"].lt(right)
                                & all_usable["label_end_date"].lt(right)].copy()
                  for left, right in zip(boundaries[:-1], boundaries[1:])]
        summaries = []
        for name, block, left, right in zip("ABCD", blocks, boundaries[:-1], boundaries[1:]):
            if len(block) < self.min_rows:
                raise ValueError(f"Insufficient completed labels in stage {name}: {len(block)}")
            summaries.append({"stage": name, "starts": left.date().isoformat(),
                              "ends_exclusive": right.date().isoformat(), "rows": len(block),
                              "latest_label_end": block["label_end_date"].max().date().isoformat()})
        market_columns = metadata["feature_groups"]["market"]
        daily = blocks[0].groupby("date").agg(
            opportunity=("net_return_h3", "median"),
            lower_quartile=("net_return_h3", lambda y: y.quantile(.25)))
        daily = daily.join(blocks[0].groupby("date")[market_columns].first())
        if len(daily) < self.min_rows:
            raise ValueError("Insufficient independent market sessions in stage A")
        self.market_opportunity = self._head(market_columns, "ridge").fit(daily, daily["opportunity"])
        self.market_risk = self._head(market_columns, "probability").fit(daily, daily["lower_quartile"].lt(-.025))
        stopping = self._candidates(blocks[0], metadata)
        stopping_paths = self._fit_stopping(stopping, states, boundaries[0], boundaries[1])
        policy_blocks = []
        for block in blocks[1:]:
            events = self._candidates(block, metadata)
            outcomes = self.policy_outcomes(events, states)
            policy_blocks.append(events.merge(outcomes, on=KEY, validate="one_to_one"))
        self.expert_heads, expert_counts = {}, {}
        for name in EXPERTS:
            subset = policy_blocks[0].loc[policy_blocks[0][metadata["expert_columns"][name]]]
            expert_counts[name] = {"rows": len(subset), "active": len(subset) >= self.min_rows}
            if len(subset) < self.min_rows:
                continue
            # Event-conditioned nonlinear breakout/reversal; regularized linear
            # pullback. The latter estimates continuation without tree splits.
            self.expert_heads[name] = {
                "mean": self._head(self.entry_columns, "ridge" if name == "pullback" else "mean").fit(subset, subset["policy_net_return"]),
                "probability": self._head(self.entry_columns, "probability").fit(subset, subset["policy_net_return"].gt(0)),
                "q10": self._head(self.entry_columns, "q10").fit(subset, subset["policy_net_return"]),
                "duration": self._head(self.entry_columns, "ridge").fit(subset, subset["policy_holding_sessions"]),
            }
        if not self.expert_heads:
            raise ValueError("No event expert has enough later-block examples")
        meta = self._lower_predictions(policy_blocks[1])
        mask = meta["any_expert_active"]
        meta, outcomes = meta.loc[mask], policy_blocks[1].loc[mask]
        if len(meta) < self.min_rows:
            raise ValueError("Insufficient later-block forecasts for arbitration")
        self.meta_columns = [c for c in meta if c not in {"selected_expert", "any_expert_active"}]
        self.arbiter = {
            "mean": self._head(self.meta_columns, "mean").fit(meta, outcomes["policy_net_return"]),
            "probability": self._head(self.meta_columns, "probability").fit(meta, outcomes["policy_net_return"].gt(0)),
            "q10": self._head(self.meta_columns, "q10").fit(meta, outcomes["policy_net_return"]),
            "duration": self._head(self.meta_columns, "ridge").fit(meta, outcomes["policy_holding_sessions"]),
        }
        calibration = self._lower_predictions(policy_blocks[2])
        mask = calibration["any_expert_active"]
        calibration, target = calibration.loc[mask], policy_blocks[2].loc[mask, "policy_net_return"]
        if len(calibration) < self.min_rows:
            raise ValueError("Insufficient later-block arbitration calibration")
        self.return_bias = float(np.mean(target - self.arbiter["mean"].predict(calibration)))
        self.quantile_offset = float(np.quantile(target - self.arbiter["q10"].predict(calibration), .10))
        self.calibrator = None
        if target.gt(0).nunique() == 2:
            p = self.arbiter["probability"].predict(calibration)
            self.calibrator = LogisticRegression(C=1.0, max_iter=1000).fit(self._logits(p), target.gt(0))
        self.trained_through = max(block["label_end_date"].max() for block in blocks).date().isoformat()
        self.metadata = {"architecture": "broker_swing_policy", "research_only": True,
                         "prediction_start": start.date().isoformat(), "trained_through": self.trained_through,
                         "temporal_stages": summaries, "market_sessions": len(daily),
                         "stopping_paths": stopping_paths, "stopping_stages": self.stopping_stages,
                         "expert_samples": expert_counts,
                         "arbiter_rows": len(meta), "calibration_rows": len(calibration),
                         "probability_calibrated": self.calibrator is not None,
                         "exit_target": "downstream fitted-policy return minus next-close sale return",
                         "upper_layer_target": "realized return under frozen stage A stopping policy",
                         "purge": "full seven-session label end strictly before next stage",
                         "capacity": {"trees": self.n_estimators, "leaves": 7, "min_rows": self.min_rows}}
        self.is_fitted = True
        self.model_version = None
        return self

    @staticmethod
    def _logits(probability):
        p = np.clip(probability, 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def _check_prediction(self, dates):
        if not self.is_fitted:
            raise ValueError("Model is not fitted")
        if len(dates) and pd.to_datetime(dates).min() <= pd.Timestamp(self.trained_through):
            raise ValueError("Inference must follow every observed training/calibration label")

    def forecast_entries(self, entries, use_arbiter=True):
        self._check_prediction(entries["date"])
        rows = self._candidates(entries, self.dataset_metadata)
        lower = self._lower_predictions(rows)
        active = lower["any_expert_active"]
        lower, rows = lower.loc[active], rows.loc[active]
        result = rows[KEY + ["adtv20_shares"]].rename(columns={"date": "decision_date"}).copy()
        if use_arbiter:
            result["expected_net_return"] = self.arbiter["mean"].predict(lower) + self.return_bias
            p = self.arbiter["probability"].predict(lower)
            result["p_profit"] = self.calibrator.predict_proba(self._logits(p))[:, 1] if self.calibrator is not None and len(p) else p
            result["downside_q10"] = self.arbiter["q10"].predict(lower) + self.quantile_offset
            result["expected_holding_sessions"] = np.clip(self.arbiter["duration"].predict(lower), 3, 7)
        else:
            # Explicit raw specialist control: excludes arbitration and its
            # calibration, while retaining the same frozen context/exit policy.
            for role, column in (("mean", "expected_net_return"), ("probability", "p_profit"),
                                 ("q10", "downside_q10"), ("duration", "expected_holding_sessions")):
                result[column] = [lower.loc[i, f"{name}_{role}"] for i, name in lower["selected_expert"].items()]
        result["market_risk_probability"] = lower["market_risk_probability"]
        result["market_opportunity_score"] = lower["market_opportunity_score"]
        result["expert_id"] = lower["selected_expert"]
        result["model_version"] = self.model_version
        result["trained_through"] = self.trained_through
        return result.reset_index(drop=True)

    def forecast_exits(self, states):
        self._check_prediction(states["date"])
        rows = states.loc[states["state_feature_valid"]].copy()
        result = rows[["entry_decision_date", "ticker", "date", "holding_sessions"]].rename(columns={"date": "state_date"})
        result["continuation_net_return"] = self._stopping_values(rows)
        result["action"] = np.where(result["continuation_net_return"].gt(0), "HOLD", "EXIT")
        result["model_version"] = self.model_version
        result["trained_through"] = self.trained_through
        return result.reset_index(drop=True)

    def save(self, path):
        if not self.is_fitted:
            raise ValueError("Cannot save an unfitted model")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Hash the exact serialized artifact; do not serialize its own hash.
        self.model_version = None
        joblib.dump({"artifact_version": self.ARTIFACT_VERSION, "model": self}, path)
        self.model_version = sha256(path.read_bytes()).hexdigest()
        return self.model_version

    @classmethod
    def load(cls, path):
        path = Path(path)
        payload = joblib.load(path)
        if payload["artifact_version"] != cls.ARTIFACT_VERSION or not isinstance(payload["model"], cls):
            raise ValueError("Unsupported broker model artifact")
        model = payload["model"]
        model.model_version = sha256(path.read_bytes()).hexdigest()
        return model
