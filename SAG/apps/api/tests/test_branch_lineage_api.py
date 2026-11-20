"""Comprehensive behavioral, bi-temporal ECCE, security/ACL, pagination, and GC tests for 'Branch in new chat'."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_agent import AgentRuntime
from sag_agent.types import (
    CancellationToken,
    ModelChunk,
    ModelProvider,
    ModelRequest,
    ToolCall,
    Usage,
)
from sag_api.api.v1 import api_router
from sag_api.core.config import settings
from sag_api.core.deps import (
    get_agent_runtime,
    get_engine_manager,
    get_llm,
    get_session,
    get_tool_registry,
)
from sag_api.core.errors import ApiError
from sag_api.core.security import create_access_token
from sag_api.db.base import Base
from sag_api.db.models import (
    Agent,
    Document,
    DocumentVersion,
    Message,
    SessionDecisionLedger,
    SessionUnit,
    Source,
    SourceProjectMapping,
    Thread,
    User,
)
from sag_api.enums import DocumentStatus
from sag_api.services.agent_domain import _encode_message_cursor
from sag_api.services.document_service import revoke_project_document
from sag_api.services.session_context.substrate import SESSION_INDEX_CACHE
from sag_api.tools import Tool, ToolContext, ToolMeta, ToolRegistry, ToolResult


class GroundedSearchTool(Tool):
    meta = ToolMeta(
        name="search_context",
        description="Stub grounded knowledge search for branch lineage tests.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )

    async def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        ctx.search_context_called = True
        return ToolResult(
            content="[1] Architecture spec confirms PostgreSQL 16.",
            citations=[
                {
                    "n": 1,
                    "kind": "internal",
                    "source_id": "src_proj_1",
                    "source_name": "Project Spec",
                    "document_id": "doc_1",
                    "document_version_id": "ver_1",
                    "document_name": "spec.md",
                    "version_no": 1,
                    "chunk_id": "chk_1",
                    "page_from": 1,
                    "page_to": 1,
                    "anchor": "p1",
                    "snippet": "PostgreSQL 16 is primary.",
                }
            ],
            data={"section_count": 1},
        )


class RecordingLLM(ModelProvider):
    configured = True

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[list[dict]] = []

    def queue(self, *texts: str) -> None:
        self._responses.extend(texts)

    async def complete(self, messages: list[dict], **_kwargs) -> str:
        return "Summary"

    async def stream_turn(self, request: ModelRequest, cancellation: CancellationToken):
        self.calls.append([m.to_model_dict() for m in request.messages])
        cancellation.raise_if_cancelled()
        has_tool_output = any(m.role == "tool" for m in request.messages)
        if not has_tool_output and any(
            (t.get("function") or {}).get("name") == "search_context" for t in request.tools
        ):
            yield ModelChunk(
                tool_calls=(
                    ToolCall(id="tc_search_1", name="search_context", arguments={"query": "spec"}),
                ),
                finish_reason="tool_calls",
                usage=Usage(requests=1, input_tokens=8, output_tokens=4),
            )
            return
        text = self._responses.pop(0) if self._responses else "Grounded reply [1]"
        yield ModelChunk(
            text_delta=text,
            finish_reason="stop",
            usage=Usage(requests=1, input_tokens=10, output_tokens=5),
        )


def _continuum_headers(
    *,
    user_id: str,
    org_id: str = "org_alpha",
    project_id: str = "proj_alpha",
    through_bff: bool = True,
) -> dict[str, str]:
    token = create_access_token(
        user_id,
        extra={
            "org_id": org_id,
            "tenant_id": org_id,
            "project_id": project_id,
            "security_partition": project_id,
            "jti": secrets.token_urlsafe(16),
        },
    )
    headers = {"Authorization": f"Bearer {token}"}
    if through_bff:
        headers["X-SAG-BFF-Service-Token"] = "test-bff-secret"
    return headers


def _bff_share_headers(
    *,
    user_id: str,
    org_id: str = "org_alpha",
    project_id: str = "proj_alpha",
) -> dict[str, str]:
    return {
        "X-SAG-BFF-Service-Token": "test-bff-secret",
        "X-Continuum-User-Id": user_id,
        "X-Continuum-Organization-Id": org_id,
        "X-Continuum-Tenant-Id": org_id,
        "X-Continuum-Project-Id": project_id,
        "X-Continuum-Security-Partition": project_id,
    }


def _extract_sse_events(raw_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    cur_event = ""
    for line in raw_text.splitlines():
        if line.startswith("event:"):
            cur_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_str = line.split(":", 1)[1].strip()
            if data_str:
                events.append((cur_event, json.loads(data_str)))
    return events


async def _ask_turn(
    client: httpx.AsyncClient,
    *,
    agent_id: str,
    thread_id: str,
    query: str,
    headers: dict[str, str],
) -> str:
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/threads/{thread_id}/ask",
        headers=headers,
        json={"query": query},
    )
    assert resp.status_code == 200, resp.text
    events = _extract_sse_events(resp.text)
    completed = [payload for ev, payload in events if ev == "run.completed"]
    assert completed, f"Expected run.completed in {events}"
    return str(completed[-1]["payload"]["message_id"])


@pytest.fixture
async def branch_env(monkeypatch: pytest.MonkeyPatch):
    SESSION_INDEX_CACHE.clear()
    monkeypatch.setattr(settings, "auth_mode", "continuum")
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-secret")
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "session_context_persistence_enabled", True)
    monkeypatch.setattr(settings, "session_context_embedding_mode", "deterministic")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _rec) -> None:  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA recursive_triggers=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    import sag_api.api.v1.agents as agents_api_mod

    monkeypatch.setattr(agents_api_mod, "SessionLocal", session_factory)

    runtime = AgentRuntime()
    await runtime.start()
    llm = RecordingLLM()
    registry = ToolRegistry()
    registry.register(GroundedSearchTool())
    engine_mgr = SimpleNamespace()

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_err(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    @app.exception_handler(RequestValidationError)
    async def _req_val_err(_req: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "VALIDATION_ERROR", "message": str(exc.errors()[:1])}},
        )

    app.include_router(api_router)

    async def _override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_engine_manager] = lambda: engine_mgr

    u_owner = "651a2b3c4d5e6f7a8b9c0d01"
    u_peer = "651a2b3c4d5e6f7a8b9c0d02"
    u_outsider = "651a2b3c4d5e6f7a8b9c0d03"

    async with session_factory() as s:
        s.add_all(
            [
                User(id=u_owner, email=f"continuum-{u_owner}@sag.invalid", password_hash="x", name="KeyT"),
                User(id=u_peer, email=f"continuum-{u_peer}@sag.invalid", password_hash="x", name="Peer"),
                User(id=u_outsider, email=f"continuum-{u_outsider}@sag.invalid", password_hash="x", name="Out"),
                Agent(id="agent_default", name="Continuum AI", is_default=True),
                Source(id="src_proj_1", name="Project Spec", sag_source_config_id="cfg_1"),
                SourceProjectMapping(
                    id="spm_1",
                    source_id="src_proj_1",
                    organization_id="org_alpha",
                    project_id="proj_alpha",
                    state="CONFIRMED",
                    confirmed_at=datetime.now(UTC),
                    confirmed_by="admin",
                    approval_ref="apr_1",
                ),
            ]
        )
        await s.flush()
        s.add(
            Document(
                id="doc_1",
                tenant_id="org_alpha",
                project_id="proj_alpha",
                owner_id=u_owner,
                logical_source_id="ls_spec_1",
                source_id="src_proj_1",
                filename="spec.md",
                content_type="text/markdown",
                size_bytes=128,
                storage_path="/tmp/spec.md",
                status=DocumentStatus.READY,
                is_active=True,
            )
        )
        await s.flush()
        s.add(
            DocumentVersion(
                id="ver_1",
                document_id="doc_1",
                version_no=1,
                file_hash="hash_spec_1",
                status="READY",
                search_status="READY",
                knowledge_status="READY",
                search_ready_at=datetime.now(UTC),
                knowledge_ready_at=datetime.now(UTC),
                metadata_json={"security_partition_id": "proj_alpha"},
            )
        )
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(
            client=client,
            session_factory=session_factory,
            llm=llm,
            u_owner=u_owner,
            u_peer=u_peer,
            u_outsider=u_outsider,
        )

    await runtime.stop()
    SESSION_INDEX_CACHE.clear()
    await engine.dispose()


async def test_branch_creates_zero_duplicate_messages_and_isolates_future_turns(branch_env):
    client: httpx.AsyncClient = branch_env.client
    headers = _continuum_headers(user_id=branch_env.u_owner)

    # Create Thread A and run 2 turns (A -> B -> C -> D)
    r_thread = await client.post(
        "/api/v1/agents/agent_default/threads",
        headers=headers,
        json={"title": "Architecture Exploration"},
    )
    assert r_thread.status_code == 201
    thread_a = r_thread.json()
    assert thread_a["is_branched"] is False
    assert thread_a["parent_thread_id"] is None

    branch_env.llm.queue("Answer A1 about auth [1]", "Answer A2 about database [1]")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_a["id"],
        query="How does auth work?",
        headers=headers,
    )
    msg_d_id = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_a["id"],
        query="What database do we use?",
        headers=headers,
    )

    # Requires BFF service token for branch mutation
    no_bff_headers = _continuum_headers(user_id=branch_env.u_owner, through_bff=False)
    r_forbidden = await client.post(
        f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages/{msg_d_id}/branch",
        headers=no_bff_headers,
        json={},
    )
    assert r_forbidden.status_code in (401, 403)

    # Branch at message D
    r_branch = await client.post(
        f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages/{msg_d_id}/branch",
        headers=headers,
        json={},
    )
    assert r_branch.status_code == 201, r_branch.text
    thread_b = r_branch.json()
    assert thread_b["id"] != thread_a["id"]
    assert thread_b["title"] == "Architecture Exploration"
    assert thread_b["is_branched"] is True
    assert thread_b["parent_thread_id"] == thread_a["id"]
    assert thread_b["parent_thread_title"] == "Architecture Exploration"
    assert thread_b["branch_point_message_id"] == msg_d_id

    # Verify ZERO cloned Message rows in DB for Thread B
    async with branch_env.session_factory() as s:
        own_count = await s.scalar(select(func.count(Message.id)).where(Message.thread_id == thread_b["id"]))
        assert own_count == 0

    # Thread B lists inherited prefix [Q1, A1, Q2, A2] with is_inherited = True
    r_b_msgs = await client.get(
        f"/api/v1/agents/agent_default/threads/{thread_b['id']}/messages",
        headers=headers,
    )
    assert r_b_msgs.status_code == 200
    b_items = r_b_msgs.json()["items"]
    assert len(b_items) == 4
    assert all(item["is_inherited"] is True for item in b_items)
    assert b_items[-1]["id"] == msg_d_id

    # Continue Thread A with post-fork turn E/F
    branch_env.llm.queue("Post-fork secret on Thread A [1]")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_a["id"],
        query="Thread A secret post-fork question E",
        headers=headers,
    )

    # Continue Thread B with turn X/Y
    branch_env.llm.calls.clear()
    branch_env.llm.queue("Branch B independent direction Y [1]")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_b["id"],
        query="Explore Redis caching on Branch B",
        headers=headers,
    )

    # Prompt sent to LLM for Branch B MUST contain inherited A1/A2 context and MUST NOT contain E/F!
    first_call_msgs = branch_env.llm.calls[0]
    joined_prompt = "\n".join(str(m.get("content") or "") for m in first_call_msgs)
    assert "Answer A2 about database" in joined_prompt
    assert "Thread A secret post-fork" not in joined_prompt
    assert "Post-fork secret on Thread A" not in joined_prompt

    # Thread A messages = 6 (all own), Thread B messages = 6 (4 inherited + 2 own)
    a_after = (
        await client.get(f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages", headers=headers)
    ).json()["items"]
    b_after = (
        await client.get(f"/api/v1/agents/agent_default/threads/{thread_b['id']}/messages", headers=headers)
    ).json()["items"]
    assert len(a_after) == 6
    assert all(item["is_inherited"] is False for item in a_after)
    assert "Explore Redis caching on Branch B" not in {item["content"] for item in a_after}

    assert len(b_after) == 6
    assert [item["is_inherited"] for item in b_after] == [True, True, True, True, False, False]
    assert "Thread A secret post-fork question E" not in {item["content"] for item in b_after}


async def test_bitemporal_ecce_as_of_isolation_when_parent_or_child_supersedes_decisions(branch_env):
    client: httpx.AsyncClient = branch_env.client
    headers = _continuum_headers(user_id=branch_env.u_owner)

    thread_a = (
        await client.post("/api/v1/agents/agent_default/threads", headers=headers, json={"title": "DB Decision"})
    ).json()

    # Turn 1 on Thread A confirms PostgreSQL 16
    branch_env.llm.queue("Chốt dùng PostgreSQL 16 làm database chính [1]")
    msg_a1_id = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_a["id"],
        query="Chốt dùng PostgreSQL 16 làm database chính.",
        headers=headers,
    )

    # Branch B created at msg_a1_id
    thread_b = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages/{msg_a1_id}/branch",
            headers=headers,
            json={},
        )
    ).json()

    # AFTER branching, Thread A supersedes PostgreSQL 16 -> CockroachDB!
    branch_env.llm.queue(
        "Đã chốt thay PostgreSQL 16 bằng CockroachDB làm database chính [1]",
        "CockroachDB đang là database chính trên Thread A [1]",
    )
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_a["id"],
        query="Chốt quyết định cuối cùng: thay PostgreSQL 16 bằng CockroachDB làm database chính.",
        headers=headers,
    )
    # Trigger one follow-up ask on Thread A so Turn 2's SUPERSEDED decision is flushed to PostgreSQL
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_a["id"],
        query="Hiện tại dịch vụ đang chốt dùng database nào?",
        headers=headers,
    )

    # Confirm Thread A's persisted DB row actually marked an earlier turn/decision SUPERSEDED
    async with branch_env.session_factory() as s:
        a_decs = list(
            (
                await s.scalars(
                    select(SessionDecisionLedger).where(
                        SessionDecisionLedger.thread_id == thread_a["id"]
                    )
                )
            ).all()
        )
        assert any(d.epistemic_status == "SUPERSEDED" for d in a_decs)

    # Cold-start eviction: force Branch B to hydrate from PostgreSQL!
    SESSION_INDEX_CACHE.clear()
    branch_env.llm.calls.clear()
    branch_env.llm.queue("Ở Branch B, PostgreSQL 16 vẫn là database chính [1]")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_b["id"],
        query="Hiện tại dịch vụ đang chốt dùng database nào?",
        headers=headers,
    )

    b_prompt = "\n".join(str(m.get("content") or "") for m in branch_env.llm.calls[0])
    assert "PostgreSQL 16" in b_prompt
    assert "CockroachDB" not in b_prompt

    # Now Branch B supersedes PostgreSQL 16 -> MySQL on its own timeline (also flushes Branch B's first own turn!),
    # then cold-restarts!
    branch_env.llm.queue("Đã chốt thay PostgreSQL 16 bằng MySQL trong nhánh này [1]")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_b["id"],
        query="Chốt quyết định cuối cùng: thay PostgreSQL 16 bằng MySQL làm database chính.",
        headers=headers,
    )
    SESSION_INDEX_CACHE.clear()
    branch_env.llm.calls.clear()
    branch_env.llm.queue("MySQL là database chính của Branch B [1]")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_b["id"],
        query="Sau khi đổi, database chính hiện tại là gì?",
        headers=headers,
    )
    b_restart_prompt = "\n".join(str(m.get("content") or "") for m in branch_env.llm.calls[0])
    assert "MySQL" in b_restart_prompt
    assert "CockroachDB" not in b_restart_prompt

    # Verify Branch B persisted ONLY its own turns (ordinal >= 2), recorded `inherited_turn:<tid>`,
    # and never mutated Thread A's rows
    async with branch_env.session_factory() as s:
        b_units = list(
            (
                await s.scalars(
                    select(SessionUnit)
                    .where(SessionUnit.thread_id == thread_b["id"], SessionUnit.unit_kind == "TURN")
                    .order_by(SessionUnit.ordinal.asc())
                )
            ).all()
        )
        assert len(b_units) >= 2
        assert all(u.ordinal >= 2 for u in b_units)
        b_decs = list(
            (
                await s.scalars(
                    select(SessionDecisionLedger).where(
                        SessionDecisionLedger.thread_id == thread_b["id"]
                    )
                )
            ).all()
        )
        assert any(
            any(str(x).startswith("inherited_turn:") for x in (d.supersedes_ids_json or []))
            for d in b_decs
        )

    # Second cold restart after MySQL was persisted to Branch B's own SessionDecisionLedger (`inherited_turn:<tid>`)
    SESSION_INDEX_CACHE.clear()
    branch_env.llm.calls.clear()
    branch_env.llm.queue("MySQL vẫn đang active sau lần cold restart thứ hai [1]")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=thread_b["id"],
        query="Database chính hiện tại sau lần khởi động lại thứ hai là gì?",
        headers=headers,
    )
    b_second_cold_prompt = "\n".join(str(m.get("content") or "") for m in branch_env.llm.calls[0])
    assert "MySQL" in b_second_cold_prompt
    assert "CockroachDB" not in b_second_cold_prompt


async def test_nested_branching_direct_origin_pointer_and_regenerated_version_cutoff(branch_env):
    client: httpx.AsyncClient = branch_env.client
    headers = _continuum_headers(user_id=branch_env.u_owner)

    thread_a = (
        await client.post("/api/v1/agents/agent_default/threads", headers=headers, json={"title": "Root Thread"})
    ).json()

    branch_env.llm.queue("Root Turn 1 [1]", "Root Turn 2 v1 [1]")
    msg_a1_id = await _ask_turn(
        client, agent_id="agent_default", thread_id=thread_a["id"], query="Q1", headers=headers
    )
    msg_a2_v1_id = await _ask_turn(
        client, agent_id="agent_default", thread_id=thread_a["id"], query="Q2", headers=headers
    )

    # Regenerate Q2 twice -> v2 and v3
    branch_env.llm.queue("Root Turn 2 v2 [1]", "Root Turn 2 v3 [1]")
    r_regen_2 = await client.post(
        f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages/{msg_a2_v1_id}/regenerate",
        headers=headers,
        json={},
    )
    assert r_regen_2.status_code == 200
    msg_a2_v2_id = [
        p["payload"]["message_id"] for ev, p in _extract_sse_events(r_regen_2.text) if ev == "run.completed"
    ][-1]

    r_regen_3 = await client.post(
        f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages/{msg_a2_v2_id}/regenerate",
        headers=headers,
        json={},
    )
    assert r_regen_3.status_code == 200

    # User views < 2 / 3 > (msg_a2_v2_id) and branches!
    thread_b = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages/{msg_a2_v2_id}/branch",
            headers=headers,
            json={"title": "Branch from v2"},
        )
    ).json()
    assert thread_b["title"] == "Branch from v2"

    # Even if user goes back to Thread A and regenerates Q1 AFTER branching, Branch B stays frozen!
    branch_env.llm.queue("Root Turn 1 post-fork v2 [1]")
    r_regen_q1_after = await client.post(
        f"/api/v1/agents/agent_default/threads/{thread_a['id']}/messages/{msg_a1_id}/regenerate",
        headers=headers,
        json={},
    )
    assert r_regen_q1_after.status_code == 200

    b_msgs = (
        await client.get(f"/api/v1/agents/agent_default/threads/{thread_b['id']}/messages", headers=headers)
    ).json()["items"]
    assert [m["content"] for m in b_msgs] == [
        "Q1",
        "Root Turn 1 [1]",
        "Q2",
        "Root Turn 2 v2 [1]",
    ]

    # Add an own turn on Branch B, then branch C1 from B's own turn, and branch C2 from inherited msg_a1_id!
    branch_env.llm.queue("Branch B Turn 3 [1]")
    msg_b3_id = await _ask_turn(
        client, agent_id="agent_default", thread_id=thread_b["id"], query="Q3 on B", headers=headers
    )

    thread_c1 = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{thread_b['id']}/messages/{msg_b3_id}/branch",
            headers=headers,
            json={},
        )
    ).json()
    assert thread_c1["parent_thread_id"] == thread_b["id"]
    c1_msgs = (
        await client.get(f"/api/v1/agents/agent_default/threads/{thread_c1['id']}/messages", headers=headers)
    ).json()["items"]
    assert [m["content"] for m in c1_msgs] == [
        "Q1",
        "Root Turn 1 [1]",
        "Q2",
        "Root Turn 2 v2 [1]",
        "Q3 on B",
        "Branch B Turn 3 [1]",
    ]

    # Branching C2 from inherited msg_a1_id while inside Branch B flattens parent_thread_id directly to Thread A!
    thread_c2 = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{thread_b['id']}/messages/{msg_a1_id}/branch",
            headers=headers,
            json={},
        )
    ).json()
    assert thread_c2["parent_thread_id"] == thread_a["id"]
    assert thread_c2["branch_point_message_id"] == msg_a1_id
    c2_msgs = (
        await client.get(f"/api/v1/agents/agent_default/threads/{thread_c2['id']}/messages", headers=headers)
    ).json()["items"]
    assert [m["content"] for m in c2_msgs] == ["Q1", "Root Turn 1 [1]"]


async def test_multi_segment_hmac_cursor_pagination_and_v1_compat(branch_env):
    client: httpx.AsyncClient = branch_env.client
    headers = _continuum_headers(user_id=branch_env.u_owner)

    t_a = (await client.post("/api/v1/agents/agent_default/threads", headers=headers, json={"title": "A"})).json()
    branch_env.llm.queue("A1 [1]", "A2 [1]")
    _ = await _ask_turn(client, agent_id="agent_default", thread_id=t_a["id"], query="Q1", headers=headers)
    m_a2 = await _ask_turn(client, agent_id="agent_default", thread_id=t_a["id"], query="Q2", headers=headers)

    t_b = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages/{m_a2}/branch",
            headers=headers,
            json={"title": "B"},
        )
    ).json()
    branch_env.llm.queue("B1 [1]", "B2 [1]")
    _ = await _ask_turn(client, agent_id="agent_default", thread_id=t_b["id"], query="Q3", headers=headers)
    m_b2 = await _ask_turn(client, agent_id="agent_default", thread_id=t_b["id"], query="Q4", headers=headers)

    t_c = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_b2}/branch",
            headers=headers,
            json={"title": "C"},
        )
    ).json()
    branch_env.llm.queue("C1 [1]", "C2 [1]")
    _ = await _ask_turn(client, agent_id="agent_default", thread_id=t_c["id"], query="Q5", headers=headers)
    _ = await _ask_turn(client, agent_id="agent_default", thread_id=t_c["id"], query="Q6", headers=headers)

    # Paginate Thread C (12 messages across 3 segments) with limit=5
    collected: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        url = f"/api/v1/agents/agent_default/threads/{t_c['id']}/messages?limit=5"
        if cursor:
            url += f"&cursor={cursor}"
        res = await client.get(url, headers=headers)
        assert res.status_code == 200
        body = res.json()
        pages += 1
        # Prepend older page to reconstruct full chronological timeline
        collected = body["items"] + collected
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
        assert cursor

    assert pages == 3
    assert [m["content"] for m in collected] == [
        "Q1", "A1 [1]", "Q2", "A2 [1]",
        "Q3", "B1 [1]", "Q4", "B2 [1]",
        "Q5", "C1 [1]", "Q6", "C2 [1]",
    ]
    assert [m["is_inherited"] for m in collected] == [True] * 8 + [False] * 4

    # Cross-thread cursor tampering fails with 400 INVALID_CURSOR
    p1_a = (
        await client.get(f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages?limit=1", headers=headers)
    ).json()
    bad_res = await client.get(
        f"/api/v1/agents/agent_default/threads/{t_c['id']}/messages?limit=2&cursor={p1_a['next_cursor']}",
        headers=headers,
    )
    assert bad_res.status_code == 422

    # Legacy v1 cursor for leaf segment still decodes cleanly
    async with branch_env.session_factory() as s:
        latest_c_msg = await s.scalar(
            select(Message)
            .where(Message.thread_id == t_c["id"])
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        assert latest_c_msg is not None
        legacy_v1_cursor = _encode_message_cursor(t_c["id"], latest_c_msg)

    compat_res = await client.get(
        f"/api/v1/agents/agent_default/threads/{t_c['id']}/messages?limit=5&cursor={legacy_v1_cursor}",
        headers=headers,
    )
    assert compat_res.status_code == 200
    assert compat_res.json()["items"][-1]["content"] == "Q6"


async def test_tombstone_delete_ref_counted_gc_message_delete_guard_and_agent_delete(branch_env):
    client: httpx.AsyncClient = branch_env.client
    headers = _continuum_headers(user_id=branch_env.u_owner)

    t_a = (
        await client.post(
            "/api/v1/agents/agent_default/threads",
            headers=headers,
            json={"title": "Parent A"},
        )
    ).json()
    branch_env.llm.queue("A1 [1]", "A2 post-fork [1]")
    m_a1 = await _ask_turn(client, agent_id="agent_default", thread_id=t_a["id"], query="Q1", headers=headers)

    t_b = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages/{m_a1}/branch",
            headers=headers,
            json={"title": "Child B"},
        )
    ).json()
    m_a2_post = await _ask_turn(
        client, agent_id="agent_default", thread_id=t_a["id"], query="Q2 after fork", headers=headers
    )
    branch_env.llm.queue("B1 [1]")
    m_b1 = await _ask_turn(client, agent_id="agent_default", thread_id=t_b["id"], query="QB1", headers=headers)

    t_c = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_b1}/branch",
            headers=headers,
            json={"title": "Grandchild C"},
        )
    ).json()

    # Deleting a message inside Branch B's inherited prefix on Parent A returns 409 Conflict
    r_del_protected = await client.delete(
        f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages/{m_a1}",
        headers=headers,
    )
    assert r_del_protected.status_code == 409

    # Deleting a post-fork message on Parent A succeeds
    r_del_post = await client.delete(
        f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages/{m_a2_post}",
        headers=headers,
    )
    assert r_del_post.status_code == 200

    # Delete Parent A -> Tombstone! Vanishes from list_threads and returns 404 directly
    r_del_a = await client.delete(f"/api/v1/agents/agent_default/threads/{t_a['id']}", headers=headers)
    assert r_del_a.status_code == 200

    threads_list = (await client.get("/api/v1/agents/agent_default/threads?limit=50", headers=headers)).json()
    listed_ids = {t["id"] for t in threads_list}
    assert t_a["id"] not in listed_ids
    assert {t_b["id"], t_c["id"]}.issubset(listed_ids)

    # Child B still has is_branched=True, but parent_thread_id & parent_thread_title are None (parent tombstoned!)
    b_meta = next(t for t in threads_list if t["id"] == t_b["id"])
    assert b_meta["is_branched"] is True
    assert b_meta["parent_thread_id"] is None
    assert b_meta["parent_thread_title"] is None

    assert (
        await client.get(f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages", headers=headers)
    ).status_code == 404

    # Child B and Grandchild C still read inherited history cleanly!
    c_msgs = (
        await client.get(f"/api/v1/agents/agent_default/threads/{t_c['id']}/messages", headers=headers)
    ).json()["items"]
    assert [m["content"] for m in c_msgs] == ["Q1", "A1 [1]", "QB1", "B1 [1]"]

    # Delete Child B -> Tombstoned (Grandchild C still alive)
    del_b = await client.delete(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}", headers=headers
    )
    assert del_b.status_code == 200
    async with branch_env.session_factory() as s:
        assert await s.get(Thread, t_a["id"]) is not None
        assert await s.get(Thread, t_b["id"]) is not None

    # Delete Grandchild C -> Reference-counted GC physically deletes C, B, AND A!
    del_c = await client.delete(
        f"/api/v1/agents/agent_default/threads/{t_c['id']}", headers=headers
    )
    assert del_c.status_code == 200
    async with branch_env.session_factory() as s:
        assert await s.get(Thread, t_c["id"]) is None
        assert await s.get(Thread, t_b["id"]) is None
        assert await s.get(Thread, t_a["id"]) is None
        assert await s.scalar(select(func.count(Message.id)).where(Message.id == m_a1)) == 0

    # Also verify deleting a custom Agent with branched threads succeeds without FK RESTRICT errors
    custom_agent = (
        await client.post("/api/v1/agents", headers=headers, json={"name": "Custom Agent"})
    ).json()
    ca_root = (
        await client.post(f"/api/v1/agents/{custom_agent['id']}/threads", headers=headers, json={"title": "Root"})
    ).json()
    branch_env.llm.queue("Custom reply [1]")
    ca_msg = await _ask_turn(
        client, agent_id=custom_agent["id"], thread_id=ca_root["id"], query="Hello", headers=headers
    )
    _ = (
        await client.post(
            f"/api/v1/agents/{custom_agent['id']}/threads/{ca_root['id']}/messages/{ca_msg}/branch",
            headers=headers,
            json={},
        )
    ).json()
    del_agent_res = await client.delete(f"/api/v1/agents/{custom_agent['id']}", headers=headers)
    assert del_agent_res.status_code == 200


async def test_live_acl_revocation_and_inherited_answer_actions(branch_env):
    client: httpx.AsyncClient = branch_env.client
    headers = _continuum_headers(user_id=branch_env.u_owner)
    peer_bff_headers = _bff_share_headers(user_id=branch_env.u_peer)

    t_a = (
        await client.post(
            "/api/v1/agents/agent_default/threads",
            headers=headers,
            json={"title": "ACL Chat"},
        )
    ).json()
    branch_env.llm.queue("Grounded secret answer [1]")
    m_a1 = await _ask_turn(
        client, agent_id="agent_default", thread_id=t_a["id"], query="What is the secret spec?", headers=headers
    )

    t_b = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages/{m_a1}/branch",
            headers=headers,
            json={},
        )
    ).json()

    # 1. Feedback & Share work on inherited message m_a1 via Branch B!
    r_like = await client.put(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_a1}/feedback",
        headers=headers,
        json={"rating": "LIKE"},
    )
    assert r_like.status_code == 200
    assert r_like.json()["rating"] == "LIKE"

    r_share = await client.post(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_a1}/shares",
        headers=headers,
        json={},
    )
    assert r_share.status_code == 201
    share_id = r_share.json()["id"]

    r_peer_view = await client.get(
        f"/api/v1/projects/proj_alpha/shares/{share_id}", headers=peer_bff_headers
    )
    assert r_peer_view.status_code == 200

    # 2. In-place Regenerate on inherited message m_a1 in Branch B is rejected with 400!
    r_regen_inherited = await client.post(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_a1}/regenerate",
        headers=headers,
        json={},
    )
    assert r_regen_inherited.status_code == 400

    # 3. Own turn on Branch B CAN be regenerated AND its regeneration prompt still sees inherited Q1/A1!
    branch_env.llm.queue("Branch B own v1 [1]", "Branch B own v2 [1]")
    m_b1 = await _ask_turn(
        client, agent_id="agent_default", thread_id=t_b["id"], query="Follow up on spec", headers=headers
    )
    # Share on m_b1 (which has ECCE session_citations pointing to inherited m_a1!) also succeeds!
    r_share_b1 = await client.post(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_b1}/shares",
        headers=headers,
        json={},
    )
    assert r_share_b1.status_code == 201

    branch_env.llm.calls.clear()
    r_regen_own = await client.post(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_b1}/regenerate",
        headers=headers,
        json={},
    )
    assert r_regen_own.status_code == 200
    regen_prompt = "\n".join(str(m.get("content") or "") for m in branch_env.llm.calls[0])
    assert "Grounded secret answer" in regen_prompt

    # 4. Cross-project principal cannot use Branch B as a backdoor (owner_key is bound to project scope -> safe 404)!
    wrong_proj_headers = _continuum_headers(user_id=branch_env.u_owner, project_id="proj_other")
    r_wrong_proj = await client.get(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages",
        headers=wrong_proj_headers,
    )
    assert r_wrong_proj.status_code == 404

    # 5. Live document revocation immediately masks inherited answer in Branch B, blocks re-branching,
    #    and excludes revoked content from Branch B's ECCE LLM prompt!
    async with branch_env.session_factory() as s:
        await revoke_project_document(
            s,
            tenant_id="org_alpha",
            project_id="proj_alpha",
            logical_source_id="ls_spec_1",
            reason="compliance_revocation",
        )

    r_after_revoke = await client.get(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages",
        headers=headers,
    )
    assert r_after_revoke.status_code == 200
    # Re-branching from m_a1 (whose document version is now REVOKED) fails closed (404)!
    r_rebranch_revoked = await client.post(
        f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages/{m_a1}/branch",
        headers=headers,
        json={},
    )
    assert r_rebranch_revoked.status_code == 404

    # Unconfirming SourceProjectMapping hides m_a1 in Branch B messages AND strips it from ECCE prompt!
    async with branch_env.session_factory() as s:
        spm = await s.get(SourceProjectMapping, "spm_1")
        assert spm is not None
        spm.state = "REVOKED"
        spm.revoked_at = datetime.now(UTC)
        spm.revoked_by = "admin"
        spm.revocation_ref = "rev_1"
        await s.commit()

    msgs_unconfirmed = (
        await client.get(f"/api/v1/agents/agent_default/threads/{t_b['id']}/messages", headers=headers)
    ).json()["items"]
    assert msgs_unconfirmed[1]["content"].startswith("[Answer hidden because")

    branch_env.llm.calls.clear()
    branch_env.llm.queue("Safe ungrounded answer")
    _ = await _ask_turn(
        client,
        agent_id="agent_default",
        thread_id=t_b["id"],
        query="Can you still read the revoked secret?",
        headers=headers,
    )
    post_revoke_prompt = "\n".join(str(m.get("content") or "") for m in branch_env.llm.calls[0])
    assert "Grounded secret answer" not in post_revoke_prompt



async def test_inherited_scope_resolution_and_prior_regeneration_dedup(branch_env):
    """Post-PR review regressions:
    1) `resolve_persistence_scope` resolves single-project scope for a multi-project principal on a
       newly branched thread (`0` own messages) via inherited ancestor citations / persisted units.
    2) Superseded regenerated drafts in an ancestor segment are deduplicated in both ECCE memory
       (`load_ancestor_segments_visible_records`) and legacy `_history()`.
    """
    from sag_api.core.principal_assertion import VerifiedPrincipal
    from sag_api.services.agent_domain import _history
    from sag_api.services.session_context.persistence import resolve_persistence_scope
    from sag_api.services.session_context.runtime import load_ancestor_segments_visible_records

    client: httpx.AsyncClient = branch_env.client
    headers = _continuum_headers(user_id=branch_env.u_owner)

    t_a = (
        await client.post(
            "/api/v1/agents/agent_default/threads",
            headers=headers,
            json={"title": "Dedup & Scope Root"},
        )
    ).json()

    branch_env.llm.queue(
        "Superseded draft v1_MARKER [1]",
        "Active draft v2_MARKER [1]",
        "Turn 2 anchor [1]",
    )
    m_a1_v1 = await _ask_turn(
        client, agent_id="agent_default", thread_id=t_a["id"], query="Draft question", headers=headers
    )
    r_regen = await client.post(
        f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages/{m_a1_v1}/regenerate",
        headers=headers,
        json={},
    )
    assert r_regen.status_code == 200
    m_a2 = await _ask_turn(
        client, agent_id="agent_default", thread_id=t_a["id"], query="Anchor question", headers=headers
    )

    t_b = (
        await client.post(
            f"/api/v1/agents/agent_default/threads/{t_a['id']}/messages/{m_a2}/branch",
            headers=headers,
            json={},
        )
    ).json()

    multi_proj_principal = VerifiedPrincipal(
        subject=branch_env.u_owner,
        organization_id="org_alpha",
        allowed_project_ids=frozenset({"proj_alpha", "proj_beta"}),
        issuer="continuum",
        key_id="k1",
        token_id="t1",
        issued_at=1_700_000_000,
        expires_at=1_900_000_000,
        tenant_id="org_alpha",
        allowed_partition_ids=frozenset({"proj_alpha", "proj_beta"}),
    )

    async with branch_env.session_factory() as s:
        # 1) Multi-project principal inherits unambiguous `proj_alpha` persistence scope on empty Branch B
        scope = await resolve_persistence_scope(
            s,
            thread_id=t_b["id"],
            principal=multi_proj_principal,
            owner_key=f"continuum:org_alpha:proj_alpha:{branch_env.u_owner}",
        )
        assert scope is not None
        assert scope.project_id == "proj_alpha"

        # 2) Ancestor visible records & legacy `_history` keep ONLY v2_MARKER and drop v1_MARKER
        anc_segs, _, _ = await load_ancestor_segments_visible_records(
            s,
            thread_id=t_b["id"],
            principal=multi_proj_principal,
        )
        anc_texts = "\n".join(rec["content"] for _, recs in anc_segs for rec in recs)
        assert "Active draft v2_MARKER" in anc_texts
        assert "Superseded draft v1_MARKER" not in anc_texts

        legacy_hist = await _history(
            s,
            t_b["id"],
            exclude_id="",
            principal=multi_proj_principal,
            requested_source_ids=None,
        )
        legacy_texts = "\n".join(item["content"] for item in legacy_hist)
        assert "Active draft v2_MARKER" in legacy_texts
        assert "Superseded draft v1_MARKER" not in legacy_texts
