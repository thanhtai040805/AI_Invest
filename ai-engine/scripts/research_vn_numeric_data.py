"""Profile frozen numerical observations before researching a trading algorithm.

Local files only. Does not import ingestion modules, query services, fit models,
or infer that a documented API has historical payloads available to this LAB.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]


def fingerprint(path):
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def numeric_profile(frame, origin):
    rows = []
    for column in frame.select_dtypes(include="number").columns:
        values = frame[column]
        finite = values.loc[np.isfinite(values)]
        rows.append({"origin": origin, "column": column, "rows": len(values),
                     "missing": int(values.isna().sum()),
                     "nonfinite_nonmissing": int((values.notna() & ~np.isfinite(values)).sum()),
                     "zero": int(values.eq(0).sum()), "nonzero_finite": int(finite.ne(0).sum()),
                     "unique_finite": int(finite.nunique()),
                     "minimum": float(finite.min()) if len(finite) else None,
                     "maximum": float(finite.max()) if len(finite) else None,
                     "zero_is_observed": "UNVERIFIED_SOURCE_SEMANTICS"})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    lab_runs = ROOT / "scratch/vn_swing_lab/runs"
    if not output.is_relative_to(lab_runs) or output == lab_runs or output.exists():
        raise ValueError("Use a new named run within scratch/vn_swing_lab/runs")
    inputs = [args.bars, args.dataset, args.metadata, args.catalog]
    hashes = {str(path.resolve()): fingerprint(path) for path in inputs}
    catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
    metadata = json.loads(args.metadata.read_text(encoding="utf-8-sig"))
    bars = pd.read_csv(args.bars)
    bars["date"] = pd.to_datetime(bars["date"], errors="raise")
    columns = pq.ParquetFile(args.dataset).schema.names
    chosen = [name for name in ("foreign_flow_ratio", "foreign_flow_ratio_5d", "continuous_volume_share", "adtv20_vnd") if name in columns]
    features = pd.read_parquet(args.dataset, columns=chosen)
    profiles = numeric_profile(bars, "bars_export") + numeric_profile(features, "derived_dataset")
    tolerance = bars["close"].abs().clip(lower=1) * 1e-8
    invalid = (bars["high"].add(tolerance).lt(bars[["open", "close", "low"]].max(axis=1))
               | bars["low"].sub(tolerance).gt(bars[["open", "close", "high"]].min(axis=1)))
    volume_known = bars["volume_continuous"].notna() & bars["volume_total"].notna()
    same_volume = volume_known & bars["volume_continuous"].eq(bars["volume_total"])
    coverage = bars.assign(year=bars["date"].dt.year, invalid_ohlc=invalid,
                           volume_equal=same_volume, volume_comparable=volume_known).groupby(["year", "data_source"], dropna=False).agg(
        rows=("ticker", "size"), tickers=("ticker", "nunique"), sessions=("date", "nunique"),
        invalid_ohlc=("invalid_ohlc", "sum"), volume_equal=("volume_equal", "sum"), volume_comparable=("volume_comparable", "sum"))
    tickers = bars.groupby("ticker").agg(first_date=("date", "min"), last_date=("date", "max"), rows=("date", "size"))
    index_sessions = set(bars.loc[bars["ticker"].eq("VNINDEX"), "date"])
    stock_sessions = set(bars.loc[~bars["ticker"].eq("VNINDEX"), "date"])
    required_time_fields = ["published_at", "received_at", "available_at", "revision_id"]
    foreign_columns = [name for name in bars if name.startswith("foreign_") or name.startswith("net_foreign")]
    code_paths = [ROOT / "app/infrastructure/data_pipelines/data_enricher.py",
                  ROOT / "app/infrastructure/data_pipelines/ohlcv_ingestion_service.py",
                  ROOT / "app/domain/services/ml/feature_forge.py",
                  ROOT / "app/infrastructure/external_api/dnse/redis_pub.py"]
    code = {str(path.relative_to(ROOT)): path.read_text(encoding="utf-8") for path in code_paths}
    enricher = next(value for key, value in code.items() if key.endswith("data_enricher.py"))
    summary = {
        "status": "DIAGNOSTIC_ONLY", "workflow_stage": "NUMERIC_DATA_RESEARCH", "algorithm_training_performed": False,
        "input_sha256": hashes, "audited_code_sha256": {str(path): fingerprint(path) for path in code_paths},
        "bars": {"rows": len(bars), "stock_tickers": int(bars.loc[~bars["ticker"].eq("VNINDEX"), "ticker"].nunique()),
                 "date_min": bars["date"].min().date().isoformat(), "date_max": bars["date"].max().date().isoformat(),
                 "columns": list(bars.columns), "duplicate_ticker_dates": int(bars.duplicated(["ticker", "date"]).sum()),
                 "invalid_ohlc_rows": int(invalid.sum()), "continuous_equals_total_rows": int(same_volume.sum()),
                 "volume_comparable_rows": int(volume_known.sum()),
                 "stock_sessions_missing_index": len(stock_sessions - index_sessions),
                 "missing_publication_vintage_fields": [name for name in required_time_fields if name not in bars],
                 "foreign_raw_columns": foreign_columns},
        "numeric_profiles": profiles,
        "derived_flow_interpretation": "Columns without raw flow observations do not establish observed foreign flow; an all-zero series is not evidence of neutral flow",
        "feature_groups_used_by_previous_model": metadata["feature_groups"],
        "static_repository_evidence": {
            "hash_derived_foreign_fallback_present": "h = int(hashlib.md5(symbol.encode())" in enricher and "res.setdefault('foreign_buy_qty', (h % 500)" in enricher,
            "meaning": "Static code path exists; this audit does not prove it was invoked by any particular PROD prediction",
            "volume_split_fallback": "Ingestion can use total volume as continuous; failed intraday splitting can return all zeros",
            "missing_flow_fallback": "Feature forge fills absent joined flow with zero; observation status must be preserved separately",
            "realtime_storage": "Redis publisher uses replaceable caches, pub/sub and bounded lists; this is not a verified durable historical quote archive"
        },
        "catalog_statuses": [{"id": stream["id"], "priority": stream["priority"], "status": stream["local_status"], "next_evidence": stream["next_evidence"]} for stream in catalog["streams"]],
        "decision": "DATA_FIRST: verify original price/volume semantics, PIT universe/rights and a real flow/quote numerical source before enabling new algorithm research",
        "limitations": ["Local export coverage is not complete listing/delisting coverage", "Observed sessions are not an independently verified exchange calendar",
                        "No API payload, licensing or external historical depth was verified by this local audit", "SAG_CLOSED; no SAG data or outputs accessed",
                        "No profit, predictive information gain or causal flow effect inferred from field availability"]
    }
    output.mkdir(parents=True)
    pd.DataFrame(profiles).to_csv(output / "numeric_profiles.csv", index=False)
    coverage.to_csv(output / "year_source_coverage.csv")
    tickers.to_csv(output / "ticker_coverage.csv")
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    flow_coverage = [{"column": row["column"], "missing_rows": row["missing"], "zero_rows": row["zero"],
                      "nonzero_finite_rows": row["nonzero_finite"],
                      "classification": "ALL_MISSING" if row["missing"] == row["rows"] else
                      "ALL_OBSERVED_ZERO" if row["missing"] == 0 and row["nonzero_finite"] == 0 else
                      "PARTIAL_OR_NONZERO"}
                     for row in profiles if row["origin"] == "derived_dataset" and "foreign" in row["column"]]
    lines = ["# Numeric data audit", "", "DIAGNOSTIC_ONLY; DATA_FIRST; no new algorithm trained.", "",
             f"Bars: {len(bars):,}; stocks: {summary['bars']['stock_tickers']}; invalid OHLC: {int(invalid.sum()):,}.",
             f"Continuous equals total volume: {int(same_volume.sum()):,}/{int(volume_known.sum()):,}; separation needs source validation.",
             f"Raw foreign-flow columns: {foreign_columns}. Derived flow coverage: {flow_coverage}.",
             "Publication/revision fields absent from bars: " + ", ".join(summary["bars"]["missing_publication_vintage_fields"]), "",
             "Static repository code has a hash-derived foreign-flow fallback; actual PROD invocation remains unverified.", "",
             "Next: verify price/volume/PIT rights, obtain authentic flow and quote payloads, then decide the smallest numerical dataset for one falsifiable hypothesis.", "",
             *[f"- {item}" for item in summary["limitations"]]]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "rows": len(bars), "invalid_ohlc": int(invalid.sum()), "derived_flow_coverage": flow_coverage, "status": "DIAGNOSTIC_ONLY"}), flush=True)


if __name__ == "__main__":
    main()
