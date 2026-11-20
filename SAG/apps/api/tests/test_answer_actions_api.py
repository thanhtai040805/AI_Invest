"""Behavioral and security tests for SAG AI answer actions (feedback, share, regenerate, admin)."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import event, select
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
    AnswerFeedback,
    Document,
    DocumentVersion,
    Message,
    Source,
    SourceProjectMapping,
    User,
)
from sag_api.enums import DocumentStatus, MessageRole, MessageStatus
from sag_api.services import answer_actions_service as actions_svc
from sag_api.services.document_service import revoke_project_document
from sag_api.tools import Tool, ToolContext, ToolMeta, ToolRegistry, ToolResult


class StubSearchContextTool(Tool):
    meta = ToolMeta(
        name="search_context",
        description="Stub grounded knowledge search for answer actions API tests.",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )

    async def invoke(self, args: dict, ctx: ToolContext) -> ToolResult:
        ctx.search_context_called = True
        return ToolResult(
            content="[1] PostgreSQL is the primary relational store.",
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
                    "snippet": "PostgreSQL is primary.",
                }
            ],
            data={"section_count": 1},
        )


class FakeLLM(ModelProvider):
    """Deterministic fake LLM performing grounded search_context on turn 1 then answering."""

    configured = True

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or ["Regenerated answer [1]"])
        self.calls: list[list[dict]] = []
        self.in_flight_started: asyncio.Event | None = None
        self.release_gate: asyncio.Event | None = None

    async def complete(self, messages: list[dict], **_kwargs) -> str:
        return "Summary"

    async def stream_turn(self, request: ModelRequest, cancellation: CancellationToken):
        self.calls.append([m.to_model_dict() for m in request.messages])
        if self.in_flight_started is not None:
            self.in_flight_started.set()
        if self.release_gate is not None:
            await self.release_gate.wait()
        cancellation.raise_if_cancelled()
        has_tool_output = any(m.role == "tool" for m in request.messages)
        if not has_tool_output and any(
            (t.get("function") or {}).get("name") == "search_context" for t in request.tools
        ):
            yield ModelChunk(
                tool_calls=(
                    ToolCall(id="tc_search_1", name="search_context", arguments={"query": "db"}),
                ),
                finish_reason="tool_calls",
                usage=Usage(requests=1, input_tokens=8, output_tokens=4),
            )
            return
        text = self._responses.pop(0) if self._responses else "Regenerated answer [1]"
        yield ModelChunk(
            text_delta=text,
            finish_reason="stop",
            usage=Usage(requests=1, input_tokens=10, output_tokens=5),
        )


def _continuum_bearer(
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


def _bff_headers(
    *,
    token: str = "test-bff-secret",
    user_id: str | None = None,
    org_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, str]:
    headers = {"X-SAG-BFF-Service-Token": token}
    if user_id and org_id and project_id:
        headers.update(
            {
                "X-Continuum-User-Id": user_id,
                "X-Continuum-Organization-Id": org_id,
                "X-Continuum-Tenant-Id": org_id,
                "X-Continuum-Project-Id": project_id,
                "X-Continuum-Security-Partition": project_id,
            }
        )
    return headers


@pytest.fixture
async def action_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_mode", "continuum")
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-secret")
    monkeypatch.setattr(settings, "session_context_mode", "off")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _rec):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Patch agents.SessionLocal so SSE streaming persists into the in-memory DB
    import sag_api.api.v1.agents as agents_api_mod

    monkeypatch.setattr(agents_api_mod, "SessionLocal", session_factory)

    runtime = AgentRuntime()
    await runtime.start()
    llm = FakeLLM(["Fresh regenerated v2 [1]", "Fresh regenerated v3 [1]"])
    registry = ToolRegistry()
    registry.register(StubSearchContextTool())
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
                User(id=u_owner, email=f"continuum-{u_owner}@sag.invalid", password_hash="x", name="Owner"),
                User(id=u_peer, email=f"continuum-{u_peer}@sag.invalid", password_hash="x", name="Peer"),
                User(id=u_outsider, email=f"continuum-{u_outsider}@sag.invalid", password_hash="x", name="Out"),
                Agent(id="agent_default", name="Continuum AI", is_default=True),
                Source(
                    id="src_proj_1",
                    name="Project Spec",
                    sag_source_config_id="cfg_1",
                ),
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

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Create owner's thread via real endpoint so owner_key matches Continuum session
        create_res = await client.post(
            "/api/v1/agents/agent_default/threads",
            headers=_continuum_bearer(user_id=u_owner),
            json={"title": "Architecture Q&A"},
        )
        assert create_res.status_code == 201
        thread_id = create_res.json()["id"]

        base_time = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
        async with session_factory() as s:
            q1 = Message(
                id="msg_q1",
                thread_id=thread_id,
                role=MessageRole.USER,
                content="Question 1: What database do we use?",
                created_at=base_time,
                updated_at=base_time,
            )
            a1 = Message(
                id="msg_a1",
                thread_id=thread_id,
                role=MessageRole.ASSISTANT,
                reply_to_message_id="msg_q1",
                content="Answer 1: We use PostgreSQL [1].",
                citations=[
                    {
                        "n": 1,
                        "kind": "internal",
                        "source_id": "src_proj_1",
                        "document_id": "doc_1",
                        "document_version_id": "ver_1",
                        "chunk_id": "chk_1",
                        "page_from": 1,
                        "page_to": 1,
                        "anchor": "p1",
                        "snippet": "PostgreSQL is primary.",
                    }
                ],
                steps=[
                    {
                        "kind": "tool",
                        "step": 1,
                        "name": "search_context",
                        "arguments": {"query": "database secret internal arg"},
                        "ms": 42,
                        "details": {"scope": "knowledge", "sources": [{"id": "src_proj_1"}]},
                    }
                ],
                prompt_preview="SYSTEM SECRET PROMPT PREVIEW",
                status=MessageStatus.OK,
                created_at=base_time + timedelta(seconds=5),
                updated_at=base_time + timedelta(seconds=5),
            )
            q2 = Message(
                id="msg_q2",
                thread_id=thread_id,
                role=MessageRole.USER,
                content="LATER TURN QUESTION MUST BE EXCLUDED FROM Q1 REGENERATION",
                created_at=base_time + timedelta(seconds=20),
                updated_at=base_time + timedelta(seconds=20),
            )
            a2 = Message(
                id="msg_a2",
                thread_id=thread_id,
                role=MessageRole.ASSISTANT,
                reply_to_message_id="msg_q2",
                content="LATER TURN ANSWER MUST BE EXCLUDED",
                citations=[],
                steps=[],
                status=MessageStatus.OK,
                created_at=base_time + timedelta(seconds=25),
                updated_at=base_time + timedelta(seconds=25),
            )
            s.add_all([q1, a1, q2, a2])
            await s.commit()

        yield SimpleNamespace(
            client=client,
            sessions=session_factory,
            runtime=runtime,
            llm=llm,
            thread_id=thread_id,
            u_owner=u_owner,
            u_peer=u_peer,
            u_outsider=u_outsider,
        )

    actions_svc._ACTIVE_THREAD_GENERATIONS.clear()
    await runtime.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_feedback_upsert_switch_delete_validation_and_owner_isolation(action_env) -> None:
    c: httpx.AsyncClient = action_env.client
    tid = action_env.thread_id
    owner_hdr = _continuum_bearer(user_id=action_env.u_owner)
    peer_hdr = _continuum_bearer(user_id=action_env.u_peer)
    url = f"/api/v1/agents/agent_default/threads/{tid}/messages/msg_a1/feedback"

    # Initial GET -> null feedback, no-store header
    res0 = await c.get(url, headers=owner_hdr)
    assert res0.status_code == 200
    assert res0.headers.get("cache-control") == "no-store"
    assert res0.json() == {"feedback": None}

    # LIKE with reasonCategory -> rejected 400
    bad_like = await c.put(
        url,
        headers=owner_hdr,
        json={"rating": "LIKE", "reasonCategory": "INACCURATE"},
    )
    assert bad_like.status_code == 400

    # DISLIKE without reasonCategory -> rejected 400
    bad_dislike = await c.put(url, headers=owner_hdr, json={"rating": "DISLIKE"})
    assert bad_dislike.status_code == 400

    # DISLIKE OTHER with blank text -> rejected 400
    bad_other = await c.put(
        url,
        headers=owner_hdr,
        json={"rating": "DISLIKE", "reasonCategory": "OTHER", "reasonText": "   "},
    )
    assert bad_other.status_code == 400

    # Forged actor/scope field -> rejected 400
    forged = await c.put(
        url,
        headers=owner_hdr,
        json={"rating": "LIKE", "voter_subject": action_env.u_peer},
    )
    assert forged.status_code == 400

    # Valid LIKE
    like_res = await c.put(url, headers=owner_hdr, json={"rating": "LIKE"})
    assert like_res.status_code == 200
    assert like_res.json()["rating"] == "LIKE"
    assert like_res.json()["reason_category"] is None

    # Switch LIKE -> DISLIKE on same answer updates in place (does NOT create a 2nd row!)
    dislike_res = await c.put(
        url,
        headers=owner_hdr,
        json={
            "rating": "DISLIKE",
            "reasonCategory": "OTHER",
            "reasonText": "Thiếu trích dẫn cho phần cấu hình pool.",
        },
    )
    assert dislike_res.status_code == 200
    body = dislike_res.json()
    assert body["rating"] == "DISLIKE"
    assert body["reason_category"] == "OTHER"
    assert body["reason_text"] == "Thiếu trích dẫn cho phần cấu hình pool."

    async with action_env.sessions() as s:
        rows = list((await s.scalars(select(AnswerFeedback))).all())
        assert len(rows) == 1

    # Non-owner in same project cannot read, overwrite, or delete owner's private answer vote -> 404
    assert (await c.get(url, headers=peer_hdr)).status_code == 404
    assert (await c.put(url, headers=peer_hdr, json={"rating": "LIKE"})).status_code == 404
    assert (await c.delete(url, headers=peer_hdr)).status_code == 404

    # Voting on USER message -> rejected 400
    user_msg_url = f"/api/v1/agents/agent_default/threads/{tid}/messages/msg_q1/feedback"
    assert (await c.put(user_msg_url, headers=owner_hdr, json={"rating": "LIKE"})).status_code == 400

    # Owner DELETE is idempotent 204
    del1 = await c.delete(url, headers=owner_hdr)
    assert del1.status_code == 204
    del2 = await c.delete(url, headers=owner_hdr)
    assert del2.status_code == 204
    assert (await c.get(url, headers=owner_hdr)).json() == {"feedback": None}


@pytest.mark.asyncio
async def test_share_create_read_revoke_and_live_source_acl_revalidation(action_env) -> None:
    c: httpx.AsyncClient = action_env.client
    tid = action_env.thread_id
    owner_hdr = _continuum_bearer(user_id=action_env.u_owner)
    peer_bff_hdr = _bff_headers(
        user_id=action_env.u_peer,
        org_id="org_alpha",
        project_id="proj_alpha",
    )
    outsider_bff_hdr = _bff_headers(
        user_id=action_env.u_outsider,
        org_id="org_alpha",
        project_id="proj_beta",
    )
    create_url = f"/api/v1/agents/agent_default/threads/{tid}/messages/msg_a1/shares"

    # Forged extra fields rejected
    assert (await c.post(create_url, headers=owner_hdr, json={"public": True})).status_code == 400

    # First create -> 201 Created; repeat create -> 200 OK with same share ID
    res_new = await c.post(create_url, headers=owner_hdr, json={})
    assert res_new.status_code == 201
    share_id = res_new.json()["id"]
    assert res_new.json()["project_id"] == "proj_alpha"
    assert res_new.json()["message_id"] == "msg_a1"

    res_existing = await c.post(create_url, headers=owner_hdr, json={})
    assert res_existing.status_code == 200
    assert res_existing.json()["id"] == share_id

    read_url = f"/api/v1/projects/proj_alpha/shares/{share_id}"

    # Unauthenticated read -> 401/403 (never 200)
    unauth = await c.get(read_url)
    assert unauth.status_code in (401, 403)

    # Same-project peer reads ONLY question + answer + citations (no steps, prompt_preview, or later turns!)
    peer_read = await c.get(read_url, headers=peer_bff_hdr)
    assert peer_read.status_code == 200
    payload = peer_read.json()
    assert payload["question"] == {"content": "Question 1: What database do we use?"}
    assert payload["answer"]["id"] == "msg_a1"
    assert payload["answer"]["content"] == "Answer 1: We use PostgreSQL [1]."
    assert len(payload["answer"]["citations"]) == 1
    serialized = json.dumps(payload)
    assert "SYSTEM SECRET PROMPT PREVIEW" not in serialized
    assert "database secret internal arg" not in serialized
    assert "LATER TURN" not in serialized
    assert action_env.u_owner not in serialized

    # Cross-project read -> safe 404 (when accessed under proj_beta)
    cross_proj = await c.get(f"/api/v1/projects/proj_beta/shares/{share_id}", headers=outsider_bff_hdr)
    assert cross_proj.status_code == 404

    # Peer (non-creator) cannot revoke -> safe 404
    peer_revoke = await c.delete(read_url, headers=peer_bff_hdr)
    assert peer_revoke.status_code == 404

    # Revoke source mapping in DB -> next read immediately fails closed with safe 404!
    async with action_env.sessions() as s:
        mapping = await s.get(SourceProjectMapping, "spm_1")
        mapping.state = "REVOKED"
        mapping.revoked_at = datetime.now(UTC)
        mapping.revoked_by = "admin"
        mapping.revocation_ref = "rev_1"
        await s.commit()

    revoked_src_read = await c.get(read_url, headers=peer_bff_hdr)
    assert revoked_src_read.status_code == 404

    # Restore source mapping -> readable again until creator revokes share
    async with action_env.sessions() as s:
        mapping = await s.get(SourceProjectMapping, "spm_1")
        mapping.state = "CONFIRMED"
        mapping.revoked_at = None
        mapping.revoked_by = None
        mapping.revocation_ref = None
        await s.commit()
    assert (await c.get(read_url, headers=peer_bff_hdr)).status_code == 200

    # Creator revokes -> 204 (and idempotent 204), subsequent read -> 404, new create -> fresh 201!
    owner_bff_hdr = _bff_headers(
        user_id=action_env.u_owner,
        org_id="org_alpha",
        project_id="proj_alpha",
    )
    assert (await c.delete(read_url, headers=owner_bff_hdr)).status_code == 204
    assert (await c.delete(read_url, headers=owner_bff_hdr)).status_code == 204
    assert (await c.get(read_url, headers=peer_bff_hdr)).status_code == 404

    res_recreated = await c.post(create_url, headers=owner_hdr, json={})
    assert res_recreated.status_code == 201
    assert res_recreated.json()["id"] != share_id


@pytest.mark.asyncio
async def test_regenerate_uses_only_prior_history_preserves_old_answer_and_blocks_concurrent_runs(
    action_env,
) -> None:
    c: httpx.AsyncClient = action_env.client
    tid = action_env.thread_id
    owner_hdr = _continuum_bearer(user_id=action_env.u_owner)

    # Vote on msg_a1 BEFORE regenerating so we prove old answer & vote remain untouched
    vote_url = f"/api/v1/agents/agent_default/threads/{tid}/messages/msg_a1/feedback"
    assert (await c.put(vote_url, headers=owner_hdr, json={"rating": "LIKE"})).status_code == 200

    regen_url = f"/api/v1/agents/agent_default/threads/{tid}/messages/msg_a1/regenerate"
    res = await c.post(regen_url, headers=owner_hdr, json={})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")
    assert "run.completed" in res.text
    assert "Fresh regenerated v2" in res.text

    # Verify LLM prompt did NOT include original answer msg_a1 OR later turns msg_q2/msg_a2!
    assert len(action_env.llm.calls) == 2
    sent_contents = "\n".join(str(m.get("content") or "") for m in action_env.llm.calls[0])
    assert "Question 1: What database do we use?" in sent_contents
    assert "Answer 1: We use PostgreSQL" not in sent_contents
    assert "LATER TURN" not in sent_contents

    # Fetch messages: NO duplicate user bubble; msg_a1 intact with its LIKE vote; new answer has lineage!
    msgs_res = await c.get(
        f"/api/v1/agents/agent_default/threads/{tid}/messages?limit=20",
        headers=owner_hdr,
    )
    assert msgs_res.status_code == 200
    items = msgs_res.json()["items"]
    user_q1_bubbles = [m for m in items if m["role"] == "user" and "Question 1" in m["content"]]
    assert len(user_q1_bubbles) == 1

    new_answers = [m for m in items if m["content"] == "Fresh regenerated v2 [1]"]
    assert len(new_answers) == 1
    new_ans = new_answers[0]
    assert new_ans["reply_to_message_id"] == "msg_q1"
    assert new_ans["regenerated_from_message_id"] == "msg_a1"
    assert len(new_ans["citations"]) == 1
    assert new_ans["citations"][0]["source_id"] == "src_proj_1"

    # Old answer vote is still LIKE; new answer has NO transferred vote
    assert (await c.get(vote_url, headers=owner_hdr)).json()["feedback"]["rating"] == "LIKE"
    new_vote_url = f"/api/v1/agents/agent_default/threads/{tid}/messages/{new_ans['id']}/feedback"
    assert (await c.get(new_vote_url, headers=owner_hdr)).json() == {"feedback": None}

    # Concurrent regeneration OR ask on the same thread -> 409 Conflict!
    started = asyncio.Event()
    gate = asyncio.Event()
    action_env.llm.in_flight_started = started
    action_env.llm.release_gate = gate
    action_env.llm._responses = ["Slow regen [1]"]

    first_task = asyncio.create_task(c.post(regen_url, headers=owner_hdr, json={}))
    await asyncio.wait_for(started.wait(), timeout=3.0)

    second = await c.post(regen_url, headers=owner_hdr, json={})
    assert second.status_code == 409
    concurrent_ask = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/ask",
        headers=owner_hdr,
        json={"query": "Can I interrupt?"},
    )
    assert concurrent_ask.status_code == 409

    gate.set()
    first_done = await first_task
    assert first_done.status_code == 200
    action_env.llm.in_flight_started = None
    action_env.llm.release_gate = None


@pytest.mark.asyncio
async def test_platform_admin_feedback_service_auth_totals_filters_and_cursor_binding(
    action_env,
) -> None:
    c: httpx.AsyncClient = action_env.client
    owner_hdr = _continuum_bearer(user_id=action_env.u_owner, through_bff=False)
    svc_hdr = _bff_headers()

    # Seed feedback across multiple orgs/projects/timestamps
    t0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    async with action_env.sessions() as s:
        s.add_all(
            [
                Message(id="m_extra_1", thread_id=action_env.thread_id, role=MessageRole.ASSISTANT, content="Secret A"),
                Message(id="m_extra_2", thread_id=action_env.thread_id, role=MessageRole.ASSISTANT, content="Secret B"),
                Message(id="m_extra_3", thread_id=action_env.thread_id, role=MessageRole.ASSISTANT, content="Secret C"),
            ]
        )
        await s.flush()
        s.add_all(
            [
                AnswerFeedback(
                    id="fb_1",
                    message_id="msg_a1",
                    voter_subject=action_env.u_owner,
                    tenant_id="org_alpha",
                    project_id="proj_alpha",
                    rating="LIKE",
                    created_at=t0,
                    updated_at=t0,
                ),
                AnswerFeedback(
                    id="fb_2",
                    message_id="m_extra_1",
                    voter_subject=action_env.u_owner,
                    tenant_id="org_alpha",
                    project_id="proj_alpha",
                    rating="DISLIKE",
                    reason_category="INACCURATE",
                    reason_text="Sai số liệu Q3",
                    created_at=t0 + timedelta(minutes=1),
                    updated_at=t0 + timedelta(minutes=1),
                ),
                AnswerFeedback(
                    id="fb_3",
                    message_id="m_extra_2",
                    voter_subject=action_env.u_peer,
                    tenant_id="org_alpha",
                    project_id="proj_alpha",
                    rating="DISLIKE",
                    reason_category="OTHER",
                    reason_text="Thiếu nguồn tham chiếu",
                    created_at=t0 + timedelta(minutes=2),
                    updated_at=t0 + timedelta(minutes=2),
                ),
                AnswerFeedback(
                    id="fb_4",
                    message_id="m_extra_3",
                    voter_subject=action_env.u_outsider,
                    tenant_id="org_beta",
                    project_id="proj_beta",
                    rating="LIKE",
                    created_at=t0 + timedelta(minutes=3),
                    updated_at=t0 + timedelta(minutes=3),
                ),
            ]
        )
        await s.commit()

    admin_url = "/api/v1/platform/ai/feedback"

    # Ordinary Continuum user JWT CANNOT call global admin endpoint -> 403!
    denied = await c.get(admin_url, headers=owner_hdr)
    assert denied.status_code == 403

    # Unknown filter rejected -> 400!
    unknown_param = await c.get(f"{admin_url}?voter_subject=leak", headers=svc_hdr)
    assert unknown_param.status_code == 400

    # Scoped to org_alpha + limit=1: totals reflect ALL 3 matching rows (1 like, 2 dislikes), independent of page!
    page1 = await c.get(f"{admin_url}?organizationId=org_alpha&limit=1", headers=svc_hdr)
    assert page1.status_code == 200
    p1 = page1.json()
    assert p1["totals"] == {"likes": 1, "dislikes": 2}
    assert len(p1["items"]) == 1
    assert p1["items"][0]["id"] == "fb_3"
    assert p1["items"][0]["organization_id"] == "org_alpha"
    assert "voter_subject" not in p1["items"][0]
    assert "Secret" not in json.dumps(p1)
    assert p1["next_cursor"] is not None

    # Cursor bound to active filters: reusing org_alpha cursor with rating=DISLIKE fails with 400!
    tampered = await c.get(
        f"{admin_url}?organizationId=org_alpha&rating=DISLIKE&cursor={p1['next_cursor']}",
        headers=svc_hdr,
    )
    assert tampered.status_code == 400

    # Valid next page with matching filters returns fb_2 and keeps identical totals
    page2 = await c.get(
        f"{admin_url}?organizationId=org_alpha&limit=1&cursor={p1['next_cursor']}",
        headers=svc_hdr,
    )
    assert page2.status_code == 200
    p2 = page2.json()
    assert p2["totals"] == {"likes": 1, "dislikes": 2}
    assert [row["id"] for row in p2["items"]] == ["fb_2"]

    # UTC window filter: 'from' inclusive (t0 + 1m -> includes fb_2), 'to' exclusive (t0 + 2m -> excludes fb_3)
    from_iso = (t0 + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    to_iso = (t0 + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    window_res = await c.get(
        f"{admin_url}?organizationId=org_alpha&from={from_iso}&to={to_iso}",
        headers=svc_hdr,
    )
    assert window_res.status_code == 200
    win = window_res.json()
    assert win["totals"] == {"likes": 0, "dislikes": 1}
    assert [row["id"] for row in win["items"]] == ["fb_2"]


@pytest.mark.asyncio
async def test_legacy_parent_question_resolution_and_recoverable_failed_answer_regeneration(
    action_env,
) -> None:
    c: httpx.AsyncClient = action_env.client
    tid = action_env.thread_id
    owner_hdr = _continuum_bearer(user_id=action_env.u_owner)

    t_leg = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
    async with action_env.sessions() as s:
        s.add_all(
            [
                # Legacy unambiguous pair (reply_to_message_id is None, immediately preceded by single USER turn)
                Message(
                    id="leg_q1",
                    thread_id=tid,
                    role=MessageRole.USER,
                    content="Legacy question without FK?",
                    created_at=t_leg,
                    updated_at=t_leg,
                ),
                Message(
                    id="leg_a1",
                    thread_id=tid,
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id=None,
                    content="Legacy answer [1].",
                    citations=[],
                    steps=[],
                    status=MessageStatus.OK,
                    created_at=t_leg + timedelta(seconds=2),
                    updated_at=t_leg + timedelta(seconds=2),
                ),
                # Legacy ambiguous answer (preceded by another ASSISTANT turn, no reply_to_message_id)
                Message(
                    id="leg_a_ambiguous",
                    thread_id=tid,
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id=None,
                    content="Orphaned second legacy answer.",
                    citations=[],
                    steps=[],
                    status=MessageStatus.OK,
                    created_at=t_leg + timedelta(seconds=4),
                    updated_at=t_leg + timedelta(seconds=4),
                ),
                # Recoverable terminal FAILED answer with known parent question
                Message(
                    id="q_failed",
                    thread_id=tid,
                    role=MessageRole.USER,
                    content="Retryable failed question?",
                    created_at=t_leg + timedelta(seconds=10),
                    updated_at=t_leg + timedelta(seconds=10),
                ),
                Message(
                    id="a_failed",
                    thread_id=tid,
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id="q_failed",
                    content="",
                    citations=[],
                    steps=[],
                    status=MessageStatus.FAILED,
                    error={"code": "STREAM_ERROR", "message": "Temporary timeout", "retryable": True},
                    created_at=t_leg + timedelta(seconds=12),
                    updated_at=t_leg + timedelta(seconds=12),
                ),
            ]
        )
        await s.commit()

    # Unambiguous legacy answer regenerates cleanly
    leg_ok = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/leg_a1/regenerate",
        headers=owner_hdr,
        json={},
    )
    assert leg_ok.status_code == 200
    assert "run.completed" in leg_ok.text

    # Ambiguous legacy answer fails closed with 400 (no speculative guessing)
    leg_bad = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/leg_a_ambiguous/regenerate",
        headers=owner_hdr,
        json={},
    )
    assert leg_bad.status_code == 400

    # Failed answer cannot be voted/shared (400), but CAN be regenerated ("Try again")!
    assert (
        await c.put(
            f"/api/v1/agents/agent_default/threads/{tid}/messages/a_failed/feedback",
            headers=owner_hdr,
            json={"rating": "DISLIKE", "reasonCategory": "INCOMPLETE"},
        )
    ).status_code == 400
    assert (
        await c.post(
            f"/api/v1/agents/agent_default/threads/{tid}/messages/a_failed/shares",
            headers=owner_hdr,
            json={},
        )
    ).status_code == 400

    retry_ok = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/a_failed/regenerate",
        headers=owner_hdr,
        json={},
    )
    assert retry_ok.status_code == 200
    assert "run.completed" in retry_ok.text


@pytest.mark.asyncio
async def test_direct_sag_jwt_share_read_and_revoke_rejected_without_bff_service_token(
    action_env,
) -> None:
    """Finding #1 regression: direct SAG JWT must not read or revoke shares without BFF service auth."""
    c: httpx.AsyncClient = action_env.client
    tid = action_env.thread_id
    owner_jwt_hdr = _continuum_bearer(user_id=action_env.u_owner, through_bff=False)
    outsider_jwt_hdr = _continuum_bearer(
        user_id=action_env.u_outsider,
        org_id="org_alpha",
        project_id="proj_alpha",
        through_bff=False,
    )

    create_res = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/msg_a1/shares",
        headers=_continuum_bearer(user_id=action_env.u_owner),
        json={},
    )
    assert create_res.status_code == 201
    share_id = create_res.json()["id"]
    share_url = f"/api/v1/projects/proj_alpha/shares/{share_id}"

    # Direct Bearer JWT without BFF service authentication MUST be rejected on both GET and DELETE
    direct_outsider_get = await c.get(share_url, headers=outsider_jwt_hdr)
    assert direct_outsider_get.status_code == 403
    direct_owner_get = await c.get(share_url, headers=owner_jwt_hdr)
    assert direct_owner_get.status_code == 403
    direct_owner_del = await c.delete(share_url, headers=owner_jwt_hdr)
    assert direct_owner_del.status_code == 403

    # Legitimate BFF service-authenticated member read still returns 200
    peer_bff_hdr = _bff_headers(
        user_id=action_env.u_peer,
        org_id="org_alpha",
        project_id="proj_alpha",
    )
    assert (await c.get(share_url, headers=peer_bff_hdr)).status_code == 200


