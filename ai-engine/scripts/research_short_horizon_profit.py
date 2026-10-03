"""Train, select chronologically, and evaluate the offline profit challenger.

No database, broker, production configuration or existing model is modified.
Run from ai-engine with --bars pointing at a provenance-recorded CSV/parquet.
Development years select among a finite set of predeclared candidates. That choice is
written before the final holdout is opened. Historical holdout is chronological
out-of-sample, not a claim that nobody previously inspected the market period.
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
from sklearn.metrics import brier_score_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain.services.ml.short_horizon_profit_model import ShortHorizonProfitModel
from experiments.short_horizon_profit_dataset import build_dataset
from experiments.short_horizon_profit_portfolio import PortfolioPolicy, simulate_portfolio


FAMILIES = ("linear", "boosted")
HORIZONS = (3, 5)
GATES = {"open": (0.50, 0.0), "selective": (0.55, 0.001), "strict": (0.60, 0.002)}
RESEARCH_LIMITS = {
    "min_closed_trades": 150,
    "min_profit_factor": 1.20, "min_expectancy": 0.002,
    "max_drawdown": 0.10, "positive_each_development_year": True,
}


def write_json(path: Path, value) -> None:
    def convert(item):
        if isinstance(item, (pd.Timestamp, np.datetime64)):
            return pd.Timestamp(item).isoformat()
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, Path):
            return str(item)
        raise TypeError(type(item).__name__)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=convert, allow_nan=False), encoding="utf-8")


def model_features(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return frame.set_index(["date", "ticker"])[columns]


def fit_before(dataset, metadata, family, prediction_start, output, n_jobs,
               training_window_months=60):
    if not isinstance(training_window_months, int) or isinstance(training_window_months, bool) or training_window_months < 12:
        raise ValueError("Training window must include at least twelve historical months")
    start = pd.Timestamp(prediction_start)
    calibration_start = start - pd.DateOffset(months=3)
    training_start = start - pd.DateOffset(months=training_window_months)
    usable = dataset["universe_eligible"] & dataset["label_status"].eq("ok")
    train = dataset.loc[usable & dataset["date"].ge(training_start)
                        & dataset["date"].lt(calibration_start)
                        & dataset["label_end_date"].lt(calibration_start)]
    calibration = dataset.loc[usable & dataset["date"].ge(calibration_start)
                              & dataset["date"].lt(start)
                              & dataset["label_end_date"].lt(start)]
    if len(train) < 1000 or len(calibration) < 500:
        raise ValueError(f"Insufficient temporal training/calibration rows: {len(train)}/{len(calibration)}")
    cutoff = max(train["label_end_date"].max(), calibration["label_end_date"].max())
    print(f"FIT {family} H{metadata['horizon_sessions']} through {cutoff.date()}: train={len(train)}, calibration={len(calibration)}", flush=True)
    model = ShortHorizonProfitModel(family=family, n_jobs=n_jobs).fit(
        model_features(train, metadata["feature_columns"]), train.set_index(["date", "ticker"])["net_return"],
        model_features(calibration, metadata["feature_columns"]), calibration.set_index(["date", "ticker"])["net_return"],
        trained_through=cutoff.date().isoformat(),
        metadata={"dataset": metadata, "research_only": True,
                  "purge": "label_end_date strictly before the next block",
                  "training_window_months": training_window_months,
                  "prediction_start": start.date().isoformat()},
    )
    model.save(output)
    return model


def forecasts_for(model, dataset, metadata, start, end, calendar, calibration_mode="annual",
                  training_window_months=60, model_output_dir=None):
    """Return forecasts and the last fitted model, with each outcome purged.

    monthly refreshes calibration only; monthly-refit also replaces all base
    estimators and preprocessing using a rolling historical training window.
    """
    if calibration_mode not in {"annual", "monthly", "monthly-refit"}:
        raise ValueError("Unknown calibration mode")
    if calibration_mode == "monthly-refit" and model_output_dir is None:
        raise ValueError("Monthly refit requires a directory for model provenance")
    period_calendar = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    if len(period_calendar) <= metadata["horizon_sessions"]:
        raise ValueError("Evaluation period is shorter than the holding horizon")
    # A predeclared end date limits new entries, never a ticker's future outcome.
    last_decision = period_calendar[-metadata["horizon_sessions"] - 1]
    rows = dataset.loc[dataset["universe_eligible"] & dataset["date"].ge(start)
                       & dataset["date"].le(last_decision)].copy()
    if calibration_mode == "annual":
        predictions = model.predict(model_features(rows, metadata["feature_columns"])).reset_index()
    else:
        chunks = []
        for month, month_rows in rows.groupby(rows["date"].dt.to_period("M"), sort=True):
            month_start = month.start_time
            if calibration_mode == "monthly-refit":
                if month_start > pd.Timestamp(model.metadata["prediction_start"]):
                    selection = model.metadata.get("selection")
                    artifact = Path(model_output_dir) / f"model_{model.family}_h{metadata['horizon_sessions']}_{month}.joblib"
                    model = fit_before(dataset, metadata, model.family, month_start,
                                       artifact, model.n_jobs, training_window_months)
                    if selection is not None:
                        model.metadata["selection"] = selection
                        model.save(artifact)
            else:
                calibration = dataset.loc[dataset["universe_eligible"] & dataset["label_status"].eq("ok")
                                          & dataset["date"].ge(month_start - pd.DateOffset(months=3))
                                          & dataset["date"].lt(month_start)
                                          & dataset["label_end_date"].lt(month_start)]
                if len(calibration) < 500:
                    raise ValueError(f"Insufficient completed labels before {month}: {len(calibration)}")
                cutoff = calibration["label_end_date"].max().date().isoformat()
                model.recalibrate(model_features(calibration, metadata["feature_columns"]),
                                  calibration.set_index(["date", "ticker"])["net_return"], trained_through=cutoff)
            chunks.append(model.predict(model_features(month_rows, metadata["feature_columns"])).reset_index())
        predictions = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=["date", "ticker", *model.OUTPUT_COLUMNS])
    predictions = predictions.merge(rows, on=["date", "ticker"], validate="one_to_one")
    predictions["decision_date"] = predictions["date"]
    predictions["horizon_sessions"] = metadata["horizon_sessions"]
    return predictions, model


def prediction_metrics(predictions):
    labeled = predictions.loc[predictions["label_status"].eq("ok")]
    y = labeled["net_return"].to_numpy()
    p = labeled["p_profit"].to_numpy()
    result = {
        "rows": len(predictions), "labeled_rows": len(labeled),
        "censored_rows": len(predictions) - len(labeled),
        "unconditional_profit_fraction": float(np.mean(y > 0)) if len(y) else None,
        "brier_score": float(brier_score_loss(y > 0, p)) if len(y) else None,
        "auc": float(roc_auc_score(y > 0, p)) if len(np.unique(y > 0)) == 2 else None,
        "return_rmse": float(np.sqrt(np.mean((y - labeled["expected_net_return"]) ** 2))) if len(y) else None,
        "q10_breach_fraction": float(np.mean(y < labeled["downside_q10"])) if len(y) else None,
        "adjustment_affected_labeled_rows": int(labeled["has_adjustment_in_label_window"].sum()) if "has_adjustment_in_label_window" in labeled else None,
        "material_adjustment_affected_labeled_rows": int(labeled["has_material_adjustment_in_label_window"].sum()) if "has_material_adjustment_in_label_window" in labeled else None,
    }
    bins = pd.cut(labeled["p_profit"], bins=[0, .4, .5, .55, .6, .7, 1], include_lowest=True)
    result["calibration_bins"] = [
        {"bin": str(key), "rows": len(group), "predicted": float(group["p_profit"].mean()),
         "observed": float(group["net_return"].gt(0).mean())}
        for key, group in labeled.groupby(bins, observed=True) if len(group)
    ]
    return result


def policy_for(horizon, gate, friction=20.0):
    probability, expected = GATES[gate]
    return PortfolioPolicy(holding_sessions=horizon, min_profit_probability=probability,
                           min_expected_net_return=expected, friction_bps=friction)


def run_portfolio(predictions, bars, policy, output_prefix=None):
    trades, nav, metrics = simulate_portfolio(predictions, bars, policy)
    if output_prefix is not None:
        trades.to_csv(str(output_prefix) + "_trades.csv", index=False)
        nav.to_csv(str(output_prefix) + "_nav.csv", index=False)
        stock = trades.groupby("ticker").agg(
            closed_trades=("net_pnl_vnd", "size"), wins=("net_pnl_vnd", lambda values: int(values.gt(0).sum())),
            net_pnl_vnd=("net_pnl_vnd", "sum"), mean_net_return=("net_return", "mean"),
        ).reset_index()
        stock["win_rate"] = stock["wins"] / stock["closed_trades"]
        stock.sort_values("net_pnl_vnd").to_csv(str(output_prefix) + "_by_stock.csv", index=False)
        write_json(Path(str(output_prefix) + "_metrics.json"), metrics)
    return trades, nav, metrics


def aggregate_development(records):
    trades = sum(item["closed_trades"] for item in records)
    wins = sum((item["win_rate_closed_net"] or 0) * item["closed_trades"] for item in records)
    profit = sum(max(0, item.get("gross_profit_vnd", 0)) for item in records)
    loss = sum(max(0, item.get("gross_loss_vnd", 0)) for item in records)
    expectancy = sum((item["expectancy_net_return"] or 0) * item["closed_trades"] for item in records) / trades if trades else 0
    drawdown = max(item["max_drawdown"] for item in records)
    returns = [item["total_return"] for item in records]
    profit_delays = [item["sessions_to_first_positive_realized_pnl"] for item in records]
    early_pnl = [item["realized_pnl_vnd_at_20_sessions"] for item in records]
    summary = {"closed_trades": trades, "win_rate": wins / trades if trades else None,
               "profit_factor": profit / loss if loss else None,
               "expectancy": expectancy, "max_annual_drawdown": drawdown,
               "annual_nav_returns": returns, "mean_annual_nav_return": float(np.mean(returns)),
               "first_positive_realized_pnl_dates": [item["first_positive_realized_pnl_date"] for item in records],
               "mean_sessions_to_first_positive_realized_pnl": float(np.mean(profit_delays)) if all(value is not None for value in profit_delays) else None,
               "mean_first_20_session_net_pnl_vnd": float(np.mean(early_pnl)) if all(value is not None for value in early_pnl) else None,
               "resolved_inventory_each_year": all(not item["open_positions"] for item in records)}
    summary["passes_development_gate"] = bool(
        trades >= RESEARCH_LIMITS["min_closed_trades"]
        and ((loss == 0 and profit > 0) or (summary["profit_factor"] is not None and summary["profit_factor"] >= RESEARCH_LIMITS["min_profit_factor"]))
        and expectancy >= RESEARCH_LIMITS["min_expectancy"] and drawdown <= RESEARCH_LIMITS["max_drawdown"]
        and all(value > 0 for value in returns) and summary["resolved_inventory_each_year"]
    )
    return summary


def quality_report(bars):
    stocks = bars.loc[bars["ticker"].ne("VNINDEX")]
    tolerance = bars["close"].abs().clip(lower=1) * 1e-8
    invalid = (bars["high"].add(tolerance).lt(bars[["open", "close", "low"]].max(axis=1))
               | bars["low"].sub(tolerance).gt(bars[["open", "close", "high"]].min(axis=1)))
    return {"rows": len(bars), "tickers": int(stocks["ticker"].nunique()),
            "date_min": bars["date"].min(), "date_max": bars["date"].max(),
            "duplicate_keys": int(bars.duplicated(["date", "ticker"]).sum()),
            "nonpositive_prices": int(bars[["open", "high", "low", "close"]].le(0).any(axis=1).sum()),
            "ohlc_order_violations": int((bars["high"].lt(bars[["open", "close", "low"]].max(axis=1))
                                          | bars["low"].gt(bars[["open", "close", "high"]].min(axis=1))).sum()),
            "material_ohlc_violations": int(invalid.sum()),
            "sources": {str(k): int(v) for k, v in bars["data_source"].value_counts().items()} if "data_source" in bars else {}}


def weekly_pnl_bootstrap(trades, nav):
    """Descriptive uncertainty, keeping trades of a calendar week together.

    Includes empty weeks. Not corrected for candidate selection and not a
    forecast guarantee; weeks themselves can remain serially dependent.
    """
    if nav.empty:
        return {"weeks": 0, "mean_weekly_pnl_ci95_vnd": None}
    calendar = pd.date_range(nav["date"].min(), nav["date"].max(), freq="D")
    weeks = pd.PeriodIndex(calendar, freq="W-FRI").unique()
    realized = trades.groupby(pd.to_datetime(trades["exit_date"]).dt.to_period("W-FRI"))["net_pnl_vnd"].sum()
    values = realized.reindex(weeks, fill_value=0).to_numpy()
    samples = np.random.default_rng(42).choice(values, size=(2000, len(values)), replace=True).mean(axis=1)
    return {"weeks": len(values), "mean_weekly_pnl_vnd": float(values.mean()),
            "mean_weekly_pnl_ci95_vnd": np.quantile(samples, [.025, .975]).tolist(),
            "caveat": "Descriptive weekly block resampling, not selection-adjusted and not proof of independence"}


def baseline_forecasts(dataset, horizon, start, end, calendar, kind):
    period_calendar = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    last_decision = period_calendar[-horizon - 1]
    result = dataset.loc[dataset["universe_eligible"] & dataset["date"].ge(start) & dataset["date"].le(last_decision)].copy()
    strength = result["relative_strength_20d"] if kind == "momentum" else -result["return_3d"]
    # These are fixed ordering rules, not calibrated profit estimates.
    result["expected_net_return"] = 0.01 + strength.groupby(result["date"]).rank(pct=True) * 0.01
    result["p_profit"] = 0.60
    result["downside_q10"] = -0.05
    result["decision_date"] = result["date"]
    result["horizon_sessions"] = horizon
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--development-years", type=int, nargs="+", default=[2023, 2024, 2025])
    parser.add_argument("--holdout-start", default="2026-01-01")
    parser.add_argument("--holdout-end", default="2026-07-31")
    parser.add_argument("--diagnostic-end", default="2026-10-01")
    parser.add_argument("--n-jobs", type=int, default=4)
    parser.add_argument("--calibration-mode", choices=["annual", "monthly", "monthly-refit"], default="annual")
    parser.add_argument("--training-window-months", type=int, default=60,
                        help="Historical window including the separate final three calibration months")
    parser.add_argument("--families", choices=FAMILIES, nargs="+", default=list(FAMILIES))
    parser.add_argument("--horizons", choices=HORIZONS, type=int, nargs="+", default=list(HORIZONS))
    parser.add_argument("--holdout-previously-inspected", action="store_true",
                        help="Record that the final period is a reused diagnostic rather than an untouched holdout")
    parser.add_argument("--allow-missing-index-sessions", action="store_true",
                        help="Keep observed stock sessions by inserting index rows with NaN prices; never forward-fill index levels")
    args = parser.parse_args()
    if args.training_window_months < 12:
        raise ValueError("Training window must include at least twelve historical months")
    families = list(dict.fromkeys(args.families))
    horizons = list(dict.fromkeys(args.horizons))
    candidate_count = len(families) * len(horizons) * len(GATES)
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError("Output must be empty: preserve earlier research results and selection locks")
    args.output.mkdir(parents=True, exist_ok=True)
    years = sorted(set(args.development_years))
    if not years or pd.Timestamp(year=years[-1], month=12, day=31) >= pd.Timestamp(args.holdout_start):
        raise ValueError("Development must end before holdout")
    if pd.Timestamp(args.holdout_start) >= pd.Timestamp(args.holdout_end):
        raise ValueError("Holdout must have a positive duration")
    bars = pd.read_parquet(args.bars) if args.bars.suffix == ".parquet" else pd.read_csv(args.bars)
    bars["date"] = pd.to_datetime(bars["date"])
    bars["volume"] = bars["volume_continuous"]
    quality = quality_report(bars)
    tolerance = bars["close"].abs().clip(lower=1) * 1e-8
    invalid = (bars["high"].add(tolerance).lt(bars[["open", "close", "low"]].max(axis=1))
               | bars["low"].sub(tolerance).gt(bars[["open", "close", "high"]].min(axis=1)))
    # Quarantine inconsistent source quotes; never invent a corrected fill price.
    bars.loc[invalid, ["open", "high", "low", "close"]] = np.nan
    quality["invalid_quote_policy"] = "Raw OHLC outside tolerance quarantined to NaN for labels and fills, rows retained"
    missing_index_dates = sorted(set(bars["date"]).difference(bars.loc[bars["ticker"].eq("VNINDEX"), "date"]))
    quality["missing_index_session_dates"] = [day.date().isoformat() for day in missing_index_dates]
    if missing_index_dates:
        if not args.allow_missing_index_sessions:
            raise ValueError("Missing index sessions; inspect source and explicitly allow NaN index calendar placeholders to continue")
        placeholders = pd.DataFrame({"date": missing_index_dates, "ticker": "VNINDEX", "data_source": "calendar_placeholder_no_price"})
        bars = pd.concat([bars, placeholders], ignore_index=True).sort_values(["ticker", "date"])
    calendar = pd.DatetimeIndex(sorted(bars.loc[bars["ticker"].eq("VNINDEX"), "date"].unique()))
    source_hash = sha256(args.bars.read_bytes()).hexdigest()
    write_json(args.output / "protocol.json", {
        "source_path": args.bars.resolve(), "source_sha256": source_hash,
        "families": families, "horizons": horizons, "gates": GATES,
        "candidate_count": candidate_count, "training_window_months": args.training_window_months,
        "development_years": years, "holdout_start": args.holdout_start, "holdout_end": args.holdout_end,
        "calibration_mode": args.calibration_mode, "holdout_previously_inspected": args.holdout_previously_inspected,
        "code_sha256": {str(path.relative_to(Path(__file__).parents[1])): sha256(path.read_bytes()).hexdigest()
                        for path in [Path(__file__), Path(__file__).parents[1] / "app/domain/services/ml/short_horizon_profit_model.py",
                                     Path(__file__).parents[1] / "experiments/short_horizon_profit_dataset.py",
                                     Path(__file__).parents[1] / "experiments/short_horizon_profit_portfolio.py"]},
        "research_limits": RESEARCH_LIMITS, "default_portfolio_policy": asdict(PortfolioPolicy()),
        "selection": "passes net-profit gates first, then highest mean annual NAV return, then shorter time to positive realized net PnL on development only; if none pass, diagnostic winner only",
        "win_rate_role": "descriptive; asymmetric profitable payoffs need not win most trades",
        "freeze": "No hyperparameter or threshold adaptation after holdout results",
        "quality": quality, "missing_index_policy": "NaN placeholders, no synthetic/forward-filled prices" if missing_index_dates else "complete observed index calendar",
    })
    datasets, metadata_by_horizon = {}, {}
    candidate_records = {f"{family}_h{horizon}_{gate}": [] for family in families for horizon in horizons for gate in GATES}
    for horizon in horizons:
        print(f"BUILD H{horizon}", flush=True)
        dataset, metadata = build_dataset(bars, horizon_sessions=horizon)
        dataset.to_parquet(args.output / f"dataset_h{horizon}.parquet", index=False)
        write_json(args.output / f"dataset_h{horizon}_metadata.json", metadata)
        datasets[horizon], metadata_by_horizon[horizon] = dataset, metadata
        for family in families:
            for year in years:
                start, end = f"{year}-01-01", f"{year}-12-31"
                model = fit_before(dataset, metadata, family, start,
                                   args.output / f"model_{family}_h{horizon}_{year}.joblib", args.n_jobs,
                                   args.training_window_months)
                predictions, model = forecasts_for(model, dataset, metadata, start, end, calendar, args.calibration_mode,
                                                   args.training_window_months, args.output)
                model.save(args.output / f"model_{family}_h{horizon}_{year}_last_calibration.joblib")
                predictions.to_parquet(args.output / f"forecasts_{family}_h{horizon}_{year}.parquet", index=False)
                write_json(args.output / f"forecast_metrics_{family}_h{horizon}_{year}.json", prediction_metrics(predictions))
                period_bars = bars.loc[bars["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
                for gate in GATES:
                    trades, _, metrics = run_portfolio(predictions, period_bars, policy_for(horizon, gate))
                    metrics["gross_profit_vnd"] = float(trades.loc[trades["net_pnl_vnd"] > 0, "net_pnl_vnd"].sum())
                    metrics["gross_loss_vnd"] = float(-trades.loc[trades["net_pnl_vnd"] < 0, "net_pnl_vnd"].sum())
                    candidate_records[f"{family}_h{horizon}_{gate}"].append(metrics)
                    print(f"DEV {year} {family} H{horizon} {gate}: return={metrics['total_return']:.2%}, trades={len(trades)}, win={metrics['win_rate_closed_net']}", flush=True)
    summaries = {key: aggregate_development(values) for key, values in candidate_records.items()}
    ranked = sorted(summaries, key=lambda key: (
        summaries[key]["passes_development_gate"], summaries[key]["mean_annual_nav_return"],
        -(summaries[key]["mean_sessions_to_first_positive_realized_pnl"]
          if summaries[key]["mean_sessions_to_first_positive_realized_pnl"] is not None else float("inf")), key,
    ), reverse=True)
    winner = ranked[0]
    selection = {"selected": winner, "development_gate_passed": summaries[winner]["passes_development_gate"],
                 "candidates": summaries, "annual_detail": candidate_records,
                 "promotion_status": "RESEARCH_ONLY_PENDING_HOLDOUT" if summaries[winner]["passes_development_gate"] else "REJECTED_DEVELOPMENT_DIAGNOSTIC_ONLY",
                  "calibration_mode": args.calibration_mode, "training_window_months": args.training_window_months}
    write_json(args.output / "selection_lock.json", selection)
    print(f"LOCKED {winner}; development pass={selection['development_gate_passed']}", flush=True)
    family, horizon_text, gate = winner.split("_")
    horizon = int(horizon_text[1:])
    dataset, metadata = datasets[horizon], metadata_by_horizon[horizon]
    model = fit_before(dataset, metadata, family, args.holdout_start, args.output / "selected_model.joblib", args.n_jobs,
                       args.training_window_months)
    policy = policy_for(horizon, gate)
    model.metadata["selection"] = {"candidate": winner, "development_gate_passed": selection["development_gate_passed"], "policy": asdict(policy)}
    model.save(args.output / "selected_model.joblib")
    results = {"selected": winner, "calibration_mode": args.calibration_mode,
               "training_window_months": args.training_window_months,
               "development_gate_passed": selection["development_gate_passed"],
               "holdout_previously_inspected": args.holdout_previously_inspected, "evaluation": {}}
    periods = {"holdout": (args.holdout_start, args.holdout_end),
               "previously_inspected_diagnostic": ((pd.Timestamp(args.holdout_end) + pd.Timedelta(days=1)).date().isoformat(), args.diagnostic_end)}
    for label, (start, end) in periods.items():
        if pd.Timestamp(end) <= pd.Timestamp(start):
            continue
        predictions, model = forecasts_for(model, dataset, metadata, start, end, calendar, args.calibration_mode,
                                           args.training_window_months, args.output)
        predictions.to_parquet(args.output / f"{label}_predictions.parquet", index=False)
        period_bars = bars.loc[bars["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
        trades, nav, base = run_portfolio(predictions, period_bars, policy, args.output / label)
        base["weekly_pnl_uncertainty"] = weekly_pnl_bootstrap(trades, nav)
        _, _, stress = run_portfolio(predictions, period_bars, replace(policy, friction_bps=100.0), args.output / f"{label}_stress")
        benchmarks = {}
        for kind in ("momentum", "reversal"):
            fixed = baseline_forecasts(dataset, horizon, start, end, calendar, kind)
            _, _, benchmarks[kind] = run_portfolio(fixed, period_bars, policy_for(horizon, "open"), args.output / f"{label}_{kind}")
        index = period_bars.loc[period_bars["ticker"].eq("VNINDEX")].sort_values("date")
        observed_index = index.loc[index["close"].gt(0)]
        benchmarks["vnindex_price_return"] = float(observed_index["close"].iloc[-1] / observed_index["close"].iloc[0] - 1)
        benchmarks["cash_return"] = 0.0
        results["evaluation"][label] = {"portfolio": base, "friction_100bps": stress,
                                       "forecasts": prediction_metrics(predictions), "benchmarks": benchmarks}
        print(f"{label.upper()}: return={base['total_return']:.2%}, DD={base['max_drawdown']:.2%}, trades={base['closed_trades']}, win={base['win_rate_closed_net']}", flush=True)
    holdout = results["evaluation"]["holdout"]["portfolio"]
    stress = results["evaluation"]["holdout"]["friction_100bps"]
    results["empirical_gate_passed"] = bool(selection["development_gate_passed"]
        and holdout["closed_trades"] >= 50
        and ((holdout["profit_factor"] or 0) >= 1.2 or holdout["profit_factor_status"] == "NO_LOSING_TRADES") and holdout["total_return"] > 0
        and (holdout["expectancy_net_return"] or 0) >= .002
        and holdout["max_drawdown"] <= .10 and stress["total_return"] > 0
        and not holdout["open_positions"] and not stress["open_positions"])
    results["production_status"] = "NOT_ELIGIBLE_DATA_EXECUTION_VALIDATION_REQUIRED" if results["empirical_gate_passed"] else "REJECTED_PROFITABILITY_GATE"
    results["limitations"] = [
        f"No guaranteed future profitability; selection spans {candidate_count} predeclared candidates and still has selection risk.",
        "Historical data and corporate adjustments may be revised; export has no original publication-time ledger.",
        "Raw price profit excludes cash/stock/rights entitlements; adjusted OHLC features use the provider's current history.",
        "Next-open/close fills are daily-bar proxies, not replayed order-book executions; no price-band queue/partial-fill evidence.",
        "Historical listing coverage and delisted missing records remain a survivorship risk.",
        "August-September 2026 had already been inspected and is diagnostic, never an untouched holdout.",
        "The existing model's replay metrics cannot be treated as a matched control for this different period and entry/exit policy.",
    ]
    write_json(args.output / "report.json", results)
    model.metadata["research_result_status"] = results["production_status"]
    model.save(args.output / "selected_model_last_calibration.joblib")
    print(f"DONE {args.output.resolve()} status={results['production_status']}", flush=True)


if __name__ == "__main__":
    main()
