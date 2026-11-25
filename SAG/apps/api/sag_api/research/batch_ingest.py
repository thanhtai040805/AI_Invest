"""HOSE Tier-Aware Batch Ingest Pipeline (`pilot_8 | a5_36 | a4_81 | a3_buffer_139 | custom`).

Connects Core PostgreSQL (read-only) -> ResearchDocumentSelector -> InputInspector ->
3-Layer ResearchInputBuilder (PDF Bookmarks / DeepInfra LLM TOC Router / Offset Calibration)
-> Dual-Representation Table Engine -> SAG Sub-Document Ingestion (Natural 4-Doc Classes, PIT valid_from)
-> Zero-Hardcode 3-Layer KG Refresh.
"""

from __future__ import annotations

import asyncio
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
    parse_fiscal_quarter_from_meta,
    parse_fiscal_year_from_meta,
    select_research_documents,
)
from sag_api.research.ocr_adapter import MinerUOcrAdapter
from sag_api.research.r2_markdown_cache import SagR2MarkdownCache
from sag_api.research.table_engine import (
    ExtractedTableRecord,
    TableRepositoryProtocol,
    extract_tables_from_document,
)
from sag_api.research.vn_filing_sources import (
    CafeFFallbackClient,
    VietcapIQPublicDateClient,
    VietstockDocumentClient,
    attach_cafef_fallback_urls,
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
    status: Literal["INGESTED", "R2_PREPARED", "DRY_RUN_READY", "NO_DOCUMENTS", "FAILED"]
    selected_documents: int = 0
    rejected_documents: int = 0
    branches: list[str] = field(default_factory=list)
    localization_methods: list[str] = field(default_factory=list)
    sub_doc_slots: list[str] = field(default_factory=list)
    missing_core_slots: list[str] = field(default_factory=list)
    ocr_pages_used: int = 0
    r2_cache_hits: int = 0
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


def _is_valid_vn_stock_ticker(sym: str) -> bool:
    """Vietnamese listed equities use 3-char ASCII alphanumeric tickers (filters test sentinels like HYDRATE)."""
    return len(sym) == 3 and sym.isascii() and sym.isalnum()


class AsyncPGCoreDBBatchReader:
    """Strictly read-only Core PostgreSQL reader (`default_transaction_read_only = on`)."""

    def __init__(
        self,
        core_database_url: str,
        *,
        include_live_vn_sources: bool = True,
    ) -> None:
        self._dsn = _normalize_pg_dsn(core_database_url)
        if not self._dsn:
            raise ValueError("SAG_CORE_DATABASE_URL is required for Core DB tier batch ingest")
        self._include_live_vn_sources = include_live_vn_sources

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
                return [str(r["sym"]) for r in rows if r["sym"] and _is_valid_vn_stock_ticker(str(r["sym"]))]

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
                return [str(r["sym"]) for r in rows if r["sym"] and _is_valid_vn_stock_ticker(str(r["sym"]))]

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
                return [str(r["sym"]) for r in rows if r["sym"] and _is_valid_vn_stock_ticker(str(r["sym"]))]
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
            is_brokerage = False
            stock_row = await conn.fetchrow(
                "SELECT sector, industry FROM stocks WHERE UPPER(symbol) = $1 LIMIT 1",
                sym,
            )
            if stock_row:
                sec_ind = f"{stock_row['sector'] or ''} {stock_row['industry'] or ''}".lower()
                is_brokerage = any(
                    kw in sec_ind for kw in ("chứng khoán", "chung khoan", "securities", "broker")
                )

            seen_primary_urls: set[str] = set()
            for row in kd_rows:
                urls: list[str] = []
                # Direct PDF/archive links in `article_pdf_urls` MUST come BEFORE HTML landing page `url`
                extra_urls = row["article_pdf_urls"] or []
                for u in extra_urls:
                    us = str(u or "").strip()
                    if us and us not in urls:
                        urls.append(us)
                row_url = str(row["url"] or "").strip()
                if row_url and row_url not in urls:
                    # Skip Vietstock HTML viewer URL when we already have direct static PDF/archive links
                    is_html_viewer = ".htm" in row_url.lower() and not row_url.lower().endswith(
                        (".pdf", ".zip", ".rar")
                    )
                    if not (urls and is_html_viewer):
                        urls.append(row_url)
                if not urls:
                    continue
                pub = row["published_date"]
                pub_date = pub.date() if isinstance(pub, datetime) else pub
                if pub_date is not None and pub_date.year < min_year - 1:
                    continue
                seen_primary_urls.add(urls[0])
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
                gpm_delta_bps = compute_gross_margin_delta_bps(gpm_cur, gpm_prev)
                anomaly = FinancialAnomalyContext(
                    yoy_revenue_change_pct=float(yoy_rev) if yoy_rev is not None else None,
                    yoy_gpm_bps_change=gpm_delta_bps,
                )
        finally:
            await conn.close()

        if self._include_live_vn_sources:
            pub_dates: dict[tuple[int, int], date] = {}
            try:
                pub_dates = await VietcapIQPublicDateClient().fetch_public_dates(sym)
            except Exception as vc_exc:  # noqa: BLE001
                log.warning("Vietcap IQ publicDate fetch failed for %s: %s", sym, vc_exc)

            # Enrich Core DB BCTC candidates with official Vietcap IQ `publicDate` (defeats Vietstock LastUpdate trap)
            if pub_dates:
                for cand in candidates:
                    if cand.announcement_date is not None:
                        continue
                    if cand.doc_type in ("financial_statement", "BCTC", "LATEST_QUARTER"):
                        u0 = cand.urls[0] if cand.urls else ""
                        fy = parse_fiscal_year_from_meta(cand.title, u0)
                        fq = parse_fiscal_quarter_from_meta(cand.title, u0)
                        if fy is not None and (fy, fq) in pub_dates:
                            cand.announcement_date = pub_dates[(fy, fq)]

            try:
                vs_cands, _skipped_en = await VietstockDocumentClient().collect_candidates(
                    sym,
                    as_of_date=as_of_date,
                    years_back=years_back,
                    is_brokerage=is_brokerage,
                    vietcap_public_dates=pub_dates,
                )
                for vc in vs_cands:
                    u0 = vc.urls[0] if vc.urls else ""
                    if u0 and u0 not in seen_primary_urls:
                        seen_primary_urls.add(u0)
                        candidates.append(vc)
            except Exception as vs_exc:  # noqa: BLE001
                log.warning("Live Vietstock fetch failed for %s: %s", sym, vs_exc)

            try:
                cafef_urls = await CafeFFallbackClient().fetch_backup_urls(sym)
                if cafef_urls:
                    attach_cafef_fallback_urls(candidates, cafef_urls)
            except Exception as cf_exc:  # noqa: BLE001
                log.warning("CafeF fallback URL fetch failed for %s: %s", sym, cf_exc)

        return candidates, anomaly


def compute_gross_margin_delta_bps(
    gpm_cur: float | int | None,
    gpm_prev: float | int | None,
) -> float | None:
    """Compute YoY/QoQ gross-margin delta in bps, auto-handling decimal (0.25) vs percent (25.0) storage."""
    if gpm_cur is None or gpm_prev is None:
        return None
    cur_f, prev_f = float(gpm_cur), float(gpm_prev)
    scale = 10_000.0 if (abs(cur_f) <= 1.0 and abs(prev_f) <= 1.0) else 100.0
    return round((cur_f - prev_f) * scale, 2)


def resolve_default_core_database_url() -> str:
    return (os.environ.get("SAG_CORE_DATABASE_URL") or settings.core_database_url or "").strip()


def _bundle_valid_date_str(bundle: SubDocumentBundle) -> str:
    """Return filesystem-safe `YYYY-MM-DD` date token for `bundle.valid_from` (`date` or `datetime`)."""
    vf = bundle.valid_from
    return vf.date().isoformat() if isinstance(vf, datetime) else vf.isoformat()[:10]


def build_research_logical_source_id(bundle: SubDocumentBundle) -> str:
    """Build a collision-free logical_source_id for HOSE research sub-documents.

    Includes `doc_type` so distinct filings sharing a slot/period (`BCTC` vs `BCTN`,
    `LATEST_QUARTER` vs `GIAI_TRINH` vs `TY_LE_AT_TC`) never supersede each other,
    and appends `valid_from` (`YYYY-MM-DD`) for AGMs so an Extraordinary AGM never overwrites an Annual AGM.
    """
    base = (
        f"hose:{bundle.project_id}:{bundle.fiscal_year}Q{bundle.fiscal_quarter}:"
        f"{bundle.doc_type}:{bundle.sub_doc_slot}"
    )
    if bundle.doc_type in ("NQ_DHDCD", "TAI_LIEU_DHDCD"):
        return f"{base}:{_bundle_valid_date_str(bundle)}"
    return base


def build_research_upload_filename(bundle: SubDocumentBundle) -> str:
    """Build a descriptive, Windows/POSIX-safe upload filename matching `build_research_logical_source_id`."""
    base = (
        f"{bundle.project_id}_{bundle.fiscal_year}Q{bundle.fiscal_quarter}_"
        f"{bundle.doc_type}_{bundle.sub_doc_slot}"
    )
    if bundle.doc_type in ("NQ_DHDCD", "TAI_LIEU_DHDCD"):
        return f"{base}_{_bundle_valid_date_str(bundle)}.md"
    return f"{base}.md"


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
        canonical_doc_type=selected.canonical_doc_type,
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
    r2_only: bool = False,
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
    r2_cache: SagR2MarkdownCache | None = None,
    max_docs_per_ticker: int = 8,
    include_recent_quarters: bool = True,
    max_latest_quarters: int = 2,
    doc_concurrency: int = 12,
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

    active_r2_cache = r2_cache if r2_cache is not None else SagR2MarkdownCache()

    ingested_tickers: list[str] = []
    eff_doc_conc = max(1, int(doc_concurrency))
    doc_sem = asyncio.Semaphore(eff_doc_conc)
    select_sem = asyncio.Semaphore(min(4, eff_doc_conc))

    async def _process_single_doc(
        sym: str,
        sel: SelectedDocument,
        *,
        skip_trailing_scanned_bctc: bool = False,
    ) -> dict[str, Any]:
        async with doc_sem:
            doc_out: dict[str, Any] = {
                "r2_hit": 0,
                "rejected_extra": 0,
                "error": None,
                "branch": None,
                "loc_method": None,
                "ocr_pages": 0,
                "missing_slots": set(),
                "bctc_cov": None,
                "noise_red": None,
                "slots": set(),
                "uploaded": 0,
                "tables_extracted": 0,
                "tables_verified": 0,
                "tables_unverified": 0,
                "bctc_embedded_full_digital": False,
            }
            pdf_bytes: bytes = b""
            try:
                pdf_bytes, resolved_url = await _load_pdf_bytes_for_selected(
                    sel, source_paths=overrides, fetcher=fetcher
                )
                cached_build: ResearchInputBuildResult | None = None
                if active_r2_cache.is_configured:
                    try:
                        cached_build = await active_r2_cache.load_cached_build_result(
                            ticker=sym,
                            pdf_bytes=pdf_bytes,
                            doc_type=sel.canonical_doc_type,
                            published_date=sel.pit_resolution.effective_published_at,
                            fiscal_year=sel.pit_resolution.parsed_fiscal_year,
                            fiscal_quarter=sel.pit_resolution.parsed_fiscal_quarter,
                        )
                    except InputBuildError:
                        raise
                    except Exception as cache_read_exc:  # noqa: BLE001
                        log.warning(
                            "R2 markdown cache read failed for %s (%s): %s",
                            sym,
                            sel.candidate.doc_id,
                            cache_read_exc,
                        )

                if cached_build is not None:
                    build_res = cached_build
                    doc_out["r2_hit"] = 1
                else:
                    build_res = await build_research_sub_documents(
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
                        skip_trailing_scanned_bctc=skip_trailing_scanned_bctc,
                    )
                    if not dry_run and active_r2_cache.is_configured and build_res.sub_documents:
                        try:
                            await active_r2_cache.save_cleaned_build_result(
                                pdf_bytes=pdf_bytes,
                                build_result=build_res,
                                fiscal_year=sel.pit_resolution.parsed_fiscal_year,
                                fiscal_quarter=sel.pit_resolution.parsed_fiscal_quarter,
                            )
                        except Exception as cache_write_exc:  # noqa: BLE001
                            log.warning(
                                "R2 markdown cache write failed for %s (%s): %s",
                                sym,
                                sel.candidate.doc_id,
                                cache_write_exc,
                            )
            except InputBuildError as build_exc:
                err_s = str(build_exc)
                if (
                    "EXCLUDED_FULL_ENGLISH" in err_s
                    or "EXCLUDED_CACHED_ENGLISH_DOC" in err_s
                ):
                    doc_out["rejected_extra"] = 1
                    if (
                        not dry_run
                        and active_r2_cache.is_configured
                        and pdf_bytes
                        and "EXCLUDED_CACHED_ENGLISH_DOC" not in err_s
                    ):
                        try:
                            await active_r2_cache.save_english_tombstone(
                                ticker=sym,
                                pdf_bytes=pdf_bytes,
                                doc_type=sel.canonical_doc_type,
                                fiscal_year=sel.pit_resolution.parsed_fiscal_year,
                                fiscal_quarter=sel.pit_resolution.parsed_fiscal_quarter,
                                reason=err_s,
                            )
                        except Exception as tomb_exc:  # noqa: BLE001
                            log.warning("R2 tombstone write failed for %s: %s", sym, tomb_exc)
                    return doc_out
                doc_out["error"] = f"{sel.candidate.doc_id}: {build_exc}"
                return doc_out
            except Exception as doc_exc:  # noqa: BLE001
                doc_out["error"] = f"{sel.candidate.doc_id}: {doc_exc}"
                return doc_out

            doc_out["branch"] = build_res.routed_branch
            doc_out["loc_method"] = build_res.localization_method
            doc_out["ocr_pages"] = build_res.ocr_pages_used
            doc_out["missing_slots"] = set(build_res.missing_core_slots)
            if sel.canonical_doc_type == "BCTN" and build_res.routed_branch == "A":
                min_full_pages = 40 if build_res.inspection.pages >= 60 else 10
                embedded_bctc_pages = sum(
                    len(b.page_numbers)
                    for b in build_res.sub_documents
                    if b.sub_doc_slot == "BCTC_AUDITED"
                )
                doc_out["bctc_embedded_full_digital"] = embedded_bctc_pages >= min_full_pages

            audit = build_res.coverage_audit
            if audit.bctc_total_pages > 0:
                doc_out["bctc_cov"] = audit.bctc_notes_coverage_pct
            if build_res.doc_type == "BCTN" and (audit.bctn_core_pages + audit.dropped_pages) > 0:
                doc_out["noise_red"] = audit.noise_reduction_pct

            unwrapped_pdf_bytes = pdf_bytes
            try:
                unwrapped_pdf_bytes, _, _, _ = unwrap_pdf_payload(
                    pdf_bytes, doc_type=sel.canonical_doc_type
                )
            except Exception:  # noqa: BLE001
                unwrapped_pdf_bytes = pdf_bytes

            try:
                for bundle in build_res.sub_documents:
                    doc_out["slots"].add(bundle.sub_doc_slot)
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
                        doc_out["uploaded"] += 1

                    # Dual-Representation Financial Table Engine wired directly into batch ingest
                    # Skip table extraction in `--r2-only` mode (tables are persisted in Phase B index)
                    # and offload synchronous pdfplumber work off the asyncio event loop.
                    if extract_tables and not r2_only and bundle.sub_doc_slot in (
                        "BCTC_AUDITED",
                        "QUARTER_NOTES",
                        "BCTN_CORE",
                        "AGM_TARGETS",
                    ):
                        extracted_tables = await asyncio.to_thread(
                            extract_tables_from_document,
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
                            two_col_crops=build_res.inspection.bilingual_two_col_crops,
                        )
                        doc_out["tables_extracted"] += len(extracted_tables)
                        for tbl_rec in extracted_tables:
                            if tbl_rec.verification_status == "VERIFIED":
                                doc_out["tables_verified"] += 1
                            elif tbl_rec.verification_status == "TABLE_UNVERIFIED":
                                doc_out["tables_unverified"] += 1
                            if not dry_run and table_repo is not None:
                                await table_repo.upsert_table(tbl_rec)
                        if not dry_run and table_card_indexer is not None and extracted_tables:
                            await table_card_indexer(bundle, doc_ver_id, extracted_tables)
            except Exception as upload_exc:  # noqa: BLE001
                doc_out["slots"].clear()
                doc_out["error"] = str(upload_exc)
                return doc_out
            log.info(
                "Doc ready ticker=%s type=%s fy=%s q=%s branch=%s ocr_pages=%d r2_hit=%d tables=%d/%d",
                sym,
                sel.canonical_doc_type,
                sel.pit_resolution.parsed_fiscal_year,
                sel.pit_resolution.parsed_fiscal_quarter,
                doc_out["branch"],
                doc_out["ocr_pages"],
                doc_out["r2_hit"],
                doc_out["tables_verified"],
                doc_out["tables_extracted"],
            )
            return doc_out

    async def _process_ticker(sym: str) -> TickerIngestReport:
        report = TickerIngestReport(ticker=sym, status="NO_DOCUMENTS")
        try:
            async with select_sem:
                if core_reader is not None:
                    candidates, anomaly = await core_reader.fetch_candidates_for_ticker(
                        sym, as_of_date=cutoff, years_back=years_back
                    )
                elif sym in overrides:
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

                has_local_or_inmemory_payload = any(c.raw_bytes is not None for c in candidates) or (
                    fetcher is not None
                )
                eff_max_quarters = max(1, min(int(max_latest_quarters), 8))
                selection = await select_research_documents(
                    sym,
                    candidates,
                    as_of_date=cutoff,
                    anomaly_context=anomaly,
                    fetcher=fetcher,
                    inspect_payloads=has_local_or_inmemory_payload,
                    max_latest_quarters=eff_max_quarters,
                    require_quarter_anomaly=not include_recent_quarters,
                )
            report.selected_documents = len(selection.selected)
            report.rejected_documents = len(selection.rejected)

            if not selection.selected:
                return report

            selected_slice = selection.selected[:max_docs_per_ticker]
            bctc_years = {
                sel.pit_resolution.parsed_fiscal_year
                for sel in selected_slice
                if sel.canonical_doc_type == "BCTC"
                and sel.pit_resolution.parsed_fiscal_year is not None
            }
            bctn_tasks_by_fy: dict[int, asyncio.Task[dict[str, Any]]] = {}
            other_tasks: list[asyncio.Task[dict[str, Any]]] = []
            deferred_bctcs: list[SelectedDocument] = []

            for sel in selected_slice:
                fy_s = sel.pit_resolution.parsed_fiscal_year
                if sel.canonical_doc_type == "BCTN" and fy_s is not None:
                    bctn_tasks_by_fy[fy_s] = asyncio.create_task(
                        _process_single_doc(
                            sym,
                            sel,
                            skip_trailing_scanned_bctc=(fy_s in bctc_years),
                        )
                    )
                elif sel.canonical_doc_type == "BCTC" and fy_s is not None:
                    deferred_bctcs.append(sel)
                else:
                    other_tasks.append(asyncio.create_task(_process_single_doc(sym, sel)))

            async def _run_bctc_after_bctn(sel: SelectedDocument) -> dict[str, Any]:
                fy_s = sel.pit_resolution.parsed_fiscal_year
                bctn_task = bctn_tasks_by_fy.get(fy_s) if fy_s is not None else None
                if bctn_task is not None:
                    bctn_res = await bctn_task
                    if bctn_res.get("bctc_embedded_full_digital"):
                        log.info(
                            "Skipped standalone BCTC for %s FY%s: Digital Branch-A BCTN already embeds full notes",
                            sym,
                            fy_s,
                        )
                        return {
                            "r2_hit": 0,
                            "rejected_extra": 1,
                            "error": None,
                            "branch": None,
                            "loc_method": None,
                            "ocr_pages": 0,
                            "missing_slots": set(),
                            "bctc_cov": None,
                            "noise_red": None,
                            "slots": set(),
                            "uploaded": 0,
                            "tables_extracted": 0,
                            "tables_verified": 0,
                            "tables_unverified": 0,
                            "bctc_embedded_full_digital": False,
                        }
                return await _process_single_doc(sym, sel)

            for sel in deferred_bctcs:
                other_tasks.append(asyncio.create_task(_run_bctc_after_bctn(sel)))

            all_tasks = list(bctn_tasks_by_fy.values()) + other_tasks
            doc_results = list(await asyncio.gather(*all_tasks)) if all_tasks else []

            seen_slots: set[str] = set()
            seen_missing: set[str] = set()
            bctc_cov_samples: list[float] = []
            noise_red_samples: list[float] = []

            for res in doc_results:
                report.r2_cache_hits += res["r2_hit"]
                report.rejected_documents += res["rejected_extra"]
                report.ocr_pages_used += res["ocr_pages"]
                if res["branch"] and res["branch"] not in report.branches:
                    report.branches.append(res["branch"])
                if res["loc_method"] and res["loc_method"] not in report.localization_methods:
                    report.localization_methods.append(res["loc_method"])
                if res["error"]:
                    report.errors.append(res["error"])
                    continue
                seen_missing.update(res["missing_slots"])
                if res["bctc_cov"] is not None:
                    bctc_cov_samples.append(res["bctc_cov"])
                if res["noise_red"] is not None:
                    noise_red_samples.append(res["noise_red"])
                seen_slots.update(res["slots"])
                report.uploaded_sub_docs += res["uploaded"]
                report.tables_extracted += res["tables_extracted"]
                report.tables_verified += res["tables_verified"]
                report.tables_unverified += res["tables_unverified"]

            report.sub_doc_slots = sorted(seen_slots)
            report.missing_core_slots = sorted(seen_missing - seen_slots)
            if bctc_cov_samples:
                report.bctc_notes_coverage_pct = round(min(bctc_cov_samples), 2)
            if noise_red_samples:
                report.noise_reduction_pct = round(sum(noise_red_samples) / len(noise_red_samples), 1)
            if report.sub_doc_slots:
                if dry_run:
                    report.status = "DRY_RUN_READY"
                elif r2_only:
                    report.status = "R2_PREPARED"
                else:
                    report.status = "INGESTED"
            elif report.errors:
                report.status = "FAILED"
        except Exception as exc:  # noqa: BLE001
            report.status = "FAILED"
            report.errors.append(str(exc))

        log.info(
            "Batch ingest ticker=%s status=%s selected=%d rejected=%d slots=%s ocr_pages=%d "
            "r2_hits=%d tables=%d/%d errors=%d",
            sym,
            report.status,
            report.selected_documents,
            report.rejected_documents,
            report.sub_doc_slots,
            report.ocr_pages_used,
            report.r2_cache_hits,
            report.tables_verified,
            report.tables_extracted,
            len(report.errors),
        )
        return report

    if eff_doc_conc > 1 and len(tickers) > 1:
        ticker_reports = await asyncio.gather(*(_process_ticker(sym) for sym in tickers))
    else:
        ticker_reports = [await _process_ticker(sym) for sym in tickers]

    for rep in ticker_reports:
        summary.reports.append(rep)
        if rep.status in ("DRY_RUN_READY", "R2_PREPARED", "INGESTED"):
            ingested_tickers.append(rep.ticker)

    if refresh_kg and not dry_run and not r2_only and kg_refresher is not None and ingested_tickers:
        summary.kg_refresh = await kg_refresher(ingested_tickers, cutoff)

    return summary