@pytest.mark.asyncio
async def test_share_fails_closed_after_cited_project_document_revoked(
    action_env,
) -> None:
    """Finding #2 regression: revoke_project_document invalidates shares even if Source stays CONFIRMED."""
    c: httpx.AsyncClient = action_env.client
    tid = action_env.thread_id
    owner_jwt_hdr = _continuum_bearer(user_id=action_env.u_owner)
    peer_bff_hdr = _bff_headers(
        user_id=action_env.u_peer,
        org_id="org_alpha",
        project_id="proj_alpha",
    )

    create_url = f"/api/v1/agents/agent_default/threads/{tid}/messages/msg_a1/shares"
    create_res = await c.post(create_url, headers=owner_jwt_hdr, json={})
    assert create_res.status_code == 201
    share_id = create_res.json()["id"]
    read_url = f"/api/v1/projects/proj_alpha/shares/{share_id}"

    # Readable before document revocation
    assert (await c.get(read_url, headers=peer_bff_hdr)).status_code == 200

    # Revoke cited document (keeps SourceProjectMapping CONFIRMED, sets doc.is_active=False + ver=REVOKED)
    async with action_env.sessions() as s:
        rev = await revoke_project_document(
            s,
            tenant_id="org_alpha",
            project_id="proj_alpha",
            document_id="doc_1",
            reason="ACL_REVOKED",
        )
        assert rev.status == "REVOKED"
        mapping = await s.get(SourceProjectMapping, "spm_1")
        assert mapping is not None and mapping.state == "CONFIRMED"

    # Existing share read AND repeat/new share creation MUST fail closed with 404!
    after_revoke = await c.get(read_url, headers=peer_bff_hdr)
    assert after_revoke.status_code == 404
    create_after_revoke = await c.post(create_url, headers=owner_jwt_hdr, json={})
    assert create_after_revoke.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_feedback_and_share_races_do_not_raise_500(
    tmp_path,
) -> None:
    """Finding #3 regression: concurrent vote/share races never raise MissingGreenlet or StaleDataError."""
    from sag_api.core.principal_assertion import VerifiedPrincipal
    from sag_api.db.models import Thread
    from sag_api.schemas.answer_actions import FeedbackUpsertIn

    db_path = tmp_path / "race_actions.db"
    race_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")

    @event.listens_for(race_engine.sync_engine, "connect")
    def _race_pragmas(conn, _rec):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")

    async with race_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    race_sessions = async_sessionmaker(race_engine, expire_on_commit=False)
    u_owner = "651a2b3c4d5e6f7a8b9c0d01"
    owner_key = f"continuum:org_alpha:proj_alpha:{u_owner}"
    principal = VerifiedPrincipal(
        subject=u_owner,
        organization_id="org_alpha",
        allowed_project_ids=frozenset({"proj_alpha"}),
        issuer="continuum-sag-session",
        key_id="sag-hs256-session",
        token_id="tok_race",
        issued_at=1,
        expires_at=9999999999,
        tenant_id="org_alpha",
        allowed_partition_ids=frozenset({"proj_alpha"}),
    )

    t0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    async with race_sessions() as s:
        s.add_all(
            [
                Agent(id="agent_default", name="Continuum AI", is_default=True),
                Thread(id="thr_race", agent_id="agent_default", owner_key=owner_key, title="Race"),
            ]
        )
        await s.flush()
        s.add_all(
            [
                Message(
                    id="rq_1",
                    thread_id="thr_race",
                    role=MessageRole.USER,
                    content="Race question?",
                    created_at=t0,
                    updated_at=t0,
                ),
                Message(
                    id="ra_1",
                    thread_id="thr_race",
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id="rq_1",
                    content="Race answer.",
                    citations=[],
                    steps=[],
                    status=MessageStatus.OK,
                    created_at=t0 + timedelta(seconds=1),
                    updated_at=t0 + timedelta(seconds=1),
                ),
            ]
        )
        await s.commit()

    try:
        # 1. Deterministic first-vote race: session B inserts before session A executes upsert!
        async with race_sessions() as s_loser:
            orig_scalars = s_loser.scalars
            injected = False

            async def _interleaved_scalars(statement, *args, **kwargs):
                nonlocal injected
                if not injected and "INSERT" in str(statement).upper():
                    injected = True
                    async with race_sessions() as s_winner:
                        await actions_svc.upsert_own_feedback(
                            s_winner,
                            agent_id="agent_default",
                            thread_id="thr_race",
                            message_id="ra_1",
                            owner_key=owner_key,
                            principal=principal,
                            payload=FeedbackUpsertIn(rating="LIKE"),
                        )
                return await orig_scalars(statement, *args, **kwargs)

            s_loser.scalars = _interleaved_scalars  # type: ignore[method-assign]
            res_after_race = await actions_svc.upsert_own_feedback(
                s_loser,
                agent_id="agent_default",
                thread_id="thr_race",
                message_id="ra_1",
                owner_key=owner_key,
                principal=principal,
                payload=FeedbackUpsertIn(rating="DISLIKE", reasonCategory="INCOMPLETE"),
            )
            assert res_after_race.rating == "DISLIKE"
            assert res_after_race.reason_category == "INCOMPLETE"

        # 2. Deterministic PUT vs DELETE race + concurrent gather across separate sessions!
        async with race_sessions() as s_updater:
            orig_scalars_upd = s_updater.scalars
            deleted_in_between = False

            async def _delete_before_upsert(statement, *args, **kwargs):
                nonlocal deleted_in_between
                if not deleted_in_between and "INSERT" in str(statement).upper():
                    deleted_in_between = True
                    async with race_sessions() as s_deleter:
                        await actions_svc.delete_own_feedback(
                            s_deleter,
                            agent_id="agent_default",
                            thread_id="thr_race",
                            message_id="ra_1",
                            owner_key=owner_key,
                            principal=principal,
                        )
                return await orig_scalars_upd(statement, *args, **kwargs)

            s_updater.scalars = _delete_before_upsert  # type: ignore[method-assign]
            res_put_vs_del = await actions_svc.upsert_own_feedback(
                s_updater,
                agent_id="agent_default",
                thread_id="thr_race",
                message_id="ra_1",
                owner_key=owner_key,
                principal=principal,
                payload=FeedbackUpsertIn(rating="LIKE"),
            )
            assert res_put_vs_del.rating == "LIKE"

        async def _concurrent_vote(rating: str):
            async with race_sessions() as s_conc:
                body = (
                    FeedbackUpsertIn(rating="LIKE")
                    if rating == "LIKE"
                    else FeedbackUpsertIn(rating="DISLIKE", reasonCategory="OUTDATED")
                )
                return await actions_svc.upsert_own_feedback(
                    s_conc,
                    agent_id="agent_default",
                    thread_id="thr_race",
                    message_id="ra_1",
                    owner_key=owner_key,
                    principal=principal,
                    payload=body,
                )

        async def _concurrent_delete():
            async with race_sessions() as s_conc:
                await actions_svc.delete_own_feedback(
                    s_conc,
                    agent_id="agent_default",
                    thread_id="thr_race",
                    message_id="ra_1",
                    owner_key=owner_key,
                    principal=principal,
                )

        await asyncio.gather(
            _concurrent_vote("LIKE"),
            _concurrent_delete(),
            _concurrent_vote("DISLIKE"),
            _concurrent_delete(),
            _concurrent_vote("LIKE"),
        )

        # 3. Deterministic POST /shares race: session B inserts before session A commits!
        async with race_sessions() as s_share_loser:
            orig_share_commit = s_share_loser.commit
            share_injected = False
            winner_share_id = ""

            async def _interleaved_share_commit():
                nonlocal share_injected, winner_share_id
                if not share_injected:
                    share_injected = True
                    async with race_sessions() as s_share_winner:
                        winner_out, winner_created = await actions_svc.create_or_get_share(
                            s_share_winner,
                            agent_id="agent_default",
                            thread_id="thr_race",
                            message_id="ra_1",
                            owner_key=owner_key,
                            principal=principal,
                        )
                        assert winner_created is True
                        winner_share_id = winner_out.id
                return await orig_share_commit()

            s_share_loser.commit = _interleaved_share_commit  # type: ignore[method-assign]
            loser_out, loser_created = await actions_svc.create_or_get_share(
                s_share_loser,
                agent_id="agent_default",
                thread_id="thr_race",
                message_id="ra_1",
                owner_key=owner_key,
                principal=principal,
            )
            assert loser_created is False
            assert loser_out.id == winner_share_id
    finally:
        await race_engine.dispose()


