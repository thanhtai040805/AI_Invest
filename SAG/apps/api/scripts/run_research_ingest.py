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
import logging
import os
import sys
from datetime import date
from typing import Any

import httpx

from sag_api.research.batch_ingest import (
    AsyncPGCoreDBBatchReader,
    build_research_logical_source_id,
    build_research_upload_filename,
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
            f"{bundle.doc_type}-{bundle.sub_doc_slot}-{bundle.content_hash[:12]}"
        )
        logical_source_id = build_research_logical_source_id(bundle)
        headers = {
            "X-SAG-BFF-Service-Token": bff_token,
            "X-Continuum-User-Id": user_id,
            "X-Continuum-Organization-Id": resolved_org_id,
            "X-Continuum-Project-Id": bundle.project_id,
            "X-Continuum-Security-Partition": bundle.project_id,
            "Idempotency-Key": idem_key,
        }
        filename = build_research_upload_filename(bundle)
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


async def _fetch_indexed_tickers(org_id: str) -> set[str]:
    """Return tickers that already have at least one active SEARCH_READY document version."""
    from sqlalchemy import select

    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Document, DocumentVersion

    async with SessionLocal() as sess:
        rows = (
            await sess.execute(
                select(Document.project_id)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(
                    Document.tenant_id == org_id,
                    Document.is_active.is_(True),
                    DocumentVersion.search_status == "SEARCH_READY",
                )
                .distinct()
            )
        ).scalars().all()
    return {str(sym).strip().upper() for sym in rows if sym}


