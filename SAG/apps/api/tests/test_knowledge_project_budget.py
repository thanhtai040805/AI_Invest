from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.db.base import Base
from sag_api.db.models import Document, DocumentVersion, KnowledgeJob, Source
from sag_api.jobs.knowledge import QueuePolicy, claim_job, enqueue


@pytest_asyncio.fixture
async def foundation_queue_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Source(id="source", name="Source", sag_source_config_id="queue-test"))
        await session.flush()
        for index, project in enumerate(("project-a", "project-a", "project-b"), 1):
            session.add(
                Document(
                    id=f"d{index}",
                    source_id="source",
                    tenant_id="tenant",
                    project_id=project,
                    filename="notes.md",
                    storage_path="unused",
                )
            )
            await session.flush()
            session.add(
                DocumentVersion(
                    id=f"v{index}",
                    document_id=f"d{index}",
                    version_no=1,
                    file_hash="hash",
                )
            )
            await session.flush()
            session.add(
                KnowledgeJob(
                    id=f"j{index}",
                    document_version_id=f"v{index}",
                    tenant_id="tenant",
                    kind="FOUNDATION",
                    input_checksum="input",
                    priority=4 - index,
                    available_at=datetime.now(UTC),
                    created_at=datetime.now(UTC),
                )
            )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_rejects_removed_e2_and_only_admits_foundation(foundation_queue_db):
    policy = QueuePolicy()
    async with foundation_queue_db() as session:
        rejected = await enqueue(session, "v1", "tenant", "E2", "input-e2", policy, unit_id="u1")
        assert rejected is None
        admitted = await enqueue(session, "v1", "tenant", "FOUNDATION", "input-f", policy)
        assert admitted is not None
        assert admitted.kind == "FOUNDATION"
        assert admitted.reserved_tokens == 0


@pytest.mark.asyncio
async def test_claim_job_orders_foundation_by_priority_and_ignores_legacy_e2(foundation_queue_db):
    policy = QueuePolicy(concurrency=4)
    now = datetime.now(UTC)
    async with foundation_queue_db() as session:
        session.add(
            KnowledgeJob(
                id="legacy-e2",
                document_version_id="v1",
                tenant_id="tenant",
                kind="E2",
                input_checksum="input",
                priority=999,
                available_at=now,
                created_at=now,
            )
        )
        await session.flush()
        for expected in ("j1", "j2", "j3"):
            job = await claim_job(session, policy, now=now)
            assert job is not None
            assert job.id == expected
            assert job.kind == "FOUNDATION"
            assert job.reserved_tokens == 0
            job.status = "SUCCEEDED"
            await session.flush()
        assert await claim_job(session, policy, now=now) is None


@pytest.mark.asyncio
async def test_lease_expiry_recovers_running_foundation_job(foundation_queue_db):
    policy = QueuePolicy(max_attempts=3)
    now = datetime.now(UTC)
    async with foundation_queue_db() as session:
        first = await claim_job(session, policy, now=now, foundation_timeout_seconds=30)
        assert first is not None and first.id == "j1"
        expired_time = now + timedelta(seconds=120)
        reclaimed = await claim_job(session, policy, now=expired_time, foundation_timeout_seconds=30)
        assert reclaimed is not None and reclaimed.id == "j1"
        assert reclaimed.attempts == 2


@pytest.mark.asyncio
async def test_scope_mismatch_fails_closed_before_execution(foundation_queue_db):
    async with foundation_queue_db() as session:
        job = await session.get(KnowledgeJob, "j1")
        job.tenant_id = "wrong-tenant"
        await session.flush()
        claimed = await claim_job(session, QueuePolicy())
        assert claimed is not None and claimed.id == "j2"
        assert job.status == "FAILED"
        assert job.error_code == "INVALID_PROJECT_SCOPE"
        assert job.attempts == 0
