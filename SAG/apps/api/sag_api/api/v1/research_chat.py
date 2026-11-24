"""Research Chat HTTP + SSE API endpoints (`/api/v1/research-chat/*`).

Aligned with PROJECT.md Contract 3:
- Uses dedicated `require_research_bff_principal` (NEVER `require_project_bff_principal`).
- `POST /api/v1/research-chat/stream` (`EventSourceResponse`, no buffering)
- `GET /api/v1/research-chat/kg/{ticker}?as_of_date=YYYY-MM-DD`
- `GET /api/v1/research-chat/tables/{table_id}`
- `POST /api/v1/research-chat/ingest-pilot`
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import secrets
import time
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from sag_api.core.config import settings
from sag_api.core.db import get_session
from sag_api.core.error_taxonomy import ErrorCode
from sag_api.core.errors import ApiError, ForbiddenError, NotFoundError, ServiceUnavailableError, ValidationError
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import (
    VerifiedPrincipal,
    require_bff_service_token,
    required_bff_header,
)
from sag_api.research.chat_orchestrator import (
    ResearchChatOrchestrator,
    SQLAlchemyTableStore,
    TableStoreProtocol,
)
from sag_api.research.kg_store import (
    KGStoreProtocol,
    SQLAlchemyKGStore,
    refresh_research_knowledge_graph,
)
from sag_api.schemas.research_chat import (
    BatchIngestRequest,
    BatchIngestResponse,
    BatchIngestTickerReportSchema,
    HydratedTableResponse,
    IngestedTickersResponse,
    KGSubgraphResponse,
    PilotIngestItemResult,
    PilotIngestRequest,
    PilotIngestResponse,
    ResearchChatStreamRequest,
)

log = get_logger("research_chat_api")

router = APIRouter(prefix="/research-chat", tags=["research-chat"])


async def require_research_bff_principal(request: Request) -> VerifiedPrincipal:
    """Authenticate Continuum BFF callers on `/api/v1/research-chat/*` (no `{project_id}` path param)."""
    await require_bff_service_token(request)

    subject = required_bff_header(request, "X-Continuum-User-Id")
    organization_id = required_bff_header(request, "X-Continuum-Organization-Id")
    project_id = required_bff_header(request, "X-Continuum-Project-Id")
    partition_id = required_bff_header(
        request,
        "X-Continuum-Security-Partition",
        max_length=64,
    )
    if partition_id != project_id:
        raise ForbiddenError("Continuum service scope does not match Security Partition")

    if project_id == "all":
        raw_allowed = required_bff_header(
            request,
            "X-Continuum-Allowed-Project-Ids",
            max_length=8192,
        )
        parsed_ids = [part.strip().upper() for part in raw_allowed.split(",") if part.strip()]
        max_projects = settings.principal_assertion_max_project_ids
        if (
            not parsed_ids
            or len(parsed_ids) > max_projects
            or any(len(pid) > 64 or pid.lower() == "all" for pid in parsed_ids)
        ):
            raise ForbiddenError("Invalid Continuum service context")
        resolved_project_ids = frozenset(parsed_ids)
        resolved_partition_ids = frozenset(parsed_ids)
        conversation_scope: str | None = "all"
    else:
        normalized_pid = project_id.upper()
        resolved_project_ids = frozenset({normalized_pid})
        resolved_partition_ids = frozenset({normalized_pid})
        conversation_scope = None

    issued_at = int(time.time())
    principal = VerifiedPrincipal(
        subject=subject,
        organization_id=organization_id,
        allowed_project_ids=resolved_project_ids,
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id=secrets.token_urlsafe(18),
        issued_at=issued_at,
        expires_at=issued_at + 60,
        tenant_id=organization_id,
        allowed_partition_ids=resolved_partition_ids,
        conversation_scope=conversation_scope,
    )
    request.state.principal = principal
    return principal


def get_kg_store(session: AsyncSession = Depends(get_session)) -> KGStoreProtocol:
    return SQLAlchemyKGStore(session)


def get_table_store(session: AsyncSession = Depends(get_session)) -> TableStoreProtocol:
    return SQLAlchemyTableStore(session)


def get_research_orchestrator(
    request: Request,
    session: AsyncSession = Depends(get_session),
    kg_store: KGStoreProtocol = Depends(get_kg_store),
    table_store: TableStoreProtocol = Depends(get_table_store),
) -> ResearchChatOrchestrator:
    engine_manager = getattr(request.app.state, "engine_manager", None)
    llm = getattr(request.app.state, "llm", None)
    return ResearchChatOrchestrator(
        session=session,
        kg_store=kg_store,
        table_store=table_store,
        llm=llm,
        engine_manager=engine_manager,
    )


def _authorize_ticker(principal: VerifiedPrincipal, ticker: str) -> str:
    sym = ticker.strip().upper()
    if "*" not in principal.allowed_project_ids and sym not in principal.allowed_project_ids:
        raise ForbiddenError(f"Ticker {sym} is not authorized in Continuum principal scope")
    return sym


def _sse(event: str, data: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(
        event=event,
        data=json.dumps(data, ensure_ascii=False, default=str),
    )


@router.post("/stream")
async def stream_research_chat(
    body: ResearchChatStreamRequest,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    orchestrator: ResearchChatOrchestrator = Depends(get_research_orchestrator),
) -> EventSourceResponse:
    """Stream 7-stage SAG v2 Research Chat execution over SSE."""
    # Fail-closed validation (6-ticker ceiling & principal scope) BEFORE opening SSE stream
    orchestrator.validate_and_resolve_tickers(
        primary_ticker=body.ticker,
        compare_tickers=body.compare_tickers,
        principal=principal,
    )

    async def event_gen():
        try:
            async for event_name, payload in orchestrator.stream_chat(body, principal=principal):
                yield _sse(event_name, payload)
        except asyncio.CancelledError:
            raise
        except ApiError as error:
            log.warning("Research chat stream terminated (%s): %s", error.code, error.message)
            yield _sse("error", {"code": error.code, "message": error.message})
        except Exception as error:  # noqa: BLE001
            log.exception("Unhandled error in research chat stream: %s", error)
            yield _sse(
                "error",
                {"code": ErrorCode.STREAM_ERROR, "message": "Research chat stream failed"},
            )

    return EventSourceResponse(
        event_gen(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tickers", response_model=IngestedTickersResponse)
async def list_ingested_research_tickers(
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> IngestedTickersResponse:
    """Return the live org-scoped list of tickers currently ingested in SAG (0% hardcoded)."""
    from sqlalchemy import select

    from sag_api.db.models import Document
    from sag_api.research.batch_ingest import PILOT_8_TICKERS

    filters = [Document.is_active.is_(True), Document.project_id.is_not(None)]
    if principal.tenant_id:
        filters.append(Document.tenant_id == principal.tenant_id)
    rows = (await session.execute(select(Document.project_id).where(*filters).distinct())).all()
    discovered = sorted(
        {
            str(pid).strip().upper()
            for (pid,) in rows
            if pid
            and (
                "*" in principal.allowed_project_ids
                or str(pid).strip().upper() in principal.allowed_project_ids
            )
        }
    )
    return IngestedTickersResponse(
        organization_id=principal.organization_id,
        tickers=discovered,
        pilot_fallback=list(PILOT_8_TICKERS),
    )


@router.get("/kg/{ticker}", response_model=KGSubgraphResponse)
async def get_ticker_kg_subgraph(
    ticker: str,
    as_of_date: date | None = Query(default=None),
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    kg_store: KGStoreProtocol = Depends(get_kg_store),
) -> KGSubgraphResponse:
    """Return strict Point-in-Time 3-layer KG subgraph for a ticker."""
    sym = _authorize_ticker(principal, ticker)
    cutoff = as_of_date or date.today()
    data = await kg_store.get_subgraph_for_ticker(sym, cutoff)
    return KGSubgraphResponse(**data)


@router.get("/tables/{table_id}", response_model=HydratedTableResponse)
async def get_hydrated_table(
    table_id: str,
    as_of_date: date | None = Query(default=None),
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    table_store: TableStoreProtocol = Depends(get_table_store),
) -> HydratedTableResponse:
    """Return a single full hydrated 2D table from `sag_extracted_tables` with PIT, tenant & ACL check."""
    import inspect

    sig = inspect.signature(table_store.get_table_by_id)
    if "tenant_id" in sig.parameters and principal.tenant_id:
        tbl = await table_store.get_table_by_id(
            table_id,
            as_of_date=as_of_date,
            tenant_id=principal.tenant_id,
        )
    else:
        tbl = await table_store.get_table_by_id(table_id, as_of_date=as_of_date)
    if tbl is None:
        raise NotFoundError(f"Hydrated table '{table_id}' not found")
    _authorize_ticker(principal, str(tbl.get("project_id") or ""))
    return HydratedTableResponse(**tbl)


@router.post("/ingest-pilot", response_model=PilotIngestResponse)
async def ingest_pilot_sources(
    body: PilotIngestRequest,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
) -> PilotIngestResponse:
    """Inspect/trigger pilot HOSE source ingestion (fail-closed if source missing)."""
    cutoff = body.as_of_date or date.today()
    results: list[PilotIngestItemResult] = []
    ready_count = 0

    for raw_ticker in body.tickers:
        sym = _authorize_ticker(principal, raw_ticker)
        raw_path = body.source_paths.get(sym) or body.source_paths.get(raw_ticker)
        if not raw_path:
            results.append(
                PilotIngestItemResult(
                    ticker=sym,
                    status="MISSING_SOURCE",
                    detail=f"No source path provided for {sym}",
                )
            )
            continue

        path = pathlib.Path(raw_path)
        if not path.is_file():
            results.append(
                PilotIngestItemResult(
                    ticker=sym,
                    status="MISSING_SOURCE",
                    detail=f"Source file not found for {sym}: {path.name}",
                )
            )
            continue

        branch = "DIGITAL_AUTO"
        pages_total = 0
        try:
            from sag_api.research.input_inspector import inspect_pdf_bytes

            insp = inspect_pdf_bytes(path.read_bytes(), source_url=str(path))
            branch = str(insp.routed_branch or branch)
            pages_total = int(insp.pages or 0)
        except ImportError:
            try:
                import fitz

                with fitz.open(str(path)) as doc:
                    pages_total = len(doc)
            except Exception:  # noqa: BLE001
                pages_total = 0

        ready_count += 1
        results.append(
            PilotIngestItemResult(
                ticker=sym,
                status="READY",
                branch=branch,
                pages_total=pages_total,
                detail=f"Inspected {path.name}",
            )
        )

    if ready_count == 0:
        raise NotFoundError("No valid pilot source files found (fail-closed)")

    return PilotIngestResponse(as_of_date=cutoff.isoformat(), results=results)


@router.post("/ingest-batch", response_model=BatchIngestResponse)
async def ingest_research_batch(
    body: BatchIngestRequest,
    request: Request,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> BatchIngestResponse:
    """Run tier-aware HOSE Stock Research batch ingest (`pilot_8 | a5_36 | a4_81 | a3_buffer_139 | custom`)."""
    from sag_api.research.batch_ingest import (
        PILOT_8_TICKERS,
        AsyncPGCoreDBBatchReader,
        resolve_default_core_database_url,
        run_research_batch_ingest,
    )
    from sag_api.research.input_builder import SubDocumentBundle
    from sag_api.research.table_engine import SqlTableRepository
    from sag_api.services.document_service import handle_document_upload

    if body.tier == "custom" and not body.tickers:
        raise ValidationError("tier='custom' requires a non-empty tickers list")

    core_reader = None
    core_url = resolve_default_core_database_url()
    if core_url:
        core_reader = AsyncPGCoreDBBatchReader(core_url)
    elif body.tier in ("a5_36", "a4_81", "a3_buffer_139"):
        raise ServiceUnavailableError(
            "SAG_CORE_DATABASE_URL is not configured for Core DB tier resolution"
        )

    # Auth-Before-Action (N8b): resolve & authorize ALL target tickers BEFORE any ingest runs
    cutoff = body.as_of_date or date.today()
    if body.tier == "custom":
        pre_resolved = sorted({t.strip().upper() for t in body.tickers if t and t.strip()})
    elif body.tier == "pilot_8":
        pre_resolved = list(PILOT_8_TICKERS)
    else:
        assert core_reader is not None
        pre_resolved = await core_reader.resolve_tickers_for_tier(body.tier, as_of_date=cutoff)

    for sym in pre_resolved:
        _authorize_ticker(principal, sym)

    subdoc_uploader = None
    table_repo = None
    if not body.dry_run:
        job_queue = getattr(request.app.state, "job_queue", None)

        async def _internal_upload(bundle: SubDocumentBundle) -> dict[str, Any]:
            idem_key = (
                f"{bundle.project_id}-{bundle.fiscal_year}Q{bundle.fiscal_quarter}-"
                f"{bundle.sub_doc_slot}-{bundle.content_hash[:12]}"
            )
            logical_source_id = (
                f"hose:{bundle.project_id}:{bundle.fiscal_year}Q{bundle.fiscal_quarter}:{bundle.sub_doc_slot}"
            )
            filename = (
                f"{bundle.project_id}_{bundle.fiscal_year}Q{bundle.fiscal_quarter}_{bundle.sub_doc_slot}.md"
            )
            res = await handle_document_upload(
                session,
                tenant_id=principal.tenant_id or principal.organization_id,
                project_id=bundle.project_id,
                owner_id=principal.subject,
                security_partition_id=bundle.project_id,
                client_token=idem_key,
                file_bytes=bundle.markdown_content.encode("utf-8"),
                original_filename=filename,
                content_type="text/markdown",
                logical_source_id=logical_source_id,
                source_published_at=bundle.valid_from,
                fiscal_year=bundle.fiscal_year,
                fiscal_quarter=bundle.fiscal_quarter,
                doc_type=bundle.doc_type,
                sub_doc_slot=bundle.sub_doc_slot,
                job_queue=job_queue,
            )
            return res.model_dump()

        subdoc_uploader = _internal_upload
        table_repo = SqlTableRepository(session)
        engine_manager = getattr(request.app.state, "engine_manager", None)

        async def _index_table_cards(
            bundle: SubDocumentBundle,
            doc_ver_id: str,
            extracted_tables: list[Any],
        ) -> int:
            from sag_api.services.search_index_service import index_table_search_cards_for_version

            q_client = None
            embedder = None
            if engine_manager is not None:
                get_q = getattr(engine_manager, "get_search_unit_qdrant_client", None)
                if callable(get_q):
                    try:
                        q_client = await get_q()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Could not acquire Qdrant client for table cards (%s)", type(exc).__name__)
                get_emb = getattr(engine_manager, "get_sag_embedding", None)
                if callable(get_emb):
                    try:
                        embedder = await get_emb()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Could not acquire embedder for table cards (%s)", type(exc).__name__)
            return await index_table_search_cards_for_version(
                session,
                document_version_id=doc_ver_id,
                project_id=bundle.project_id,
                table_records=extracted_tables,
                security_partition_id=bundle.project_id,
                tenant_id=principal.tenant_id or principal.organization_id,
                qdrant_client=q_client,
                embedder=embedder,
            )

        async def _refresh_kg(ingested: list[str], cutoff_date: date) -> dict[str, Any]:
            return await refresh_research_knowledge_graph(
                session,
                tickers=ingested,
                as_of_date=cutoff_date,
                core_database_url=core_url or None,
                tenant_id=principal.tenant_id or principal.organization_id,
            )

        table_card_indexer = _index_table_cards
        kg_refresher = _refresh_kg
    else:
        table_card_indexer = None
        kg_refresher = None

    summary = await run_research_batch_ingest(
        tier="custom" if body.tier != "pilot_8" else "pilot_8",
        as_of_date=cutoff,
        custom_tickers=pre_resolved,
        years_back=body.years_back,
        dry_run=body.dry_run,
        enable_llm_toc=body.enable_llm_toc,
        refresh_kg=body.refresh_kg,
        auto_mineru_ocr=(not body.dry_run and settings.mineru_configured),
        source_paths=body.source_paths,
        core_reader=core_reader,
        subdoc_uploader=subdoc_uploader,
        table_repo=table_repo,
        table_card_indexer=table_card_indexer,
        kg_refresher=kg_refresher,
    )
    if not body.dry_run and table_repo is not None:
        await session.commit()

    return BatchIngestResponse(
        tier=body.tier,
        as_of_date=summary.as_of_date,
        dry_run=summary.dry_run,
        resolved_tickers=summary.resolved_tickers,
        total_sub_docs_uploaded=summary.total_sub_docs_uploaded,
        total_ocr_pages=summary.total_ocr_pages,
        reports=[
            BatchIngestTickerReportSchema(
                ticker=r.ticker,
                status=r.status,
                selected_documents=r.selected_documents,
                rejected_documents=r.rejected_documents,
                branches=r.branches,
                localization_methods=r.localization_methods,
                sub_doc_slots=r.sub_doc_slots,
                missing_core_slots=r.missing_core_slots,
                ocr_pages_used=r.ocr_pages_used,
                uploaded_sub_docs=r.uploaded_sub_docs,
                tables_extracted=r.tables_extracted,
                tables_verified=r.tables_verified,
                tables_unverified=r.tables_unverified,
                bctc_notes_coverage_pct=r.bctc_notes_coverage_pct,
                noise_reduction_pct=r.noise_reduction_pct,
                errors=r.errors,
            )
            for r in summary.reports
        ],
        kg_refresh=summary.kg_refresh,
    )
