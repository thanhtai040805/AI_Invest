"""Assign explicitly verified owners to legacy conversations and copy their images."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal
from sag_api.db.models import Agent, Message, Thread

_OWNER_KEY = re.compile(r"[0-9a-f]{64}\Z")
_ATTACHMENT_ID = re.compile(r"[0-9a-f]{32}\.(?:png|jpe?g|webp|gif)\Z")


def _mapping(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object mapping row IDs to owner keys")
    result: dict[str, str] = {}
    for row_id, owner_key in value.items():
        if (
            not isinstance(row_id, str)
            or not row_id
            or not isinstance(owner_key, str)
            or _OWNER_KEY.fullmatch(owner_key) is None
        ):
            raise ValueError(f"{name} contains an invalid row ID or owner key")
        result[row_id] = owner_key
    return result


def read_mapping(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("mapping file must contain a JSON object")
    return _mapping(data.get("agents", {}), "agents"), _mapping(data.get("threads", {}), "threads")


async def migrate(
    agents: dict[str, str],
    threads: dict[str, str],
    *,
    apply: bool,
    session_factory: async_sessionmaker = SessionLocal,
    upload_dir: str = settings.upload_dir,
) -> dict[str, int | str]:
    async with session_factory() as session, session.begin():
        agent_ids = set(agents)
        thread_ids = set(threads)
        agent_statement = select(Agent).where(Agent.id.in_(agent_ids))
        thread_statement = select(Thread).where(Thread.id.in_(thread_ids))
        if apply:
            agent_statement = agent_statement.with_for_update()
            thread_statement = thread_statement.with_for_update()
        agent_rows = list(
            (await session.scalars(agent_statement)).all()
        )
        thread_rows = list(
            (await session.scalars(thread_statement)).all()
        )
        agent_by_id = {agent.id: agent for agent in agent_rows}
        thread_by_id = {thread.id: thread for thread in thread_rows}
        if agent_ids - agent_by_id.keys() or thread_ids - thread_by_id.keys():
            raise ValueError("mapping references an unknown agent or thread")

        related_agent_ids = agent_ids | {thread.agent_id for thread in thread_rows}
        related_agent_statement = select(Agent).where(Agent.id.in_(related_agent_ids))
        if apply:
            related_agent_statement = related_agent_statement.with_for_update()
        related_agents = list((await session.scalars(related_agent_statement)).all())
        related_agent_by_id = {agent.id: agent for agent in related_agents}

        for agent_id, owner_key in agents.items():
            agent = agent_by_id[agent_id]
            if agent.is_default:
                raise ValueError("shared default agents cannot be assigned a private owner")
            if agent.owner_key not in (None, owner_key):
                raise ValueError(f"agent {agent_id} already has a different owner")
        for thread_id, owner_key in threads.items():
            thread = thread_by_id[thread_id]
            if thread.owner_key not in (None, owner_key):
                raise ValueError(f"thread {thread_id} already has a different owner")
            agent = related_agent_by_id[thread.agent_id]
            if not agent.is_default and agents.get(agent.id, agent.owner_key) != owner_key:
                raise ValueError(f"thread {thread_id} owner does not match its private agent")

        attachments_by_owner: dict[str, set[str]] = {}
        if thread_ids:
            messages = await session.scalars(select(Message).where(Message.thread_id.in_(thread_ids)))
            for message in messages:
                owner_key = threads[message.thread_id]
                for attachment in message.attachments or []:
                    attachment_id = attachment.get("id") if isinstance(attachment, dict) else None
                    if isinstance(attachment_id, str) and _ATTACHMENT_ID.fullmatch(attachment_id):
                        attachments_by_owner.setdefault(owner_key, set()).add(attachment_id)

        legacy_dir = Path(upload_dir) / "attachments"
        files_to_copy: list[tuple[Path, Path]] = []
        missing_files = 0
        for owner_key, attachment_ids in attachments_by_owner.items():
            for attachment_id in attachment_ids:
                source = legacy_dir / attachment_id
                target = legacy_dir / owner_key / attachment_id
                if target.is_file():
                    continue
                if source.is_file():
                    files_to_copy.append((source, target))
                else:
                    missing_files += 1

        null_agents = int(
            await session.scalar(select(func.count()).select_from(Agent).where(Agent.owner_key.is_(None))) or 0
        )
        null_threads = int(
            await session.scalar(select(func.count()).select_from(Thread).where(Thread.owner_key.is_(None))) or 0
        )
        agent_updates = sum(agent_by_id[row_id].owner_key is None for row_id in agents)
        thread_updates = sum(thread_by_id[row_id].owner_key is None for row_id in threads)
        report: dict[str, int | str] = {
            "status": "applied" if apply else "dry_run",
            "agents_to_assign": agent_updates,
            "threads_to_assign": thread_updates,
            "attachments_to_copy": len(files_to_copy),
            "missing_legacy_attachments": missing_files,
            "unmapped_agents_remaining": max(0, null_agents - agent_updates),
            "unmapped_threads_remaining": max(0, null_threads - thread_updates),
        }

        if apply:
            for source, target in files_to_copy:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            for agent_id, owner_key in agents.items():
                if agent_by_id[agent_id].owner_key is None:
                    await session.execute(
                        update(Agent).where(Agent.id == agent_id, Agent.owner_key.is_(None)).values(owner_key=owner_key)
                    )
            for thread_id, owner_key in threads.items():
                if thread_by_id[thread_id].owner_key is None:
                    await session.execute(
                        update(Thread)
                        .where(Thread.id == thread_id, Thread.owner_key.is_(None))
                        .values(owner_key=owner_key)
                    )
    return report


async def _run(mapping_path: Path, apply: bool) -> dict[str, int | str]:
    agents, threads = read_mapping(mapping_path)
    return await migrate(agents, threads, apply=apply)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True, help="JSON owner mapping; see the Phase 11 runbook")
    parser.add_argument("--apply", action="store_true", help="assign owners and copy referenced legacy attachments")
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args.mapping, args.apply))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        raise SystemExit(2) from error
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
