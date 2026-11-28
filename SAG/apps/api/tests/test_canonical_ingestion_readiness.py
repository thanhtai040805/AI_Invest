import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.config import Settings
from sag_api.core.errors import ValidationError
from sag_api.db.base import Base
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    IngestionRun,
    Job,
    KnowledgeJob,
    Source,
    SourceProjectMapping,
)
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.jobs import tasks
from sag_api.jobs.knowledge import KnowledgeWorker
from sag_api.sag.dto import ProcessCheckpoint
from sag_api.sag.engine_manager import EngineManager
from sag_api.sag.incremental_processor import IncrementalDocumentProcessor
from sag_api.services import search_index_service
from sag_api.services.source_service import search_source_candidates


@pytest_asyncio.fixture
async def ingestion_db(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    file = tmp_path / "notes.md"
    file.write_text("# Upload policy\n\nThe upload size limit is 10 MiB per file.", encoding="utf-8")
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    async with factory() as session:
        session.add(Source(id="source", name="Source", sag_source_config_id="config"))
        await session.flush()
        session.add(
            SourceProjectMapping(
                source_id="source",
                organization_id="tenant",
                project_id="project",
                state="CONFIRMED",
                confirmed_at=datetime.now(UTC),
                confirmed_by="user",
                approval_ref="test",
            )
        )
        session.add(
            Document(
                id="document",
                source_id="source",
                tenant_id="tenant",
                project_id="project",
                filename=file.name,
                storage_path=str(file),
                status=DocumentStatus.PENDING,
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id="version",
                document_id="document",
                version_no=1,
                file_hash=digest,
                metadata_json={"security_partition_id": "project"},
            )
        )
        await session.flush()
        session.add(
            IngestionRun(
                id="run",
                tenant_id="tenant",
                project_id="project",
                document_version_id="version",
                idempotency_key="key",
                payload_hash=digest,
                status="QUEUED",
            )
        )
        session.add(
            Job(
                id="job",
                type=JobType.PROCESS_DOCUMENT,
                status=JobStatus.RUNNING,
                source_id="source",
                document_id="document",
                payload={"run_id": "run"},
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [True, False])
async def test_canonical_ingestion_never_calls_legacy_extractor_by_default(ingestion_db, monkeypatch, verified):
    monkeypatch.setattr(tasks.settings, "document_event_extraction_enabled", False)

    async def index(session, *, document_version, **kwargs):
        document_version.search_status = "SEARCH_READY" if verified else "INDEX_FAILED"
        document_version.search_ready_at = datetime.now(UTC) if verified else None
        return [SimpleNamespace(id="canonical-unit")]

    monkeypatch.setattr(search_index_service, "run_search_indexing_stage", index)
    manager = SimpleNamespace(
        get_sag_embedding=AsyncMock(return_value=None),
        process_document=AsyncMock(side_effect=AssertionError("Unexpected legacy LLM extraction")),
    )
    async with ingestion_db() as session:
        job = await session.get(Job, "job")
        if verified:
            await tasks._process_document_unlocked(session, job, engine_manager=manager)
            await session.refresh(await session.get(Document, "document"))
            assert (await session.get(Document, "document")).status == DocumentStatus.READY
            assert (await session.get(IngestionRun, "run")).status == "SUCCEEDED"
            assert (await session.get(DocumentVersion, "version")).knowledge_status == "NOT_STARTED"
            assert (await session.scalars(select(CanonicalBlock))).all()
        else:
            with pytest.raises(RuntimeError, match="manifest"):
                await tasks._process_document_unlocked(session, job, engine_manager=manager)
            assert (await session.get(Document, "document")).status == DocumentStatus.FAILED
        manager.process_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_foundation_can_start_from_search_ready_before_legacy_document_completion(ingestion_db):
    async with ingestion_db() as session:
        doc = await session.get(Document, "document")
        doc.status = DocumentStatus.EXTRACTING
        version = await session.get(DocumentVersion, "version")
        version.search_status, version.search_ready_at = "SEARCH_READY", datetime.now(UTC)
        text = "The upload size limit is 10 MiB."
        session.add(
            CanonicalBlock(
                id="block",
                document_version_id="version",
                ordinal=0,
                block_type="paragraph",
                page_from=1,
                page_to=1,
                section_path="Upload",
                normalized_text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                source_anchor="upload",
            )
        )
        await session.commit()
    worker = KnowledgeWorker(ingestion_db, Settings())
    await worker.discover()
    async with ingestion_db() as session:
        job = await session.scalar(select(KnowledgeJob))
        assert job is not None and job.kind == "FOUNDATION" and job.status == "QUEUED"
        assert (await session.get(Document, "document")).status == DocumentStatus.EXTRACTING


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_legacy_processor_requires_explicit_event_extraction_opt_in(enabled):
    processor = IncrementalDocumentProcessor(SimpleNamespace(), "config", max_concurrency=1, extract_events=enabled)
    processor._sample_language = AsyncMock()
    processor._chunk_set_ref = lambda checkpoint: SimpleNamespace(chunk_ids=checkpoint.chunk_ids)
    processor._extract = AsyncMock(return_value=(False, SimpleNamespace(event_ids=["event"], event_count=1)))
    outcome = await processor.process(
        None,
        checkpoint=ProcessCheckpoint(source_id="source", chunk_ids=["chunk"]),
        on_checkpoint=AsyncMock(),
        should_pause=AsyncMock(return_value=False),
    )
    assert outcome.event_count == (1 if enabled else 0)
    assert processor._extract.await_count == int(enabled)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_limit", [0, 2])
async def test_source_search_never_silently_omits_authorized_sources(ingestion_db, monkeypatch, configured_limit):
    monkeypatch.setattr(tasks.settings, "search_source_candidate_limit", configured_limit)
    async with ingestion_db() as session:
        for index in range(18):
            source_id = f"extra-{index}"
            session.add(Source(id=source_id, name=source_id, sag_source_config_id=source_id))
            await session.flush()
            session.add(
                SourceProjectMapping(
                    source_id=source_id,
                    organization_id="tenant",
                    project_id="project" if index < 17 else "other",
                    state="CONFIRMED",
                    confirmed_at=datetime.now(UTC),
                    confirmed_by="user",
                    approval_ref="test",
                )
            )
        await session.commit()
        principal = SimpleNamespace(allowed_project_ids={"project"}, organization_id="tenant")
        if configured_limit:
            with pytest.raises(ValidationError, match="budget"):
                await search_source_candidates(session, principal=principal, include_project_sources=True)
        else:
            result = await search_source_candidates(session, principal=principal, include_project_sources=True)
            assert len(result) == 18
            assert "extra-17" not in {source.id for source in result}


@pytest.mark.asyncio
async def test_canonical_embedding_reuses_sdk_client_without_starting_event_storage(monkeypatch):
    import zleap.sag.core.ai.factory as factory

    client = SimpleNamespace(
        model="test",
        base_url="https://example.invalid",
        dimensions=1024,
        max_tokens=8192,
        generate=AsyncMock(),
        batch_generate=AsyncMock(),
        close=AsyncMock(),
    )
    create = AsyncMock(return_value=client)
    monkeypatch.setattr(factory, "create_embedding_client", create)
    manager = EngineManager(Settings())
    manager._slot = AsyncMock(side_effect=AssertionError("Legacy storage must not start for canonical embeddings"))
    first = await manager.get_sag_embedding("source-a")
    second = await manager.get_sag_embedding("source-b")
    assert first is second
    create.assert_awaited_once()
    manager._slot.assert_not_awaited()
    await manager.aclose_all()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("prune_fails", [False, True])
@pytest.mark.parametrize("missing_index", [False, True])
async def test_verified_new_version_publishes_even_when_old_point_cleanup_fails(ingestion_db, monkeypatch, prune_fails, missing_index):
    import json

    import httpx

    from sag_api.services import dedup_and_temporal_service as temporal

    points = []
    deleted = []
    created_indexes = []

    async def index(_client, *, units, **kwargs):
        points.extend(
            {
                "id": search_index_service.generate_search_unit_point_id("search_units_project", unit.id),
                "payload": {"content_hash": unit.content_hash},
            }
            for unit in units
        )
        return len(units)

    def respond(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/index"):
            created_indexes.append(body["field_name"])
            return httpx.Response(200, json={"result": {}})
        if request.url.path.endswith("/points/count"):
            return httpx.Response(200, json={"result": {"count": len(points)}})
        if request.url.path.endswith("/points/scroll"):
            return httpx.Response(200, json={"result": {"points": points}})
        match = body["filter"]["must"][0]["match"]
        if missing_index and match.get("value") == "version" and not created_indexes:
            return httpx.Response(400, text='Index required but not found for "document_version_id"')
        if match.get("any") == ["old-version"]:
            deleted.append(match["any"])
            if prune_fails:
                return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"result": {}})

    monkeypatch.setattr(search_index_service, "index_search_units_to_qdrant", index)
    monkeypatch.setattr(
        temporal,
        "resolve_temporal_supersedes",
        AsyncMock(
            return_value={
                "action": "SUPERSEDED",
                "supersedes_id": "old-version",
            }
        ),
    )
    async with ingestion_db() as session:
        session.add(
            CanonicalBlock(
                id="block",
                document_version_id="version",
                ordinal=0,
                block_type="paragraph",
                page_from=1,
                page_to=1,
                section_path="Upload",
                normalized_text="Upload policy is 10 MiB.",
                content_hash=hashlib.sha256(b"Upload policy is 10 MiB.").hexdigest(),
                source_anchor="upload",
            )
        )
        await session.flush()
        version = await session.get(DocumentVersion, "version")
        async with httpx.AsyncClient(
            base_url="https://qdrant.invalid", transport=httpx.MockTransport(respond)
        ) as client:
            units = await search_index_service.run_search_indexing_stage(
                session,
                project_id="project",
                document_version=version,
                security_partition_id="project",
                tenant_id="tenant",
                qdrant_client=client,
            )
        assert units
        assert version.search_status == "SEARCH_READY"
        assert deleted == [["old-version"]]
        assert created_indexes == (["document_version_id"] if missing_index else [])


@pytest.mark.asyncio
async def test_project_upload_revoke_and_status_lifecycle(ingestion_db, tmp_path, monkeypatch):
    from sag_api.services.document_service import (
        get_document_version_status,
        handle_document_upload,
        resolve_project_document,
        revoke_project_document,
    )

    monkeypatch.setattr(tasks.settings, "data_dir", str(tmp_path))
    async with ingestion_db() as session:
        upload_res = await handle_document_upload(
            session,
            tenant_id="tenant-2",
            project_id="project-2",
            owner_id="user-1",
            security_partition_id="project-2",
            client_token="idem-1",
            file_bytes=b"key: value\n",
            original_filename="config.yaml",
            content_type="application/yaml",
            logical_source_id="be-doc-123",
        )
        assert upload_res.version_no == 1
        assert upload_res.is_duplicate is False

        mapping = await session.scalar(
            select(SourceProjectMapping).where(
                SourceProjectMapping.organization_id == "tenant-2",
                SourceProjectMapping.project_id == "project-2",
                SourceProjectMapping.state == "CONFIRMED",
            )
        )
        assert mapping is not None

        resolved = await resolve_project_document(
            session,
            tenant_id="tenant-2",
            project_id="project-2",
            document_id_or_logical_id="be-doc-123",
        )
        assert resolved.id == upload_res.document_id
        resolved.progress = 45
        resolved.chunk_count = 7
        await session.commit()

        status_res = await get_document_version_status(
            session,
            project_id="project-2",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert status_res.progress == 45
        assert status_res.chunk_count == 7
        assert status_res.document_status == "pending"

        revoke_res = await revoke_project_document(
            session,
            tenant_id="tenant-2",
            project_id="project-2",
            logical_source_id="be-doc-123",
            reason="DOCUMENT_ARCHIVED",
        )
        assert revoke_res.status == "REVOKED"
        assert revoke_res.document_id == upload_res.document_id

        revoke_again = await revoke_project_document(
            session,
            tenant_id="tenant-2",
            project_id="project-2",
            logical_source_id="be-doc-123",
        )
        assert revoke_again.status == "ALREADY_REVOKED"

        not_found = await revoke_project_document(
            session,
            tenant_id="tenant-2",
            project_id="project-2",
            logical_source_id="missing-doc",
        )
        assert not_found.status == "NOT_FOUND"

        reupload_res = await handle_document_upload(
            session,
            tenant_id="tenant-2",
            project_id="project-2",
            owner_id="user-1",
            security_partition_id="project-2",
            client_token="idem-2",
            file_bytes=b"key: value\n",
            original_filename="config.yaml",
            content_type="application/yaml",
            logical_source_id="be-doc-123",
        )
        assert reupload_res.version_no == 2
        assert reupload_res.is_duplicate is False


@pytest.mark.asyncio
async def test_canonical_stage_progress_pause_resume_and_reprocess_status(ingestion_db, tmp_path, monkeypatch):
    JobPaused = tasks.JobPaused
    from sag_api.services import canonical_service, dedup_and_temporal_service
    from sag_api.services.document_service import (
        get_document_version_status,
        handle_document_upload,
        pause_document,
        reprocess_document,
        resume_document,
    )

    monkeypatch.setattr(tasks.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(tasks.settings, "document_event_extraction_enabled", False)

    fake_queue = SimpleNamespace(
        enqueue=AsyncMock(),
        begin_source_maintenance=lambda *_args, **_kwargs: None,
        source_maintenance_requested=lambda *_args, **_kwargs: False,
        source_stop_requested=lambda *_args, **_kwargs: False,
    )

    observed_stages: list[tuple[str, int, str]] = []
    parse_calls = 0
    dedup_calls = 0

    orig_parse = canonical_service.parse_and_persist_document_content
    orig_dedup = dedup_and_temporal_service.run_dedup_and_temporal_stage

    async with ingestion_db() as session:
        upload_res = await handle_document_upload(
            session,
            tenant_id="tenant-stage",
            project_id="project-stage",
            owner_id="user-stage",
            security_partition_id="project-stage",
            client_token="idem-stage-1",
            file_bytes=b"# Architecture\n\nVector indexing pipeline details.\n",
            original_filename="arch.md",
            content_type="text/markdown",
            logical_source_id="be-doc-stage",
            job_queue=fake_queue,
        )
        initial_status = await get_document_version_status(
            session,
            project_id="project-stage",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert initial_status.current_stage == "RECEIVE"
        assert initial_status.progress == 5
        assert initial_status.document_status == "pending"

        doc = await session.get(Document, upload_res.document_id)
        source = await session.get(Source, doc.source_id)
        job = await session.scalar(select(Job).where(Job.document_id == doc.id))
        job.status = JobStatus.RUNNING
        await session.commit()

        async def wrapped_parse(sess, ver_id, content, **kwargs):
            nonlocal parse_calls
            parse_calls += 1
            st = await get_document_version_status(
                sess,
                project_id="project-stage",
                document_id=upload_res.document_id,
                version_no=1,
            )
            observed_stages.append((st.current_stage, st.progress, st.document_status or ""))
            return await orig_parse(sess, ver_id, content, **kwargs)

        async def wrapped_dedup(sess, **kwargs):
            nonlocal dedup_calls
            dedup_calls += 1
            st = await get_document_version_status(
                sess,
                project_id="project-stage",
                document_id=upload_res.document_id,
                version_no=1,
            )
            observed_stages.append((st.current_stage, st.progress, st.document_status or ""))
            result = await orig_dedup(sess, **kwargs)
            # Trigger cooperative pause right after DEDUP_TEMPORAL completes (before INDEX_SEARCH)
            async with ingestion_db() as ctrl_sess:
                ctrl_source = await ctrl_sess.get(Source, source.id)
                await pause_document(ctrl_sess, ctrl_source, doc.id)
            return result

        async def wrapped_index(sess, *, document_version, **kwargs):
            st = await get_document_version_status(
                sess,
                project_id="project-stage",
                document_id=upload_res.document_id,
                version_no=1,
            )
            observed_stages.append((st.current_stage, st.progress, st.document_status or ""))
            document_version.search_status = "SEARCH_READY"
            document_version.search_ready_at = datetime.now(UTC)
            return [SimpleNamespace(id="unit-1")]

        monkeypatch.setattr(canonical_service, "parse_and_persist_document_content", wrapped_parse)
        monkeypatch.setattr(dedup_and_temporal_service, "run_dedup_and_temporal_stage", wrapped_dedup)
        monkeypatch.setattr(search_index_service, "run_search_indexing_stage", wrapped_index)

        manager = SimpleNamespace(
            get_sag_embedding=AsyncMock(return_value=None),
            process_document=AsyncMock(side_effect=AssertionError("Legacy extractor should not run")),
        )

        with pytest.raises(JobPaused):
            await tasks._process_document_unlocked(session, job, engine_manager=manager, job_queue=fake_queue)

        paused_status = await get_document_version_status(
            session,
            project_id="project-stage",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert paused_status.current_stage == "PAUSED"
        assert paused_status.document_status == "paused"
        assert paused_status.progress == 65

        # Resume (Continue): must skip PARSE and DEDUP_TEMPORAL and proceed straight to INDEX_SEARCH (85%) -> COMPLETE (100%)
        resumed_job = await resume_document(session, source, doc.id, job_queue=fake_queue)
        resumed_job.status = JobStatus.RUNNING
        await session.commit()

        await tasks._process_document_unlocked(session, resumed_job, engine_manager=manager, job_queue=fake_queue)
        resumed_job.status = JobStatus.SUCCEEDED
        await session.commit()

        assert parse_calls == 1
        assert dedup_calls == 1
        assert observed_stages == [
            ("PARSE", 35, "loading"),
            ("DEDUP_TEMPORAL", 65, "extracting"),
            ("INDEX_SEARCH", 85, "extracting"),
        ]

        ready_status = await get_document_version_status(
            session,
            project_id="project-stage",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert ready_status.current_stage == "COMPLETE"
        assert ready_status.progress == 100
        assert ready_status.search_ready is True
        assert ready_status.knowledge_ready is False
        assert ready_status.document_status == "ready"

        from sag_api.db.models.routing_rag import DocumentVersion

        ver_row = await session.get(DocumentVersion, upload_res.version_id)
        assert ver_row is not None
        ver_row.knowledge_status = "DATA_READY"
        await session.commit()

        data_ready_status = await get_document_version_status(
            session,
            project_id="project-stage",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert data_ready_status.knowledge_status == "DATA_READY"
        assert data_ready_status.knowledge_ready is True

        # Reprocess from READY: status immediately reflects queued RECEIVE · 5%
        await reprocess_document(session, source, doc.id, job_queue=fake_queue)
        reprocess_status = await get_document_version_status(
            session,
            project_id="project-stage",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert reprocess_status.current_stage == "RECEIVE"
        assert reprocess_status.progress == 0
        assert reprocess_status.search_ready is False
        assert reprocess_status.knowledge_ready is False
        assert reprocess_status.knowledge_status == "NOT_STARTED"
        assert reprocess_status.document_status == "pending"



@pytest.mark.asyncio
async def test_get_document_version_chunks_orders_units_and_enforces_partition_acl(
    ingestion_db,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extracted chunks endpoint returns ordered SearchUnits, falls back to CanonicalBlocks before indexing, and enforces ACLs."""
    from sag_api.core.config import settings
    from sag_api.core.errors import ForbiddenError
    from sag_api.core.identity import VerifiedPrincipal
    from sag_api.db.models.routing_rag import SearchUnit
    from sag_api.services.canonical_service import parse_and_persist_document_content
    from sag_api.services.document_service import (
        get_document_version_chunks,
        handle_document_upload,
        revoke_project_document,
    )

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))

    markdown_body = (
        "# System Architecture\n\n"
        "Multi-tenant RBAC boundary isolates project data across Cloudflare R2 and PostgreSQL.\n\n"
        "## Sharding Strategy\n\n"
        "| Shard | Region | Capacity |\n"
        "| --- | --- | --- |\n"
        "| S1 | APAC | 10 TB |\n"
    )

    async with ingestion_db() as session:
        upload_res = await handle_document_upload(
            session,
            tenant_id="tenant-chunks",
            project_id="project-chunks",
            owner_id="user-1",
            security_partition_id="project-chunks:default",
            client_token="chunks-test-1",
            file_bytes=markdown_body.encode("utf-8"),
            original_filename="architecture-v2.md",
            content_type="text/markdown",
            logical_source_id="continuum:project_document:doc-chunks-1",
        )

        # Before parsing: EMPTY
        empty_res = await get_document_version_chunks(
            session,
            project_id="project-chunks",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert empty_res.source_stage == "EMPTY"
        assert empty_res.total_chunks == 0
        assert empty_res.items == []

        # After PARSE (before SearchUnit minting): returns CANONICAL_BLOCKS
        blocks = await parse_and_persist_document_content(
            session,
            upload_res.version_id,
            markdown_body,
            page_from=1,
            page_to=2,
            document_title="System Architecture",
        )
        await session.commit()
        assert len(blocks) >= 2

        parsed_res = await get_document_version_chunks(
            session,
            project_id="project-chunks",
            document_id=upload_res.document_id,
            version_no=1,
        )
        assert parsed_res.source_stage == "CANONICAL_BLOCKS"
        assert parsed_res.total_chunks == len(blocks)
        assert "Multi-tenant RBAC" in "\n".join(item.content for item in parsed_res.items)

        # After SearchUnit minting: returns SEARCH_UNITS in reading order + pagination
        unit_a = SearchUnit(
            id="su-first",
            document_version_id=upload_res.version_id,
            block_from_id=blocks[0].id,
            block_to_id=blocks[min(1, len(blocks) - 1)].id,
            security_partition_id="project-chunks:default",
            content_hash="hash-a",
            token_count=42,
            page_from=1,
            page_to=1,
            section_path="System Architecture",
        )
        session.add(unit_a)
        if len(blocks) > 2:
            unit_b = SearchUnit(
                id="su-second",
                document_version_id=upload_res.version_id,
                block_from_id=blocks[2].id,
                block_to_id=blocks[-1].id,
                security_partition_id="project-chunks:default",
                content_hash="hash-b",
                token_count=35,
                page_from=2,
                page_to=2,
                section_path="System Architecture > Sharding Strategy",
            )
            session.add(unit_b)
        await session.commit()

        authorized = VerifiedPrincipal(
            user_id="user-1",
            tenant_id="tenant-chunks",
            allowed_projects=frozenset({"project-chunks"}),
            allowed_partitions=frozenset({"project-chunks:default"}),
        )
        indexed_res = await get_document_version_chunks(
            session,
            project_id="project-chunks",
            document_id=upload_res.document_id,
            version_no=1,
            principal=authorized,
        )
        assert indexed_res.source_stage == "SEARCH_UNITS"
        assert indexed_res.total_chunks >= 1
        assert indexed_res.items[0].ordinal == 1
        assert indexed_res.items[0].chunk_id == "su-first"
        assert indexed_res.items[0].token_count == 42

        # Unauthorized partition principal is blocked fail-closed
        wrong_partition = VerifiedPrincipal(
            user_id="user-2",
            tenant_id="tenant-chunks",
            allowed_projects=frozenset({"project-chunks"}),
            allowed_partitions=frozenset({"project-chunks:restricted"}),
        )
        with pytest.raises(ForbiddenError):
            await get_document_version_chunks(
                session,
                project_id="project-chunks",
                document_id=upload_res.document_id,
                version_no=1,
                principal=wrong_partition,
            )

        # Revoked document returns EMPTY
        await revoke_project_document(
            session,
            tenant_id="tenant-chunks",
            project_id="project-chunks",
            document_id=upload_res.document_id,
            reason="REVOKED_BY_LEADER",
        )
        revoked_res = await get_document_version_chunks(
            session,
            project_id="project-chunks",
            document_id=upload_res.document_id,
            version_no=1,
            principal=authorized,
        )
        assert revoked_res.source_stage == "EMPTY"
        assert revoked_res.total_chunks == 0
        assert revoked_res.items == []



@pytest.mark.asyncio
async def test_revoked_resync_with_same_idempotency_key_and_project_source_control_resolution(
    ingestion_db,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-syncing a REVOKED document with the same client_token allocates v2, and project control routes resolve project sources without 403."""
    from sag_api.api.v1.documents import _resolve_project_document_with_source
    from sag_api.core.config import settings
    from sag_api.core.principal_assertion import VerifiedPrincipal as BffVerifiedPrincipal
    from sag_api.services.document_service import (
        handle_document_object_ingest,
        handle_document_upload,
        revoke_project_document,
    )

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "object_ingest_allowed_hosts", ["r2.example.internal"])

    async with ingestion_db() as session:
        first = await handle_document_upload(
            session,
            tenant_id="tenant-resync",
            project_id="project-resync",
            owner_id="leader-1",
            security_partition_id="project-resync",
            client_token="continuum:project_document:doc-9:v1:abcd1234",
            file_bytes=b"# Spec\n\nSame content re-indexed after detach.\n",
            original_filename="spec.md",
            content_type="text/markdown",
            logical_source_id="continuum:project_document:doc-9",
        )
        assert first.version_no == 1
        assert first.is_duplicate is False

        # Verify project-scoped control route helper resolves auto-provisioned project Source without 403
        bff_principal = BffVerifiedPrincipal(
            subject="leader-1",
            organization_id="tenant-resync",
            allowed_project_ids=frozenset({"project-resync"}),
            issuer="continuum-bff",
            key_id="kid-1",
            token_id="jti-1",
            issued_at=1_700_000_000,
            expires_at=1_700_000_900,
            tenant_id="tenant-resync",
            allowed_partition_ids=frozenset({"project-resync"}),
        )
        resolved_doc, resolved_source = await _resolve_project_document_with_source(
            session,
            principal=bff_principal,
            project_id="project-resync",
            document_id=first.document_id,
        )
        assert resolved_doc.id == first.document_id
        assert resolved_source.id == resolved_doc.source_id

        # Revoke v1 -> 0% REVOKED
        rev = await revoke_project_document(
            session,
            tenant_id="tenant-resync",
            project_id="project-resync",
            document_id=first.document_id,
            reason="MANUAL_REVOKE",
        )
        assert rev.status == "REVOKED"

        # Re-sync with the EXACT SAME client_token -> must allocate v2 (not return REVOKED duplicate!)
        resynced = await handle_document_upload(
            session,
            tenant_id="tenant-resync",
            project_id="project-resync",
            owner_id="leader-1",
            security_partition_id="project-resync",
            client_token="continuum:project_document:doc-9:v1:abcd1234",
            file_bytes=b"# Spec\n\nSame content re-indexed after detach.\n",
            original_filename="spec.md",
            content_type="text/markdown",
            logical_source_id="continuum:project_document:doc-9",
        )
        assert resynced.version_no == 2
        assert resynced.is_duplicate is False
        assert resynced.status == "RECEIVED"
        assert resynced.search_status == "PENDING"

        # Also verify Direct-to-R2 object ingest path with same client_token after revoke
        sha = "a" * 64
        obj_v1 = await handle_document_object_ingest(
            session,
            tenant_id="tenant-resync",
            project_id="project-resync",
            owner_id="leader-1",
            security_partition_id="project-resync",
            client_token="continuum:project_document:doc-r2:v1:aaaa1111",
            source_url="https://r2.example.internal/bucket/doc.pdf?X-Amz-Signature=1",
            original_filename="doc.pdf",
            expected_sha256=sha,
            expected_size_bytes=2048,
            content_type="application/pdf",
            logical_source_id="continuum:project_document:doc-r2",
        )
        assert obj_v1.version_no == 1
        await revoke_project_document(
            session,
            tenant_id="tenant-resync",
            project_id="project-resync",
            document_id=obj_v1.document_id,
            reason="MANUAL_REVOKE",
        )
        obj_v2 = await handle_document_object_ingest(
            session,
            tenant_id="tenant-resync",
            project_id="project-resync",
            owner_id="leader-1",
            security_partition_id="project-resync",
            client_token="continuum:project_document:doc-r2:v1:aaaa1111",
            source_url="https://r2.example.internal/bucket/doc.pdf?X-Amz-Signature=2",
            original_filename="doc.pdf",
            expected_sha256=sha,
            expected_size_bytes=2048,
            content_type="application/pdf",
            logical_source_id="continuum:project_document:doc-r2",
        )
        assert obj_v2.version_no == 2
        assert obj_v2.is_duplicate is False
        assert obj_v2.status == "RECEIVED"
        assert obj_v2.search_status == "PENDING"


@pytest.mark.asyncio
async def test_reprocess_ready_project_document_completes_without_legacy_engine_schema(
    ingestion_db,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full worker execution of REPROCESS on a READY project document skips legacy zleap-sag cleanup and reaches 100% SEARCH_READY."""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy import select

    from sag_api.db.models import Document, Job, Source
    from sag_api.enums import JobStatus, JobType
    from sag_api.jobs import tasks
    from sag_api.services import search_index_service
    from sag_api.services.document_service import (
        get_document_version_status,
        handle_document_upload,
        reprocess_document,
    )

    monkeypatch.setattr(tasks.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(tasks.settings, "document_event_extraction_enabled", False)
    monkeypatch.setattr(tasks.settings, "object_ingest_allowed_hosts", ["r2.example.internal"])

    fake_queue = SimpleNamespace(
        enqueue=AsyncMock(),
        begin_source_maintenance=lambda *_args, **_kwargs: None,
        source_maintenance_requested=lambda *_args, **_kwargs: False,
        source_stop_requested=lambda *_args, **_kwargs: False,
    )

    async def fake_index(_sess, *, document_version, **_kwargs):
        document_version.search_status = "SEARCH_READY"
        document_version.search_ready_at = datetime.now(UTC)
        return [SimpleNamespace(id="unit-reprocessed-1")]

    monkeypatch.setattr(search_index_service, "run_search_indexing_stage", fake_index)

    manager = SimpleNamespace(
        get_sag_embedding=AsyncMock(return_value=None),
        process_document=AsyncMock(side_effect=AssertionError("Legacy extractor should not run")),
        delete_document_data=AsyncMock(side_effect=AssertionError("Legacy delete_document_data must not run for Phase 2-only project documents")),
    )

    async with ingestion_db() as session:
        uploaded = await handle_document_upload(
            session,
            tenant_id="tenant-reproc",
            project_id="project-reproc",
            owner_id="leader-1",
            security_partition_id="project-reproc",
            client_token="continuum:project_document:reproc-1:v1:1234",
            file_bytes=b"# Architecture\n\nReprocess worker end-to-end verification.\n",
            original_filename="arch.md",
            content_type="text/markdown",
            logical_source_id="continuum:project_document:reproc-1",
        )
        doc = await session.get(Document, uploaded.document_id)
        source = await session.get(Source, doc.source_id)
        initial_job = await session.scalar(select(Job).where(Job.document_id == doc.id))
        initial_job.status = JobStatus.RUNNING
        await session.commit()

        await tasks._process_document_unlocked(session, initial_job, engine_manager=manager, job_queue=fake_queue)
        initial_job.status = JobStatus.SUCCEEDED
        await session.commit()

        # Trigger REPROCESS from READY (100%)
        reproc_job = await reprocess_document(
            session,
            source,
            doc.id,
            job_queue=fake_queue,
            remote_source_url="https://r2.example.internal/bucket/arch.md?X-Amz-Signature=fresh",
            original_filename="arch.md",
        )
        assert reproc_job.type == JobType.REPROCESS_DOCUMENT
        reproc_job.status = JobStatus.RUNNING
        await session.commit()

        await tasks._reprocess_document_task_unlocked(session, reproc_job, engine_manager=manager, job_queue=fake_queue)
        reproc_job.status = JobStatus.SUCCEEDED
        await session.commit()
        assert "remote_source_url" not in (reproc_job.payload or {})

        proc_job_id = (reproc_job.payload or {}).get("process_job_id")
        assert proc_job_id
        proc_job = await session.get(Job, proc_job_id)
        assert proc_job is not None
        assert proc_job.type == JobType.PROCESS_DOCUMENT
        proc_job.status = JobStatus.RUNNING
        await session.commit()

        await tasks._process_document_unlocked(session, proc_job, engine_manager=manager, job_queue=fake_queue)
        proc_job.status = JobStatus.SUCCEEDED
        await session.commit()

        final_status = await get_document_version_status(
            session,
            project_id="project-reproc",
            document_id=doc.id,
            version_no=1,
        )
        assert final_status.progress == 100
        assert final_status.search_ready is True
        assert final_status.current_stage == "COMPLETE"
        assert final_status.document_status == "ready"


@pytest.mark.asyncio
async def test_project_document_readiness_batch_requires_confirmed_project_mapping(ingestion_db):
    from sag_api.core.identity import VerifiedPrincipal
    from sag_api.schemas.routing_rag import ProjectDocumentVersionRef
    from sag_api.services.document_service import get_project_document_readiness_batch

    async with ingestion_db() as session:
        ready_document = await session.get(Document, "document")
        ready_document.status = DocumentStatus.READY
        ready_document.chunk_count = 8
        ready_version = await session.get(DocumentVersion, "version")
        ready_version.status = "READY"
        ready_version.search_status = "SEARCH_READY"
        ready_version.search_ready_at = datetime.now(UTC)

        session.add(Source(id="pending-source", name="Pending", sag_source_config_id="pending-config"))
        await session.flush()
        session.add(
            SourceProjectMapping(
                source_id="pending-source",
                organization_id="tenant",
                project_id="project",
                state="PENDING",
            )
        )
        session.add(
            Document(
                id="pending-document",
                source_id="pending-source",
                tenant_id="tenant",
                project_id="project",
                filename="pending.md",
                storage_path="/tmp/pending.md",
                status=DocumentStatus.READY,
                chunk_count=4,
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id="pending-version",
                document_id="pending-document",
                version_no=1,
                file_hash="a" * 64,
                status="READY",
                search_status="SEARCH_READY",
                search_ready_at=datetime.now(UTC),
                metadata_json={"security_partition_id": "project"},
            )
        )
        await session.commit()

        principal = VerifiedPrincipal(
            user_id="user",
            tenant_id="tenant",
            allowed_projects=frozenset({"project"}),
            allowed_partitions=frozenset({"project"}),
        )
        result = await get_project_document_readiness_batch(
            session,
            project_id="project",
            documents=[
                ProjectDocumentVersionRef(document_id="document", version_no=1),
                ProjectDocumentVersionRef(document_id="pending-document", version_no=1),
                ProjectDocumentVersionRef(document_id="missing-document", version_no=1),
            ],
            principal=principal,
        )

    assert result.items[0].found is True
    assert result.items[0].search_ready is True
    assert result.items[0].chunk_count == 8
    assert result.items[1].found is False
    assert result.items[1].search_ready is False
    assert result.items[2].found is False
    assert result.items[2].search_ready is False


@pytest.mark.asyncio
async def test_project_document_readiness_batch_route_enforces_bff_project_scope(ingestion_db):
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from sag_api.api.v1.documents import project_router
    from sag_api.core.db import get_session
    from sag_api.core.errors import ApiError
    from sag_api.core.principal_assertion import require_project_bff_principal

    async with ingestion_db() as session:
        app = FastAPI()
        app.include_router(project_router)

        @app.exception_handler(ApiError)
        async def handle_api_error(request, exc):
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.to_envelope(),
            )

        principal = SimpleNamespace(
            subject="user",
            tenant_id="tenant",
            allowed_project_ids=frozenset({"project"}),
            allowed_partition_ids=frozenset({"project"}),
        )
        app.dependency_overrides[require_project_bff_principal] = lambda: principal
        app.dependency_overrides[get_session] = lambda: session

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/projects/project/documents/readiness",
                json={"documents": [{"document_id": "missing", "version_no": 1}]},
            )
            denied = await client.post(
                "/projects/other/documents/readiness",
                json={"documents": [{"document_id": "missing", "version_no": 1}]},
            )
            duplicate = await client.post(
                "/projects/project/documents/readiness",
                json={
                    "documents": [
                        {"document_id": "missing", "version_no": 1},
                        {"document_id": "missing", "version_no": 1},
                    ]
                },
            )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "document_id": "missing",
            "version_no": 1,
            "found": False,
            "status": "NOT_FOUND",
            "search_status": "NOT_FOUND",
            "search_ready": False,
            "document_status": None,
            "chunk_count": 0,
        }
    ]
    assert denied.status_code == 403
    assert duplicate.status_code == 422
