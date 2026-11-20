"""RED-first security and behavioral tests for SAG Project Mind Map V1 API."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_api.api.v1 import api_router
from sag_api.core.config import Settings, settings
from sag_api.core.deps import get_llm, get_session
from sag_api.core.error_taxonomy import ErrorCode, ErrorLayer, ErrorStage
from sag_api.core.errors import ApiError, ServiceUnavailableError
from sag_api.core.security import create_access_token
from sag_api.db.base import Base
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    SearchUnit,
    Source,
    SourceProjectMapping,
)
from sag_api.enums import DocumentStatus
from sag_api.generation import llm as llm_module
from sag_api.generation.llm import LLMClient


class FakeMindMapLLM:
    """Controllable LLM stub for Mind Map synthesis tests."""

    def __init__(self, response: str | Exception = "{}") -> None:
        self.configured = True
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append({"messages": messages, "response_format": response_format})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _bff_headers(
    *,
    token: str = "test-bff-secret",
    user_id: str = "651a2b3c4d5e6f7a8b9c0d01",
    org_id: str = "org_alpha",
    tenant_id: str | None = None,
    project_id: str = "651a2b3c4d5e6f7a8b9c0d99",
    partition_id: str | None = None,
) -> dict[str, str]:
    resolved_tenant = org_id if tenant_id is None else tenant_id
    resolved_partition = project_id if partition_id is None else partition_id
    return {
        "X-SAG-BFF-Service-Token": token,
        "X-Continuum-User-Id": user_id,
        "X-Continuum-Organization-Id": org_id,
        "X-Continuum-Tenant-Id": resolved_tenant,
        "X-Continuum-Project-Id": project_id,
        "X-Continuum-Security-Partition": resolved_partition,
    }


def _direct_user_jwt_headers(
    *,
    user_id: str = "651a2b3c4d5e6f7a8b9c0d01",
    org_id: str = "org_alpha",
    project_id: str = "651a2b3c4d5e6f7a8b9c0d99",
) -> dict[str, str]:
    token = create_access_token(
        user_id,
        extra={
            "org_id": org_id,
            "tenant_id": org_id,
            "project_id": project_id,
            "security_partition": project_id,
            "jti": secrets.token_urlsafe(12),
        },
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_ready_document(
    session: AsyncSession,
    *,
    source_id: str,
    doc_id: str,
    ver_id: str,
    org_id: str,
    project_id: str,
    partition_id: str | None = None,
    filename: str,
    logical_source_id: str | None = None,
    version_no: int = 1,
    search_status: str = "SEARCH_READY",
    doc_status: DocumentStatus = DocumentStatus.READY,
    is_active: bool = True,
    sections: list[tuple[str, str, int]] | None = None,
) -> None:
    sec_part = partition_id or project_id
    doc = Document(
        id=doc_id,
        tenant_id=org_id,
        project_id=project_id,
        source_id=source_id,
        logical_source_id=logical_source_id,
        filename=filename,
        storage_path=f"/tmp/{doc_id}_{filename}",
        status=doc_status,
        is_active=is_active,
    )
    ver = DocumentVersion(
        id=ver_id,
        document_id=doc_id,
        version_no=version_no,
        file_hash=hashlib.sha256(f"{doc_id}:{ver_id}".encode()).hexdigest(),
        status="REVOKED" if search_status == "REVOKED" else "READY",
        search_status=search_status,
        knowledge_status="READY",
        search_ready_at=datetime.now(UTC) if search_status in ("READY", "SEARCH_READY") else None,
        metadata_json={"security_partition_id": sec_part},
    )
    session.add_all([doc, ver])
    await session.flush()

    for idx, (section_path, text, page_no) in enumerate(sections or [], start=1):
        blk_id = f"{ver_id}_blk_{idx}"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        block = CanonicalBlock(
            id=blk_id,
            document_version_id=ver_id,
            ordinal=idx,
            block_type="paragraph",
            page_from=page_no,
            page_to=page_no,
            section_path=section_path,
            source_anchor=f"p{page_no}-b{idx}",
            normalized_text=text,
            content_hash=content_hash,
        )
        unit = SearchUnit(
            id=f"{ver_id}_su_{idx}",
            document_version_id=ver_id,
            block_from_id=blk_id,
            block_to_id=blk_id,
            security_partition_id=sec_part,
            content_hash=content_hash,
            token_count=max(1, len(text.split())),
            page_from=page_no,
            page_to=page_no,
            section_path=section_path,
        )
        session.add_all([block, unit])


def _count_tree_nodes(node: dict[str, Any]) -> tuple[int, int, int]:
    """Return (total_nodes, max_depth, max_children_on_any_node)."""

    def _walk(curr: dict[str, Any], depth: int) -> tuple[int, int, int]:
        children = curr.get("children") or []
        total = 1
        max_d = depth
        max_c = len(children)
        for child in children:
            c_total, c_depth, c_children = _walk(child, depth + 1)
            total += c_total
            max_d = max(max_d, c_depth)
            max_c = max(max_c, c_children)
        return total, max_d, max_c

    return _walk(node, 0)


@pytest.fixture
async def mind_map_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_mode", "continuum")
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-secret")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(conn, _rec):
        conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = FakeMindMapLLM()

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _api_err(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    app.include_router(api_router)

    async def _override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_llm] = lambda: llm

    project_alpha = "651a2b3c4d5e6f7a8b9c0d99"
    project_beta = "651a2b3c4d5e6f7a8b9c0d88"

    async with session_factory() as s:
        s.add_all(
            [
                Source(id="src_alpha_ok", name="Alpha Knowledge", sag_source_config_id="cfg_a1"),
                SourceProjectMapping(
                    id="spm_alpha_ok",
                    source_id="src_alpha_ok",
                    organization_id="org_alpha",
                    project_id=project_alpha,
                    state="CONFIRMED",
                    confirmed_at=datetime.now(UTC),
                    confirmed_by="admin",
                    approval_ref="apr_alpha",
                ),
                Source(id="src_alpha_pending_map", name="Unconfirmed", sag_source_config_id="cfg_a2"),
                SourceProjectMapping(
                    id="spm_alpha_pending",
                    source_id="src_alpha_pending_map",
                    organization_id="org_alpha",
                    project_id=project_alpha,
                    state="PENDING",
                ),
                Source(id="src_beta_ok", name="Beta Knowledge", sag_source_config_id="cfg_b1"),
                SourceProjectMapping(
                    id="spm_beta_ok",
                    source_id="src_beta_ok",
                    organization_id="org_alpha",
                    project_id=project_beta,
                    state="CONFIRMED",
                    confirmed_at=datetime.now(UTC),
                    confirmed_by="admin",
                    approval_ref="apr_beta",
                ),
            ]
        )
        await s.commit()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sag.test") as client:
        yield SimpleNamespace(
            app=app,
            client=client,
            session_factory=session_factory,
            llm=llm,
            project_alpha=project_alpha,
            project_beta=project_beta,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_mind_map_enforces_bff_service_boundary(mind_map_env) -> None:
    client: httpx.AsyncClient = mind_map_env.client
    pid = mind_map_env.project_alpha
    url = f"/api/v1/projects/{pid}/mind-map"

    # 1. Missing all auth headers -> 403
    r_none = await client.post(url, json={})
    assert r_none.status_code == 403

    # 2. Invalid BFF token -> 403
    r_bad_tok = await client.post(url, headers=_bff_headers(token="wrong", project_id=pid), json={})
    assert r_bad_tok.status_code == 403

    # 3. Direct user Bearer JWT without BFF service token -> 403 (no bypass!)
    r_jwt = await client.post(url, headers=_direct_user_jwt_headers(project_id=pid), json={})
    assert r_jwt.status_code == 403

    # 4. Header project_id mismatch with route project_id -> 403
    r_proj_mismatch = await client.post(
        url,
        headers=_bff_headers(project_id=mind_map_env.project_beta),
        json={},
    )
    assert r_proj_mismatch.status_code == 403

    # 5. Tenant header mismatch with organization -> 403
    r_tenant_mismatch = await client.post(
        url,
        headers=_bff_headers(project_id=pid, tenant_id="org_evil"),
        json={},
    )
    assert r_tenant_mismatch.status_code == 403


@pytest.mark.asyncio
async def test_mind_map_validates_strict_body_and_topic_bounds(mind_map_env) -> None:
    client: httpx.AsyncClient = mind_map_env.client
    pid = mind_map_env.project_alpha
    headers = _bff_headers(project_id=pid)
    url = f"/api/v1/projects/{pid}/mind-map"

    # Unknown body key (e.g. client trying to inject sourceIds) -> 400
    r_extra = await client.post(url, headers=headers, json={"topic": "Auth", "sourceIds": ["src_1"]})
    assert r_extra.status_code == 400

    # Whitespace-only topic -> 400
    r_ws = await client.post(url, headers=headers, json={"topic": "    \t  "})
    assert r_ws.status_code == 400

    # Topic > 200 trimmed chars -> 400
    r_long = await client.post(url, headers=headers, json={"topic": "a" * 201})
    assert r_long.status_code == 400

    # Non-object JSON -> 400
    r_arr = await client.post(url, headers=headers, json=["not", "an", "object"])
    assert r_arr.status_code == 400

    # Unknown query parameter -> 400
    r_query = await client.post(f"{url}?debug=true", headers=headers, json={})
    assert r_query.status_code == 400


@pytest.mark.asyncio
async def test_mind_map_empty_project_skips_llm_and_reports_pending_docs(mind_map_env) -> None:
    pid = mind_map_env.project_alpha
    async with mind_map_env.session_factory() as s:
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_pending_1",
            ver_id="ver_pending_1",
            org_id="org_alpha",
            project_id=pid,
            filename="draft_spec.pdf",
            search_status="PENDING",
            doc_status=DocumentStatus.LOADING,
            sections=[],
        )
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_pending_2",
            ver_id="ver_pending_2",
            org_id="org_alpha",
            project_id=pid,
            filename="uploading.docx",
            search_status="PENDING",
            doc_status=DocumentStatus.PENDING,
            sections=[],
        )
        await s.commit()

    resp = await mind_map_env.client.post(
        f"/api/v1/projects/{pid}/mind-map",
        headers=_bff_headers(project_id=pid),
        content=b"",
    )
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
    body = resp.json()
    assert body["projectId"] == pid
    assert body["topic"] is None
    assert body["status"] == "empty"
    assert body["generationMode"] == "none"
    assert body["fallbackReason"] is None
    assert body["tree"] is None
    assert body["citations"] == []
    assert len(body["scopeFingerprint"]) == 64
    assert body["coverage"] == {
        "accessibleReadyDocumentCount": 0,
        "usedDocumentCount": 0,
        "omittedDocumentCount": 0,
        "pendingDocumentCount": 2,
        "truncatedDocumentCount": 0,
        "evidenceUnitCount": 0,
    }
    assert mind_map_env.llm.calls == []


@pytest.mark.asyncio
async def test_mind_map_acl_isolation_clamps_tree_and_prunes_unsupported_nodes(mind_map_env) -> None:
    pid = mind_map_env.project_alpha
    continuum_doc_1 = "651a2b3c4d5e6f7a8b9c0d11"
    continuum_doc_2 = "651a2b3c4d5e6f7a8b9c0d22"

    async with mind_map_env.session_factory() as s:
        # Authorized ready doc 1
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_alpha_1",
            ver_id="ver_alpha_1",
            org_id="org_alpha",
            project_id=pid,
            logical_source_id=f"continuum:project_document:{continuum_doc_1}",
            filename="system-architecture.md",
            version_no=2,
            sections=[
                ("1. Overview > 1.1 BFF Gateway", "NestJS BFF enforces cookie CSRF and RBAC.", 1),
                ("2. Storage > 2.1 PostgreSQL", "SAG stores SearchUnits in PostgreSQL 16.", 3),
            ],
        )
        # Authorized ready doc 2
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_alpha_2",
            ver_id="ver_alpha_2",
            org_id="org_alpha",
            project_id=pid,
            logical_source_id=f"project-document:{continuum_doc_2}",
            filename="security-runbook.md",
            version_no=1,
            sections=[
                ("1. Token Policy", "Service tokens use constant-time digest checks.", 2),
            ],
        )
        # Revoked version in same project -> MUST be excluded
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_alpha_revoked",
            ver_id="ver_alpha_revoked",
            org_id="org_alpha",
            project_id=pid,
            filename="revoked-secret.md",
            search_status="REVOKED",
            sections=[("Secret", "TOP SECRET REVOKED TEXT", 1)],
        )
        # Unconfirmed SourceProjectMapping -> MUST be excluded
        await _seed_ready_document(
            s,
            source_id="src_alpha_pending_map",
            doc_id="doc_unconfirmed",
            ver_id="ver_unconfirmed",
            org_id="org_alpha",
            project_id=pid,
            filename="unconfirmed.md",
            sections=[("Hidden", "UNCONFIRMED SOURCE TEXT", 1)],
        )
        # Other project (project_beta) -> MUST be excluded
        await _seed_ready_document(
            s,
            source_id="src_beta_ok",
            doc_id="doc_beta_1",
            ver_id="ver_beta_1",
            org_id="org_alpha",
            project_id=mind_map_env.project_beta,
            filename="beta-roadmap.md",
            sections=[("Beta", "CROSS PROJECT SECRET", 1)],
        )
        await s.commit()

    # Build an oversized, over-deep LLM tree with invalid citations mixed in
    deep_chain = {
        "id": "lvl1_arch",
        "label": "Kiến trúc hệ thống",
        "summary": "Tổng quan BFF và lưu trữ",
        "citationNumbers": [1],
        "children": [
            {
                "id": "lvl2_bff",
                "label": "Tầng NestJS BFF",
                "summary": "Kiểm tra CSRF và phân quyền",
                "citationNumbers": [1, 999],
                "children": [
                    {
                        "id": "lvl3_csrf",
                        "label": "Xác thực Cookie CSRF",
                        "summary": "Bảo vệ POST request",
                        "citationNumbers": [1],
                        "children": [
                            {
                                "id": "lvl4_too_deep",
                                "label": "Tầng 5 vượt giới hạn",
                                "summary": "Phải bị cắt bỏ vì tối đa 4 tầng (depth 0..3)",
                                "citationNumbers": [1],
                                "children": [],
                            }
                        ],
                    },
                    {
                        "id": "lvl3_hallucinated",
                        "label": "Nhánh bịa nguồn [888]",
                        "summary": "Không có nguồn hợp lệ trong catalog",
                        "citationNumbers": [888],
                        "children": [],
                    },
                ],
            }
        ],
    }
    # Add 9 more top-level siblings to verify max 8 children per node & <= 60 total nodes
    many_siblings = [deep_chain] + [
        {
            "id": f"sib_{i}",
            "label": f"Chuyên đề bảo mật #{i}",
            "summary": "Đối chiếu token dịch vụ",
            "citationNumbers": [2 if i % 2 == 0 else 3],
            "children": [
                {
                    "id": f"sib_{i}_c_{j}",
                    "label": f"Mục con {i}.{j}",
                    "summary": "Chi tiết thực thi",
                    "citationNumbers": [2],
                    "children": [],
                }
                for j in range(10)
            ],
        }
        for i in range(1, 10)
    ]
    mind_map_env.llm.response = "```json\n" + json.dumps(
        {
            "tree": {
                "id": "root",
                "label": "Bản đồ kiến trúc Alpha",
                "summary": "Tổng hợp từ 2 tài liệu chuẩn",
                "citationNumbers": [],
                "children": many_siblings,
            }
        },
        ensure_ascii=False,
    ) + "\n```"

    resp = await mind_map_env.client.post(
        f"/api/v1/projects/{pid}/mind-map",
        headers=_bff_headers(project_id=pid),
        json={"topic": "  Kiến trúc & Bảo mật  "},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["generationMode"] == "llm"
    assert body["fallbackReason"] is None
    assert body["topic"] == "Kiến trúc & Bảo mật"
    assert body["coverage"] == {
        "accessibleReadyDocumentCount": 2,
        "usedDocumentCount": 2,
        "omittedDocumentCount": 0,
        "pendingDocumentCount": 0,
        "truncatedDocumentCount": 0,
        "evidenceUnitCount": 3,
    }

    # Verify prompt sent to LLM never leaked revoked, unconfirmed, or cross-project text
    sent_prompt = json.dumps(mind_map_env.llm.calls[0]["messages"], ensure_ascii=False)
    assert "TOP SECRET REVOKED TEXT" not in sent_prompt
    assert "UNCONFIRMED SOURCE TEXT" not in sent_prompt
    assert "CROSS PROJECT SECRET" not in sent_prompt

    tree = body["tree"]
    total_nodes, max_depth, max_children = _count_tree_nodes(tree)
    assert total_nodes <= 60
    assert max_depth <= 3  # depth 0..3 == 4 levels max
    assert max_children <= 8

    # Verify unsupported leaf ("Nhánh bịa nguồn [888]") and depth-4 node were pruned
    serialized_tree = json.dumps(tree, ensure_ascii=False)
    assert "Nhánh bịa nguồn" not in serialized_tree
    assert "Tầng 5 vượt giới hạn" not in serialized_tree

    # Verify Continuum 24-hex document IDs extracted cleanly for frontend deep-linking
    citations = body["citations"]
    assert len(citations) >= 2
    doc_ids = {c["documentId"] for c in citations}
    assert continuum_doc_1 in doc_ids
    assert continuum_doc_2 in doc_ids
    valid_cit_nums = {c["number"] for c in citations}

    def _assert_node_citations(node: dict[str, Any], is_root: bool = False) -> None:
        nums = node["citationNumbers"]
        assert set(nums).issubset(valid_cit_nums)
        if not is_root and not node["children"]:
            assert len(nums) >= 1
        for child in node["children"]:
            _assert_node_citations(child, False)

    _assert_node_citations(tree, is_root=True)


@pytest.mark.asyncio
async def test_mind_map_outline_fallback_on_llm_failure_and_invalid_json(mind_map_env) -> None:
    pid = mind_map_env.project_alpha
    async with mind_map_env.session_factory() as s:
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_fb_1",
            ver_id="ver_fb_1",
            org_id="org_alpha",
            project_id=pid,
            filename="operations-guide.md",
            sections=[
                ("1. Deployment > 1.1 Rolling Update", "Zero-downtime rolling deployment steps.", 1),
                ("2. Rollback > 2.1 Instant Revert", "Revert API container before DB changes.", 4),
            ],
        )
        await s.commit()

    # Case 1: LLM raises ServiceUnavailableError -> outline_fallback (LLM_UNAVAILABLE)
    mind_map_env.llm.response = ServiceUnavailableError(
        "Provider rate limited",
        code=ErrorCode.LLM_UNAVAILABLE,
        layer=ErrorLayer.LLM,
        stage=ErrorStage.GENERATE,
    )
    r_unavail = await mind_map_env.client.post(
        f"/api/v1/projects/{pid}/mind-map",
        headers=_bff_headers(project_id=pid),
        json={},
    )
    assert r_unavail.status_code == 200
    b_unavail = r_unavail.json()
    assert b_unavail["status"] == "ready"
    assert b_unavail["generationMode"] == "outline_fallback"
    assert b_unavail["fallbackReason"] == "LLM_UNAVAILABLE"
    assert b_unavail["tree"] is not None
    assert len(b_unavail["tree"]["children"]) >= 1
    assert len(b_unavail["citations"]) >= 1
    total_nodes, max_depth, max_children = _count_tree_nodes(b_unavail["tree"])
    assert total_nodes <= 60
    assert max_depth <= 3
    assert max_children <= 8

    # Case 2: LLM returns malformed non-JSON -> outline_fallback (LLM_INVALID_OUTPUT)
    mind_map_env.llm.response = "Sorry, I cannot output JSON today."
    r_invalid = await mind_map_env.client.post(
        f"/api/v1/projects/{pid}/mind-map",
        headers=_bff_headers(project_id=pid),
        json={},
    )
    assert r_invalid.status_code == 200
    b_invalid = r_invalid.json()
    assert b_invalid["status"] == "ready"
    assert b_invalid["generationMode"] == "outline_fallback"
    assert b_invalid["fallbackReason"] == "LLM_INVALID_OUTPUT"
    assert b_invalid["tree"] is not None


@pytest.mark.asyncio
async def test_mind_map_database_failure_returns_503_never_fallback(
    mind_map_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = mind_map_env.project_alpha

    async def _broken_session():
        class BrokenSession:
            async def execute(self, *_args, **_kwargs):
                raise OperationalError("SELECT 1", {}, Exception("db down"))

            async def scalars(self, *_args, **_kwargs):
                raise OperationalError("SELECT 1", {}, Exception("db down"))

        yield BrokenSession()

    mind_map_env.app.dependency_overrides[get_session] = _broken_session
    resp = await mind_map_env.client.post(
        f"/api/v1/projects/{pid}/mind-map",
        headers=_bff_headers(project_id=pid),
        json={},
    )
    assert resp.status_code == 503
    assert resp.json().get("generationMode") is None


@pytest.mark.asyncio
async def test_mind_map_coverage_reports_omitted_and_truncated_documents(mind_map_env) -> None:
    pid = mind_map_env.project_alpha
    async with mind_map_env.session_factory() as s:
        for i in range(1, 27):
            is_target = i == 26
            text = (
                ("ZeroTrustRotation " * 120)
                if is_target
                else f"Generic handbook content for module {i}."
            )
            await _seed_ready_document(
                s,
                source_id="src_alpha_ok",
                doc_id=f"doc_cov_{i:02d}",
                ver_id=f"ver_cov_{i:02d}",
                org_id="org_alpha",
                project_id=pid,
                filename=f"module-{i:02d}.md",
                sections=[(f"Section {i}", text, 1)],
            )
        await s.commit()

    mind_map_env.llm.response = json.dumps(
        {
            "tree": {
                "id": "root",
                "label": "ZeroTrustRotation",
                "summary": "Focused topic synthesis",
                "citationNumbers": [1],
                "children": [
                    {
                        "id": "c1",
                        "label": "Module 26 Rotation",
                        "summary": "Key rotation rule",
                        "citationNumbers": [1],
                        "children": [],
                    }
                ],
            }
        }
    )

    resp = await mind_map_env.client.post(
        f"/api/v1/projects/{pid}/mind-map",
        headers=_bff_headers(project_id=pid),
        json={"topic": "ZeroTrustRotation"},
    )
    assert resp.status_code == 200
    body = resp.json()
    cov = body["coverage"]
    assert cov["accessibleReadyDocumentCount"] == 26
    assert cov["usedDocumentCount"] == 24
    assert cov["omittedDocumentCount"] == 2
    assert cov["truncatedDocumentCount"] >= 1
    assert any(c["documentName"] == "module-26.md" for c in body["citations"])


@pytest.mark.asyncio
@pytest.mark.parametrize("match_in", ["content", "heading"])
async def test_mind_map_topic_ranks_late_units_before_per_document_limit(
    mind_map_env,
    match_in: str,
) -> None:
    pid = mind_map_env.project_alpha
    topic = "EmergencyKeyRotation"
    late_marker = "LateAuthorizedEvidence"
    hidden_marker = "ForbiddenPartitionEvidence"
    sections = [
        (f"General section {i}", f"Routine handbook introduction {i}.", i)
        for i in range(1, 17)
    ]
    sections.extend(
        [
            (
                topic if match_in == "heading" else "Credential operations",
                f"{late_marker}: {topic if match_in == 'content' else 'This procedure'} requires two approvers.",
                17,
            ),
            (topic, f"{hidden_marker}: {topic} " * 30, 18),
        ]
    )
    async with mind_map_env.session_factory() as s:
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_late_topic",
            ver_id="ver_late_topic",
            org_id="org_alpha",
            project_id=pid,
            filename="operations-handbook.md",
            sections=sections,
        )
        hidden_unit = await s.get(SearchUnit, "ver_late_topic_su_18")
        assert hidden_unit is not None
        hidden_unit.security_partition_id = "forbidden-partition"
        await s.commit()

    response = await mind_map_env.client.post(
        f"/api/v1/projects/{pid}/mind-map",
        headers=_bff_headers(project_id=pid),
        json={"topic": topic},
    )
    assert response.status_code == 200
    assert len(mind_map_env.llm.calls) == 1
    user_prompt = mind_map_env.llm.calls[0]["messages"][1]["content"]
    evidence_prompt = user_prompt.split("Numbered authorized evidence excerpts:", 1)[1]
    assert late_marker in evidence_prompt
    assert hidden_marker not in user_prompt
    coverage = response.json()["coverage"]
    assert coverage["accessibleReadyDocumentCount"] == 1
    assert coverage["usedDocumentCount"] == 1
    assert 1 <= coverage["evidenceUnitCount"] <= 16
    assert coverage["truncatedDocumentCount"] == 1


@pytest.mark.asyncio
async def test_llm_client_complete_structured_output_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Settings(
        llm_provider="openai",
        llm_api_key="sk-test",
        llm_model="deepseek-v4-flash",
        llm_base_url="https://api.example.invalid/v1",
        llm_structured_output_mode="auto",
    )
    recorded_kwargs: list[dict[str, Any]] = []

    class UnsupportedSchemaError(Exception):
        status_code = 400

    async def _fake_acompletion(**kwargs: Any) -> Any:
        recorded_kwargs.append(kwargs)
        rf = kwargs.get("response_format")
        if isinstance(rf, dict) and rf.get("type") == "json_schema":
            raise UnsupportedSchemaError("response_format json_schema is not supported by this model")
        return {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

    monkeypatch.setattr(llm_module, "_litellm_completion", _fake_acompletion)
    client = LLMClient(cfg)

    # 1. Legacy call without response_format continues to work unchanged
    plain = await client.complete([{"role": "user", "content": "hi"}])
    assert plain == '{"ok": true}'
    assert "response_format" not in recorded_kwargs[0]

    # 2. json_schema call automatically downgrades to json_object on 400 unsupported
    structured = await client.complete(
        [{"role": "user", "content": "emit json"}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "mind_map", "schema": {"type": "object"}},
        },
    )
    assert structured == '{"ok": true}'
    assert recorded_kwargs[1]["response_format"]["type"] == "json_schema"
    assert recorded_kwargs[2]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_mind_map_get_and_post_persist_snapshot_across_project_users_until_explicit_refresh(
    mind_map_env,
) -> None:
    client: httpx.AsyncClient = mind_map_env.client
    session_factory = mind_map_env.session_factory
    llm: FakeMindMapLLM = mind_map_env.llm
    pid = mind_map_env.project_alpha
    url = f"/api/v1/projects/{pid}/mind-map"

    user_one = _bff_headers(user_id="651a2b3c4d5e6f7a8b9c0d01", project_id=pid)
    user_two = _bff_headers(user_id="651a2b3c4d5e6f7a8b9c0d02", project_id=pid)

    # 1. Zero ready docs -> GET returns empty immediately with 0 LLM calls
    r_empty = await client.get(url, headers=user_one)
    assert r_empty.status_code == 200
    assert r_empty.json()["status"] == "empty"
    assert r_empty.json()["cached"] is False
    assert len(llm.calls) == 0

    # 2. Seed ready doc -> GET returns uninitialized (never calls LLM)
    async with session_factory() as s:
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_persist_1",
            ver_id="ver_persist_1",
            org_id="org_alpha",
            project_id=pid,
            filename="system-overview.md",
            logical_source_id="continuum:project_document:507f1f77bcf86cd799439021",
            sections=[("Overview > Core", "Core architecture overview text.", 1)],
        )
        await s.commit()

    r_uninit = await client.get(url, headers=user_one)
    assert r_uninit.status_code == 200
    body_uninit = r_uninit.json()
    assert body_uninit["status"] == "uninitialized"
    assert body_uninit["tree"] is None
    assert body_uninit["coverage"]["accessibleReadyDocumentCount"] == 1
    assert len(llm.calls) == 0

    # 3. User 1 generates overview Mind Map -> persisted to DB
    llm.response = json.dumps(
        {
            "tree": {
                "id": "root",
                "label": "System Overview V1",
                "summary": "Initial persisted map",
                "citationNumbers": [],
                "children": [
                    {
                        "id": "b1",
                        "label": "Core Architecture",
                        "summary": "Core details",
                        "citationNumbers": [1],
                        "children": [],
                    }
                ],
            }
        }
    )
    r_gen1 = await client.post(url, headers=user_one, json={})
    assert r_gen1.status_code == 200
    gen1_body = r_gen1.json()
    assert gen1_body["status"] == "ready"
    assert gen1_body["cached"] is False
    assert gen1_body["hasSourceUpdates"] is False
    assert gen1_body["tree"]["label"] == "System Overview V1"
    assert len(llm.calls) == 1

    # 4. User 2 opens Mind Map -> instant GET hit, cached=True, 0 extra LLM calls
    r_get_u2 = await client.get(url, headers=user_two)
    assert r_get_u2.status_code == 200
    u2_body = r_get_u2.json()
    assert u2_body["status"] == "ready"
    assert u2_body["cached"] is True
    assert u2_body["hasSourceUpdates"] is False
    assert u2_body["tree"]["label"] == "System Overview V1"
    assert len(u2_body["citations"]) == 1
    assert len(llm.calls) == 1

    # POST with refresh=False also reuses saved snapshot without calling LLM
    r_post_cached = await client.post(url, headers=user_two, json={"refresh": False})
    assert r_post_cached.status_code == 200
    assert r_post_cached.json()["cached"] is True
    assert len(llm.calls) == 1

    # 5. New document uploaded -> GET still serves saved map immediately with hasSourceUpdates=True
    async with session_factory() as s:
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_persist_2",
            ver_id="ver_persist_2",
            org_id="org_alpha",
            project_id=pid,
            filename="deployment-guide.md",
            logical_source_id="continuum:project_document:507f1f77bcf86cd799439022",
            sections=[("Deployment > Docker", "Container rollout strategy.", 1)],
        )
        await s.commit()

    r_get_stale = await client.get(url, headers=user_two)
    assert r_get_stale.status_code == 200
    stale_body = r_get_stale.json()
    assert stale_body["status"] == "ready"
    assert stale_body["cached"] is True
    assert stale_body["hasSourceUpdates"] is True
    assert stale_body["coverage"]["accessibleReadyDocumentCount"] == 2
    assert stale_body["tree"]["label"] == "System Overview V1"
    assert len(llm.calls) == 1

    # 6. Explicit user refresh (POST refresh=True) regenerates and updates saved snapshot
    llm.response = json.dumps(
        {
            "tree": {
                "id": "root",
                "label": "System Overview V2 Refreshed",
                "summary": "Updated with deployment docs",
                "citationNumbers": [],
                "children": [
                    {
                        "id": "b1",
                        "label": "Core & Deployment",
                        "summary": "Combined view",
                        "citationNumbers": [1, 2],
                        "children": [],
                    }
                ],
            }
        }
    )
    r_regen = await client.post(url, headers=user_two, json={"refresh": True})
    assert r_regen.status_code == 200
    regen_body = r_regen.json()
    assert regen_body["cached"] is False
    assert regen_body["hasSourceUpdates"] is False
    assert regen_body["tree"]["label"] == "System Overview V2 Refreshed"
    assert len(llm.calls) == 2

    r_after_regen = await client.get(url, headers=user_one)
    assert r_after_regen.status_code == 200
    assert r_after_regen.json()["cached"] is True
    assert r_after_regen.json()["hasSourceUpdates"] is False
    assert r_after_regen.json()["tree"]["label"] == "System Overview V2 Refreshed"


@pytest.mark.asyncio
async def test_mind_map_saved_snapshot_auto_invalidates_when_used_document_is_revoked(
    mind_map_env,
) -> None:
    client: httpx.AsyncClient = mind_map_env.client
    session_factory = mind_map_env.session_factory
    llm: FakeMindMapLLM = mind_map_env.llm
    pid = mind_map_env.project_alpha
    url = f"/api/v1/projects/{pid}/mind-map"
    headers = _bff_headers(project_id=pid)

    async with session_factory() as s:
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_revoke_1",
            ver_id="ver_revoke_1",
            org_id="org_alpha",
            project_id=pid,
            filename="sensitive-spec.md",
            sections=[("Security", "Top secret token rotation details.", 1)],
        )
        await _seed_ready_document(
            s,
            source_id="src_alpha_ok",
            doc_id="doc_keep_2",
            ver_id="ver_keep_2",
            org_id="org_alpha",
            project_id=pid,
            filename="public-readme.md",
            sections=[("Intro", "Public project introduction.", 1)],
        )
        await s.commit()

    llm.response = json.dumps(
        {
            "tree": {
                "id": "root",
                "label": "Combined Map",
                "summary": "Includes sensitive spec",
                "citationNumbers": [],
                "children": [
                    {
                        "id": "sec",
                        "label": "Top Secret Rotation",
                        "summary": "Derived from doc_revoke_1",
                        "citationNumbers": [1],
                        "children": [],
                    }
                ],
            }
        }
    )
    r_gen = await client.post(url, headers=headers, json={})
    assert r_gen.status_code == 200
    assert r_gen.json()["status"] == "ready"

    # Revoke ver_revoke_1 -> saved snapshot MUST be invalidated immediately on GET
    async with session_factory() as s:
        ver = await s.get(DocumentVersion, "ver_revoke_1")
        assert ver is not None
        ver.search_status = "REVOKED"
        await s.commit()

    r_after_revoke = await client.get(url, headers=headers)
    assert r_after_revoke.status_code == 200
    revoked_check = r_after_revoke.json()
    assert revoked_check["status"] == "uninitialized"
    assert revoked_check["tree"] is None
    assert revoked_check["citations"] == []

    # Mismatched partition header is rejected with 403; sibling project never sees snapshot
    other_partition_headers = _bff_headers(project_id=pid, partition_id="isolated_partition")
    r_other_part = await client.get(url, headers=other_partition_headers)
    assert r_other_part.status_code == 403

    beta_url = f"/api/v1/projects/{mind_map_env.project_beta}/mind-map"
    r_beta = await client.get(beta_url, headers=_bff_headers(project_id=mind_map_env.project_beta))
    assert r_beta.status_code == 200
    assert r_beta.json()["status"] == "empty"


@pytest.mark.asyncio
async def test_mind_map_get_enforces_bff_auth_and_strict_query_validation(
    mind_map_env,
) -> None:
    client: httpx.AsyncClient = mind_map_env.client
    pid = mind_map_env.project_alpha
    url = f"/api/v1/projects/{pid}/mind-map"
    headers = _bff_headers(project_id=pid)

    assert (await client.get(url)).status_code == 403
    assert (
        await client.get(url, headers=_direct_user_jwt_headers(project_id=pid))
    ).status_code == 403
    assert (await client.get(f"{url}?extra=1", headers=headers)).status_code == 400
    assert (await client.get(f"{url}?topic=   ", headers=headers)).status_code == 400
    assert (
        await client.get(f"{url}?topic={'x' * 201}", headers=headers)
    ).status_code == 400
    # Unicode casefold expansion guard (101 'ß' -> 202 'ss' chars > VARCHAR(200) topic_key)
    llm_calls_before = len(mind_map_env.llm.calls)
    assert (
        await client.get(f"{url}?topic={'ß' * 101}", headers=headers)
    ).status_code == 400
    assert (
        await client.post(url, headers=headers, json={"topic": "ß" * 101})
    ).status_code == 400
    assert len(mind_map_env.llm.calls) == llm_calls_before
    # Exactly 100 'ß' -> 200 'ss' chars fits VARCHAR(200) cleanly
    assert (
        await client.get(f"{url}?topic={'ß' * 100}", headers=headers)
    ).status_code == 200
    assert (
        await client.post(url, headers=headers, json={"refresh": "yes"})
    ).status_code == 400


def test_citation_excerpt_preserves_multiline_markdown_and_closes_truncated_fences() -> None:
    from sag_api.generation.prompt import _citation_excerpt, build_citations
    from sag_api.sag import RetrievedSection

    markdown_with_diagram = (
        "6. Trực Quan Hóa Vũ Trụ Tri Thức 3D\n\n"
        "> ⭐ ĐIỂM NHẤN CỐT LÕI\n"
        "> Thay vì xem tri thức dưới dạng bảng phẳng.\n\n"
        "```\n"
        "┌─────────────────────────────┐\n"
        "│ 3D KNOWLEDGE GALAXY PIPELINE│\n"
        "└─────────────────────────────┘\n"
        "```"
    )
    excerpt = _citation_excerpt(markdown_with_diagram)
    assert "\n> ⭐ ĐIỂM NHẤN CỐT LÕI\n" in excerpt
    assert "\n```\n┌─────────────────────────────┐\n" in excerpt

    long_fenced = "Intro\n```text\n" + ("│ box row │\n" * 160) + "```"
    truncated = _citation_excerpt(long_fenced)
    assert truncated.endswith("…\n```")
    assert truncated.count("```") == 2

    sec = RetrievedSection(
        chunk_id="su_hex_1",
        search_unit_id="su_hex_1",
        heading="Architecture > 3D Universe",
        content=markdown_with_diagram,
        score=0.92,
        source_id="src_1",
        source_config_id="cfg_1",
        document_id="sag-uuid-doc-1",
        public_document_id="507f1f77bcf86cd799439011",
        document_version_id="ver_1",
        document_name="02_FRONTEND_NEXTJS.md",
        version_no=1,
        page_from=1,
        page_to=1,
        anchor="p1-b1",
        section_path="Architecture > 3D Universe",
        project_id="651a2b3c4d5e6f7a8b9c0d99",
    )
    built = build_citations([sec], {"cfg_1": {"id": "src_1", "name": "Docs"}})
    assert len(built) == 1
    assert built[0]["document_id"] == "sag-uuid-doc-1"
    assert built[0]["public_document_id"] == "507f1f77bcf86cd799439011"
    assert built[0]["project_id"] == "651a2b3c4d5e6f7a8b9c0d99"
    assert built[0]["chunk_id"] == "su_hex_1"
    assert "\n```\n" in built[0]["snippet"]


@pytest.mark.asyncio
async def test_mind_map_all_projects_stratifies_documents_and_populates_citation_project_id(
    mind_map_env,
) -> None:
    client: httpx.AsyncClient = mind_map_env.client
    session_factory = mind_map_env.session_factory
    llm: FakeMindMapLLM = mind_map_env.llm
    p_alpha = mind_map_env.project_alpha
    p_beta = mind_map_env.project_beta

    # Seed 26 docs in project_alpha and 2 docs in project_beta (newer in alpha so naive sort would starve beta)
    async with session_factory() as s:
        for i in range(1, 3):
            await _seed_ready_document(
                s,
                source_id="src_beta_ok",
                doc_id=f"doc_beta_strat_{i:02d}",
                ver_id=f"ver_beta_strat_{i:02d}",
                org_id="org_alpha",
                project_id=p_beta,
                logical_source_id=f"continuum:project_document:651a2b3c4d5e6f7a8b9c0b{i:02d}",
                filename=f"beta-service-{i:02d}.md",
                sections=[(f"Beta Section {i}", f"Beta service architecture details {i}.", 1)],
            )
        for i in range(1, 27):
            await _seed_ready_document(
                s,
                source_id="src_alpha_ok",
                doc_id=f"doc_alpha_strat_{i:02d}",
                ver_id=f"ver_alpha_strat_{i:02d}",
                org_id="org_alpha",
                project_id=p_alpha,
                logical_source_id=f"continuum:project_document:651a2b3c4d5e6f7a8b9c0a{i:02d}",
                filename=f"alpha-module-{i:02d}.md",
                sections=[(f"Alpha Section {i}", f"Alpha module implementation {i}.", 1)],
            )
        await s.commit()

    # Force outline_fallback so all 24 stratified docs are organized into multi-project clusters
    llm.response = ServiceUnavailableError(
        "Rate limit",
        code=ErrorCode.LLM_UNAVAILABLE,
        layer=ErrorLayer.LLM,
        stage=ErrorStage.GENERATE,
    )

    headers = {
        **_bff_headers(project_id="all", partition_id="all"),
        "X-Continuum-Allowed-Project-Ids": f"{p_alpha},{p_beta}",
    }
    resp = await client.post("/api/v1/projects/all/mind-map", headers=headers, json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["projectId"] == "all"
    assert body["status"] == "ready"
    assert body["generationMode"] == "outline_fallback"
    assert body["coverage"]["accessibleReadyDocumentCount"] == 28
    assert body["coverage"]["usedDocumentCount"] == 24
    cited_projects = {c.get("projectId") for c in body["citations"]}
    assert p_alpha in cited_projects
    assert p_beta in cited_projects
