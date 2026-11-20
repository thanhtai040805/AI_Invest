"""Inventory and apply owner-approved Project -> Source mapping changes."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, inspect, select

from sag_api.db.models import Document, Source, SourceProjectMapping

SOURCE_LOOKUP_BATCH_SIZE = 500


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory", help="Print Sources and their current mapping state as CSV")
    apply = commands.add_parser("apply", help="Validate an owner-approved mapping CSV; writes only with --apply")
    apply.add_argument("csv_path", type=Path)
    apply.add_argument("--approved-by", required=True)
    apply.add_argument("--apply", action="store_true", help="Commit the validated rows; default is dry-run")
    revoke = commands.add_parser("revoke", help="Revoke a current mapping; writes only with --apply")
    revoke.add_argument("source_id")
    revoke.add_argument("--approved-by", required=True)
    revoke.add_argument("--approval-ref", required=True)
    revoke.add_argument("--apply", action="store_true", help="Commit the revocation; default is dry-run")
    return parser


async def _latest_mappings(session) -> dict[str, SourceProjectMapping]:
    rows = await session.execute(
        select(SourceProjectMapping).order_by(
            SourceProjectMapping.source_id,
            SourceProjectMapping.mapping_version.desc(),
            SourceProjectMapping.created_at.desc(),
        )
    )
    result: dict[str, SourceProjectMapping] = {}
    for row in rows.scalars().all():
        result.setdefault(row.source_id, row)
    return result


async def _inventory(session) -> None:
    latest = await _latest_mappings(session)
    rows = await session.execute(
        select(Source, func.count(Document.id))
        .outerjoin(Document, Document.source_id == Source.id)
        .group_by(Source.id)
        .order_by(Source.id)
    )
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(
        [
            "source_id",
            "name",
            "source_status",
            "document_count",
            "chunk_count",
            "event_count",
            "mapping_state",
            "organization_id",
            "project_id",
            "mapping_version",
        ]
    )
    for source, document_count in rows.all():
        mapping = latest.get(source.id)
        writer.writerow(
            [
                source.id,
                source.name,
                getattr(source.status, "value", source.status),
                document_count,
                source.chunk_count,
                source.event_count,
                mapping.state if mapping else "UNMAPPED",
                mapping.organization_id if mapping else "",
                mapping.project_id if mapping else "",
                mapping.mapping_version if mapping else "",
            ]
        )


def _read_mapping_file(path: Path) -> tuple[list[dict[str, str]], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    required = {"source_id", "organization_id", "project_id", "approval_ref"}
    if not required.issubset(set(reader.fieldnames or [])):
        raise ValueError("CSV header must include source_id,organization_id,project_id,approval_ref")
    seen: set[str] = set()
    for line_number, raw_row in enumerate(reader, start=2):
        row = {key: (value or "") for key, value in raw_row.items() if key is not None}
        if any(value != value.strip() for value in row.values()):
            raise ValueError(f"line {line_number}: values must not contain leading/trailing whitespace")
        source_id = row.get("source_id", "")
        if not source_id or len(source_id) > 32 or source_id in seen:
            raise ValueError(f"line {line_number}: source_id is empty, too long, or duplicated")
        if not row.get("organization_id") or len(row["organization_id"]) > 256:
            raise ValueError(f"line {line_number}: invalid organization_id")
        if not row.get("project_id") or len(row["project_id"]) > 256:
            raise ValueError(f"line {line_number}: invalid project_id")
        if not row.get("approval_ref") or len(row["approval_ref"]) > 256:
            raise ValueError(f"line {line_number}: approval_ref is required and must be <= 256 chars")
        seen.add(source_id)
        rows.append(row)
    return rows, digest


async def _apply(session, rows: list[dict[str, str]], digest: str, approved_by: str, write: bool) -> None:
    if not approved_by.strip() or approved_by != approved_by.strip():
        raise ValueError("approved-by must be a non-empty canonical operator ID")
    source_ids = [row["source_id"] for row in rows]
    sources: dict[str, Source] = {}
    for start in range(0, len(source_ids), SOURCE_LOOKUP_BATCH_SIZE):
        batch = source_ids[start : start + SOURCE_LOOKUP_BATCH_SIZE]
        result = await session.execute(select(Source).where(Source.id.in_(batch)))
        sources.update({source.id: source for source in result.scalars().all()})
    if len(sources) != len(rows):
        raise ValueError("CSV contains one or more Source IDs that do not exist")
    current = await _latest_mappings(session)
    batch_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    changes: list[SourceProjectMapping] = []
    for row in rows:
        active = current.get(row["source_id"])
        if active and active.state == "CONFIRMED":
            if (
                active.organization_id == row["organization_id"]
                and active.project_id == row["project_id"]
                and active.approval_ref == row["approval_ref"]
            ):
                continue
            raise ValueError(f"Source {row['source_id']} already has a different confirmed mapping; revoke separately")
        if active and active.state == "PENDING":
            if active.organization_id != row["organization_id"] or active.project_id != row["project_id"]:
                raise ValueError(f"Source {row['source_id']} has a conflicting pending mapping")
            active.state = "CONFIRMED"
            active.confirmed_at = now
            active.confirmed_by = approved_by
            active.approval_ref = row["approval_ref"]
            active.batch_id = batch_id
            active.input_sha256 = digest
            changes.append(active)
            continue
        previous_version = active.mapping_version if active else 0
        changes.append(
            SourceProjectMapping(
                source_id=row["source_id"],
                organization_id=row["organization_id"],
                project_id=row["project_id"],
                state="CONFIRMED",
                mapping_version=previous_version + 1,
                confirmed_at=now,
                confirmed_by=approved_by,
                approval_ref=row["approval_ref"],
                batch_id=batch_id,
                input_sha256=digest,
            )
        )
    print(
        f"validated={len(rows)} changes={len(changes)} noops={len(rows) - len(changes)} "
        f"mode={'APPLY' if write else 'DRY_RUN'} batch_id={batch_id} sha256={digest}"
    )
    if write:
        session.add_all(changes)
        await session.commit()
    else:
        await session.rollback()


async def _revoke(session, source_id: str, approved_by: str, approval_ref: str, write: bool) -> None:
    if not approved_by.strip() or approved_by != approved_by.strip():
        raise ValueError("approved-by must be a non-empty canonical operator ID")
    if not approval_ref.strip() or approval_ref != approval_ref.strip() or len(approval_ref) > 256:
        raise ValueError("approval-ref must be a canonical non-empty value of at most 256 characters")
    current = (await _latest_mappings(session)).get(source_id)
    if current is None or current.state == "REVOKED":
        raise ValueError("Source has no active Project mapping")
    now = datetime.now(UTC)
    print(f"source_id={source_id} mapping_version={current.mapping_version + 1} mode={'APPLY' if write else 'DRY_RUN'}")
    if write:
        current.state = "REVOKED"
        current.mapping_version += 1
        current.revoked_at = now
        current.revoked_by = approved_by
        current.revocation_ref = approval_ref
        await session.commit()
    else:
        await session.rollback()


async def _run(args: argparse.Namespace) -> None:
    from sag_api.core.db import SessionLocal, engine

    async with engine.connect() as connection:
        has_mapping_table = await connection.run_sync(
            lambda sync: inspect(sync).has_table(SourceProjectMapping.__tablename__)
        )
    if not has_mapping_table:
        raise RuntimeError("source_project_mappings is missing; apply the reviewed ACL DDL before using this tool")
    async with SessionLocal() as session:
        if args.command == "inventory":
            await _inventory(session)
        elif args.command == "apply":
            if not args.csv_path.is_file():
                raise ValueError("Mapping CSV does not exist")
            rows, digest = _read_mapping_file(args.csv_path)
            await _apply(session, rows, digest, args.approved_by, args.apply)
        else:
            if not args.approved_by.strip() or not args.approval_ref.strip():
                raise ValueError("approved-by and approval-ref are required")
            await _revoke(session, args.source_id, args.approved_by, args.approval_ref, args.apply)


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
