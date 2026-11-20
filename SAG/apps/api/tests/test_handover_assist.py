from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sag_api.api.v1 import api_router
from sag_api.core.config import settings
from sag_api.core.deps import get_llm, get_session
from sag_api.core.errors import ApiError, ServiceUnavailableError


def _build_app(llm_mock: object) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    app.include_router(api_router)
    app.dependency_overrides[get_llm] = lambda: llm_mock

    async def _fake_session():
        yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))

    app.dependency_overrides[get_session] = _fake_session
    return app


def _bff_headers(
    *,
    token: str = "test-bff-token",
    user_id: str = "651a2b3c4d5e6f7a8b9c0d11",
    org_id: str = "651a2b3c4d5e6f7a8b9c0d10",
    project_id: str = "751a2b3c4d5e6f7a8b9c0d20",
    partition_id: str | None = None,
) -> dict[str, str]:
    return {
        "X-SAG-BFF-Service-Token": token,
        "X-Continuum-User-Id": user_id,
        "X-Continuum-Organization-Id": org_id,
        "X-Continuum-Tenant-Id": org_id,
        "X-Continuum-Project-Id": project_id,
        "X-Continuum-Security-Partition": partition_id or project_id,
    }


@pytest.mark.asyncio
async def test_handover_assist_enforces_bff_token_and_project_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    llm = SimpleNamespace(configured=True, complete=AsyncMock(return_value="{}"))
    app = _build_app(llm)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Wrong BFF token -> 403
        bad_token = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(token="wrong-token"),
            json={"task": "SOURCE_PROPOSAL", "query": "deployment", "sources": []},
        )
        assert bad_token.status_code == 403

        # Cross-project header mismatch -> 403
        cross_project = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d99/handover/assist",
            headers=_bff_headers(project_id="751a2b3c4d5e6f7a8b9c0d20"),
            json={"task": "SOURCE_PROPOSAL", "query": "deployment", "sources": []},
        )
        assert cross_project.status_code == 403


@pytest.mark.asyncio
async def test_handover_assist_rejects_task_note_in_official_proposal_and_qa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    llm = SimpleNamespace(configured=True, complete=AsyncMock(return_value="{}"))
    app = _build_app(llm)
    task_note_source = {
        "kind": "TASK_NOTE",
        "id": "851a2b3c4d5e6f7a8b9c0d30",
        "version": 1,
        "title": "Task scratch note",
        "excerpts": [
            {
                "locatorKind": "SECTION",
                "locatorValue": "whatDone",
                "heading": "What Done",
                "content": "Refactored worker retry loop.",
            }
        ],
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        proposal_res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={"task": "SOURCE_PROPOSAL", "query": "worker retry", "sources": [task_note_source]},
        )
        assert proposal_res.status_code == 422

        qa_res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={
                "task": "QUESTION_ANSWER",
                "query": "How does worker retry work?",
                "sources": [task_note_source],
            },
        )
        assert qa_res.status_code == 422


