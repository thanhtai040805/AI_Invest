"""Unit and integration tests for Manager All-Projects ('all') AI Chat scope in SAG."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_agent import AgentRuntime
from sag_api.api.v1 import api_router
from sag_api.core.config import settings
from sag_api.core.conversation_ownership import conversation_owner_key
from sag_api.core.deps import (
    get_agent_runtime,
    get_engine_manager,
    get_llm,
    get_session,
    get_tool_registry,
)
from sag_api.core.errors import ApiError, ForbiddenError
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import (
    Agent,
    Document,
    DocumentVersion,
    Message,
    SearchUnit,
    Source,
    SourceProjectMapping,
    Thread,
)
from sag_api.enums import DocumentStatus, MessageRole, MessageStatus
from sag_api.services import answer_actions_service as actions_svc
from sag_api.tools import ToolRegistry


def test_conversation_owner_key_is_isolated_and_stable_for_all_projects_scope() -> None:
    single_alpha = VerifiedPrincipal(
        subject="651a2b3c4d5e6f7a8b9c0d01",
        organization_id="org_1",
        allowed_project_ids=frozenset({"proj_alpha"}),
        issuer="continuum-sag-session",
        key_id="sag-hs256-session",
        token_id="t1",
        issued_at=1,
        expires_at=9999999999,
        tenant_id="org_1",
        allowed_partition_ids=frozenset({"proj_alpha"}),
        conversation_scope=None,
    )
    all_two_projects = VerifiedPrincipal(
        subject="651a2b3c4d5e6f7a8b9c0d01",
        organization_id="org_1",
        allowed_project_ids=frozenset({"proj_alpha", "proj_beta"}),
        issuer="continuum-sag-session",
        key_id="sag-hs256-session",
        token_id="t2",
        issued_at=1,
        expires_at=9999999999,
        tenant_id="org_1",
        allowed_partition_ids=frozenset({"proj_alpha", "proj_beta"}),
        conversation_scope="all",
    )
    all_three_projects = VerifiedPrincipal(
        subject="651a2b3c4d5e6f7a8b9c0d01",
        organization_id="org_1",
        allowed_project_ids=frozenset({"proj_alpha", "proj_beta", "proj_gamma"}),
        issuer="continuum-sag-session",
        key_id="sag-hs256-session",
        token_id="t3",
        issued_at=1,
        expires_at=9999999999,
        tenant_id="org_1",
        allowed_partition_ids=frozenset({"proj_alpha", "proj_beta", "proj_gamma"}),
        conversation_scope="all",
    )

    key_single = conversation_owner_key("user_1", single_alpha)
    key_all_2 = conversation_owner_key("user_1", all_two_projects)
    key_all_3 = conversation_owner_key("user_1", all_three_projects)

    assert key_all_2 != key_single
    assert key_all_2 == key_all_3


def test_resolve_single_project_scope_supports_all_projects_conversation_scope() -> None:
    all_principal = VerifiedPrincipal(
        subject="651a2b3c4d5e6f7a8b9c0d01",
        organization_id="org_1",
        allowed_project_ids=frozenset({"proj_alpha", "proj_beta"}),
        issuer="continuum-sag-session",
        key_id="sag-hs256-session",
        token_id="t1",
        issued_at=1,
        expires_at=9999999999,
        tenant_id="org_1",
        allowed_partition_ids=frozenset({"proj_alpha", "proj_beta"}),
        conversation_scope="all",
    )
    assert actions_svc._resolve_single_project_scope(all_principal) == ("org_1", "all")

    unscoped_multi = VerifiedPrincipal(
        subject="651a2b3c4d5e6f7a8b9c0d01",
        organization_id="org_1",
        allowed_project_ids=frozenset({"proj_alpha", "proj_beta"}),
        issuer="continuum-sag-session",
        key_id="sag-hs256-session",
        token_id="t2",
        issued_at=1,
        expires_at=9999999999,
        tenant_id="org_1",
        allowed_partition_ids=frozenset({"proj_alpha", "proj_beta"}),
        conversation_scope=None,
    )
    with pytest.raises(ForbiddenError):
        actions_svc._resolve_single_project_scope(unscoped_multi)


@pytest.mark.asyncio
async def test_continuum_login_all_projects_and_multi_project_actions_and_mind_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    runtime = AgentRuntime()
    await runtime.start()
    llm = SimpleNamespace(configured=False)
    registry = ToolRegistry()
    engine_mgr = SimpleNamespace()

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_err(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    app.include_router(api_router)

    async def _override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_tool_registry] = lambda: registry
    app.dependency_overrides[get_engine_manager] = lambda: engine_mgr

    subject_id = "651a2b3c4d5e6f7a8b9c0d99"
    org_id = "org_continuum_all"
    proj_a = "651a2b3c4d5e6f7a8b9c0da1"
    proj_b = "651a2b3c4d5e6f7a8b9c0db2"

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Missing allowed_project_ids when project_id == "all" is rejected (422)
            bad_login = await client.post(
                "/api/v1/auth/continuum-login",
                headers={
                    "X-SAG-BFF-Service-Token": "test-bff-secret",
                    "X-Continuum-User-Id": subject_id,
                    "X-Continuum-Name": "Manager KeyT",
                    "X-Continuum-Organization-Id": org_id,
                    "X-Continuum-Project-Id": "all",
                },
                json={"displayName": "Manager KeyT"},
            )
            assert bad_login.status_code == 422

            # 2. Valid all-projects login mints multi-project session token
            ok_login = await client.post(
                "/api/v1/auth/continuum-login",
                headers={
                    "X-SAG-BFF-Service-Token": "test-bff-secret",
                    "X-Continuum-User-Id": subject_id,
                    "X-Continuum-Name": "Manager KeyT",
                    "X-Continuum-Organization-Id": org_id,
                    "X-Continuum-Project-Id": "all",
                },
                json={
                    "displayName": "Manager KeyT",
                    "allowed_project_ids": [proj_b, proj_a],
                },
            )
            assert ok_login.status_code == 200
            token_all = ok_login.json()["access_token"]
            auth_headers = {
                "Authorization": f"Bearer {token_all}",
                "X-SAG-BFF-Service-Token": "test-bff-secret",
            }

            default_agent = (await client.get("/api/v1/agents/default", headers=auth_headers)).json()
            agent_id = default_agent["id"]

            # Create thread in all-projects workspace
            thr_resp = await client.post(
                f"/api/v1/agents/{agent_id}/threads",
                headers=auth_headers,
                json={"title": "All projects overview"},
            )
            assert thr_resp.status_code == 201
            thread_id = thr_resp.json()["id"]

            # Single-project session must NOT see the all-projects thread
            single_login = await client.post(
                "/api/v1/auth/continuum-login",
                headers={
                    "X-SAG-BFF-Service-Token": "test-bff-secret",
                    "X-Continuum-User-Id": subject_id,
                    "X-Continuum-Name": "Manager KeyT",
                    "X-Continuum-Organization-Id": org_id,
                    "X-Continuum-Project-Id": proj_a,
                },
                json={"displayName": "Manager KeyT"},
            )
            assert single_login.status_code == 200
            single_headers = {
                "Authorization": f"Bearer {single_login.json()['access_token']}",
                "X-SAG-BFF-Service-Token": "test-bff-secret",
            }
            single_threads = (
                await client.get(f"/api/v1/agents/{agent_id}/threads", headers=single_headers)
            ).json()
            assert [t["id"] for t in single_threads] == []

            # All-projects session with an extra project added still sees the same thread
            expanded_login = await client.post(
                "/api/v1/auth/continuum-login",
                headers={
                    "X-SAG-BFF-Service-Token": "test-bff-secret",
                    "X-Continuum-User-Id": subject_id,
                    "X-Continuum-Name": "Manager KeyT",
                    "X-Continuum-Organization-Id": org_id,
                    "X-Continuum-Project-Id": "all",
                },
                json={
                    "displayName": "Manager KeyT",
                    "allowed_project_ids": [proj_a, proj_b, "651a2b3c4d5e6f7a8b9c0dc3"],
                },
            )
            assert expanded_login.status_code == 200
            expanded_headers = {
                "Authorization": f"Bearer {expanded_login.json()['access_token']}",
                "X-SAG-BFF-Service-Token": "test-bff-secret",
            }
            all_threads = (
                await client.get(f"/api/v1/agents/{agent_id}/threads", headers=expanded_headers)
            ).json()
            assert [t["id"] for t in all_threads] == [thread_id]

            # Seed messages and test feedback + share + mind-map on project_id == "all"
            t0 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
            async with session_factory() as s:
                s.add_all(
                    [
                        Message(
                            id="msg_q_all",
                            thread_id=thread_id,
                            role=MessageRole.USER,
                            status=MessageStatus.OK,
                            content="Tổng hợp tiến độ tất cả dự án?",
                            created_at=t0,
                            updated_at=t0,
                        ),
                        Message(
                            id="msg_a_all",
                            thread_id=thread_id,
                            role=MessageRole.ASSISTANT,
                            status=MessageStatus.OK,
                            reply_to_message_id="msg_q_all",
                            content="Dự án A và Dự án B đều đang đúng tiến độ.",
                            citations=[],
                            created_at=t0,
                            updated_at=t0,
                        ),
                    ]
                )
                await s.commit()

            fb_resp = await client.put(
                f"/api/v1/agents/{agent_id}/threads/{thread_id}/messages/msg_a_all/feedback",
                headers=auth_headers,
                json={"rating": "LIKE"},
            )
            assert fb_resp.status_code == 200
            assert fb_resp.json()["rating"] == "LIKE"

            share_resp = await client.post(
                f"/api/v1/agents/{agent_id}/threads/{thread_id}/messages/msg_a_all/shares",
                headers=auth_headers,
                json={},
            )
            assert share_resp.status_code == 201
            share_id = share_resp.json()["id"]
            assert share_resp.json()["project_id"] == "all"

            bff_all_headers = {
                "X-SAG-BFF-Service-Token": "test-bff-secret",
                "X-Continuum-User-Id": subject_id,
                "X-Continuum-Organization-Id": org_id,
                "X-Continuum-Tenant-Id": org_id,
                "X-Continuum-Project-Id": "all",
                "X-Continuum-Security-Partition": "all",
                "X-Continuum-Allowed-Project-Ids": f"{proj_a},{proj_b}",
            }
            read_share_resp = await client.get(
                f"/api/v1/projects/all/shares/{share_id}",
                headers=bff_all_headers,
            )
            assert read_share_resp.status_code == 200
            assert read_share_resp.json()["answer"]["id"] == "msg_a_all"

            mind_map_resp = await client.get(
                "/api/v1/projects/all/mind-map",
                headers=bff_all_headers,
            )
            assert mind_map_resp.status_code == 200
            assert mind_map_resp.json()["projectId"] == "all"
            assert mind_map_resp.json()["status"] == "empty"
    finally:
        await runtime.stop()
        await engine.dispose()