@pytest.mark.asyncio
async def test_legacy_consecutive_user_turns_with_distinct_timestamps_rejected_400(
    action_env,
) -> None:
    """Finding #4 regression: legacy q1(T), q2(T+1), a1(T+2) without reply_to_message_id must fail closed with 400."""
    c: httpx.AsyncClient = action_env.client
    tid = action_env.thread_id
    owner_hdr = _continuum_bearer(user_id=action_env.u_owner)

    t0 = datetime(2026, 10, 30, 11, 0, tzinfo=UTC)
    async with action_env.sessions() as s:
        s.add_all(
            [
                Message(
                    id="amb_q1",
                    thread_id=tid,
                    role=MessageRole.USER,
                    content="First overlapping legacy question?",
                    created_at=t0,
                    updated_at=t0,
                ),
                Message(
                    id="amb_q2",
                    thread_id=tid,
                    role=MessageRole.USER,
                    content="Second overlapping legacy question?",
                    created_at=t0 + timedelta(seconds=1),
                    updated_at=t0 + timedelta(seconds=1),
                ),
                Message(
                    id="amb_a1",
                    thread_id=tid,
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id=None,
                    content="Legacy answer whose parent is ambiguous.",
                    citations=[],
                    steps=[],
                    status=MessageStatus.OK,
                    created_at=t0 + timedelta(seconds=2),
                    updated_at=t0 + timedelta(seconds=2),
                ),
            ]
        )
        await s.commit()

    regen_res = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/amb_a1/regenerate",
        headers=owner_hdr,
        json={},
    )
    assert regen_res.status_code == 400

    share_res = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/amb_a1/shares",
        headers=owner_hdr,
        json={},
    )
    assert share_res.status_code == 400


