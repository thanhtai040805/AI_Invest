"""Rebuild/verify inactive knowledge candidates, or benchmark checked-in source artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.config import settings
from sag_api.db.base import Base
from sag_api.db.models import (
    Document,
    DocumentVersion,
    KnowledgeJob,
    KnowledgeUnit,
    Source,
    SourceProjectMapping,
    SourceSnapshot,
)
from sag_api.enums import DocumentStatus
from sag_api.services.canonical_service import parse_and_persist_document_content
from sag_api.services.knowledge_candidate_service import (
    CorpusQuery,
    build_knowledge_candidate,
    verify_knowledge_candidate,
)
from sag_api.services.knowledge_service import KnowledgeConfig, rebuild_knowledge_version, stable_id, verify_graph
from sag_api.services.query_routing_service import RoutingPolicy
from sag_api.services.routing_tree_service import TreeBuildConfig

SAG_ROOT = Path(__file__).resolve().parents[3]


async def import_corpus(session, spec: dict) -> tuple[list[CorpusQuery], list[dict]]:
    tenant, project = spec["tenant_id"], spec["project_id"]
    source_by_path, versions, artifacts = {}, {}, []
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    for entry in spec["documents"]:
        path = (SAG_ROOT / entry["path"]).resolve()
        if not path.is_relative_to(SAG_ROOT):
            raise ValueError("Corpus artifacts must be under SAG")
        content = path.read_text(encoding="utf-8")
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        source_id = stable_id(tenant, project, entry["path"], "source").replace("-", "")
        source_by_path[entry["path"]] = source_id
        document_id = stable_id(source_id, "document").replace("-", "")
        version_id = stable_id(document_id, file_hash)
        if not await session.get(Source, source_id):
            session.add(Source(id=source_id, name=entry["path"], sag_source_config_id=source_id))
            await session.flush()
            session.add(
                SourceProjectMapping(
                    source_id=source_id,
                    organization_id=tenant,
                    project_id=project,
                    state="CONFIRMED",
                    confirmed_at=instant,
                    confirmed_by="offline-benchmark",
                    approval_ref="checked-in-corpus-fixture",
                )
            )
        if not await session.get(Document, document_id):
            session.add(
                Document(
                    id=document_id,
                    tenant_id=tenant,
                    project_id=project,
                    source_id=source_id,
                    filename=path.name,
                    storage_path=str(path),
                    status=DocumentStatus.READY,
                    is_active=True,
                )
            )
            await session.flush()
        version = await session.get(DocumentVersion, version_id)
        if not version:
            count = (
                await session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document_id))
            ).all()
            version = DocumentVersion(
                id=version_id,
                document_id=document_id,
                version_no=len(count) + 1,
                file_hash=file_hash,
                observed_at=instant,
                valid_from=instant,
                valid_to=datetime(9999, 12, 31, tzinfo=UTC),
                search_status="SEARCH_READY",
                status="SEARCH_READY",
                search_ready_at=instant,
                metadata_json={"security_partition_id": entry["partition_id"]},
            )
            session.add(version)
            await session.flush()
            session.add(
                SourceSnapshot(
                    id=stable_id(version_id, "snapshot"),
                    document_version_id=version_id,
                    storage_uri=path.as_uri(),
                    original_filename=path.name,
                    mime_type="text/markdown",
                    byte_size=path.stat().st_size,
                    checksum_sha256=file_hash,
                )
            )
            await parse_and_persist_document_content(session, version_id, content)
        # Source artifacts, not preconstructed KnowledgeUnit/edge fixtures, feed the producer.
        await rebuild_knowledge_version(session, version_id)
        versions[entry["path"]] = version_id
        artifacts.append({**entry, "sha256": file_hash, "source_id": source_id, "document_version_id": version_id})
    queries = []
    for gold in spec["queries"]:
        matches = (
            await session.scalars(
                select(KnowledgeUnit)
                .where(KnowledgeUnit.document_version_id == versions[gold["path"]], KnowledgeUnit.is_current.is_(True))
                .order_by(KnowledgeUnit.ordinal)
            )
        ).all()
        target = next((u for u in matches if gold["contains"] in u.text), None)
        if not target:
            raise ValueError("Gold corpus anchor missing: " + gold["query_id"])
        allowed = gold.get(
            "authorized_paths",
            [e["path"] for e in spec["documents"] if e["partition_id"] == target.security_partition_id],
        )
        queries.append(
            CorpusQuery(
                query_id=gold["query_id"],
                query=gold["query"],
                target_unit_id=target.id,
                partition_id=target.security_partition_id,
                authorized_source_ids=tuple(source_by_path[p] for p in allowed),
                k=spec["k"],
            )
        )
    return queries, artifacts


async def execute(args):
    options = {}
    database_url = settings.database_url
    if args.schema:
        if not re.fullmatch(r"knowledge_(?:benchmark|test)_[a-z0-9_]{1,40}", args.schema):
            raise ValueError("Use an isolated knowledge_benchmark_* or knowledge_test_* schema")
        bootstrap = create_async_engine(database_url)
        async with bootstrap.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{args.schema}"'))
        await bootstrap.dispose()
        options = {"connect_args": {"server_settings": {"search_path": args.schema}}}
    if args.command == "benchmark" and database_url.startswith("postgresql") and not args.schema:
        raise ValueError("PostgreSQL corpus benchmark requires --schema for isolation")
    engine = create_async_engine(database_url, **options)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if args.command == "benchmark":
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            if args.command == "verify":
                manifest = await verify_knowledge_candidate(session, args.tree_version)
            elif args.command == "retry":
                job = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == args.job_id).with_for_update())
                if not job or job.status not in {"FAILED", "EXPIRED", "RETRY"}:
                    raise ValueError("Retry requires an existing failed/expired job")
                job.status, job.attempts, job.available_at = "QUEUED", 0, datetime.now(UTC)
                job.created_at = job.available_at  # Explicit operator retry starts a fresh max-age window.
                job.error_code, job.lease_token, job.lease_until = None, None, None
                await session.commit()
                print(json.dumps({"job_id": job.id, "status": job.status}))
                return
            elif args.command == "rebuild":
                versions = (
                    await session.scalars(
                        select(DocumentVersion)
                        .join(Document)
                        .where(
                            Document.tenant_id == args.tenant,
                            Document.project_id == args.project,
                            Document.is_active.is_(True),
                            DocumentVersion.search_status.in_(("READY", "SEARCH_READY")),
                        )
                        .order_by(DocumentVersion.id)
                    )
                ).all()
                total = 0
                for version in versions:
                    total += len(await rebuild_knowledge_version(session, version.id))
                await session.commit()
                print(json.dumps({"version_count": len(versions), "unit_count": total}))
                return
            else:
                spec = json.loads(Path(args.input).read_text(encoding="utf-8"))
                artifacts = []
                if args.command == "benchmark":
                    queries, artifacts = await import_corpus(session, spec)
                    tenant, project = spec["tenant_id"], spec["project_id"]
                else:
                    queries = [CorpusQuery.model_validate(q) for q in spec["queries"]]
                    tenant, project = args.tenant, args.project
                manifest = await build_knowledge_candidate(
                    session,
                    tenant_id=tenant,
                    project_id=project,
                    queries=queries,
                    config=TreeBuildConfig(**spec["tree_config"]),
                    graph_config=KnowledgeConfig(**spec.get("graph_config", {})),
                    routing_policy=RoutingPolicy(**spec.get("routing_policy", {})),
                )
                await session.commit()
                # Read persisted rows in a new transaction, then rebuild and compare the same candidate.
                await verify_knowledge_candidate(session, manifest.tree_version)
                again = await build_knowledge_candidate(
                    session,
                    tenant_id=tenant,
                    project_id=project,
                    queries=list(reversed(queries)),
                    config=TreeBuildConfig(**spec["tree_config"]),
                    graph_config=KnowledgeConfig(**spec.get("graph_config", {})),
                    routing_policy=RoutingPolicy(**spec.get("routing_policy", {})),
                )
                assert again.tree_version == manifest.tree_version
                await session.commit()
            snapshot, producer = manifest.manifest_json["snapshot"], manifest.manifest_json["producer"]
            graph = await verify_graph(session, producer["graph_build_id"])
            report = {
                "tree_version": manifest.tree_version,
                "status": manifest.status,
                "checksum": manifest.checksum,
                "database_dialect": engine.dialect.name,
                "schema": args.schema,
                "node_count": manifest.node_count,
                "leaf_count": manifest.leaf_count,
                "source_artifacts": artifacts if args.command != "verify" else [],
                "evidence": {
                    "contract": manifest.manifest_json["contract"],
                    "unit_count": snapshot["unit_count"],
                    "edge_count": len(snapshot["edges"]),
                    "config": snapshot["config"],
                    "algorithm_versions": snapshot["algorithm_versions"],
                    "metrics": snapshot["quality_metrics"],
                    "quality_gates": snapshot["quality_gates"],
                    "graph_build_id": graph.id,
                    "graph_checksum": graph.checksum,
                    "graph_config": graph.manifest_json["config"],
                    "calibrations": graph.manifest_json["calibrations"],
                    "routing_policy": producer["routing_policy"],
                    "component_checksums": {k: v for k, v in producer.items() if k.endswith("_checksum")},
                    "queries": producer["benchmark_evidence"],
                },
            }
            if args.output:
                Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items() if k not in {"evidence", "source_artifacts"}}))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--output", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("benchmark", "candidate", "rebuild", "verify", "retry"):
        command = commands.add_parser(name)
        if name in {"benchmark", "candidate"}:
            command.add_argument("--input", required=True)
        if name in {"candidate", "rebuild"}:
            command.add_argument("--tenant", required=True)
            command.add_argument("--project", required=True)
        if name == "verify":
            command.add_argument("--tree-version", required=True)
        if name == "retry":
            command.add_argument("--job-id", required=True)
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
