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
from typing import Any

import httpx

from sag_api.research.batch_ingest import (
    AsyncPGCoreDBBatchReader,
    resolve_default_core_database_url,
    run_research_batch_ingest,
)
from sag_api.research.input_builder import SubDocumentBundle


def resolve_default_research_org_id() -> str:
    return (os.environ.get("SAG_RESEARCH_ORG_ID") or "aiinvest-default").strip() or "aiinvest-default"


class _CommittingSqlTableRepository:
    """Commits each upserted table record in an isolated async session for CLI runs."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def upsert_table(self, record) -> str:
        from sag_api.research.table_engine import SqlTableRepository

        async with self._session_factory() as session:
            repo = SqlTableRepository(session)
            tid = await repo.upsert_table(record)
            await session.commit()
            return tid

    async def get_hydrated_table(self, table_id: str, *, as_of_ts=None):
        from sag_api.research.table_engine import SqlTableRepository

        async with self._session_factory() as session:
            return await SqlTableRepository(session).get_hydrated_table(table_id, as_of_ts=as_of_ts)

    async def list_hydrated_tables(self, project_id: str, *, as_of_ts, sub_doc_slot=None):
        from sag_api.research.table_engine import SqlTableRepository

        async with self._session_factory() as session:
            return await SqlTableRepository(session).list_hydrated_tables(
                project_id, as_of_ts=as_of_ts, sub_doc_slot=sub_doc_slot
            )


def _build_sag_http_uploader(
    *,
    sag_api_base: str,
    bff_token: str,
    org_id: str | None = None,
    user_id: str = "000000000000000000000001",
):
    base = sag_api_base.rstrip("/")
    resolved_org_id = (org_id or resolve_default_research_org_id()).strip()

    async def _upload(bundle: SubDocumentBundle) -> dict:
        idem_key = (
            f"{bundle.project_id}-{bundle.fiscal_year}Q{bundle.fiscal_quarter}-"
            f"{bundle.sub_doc_slot}-{bundle.content_hash[:12]}"
        )
        logical_source_id = (
            f"hose:{bundle.project_id}:{bundle.fiscal_year}Q{bundle.fiscal_quarter}:{bundle.sub_doc_slot}"
        )
        headers = {
            "X-SAG-BFF-Service-Token": bff_token,
            "X-Continuum-User-Id": user_id,
            "X-Continuum-Organization-Id": resolved_org_id,
            "X-Continuum-Project-Id": bundle.project_id,
            "X-Continuum-Security-Partition": bundle.project_id,
            "Idempotency-Key": idem_key,
        }
        filename = f"{bundle.project_id}_{bundle.fiscal_year}Q{bundle.fiscal_quarter}_{bundle.sub_doc_slot}.md"
        data = {
            "logical_source_id": logical_source_id,
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
    parser.add_argument("--enable-llm-toc", action="store_true", help="Use DeepInfra 1-call BCTN 3-Way Triage")
    parser.add_argument("--auto-mineru-ocr", action="store_true", help="Enable MinerU Official API v4 for scan PDFs")
    parser.add_argument("--no-tables", action="store_true", help="Skip pdfplumber/HTML table extraction")
    parser.add_argument("--sag-api-url", default=os.environ.get("SAG_API_URL", "http://127.0.0.1:18000"))
    parser.add_argument(
        "--org-id",
        default=resolve_default_research_org_id(),
        help="Tenant/Organization ID (env SAG_RESEARCH_ORG_ID)",
    )
    args = parser.parse_args(argv)

    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else date.today()
    core_url = resolve_default_core_database_url()
    if not core_url:
        print("ERROR: SAG_CORE_DATABASE_URL is not set.", file=sys.stderr)
        return 2

    core_reader = AsyncPGCoreDBBatchReader(core_url)
    uploader = None
    table_repo = None
    if not args.dry_run:
        bff_token = (os.environ.get("SAG_BFF_SERVICE_TOKEN") or "").strip()
        if not bff_token:
            print("ERROR: SAG_BFF_SERVICE_TOKEN is required when --dry-run is not set.", file=sys.stderr)
            return 2
        uploader = _build_sag_http_uploader(
            sag_api_base=args.sag_api_url,
            bff_token=bff_token,
            org_id=args.org_id,
        )
        table_card_indexer = None
        if not args.no_tables:
            from sag_api.core.db import SessionLocal
            from sag_api.services.search_index_service import index_table_search_cards_for_version

            table_repo = _CommittingSqlTableRepository(SessionLocal)

            async def _cli_table_card_indexer(
                bundle: Any,
                doc_ver_id: str,
                extracted_tables: list[Any],
            ) -> int:
                async with SessionLocal() as sess:
                    cnt = await index_table_search_cards_for_version(
                        sess,
                        document_version_id=doc_ver_id,
                        project_id=bundle.project_id,
                        table_records=extracted_tables,
                        security_partition_id=bundle.project_id,
                        tenant_id=args.org_id,
                    )
                    await sess.commit()
                    return cnt

            table_card_indexer = _cli_table_card_indexer
    else:
        table_card_indexer = None

    summary = await run_research_batch_ingest(
        tier=args.tier,  # type: ignore[arg-type]
        as_of_date=as_of,
        custom_tickers=args.tickers,
        years_back=args.years_back,
        dry_run=args.dry_run,
        enable_llm_toc=args.enable_llm_toc,
        auto_mineru_ocr=args.auto_mineru_ocr,
        extract_tables=not args.no_tables,
        core_reader=core_reader,
        subdoc_uploader=uploader,
        table_repo=table_repo,
        table_card_indexer=table_card_indexer,
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
                        "tables_extracted": r.tables_extracted,
                        "tables_verified": r.tables_verified,
                        "tables_unverified": r.tables_unverified,
                        "bctc_notes_coverage_pct": r.bctc_notes_coverage_pct,
                        "noise_reduction_pct": r.noise_reduction_pct,
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
