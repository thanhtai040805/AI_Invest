"""Train and evaluate one predeclared multi-stream short-swing architecture.

Runs on a local OHLCV export, with no runtime/DB/broker writes. All 2026 data in
this task has already been inspected: it is explicitly a diagnostic, not a new
holdout. Ablations measure roles and never select another candidate on 2026.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain.services.ml.broker_swing_model import BrokerSwingModel
from experiments.broker_swing_dataset import build_dataset
from experiments.broker_swing_portfolio import ENTRY_COLUMNS, EXIT_COLUMNS, BrokerSwingPolicy, simulate_broker_swing


def write_json(path, value):
    def convert(item):
        if isinstance(item, (pd.Timestamp, np.datetime64)):
            return pd.Timestamp(item).isoformat()
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, Path):
            return str(item)
        raise TypeError(type(item).__name__)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    default=convert, allow_nan=False), encoding="utf-8")


def prepare_source(path, allow_missing_index_sessions=False):
    bars = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    bars["date"] = pd.to_datetime(bars["date"])
    tolerance = bars["close"].abs().clip(lower=1) * 1e-8
    invalid = (bars["high"].add(tolerance).lt(bars[["open", "close", "low"]].max(axis=1))
               | bars["low"].sub(tolerance).gt(bars[["open", "close", "high"]].min(axis=1)))
    quality = {"rows": len(bars), "stock_tickers": int(bars.loc[bars["ticker"].ne("VNINDEX"), "ticker"].nunique()),
               "date_min": bars["date"].min(), "date_max": bars["date"].max(),
               "invalid_raw_ohlc_rows": int(invalid.sum()), "duplicate_keys": int(bars.duplicated(["date", "ticker"]).sum())}
    bars.loc[invalid, ["open", "high", "low", "close"]] = np.nan
    missing = sorted(set(bars["date"]).difference(bars.loc[bars["ticker"].eq("VNINDEX"), "date"]))
    if missing and not allow_missing_index_sessions:
        raise ValueError("Inspect missing index dates and explicitly allow price-free calendar placeholders")
    if missing:
        bars = pd.concat([bars, pd.DataFrame({"date": missing, "ticker": "VNINDEX"})], ignore_index=True)
    quality["missing_index_session_dates"] = missing
    quality["raw_quote_policy"] = "invalid quotes retained with NaN prices; no synthetic fills"
    quality["missing_index_policy"] = "NaN placeholders; no forward fill"
    return bars, quality


def forecasts_for(entries, states, metadata, start, end, calendar, output, n_jobs=4):
    """Quarterly refits; each entry binds its future exit states to its artifact."""
    period = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    if len(period) < 8:
        raise ValueError("Evaluation requires at least eight observed sessions")
    # This period-wide deadline is declared before observing any stock outcome.
    last_decision = period[-8]
    candidates = entries.loc[entries["date"].ge(start) & entries["date"].le(last_decision)
                             & entries["universe_eligible"]
                             & entries[list(metadata["expert_columns"].values())].any(axis=1)]
    full, specialist, exit_chunks, outcome_chunks, artifact_records = [], [], [], [], []
    for quarter, chunk in candidates.groupby(candidates["date"].dt.to_period("Q"), sort=True):
        fit_start = quarter.start_time
        historical = entries.loc[entries["date"].ge(fit_start - pd.DateOffset(months=36))
                                 & entries["date"].lt(fit_start)]
        historical_states = states.loc[states["entry_decision_date"].ge(fit_start - pd.DateOffset(months=36))
                                       & states["entry_decision_date"].lt(fit_start)]
        print(f"FIT {quarter}: four purged entry stages and four stopping stages", flush=True)
        model = BrokerSwingModel(n_jobs=n_jobs).fit(historical, historical_states, metadata, fit_start)
        artifact = output / f"model_{quarter}.joblib"
        model.save(artifact)
        write_json(output / f"model_{quarter}_metadata.json", model.metadata)
        full.append(model.forecast_entries(chunk))
        specialist.append(model.forecast_entries(chunk, use_arbiter=False))
        chunk_keys = pd.MultiIndex.from_frame(chunk[["date", "ticker"]])
        state_keys = pd.MultiIndex.from_frame(states[["entry_decision_date", "ticker"]])
        # The state may occur in the following quarter; the ENTRY artifact still
        # predicts it. This is not a switch to the next refit's stopping policy.
        state_chunk = states.loc[state_keys.isin(chunk_keys) & states["date"].le(end)]
        predicted_states = model.forecast_exits(state_chunk)
        exit_chunks.append(predicted_states)
        outcome_chunks.append(model.policy_outcomes(chunk, state_chunk))
        artifact_records.append({"quarter": str(quarter), "path": artifact.resolve(),
                                 "sha256": model.model_version, "trained_through": model.trained_through,
                                 "entry_rows": len(full[-1]), "exit_states": len(predicted_states),
                                 "exit_binding": "same artifact as original entry across refit boundary"})
        print(f"FORECAST {quarter}: entries={len(full[-1])}, states={len(predicted_states)}, experts={model.metadata['expert_samples']}", flush=True)
    if not full:
        return (pd.DataFrame(columns=sorted(ENTRY_COLUMNS)), pd.DataFrame(columns=sorted(ENTRY_COLUMNS)),
                pd.DataFrame(columns=sorted(EXIT_COLUMNS)),
                pd.DataFrame(columns=["date", "ticker", "policy_net_return", "policy_holding_sessions"]), [])
    return (pd.concat(full, ignore_index=True), pd.concat(specialist, ignore_index=True),
            pd.concat(exit_chunks, ignore_index=True), pd.concat(outcome_chunks, ignore_index=True), artifact_records)


def evaluate_period(entries, states, metadata, bars, calendar, start, end, output, n_jobs):
    output.mkdir(parents=True, exist_ok=True)
    forecasts, specialists, exits, outcomes, artifacts = forecasts_for(
        entries, states, metadata, start, end, calendar, output, n_jobs)
    forecasts.to_parquet(output / "entry_forecasts.parquet", index=False)
    specialists.to_parquet(output / "specialist_forecasts.parquet", index=False)
    exits.to_parquet(output / "exit_forecasts.parquet", index=False)
    outcomes.to_parquet(output / "observed_policy_outcomes.parquet", index=False)
    write_json(output / "artifacts.json", artifacts)
    period_bars = bars.loc[bars["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
    policy = BrokerSwingPolicy()
    configurations = {
        "full": (forecasts, policy, "adaptive"),
        "static_exit_same_entry": (forecasts, policy, "static"),
        "specialists_without_arbiter": (specialists, policy, "adaptive"),
        "friction_100bps": (forecasts, replace(policy, friction_bps=100), "adaptive"),
    }
    results = {}
    for name, (entry_rows, capital_policy, mode) in configurations.items():
        trades, nav, metrics = simulate_broker_swing(entry_rows, exits, period_bars, capital_policy, mode)
        trades.to_csv(output / f"{name}_trades.csv", index=False)
        nav.to_csv(output / f"{name}_nav.csv", index=False)
        write_json(output / f"{name}_metrics.json", metrics)
        results[name] = metrics
        print(f"{output.name} {name}: NAV={metrics['total_return']:.2%}, PF={metrics['profit_factor']}, trades={len(trades)}, hold={metrics['mean_actual_holding_sessions']}", flush=True)
    labeled = forecasts.merge(outcomes, left_on=["decision_date", "ticker"], right_on=["date", "ticker"], how="inner")
    eligible = entries.loc[entries["date"].between(pd.Timestamp(start), pd.Timestamp(end)) & entries["universe_eligible"]]
    diagnostics = {"forecast_rows": len(forecasts), "completed_policy_labels": len(labeled),
                   "censored_forecast_rows": len(forecasts) - len(labeled),
                   "eligible_censored_labels": int(eligible["label_status"].ne("ok").sum()),
                   "policy_rmse": float(np.sqrt(np.mean((labeled["expected_net_return"] - labeled["policy_net_return"]) ** 2))) if len(labeled) else None,
                   "profit_probability_brier": float(np.mean((labeled["p_profit"] - labeled["policy_net_return"].gt(0)) ** 2)) if len(labeled) else None,
                   "q10_breach_fraction": float(labeled["policy_net_return"].lt(labeled["downside_q10"]).mean()) if len(labeled) else None,
                   "corporate_actions_modeled": False,
                   "complete_path_censoring": "Supervised labels require all H7 prices; this can exclude an otherwise observable early sale"}
    index = period_bars.loc[period_bars["ticker"].eq("VNINDEX") & period_bars["close"].gt(0)].sort_values("date")
    results["diagnostics"] = diagnostics
    results["benchmarks"] = {"cash_return": 0.0, "vnindex_price_return": float(index["close"].iloc[-1] / index["close"].iloc[0] - 1) if len(index) else None}
    write_json(output / "summary.json", results)
    return results


def development_gate(records):
    full = [r["full"] for r in records]
    profit = sum(r["gross_profit_vnd"] for r in full)
    loss = sum(r["gross_loss_vnd"] for r in full)
    trades = sum(r["closed_trades"] for r in full)
    passed = bool(trades >= 150 and profit > 0 and (loss == 0 or profit / loss >= 1.2)
                  and all(r["total_return"] > 0 and r["max_drawdown"] <= .10 and not r["open_positions"] for r in full)
                  and all(r["friction_100bps"]["total_return"] > 0 and not r["friction_100bps"]["open_positions"] for r in records))
    return {"passed": passed, "closed_trades": trades, "profit_factor": profit / loss if loss else None,
            "annual_nav_returns": [r["total_return"] for r in full],
            "annual_friction_100bps_returns": [r["friction_100bps"]["total_return"] for r in records],
            "candidate_count": 1, "selection": "one architecture; ablations do not select on diagnostic"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-missing-index-sessions", action="store_true")
    parser.add_argument("--development-years", nargs="+", type=int, default=[2023, 2024, 2025])
    parser.add_argument("--diagnostic-start", default="2026-01-01")
    parser.add_argument("--diagnostic-end", default="2026-10-01")
    parser.add_argument("--n-jobs", type=int, default=4)
    args = parser.parse_args()
    years = sorted(set(args.development_years))
    if not years or pd.Timestamp(year=years[-1], month=12, day=31) >= pd.Timestamp(args.diagnostic_start):
        raise ValueError("Development must end before inspected diagnostic")
    if pd.Timestamp(args.diagnostic_start) >= pd.Timestamp(args.diagnostic_end):
        raise ValueError("Diagnostic end must follow start")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Preserve earlier artifacts; output must be empty")
    args.output.mkdir(parents=True, exist_ok=True)
    bars, quality = prepare_source(args.bars, args.allow_missing_index_sessions)
    base = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), base / "app/domain/services/ml/broker_swing_model.py",
             base / "experiments/broker_swing_dataset.py", base / "experiments/broker_swing_portfolio.py"]
    write_json(args.output / "protocol.json", {
        "architecture": "market + event specialists + conditional stopping + arbitration + capital ledger",
        "source_path": args.bars.resolve(), "source_sha256": sha256(args.bars.read_bytes()).hexdigest(),
        "code_sha256": {str(p.relative_to(base)): sha256(p.read_bytes()).hexdigest() for p in paths},
        "capacity": {"trees": 100, "leaves": 7, "minimum_role_rows": 80}, "candidate_count": 1,
        "refit": "quarterly, full four-stage fit, rolling36months", "exit_binding": "original entry artifact",
        "development_years": years, "diagnostic_start": args.diagnostic_start, "diagnostic_end": args.diagnostic_end,
        "diagnostic_previously_inspected": True, "fresh_holdout": False,
        "capital_policy": asdict(BrokerSwingPolicy()), "quality": quality,
        "freeze": "No hyperparameter/threshold adaptation after diagnostic results",
        "gate": "DEV>=150trades,PF>=1.2,positive each year including100bps stress,DD<=10%,resolved positions",
        "sources_limitations": ["Current export vintage, not certified point-in-time universe/revisions",
                                "Raw-price PnL omits corporate entitlements; no observed auction/book fills"]})
    print("BUILD causal events and holding states", flush=True)
    entries, states, metadata = build_dataset(bars)
    entries.to_parquet(args.output / "entry_dataset.parquet", index=False)
    states.to_parquet(args.output / "position_states.parquet", index=False)
    write_json(args.output / "dataset_metadata.json", metadata)
    print(f"DATASET entries={len(entries)}, states={len(states)}, setups={metadata['expert_event_counts']}", flush=True)
    calendar = pd.DatetimeIndex(sorted(bars.loc[bars["ticker"].eq("VNINDEX"), "date"].unique()))
    records = [evaluate_period(entries, states, metadata, bars, calendar,
                               f"{year}-01-01", f"{year}-12-31", args.output / f"dev_{year}", args.n_jobs)
               for year in years]
    lock = development_gate(records)
    write_json(args.output / "development_lock.json", lock)
    print(f"LOCKED architecture; development pass={lock['passed']}", flush=True)
    diagnostic = evaluate_period(entries, states, metadata, bars, calendar, args.diagnostic_start,
                                 args.diagnostic_end, args.output / "inspected_2026", args.n_jobs)
    positive = diagnostic["full"]["total_return"] > 0 and diagnostic["friction_100bps"]["total_return"] > 0
    write_json(args.output / "results.json", {"development": lock, "annual_detail": dict(zip(years, records)),
                                             "inspected_diagnostic": diagnostic, "fresh_holdout": False,
                                             "research_gates_passed": lock["passed"] and positive,
                                             "promotion_status": "UNVERIFIED_FRESH_FORWARD_REQUIRED" if lock["passed"] and positive else "REJECTED_PROFITABILITY",
                                             "runtime_model_changed": False})


if __name__ == "__main__":
    main()
