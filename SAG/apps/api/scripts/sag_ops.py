"""SAG Operations & Disaster Recovery CLI Tool (Phase 11 Hardening).

Provides unified operational access for:
1. Rebuilding Qdrant search index from PostgreSQL source of truth
2. Rolling back blue-green Knowledge Routing Tree candidates
3. Inspecting route decision cache and operational metrics

Strictly adheres to Ponytail principles: minimal code, stdlib argparse,
direct reuse of existing rebuild_service and tree_rollback_service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal
from sag_api.core.logging import get_logger

log = get_logger("sag_ops")


async def run_rebuild_qdrant(project_id: str) -> bool:
    """Rebuild Qdrant search index for an entire project from PostgreSQL."""
    from sag_api.sag.engine_manager import EngineManager
    from sag_api.services.rebuild_service import rebuild_search_index_for_project

    if not project_id or not project_id.strip():
        print(json.dumps({"status": "error", "message": "project_id cannot be empty"}), file=sys.stderr)
        return False

    log.info("Starting Qdrant search index rebuild for project_id=%s", project_id)
    engine_manager = EngineManager(settings)
    client = await engine_manager.get_search_unit_qdrant_client()
    embedder = await engine_manager.get_sag_embedding()

    try:
        async with SessionLocal() as session:
            result = await rebuild_search_index_for_project(
                session,
                project_id=project_id,
                qdrant_client=client,
                embedder=embedder,
            )
            print(json.dumps({"status": "success", "result": result}, indent=2))
            return True
    except Exception as error:
        log.error("Rebuild failed: %s", error)
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        return False
    finally:
        if hasattr(client, "aclose"):
            await client.aclose()


async def run_rollback_tree(project_id: str, target_tree_version: str) -> bool:
    """Rollback blue-green routing tree candidate for a project."""
    from sag_api.sag.engine_manager import EngineManager
    from sag_api.services.tree_rollback_service import rollback_tree_candidate

    if not project_id or not project_id.strip():
        print(json.dumps({"status": "error", "message": "project_id cannot be empty"}), file=sys.stderr)
        return False
    if not target_tree_version or not target_tree_version.strip():
        print(json.dumps({"status": "error", "message": "target_tree_version cannot be empty"}), file=sys.stderr)
        return False

    log.info(
        "Executing tree rollback for project_id=%s target_version=%s",
        project_id,
        target_tree_version or "auto-previous",
    )
    engine_manager = EngineManager(settings)
    client = await engine_manager.get_search_unit_qdrant_client()

    try:
        result = await rollback_tree_candidate(
            project_id,
            target_tree_version=target_tree_version,
            qdrant_client=client,
        )
        data = {
            "status": "success",
            "project_id": result.project_id,
            "active_slot": result.active_slot,
            "active_tree_version": result.active_tree_version,
            "active_search_epoch": result.active_search_epoch,
            "retained_previous_tree_version": result.retained_previous_tree_version,
        }
        print(json.dumps(data, indent=2))
        return True
    except Exception as error:
        log.error("Tree rollback failed: %s", error)
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        return False
    finally:
        if hasattr(client, "aclose"):
            await client.aclose()


def run_cache_status() -> None:
    """Display status of route decision cache."""
    from sag_api.services.route_cache_service import route_decision_cache

    status = {
        "scope": "cli_process",
        "enabled": settings.search_route_decision_cache_enabled or route_decision_cache.enabled,
        "current_entries": len(route_decision_cache),
        "max_entries": route_decision_cache.max_entries,
    }
    print(json.dumps(status, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="SAG Operations & Disaster Recovery CLI Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rebuild_parser = subparsers.add_parser("rebuild-qdrant", help="Rebuild Qdrant search index from PostgreSQL")
    rebuild_parser.add_argument("--project-id", required=True, help="Target project ID")

    rollback_parser = subparsers.add_parser(
        "rollback-tree", help="Rollback active routing tree to retained previous version"
    )
    rollback_parser.add_argument("--project-id", required=True, help="Target project ID")
    rollback_parser.add_argument("--target-version", required=True, help="Exact retained tree version to restore")

    subparsers.add_parser("cache-status", help="Inspect this CLI process's in-memory route decision cache")

    args = parser.parse_args()

    success = True
    if args.command == "rebuild-qdrant":
        success = asyncio.run(run_rebuild_qdrant(args.project_id))
    elif args.command == "rollback-tree":
        success = asyncio.run(run_rollback_tree(args.project_id, args.target_version))
    elif args.command == "cache-status":
        run_cache_status()

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