@pytest.mark.asyncio
async def test_handover_assist_source_proposal_only_returns_authorized_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    llm_payload = json.dumps(
        {
            "candidates": [
                {
                    "evidenceIndex": 1,
                    "reason": "Covers production Kubernetes deployment runbook owned by departing lead.",
                },
                {
                    # Out-of-range hallucinated index must be dropped, never fabricated
                    "evidenceIndex": 99,
                    "reason": "Hallucinated source",
                },
            ]
        }
    )
    llm = SimpleNamespace(configured=True, complete=AsyncMock(return_value=llm_payload))
    app = _build_app(llm)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={
                "task": "SOURCE_PROPOSAL",
                "query": "kubernetes deployment runbook",
                "sources": [
                    {
                        "kind": "PROJECT_DOCUMENT",
                        "id": "851a2b3c4d5e6f7a8b9c0d31",
                        "version": 3,
                        "title": "K8s Deployment Runbook",
                        "excerpts": [
                            {
                                "locatorKind": "SECTION",
                                "locatorValue": "rollout",
                                "heading": "Rollout Procedure",
                                "content": "Run helm upgrade with canary values and verify readiness probes.",
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["task"] == "SOURCE_PROPOSAL"
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["source"] == {
            "kind": "PROJECT_DOCUMENT",
            "id": "851a2b3c4d5e6f7a8b9c0d31",
            "version": 3,
        }


@pytest.mark.asyncio
async def test_handover_assist_draft_generation_validates_citations_strictly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    llm_payload = json.dumps(
        {
            "items": [
                {
                    "title": "Release Rollout & Rollback Ownership",
                    "body": "Use canary rollout via Helm and rollback immediately on readiness failure [1].",
                    "requirement": "Confirm ability to execute canary rollout and rollback procedure in staging.",
                    "evidenceIndices": [1, 999],
                }
            ]
        }
    )
    llm = SimpleNamespace(configured=True, complete=AsyncMock(return_value=llm_payload))
    app = _build_app(llm)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={
                "task": "DRAFT_GENERATION",
                "query": "Summarize deployment ownership",
                "sources": [
                    {
                        "kind": "KNOWLEDGE_OBJECT",
                        "id": "851a2b3c4d5e6f7a8b9c0d32",
                        "version": 2,
                        "title": "Canary Rollout Standard",
                        "excerpts": [
                            {
                                "locatorKind": "SECTION",
                                "locatorValue": "sec-canary",
                                "heading": "Canary Rollout",
                                "content": "Helm canary rollout uses 10% traffic weight before full promotion.",
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["task"] == "DRAFT_GENERATION"
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["sources"] == [{"kind": "KNOWLEDGE_OBJECT", "id": "851a2b3c4d5e6f7a8b9c0d32", "version": 2}]
        assert item["citations"] == [
            {
                "source": {"kind": "KNOWLEDGE_OBJECT", "id": "851a2b3c4d5e6f7a8b9c0d32", "version": 2},
                "locator": {"kind": "SECTION", "value": "sec-canary"},
            }
        ]


@pytest.mark.asyncio
async def test_handover_assist_question_answer_fails_closed_on_insufficient_evidence_and_llm_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")

    # 1. Empty sources -> INSUFFICIENT_EVIDENCE without calling LLM or inventing sections
    llm = SimpleNamespace(configured=True, complete=AsyncMock(return_value="{}"))
    app = _build_app(llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        empty_res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={"task": "QUESTION_ANSWER", "query": "What is the production DB password?", "sources": []},
        )
        assert empty_res.status_code == 200
        empty_body = empty_res.json()
        assert empty_body["answerStatus"] == "INSUFFICIENT_EVIDENCE"
        assert empty_body["sections"] == []
        llm.complete.assert_not_awaited()

    # 2. Grounded ANSWERED response with verified citations
    answered_llm = SimpleNamespace(
        configured=True,
        complete=AsyncMock(
            return_value=json.dumps(
                {
                    "answerStatus": "ANSWERED",
                    "message": "Found grounded guidance in the authorized handover sources.",
                    "sections": [
                        {
                            "text": "Staging validation requires verifying readiness probes for 15 minutes [1].",
                            "evidenceIndices": [1],
                        }
                    ],
                }
            )
        ),
    )
    app_answered = _build_app(answered_llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_answered), base_url="http://test") as client:
        ans_res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={
                "task": "QUESTION_ANSWER",
                "query": "How long must readiness probes be verified in staging?",
                "sources": [
                    {
                        "kind": "PROJECT_DOCUMENT",
                        "id": "851a2b3c4d5e6f7a8b9c0d31",
                        "version": 3,
                        "title": "Staging Validation Guide",
                        "excerpts": [
                            {
                                "locatorKind": "PAGE",
                                "locatorValue": "4",
                                "heading": "Readiness Verification",
                                "content": "Verify readiness probes for 15 minutes in staging before promotion.",
                            }
                        ],
                    }
                ],
            },
        )
        assert ans_res.status_code == 200
        ans_body = ans_res.json()
        assert ans_body["answerStatus"] == "ANSWERED"
        assert len(ans_body["sections"]) == 1
        assert ans_body["sections"][0]["citations"] == [
            {
                "source": {"kind": "PROJECT_DOCUMENT", "id": "851a2b3c4d5e6f7a8b9c0d31", "version": 3},
                "locator": {"kind": "PAGE", "value": "4"},
            }
        ]

    # 3. Unconfigured / unavailable LLM -> propagates 503/500 error, NEVER fakes success
    failing_llm = SimpleNamespace(
        configured=False,
        complete=AsyncMock(side_effect=ServiceUnavailableError("LLM provider timeout")),
    )
    app_failing = _build_app(failing_llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_failing), base_url="http://test") as client:
        fail_res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={
                "task": "DRAFT_GENERATION",
                "query": "Generate handover draft",
                "sources": [
                    {
                        "kind": "PROJECT_DOCUMENT",
                        "id": "851a2b3c4d5e6f7a8b9c0d31",
                        "version": 3,
                        "title": "Staging Validation Guide",
                        "excerpts": [
                            {
                                "locatorKind": "PAGE",
                                "locatorValue": "4",
                                "heading": "Readiness Verification",
                                "content": "Verify readiness probes for 15 minutes.",
                            }
                        ],
                    }
                ],
            },
        )
        assert fail_res.status_code in {500, 503}

    # 4. ANSWERED with only unverified citations downgrades cleanly without contradictory message
    unverified_llm = SimpleNamespace(
        configured=True,
        complete=AsyncMock(
            return_value=json.dumps(
                {
                    "answerStatus": "ANSWERED",
                    "message": "Found complete answer in authorized sources.",
                    "sections": [
                        {
                            "text": "Unverified claim citing nonexistent index [999].",
                            "evidenceIndices": [999],
                        }
                    ],
                }
            )
        ),
    )
    app_unverified = _build_app(unverified_llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_unverified), base_url="http://test") as client:
        downgraded_res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={
                "task": "QUESTION_ANSWER",
                "query": "What is the escalation SLA?",
                "sources": [
                    {
                        "kind": "PROJECT_DOCUMENT",
                        "id": "851a2b3c4d5e6f7a8b9c0d31",
                        "version": 3,
                        "title": "Staging Validation Guide",
                        "excerpts": [
                            {
                                "locatorKind": "PAGE",
                                "locatorValue": "4",
                                "heading": "Readiness Verification",
                                "content": "Verify readiness probes for 15 minutes.",
                            }
                        ],
                    }
                ],
            },
        )
        assert downgraded_res.status_code == 200
        downgraded_body = downgraded_res.json()
        assert downgraded_body["answerStatus"] == "INSUFFICIENT_EVIDENCE"
        assert downgraded_body["sections"] == []
        assert "Found complete answer" not in downgraded_body["message"]


@pytest.mark.asyncio
async def test_handover_assist_distributes_evidence_budget_fairly_and_reports_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sag_api.schemas.handover_assist import HandoverAssistSourceInput
    from sag_api.services import handover_assist_service

    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")

    # 1. Exact F10 reproduction: 5 sources x 25 excerpts with cap=80 -> [16, 16, 16, 16, 16] (never [25, 25, 25, 5, 0])
    five_sources_raw = [
        {
            "kind": "PROJECT_DOCUMENT",
            "id": f"851a2b3c4d5e6f7a8b9c0d{i + 10:02d}",
            "version": 1,
            "title": f"Runbook Source #{i + 1}",
            "excerpts": [
                {
                    "locatorKind": "SECTION",
                    "locatorValue": f"sec-{j + 1}",
                    "heading": f"Section {j + 1}",
                    "content": f"Source {i + 1} operational detail #{j + 1}.",
                }
                for j in range(25)
            ],
        }
        for i in range(5)
    ]
    parsed_five = [HandoverAssistSourceInput.model_validate(s) for s in five_sources_raw]
    units_80, cov_80 = handover_assist_service._build_evidence_units(parsed_five, {}, max_units=80)
    per_source_counts_80 = [sum(1 for u in units_80 if u.source.id == s["id"]) for s in five_sources_raw]
    assert per_source_counts_80 == [16, 16, 16, 16, 16]
    assert cov_80.requestedSources == 5
    assert len(cov_80.includedSources) == 5
    assert cov_80.skippedSources == []
    assert len(cov_80.truncatedSources) == 5
    assert cov_80.totalEvidenceUnits == 80
    assert cov_80.truncated is True

    # 2. Schema max_length=100 sources under default budget (100) -> every single source gets 1 unit
    hundred_sources_raw = [
        {
            "kind": "KNOWLEDGE_OBJECT",
            "id": f"951a2b3c4d5e6f7a8b9c{i:04x}",
            "version": 1,
            "title": f"Knowledge #{i}",
            "excerpts": [
                {
                    "locatorKind": "SECTION",
                    "locatorValue": "sec-1",
                    "heading": "Summary",
                    "content": f"Summary {i} part 1.",
                },
                {
                    "locatorKind": "SECTION",
                    "locatorValue": "sec-2",
                    "heading": "Details",
                    "content": f"Summary {i} part 2.",
                },
            ],
        }
        for i in range(100)
    ]
    llm = SimpleNamespace(
        configured=True,
        complete=AsyncMock(
            return_value=json.dumps(
                {
                    "candidates": [
                        {
                            "evidenceIndex": 100,
                            "reason": "Even the 100th source is represented in the catalog.",
                        }
                    ]
                }
            )
        ),
    )
    app = _build_app(llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/projects/751a2b3c4d5e6f7a8b9c0d20/handover/assist",
            headers=_bff_headers(),
            json={
                "task": "SOURCE_PROPOSAL",
                "query": "architecture ownership",
                "maxItems": 10,
                "sources": hundred_sources_raw,
            },
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["coverage"]["requestedSources"] == 100
        assert len(payload["coverage"]["includedSources"]) == 100
        assert payload["coverage"]["skippedSources"] == []
        assert payload["coverage"]["totalEvidenceUnits"] == 100
        assert payload["coverage"]["truncated"] is True
        assert payload["candidates"] == [
            {
                "source": {
                    "kind": "KNOWLEDGE_OBJECT",
                    "id": hundred_sources_raw[99]["id"],
                    "version": 1,
                },
                "reason": "Even the 100th source is represented in the catalog.",
            }
        ]


@pytest.mark.parametrize("reason", ["duplicate_excerpt", "normalized_logical_id"])
def test_coverage_aggregates_truncation_across_duplicate_inputs(reason: str) -> None:
    from sag_api.schemas.handover_assist import HandoverAssistSourceInput
    from sag_api.services.handover_assist_service import _build_evidence_units

    first = HandoverAssistSourceInput.model_validate(
        {
            "kind": "PROJECT_DOCUMENT",
            "id": "851a2b3c4d5e6f7a8b9c0d31",
            "version": 1,
            "title": "Runbook",
            "excerpts": [{"locatorKind": "SECTION", "locatorValue": "one", "content": "Short"}],
        }
    )
    second = first.model_copy(update={"logicalSourceId": " logical-runbook "})
    if reason == "duplicate_excerpt":
        second = HandoverAssistSourceInput.model_validate(
            {
                **first.model_dump(),
                "excerpts": [
                    {"locatorKind": "SECTION", "locatorValue": "two", "content": "x" * 2000},
                ],
            }
        )
    _, coverage = _build_evidence_units([first, second], {}, truncated_indexed_sources={("logical-runbook", 1)})
    assert coverage.requestedSources == 1
    assert coverage.truncated is True
    assert len(coverage.truncatedSources) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("wide_span", [False, True])
async def test_handover_assist_filters_indexed_units_by_exact_version_in_sql_before_limit(
    monkeypatch: pytest.MonkeyPatch,
    wide_span: bool,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sag_api.db.base import Base
    from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, SearchUnit

    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                Document.__table__,
                DocumentVersion.__table__,
                CanonicalBlock.__table__,
                SearchUnit.__table__,
            ],
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = "651a2b3c4d5e6f7a8b9c0d10"
    project_id = "751a2b3c4d5e6f7a8b9c0d20"

    async with session_factory() as db:
        doc = Document(
            id="doc-runbook-row",
            tenant_id=org_id,
            project_id=project_id,
            logical_source_id="continuum-doc-runbook",
            filename="runbook.md",
            storage_path="/tmp/runbook.md",
            is_active=True,
        )
        v1 = DocumentVersion(
            id="doc-ver-1",
            document_id=doc.id,
            version_no=1,
            file_hash="a" * 64,
            status="READY",
            search_status="READY",
        )
        v2 = DocumentVersion(
            id="doc-ver-2",
            document_id=doc.id,
            version_no=2,
            file_hash="b" * 64,
            status="READY",
            search_status="READY",
        )
        db.add_all([doc, v1, v2])

        # Seed 100 chunks on stale v1 so an unfiltered LIMIT(80) would return ONLY v1 rows
        for idx in range(100):
            blk = CanonicalBlock(
                id=f"v1-blk-{idx:03d}",
                document_version_id=v1.id,
                ordinal=idx,
                block_type="paragraph",
                page_from=1,
                page_to=1,
                section_path="Legacy v1",
                normalized_text=f"Stale v1 chunk #{idx} that must never enter v2 context.",
                content_hash=f"{idx:064x}",
            )
            su = SearchUnit(
                id=f"v1-su-{idx:03d}",
                document_version_id=v1.id,
                block_from_id=blk.id,
                block_to_id=blk.id,
                security_partition_id=project_id,
                content_hash=f"{idx:064x}",
                token_count=20,
                page_from=1,
                page_to=1,
                section_path="Legacy v1",
            )
            db.add_all([blk, su])

        # Seed 2 chunks on requested v2
        for idx in range(2):
            blk2 = CanonicalBlock(
                id=f"v2-blk-{idx:03d}",
                document_version_id=v2.id,
                ordinal=idx,
                block_type="paragraph",
                page_from=idx + 1,
                page_to=idx + 1,
                section_path=f"Runbook v2 Section {idx + 1}",
                normalized_text=f"Pinned v2 authoritative step #{idx + 1}: blue-green cutover.",
                content_hash=f"{idx + 1000:064x}",
            )
            su2 = SearchUnit(
                id=f"v2-su-{idx:03d}",
                document_version_id=v2.id,
                block_from_id=blk2.id,
                block_to_id=blk2.id,
                security_partition_id=project_id,
                content_hash=f"{idx + 1000:064x}",
                token_count=20,
                page_from=idx + 1,
                page_to=idx + 1,
                section_path=f"Runbook v2 Section {idx + 1}",
            )
            db.add_all([blk2, su2])
        # Unrelated blocks in the requested version must not be materialized.
        for idx in range(1000):
            db.add(
                CanonicalBlock(
                    id=f"unreferenced-{idx}",
                    document_version_id=v2.id,
                    ordinal=1000 + idx,
                    block_type="paragraph",
                    page_from=99,
                    page_to=99,
                    section_path="Unreferenced",
                    normalized_text="UNREFERENCED_BLOCK_" + "x" * 5000,
                    content_hash=f"{idx + 2000:064x}",
                )
            )
        if wide_span:
            db.add(
                SearchUnit(
                    id="wide-unit",
                    document_version_id=v2.id,
                    block_from_id="unreferenced-0",
                    block_to_id="unreferenced-999",
                    security_partition_id=project_id,
                    content_hash="e" * 64,
                    token_count=10000,
                    page_from=99,
                    page_to=99,
                    section_path="Oversized unit",
                )
            )
        await db.commit()

    captured_prompts: list[list[dict[str, str]]] = []

    async def _complete_spy(messages: list[dict[str, str]]) -> str:
        captured_prompts.append(messages)
        return json.dumps(
            {
                "answerStatus": "ANSWERED",
                "message": "Grounded in v2 indexed chunks.",
                "sections": [
                    {
                        "text": "Blue-green cutover is required in v2 [1].",
                        "evidenceIndices": [1],
                    }
                ],
            }
        )

    llm = SimpleNamespace(configured=True, complete=AsyncMock(side_effect=_complete_spy))
    app = _build_app(llm)

    fetched_block_rows: list[list] = []

    async def _real_sqlite_session():
        async with session_factory() as db:
            original_execute = db.execute

            async def _inspect_execute(statement, *args, **kwargs):
                result = await original_execute(statement, *args, **kwargs)
                if "normalized_text" in str(statement):
                    rows = result.all()
                    fetched_block_rows.append(rows)
                    return SimpleNamespace(all=lambda: rows)
                return result

            monkeypatch.setattr(db, "execute", _inspect_execute)
            yield db

    app.dependency_overrides[get_session] = _real_sqlite_session

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                f"/api/v1/projects/{project_id}/handover/assist",
                headers=_bff_headers(org_id=org_id, project_id=project_id),
                json={
                    "task": "QUESTION_ANSWER",
                    "query": "What cutover method is required in v2?",
                    "sources": [
                        {
                            "kind": "PROJECT_DOCUMENT",
                            "id": "851a2b3c4d5e6f7a8b9c0d31",
                            "version": 2,
                            "title": "Production Cutover Runbook",
                            "logicalSourceId": "continuum-doc-runbook",
                            "excerpts": [],
                        }
                    ],
                },
            )
            assert res.status_code == 200
            body = res.json()
            assert body["answerStatus"] == "ANSWERED"
            assert body["sections"][0]["citations"] == [
                {
                    "source": {
                        "kind": "PROJECT_DOCUMENT",
                        "id": "851a2b3c4d5e6f7a8b9c0d31",
                        "version": 2,
                    },
                    "locator": {"kind": "CHUNK", "value": "v2-su-000"},
                }
            ]
            assert len(captured_prompts) == 1
            prompt_user_text = captured_prompts[0][1]["content"]
            assert "Pinned v2 authoritative step #1: blue-green cutover." in prompt_user_text
            assert "Stale v1 chunk" not in prompt_user_text
            assert len(fetched_block_rows) == 1
            assert len(fetched_block_rows[0]) <= (10 if wide_span else 2)
            assert all(len(str(row[-1])) <= 1600 for row in fetched_block_rows[0])
            if wide_span:
                assert body["coverage"]["truncated"] is True
            else:
                assert "UNREFERENCED_BLOCK_" not in prompt_user_text
    finally:
        await engine.dispose()
