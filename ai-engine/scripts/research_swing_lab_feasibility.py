"""DEV-only economic diagnostics for the swing LAB, without model fitting.

Reads immutable local bars/forecasts, never a model pickle, DB or broker. Cohorts
are descriptive research directions, not stock whitelists or promotion evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.broker_swing_portfolio import BrokerSwingPolicy, simulate_broker_swing


DIMENSIONS = ["regime", "liquidity_band", "volatility_band", "hose_tick_proxy_band"]
SETUPS = ["breakout", "pullback", "reversal"]


def fingerprint(path):
    digest = sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    def convert(item):
        if isinstance(item, (pd.Timestamp, np.datetime64)):
            return pd.Timestamp(item).isoformat()
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, Path):
            return str(item)
        raise TypeError(type(item).__name__)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=convert,
                               allow_nan=False), encoding="utf-8")


def annotate(frame):
    frame = frame.copy()
    frame["year"] = frame["date"].dt.year
    up = frame["market_ma20_distance"].gt(0) & frame["market_return_20d"].gt(0)
    down = frame["market_ma20_distance"].lt(0) & frame["market_return_20d"].lt(0)
    frame["regime"] = np.select([up, down], ["uptrend", "downtrend"], default="mixed")
    frame["liquidity_band"] = pd.cut(frame["adtv20_vnd"], [0, 30e9, 100e9, np.inf],
                                       labels=["10_to_30bn", "30_to_100bn", "100bn_plus"])
    frame["volatility_band"] = pd.cut(frame["atr_14_pct"], [-np.inf, .02, .04, np.inf],
                                        labels=["atr_below_2pct", "atr_2_to_4pct", "atr_above_4pct"])
    # Exchange metadata is absent. This is sensitivity to HOSE price bands only;
    # it must never be asserted as the stock's actual venue or execution tick.
    price = frame["decision_close_vnd"]
    proxy = np.select([price.lt(10000), price.lt(50000)], [10.0, 50.0], default=100.0) / price
    frame["hose_tick_proxy_band"] = pd.cut(proxy, [0, .002, .004, np.inf],
                                             labels=["below_20bps", "20_to_40bps", "above_40bps"])
    return frame


def weekly_interval(frame, return_column):
    """Descriptive moving blocks; overlapping trades are never counted IID."""
    daily = frame.groupby("date")[return_column].mean()
    weekly = daily.groupby(daily.index.to_period("W-FRI")).mean().dropna().to_numpy()
    if len(weekly) < 8:
        return None
    rng = np.random.default_rng(42)
    block = min(4, len(weekly))
    starts = rng.integers(0, len(weekly), size=(1000, int(np.ceil(len(weekly) / block))))
    sampled = weekly[(starts[..., None] + np.arange(block)) % len(weekly)]
    averages = sampled.reshape(1000, -1)[:, :len(weekly)].mean(axis=1)
    return np.quantile(averages, [.025, .975]).tolist()


def return_summary(frame, net, gross):
    valid = frame.loc[frame[net].notna() & frame[gross].notna()]
    return {"rows": len(frame), "observed_rows": len(valid),
            "censored_rows": len(frame) - len(valid), "sessions": int(valid["date"].nunique()),
            "mean_gross_price_return": float(valid[gross].mean()) if len(valid) else None,
            "mean_net_return": float(valid[net].mean()) if len(valid) else None,
            "mean_cost_drag": float((valid[gross] - valid[net]).mean()) if len(valid) else None,
            "net_profit_fraction": float(valid[net].gt(0).mean()) if len(valid) else None,
            "gross_positive_net_nonpositive_fraction": float((valid[gross].gt(0) & valid[net].le(0)).mean()) if len(valid) else None,
            "mean_session_equal_net_return": float(valid.groupby("date")[net].mean().mean()) if len(valid) else None,
            "descriptive_week_block_net_ci95": weekly_interval(valid, net)}


def label_cohorts(entries):
    rows = []
    scopes = [("liquid_universe", entries)] + [(name, entries.loc[entries[f"expert_{name}"]]) for name in SETUPS]
    for scope, frame in scopes:
        for horizon in (3, 5, 7):
            net, gross = f"net_return_h{horizon}", f"gross_return_h{horizon}"
            for year, annual in frame.groupby("year", observed=True):
                rows.append({"scope": scope, "horizon": horizon, "dimension": "all", "cohort": "all", "year": year,
                             **return_summary(annual, net, gross)})
            for dimension in DIMENSIONS:
                for key, subset in frame.groupby(["year", dimension], observed=True):
                    rows.append({"scope": scope, "horizon": horizon, "dimension": dimension, "year": key[0],
                                 "cohort": str(key[1]), **return_summary(subset, net, gross)})
    return pd.DataFrame(rows)


def calibration_cohorts(frame):
    rows = []
    for scope, subset in (("all_forecasts", frame), ("positive_ev", frame.loc[frame["expected_net_return"].gt(0)])):
        for dimension in ["expert_id", *DIMENSIONS]:
            for key, group in subset.groupby(["year", dimension], observed=True):
                labeled = group.loc[group["policy_net_return"].notna()]
                row = {"scope": scope, "dimension": dimension, "year": key[0], "cohort": str(key[1]),
                       **return_summary(group, "policy_net_return", "policy_gross_return")}
                row.update({"mean_predicted_net_return": float(labeled["expected_net_return"].mean()) if len(labeled) else None,
                            "mean_predicted_probability": float(labeled["p_profit"].mean()) if len(labeled) else None,
                            "mean_ev_error": float((labeled["expected_net_return"] - labeled["policy_net_return"]).mean()) if len(labeled) else None,
                            "q10_breach_fraction": float(labeled["policy_net_return"].lt(labeled["downside_q10"]).mean()) if len(labeled) else None})
                rows.append(row)
    return pd.DataFrame(rows)


def matched_exits(trades, entries, policy):
    labels = entries[["date", "ticker", "exit_raw_price_vnd_h7", "net_return_h7"]].rename(columns={"date": "decision_date"}).copy()
    labels.loc[labels["net_return_h7"].isna(), "exit_raw_price_vnd_h7"] = np.nan
    matched = trades.merge(labels, on=["decision_date", "ticker"], how="left", validate="one_to_one")
    sell_notional = matched["shares"] * matched["exit_raw_price_vnd_h7"] * (1 - policy.friction_bps / 20000)
    sell_fee = (sell_notional * policy.brokerage_fee_rate).clip(lower=policy.minimum_fee)
    matched["same_size_static7_pnl_vnd"] = sell_notional - sell_fee - sell_notional * policy.sell_tax_rate - matched["entry_cash_cost"]
    matched["adaptive_minus_static7_pnl_vnd"] = matched["net_pnl_vnd"] - matched["same_size_static7_pnl_vnd"]
    matched["same_size_static7_net_return"] = matched["same_size_static7_pnl_vnd"] / matched["entry_cash_cost"]
    return matched


def stable_directions(labels, years):
    """Discovery only: compare signs across years without picking a winner."""
    directions = []
    for key, group in labels.groupby(["scope", "horizon", "dimension", "cohort"], observed=True):
        if set(group["year"]) != set(years) or group["observed_rows"].min() < 100:
            continue
        gross_positive = bool(group["mean_gross_price_return"].gt(0).all())
        net_positive = bool(group["mean_net_return"].gt(0).all())
        if gross_positive:
            directions.append({"scope": key[0], "horizon": key[1], "dimension": key[2], "cohort": key[3],
                "gross_positive_each_year": True, "net_positive_each_year": net_positive,
                "minimum_annual_observed_rows": int(group["observed_rows"].min()),
                "annual_gross_means": group["mean_gross_price_return"].tolist(),
                "annual_net_means": group["mean_net_return"].tolist(),
                "interpretation": "Research direction only; correlated observations and many descriptive comparisons"})
    return directions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--nav", type=float, default=1e9)
    parser.add_argument("--development-years", nargs="+", type=int, default=[2023, 2024, 2025])
    args = parser.parse_args()
    years = sorted(set(args.development_years))
    if not years or years[-1] > 2025 or not np.isfinite(args.nav) or args.nav <= 0:
        raise ValueError("This diagnostic uses DEV through 2025 and a finite positive cash NAV")
    lab_root = Path(__file__).resolve().parents[1] / "scratch" / "vn_swing_lab" / "runs"
    output = args.output.resolve()
    if not output.is_relative_to(lab_root) or output == lab_root:
        raise ValueError("Output must be a named immutable run under ai-engine/scratch/vn_swing_lab/runs")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Preserve existing run: output must be empty")
    inputs = [args.bars, args.research / "entry_dataset.parquet", args.research / "dataset_metadata.json", args.research / "protocol.json"]
    for year in years:
        inputs.extend(args.research / f"dev_{year}" / name for name in
                      ("entry_forecasts.parquet", "exit_forecasts.parquet", "observed_policy_outcomes.parquet",
                       "full_trades.csv", "static_exit_same_entry_trades.csv"))
    hashes = {str(path.resolve()): fingerprint(path) for path in inputs}
    source_protocol = json.loads((args.research / "protocol.json").read_text(encoding="utf-8"))
    if source_protocol["source_sha256"] != hashes[str(args.bars.resolve())]:
        raise ValueError("Bars do not match the frozen research source fingerprint")
    metadata = json.loads((args.research / "dataset_metadata.json").read_text(encoding="utf-8"))
    policy = BrokerSwingPolicy(initial_cash=args.nav)
    expected_costs = {"execution_friction_roundtrip_bps": policy.friction_bps,
                      "buy_fee_rate": policy.brokerage_fee_rate,
                      "sell_fee_rate": policy.brokerage_fee_rate, "sell_tax_rate": policy.sell_tax_rate}
    if any(metadata["costs"].get(key) != value for key, value in expected_costs.items()):
        raise ValueError("Dataset costs differ from the declared frozen portfolio convention")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "protocol.json", {"status": "DIAGNOSTIC_ONLY", "run_kind": "DEV_ECONOMIC_FEASIBILITY",
        "hypothesis": "Separate gross price edge, execution cost, forecast mispricing and same-entry exit contribution before choosing another research direction",
        "development_years": years, "nav_vnd": args.nav, "input_sha256": hashes,
        "code_sha256": {str(path.resolve()): fingerprint(path) for path in
                          (Path(__file__), Path(__file__).parents[1] / "experiments/broker_swing_portfolio.py")},
        "families_or_thresholds_selected": False, "training_performed": False, "fresh_holdout": False,
        "vn30_membership_status": "NEEDS_PIT_MEMBERSHIP", "exchange_metadata_status": "UNAVAILABLE",
        "cohort_policy": "Fixed descriptive bands; setup cohorts may overlap; no winner whitelist or threshold tuning",
        "forbidden_boundaries": ["SAG", "DB", "broker", "runtime configuration", "model pickle"]})
    print("READ frozen DEV observations; no fit or 2026 evaluation", flush=True)
    entries = pd.read_parquet(args.research / "entry_dataset.parquet")
    entries["date"] = pd.to_datetime(entries["date"])
    entries = annotate(entries.loc[entries["date"].dt.year.isin(years) & entries["universe_eligible"]])
    for horizon in (3, 5, 7):
        entries[f"gross_return_h{horizon}"] = entries[f"exit_raw_price_vnd_h{horizon}"] / entries["entry_raw_price_vnd"] - 1
    labels = label_cohorts(entries)
    labels.to_csv(output / "cohort_labels.csv", index=False)
    bars = pd.read_csv(args.bars)
    bars["date"] = pd.to_datetime(bars["date"])
    tolerance = bars["close"].abs().clip(lower=1) * 1e-8
    invalid = (bars["high"].add(tolerance).lt(bars[["open", "close", "low"]].max(axis=1))
               | bars["low"].sub(tolerance).gt(bars[["open", "close", "high"]].min(axis=1)))
    bars.loc[invalid, ["open", "high", "low", "close"]] = np.nan
    forecasts, matched, ledger = [], [], {}
    friction = metadata["costs"]["execution_friction_roundtrip_bps"] / 20000
    cost_factor = (1 - friction) / (1 + friction) * (1 - policy.brokerage_fee_rate - policy.sell_tax_rate) / (1 + policy.brokerage_fee_rate)
    for year in years:
        source = args.research / f"dev_{year}"
        forecast = pd.read_parquet(source / "entry_forecasts.parquet")
        exits = pd.read_parquet(source / "exit_forecasts.parquet")
        outcomes = pd.read_parquet(source / "observed_policy_outcomes.parquet")
        forecast = forecast.merge(entries, left_on=["decision_date", "ticker"], right_on=["date", "ticker"], how="left", validate="one_to_one", suffixes=("", "_dataset"))
        forecast = forecast.merge(outcomes, on=["date", "ticker"], how="left", validate="one_to_one")
        forecast["policy_gross_return"] = (1 + forecast["policy_net_return"]) / cost_factor - 1
        forecasts.append(forecast)
        period_bars = bars.loc[bars["date"].dt.year.eq(year)].copy()
        # Frozen dataset calendar may contain price-free missing-index sessions.
        missing = sorted(set(period_bars["date"]) - set(period_bars.loc[period_bars["ticker"].eq("VNINDEX"), "date"]))
        if missing:
            period_bars = pd.concat([period_bars, pd.DataFrame({"date": missing, "ticker": "VNINDEX"})], ignore_index=True)
        ledger[str(year)] = {}
        for name, capital_policy, mode in (("frozen_full", policy, "adaptive"),
                                           ("frozen_static7", policy, "static"),
                                           ("zero_cost_counterfactual", replace(policy, friction_bps=0, brokerage_fee_rate=0, sell_tax_rate=0, minimum_fee=0), "adaptive")):
            trades, nav, metrics = simulate_broker_swing(forecast, exits, period_bars, capital_policy, mode)
            trades.to_csv(output / f"{year}_{name}_trades.csv", index=False)
            nav.to_csv(output / f"{year}_{name}_nav.csv", index=False)
            write_json(output / f"{year}_{name}_metrics.json", metrics)
            ledger[str(year)][name] = metrics
            if name == "frozen_full":
                matched.append(matched_exits(trades, entries.loc[entries["year"].eq(year)], policy))
            print(f"DEV {year} {name}: NAV {metrics['total_return']:.3%}, trades {metrics['closed_trades']}", flush=True)
    forecasts = pd.concat(forecasts, ignore_index=True)
    calibration = calibration_cohorts(forecasts)
    calibration.to_csv(output / "forecast_calibration.csv", index=False)
    matched = pd.concat(matched, ignore_index=True)
    matched.to_csv(output / "matched_exits.csv", index=False)
    matched["year"] = pd.to_datetime(matched["decision_date"]).dt.year
    matched["raw_price_pnl_vnd"] = matched["shares"] * (matched["exit_price_vnd"] / (1 - policy.friction_bps / 20000) - matched["entry_price_vnd"] / (1 + policy.friction_bps / 20000))
    matched["explicit_fees_tax_vnd"] = matched["buy_fee"] + matched["sell_fee"] + matched["sell_tax"]
    attribution = matched.groupby(["year", "ticker"]).agg(closed_trades=("net_pnl_vnd", "size"),
        net_pnl_vnd=("net_pnl_vnd", "sum"), raw_price_pnl_vnd=("raw_price_pnl_vnd", "sum"),
        explicit_fees_tax_vnd=("explicit_fees_tax_vnd", "sum"), mean_net_return=("net_return", "mean"))
    attribution.to_csv(output / "stock_attribution.csv")
    matched_observed = matched.loc[matched["same_size_static7_pnl_vnd"].notna()]
    selected = forecasts.loc[forecasts["expected_net_return"].gt(0)]
    labeled_selected = selected.loc[selected["policy_net_return"].notna()]
    summary = {"status": "DIAGNOSTIC_ONLY", "production_eligible": False,
        "vn30_membership_status": "NEEDS_PIT_MEMBERSHIP", "frozen_portfolios": ledger,
        "gross_positive_across_dev_directions": stable_directions(labels, years),
        "positive_ev_forecasts": {"rows": len(selected), "observed": len(labeled_selected),
            "mean_predicted_net_return": float(labeled_selected["expected_net_return"].mean()),
            **return_summary(selected, "policy_net_return", "policy_gross_return")},
        "matched_exit": {"adaptive_trades": len(matched), "same_size_observed_h7": len(matched_observed),
            "adaptive_minus_static7_pnl_vnd": float(matched_observed["adaptive_minus_static7_pnl_vnd"].sum()),
            "not_portfolio_counterfactual": "Same shares and entry costs; excludes altered cash/slots and subsequent entries"},
        "limitations": ["DEV was already inspected; exploratory cohorts are not independent validation",
            "Overlapping outcome windows and correlated equities; weekly intervals are descriptive, not selection corrected",
            "Missing/censored paths may be informative; gross/net comparison uses identical observed labels",
            "Raw prices omit corporate entitlements; historical price vintage and delisting coverage remain unverified",
            "Daily-bar next-open/close execution proxies; future exit depth, partial fills and floor queues unknown",
            "HOSE tick bands are price sensitivity proxies because exchange metadata is absent",
            "Zero-cost portfolios freeze forecasts but cash, size and accepted entries can change",
            "Positive observed cohorts never imply a stock whitelist or certain future profit"]}
    all_zero_cost_negative = all(value["zero_cost_counterfactual"]["total_return"] <= 0 for value in ledger.values())
    summary["research_decision"] = {
        "existing_positive_ev_calibration": "REJECTED_MEAN_EDGE_SIGN" if summary["positive_ev_forecasts"]["mean_net_return"] < 0 else "UNVERIFIED",
        "cost_only_repair": "NOT_SUPPORTED_FROZEN_ZERO_COST_NAV_NONPOSITIVE_EACH_YEAR" if all_zero_cost_negative else "COST_DRAG_CONTRIBUTES_NOT_PROFITABILITY_PROOF",
        "next_registration": "One causal entry hypothesis grounded in DEV gross/net direction; no model-depth expansion or threshold sweep",
        "vn30_comparison": "DEFER_UNTIL_HISTORICAL_POINT_IN_TIME_MEMBERSHIP"}
    write_json(output / "summary.json", summary)
    lines = ["# LAB001 economic feasibility", "", "Status: DIAGNOSTIC_ONLY. No production promotion.", "",
             "VN30 comparison: NEEDS_PIT_MEMBERSHIP. No static current-membership substitution.", "",
             "| DEV year | Frozen adaptive net | Static7 net | Frozen forecasts with zero costs |", "|---|---:|---:|---:|"]
    for year, result in ledger.items():
        lines.append(f"| {year} | {result['frozen_full']['total_return']:.3%} | {result['frozen_static7']['total_return']:.3%} | {result['zero_cost_counterfactual']['total_return']:.3%} |")
    lines.extend(["", f"Positive-EV forecasts: predicted mean {summary['positive_ev_forecasts']['mean_predicted_net_return']:.3%}; observed net {summary['positive_ev_forecasts']['mean_net_return']:.3%}; observed gross {summary['positive_ev_forecasts']['mean_gross_price_return']:.3%}.", "",
                  f"Same-size adaptive minus H7 exit PnL: {summary['matched_exit']['adaptive_minus_static7_pnl_vnd']:,.0f} VND across {len(matched_observed)} observed entries.", "",
                  "Next action: inspect stable gross/net direction across DEV years before registering one practical entry hypothesis. Do not increase EV thresholds or whitelist past winning stocks without forward evidence.", "", *[f"- {item}" for item in summary["limitations"]]])
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"DONE {output}; DIAGNOSTIC_ONLY", flush=True)


if __name__ == "__main__":
    main()
