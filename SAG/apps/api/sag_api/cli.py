from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SNAPSHOT_VERSION = 1

V2_TABLES = [
    "issuers",
    "document_assets",
    "documents",
    "document_tree_nodes",
    "evidence_spans",
    "entities",
    "entity_aliases",
    "entity_mentions",
    "facts",
    "relations",
    "embedding_chunks",
    "processing_runs",
    "assessment_runs",
    "review_queue",
]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sqlite_counts(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cur.fetchall()]
        counts: dict[str, int] = {}
        for table in tables:
            cur.execute(f'SELECT count(*) FROM "{table}"')
            counts[table] = int(cur.fetchone()[0])
        return counts
    finally:
        conn.close()


def _sqlite_tables(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        return {str(row[0]) for row in cur.fetchall()}
    finally:
        conn.close()


def _export_jsonl_tables(db_path: Path, out_dir: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    existing_tables = _sqlite_tables(db_path)
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    exported: dict[str, int] = {}
    try:
        for table in V2_TABLES:
            if table not in existing_tables:
                continue
            path = tables_dir / f"{table}.jsonl"
            count = 0
            with path.open("w", encoding="utf-8") as handle:
                for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                    handle.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
                    count += 1
            exported[table] = count
        return exported
    finally:
        conn.close()


def snapshot_export(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir).resolve()
    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "sag.db"
    objects_dir = data_dir / "objects"
    object_hashes = []
    if objects_dir.exists():
        for path in sorted(p for p in objects_dir.rglob("*") if p.is_file()):
            object_hashes.append(
                {
                    "path": str(path.relative_to(data_dir)).replace("\\", "/"),
                    "sha256": _sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    exported_tables = _export_jsonl_tables(db_path, out_dir)
    manifest = {
        "snapshot_version": SNAPSHOT_VERSION,
        "source_data_dir": str(data_dir),
        "sqlite": {
            "path": str(db_path),
            "exists": db_path.exists(),
            "sha256": _sha256_file(db_path) if db_path.exists() else None,
            "row_counts": _sqlite_counts(db_path),
        },
        "objects": object_hashes,
        "jsonl_tables": exported_tables,
        "vectors_exported": False,
        "notes": [
            "Legacy zleap/LanceDB events are not exported.",
            "Vectors should be regenerated unless compatible embedding metadata is present.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "EXPORTED", "manifest": str(out_dir / "manifest.json")}, ensure_ascii=False))


def snapshot_verify(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if manifest.get("snapshot_version") != SNAPSHOT_VERSION:
        errors.append("snapshot_version không được hỗ trợ")
    sqlite_info = manifest.get("sqlite") or {}
    db_path = Path(sqlite_info.get("path") or "")
    if sqlite_info.get("exists"):
        if not db_path.exists():
            errors.append("SQLite path không tồn tại")
        elif _sha256_file(db_path) != sqlite_info.get("sha256"):
            errors.append("SQLite sha256 không khớp")
    for obj in manifest.get("objects") or []:
        path = Path(manifest["source_data_dir"]) / obj["path"]
        if not path.exists():
            errors.append(f"Object thiếu: {obj['path']}")
        elif _sha256_file(path) != obj["sha256"]:
            errors.append(f"Object sha256 không khớp: {obj['path']}")
    if errors:
        print(json.dumps({"status": "INVALID", "errors": errors}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps({"status": "VALID", "manifest": str(manifest_path)}, ensure_ascii=False))


def snapshot_import(args: argparse.Namespace) -> None:
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not args.target:
        raise SystemExit("--target là bắt buộc")
    if not args.target.startswith(("postgresql://", "postgresql+asyncpg://")):
        raise SystemExit("target phải là PostgreSQL URL")
    snapshot_verify(argparse.Namespace(manifest=str(manifest_path)))
    report = asyncio.run(_snapshot_import_async(manifest_path, manifest, args.target, bool(args.reuse_compatible_vectors)))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def storage_gc(args: argparse.Namespace) -> None:
    root = Path(args.objects_dir).resolve()
    if not root.is_dir():
        raise SystemExit(f"object store không tồn tại: {root}")
    cutoff = time.time() - max(0, args.keep_days) * 86400
    candidates = [
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".pdf"} and path.stat().st_mtime < cutoff
    ]
    bytes_total = sum(path.stat().st_size for path in candidates)
    if args.apply:
        for path in candidates:
            path.unlink(missing_ok=True)
        for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
    print(json.dumps({
        "status": "CLEANED" if args.apply else "DRY_RUN",
        "root": str(root),
        "keep_days": args.keep_days,
        "files": len(candidates),
        "bytes": bytes_total,
    }, ensure_ascii=False))


async def _snapshot_import_async(
    manifest_path: Path,
    manifest: dict[str, Any],
    target: str,
    reuse_compatible_vectors: bool,
) -> dict[str, Any]:
    try:
        import asyncpg
    except ImportError as exc:
        raise SystemExit("snapshot-import cần optional dependency asyncpg") from exc
    dsn = target.replace("postgresql+asyncpg://", "postgresql://", 1)
    tables_dir = manifest_path.parent / "tables"
    if not tables_dir.exists():
        raise SystemExit("Snapshot không có tables/*.jsonl; chạy snapshot-export lại")
    conn = await asyncpg.connect(dsn)
    imported: dict[str, int] = {}
    skipped: dict[str, str] = {}
    try:
        await conn.execute("SET search_path TO sag, public")
        async with conn.transaction():
            existing = await conn.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'sag'
                """
            )
            target_tables = {row["table_name"] for row in existing}
            for table in V2_TABLES:
                path = tables_dir / f"{table}.jsonl"
                if not path.exists():
                    continue
                if table not in target_tables:
                    skipped[table] = "target_table_missing"
                    continue
                cols = await conn.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'sag' AND table_name = $1
                    ORDER BY ordinal_position
                    """,
                    table,
                )
                allowed = {row["column_name"] for row in cols}
                count = 0
                for line in path.read_text(encoding="utf-8").splitlines():
                    row = {k: _json_pg_value(k, v) for k, v in json.loads(line).items() if k in allowed}
                    if "id" not in row:
                        continue
                    names = list(row)
                    placeholders = ", ".join(f"${idx}" for idx in range(1, len(names) + 1))
                    columns = ", ".join(f'"{name}"' for name in names)
                    updates = ", ".join(
                        f'"{name}" = EXCLUDED."{name}"'
                        for name in names
                        if name not in {"id", "created_at"}
                    )
                    conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
                    await conn.execute(
                        f'INSERT INTO sag."{table}" ({columns}) VALUES ({placeholders}) ON CONFLICT (id) {conflict}',
                        *[row[name] for name in names],
                    )
                    count += 1
                imported[table] = count
    finally:
        await conn.close()
    return {
        "status": "IMPORTED",
        "target": target.split("@")[-1],
        "manifest": str(manifest_path),
        "imported": imported,
        "skipped": skipped,
        "vectors_reembed_required": not reuse_compatible_vectors,
    }


def _json_pg_value(column: str, value: Any) -> Any:
    if value is None:
        return None
    if column == "embedding_vector":
        return None
    if column.endswith("_json") or column in {"coverage", "embedding_json"}:
        return json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return value


def main() -> None:
    parser = argparse.ArgumentParser(prog="sagctl")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("snapshot-export")
    exp.add_argument("--data-dir", default=str(Path.cwd() / ".data"))
    exp.add_argument("--output", required=True)
    exp.set_defaults(func=snapshot_export)
    ver = sub.add_parser("snapshot-verify")
    ver.add_argument("--manifest", required=True)
    ver.set_defaults(func=snapshot_verify)
    imp = sub.add_parser("snapshot-import")
    imp.add_argument("--manifest", required=True)
    imp.add_argument("--target", required=True)
    imp.add_argument("--reuse-compatible-vectors", action="store_true")
    imp.set_defaults(func=snapshot_import)
    gc = sub.add_parser("storage-gc")
    gc.add_argument("--objects-dir", default=str(Path.cwd() / ".data" / "cache" / "uploads" / "objects"))
    gc.add_argument("--keep-days", type=int, default=1)
    gc.add_argument("--apply", action="store_true")
    gc.set_defaults(func=storage_gc)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