def _session_memory_step(message_ids: list[str], *, source_ids: list[str] | None = None) -> dict:
    from sag_api.services.agent_service import _build_session_audit_step

    citation = {
        "kind": "session_memory",
        "ref": "S1",
        "primary_message_id": message_ids[-1] if message_ids else None,
        "message_ids": message_ids,
        "cited_source_ids": source_ids or [],
        "mapped": True,
    }
    step = _build_session_audit_step({"mode": "on"}, used_session_citations=[citation])
    assert step is not None
    return step


@pytest.mark.asyncio
async def test_transitive_session_share_revalidates_original_document(action_env) -> None:
    c = action_env.client
    tid = action_env.thread_id
    t0 = datetime(2026, 10, 30, 18, 0, tzinfo=UTC)
    async with action_env.sessions() as s:
        s.add_all([
            Message(
                id=mid, thread_id=tid, role=MessageRole.ASSISTANT,
                reply_to_message_id="msg_q2", content="Remembered PostgreSQL [S1].",
                citations=[], steps=[_session_memory_step([prior], source_ids=["src_proj_1"])],
                created_at=t0 + timedelta(seconds=i), updated_at=t0,
            )
            for i, (mid, prior) in enumerate([("session_a", "msg_a1"), ("session_b", "session_a")])
        ])
        await s.commit()
    create_url = f"/api/v1/agents/agent_default/threads/{tid}/messages/session_b/shares"
    owner = _continuum_bearer(user_id=action_env.u_owner)
    reader = _bff_headers(user_id=action_env.u_peer, org_id="org_alpha", project_id="proj_alpha")
    created = await c.post(create_url, headers=owner, json={})
    assert created.status_code == 201
    read_url = f"/api/v1/projects/proj_alpha/shares/{created.json()['id']}"
    assert (await c.get(read_url, headers=reader)).status_code == 200
    async with action_env.sessions() as s:
        await revoke_project_document(
            s, tenant_id="org_alpha", project_id="proj_alpha", document_id="doc_1", reason="ACL_REVOKED"
        )
    assert (await c.get(read_url, headers=reader)).status_code == 404
    assert (await c.post(create_url, headers=owner, json={})).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken", ["missing", "cross_thread", "cycle", "malformed", "missing_refs", "unmapped", "depth", "source", "hidden"]
)
async def test_session_share_fails_closed_on_invalid_reference_graph(action_env, broken: str) -> None:
    from sag_api.db.models import Thread

    tid = action_env.thread_id
    root_step = _session_memory_step(["msg_a1"], source_ids=["src_proj_1"])
    nodes = []
    if broken == "missing":
        root_step = _session_memory_step(["missing_message"])
    elif broken == "cross_thread":
        nodes = [Message(id="cross_ref", thread_id="other_thread", role=MessageRole.USER, content="Private")]
        root_step = _session_memory_step(["cross_ref"])
    elif broken == "cycle":
        nodes = [Message(
            id="cycle_ref", thread_id=tid, role=MessageRole.ASSISTANT, content="Cycle",
            citations=[], steps=[_session_memory_step(["session_root"])],
        )]
        root_step = _session_memory_step(["cycle_ref"])
    elif broken == "malformed":
        root_step["session_citations"][0]["message_ids"] = "msg_a1"
    elif broken == "missing_refs":
        root_step["session_citations"][0].pop("message_ids")
        root_step["session_citations"][0].pop("primary_message_id")
    elif broken == "unmapped":
        root_step = _session_memory_step(["missing_message"])
        root_step["session_citations"][0].update(mapped=False, claim_level="run")
    elif broken == "depth":
        nodes = [Message(
            id=f"deep_{i}", thread_id=tid, role=MessageRole.ASSISTANT, content="History",
            citations=[], steps=[_session_memory_step([f"deep_{i + 1}" if i < 33 else "msg_q1"])],
        ) for i in range(34)]
        root_step = _session_memory_step(["deep_0"])
    elif broken == "source":
        nodes = [Message(
            id="denied_ref", thread_id=tid, role=MessageRole.ASSISTANT, content="Denied evidence",
            citations=[{"source_id": "not_authorized", "document_id": "doc_1"}], steps=[],
        )]
        root_step = _session_memory_step(["denied_ref"], source_ids=["src_proj_1"])
    elif broken == "hidden":
        from sag_api.services.session_context import HIDDEN_SCOPE_PLACEHOLDER

        nodes = [Message(
            id="hidden_ref", thread_id=tid, role=MessageRole.ASSISTANT,
            content=HIDDEN_SCOPE_PLACEHOLDER, citations=[], steps=[],
        )]
        root_step = _session_memory_step(["hidden_ref"])
    async with action_env.sessions() as s:
        if broken == "cross_thread":
            s.add(Thread(id="other_thread", agent_id="agent_default", owner_key="different_owner", title="Other"))
            await s.flush()
        s.add_all(nodes)
        s.add(Message(
            id="session_root", thread_id=tid, role=MessageRole.ASSISTANT,
            reply_to_message_id="msg_q2", content="Remembered answer [S1].", citations=[], steps=[root_step],
        ))
        await s.commit()
    result = await action_env.client.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/session_root/shares",
        headers=_continuum_bearer(user_id=action_env.u_owner), json={},
    )
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_session_share_accepts_user_context_and_repeated_acyclic_references(action_env) -> None:
    tid = action_env.thread_id
    async with action_env.sessions() as s:
        s.add_all([
            Message(
                id="user_memory", thread_id=tid, role=MessageRole.ASSISTANT,
                reply_to_message_id="msg_q2", content="Your own earlier question [S1].", citations=[],
                steps=[_session_memory_step(["msg_q1"])],
            ),
            Message(
                id="repeat_memory", thread_id=tid, role=MessageRole.ASSISTANT,
                reply_to_message_id="msg_q2", content="Remembered answer [S1].", citations=[],
                steps=[_session_memory_step(["user_memory", "msg_q1"])],
            ),
        ])
        await s.commit()
    result = await action_env.client.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/repeat_memory/shares",
        headers=_continuum_bearer(user_id=action_env.u_owner), json={},
    )
    assert result.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("overlap", [False, True])
