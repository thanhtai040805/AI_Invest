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
from sag_api.research.financial_metrics_store import (
    AsyncPGCoreMetricsReader,
    CoreMetricsReaderProtocol,
    load_org_catalog,
    resolve_core_database_url,
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
    CascadeDeleteSessionRequest,
    CascadeDeleteSessionResponse,
    HydratedTableResponse,
    IngestedTickersResponse,
    IssuerProfileItem,
    KGSubgraphResponse,
    LinkedSessionItem,
    MemoryIngestTurnRequest,
    MemoryIngestTurnResponse,
    PilotIngestItemResult,
    PilotIngestRequest,
    PilotIngestResponse,
    RelatedSessionsResponse,
    ResearchChatStreamRequest,
    UserMemoryClearResponse,
    UserMemoryItemResponse,
    UserMemoryListResponse,
    UserMemoryPatchRequest,
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


def get_core_metrics_reader() -> CoreMetricsReaderProtocol | None:
    dsn = resolve_core_database_url()
    return AsyncPGCoreMetricsReader(dsn) if dsn else None


def get_research_orchestrator(
    request: Request,
    session: AsyncSession = Depends(get_session),
    kg_store: KGStoreProtocol = Depends(get_kg_store),
    table_store: TableStoreProtocol = Depends(get_table_store),
    core_reader: CoreMetricsReaderProtocol | None = Depends(get_core_metrics_reader),
) -> ResearchChatOrchestrator:
    from sag_api.core.db import SessionLocal

    engine_manager = getattr(request.app.state, "engine_manager", None)
    llm = getattr(request.app.state, "llm", None)
    return ResearchChatOrchestrator(
        session=session,
        session_factory=SessionLocal,
        kg_store=kg_store,
        table_store=table_store,
        llm=llm,
        engine_manager=engine_manager,
        core_reader=core_reader,
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
    as_of_date: date | None = Query(default=None),
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
    core_reader: CoreMetricsReaderProtocol | None = Depends(get_core_metrics_reader),
) -> IngestedTickersResponse:
    """Return live org-scoped tickers enriched with Core PG read-only sector/profile metadata."""
    from sag_api.research.batch_ingest import PILOT_8_TICKERS

    catalog = await load_org_catalog(
        session,
        principal=principal,
        as_of_date=as_of_date or date.today(),
        core_reader=core_reader,
    )
    return IngestedTickersResponse(
        organization_id=principal.organization_id,
        tickers=catalog["tickers"],
        pilot_fallback=list(PILOT_8_TICKERS),
        sectors=catalog["sectors"],
        profiles=[IssuerProfileItem(**p) for p in catalog["profiles"]],
        catalog_source=catalog["catalog_source"],
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
        build_research_logical_source_id,
        build_research_upload_filename,
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
    uploaded_version_ids: list[str] = []
    if not body.dry_run:
        job_queue = getattr(request.app.state, "job_queue", None)

        async def _internal_upload(bundle: SubDocumentBundle) -> dict[str, Any]:
            idem_key = (
                f"{bundle.project_id}-{bundle.fiscal_year}Q{bundle.fiscal_quarter}-"
                f"{bundle.doc_type}-{bundle.sub_doc_slot}-{bundle.content_hash[:12]}"
            )
            logical_source_id = build_research_logical_source_id(bundle)
            filename = build_research_upload_filename(bundle)
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
            dumped = res.model_dump()
            ver_id = str(dumped.get("document_version_id") or dumped.get("version_id") or "")
            if ver_id:
                uploaded_version_ids.append(ver_id)
            return dumped

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
        if uploaded_version_ids:
            try:
                from sqlalchemy import select as sa_select

                from sag_api.db.models.document import Document
                from sag_api.db.models.routing_rag import DocumentVersion
                from sag_api.research.user_memory_pipeline import propagate_document_version_staleness

                new_vers = (
                    await session.scalars(
                        sa_select(DocumentVersion).where(DocumentVersion.id.in_(uploaded_version_ids))
                    )
                ).all()
                superseded_ids: list[str] = []
                doc_ids = {v.document_id for v in new_vers if v.document_id}
                for v in new_vers:
                    if v.supersedes_id:
                        superseded_ids.append(str(v.supersedes_id))
                if doc_ids:
                    older_vers = (
                        await session.scalars(
                            sa_select(DocumentVersion.id).where(
                                DocumentVersion.document_id.in_(doc_ids),
                                DocumentVersion.id.not_in(uploaded_version_ids),
                            )
                        )
                    ).all()
                    for oid in older_vers:
                        if str(oid) not in superseded_ids:
                            superseded_ids.append(str(oid))

                    # Also detect prior-period filings for the same (tenant_id, project_id, sub_doc_slot)
                    new_docs = {
                        d.id: d
                        for d in (
                            await session.scalars(sa_select(Document).where(Document.id.in_(doc_ids)))
                        ).all()
                    }
                    project_ids = {d.project_id for d in new_docs.values() if d.project_id}
                    if project_ids:
                        slot_cutoffs: dict[tuple[str, str], Any] = {}
                        for v in new_vers:
                            doc_obj = new_docs.get(v.document_id)
                            if doc_obj is None or not doc_obj.project_id:
                                continue
                            meta = v.metadata_json if isinstance(v.metadata_json, dict) else {}
                            slot = str(meta.get("sub_doc_slot") or "").strip()
                            if not slot:
                                continue
                            key = (str(doc_obj.project_id), slot)
                            prev_vf = slot_cutoffs.get(key)
                            if prev_vf is None or (v.valid_from and v.valid_from > prev_vf):
                                slot_cutoffs[key] = v.valid_from
                        if slot_cutoffs:
                            peer_rows = (
                                await session.execute(
                                    sa_select(
                                        DocumentVersion.id,
                                        Document.project_id,
                                        DocumentVersion.valid_from,
                                        DocumentVersion.metadata_json,
                                    )
                                    .join(Document, Document.id == DocumentVersion.document_id)
                                    .where(
                                        Document.tenant_id == (principal.tenant_id or principal.organization_id),
                                        Document.project_id.in_(project_ids),
                                        DocumentVersion.id.not_in(uploaded_version_ids),
                                    )
                                )
                            ).all()
                            for p_vid, p_pid, p_vf, p_meta in peer_rows:
                                p_slot = str(
                                    (p_meta.get("sub_doc_slot") if isinstance(p_meta, dict) else "") or ""
                                ).strip()
                                cutoff_vf = slot_cutoffs.get((str(p_pid or ""), p_slot))
                                if cutoff_vf is not None and p_vf is not None and p_vf < cutoff_vf:
                                    if str(p_vid) not in superseded_ids:
                                        superseded_ids.append(str(p_vid))
                if superseded_ids:
                    await propagate_document_version_staleness(
                        session,
                        superseded_ids,
                        reason_status="STALE_NEW_FILING",
                        reason_detail="Batch ingest đã nạp phiên bản tài liệu mới cho mã liên quan.",
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("Batch ingest staleness propagation skipped (%s)", type(exc).__name__)
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


# ── SAG-Native User Memory & Linked Sessions REST APIs (100% IDOR-Proof) ─────


def _memory_row_to_response(row: Any) -> UserMemoryItemResponse:
    return UserMemoryItemResponse(
        memory_id=str(row.memory_id),
        scope=str(row.scope),
        memory_type=str(row.memory_type),
        pit_sensitivity=str(row.pit_sensitivity),
        canonical_slot_key=row.canonical_slot_key,
        content_text=str(row.content_text),
        structured_payload=dict(row.structured_payload_json or {}),
        tickers=list(row.tickers_json or []),
        bound_doc_version_ids=list(row.bound_doc_version_ids_json or []),
        confidence=float(row.confidence or 0.85),
        source_authority=str(row.source_authority),
        origin_episode_id=row.origin_episode_id,
        origin_session_id=row.origin_session_id,
        source_as_of_date=row.source_as_of_date.isoformat(),
        is_backtest_sandbox=bool(row.is_backtest_sandbox),
        valid_from=row.valid_from.isoformat() if row.valid_from else "",
        valid_to=row.valid_to.isoformat() if row.valid_to else "",
        tx_from=row.tx_from.isoformat() if row.tx_from else "",
        tx_to=row.tx_to.isoformat() if row.tx_to else "",
        supersedes_memory_id=row.supersedes_memory_id,
        staleness_status=str(row.staleness_status or "FRESH"),
        staleness_reason=row.staleness_reason,
        reinforcement_count=int(row.reinforcement_count or 1),
        pinned_by_user=bool(row.pinned_by_user),
        qdrant_sync_status=str(row.qdrant_sync_status or "PENDING_UPSERT"),
        sync_version=int(row.sync_version or 1),
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _principal_scope(principal: VerifiedPrincipal) -> tuple[str, str]:
    tenant_id = (principal.tenant_id or principal.organization_id or "aiinvest-default").strip()
    user_id = str(principal.subject or "").strip()
    if not user_id:
        raise ForbiddenError("Missing authenticated subject for user memory scope")
    return tenant_id, user_id


@router.post("/memories/ingest-turn", response_model=MemoryIngestTurnResponse)
async def ingest_research_memory_turn(
    body: MemoryIngestTurnRequest,
    request: Request,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> MemoryIngestTurnResponse:
    """Enqueue an idempotent async W0 turn-memory job for the authenticated user (`principal.subject`)."""
    from sag_api.research.user_memory_pipeline import enqueue_ingest_turn_job

    tenant_id, user_id = _principal_scope(principal)
    result = await enqueue_ingest_turn_job(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=body.session_id,
        assistant_message_id=body.assistant_message_id,
        user_content=body.user_content,
        assistant_content=body.assistant_content,
        parent_session_id=body.parent_session_id,
        session_title=body.session_title,
        chat_mode=body.chat_mode,
        user_message_id=body.user_message_id,
        turn_index=body.turn_index,
        turn_as_of_date=body.turn_as_of_date,
        memory_mode=body.memory_mode,
        tickers=body.tickers,
        sectors=body.sectors,
        macro_factors=body.macro_factors,
        cited_doc_version_ids=body.cited_doc_version_ids,
        blindspot_codes=body.blindspot_codes,
        stage7_hallucinated=body.stage7_hallucinated,
        stream_interrupted=body.stream_interrupted,
    )
    await session.commit()
    worker = getattr(request.app.state, "user_memory_worker", None)
    if worker is not None and result.get("accepted") and hasattr(worker, "wakeup"):
        worker.wakeup()
    return MemoryIngestTurnResponse(**result)


@router.post("/memories/cascade-delete-session", response_model=CascadeDeleteSessionResponse)
async def cascade_delete_research_session(
    body: CascadeDeleteSessionRequest,
    request: Request,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> CascadeDeleteSessionResponse:
    """Tombstone a deleted session and soft-retract all its memories/edges for `principal.subject`."""
    from sag_api.research.user_memory_pipeline import cascade_delete_session_memories

    tenant_id, user_id = _principal_scope(principal)
    q_client = None
    engine_manager = getattr(request.app.state, "engine_manager", None)
    if engine_manager is not None:
        get_q = getattr(engine_manager, "get_search_unit_qdrant_client", None)
        if callable(get_q):
            try:
                q_client = await get_q()
            except Exception:  # noqa: BLE001
                q_client = None

    res = await cascade_delete_session_memories(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=body.session_id,
        reason=body.reason or "USER_DELETED_SESSION",
        qdrant_client=q_client,
    )
    worker = getattr(request.app.state, "user_memory_worker", None)
    if worker is not None and hasattr(worker, "wakeup"):
        worker.wakeup()
    return CascadeDeleteSessionResponse(**res)


@router.get("/memories", response_model=UserMemoryListResponse)
async def list_user_memories(
    memory_type: str | None = Query(default=None),
    ticker: str | None = Query(default=None),
    status: str = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> UserMemoryListResponse:
    """List user memories strictly scoped to `(tenant_id, principal.subject)`."""
    from sqlalchemy import select as sa_select

    from sag_api.db.models.user_memory import INF_DATETIME, UserMemoryItem

    tenant_id, user_id = _principal_scope(principal)
    filters: list[Any] = [
        UserMemoryItem.tenant_id == tenant_id,
        UserMemoryItem.user_id == user_id,
    ]
    clean_status = (status or "active").strip().lower()
    if clean_status == "active":
        filters.extend([UserMemoryItem.valid_to >= INF_DATETIME, UserMemoryItem.tx_to >= INF_DATETIME])
    elif clean_status == "stale":
        filters.extend(
            [
                UserMemoryItem.valid_to >= INF_DATETIME,
                UserMemoryItem.tx_to >= INF_DATETIME,
                UserMemoryItem.staleness_status != "FRESH",
            ]
        )
    elif clean_status == "superseded":
        filters.append((UserMemoryItem.valid_to < INF_DATETIME) | (UserMemoryItem.tx_to < INF_DATETIME))

    if memory_type:
        filters.append(UserMemoryItem.memory_type == memory_type.strip().upper())

    all_rows = (
        await session.scalars(
            sa_select(UserMemoryItem)
            .where(*filters)
            .order_by(
                UserMemoryItem.pinned_by_user.desc(),
                UserMemoryItem.updated_at.desc(),
                UserMemoryItem.created_at.desc(),
            )
        )
    ).all()

    if ticker:
        sym = ticker.strip().upper()
        all_rows = [r for r in all_rows if sym in {str(t).upper() for t in (r.tickers_json or [])}]

    total = len(all_rows)
    page = all_rows[offset : offset + limit]
    return UserMemoryListResponse(
        total=total,
        items=[_memory_row_to_response(r) for r in page],
    )


@router.patch("/memories/{memory_id}", response_model=UserMemoryItemResponse)
async def patch_user_memory(
    memory_id: str,
    body: UserMemoryPatchRequest,
    request: Request,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> UserMemoryItemResponse:
    """Edit content or pin/unpin a memory strictly owned by `(tenant_id, principal.subject)`."""
    from sqlalchemy import select as sa_select

    from sag_api.db.models.user_memory import INF_DATETIME, UserMemoryItem
    from sag_api.research.user_memory_pipeline import (
        _audit_entry,
        _memory_snapshot,
        sanitize_user_patch_content,
        user_memory_scope_lock,
    )

    if body.content_text is None and body.pinned_by_user is None:
        raise ValidationError("At least one of `content_text` or `pinned_by_user` must be provided")

    tenant_id, user_id = _principal_scope(principal)
    async with user_memory_scope_lock(session, tenant_id, user_id):
        row = await session.scalar(
            sa_select(UserMemoryItem).where(
                UserMemoryItem.memory_id == memory_id,
                UserMemoryItem.tenant_id == tenant_id,
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.valid_to >= INF_DATETIME,
                UserMemoryItem.tx_to >= INF_DATETIME,
            )
        )
        if row is None:
            raise NotFoundError(f"User memory '{memory_id}' not found")

        before = _memory_snapshot(row)
        content_changed = False
        if body.content_text is not None:
            sanitized_patch, err_code = sanitize_user_patch_content(
                body.content_text,
                current_memory_type=row.memory_type,
            )
            if sanitized_patch is None:
                raise ValidationError(f"Invalid memory content ({err_code or 'REJECTED'})")
            row.content_text = sanitized_patch["content_text"]
            row.content_hash = sanitized_patch["content_hash"]
            row.memory_type = sanitized_patch["memory_type"]
            row.pit_sensitivity = sanitized_patch["pit_sensitivity"]
            if sanitized_patch["tickers"]:
                row.tickers_json = sanitized_patch["tickers"]
            content_changed = True
        if body.pinned_by_user is not None:
            row.pinned_by_user = bool(body.pinned_by_user)

        row.source_authority = "USER_CONFIRMED"
        if content_changed or row.qdrant_sync_status == "PENDING_UPSERT":
            row.qdrant_sync_status = "PENDING_UPSERT"
        else:
            row.qdrant_sync_status = "PENDING_PATCH"
        row.sync_version = int(row.sync_version or 1) + 1
        session.add(
            _audit_entry(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_id=row.memory_id,
                action="USER_PATCH",
                actor="USER",
                before_json=before,
                after_json=_memory_snapshot(row),
                reason=body.reason or "USER_EDIT_OR_PIN",
            )
        )
        await session.flush()

    await session.commit()
    await session.refresh(row)
    worker = getattr(request.app.state, "user_memory_worker", None)
    if worker is not None and hasattr(worker, "wakeup"):
        worker.wakeup()
    return _memory_row_to_response(row)


@router.delete("/memories/{memory_id}", response_model=UserMemoryItemResponse)
async def delete_user_memory(
    memory_id: str,
    request: Request,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> UserMemoryItemResponse:
    """Soft-retract a single memory owned by `(tenant_id, principal.subject)` + schedule `PENDING_DELETE`."""
    from datetime import UTC, datetime

    from sqlalchemy import select as sa_select

    from sag_api.db.models.user_memory import INF_DATETIME, UserMemoryItem
    from sag_api.research.user_memory_pipeline import (
        _audit_entry,
        _memory_snapshot,
        user_memory_scope_lock,
    )

    tenant_id, user_id = _principal_scope(principal)
    now = datetime.now(UTC)
    async with user_memory_scope_lock(session, tenant_id, user_id):
        row = await session.scalar(
            sa_select(UserMemoryItem).where(
                UserMemoryItem.memory_id == memory_id,
                UserMemoryItem.tenant_id == tenant_id,
                UserMemoryItem.user_id == user_id,
                UserMemoryItem.tx_to >= INF_DATETIME,
            )
        )
        if row is None:
            raise NotFoundError(f"User memory '{memory_id}' not found")

        before = _memory_snapshot(row)
        row.tx_to = now
        row.valid_to = min(row.valid_to, now) if row.valid_to else now
        row.invalidation_reason = "USER_DELETED"
        row.qdrant_sync_status = "PENDING_DELETE"
        row.sync_version = int(row.sync_version or 1) + 1
        session.add(
            _audit_entry(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_id=row.memory_id,
                action="USER_DELETE",
                actor="USER",
                before_json=before,
                after_json=_memory_snapshot(row),
                reason="USER_DELETED",
            )
        )
        await session.flush()

    await session.commit()
    await session.refresh(row)
    worker = getattr(request.app.state, "user_memory_worker", None)
    if worker is not None and hasattr(worker, "wakeup"):
        worker.wakeup()
    return _memory_row_to_response(row)


@router.post("/memories/clear", response_model=UserMemoryClearResponse)
async def clear_all_user_memories(
    request: Request,
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> UserMemoryClearResponse:
    """Soft-retract ALL memories, episodes, and personal graph edges for `(tenant_id, principal.subject)`."""
    from datetime import UTC, datetime

    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from sag_api.db.models.user_memory import (
        INF_DATETIME,
        UserMemoryEdge,
        UserMemoryEpisode,
        UserMemoryItem,
        UserMemoryJob,
    )
    from sag_api.research.user_memory_pipeline import _audit_entry, user_memory_scope_lock

    tenant_id, user_id = _principal_scope(principal)
    now = datetime.now(UTC)

    async with user_memory_scope_lock(session, tenant_id, user_id):
        items = (
            await session.scalars(
                sa_select(UserMemoryItem).where(
                    UserMemoryItem.tenant_id == tenant_id,
                    UserMemoryItem.user_id == user_id,
                    UserMemoryItem.tx_to >= INF_DATETIME,
                )
            )
        ).all()
        for item in items:
            item.tx_to = now
            item.valid_to = min(item.valid_to, now) if item.valid_to else now
            item.invalidation_reason = "USER_CLEARED_ALL"
            item.qdrant_sync_status = "PENDING_DELETE"
            item.sync_version = int(item.sync_version or 1) + 1

        episodes = (
            await session.scalars(
                sa_select(UserMemoryEpisode).where(
                    UserMemoryEpisode.tenant_id == tenant_id,
                    UserMemoryEpisode.user_id == user_id,
                    UserMemoryEpisode.status != "DELETED",
                )
            )
        ).all()
        for ep in episodes:
            ep.status = "DELETED"

        edges = (
            await session.scalars(
                sa_select(UserMemoryEdge).where(
                    UserMemoryEdge.tenant_id == tenant_id,
                    UserMemoryEdge.user_id == user_id,
                    UserMemoryEdge.tx_to >= INF_DATETIME,
                )
            )
        ).all()
        for edge in edges:
            edge.tx_to = now
            edge.valid_to = min(edge.valid_to, now) if edge.valid_to else now

        # Cancel any queued/retry INGEST_TURN jobs so debounced turns cannot resurrect cleared memories
        await session.execute(
            sa_update(UserMemoryJob)
            .where(
                UserMemoryJob.tenant_id == tenant_id,
                UserMemoryJob.user_id == user_id,
                UserMemoryJob.job_kind == "INGEST_TURN",
                UserMemoryJob.status.in_(("QUEUED", "RETRY")),
            )
            .values(status="SUCCEEDED", error="CANCELLED_BY_CLEAR_ALL")
        )

        session.add(
            _audit_entry(
                tenant_id=tenant_id,
                user_id=user_id,
                memory_id=None,
                action="USER_CLEAR_ALL",
                actor="USER",
                reason=f"Cleared {len(items)} memories, {len(episodes)} episodes, {len(edges)} edges",
            )
        )
        await session.flush()

    await session.commit()
    worker = getattr(request.app.state, "user_memory_worker", None)
    if worker is not None and hasattr(worker, "wakeup"):
        worker.wakeup()
    return UserMemoryClearResponse(
        retracted_memories=len(items),
        deleted_episodes=len(episodes),
        closed_edges=len(edges),
    )


@router.get("/sessions/{session_id}/related", response_model=RelatedSessionsResponse)
async def get_related_sessions(
    session_id: str,
    limit: int = Query(default=5, ge=1, le=20),
    principal: VerifiedPrincipal = Depends(require_research_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> RelatedSessionsResponse:
    """Return top linked sessions & staleness status strictly scoped to `(tenant_id, principal.subject)`."""
    from sqlalchemy import or_ as sa_or
    from sqlalchemy import select as sa_select

    from sag_api.db.models.user_memory import (
        INF_DATETIME,
        UserMemoryEdge,
        UserMemoryEntity,
        UserMemoryEpisode,
    )

    tenant_id, user_id = _principal_scope(principal)
    clean_sid = session_id.strip()

    episode = await session.scalar(
        sa_select(UserMemoryEpisode).where(
            UserMemoryEpisode.tenant_id == tenant_id,
            UserMemoryEpisode.user_id == user_id,
            UserMemoryEpisode.external_session_id == clean_sid,
            UserMemoryEpisode.status == "ACTIVE",
        )
    )
    if episode is None:
        return RelatedSessionsResponse(session_id=clean_sid, related_sessions=[])

    sess_entity = await session.scalar(
        sa_select(UserMemoryEntity).where(
            UserMemoryEntity.tenant_id == tenant_id,
            UserMemoryEntity.user_id == user_id,
            UserMemoryEntity.canonical_key == f"SESSION:{clean_sid}",
        )
    )
    if sess_entity is None:
        return RelatedSessionsResponse(
            session_id=clean_sid,
            episode_id=episode.episode_id,
            staleness_status=episode.staleness_status or "FRESH",
            staleness_reason=episode.staleness_reason,
            related_sessions=[],
        )

    link_rels = ("SESSION_CONTINUED_FROM", "SESSION_THESIS_EVOLUTION", "SESSION_RELATED_THEME")
    edges = (
        await session.scalars(
            sa_select(UserMemoryEdge)
            .where(
                UserMemoryEdge.tenant_id == tenant_id,
                UserMemoryEdge.user_id == user_id,
                UserMemoryEdge.relation_type.in_(link_rels),
                UserMemoryEdge.valid_to >= INF_DATETIME,
                UserMemoryEdge.tx_to >= INF_DATETIME,
                sa_or(
                    UserMemoryEdge.src_entity_id == sess_entity.entity_id,
                    UserMemoryEdge.dst_entity_id == sess_entity.entity_id,
                ),
            )
            .order_by(UserMemoryEdge.weight.desc(), UserMemoryEdge.created_at.desc())
            .limit(limit * 2)
        )
    ).all()

    other_entity_ids = [
        e.dst_entity_id if e.src_entity_id == sess_entity.entity_id else e.src_entity_id
        for e in edges
    ]
    if not other_entity_ids:
        return RelatedSessionsResponse(
            session_id=clean_sid,
            episode_id=episode.episode_id,
            staleness_status=episode.staleness_status or "FRESH",
            staleness_reason=episode.staleness_reason,
            related_sessions=[],
        )

    other_entities = {
        ent.entity_id: ent
        for ent in (
            await session.scalars(
                sa_select(UserMemoryEntity).where(
                    UserMemoryEntity.entity_id.in_(other_entity_ids),
                    UserMemoryEntity.tenant_id == tenant_id,
                    UserMemoryEntity.user_id == user_id,
                )
            )
        ).all()
    }
    other_sids = [
        ent.canonical_key.split(":", 1)[1]
        for ent in other_entities.values()
        if ent.canonical_key.startswith("SESSION:")
    ]
    other_eps = {
        ep.external_session_id: ep
        for ep in (
            await session.scalars(
                sa_select(UserMemoryEpisode).where(
                    UserMemoryEpisode.tenant_id == tenant_id,
                    UserMemoryEpisode.user_id == user_id,
                    UserMemoryEpisode.external_session_id.in_(other_sids),
                    UserMemoryEpisode.status == "ACTIVE",
                )
            )
        ).all()
    }

    related: list[LinkedSessionItem] = []
    seen_sids: set[str] = set()
    for edge in edges:
        other_eid = edge.dst_entity_id if edge.src_entity_id == sess_entity.entity_id else edge.src_entity_id
        ent = other_entities.get(other_eid)
        if ent is None or not ent.canonical_key.startswith("SESSION:"):
            continue
        other_sid = ent.canonical_key.split(":", 1)[1]
        if other_sid in seen_sids or other_sid == clean_sid:
            continue
        other_ep = other_eps.get(other_sid)
        if other_ep is None:
            continue
        seen_sids.add(other_sid)
        related.append(
            LinkedSessionItem(
                badge_id=f"S{len(related) + 1}",
                episode_id=other_ep.episode_id,
                session_id=other_ep.external_session_id,
                session_title=other_ep.session_title,
                episode_summary=other_ep.episode_summary or other_ep.session_title or other_ep.external_session_id,
                tickers=list(other_ep.tickers_json or []),
                macro_factors=list(other_ep.macro_factors_json or []),
                sectors=list(other_ep.sectors_json or []),
                relation_type=str(edge.relation_type),
                link_score=round(float(edge.weight or 0.0), 4),
                source_as_of_date=other_ep.source_as_of_date.isoformat(),
                staleness_status=str(other_ep.staleness_status or "FRESH"),
                staleness_reason=other_ep.staleness_reason,
            )
        )
        if len(related) >= limit:
            break

    return RelatedSessionsResponse(
        session_id=clean_sid,
        episode_id=episode.episode_id,
        staleness_status=episode.staleness_status or "FRESH",
        staleness_reason=episode.staleness_reason,
        related_sessions=related,
    )
