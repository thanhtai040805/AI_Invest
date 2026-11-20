"""CLI runner for tier-aware HOSE Stock Research batch ingest into AIInvest SAG v2.

Usage examples:
  python scripts/run_research_ingest.py --tier pilot_8 --dry-run
  python scripts/run_research_ingest.py --tier a3_buffer_139 --dry-run
  python scripts/run_research_ingest.py --tier custom --tickers PVT FPT CTG --enable-llm-toc
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date

import httpx

from sag_api.research.batch_ingest import (
    AsyncPGCoreDBBatchReader,
    resolve_default_core_database_url,
    run_research_batch_ingest,
)
from sag_api.research.input_builder import SubDocumentBundle


def _build_sag_http_uploader(
    *,
    sag_api_base: str,
    bff_token: str,
    org_id: str = "aiinvest",
    user_id: str = "000000000000000000000001",
):
    base = sag_api_base.rstrip("/")

    async def _upload(bundle: SubDocumentBundle) -> dict:
        idem_key = (
            f"{bundle.project_id}-{bundle.fiscal_year}Q{bundle.fiscal_quarter}-"
            f"{bundle.sub_doc_slot}-{bundle.content_hash[:12]}"
        )
        headers = {
            "X-SAG-BFF-Service-Token": bff_token,
            "X-Continuum-User-Id": user_id,
            "X-Continuum-Organization-Id": org_id,
            "X-Continuum-Project-Id": bundle.project_id,
            "X-Continuum-Security-Partition": bundle.project_id,
            "Idempotency-Key": idem_key,
        }
        filename = f"{bundle.project_id}_{bundle.fiscal_year}Q{bundle.fiscal_quarter}_{bundle.sub_doc_slot}.md"
        data = {
            "source_published_at": bundle.valid_from.isoformat(),
            "fiscal_year": str(bundle.fiscal_year),
            "fiscal_quarter": str(bundle.fiscal_quarter),
            "doc_type": bundle.doc_type,
            "sub_doc_slot": bundle.sub_doc_slot,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base}/api/v1/projects/{bundle.project_id}/documents/upload",
                headers=headers,
                data=data,
                files={"file": (filename, bundle.markdown_content.encode("utf-8"), "text/markdown")},
            )
            resp.raise_for_status()
            return resp.json()

    return _upload


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIInvest SAG v2 Tier-Aware HOSE Batch Ingest")
    parser.add_argument(
        "--tier",
        choices=["pilot_8", "a5_36", "a4_81", "a3_buffer_139", "custom"],
        default="pilot_8",
    )
    parser.add_argument("--tickers", nargs="*", default=[])
    parser.add_argument("--as-of-date", default=None, help="ISO date YYYY-MM-DD (default: today)")
    parser.add_argument("--years-back", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Inspect & localize without uploading to SAG")
    parser.add_argument("--enable-llm-toc", action="store_true", help="Use DeepInfra DeepSeek-V4-Flash TOC Router")
    parser.add_argument("--sag-api-url", default=os.environ.get("SAG_API_URL", "http://127.0.0.1:18000"))
    args = parser.parse_args(argv)

    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else date.today()
    core_url = resolve_default_core_database_url()
    if not core_url:
        print("ERROR: SAG_CORE_DATABASE_URL is not set.", file=sys.stderr)
        return 2

    core_reader = AsyncPGCoreDBBatchReader(core_url)
    uploader = None
    if not args.dry_run:
        bff_token = (os.environ.get("SAG_BFF_SERVICE_TOKEN") or "").strip()
        if not bff_token:
            print("ERROR: SAG_BFF_SERVICE_TOKEN is required when --dry-run is not set.", file=sys.stderr)
            return 2
        uploader = _build_sag_http_uploader(sag_api_base=args.sag_api_url, bff_token=bff_token)

    summary = await run_research_batch_ingest(
        tier=args.tier,  # type: ignore[arg-type]
        as_of_date=as_of,
        custom_tickers=args.tickers,
        years_back=args.years_back,
        dry_run=args.dry_run,
        enable_llm_toc=args.enable_llm_toc,
        core_reader=core_reader,
        subdoc_uploader=uploader,
    )
    print(
        json.dumps(
            {
                "tier": summary.tier,
                "as_of_date": summary.as_of_date,
                "dry_run": summary.dry_run,
                "resolved_count": len(summary.resolved_tickers),
                "resolved_tickers": summary.resolved_tickers,
                "total_sub_docs_uploaded": summary.total_sub_docs_uploaded,
                "total_ocr_pages": summary.total_ocr_pages,
                "reports": [
                    {
                        "ticker": r.ticker,
                        "status": r.status,
                        "selected_documents": r.selected_documents,
                        "branches": r.branches,
                        "localization_methods": r.localization_methods,
                        "sub_doc_slots": r.sub_doc_slots,
                        "missing_core_slots": r.missing_core_slots,
                        "ocr_pages_used": r.ocr_pages_used,
                        "uploaded_sub_docs": r.uploaded_sub_docs,
                        "errors": r.errors,
                    }
                    for r in summary.reports
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