async def test_legacy_parent_checks_complete_unanswered_prefix(action_env, overlap: bool) -> None:
    from sag_api.core.errors import BadRequestError

    tid = action_env.thread_id
    sequence = (
        [("old_q1", "user"), ("old_q2", "user"), ("old_a1", "assistant"),
         ("old_q3", "user"), ("old_a2", "assistant")]
        if overlap else
        [("old_q1", "user"), ("old_a1", "assistant"), ("old_q2", "user"), ("old_a2", "assistant")]
    )
    t0 = datetime(2026, 10, 30, 19, 0, tzinfo=UTC)
    async with action_env.sessions() as s:
        s.add_all([Message(
            id=mid, thread_id=tid, role=MessageRole(role), content=mid, citations=[], steps=[],
            created_at=t0 + timedelta(seconds=i), updated_at=t0,
        ) for i, (mid, role) in enumerate(sequence)])
        await s.commit()
        target = await s.get(Message, "old_a2")
        if overlap:
            with pytest.raises(BadRequestError):
                await actions_svc.resolve_parent_question(s, thread_id=tid, answer_message=target)
        else:
            question = await actions_svc.resolve_parent_question(s, thread_id=tid, answer_message=target)
            assert question.id == "old_q2"


@pytest.mark.asyncio
async def test_regeneration_lineage_limit_never_falls_back_to_legacy_guess(action_env) -> None:
    from sag_api.core.errors import BadRequestError

    tid = action_env.thread_id
    t0 = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)
    async with action_env.sessions() as s:
        s.add(Message(
            id="lineage_q", thread_id=tid, role=MessageRole.USER, content="Original",
            created_at=t0, updated_at=t0,
        ))
        await s.flush()
        for i in range(33):
            s.add(Message(
                id=f"lineage_{i}", thread_id=tid, role=MessageRole.ASSISTANT, content="Old answer",
                regenerated_from_message_id=f"lineage_{i - 1}" if i else None,
                created_at=t0 + timedelta(seconds=i + 1), updated_at=t0,
            ))
            await s.flush()
        await s.commit()
        with pytest.raises(BadRequestError):
            await actions_svc.resolve_parent_question(
                s, thread_id=tid, answer_message=await s.get(Message, "lineage_32")
            )



