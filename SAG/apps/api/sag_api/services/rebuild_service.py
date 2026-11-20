"""Search Index Rebuild Service for Phase 2C.

Allows reconstructing Qdrant collections directly from PostgreSQL source of truth (search_units).
Guarantees disaster recovery, migration readiness, and idempotent re-indexing.
"""

from __future__ import annotations

from typing import Any
import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.logging import get_logger
from sag_api.db.models.document import Document
from sag_api.db.models.routing_rag import DocumentVersion, IngestionRun, SearchUnit
from sag_api.services.search_index_service import (
    ensure_qdrant_collection_and_indexes,
    run_search_indexing_stage,
)

log = get_logger("sag.rebuild_service")


async def rebuild_search_index_for_version(
    session: AsyncSession,
    *,
    document_version_id: str,
    project_id: str,
    security_partition_id: str,
    qdrant_client: httpx.AsyncClient,
    embedder: Any,
) -> int:
    """Rebuild Qdrant vector points for a specific document version from PostgreSQL."""
    doc_ver = await session.get(DocumentVersion, document_version_id)
    if not doc_ver:
        raise ValueError(f"DocumentVersion {document_version_id} not found")

    if not security_partition_id:
        raise ValueError(f"DocumentVersion {document_version_id} has no security_partition_id; fail closed")

    doc = await session.get(Document, doc_ver.document_id)
    if not doc or not doc.project_id or doc.project_id != project_id:
        raise ValueError(
            f"DocumentVersion {document_version_id} must have confirmed project_id matching {project_id}; fail closed"
        )
    tenant_id = doc.tenant_id if doc.tenant_id else "tenant_default"

    units = await run_search_indexing_stage(
        session,
        project_id=project_id,
        document_version=doc_ver,
        security_partition_id=security_partition_id,
        tenant_id=tenant_id,
        qdrant_client=qdrant_client,
        embedder=embedder,
    )
    return len(units)


async def rebuild_search_index_for_project(
    session: AsyncSession,
    *,
    project_id: str,
    qdrant_client: httpx.AsyncClient,
    embedder: Any,
) -> dict[str, Any]:
    """Rebuild all Qdrant vector points for an entire project from PostgreSQL.

    Guarantees strict tenant/project isolation: Document.project_id is the authoritative
    source of truth. Fails closed if any version has a conflicting IngestionRun project_id
    or lacks a confirmed security_partition_id.
    """
    collection_name = f"search_units_{project_id}"

    # Strictly scope query to DocumentVersions belonging to requested project_id via Document
    stmt = (
        select(DocumentVersion)
        .join(SearchUnit, SearchUnit.document_version_id == DocumentVersion.id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.project_id == project_id)
        .distinct()
    )
    versions = (await session.execute(stmt)).scalars().all()

    rebuilt_versions = 0
    total_units = 0
    for ver in versions:
        # Cross-project conflict check: IngestionRun project_id must not conflict with requested project_id
        conflicting_run = (
            await session.execute(
                select(IngestionRun.project_id)
                .where(IngestionRun.document_version_id == ver.id)
                .where(IngestionRun.project_id != project_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if conflicting_run:
            log.error(
                "Rebuild cross-project conflict: DocumentVersion %s belongs to project %s but has IngestionRun with project %s",
                ver.id,
                project_id,
                conflicting_run,
            )
            ver.search_status = "INDEX_FAILED"
            session.add(ver)
            await session.commit()
            raise ValueError(
                f"DocumentVersion {ver.id} has conflicting IngestionRun project_id {conflicting_run} vs expected {project_id}; rebuild failed closed"
            )

        # Determine security partition strictly; fail closed if unmapped
        sec_partition = (ver.metadata_json or {}).get("security_partition_id")
        if not sec_partition:
            su_sec = (
                await session.execute(
                    select(SearchUnit.security_partition_id)
                    .where(SearchUnit.document_version_id == ver.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            sec_partition = su_sec

        if not sec_partition:
            log.error(
                "Rebuild failed closed: DocumentVersion %s has no confirmed security_partition_id",
                ver.id,
            )
            ver.search_status = "INDEX_FAILED"
            session.add(ver)
            await session.commit()
            raise ValueError(
                f"DocumentVersion {ver.id} has unmapped security_partition_id; rebuild failed closed"
            )

        doc = await session.get(Document, ver.document_id)
        tenant_id = doc.tenant_id if doc and doc.tenant_id else "tenant_default"

        units = await run_search_indexing_stage(
            session,
            project_id=project_id,
            document_version=ver,
            security_partition_id=sec_partition,
            tenant_id=tenant_id,
            qdrant_client=qdrant_client,
            embedder=embedder,
        )
        rebuilt_versions += 1
        total_units += len(units)

    log.info(
        "Rebuilt Qdrant search index for project=%s versions=%d units=%d",
        project_id,
        rebuilt_versions,
        total_units,
    )

    return {
        "project_id": project_id,
        "collection_name": collection_name,
        "rebuilt_versions_count": rebuilt_versions,
        "total_search_units_rebuilt": total_units,
    }