async def _main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="AIInvest SAG v2 Tier-Aware HOSE Batch Ingest")
    parser.add_argument(
        "--tier",
        choices=["pilot_8", "a5_36", "a4_81", "a3_buffer_139", "custom"],
        default="pilot_8",
    )
    parser.add_argument("--tickers", nargs="*", default=[])
    parser.add_argument("--as-of-date", default=None, help="ISO date YYYY-MM-DD (default: today)")
    parser.add_argument("--years-back", type=int, default=3)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect & localize without writing R2 or uploading to SAG",
    )
    parser.add_argument(
        "--r2-only",
        action="store_true",
        help="Prepare, OCR, bilingual-clean, verify tables, and save .md bundles directly to R2",
    )
    parser.add_argument("--max-docs-per-ticker", type=int, default=8)
    parser.add_argument(
        "--doc-concurrency",
        type=int,
        default=12,
        help="Max documents processed concurrently across the MinerU token pool (default: 12)",
    )
    parser.add_argument("--enable-llm-toc", action="store_true", help="Use DeepInfra 1-call BCTN 3-Way Triage")
    parser.add_argument(
        "--auto-mineru-ocr",
        action="store_true",
        help="Explicitly enable MinerU Official API v4 (now auto-enabled whenever token is configured)",
    )
    parser.add_argument(
        "--no-mineru-ocr",
        action="store_true",
        help="Disable MinerU Official API v4 even when SAG_MINERU_OFFICIAL_API_TOKEN is set",
    )
    parser.add_argument("--no-tables", action="store_true", help="Skip pdfplumber/HTML table extraction")
    parser.add_argument(
        "--no-refresh-kg",
        action="store_true",
        help="Skip 3-Layer Knowledge Graph refresh after ingest (enabled by default on live runs)",
    )
    parser.add_argument(
        "--skip-indexed",
        action="store_true",
        help="Skip tickers that already have SEARCH_READY active documents in SAG DB (incremental expansion)",
    )
    parser.add_argument("--sag-api-url", default=os.environ.get("SAG_API_URL", "http://127.0.0.1:18000"))
    parser.add_argument(
        "--org-id",
        default=resolve_default_research_org_id(),
        help="Tenant/Organization ID (env SAG_RESEARCH_ORG_ID)",
    )
    args = parser.parse_args(argv)

    from sag_api.core.config import settings as sag_settings

    # Ensure CLI runs always target the shared multi-tenant Qdrant collection (Option A+)
    if not sag_settings.sag_qdrant_shared_collection:
        shared_col = (
            os.environ.get("SAG_SAG_QDRANT_SHARED_COLLECTION") or "aiinvest_search_units"
        ).strip() or "aiinvest_search_units"
        sag_settings.sag_qdrant_shared_collection = shared_col

    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else date.today()
    core_url = resolve_default_core_database_url()
    if not core_url:
        print("ERROR: SAG_CORE_DATABASE_URL is not set.", file=sys.stderr)
        return 2

    core_reader = AsyncPGCoreDBBatchReader(core_url)
    uploader = None
    table_repo = None
    kg_refresher = None
    table_card_indexer = None
    if not args.dry_run and not args.r2_only:
        bff_token = (os.environ.get("SAG_BFF_SERVICE_TOKEN") or sag_settings.bff_service_token or "").strip()
        if not bff_token:
            print("ERROR: SAG_BFF_SERVICE_TOKEN is required when --dry-run/--r2-only is not set.", file=sys.stderr)
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

        if not args.no_refresh_kg:
            from sag_api.core.db import SessionLocal
            from sag_api.research.kg_store import refresh_research_knowledge_graph

            async def _cli_kg_refresher(ingested: list[str], cutoff_date: date) -> dict[str, Any]:
                async with SessionLocal() as sess:
                    res = await refresh_research_knowledge_graph(
                        sess,
                        tickers=ingested,
                        as_of_date=cutoff_date,
                        core_database_url=core_url,
                        tenant_id=args.org_id,
                    )
                    await sess.commit()
                    return res

            kg_refresher = _cli_kg_refresher
    else:
        table_card_indexer = None

    effective_tier = args.tier
    effective_tickers = list(args.tickers or [])
    skipped_indexed: list[str] = []
    if args.skip_indexed:
        already_indexed = await _fetch_indexed_tickers(args.org_id)
        if effective_tier == "custom":
            candidate_tickers = [t.strip().upper() for t in effective_tickers if t.strip()]
        else:
            candidate_tickers = await core_reader.resolve_tickers_for_tier(
                effective_tier,  # type: ignore[arg-type]
                as_of_date=as_of,
            )
        skipped_indexed = sorted(t for t in candidate_tickers if t in already_indexed)
        remaining = [t for t in candidate_tickers if t not in already_indexed]
        print(
            f"[--skip-indexed] tier={effective_tier} total={len(candidate_tickers)} "
            f"skipped_ready={len(skipped_indexed)} remaining={len(remaining)}",
            file=sys.stderr,
        )
        if not remaining:
            print(
                json.dumps(
                    {
                        "tier": effective_tier,
                        "as_of_date": as_of.isoformat(),
                        "skipped_indexed": skipped_indexed,
                        "resolved_count": 0,
                        "resolved_tickers": [],
                        "total_sub_docs_uploaded": 0,
                        "total_ocr_pages": 0,
                        "reports": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        effective_tier = "custom"
        effective_tickers = remaining

    effective_auto_ocr = (not args.no_mineru_ocr) and (
        bool(args.auto_mineru_ocr) or bool(sag_settings.mineru_configured)
    )
    summary = await run_research_batch_ingest(
        tier=effective_tier,  # type: ignore[arg-type]
        as_of_date=as_of,
        custom_tickers=effective_tickers,
        years_back=args.years_back,
        dry_run=args.dry_run,
        r2_only=args.r2_only,
        enable_llm_toc=args.enable_llm_toc,
        refresh_kg=(not args.no_refresh_kg) and (not args.r2_only),
        auto_mineru_ocr=effective_auto_ocr,
        extract_tables=(not args.no_tables) and (not args.r2_only),
        max_docs_per_ticker=args.max_docs_per_ticker,
        doc_concurrency=args.doc_concurrency,
        core_reader=core_reader,
        subdoc_uploader=uploader,
        table_repo=table_repo,
        table_card_indexer=table_card_indexer,
        kg_refresher=kg_refresher,
    )
    print(
        json.dumps(
            {
                "tier": summary.tier,
                "as_of_date": summary.as_of_date,
                "dry_run": summary.dry_run,
                "r2_only": args.r2_only,
                "resolved_count": len(summary.resolved_tickers),
                "resolved_tickers": summary.resolved_tickers,
                "total_sub_docs_uploaded": summary.total_sub_docs_uploaded,
                "total_ocr_pages": summary.total_ocr_pages,
                "reports": [
                    {
                        "ticker": r.ticker,
                        "status": r.status,
                        "selected_documents": r.selected_documents,
                        "rejected_documents": r.rejected_documents,
                        "r2_cache_hits": r.r2_cache_hits,
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