async def test_answer_mutations_require_bff_even_with_valid_owner_session(action_env):
    c = action_env.client
    base = f"/api/v1/agents/agent_default/threads/{action_env.thread_id}/messages/msg_a1"
    direct = _continuum_bearer(user_id=action_env.u_owner, through_bff=False)
    operations = [
        ("PUT", "feedback", {"rating": "LIKE"}),
        ("DELETE", "feedback", None),
        ("POST", "shares", {}),
        ("POST", "regenerate", {}),
    ]
    for method, suffix, body in operations:
        result = await c.request(
            method, f"{base}/{suffix}", headers=direct, **({"json": body} if body is not None else {})
        )
        assert result.status_code == 403
    trusted = _continuum_bearer(user_id=action_env.u_owner)
    assert (await c.put(f"{base}/feedback", headers=trusted, json={"rating": "LIKE"})).status_code == 200


@pytest.mark.asyncio
async def test_legacy_audit_only_and_shadow_session_steps_revalidate_revoked_documents(
    action_env,
) -> None:
    """Session context steps without session_citations (e.g. V0_legacy recent_turn_ids or shadow) also fail closed."""
    from sag_api.services.agent_service import _build_session_audit_step

    c = action_env.client
    tid = action_env.thread_id
    t0 = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
    legacy_audit_step = _build_session_audit_step(
        {
            "mode": "legacy",
            "ablation": "V0_legacy",
            "used_recent_tokens": 18,
            "total_output_history_tokens": 18,
            "recent_turn_ids": ["msg_a1"],
        }
    )
    missing_refs_audit_step = _build_session_audit_step(
        {
            "mode": "on",
            "used_evidence_tokens": 24,
            "total_output_history_tokens": 24,
        }
    )
    assert legacy_audit_step is not None and "session_citations" not in legacy_audit_step
    assert missing_refs_audit_step is not None

    async with action_env.sessions() as s:
        s.add_all(
            [
                Message(
                    id="session_v0",
                    thread_id=tid,
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id="msg_q2",
                    content="Answer derived from V0_legacy window.",
                    citations=[],
                    steps=[legacy_audit_step],
                    created_at=t0,
                    updated_at=t0,
                ),
                Message(
                    id="session_missing_audit_refs",
                    thread_id=tid,
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id="msg_q2",
                    content="Answer missing audit turn refs.",
                    citations=[],
                    steps=[missing_refs_audit_step],
                    created_at=t0 + timedelta(seconds=1),
                    updated_at=t0 + timedelta(seconds=1),
                ),
            ]
        )
        await s.commit()

    owner = _continuum_bearer(user_id=action_env.u_owner)
    reader = _bff_headers(user_id=action_env.u_peer, org_id="org_alpha", project_id="proj_alpha")

    # Missing provenance refs despite positive session tokens -> fail closed immediately
    assert (
        await c.post(
            f"/api/v1/agents/agent_default/threads/{tid}/messages/session_missing_audit_refs/shares",
            headers=owner,
            json={},
        )
    ).status_code == 404

    # V0_legacy recent_turn_ids succeeds while doc_1 is active, then fails closed after revocation
    v0_create = await c.post(
        f"/api/v1/agents/agent_default/threads/{tid}/messages/session_v0/shares",
        headers=owner,
        json={},
    )
    assert v0_create.status_code == 201
    read_url = f"/api/v1/projects/proj_alpha/shares/{v0_create.json()['id']}"
    assert (await c.get(read_url, headers=reader)).status_code == 200

    async with action_env.sessions() as s:
        await revoke_project_document(
            s,
            tenant_id="org_alpha",
            project_id="proj_alpha",
            document_id="doc_1",
            reason="ACL_REVOKED",
        )
    assert (await c.get(read_url, headers=reader)).status_code == 404


