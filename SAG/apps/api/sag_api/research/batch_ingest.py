"""HOSE Tier-Aware Batch Ingest Pipeline (`pilot_8 | a5_36 | a4_81 | a3_buffer_139 | custom`).

Connects Core PostgreSQL (read-only) -> ResearchDocumentSelector -> InputInspector ->
3-Layer ResearchInputBuilder (PDF Bookmarks / DeepInfra LLM TOC Router / Offset Calibration)
-> Dual-Representation Table Engine -> SAG Sub-Document Ingestion (`S01..S13`, PIT valid_from)
-> Zero-Hardcode 3-Layer KG Refresh.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

import asyncpg

from sag_api.core.config import settings
from sag_api.core.logging import get_logger
from sag_api.research.input_builder import (
    DeepInfraTocRouter,
    InputBuildError,
    OcrAdapterProtocol,
    ResearchInputBuildResult,
    SubDocumentBundle,
    TocRouterProtocol,
    build_research_sub_documents,
)
from sag_api.research.input_inspector import unwrap_pdf_payload
from sag_api.research.input_selector import (
    BytesFetcherProtocol,
    FinancialAnomalyContext,
    ResearchCandidateDocument,
    SelectedDocument,
    fetch_document_with_fallback,
    select_research_documents,
)
from sag_api.research.ocr_adapter import MinerUOcrAdapter
from sag_api.research.table_engine import (
    ExtractedTableRecord,
    TableRepositoryProtocol,
    extract_tables_from_document,
)

log = get_logger("research.batch_ingest")

IngestTier = Literal["pilot_8", "a5_36", "a4_81", "a3_buffer_139", "custom"]

PILOT_8_TICKERS: tuple[str, ...] = (
    "CTG",
    "HPG",
    "FPT",
    "MWG",
    "DCM",
    "PVT",
    "VCI",
    "NT2",
)
DEFAULT_ADTV_BUFFER_VND: float = 5_000_000_000.0


class CoreDBBatchReaderProtocol(Protocol):
    """Read-only Core PostgreSQL data provider for tier resolution & filing discovery."""

    async def resolve_tickers_for_tier(
        self,
        tier: IngestTier,
        *,
        as_of_date: date,
        adtv_threshold_vnd: float = DEFAULT_ADTV_BUFFER_VND,
    ) -> list[str]: ...

    async def fetch_candidates_for_ticker(
        self,
        ticker: str,
        *,
        as_of_date: date,
        years_back: int = 3,
    ) -> tuple[list[ResearchCandidateDocument], FinancialAnomalyContext]: ...


SubDocumentUploader = Callable[[SubDocumentBundle], Awaitable[dict[str, Any]]]
TableCardIndexer = Callable[[SubDocumentBundle, str, list[ExtractedTableRecord]], Awaitable[int]]
KGRefreshHook = Callable[[list[str], date], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class TickerIngestReport:
    ticker: str
    status: Literal["INGESTED", "DRY_RUN_READY", "NO_DOCUMENTS", "FAILED"]
    selected_documents: int = 0
    rejected_documents: int = 0
    branches: list[str] = field(default_factory=list)
    localization_methods: list[str] = field(default_factory=list)
    sub_doc_slots: list[str] = field(default_factory=list)
    missing_core_slots: list[str] = field(default_factory=list)
    ocr_pages_used: int = 0
    uploaded_sub_docs: int = 0
    tables_extracted: int = 0
    tables_verified: int = 0
    tables_unverified: int = 0
    bctc_notes_coverage_pct: float = 100.0
    noise_reduction_pct: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchIngestSummary:
    tier: IngestTier
    as_of_date: str
    dry_run: bool
    resolved_tickers: list[str] = field(default_factory=list)
    reports: list[TickerIngestReport] = field(default_factory=list)
    kg_refresh: dict[str, Any] | None = None

    @property
    def total_sub_docs_uploaded(self) -> int:
        return sum(r.uploaded_sub_docs for r in self.reports)

    @property
    def total_ocr_pages(self) -> int:
        return sum(r.ocr_pages_used for r in self.reports)


def _normalize_pg_dsn(url: str) -> str:
    """Convert SQLAlchemy asyncpg/psycopg URL into raw `postgresql://` DSN for asyncpg."""
    dsn = (url or "").strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix) :]
    return dsn


class AsyncPGCoreDBBatchReader:
    """Strictly read-only Core PostgreSQL reader (`default_transaction_read_only = on`)."""

    def __init__(self, core_database_url: str) -> None:
        self._dsn = _normalize_pg_dsn(core_database_url)
        if not self._dsn:
            raise ValueError("SAG_CORE_DATABASE_URL is required for Core DB tier batch ingest")

    async def _connect(self) -> asyncpg.Connection:
        conn = await asyncpg.connect(self._dsn)
        await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")
        return conn

    async def resolve_tickers_for_tier(
        self,
        tier: IngestTier,
        *,
        as_of_date: date,
        adtv_threshold_vnd: float = DEFAULT_ADTV_BUFFER_VND,
    ) -> list[str]:
        if tier == "pilot_8":
            return list(PILOT_8_TICKERS)
        if tier == "custom":
            return []

        conn = await self._connect()
        try:
            if tier == "a5_36":
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT UPPER(TRIM(ticker)) AS sym
                    FROM log_counter_thesis
                    WHERE created_at::date <= $1
                    ORDER BY sym
                    """,
                    as_of_date,
                )
                return [str(r["sym"]) for r in rows if r["sym"]]

            if tier == "a4_81":
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT UPPER(TRIM(ticker)) AS sym
                    FROM investment_theses
                    WHERE created_at::date <= $1
                    ORDER BY sym
                    """,
                    as_of_date,
                )
                return [str(r["sym"]) for r in rows if r["sym"]]

            if tier == "a3_buffer_139":
                rows = await conn.fetch(
                    """
                    WITH a3 AS (
                        SELECT DISTINCT UPPER(TRIM(ticker)) AS sym
                        FROM log_equity_research
                        WHERE date <= $1
                    ),
                    latest_liq AS (
                        SELECT DISTINCT ON (ticker)
                            UPPER(TRIM(ticker)) AS sym,
                            adtv20_continuous
                        FROM market_data_daily
                        WHERE date <= $1 AND adtv20_continuous IS NOT NULL
                        ORDER BY ticker, date DESC
                    ),
                    buf AS (
                        SELECT sym FROM latest_liq WHERE adtv20_continuous >= $2
                    )
                    SELECT sym FROM a3
                    UNION
                    SELECT sym FROM buf
                    ORDER BY sym
                    """,
                    as_of_date,
                    float(adtv_threshold_vnd),
                )
                return [str(r["sym"]) for r in rows if r["sym"]]
        finally:
            await conn.close()

        return []

    async def fetch_candidates_for_ticker(
        self,
        ticker: str,
        *,
        as_of_date: date,
        years_back: int = 3,
    ) -> tuple[list[ResearchCandidateDocument], FinancialAnomalyContext]:
        sym = ticker.strip().upper()
        min_year = as_of_date.year - max(1, years_back)
        conn = await self._connect()
        candidates: list[ResearchCandidateDocument] = []
        anomaly = FinancialAnomalyContext()

        try:
            kd_rows = await conn.fetch(
                """
                SELECT id, symbol, published_date, title, url, source, doc_type, article_pdf_urls
                FROM knowledge_documents
                WHERE UPPER(symbol) = $1
                  AND (published_date IS NULL OR published_date::date <= $2)
                  AND COALESCE(doc_type, 'news') IN (
                      'annual_report', 'financial_statement', 'agm_resolution',
                      'prospectus', 'explanation', 'governance_report', 'analyst_report', 'news'
                  )
                ORDER BY published_date DESC NULLS LAST, id DESC
                LIMIT 120
                """,
                sym,
                as_of_date,
            )
            for row in kd_rows:
                urls: list[str] = []
                if row["url"]:
                    urls.append(str(row["url"]))
                extra_urls = row["article_pdf_urls"] or []
                for u in extra_urls:
                    if u and str(u) not in urls:
                        urls.append(str(u))
                pub = row["published_date"]
                pub_date = pub.date() if isinstance(pub, datetime) else pub
                if pub_date is not None and pub_date.year < min_year - 1:
                    continue
                candidates.append(
                    ResearchCandidateDocument(
                        doc_id=f"kd:{row['id']}",
                        ticker=sym,
                        doc_type=str(row["doc_type"] or "news"),
                        title=str(row["title"] or ""),
                        urls=urls,
                        source=str(row["source"] or "knowledge_documents"),
                        published_date=pub_date,
                    )
                )

            bctc_rows = await conn.fetch(
                """
                SELECT id, ticker, fiscal_year, fiscal_quarter, report_scope,
                       r2_pdf_url, announcement_date, created_at
                FROM bctc_pipeline_records
                WHERE UPPER(ticker) = $1
                  AND fiscal_year >= $2
                  AND r2_pdf_url IS NOT NULL
                  AND TRIM(r2_pdf_url) <> ''
                  AND UPPER(COALESCE(report_scope, '')) <> 'GOVERNANCE'
                  AND (
                      (announcement_date IS NOT NULL AND announcement_date <= $3)
                      OR (announcement_date IS NULL AND created_at::date <= $3)
                  )
                ORDER BY fiscal_year DESC, fiscal_quarter DESC
                LIMIT 40
                """,
                sym,
                min_year,
                as_of_date,
            )
            for row in bctc_rows:
                fq = int(row["fiscal_quarter"] or 0)
                fy = int(row["fiscal_year"])
                scope = str(row["report_scope"] or "CONSOLIDATED").upper()
                if scope not in ("CONSOLIDATED", "SEPARATE"):
                    scope = "UNKNOWN"
                doc_type = "LATEST_QUARTER" if fq > 0 else "BCTC"
                ann = row["announcement_date"]
                created = row["created_at"]
                pub_dt = ann or (created.date() if isinstance(created, datetime) else None)
                r2_url = str(row["r2_pdf_url"] or "").strip()
                candidates.append(
                    ResearchCandidateDocument(
                        doc_id=f"bctc:{row['id']}",
                        ticker=sym,
                        doc_type=doc_type,
                        title=f"BCTC {scope} {sym} Q{fq}/{fy}" if fq > 0 else f"BCTC {scope} {sym} FY{fy}",
                        urls=[r2_url] if r2_url else [],
                        source="bctc_pipeline_records",
                        statement_scope=scope,  # type: ignore[arg-type]
                        announcement_date=ann,
                        published_date=pub_dt,
                    )
                )

            # Detect mid-year quantitative anomaly (Condition #5 trigger for S13_QUARTER_NOTES)
            ratio_rows = await conn.fetch(
                """
                SELECT ratio_date, gross_margin, yoy_revenue_growth, yoy_earnings_growth
                FROM financial_ratios
                WHERE UPPER(symbol) = $1
                  AND frequency = 'quarterly'
                  AND COALESCE(published_date, ratio_date) <= $2
                ORDER BY ratio_date DESC
                LIMIT 5
                """,
                sym,
                as_of_date,
            )
            if len(ratio_rows) >= 2:
                latest, prev = ratio_rows[0], ratio_rows[1]
                yoy_rev = latest["yoy_revenue_growth"]
                gpm_cur, gpm_prev = latest["gross_margin"], prev["gross_margin"]
                gpm_delta_bps = (
                    (float(gpm_cur) - float(gpm_prev)) * 100.0
                    if gpm_cur is not None and gpm_prev is not None
                    else None
                )
                anomaly = FinancialAnomalyContext(
                    yoy_revenue_change_pct=float(yoy_rev) if yoy_rev is not None else None,
                    yoy_gpm_bps_change=gpm_delta_bps,
                )
        finally:
            await conn.close()

        return candidates, anomaly


def resolve_default_core_database_url() -> str:
    return (os.environ.get("SAG_CORE_DATABASE_URL") or "").strip()


async def _load_pdf_bytes_for_selected(
    selected: SelectedDocument,
    *,
    source_paths: dict[str, str],
    fetcher: BytesFetcherProtocol | None,
) -> tuple[bytes, str]:
    """Load PDF bytes from local override path (if supplied) or multi-source URL fallback."""
    cand = selected.candidate
    if selected.pdf_bytes:
        return selected.pdf_bytes, selected.resolved_url or (cand.urls[0] if cand.urls else "")
    if cand.raw_bytes:
        return cand.raw_bytes, cand.urls[0] if cand.urls else ""

    local_override = source_paths.get(cand.doc_id) or source_paths.get(cand.ticker)
    if local_override:
        p = pathlib.Path(local_override)
        if not p.is_file():
            raise InputBuildError(f"Local source override not found for {cand.ticker}: {p.name}")
        return p.read_bytes(), str(p)

    urls = [u for u in cand.urls if u]
    if not urls:
        raise InputBuildError(f"No downloadable URL or local source path for {cand.ticker} ({cand.doc_id})")
    fetched = await fetch_document_with_fallback(
        urls,
        fetcher=fetcher,
    )
    if not fetched.ok or not fetched.content:
        raise InputBuildError(
            f"Download failed for {cand.ticker} ({cand.doc_id}): {fetched.error or 'EMPTY_CONTENT'}"
        )
    return fetched.content, fetched.resolved_url


async def run_research_batch_ingest(
    *,
    tier: IngestTier,
    as_of_date: date | None = None,
    custom_tickers: Sequence[str] | None = None,
    years_back: int = 3,
    dry_run: bool = False,
    enable_llm_toc: bool = False,
    refresh_kg: bool = False,
    extract_tables: bool = True,
    auto_mineru_ocr: bool = False,
    source_paths: dict[str, str] | None = None,
    core_reader: CoreDBBatchReaderProtocol | None = None,
    fetcher: BytesFetcherProtocol | None = None,
    ocr_adapter: OcrAdapterProtocol | None = None,
    toc_router: TocRouterProtocol | None = None,
    subdoc_uploader: SubDocumentUploader | None = None,
    table_repo: TableRepositoryProtocol | None = None,
    table_card_indexer: TableCardIndexer | None = None,
    kg_refresher: KGRefreshHook | None = None,
    max_docs_per_ticker: int = 6,
) -> BatchIngestSummary:
    """Execute end-to-end tier-aware HOSE Stock Research batch ingest.

    Supports:
    - `pilot_8`: 8 calibration tickers covering all 4 branches A/B/C/D & 8 HOSE sectors.
    - `a5_36`: all tickers from Core DB `log_counter_thesis` (PIT <= as_of_date).
    - `a4_81`: all tickers from Core DB `investment_theses` (PIT <= as_of_date).
    - `a3_buffer_139`: Core DB `log_equity_research` union `market_data_daily` ADTV >= 5B VND.
    - `custom`: explicit ticker list.
    """
    cutoff = as_of_date or date.today()
    overrides = {k.upper(): v for k, v in (source_paths or {}).items()}
    summary = BatchIngestSummary(tier=tier, as_of_date=cutoff.isoformat(), dry_run=dry_run)

    if tier == "custom":
        tickers = sorted({t.strip().upper() for t in (custom_tickers or ()) if t and t.strip()})
    elif tier == "pilot_8":
        tickers = list(PILOT_8_TICKERS)
    else:
        if core_reader is None:
            core_url = resolve_default_core_database_url()
            core_reader = AsyncPGCoreDBBatchReader(core_url)
        tickers = await core_reader.resolve_tickers_for_tier(tier, as_of_date=cutoff)

    summary.resolved_tickers = tickers
    effective_toc_router: TocRouterProtocol | None = toc_router
    if effective_toc_router is None and enable_llm_toc:
        effective_toc_router = DeepInfraTocRouter()

    effective_ocr: OcrAdapterProtocol | None = ocr_adapter
    if effective_ocr is None and auto_mineru_ocr and settings.mineru_configured:
        effective_ocr = MinerUOcrAdapter(settings)

    ingested_tickers: list[str] = []

    for sym in tickers:
        report = TickerIngestReport(ticker=sym, status="NO_DOCUMENTS")
        try:
            if core_reader is not None:
                candidates, anomaly = await core_reader.fetch_candidates_for_ticker(
                    sym, as_of_date=cutoff, years_back=years_back
                )
            elif sym in overrides:
                # Offline local-file fallback when no Core DB reader is attached
                p = pathlib.Path(overrides[sym])
                candidates = [
                    ResearchCandidateDocument(
                        doc_id=f"local:{sym}:{p.name}",
                        ticker=sym,
                        doc_type="BCTN",
                        title=p.stem,
                        urls=[str(p)],
                        published_date=cutoff,
                        raw_bytes=p.read_bytes() if p.is_file() else None,
                    )
                ]
                anomaly = FinancialAnomalyContext()
            else:
                candidates = []
                anomaly = FinancialAnomalyContext()

            selection = await select_research_documents(
                sym,
                candidates,
                as_of_date=cutoff,
                anomaly_context=anomaly,
                fetcher=fetcher,
                inspect_payloads=False,
            )
            report.selected_documents = len(selection.selected)
            report.rejected_documents = len(selection.rejected)

            if not selection.selected:
                summary.reports.append(report)
                continue

            seen_slots: set[str] = set()
            seen_missing: set[str] = set()
            bctc_cov_samples: list[float] = []
            noise_red_samples: list[float] = []

            for sel in selection.selected[:max_docs_per_ticker]:
                try:
                    pdf_bytes, resolved_url = await _load_pdf_bytes_for_selected(
                        sel, source_paths=overrides, fetcher=fetcher
                    )
                    build_res: ResearchInputBuildResult = await build_research_sub_documents(
                        ticker=sym,
                        pdf_bytes=pdf_bytes,
                        doc_type=sel.canonical_doc_type,
                        published_date=sel.pit_resolution.effective_published_at,
                        title=sel.candidate.title,
                        source_url=resolved_url,
                        fiscal_year=sel.pit_resolution.parsed_fiscal_year,
                        fiscal_quarter=sel.pit_resolution.parsed_fiscal_quarter,
                        ocr_adapter=effective_ocr,
                        toc_router=effective_toc_router,
                    )
                except Exception as doc_exc:  # noqa: BLE001
                    report.errors.append(f"{sel.candidate.doc_id}: {doc_exc}")
                    continue

                if build_res.routed_branch not in report.branches:
                    report.branches.append(build_res.routed_branch)
                if build_res.localization_method not in report.localization_methods:
                    report.localization_methods.append(build_res.localization_method)
                report.ocr_pages_used += build_res.ocr_pages_used
                seen_missing.update(build_res.missing_core_slots)

                audit = build_res.coverage_audit
                if audit.bctc_total_pages > 0:
                    bctc_cov_samples.append(audit.bctc_notes_coverage_pct)
                if build_res.doc_type == "BCTN" and (audit.bctn_core_pages + audit.dropped_pages) > 0:
                    noise_red_samples.append(audit.noise_reduction_pct)

                unwrapped_pdf_bytes = pdf_bytes
                try:
                    unwrapped_pdf_bytes, _, _, _ = unwrap_pdf_payload(pdf_bytes)
                except Exception:  # noqa: BLE001
                    unwrapped_pdf_bytes = pdf_bytes

                for bundle in build_res.sub_documents:
                    seen_slots.add(bundle.sub_doc_slot)
                    doc_ver_id = bundle.content_hash[:24]
                    if not dry_run and subdoc_uploader is not None:
                        upload_resp = await subdoc_uploader(bundle)
                        resolved_ver = (
                            upload_resp.get("version_id") or upload_resp.get("document_version_id")
                            if isinstance(upload_resp, dict)
                            else None
                        )
                        if not resolved_ver:
                            if table_repo is not None or table_card_indexer is not None:
                                raise InputBuildError(
                                    f"Sub-document upload for {bundle.project_id}/{bundle.sub_doc_slot} "
                                    "did not return version_id (fail-closed to prevent FK violation)."
                                )
                            doc_ver_id = f"live:{bundle.content_hash[:16]}"
                        else:
                            doc_ver_id = str(resolved_ver)
                        report.uploaded_sub_docs += 1

                    # Dual-Representation Financial Table Engine wired directly into batch ingest
                    if extract_tables and bundle.sub_doc_slot in (
                        "BCTC_AUDITED",
                        "QUARTER_NOTES",
                        "BCTN_CORE",
                        "AGM_TARGETS",
                    ):
                        extracted_tables = extract_tables_from_document(
                            pdf_bytes=unwrapped_pdf_bytes,
                            project_id=bundle.project_id,
                            document_version_id=doc_ver_id,
                            sub_doc_slot=bundle.sub_doc_slot,
                            fiscal_year=bundle.fiscal_year,
                            fiscal_quarter=bundle.fiscal_quarter,
                            page_numbers=bundle.page_numbers,
                            markdown_content=bundle.markdown_content,
                            valid_from=bundle.valid_from,
                            left_sidebar_x=build_res.inspection.auto_sidebar_x,
                        )
                        report.tables_extracted += len(extracted_tables)
                        for tbl_rec in extracted_tables:
                            if tbl_rec.verification_status == "VERIFIED":
                                report.tables_verified += 1
                            elif tbl_rec.verification_status == "TABLE_UNVERIFIED":
                                report.tables_unverified += 1
                            if not dry_run and table_repo is not None:
                                await table_repo.upsert_table(tbl_rec)
                        if not dry_run and table_card_indexer is not None and extracted_tables:
                            await table_card_indexer(bundle, doc_ver_id, extracted_tables)

            report.sub_doc_slots = sorted(seen_slots)
            report.missing_core_slots = sorted(seen_missing - seen_slots)
            if bctc_cov_samples:
                report.bctc_notes_coverage_pct = round(min(bctc_cov_samples), 2)
            if noise_red_samples:
                report.noise_reduction_pct = round(sum(noise_red_samples) / len(noise_red_samples), 1)
            if report.sub_doc_slots:
                report.status = "DRY_RUN_READY" if dry_run else "INGESTED"
                ingested_tickers.append(sym)
            elif report.errors:
                report.status = "FAILED"
        except Exception as exc:  # noqa: BLE001
            report.status = "FAILED"
            report.errors.append(str(exc))

        summary.reports.append(report)

    if refresh_kg and not dry_run and kg_refresher is not None and ingested_tickers:
        summary.kg_refresh = await kg_refresher(ingested_tickers, cutoff)

    return summary
