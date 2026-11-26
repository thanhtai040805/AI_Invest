"""Real-corpus validation of the deterministic financial fact layer (Rule 08 Layer 4).

Reads Core PG `financial_statements` (read-only) and SAG `sag_extracted_tables` (VERIFIED only),
then reports:
  1. Coverage of key canonical items for FY and latest quarter, per accounting template.
  2. Accounting-identity pass rates.
  3. FY vs sum-of-4-quarters mismatch rate for FLOW items.
  4. Agreement between Core PG facts and independently OCR-extracted VERIFIED PDF tables.

Usage:
  python scripts/validate_financial_facts.py --tickers ACB TCB VND VHM --as-of-date 2026-11-26
  python scripts/validate_financial_facts.py --all-sag-tickers --json-out report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date
from typing import Any

import asyncpg

from sag_api.research.facts import FactSet, LookupStatus, Template, load_fact_set
from sag_api.research.financial_metrics_store import (
    AsyncPGCoreMetricsReader,
    extract_metrics_from_verified_tables,
    resolve_core_database_url,
)

KEY_ITEMS: dict[Template, tuple[str, ...]] = {
    Template.CORPORATE: (
        "revenue_net", "gross_profit", "pbt", "npat", "npat_parent", "total_assets", "equity",
        "total_liabilities", "cfo", "inventory", "st_receivables", "st_borrowings", "cash_end",
    ),
    Template.BANK: (
        "nii", "toi", "opex", "ppop", "credit_provision_expense", "pbt", "npat", "total_assets", "equity",
        "customer_loans_gross", "loan_loss_reserve", "customer_deposits", "cfo",
    ),
    Template.SECURITIES: (
        "revenue_net", "pbt", "npat", "total_assets", "equity", "margin_loans", "fvtpl_assets",
        "st_borrowings", "brokerage_revenue", "margin_lending_income", "cfo",
    ),
    Template.INSURANCE: (
        "gross_written_premium", "net_premium", "insurance_claims", "pbt", "npat", "total_assets",
        "equity", "technical_reserves", "cfo",
    ),
}

# Core fact item -> PDF snapshot field (from extract_metrics_from_verified_tables)
PDF_CROSSCHECK: dict[str, str] = {
    "npat": "net_income",
    "total_assets": "total_assets",
    "equity": "equity",
    "cfo": "cfo",
    "revenue_net": "net_revenue",
}
AGREE_TOL = 0.01


def _sag_dsn() -> str:
    dsn = (os.environ.get("SAG_DATABASE_URL") or "").replace("postgresql+asyncpg://", "postgresql://")
    if not dsn:
        raise SystemExit("SAG_DATABASE_URL is not set")
    return dsn


async def _load_sag_tickers() -> list[str]:
    conn = await asyncpg.connect(_sag_dsn())
    try:
        rows = await conn.fetch("SELECT DISTINCT project_id FROM documents WHERE is_active = true ORDER BY 1")
    finally:
        await conn.close()
    return [str(r["project_id"]).upper() for r in rows if r["project_id"]]


async def _load_verified_tables(tickers: list[str]) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(_sag_dsn())
    try:
        rows = await conn.fetch(
            """
            SELECT table_id, project_id, document_version_id, sub_doc_slot, fiscal_year, fiscal_quarter,
                   table_title, section_path, page_from, currency, unit_multiplier, headers_json, rows_json,
                   verification_status, valid_from
            FROM sag_extracted_tables
            WHERE verification_status = 'VERIFIED' AND project_id = ANY($1::text[])
            """,
            tickers,
        )
    finally:
        await conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        for k in ("headers_json", "rows_json"):
            if isinstance(rec[k], str):
                rec[k] = json.loads(rec[k])
        out.append(rec)
    return out


def _coverage(fs: FactSet, tickers: list[str], fy: int) -> dict[str, Any]:
    by_tmpl: dict[str, Counter] = defaultdict(Counter)
    missing: dict[str, list[str]] = defaultdict(list)
    for tk in tickers:
        tmpl = fs.templates.get(tk)
        if tmpl is None:
            missing["__no_statements__"].append(tk)
            continue
        latest_q = fs.latest_period(tk, "Q")
        for item in KEY_ITEMS[tmpl]:
            for label, ptype, y, q in (("FY", "FY", fy, 0), ("LATEST_Q", "Q", *(latest_q or (0, 0)))):
                by_tmpl[tmpl.value][f"{label}:total"] += 1
                res = fs.get(tk, item, ptype, y, q)
                if res.status is LookupStatus.OK:
                    by_tmpl[tmpl.value][f"{label}:found"] += 1
                else:
                    missing[f"{tmpl.value}:{label}:{item}"].append(tk)
    summary = {
        t: {
            "FY_coverage": round(c["FY:found"] / max(1, c["FY:total"]), 4),
            "LATEST_Q_coverage": round(c["LATEST_Q:found"] / max(1, c["LATEST_Q:total"]), 4),
        }
        for t, c in by_tmpl.items()
    }
    return {"summary": summary, "missing": {k: v for k, v in sorted(missing.items())}}


def _identity_summary(fs: FactSet) -> dict[str, Any]:
    agg: dict[str, Counter] = defaultdict(Counter)
    fails: list[dict[str, Any]] = []
    for chk in fs.identity_checks:
        agg[chk["identity"]]["total"] += 1
        agg[chk["identity"]]["passed"] += int(chk["passed"])
        if not chk["passed"]:
            fails.append(chk)
    return {
        "pass_rate": {k: round(v["passed"] / max(1, v["total"]), 4) for k, v in agg.items()},
        "counts": {k: dict(v) for k, v in agg.items()},
        "failures_sample": sorted(fails, key=lambda c: -c["error_ratio"])[:15],
    }


def _fy_qsum_summary(fs: FactSet) -> dict[str, Any]:
    total = flagged = 0
    by_year: dict[int, Counter] = defaultdict(Counter)
    examples: list[str] = []
    for f in fs.facts.values():
        if f.period_type == "FY" and f.kind == "FLOW":
            qs = [fs.get(f.ticker, f.item, "Q", f.fiscal_year, q) for q in (1, 2, 3, 4)]
            if all(q.status is LookupStatus.OK for q in qs):
                total += 1
                by_year[f.fiscal_year]["checked"] += 1
                if "FY_QSUM_MISMATCH" in f.flags:
                    flagged += 1
                    by_year[f.fiscal_year]["mismatch"] += 1
                    if len(examples) < 12 and f.fiscal_year >= 2024:
                        qsum = sum(q.fact.value for q in qs if q.fact is not None)
                        examples.append(f"{f.ticker} {f.item} FY{f.fiscal_year}: FY={f.value:,.0f} sumQ={qsum:,.0f}")
    return {
        "checked": total,
        "mismatch": flagged,
        "mismatch_rate": round(flagged / max(1, total), 4),
        "mismatch_rate_by_year": {
            y: round(c["mismatch"] / max(1, c["checked"]), 4) for y, c in sorted(by_year.items())
        },
        "examples": examples,
    }


def _pdf_crosscheck(fs: FactSet, tables: list[dict[str, Any]], fy: int) -> dict[str, Any]:
    snaps = extract_metrics_from_verified_tables(tables, tenant_id="validation")
    by_tk = {s["ticker"]: s for s in snaps if s["fiscal_year"] == fy and s["fiscal_quarter"] == 0}
    stats: dict[str, Counter] = defaultdict(Counter)
    disagreements: list[dict[str, Any]] = []
    for tk, snap in sorted(by_tk.items()):
        for item, pdf_field in PDF_CROSSCHECK.items():
            pdf_val = snap.get(pdf_field)
            if pdf_val is None:
                continue
            res = fs.get(tk, item, "FY", fy, 0)
            if res.status is not LookupStatus.OK or res.fact is None:
                stats[item]["core_missing"] += 1
                continue
            core_val = res.fact.value
            denom = max(abs(core_val), abs(pdf_val), 1.0)
            err = abs(core_val - pdf_val) / denom
            stats[item]["compared"] += 1
            if err <= AGREE_TOL:
                stats[item]["agree"] += 1
            else:
                disagreements.append(
                    {
                        "ticker": tk,
                        "item": item,
                        "core": core_val,
                        "pdf": pdf_val,
                        "ratio_core_to_pdf": (core_val / pdf_val) if pdf_val else None,
                        "pdf_provenance": snap.get("provenance_json", {}).get(pdf_field),
                    }
                )
    return {
        "agreement_rate": {k: round(v["agree"] / max(1, v["compared"]), 4) for k, v in stats.items()},
        "counts": {k: dict(v) for k, v in stats.items()},
        "disagreements": disagreements,
    }


async def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate deterministic financial facts on the live corpus")
    ap.add_argument("--tickers", nargs="*", default=[])
    ap.add_argument("--all-sag-tickers", action="store_true")
    ap.add_argument("--as-of-date", default=None)
    ap.add_argument("--fiscal-year", type=int, default=None)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else date.today()
    fy = args.fiscal_year or (as_of.year - 1)
    tickers = [t.strip().upper() for t in args.tickers if t.strip()]
    if args.all_sag_tickers:
        tickers = sorted(set(tickers) | set(await _load_sag_tickers()))
    if not tickers:
        print("ERROR: provide --tickers or --all-sag-tickers", file=sys.stderr)
        return 2

    core_url = resolve_core_database_url()
    if not core_url:
        print("ERROR: SAG_CORE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    reader = AsyncPGCoreMetricsReader(core_url)
    fs = await load_fact_set(reader, tickers=tickers, as_of_date=as_of)
    tables = await _load_verified_tables(tickers)

    report = {
        "as_of_date": as_of.isoformat(),
        "fiscal_year": fy,
        "tickers": tickers,
        "templates": {tk: t.value for tk, t in sorted(fs.templates.items())},
        "fact_count": len(fs.facts),
        "ignored_rows": fs.ignored_rows,
        "coverage": _coverage(fs, tickers, fy),
        "identities": _identity_summary(fs),
        "fy_vs_quarters": _fy_qsum_summary(fs),
        "pdf_crosscheck": _pdf_crosscheck(fs, tables, fy),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(
        json.dumps(
            {
                "fact_count": report["fact_count"],
                "templates": Counter(report["templates"].values()),
                "ignored_rows": report["ignored_rows"],
                "coverage": report["coverage"]["summary"],
                "identity_pass_rate": report["identities"]["pass_rate"],
                "fy_vs_quarters": {k: v for k, v in report["fy_vs_quarters"].items() if k != "examples"},
                "pdf_agreement_rate": report["pdf_crosscheck"]["agreement_rate"],
                "pdf_counts": report["pdf_crosscheck"]["counts"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