@pytest.mark.asyncio
async def test_legacy_parent_rejects_same_timestamp_uuid_tie_ambiguity(action_env) -> None:
    """Same-timestamp concurrent legacy messages sorted by random ID must fail closed instead of misbinding."""
    from sag_api.core.errors import BadRequestError

    tid = action_env.thread_id
    t_same = datetime(2026, 10, 30, 22, 0, tzinfo=UTC)
    async with action_env.sessions() as s:
        s.add_all(
            [
                Message(
                    id="tie_111_q1",
                    thread_id=tid,
                    role=MessageRole.USER,
                    content="Concurrent question 1",
                    created_at=t_same,
                    updated_at=t_same,
                ),
                Message(
                    id="tie_222_a_fast",
                    thread_id=tid,
                    role=MessageRole.ASSISTANT,
                    reply_to_message_id=None,
                    content="Fast answer at identical timestamp",
                    citations=[],
                    steps=[],
                    created_at=t_same,
                    updated_at=t_same,
                ),
                Message(
                    id="tie_333_q2",
                    thread_id=tid,
                    role=MessageRole.USER,
                    content="Concurrent question 2 with higher ID",
                    created_at=t_same,
                    updated_at=t_same,
                ),
            ]
        )
        await s.commit()
        target = await s.get(Message, "tie_222_a_fast")
        assert target is not None
        with pytest.raises(BadRequestError):
            await actions_svc.resolve_parent_question(s, thread_id=tid, answer_message=target)
