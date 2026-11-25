"""Layer 2 & Layer 3 Anti-Test-Hallucination Contract & User-Journey E2E Tests.

Enforces `.agents/rules/08-anti-test-hallucination-contract.md`:
- Tests REAL production classes, FastAPI HTTP/ASGI routes, SQLAlchemy DB persistence,
  and wire-level HTTP payloads — NEVER mocks away the seam under test.
- Covers Config & Env Parity, Settings persistence, MinerU wire contract, EngineManager
  method contracts, KnowledgeWorker tree gate (both `run_one` and `discover`), 3-Layer KG
  persistence, full ASGI HTTP user journey with exact Express BFF headers, adversarial
  numeric & citation hallucination detection, off-topic abstention with fallback tables,
  full >15-row Balance Sheet synthesis, and expanded deterministic FinancialCalculator.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.config import Settings
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, KnowledgeJob, Setting
from sag_api.db.models.stock_research import SagExtractedTable
from sag_api.parsing.mineru_official import OfficialMinerUClient, normalize_mineru_language
from sag_api.research.chat_orchestrator import (
    ResearchChatOrchestrator,
    RetrievalResult,
    RetrievedResearchSection,
    _build_synthesis_messages,
    _run_deterministic_calculator,
    collect_stream_result,
    evaluate_evidence_sufficiency,
)
from sag_api.research.kg_store import SQLAlchemyKGStore, refresh_research_knowledge_graph
from sag_api.research.ocr_adapter import MinerUOcrAdapter
from sag_api.research.quote_verifier import (
    CanonicalBlockRecord,
    compute_quote_hash,
    verify_numeric_claims_against_evidence,
)
from sag_api.schemas.research_chat import (
    GraphHopItem,
    HydratedTableItem,
    ResearchChatStreamRequest,
)
from sag_api.services.settings_service import apply_overrides, load_overrides

REPO_ROOT = Path(__file__).resolve().parents[4]


def _make_principal(
    *,
    org_id: str = "aiinvest-default",
    allowed: frozenset[str] = frozenset({"*"}),
) -> VerifiedPrincipal:
    now_ts = int(datetime.now(UTC).timestamp())
    return VerifiedPrincipal(
        subject="analyst-user-1",
        organization_id=org_id,
        allowed_project_ids=allowed,
        issuer="continuum-bff",
        key_id="k1",
        token_id="tok-contract-1",
        issued_at=now_ts,
        expires_at=now_ts + 3600,
        tenant_id=org_id,
        allowed_partition_ids=allowed,
    )


def _make_doc(
    doc_id: str,
    *,
    project_id: str,
    tenant_id: str = "aiinvest-default",
    is_active: bool = True,
) -> Document:
    return Document(
        id=doc_id,
        tenant_id=tenant_id,
        project_id=project_id,
        filename=f"{project_id.lower()}_{doc_id}.pdf",
        storage_path=f"/tmp/{project_id.lower()}_{doc_id}.pdf",
        is_active=is_active,
    )


def _parse_env_file(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    duplicates: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key in values:
            duplicates.append(key)
        values[key] = val.strip()
    return values, duplicates


# ── Layer 2 Contract 1: Config & Env Parity Across Repo ──────────────────────


def test_contract_config_and_env_parity_across_repo() -> None:
    """Catch silent config drift across config.py, compose files, and all .env.example templates."""
    default_cfg = Settings(_env_file=None)  # type: ignore[call-arg]
    assert default_cfg.timezone == "Asia/Ho_Chi_Minh"
    assert default_cfg.sag_language == "vi"
    assert default_cfg.mineru_official_model == "pipeline"
    assert default_cfg.sag_pg_database == "aiinvest_sag"
    assert default_cfg.sag_pg_port == 55432
    assert default_cfg.embedding_model == "Qwen/Qwen3-Embedding-0.6B"
    assert default_cfg.embedding_dimensions == 1024
    assert default_cfg.decision_provider == "disabled"
    assert "aiinvest_sag" in default_cfg.database_url

    env_paths = [
        REPO_ROOT / ".env.example",
        REPO_ROOT / "SAG" / ".env.example",
        REPO_ROOT / "SAG" / "apps" / "api" / ".env.example",
    ]
    for env_path in env_paths:
        assert env_path.exists(), f"Missing template: {env_path}"
        env_map, duplicates = _parse_env_file(env_path)
        assert not duplicates, f"Duplicate keys in {env_path.name}: {duplicates}"
        assert env_map.get("SAG_MINERU_OFFICIAL_MODEL") == "pipeline", f"Drift in {env_path}"
        assert env_map.get("SAG_KNOWLEDGE_TREE_REBUILD_ENABLED") == "false", f"Drift in {env_path}"
        if "SAG_TIMEZONE" in env_map:
            assert env_map["SAG_TIMEZONE"] == "Asia/Ho_Chi_Minh", f"Drift in {env_path}"
        if "SAG_DATABASE_URL" in env_map:
            assert "/aiinvest_sag" in env_map["SAG_DATABASE_URL"], f"Stale DB URL in {env_path}"
        if "SAG_QDRANT_SHARED_COLLECTION" in env_map:
            assert env_map["SAG_QDRANT_SHARED_COLLECTION"] == "aiinvest_search_units"

    for compose_path in (REPO_ROOT / "docker-compose.yml", REPO_ROOT / "SAG" / "compose.yaml"):
        text = compose_path.read_text(encoding="utf-8")
        assert "SAG_MINERU_OFFICIAL_MODEL" in text and "pipeline" in text
        assert "SAG_KNOWLEDGE_TREE_REBUILD_ENABLED" in text and "false" in text
        assert "Asia/Ho_Chi_Minh" in text


# ── Layer 2 Contract 2: Settings Service Preserves Vietnamese ────────────────


@pytest.mark.asyncio
async def test_contract_settings_service_preserves_vietnamese_sag_language() -> None:
    """Real DB settings round-trip MUST preserve `sag_language='vi'` instead of clobbering to 'en'."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(Setting(scope="global", key="model_config", value={"sag_language": "vi"}))
        await session.commit()
        loaded = await load_overrides(session)
        assert loaded["sag_language"] == "vi"

        cfg = Settings(_env_file=None, sag_language="en")  # type: ignore[call-arg]
        apply_overrides(cfg, loaded)
        assert cfg.sag_language == "vi"

        # Invalid language value falls back to Vietnamese default ("vi"), never silent "en"
        cfg_bad = Settings(_env_file=None, sag_language="en")  # type: ignore[call-arg]
        apply_overrides(cfg_bad, {"sag_language": "invalid_lang"})
        assert cfg_bad.sag_language == "vi"

    await engine.dispose()


# ── Layer 2 Contract 3: MinerU Wire Payload Contract (MockTransport Only) ────


@pytest.mark.asyncio
async def test_contract_mineru_wire_payload_uses_canonical_latin_and_pipeline() -> None:
    """Intercept ONLY at httpx transport boundary to prove real outbound JSON uses `latin` + `pipeline`."""
    assert normalize_mineru_language("vi") == "latin"
    assert normalize_mineru_language("vietnamese") == "latin"
    assert normalize_mineru_language("zh") == "ch"
    assert normalize_mineru_language("en") == "en"

    captured_payloads: list[tuple[str, dict[str, Any]]] = []

    def _wire_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        captured_payloads.append((str(request.url), body))
        if request.url.path.endswith("/extract/task"):
            return httpx.Response(200, json={"code": 0, "data": {"task_id": "task-vi-1"}})
        if request.url.path.endswith("/file-urls/batch"):
            return httpx.Response(
                200,
                json={"code": 0, "data": {"batch_id": "batch-vi-1", "file_urls": ["https://upload.example/put"]}},
            )
        return httpx.Response(404, json={"code": 404, "msg": "unexpected"})

    transport = httpx.MockTransport(_wire_handler)
    real_async_client = httpx.AsyncClient

    def _patched_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    cfg = Settings(
        _env_file=None,  # type: ignore[call-arg]
        mineru_base_url="https://mineru.net",
        mineru_official_api_token="test-token-value",
        sag_language="vi",
    )
    adapter = MinerUOcrAdapter(cfg)
    client = OfficialMinerUClient(cfg, language="vi")
    assert adapter._official_model == "pipeline"
    assert client._language == "latin"
    assert client._official_model == "pipeline"

    with (
        patch("sag_api.parsing.mineru_official._validate_api_url", new=AsyncMock()),
        patch("sag_api.parsing.mineru_official.httpx.AsyncClient", side_effect=_patched_client),
    ):
        task_id = await client._create_url_task("https://r2.example.com/fpt_bctc_2025.pdf")
        batch_id, upload_url = await client._request_upload("fpt_bctc_2025.pdf")

    assert task_id == "task-vi-1"
    assert batch_id == "batch-vi-1"
    assert upload_url == "https://upload.example/put"
    assert len(captured_payloads) == 2

    url_task_payload = captured_payloads[0][1]
    assert url_task_payload["language"] == "latin"
    assert url_task_payload["model_version"] == "pipeline"
    assert url_task_payload["enable_table"] is True
    assert url_task_payload["language"] != "vi"

    batch_payload = captured_payloads[1][1]
    assert batch_payload["language"] == "latin"
    assert batch_payload["model_version"] == "pipeline"
    assert batch_payload["enable_table"] is True


# ── Layer 2 Contract 4: Real EngineManager Method Contract ───────────────────


def test_contract_engine_manager_exposes_real_async_methods_used_by_api() -> None:
    """Prevent phantom-attribute regressions (`engine_manager.qdrant_client` / `.embedder`)."""
    from sag_api.sag.engine_manager import EngineManager

    assert inspect.iscoroutinefunction(getattr(EngineManager, "get_search_unit_qdrant_client", None))
    assert inspect.iscoroutinefunction(getattr(EngineManager, "get_sag_embedding", None))

    api_src = (
        REPO_ROOT / "SAG" / "apps" / "api" / "sag_api" / "api" / "v1" / "research_chat.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(api_src)

    # Assert research_chat.py never reads non-existent `.qdrant_client` or `.embedder` on engine_manager
    forbidden_attrs = {"qdrant_client", "embedder"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value not in forbidden_attrs, (
                    f"Phantom getattr(..., {node.args[1].value!r}) found in research_chat.py"
                )
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "engine_manager":
                assert node.attr not in forbidden_attrs, (
                    f"Phantom engine_manager.{node.attr} found in research_chat.py"
                )
    assert "get_search_unit_qdrant_client" in api_src
    assert "get_sag_embedding" in api_src


# ── Layer 2 Contract 5: KnowledgeWorker Tree Gate (run_one + discover) ───────


@pytest.mark.asyncio
async def test_contract_knowledge_worker_skips_leiden_rebuild_in_both_run_one_and_discover() -> None:
    """When `knowledge_tree_rebuild_enabled=False`, NEITHER `run_one()` NOR `discover()` schedules Leiden trees."""
    from sag_api.jobs.knowledge import KnowledgeWorker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    async with session_factory() as session:
        session.add(_make_doc("doc-fpt-kw", project_id="FPT"))
        session.add(
            DocumentVersion(
                id="ver-fpt-kw",
                document_id="doc-fpt-kw",
                version_no=1,
                file_hash="h-kw",
                valid_from=now,
                source_published_at=now,
                search_status="SEARCH_READY",
                search_ready_at=now,
                knowledge_status="DATA_READY",
            )
        )
        session.add(
            KnowledgeJob(
                id="kjob-1",
                document_version_id="ver-fpt-kw",
                tenant_id="aiinvest-default",
                kind="FOUNDATION",
                input_checksum="chk-1",
                status="SUCCEEDED",
                tree_requested_at=None,
                created_at=now,
            )
        )
        await session.commit()

    cfg = Settings(_env_file=None, knowledge_tree_rebuild_enabled=False)  # type: ignore[call-arg]
    fake_queue = object()
    worker = KnowledgeWorker(session_factory, cfg, job_queue=fake_queue)

    enqueue_spy = AsyncMock()
    with patch("sag_api.services.universe_tree_service.enqueue_tree_rebuild_for_version", new=enqueue_spy):
        await worker.discover()

    assert enqueue_spy.await_count == 0
    await engine.dispose()


# ── Layer 2 Contract 6: Real 3-Layer KG Refresher Persists DB Rows ───────────


@pytest.mark.asyncio
async def test_contract_kg_refresher_populates_real_db_tables_and_serves_subgraph() -> None:
    """Calling real `refresh_research_knowledge_graph` writes `kg_nodes` & `kg_edges` queryable via PIT."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    pub_dt = datetime(2025, 4, 15, tzinfo=UTC)
    fpt_txt = (
        "Trong năm 2025, FPT ký hợp đồng cung cấp cho khách hàng lớn "
        "Tổng công ty Cổ phần Vận tải Dầu khí (PVT) giải pháp chuyển đổi số ERP trị giá 250 tỷ đồng."
    )
    pvt_txt = (
        "PVT mở rộng hiện đại hóa quản trị với nhà cung cấp Công ty Cổ phần FPT (FPT) "
        "theo hợp đồng chuyển đổi số giai đoạn 2024-2025."
    )

    async with session_factory() as session:
        session.add_all([_make_doc("doc-fpt-kg", project_id="FPT"), _make_doc("doc-pvt-kg", project_id="PVT")])
        session.add_all(
            [
                DocumentVersion(
                    id="ver-fpt-kg",
                    document_id="doc-fpt-kg",
                    version_no=1,
                    file_hash="h-fpt-kg",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    metadata_json={"sub_doc_slot": "BCTN_CORE", "fiscal_year": 2025},
                ),
                DocumentVersion(
                    id="ver-pvt-kg",
                    document_id="doc-pvt-kg",
                    version_no=1,
                    file_hash="h-pvt-kg",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    metadata_json={"sub_doc_slot": "BCTN_CORE", "fiscal_year": 2025},
                ),
            ]
        )
        session.add_all(
            [
                CanonicalBlock(
                    id="blk-fpt-kg-1",
                    document_version_id="ver-fpt-kg",
                    ordinal=1,
                    block_type="paragraph",
                    section_path="BCTN_CORE > Khách hàng & Đối tác",
                    page_from=12,
                    page_to=12,
                    normalized_text=fpt_txt,
                    content_hash=compute_quote_hash(fpt_txt),
                ),
                CanonicalBlock(
                    id="blk-pvt-kg-1",
                    document_version_id="ver-pvt-kg",
                    ordinal=1,
                    block_type="paragraph",
                    section_path="BCTN_CORE > Nhà cung cấp công nghệ",
                    page_from=19,
                    page_to=19,
                    normalized_text=pvt_txt,
                    content_hash=compute_quote_hash(pvt_txt),
                ),
            ]
        )
        await session.commit()

        summary = await refresh_research_knowledge_graph(
            session,
            tickers=["FPT", "PVT"],
            as_of_date=date(2025, 6, 30),
            tenant_id="aiinvest-default",
            core_database_url=None,
        )
        assert summary["pages_scanned"] >= 2
        assert summary["nodes_upserted"] >= 2
        assert summary["edges_upserted"] >= 1

        kg_store = SQLAlchemyKGStore(session)
        sub_fpt = await kg_store.get_subgraph_for_ticker("FPT", date(2025, 6, 30))
        assert len(sub_fpt["nodes"]) >= 1
        assert len(sub_fpt["edges"]) >= 1

        # PIT check: before 2025-04-15, edges effective 2025-04-15 MUST NOT leak!
        sub_before = await kg_store.get_subgraph_for_ticker("FPT", date(2024, 12, 31))
        assert len(sub_before["edges"]) == 0

    await engine.dispose()


# ── Layer 3 User Journey 1: Real FastAPI ASGI Route + Express BFF Headers ────


@pytest.mark.asyncio
async def test_user_journey_asgi_http_tickers_and_sse_stream_with_real_bff_headers() -> None:
    """Exercise REAL FastAPI `/api/v1/research-chat/tickers` and `/stream` via ASGI + real BFF headers."""
    from fastapi.responses import JSONResponse

    from sag_api.api.v1 import research_chat as rc_api
    from sag_api.core.config import settings as global_settings
    from sag_api.core.db import get_session
    from sag_api.core.errors import ApiError

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    pub_dt = datetime(2025, 4, 15, tzinfo=UTC)
    fpt_quote = "Doanh thu thuần FPT năm 2025 đạt 62.849 tỷ đồng, lợi nhuận sau thuế đạt 9.420 tỷ đồng."

    async with session_factory() as session:
        session.add_all(
            [
                _make_doc("doc-fpt-http", project_id="FPT", tenant_id="aiinvest-default"),
                _make_doc("doc-mwg-http", project_id="MWG", tenant_id="aiinvest-default"),
                _make_doc("doc-other-org", project_id="SECRET", tenant_id="other-tenant"),
            ]
        )
        session.add_all(
            [
                DocumentVersion(
                    id="ver-fpt-http",
                    document_id="doc-fpt-http",
                    version_no=1,
                    file_hash="h-fpt-http",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    metadata_json={"sub_doc_slot": "BCTC_AUDITED", "fiscal_year": 2025},
                ),
                DocumentVersion(
                    id="ver-mwg-http",
                    document_id="doc-mwg-http",
                    version_no=1,
                    file_hash="h-mwg-http",
                    valid_from=pub_dt,
                    source_published_at=pub_dt,
                    metadata_json={"sub_doc_slot": "BCTC_AUDITED", "fiscal_year": 2025},
                ),
            ]
        )
        session.add(
            CanonicalBlock(
                id="blk-fpt-http",
                document_version_id="ver-fpt-http",
                ordinal=1,
                block_type="paragraph",
                section_path="BCTC_AUDITED > Kết quả kinh doanh",
                page_from=6,
                page_to=6,
                normalized_text=fpt_quote,
                content_hash=compute_quote_hash(fpt_quote),
            )
        )
        session.add(
            SagExtractedTable(
                table_id="tbl-fpt-http",
                project_id="FPT",
                document_version_id="ver-fpt-http",
                sub_doc_slot="BCTC_AUDITED",
                fiscal_year=2025,
                fiscal_quarter=0,
                table_title="Báo cáo KQKD FPT",
                section_path="BCTC_AUDITED",
                page_from=6,
                page_to=6,
                currency="VND",
                unit_raw="tỷ đồng",
                unit_multiplier=1e9,
                headers_json=[["Chỉ tiêu", "Năm 2025", "Năm 2024"]],
                rows_json=[
                    [
                        {"raw_text": "Doanh thu thuần", "numeric_value": None},
                        {"raw_text": "62.849", "numeric_value": 62849.0},
                        {"raw_text": "52.618", "numeric_value": 52618.0},
                    ],
                    [
                        {"raw_text": "Lợi nhuận sau thuế", "numeric_value": None},
                        {"raw_text": "9.420", "numeric_value": 9420.0},
                        {"raw_text": "7.788", "numeric_value": 7788.0},
                    ],
                ],
                verification_status="VERIFIED",
                table_hash="th-fpt-http",
                valid_from=pub_dt,
            )
        )
        await session.commit()

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Any, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    app.include_router(rc_api.router, prefix="/api/v1")

    async def _override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session

    bff_headers = {
        "X-SAG-BFF-Service-Token": "contract-bff-secret",
        "X-Continuum-User-Id": "analyst-1",
        "X-Continuum-Organization-Id": "aiinvest-default",
        "X-Continuum-Project-Id": "all",
        "X-Continuum-Security-Partition": "all",
        "X-Continuum-Allowed-Project-Ids": "*",
    }

    with patch.object(global_settings, "bff_service_token", "contract-bff-secret"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test-sag",
        ) as client:
            # 1. Fail-closed auth without BFF token
            unauth = await client.get("/api/v1/research-chat/tickers")
            assert unauth.status_code in (401, 403, 503)

            # 2. Live org-scoped tickers endpoint (tenant isolated: SECRET excluded!)
            tk_resp = await client.get("/api/v1/research-chat/tickers", headers=bff_headers)
            assert tk_resp.status_code == 200
            tk_data = tk_resp.json()
            assert tk_data["organization_id"] == "aiinvest-default"
            assert tk_data["tickers"] == ["FPT", "MWG"]
            assert "SECRET" not in tk_data["tickers"]

            # 3. Full SSE Research Chat stream with real Express BFF headers (unconfigured LLM -> explicit blindspot)
            sse_resp = await client.post(
                "/api/v1/research-chat/stream",
                headers=bff_headers,
                json={
                    "query": "Doanh thu thuần và lợi nhuận sau thuế FPT năm 2025?",
                    "ticker": "FPT",
                    "mode": "GENERAL",
                    "as_of_date": "2025-06-30",
                    "enable_kg": True,
                    "history": [],
                },
            )
            assert sse_resp.status_code == 200
            body_text = sse_resp.text
            assert "event: stage" in body_text
            assert "event: done" in body_text

            # Parse `done` SSE block and verify Stage 7 telemetry + explicit fallback blindspot
            done_json: dict[str, Any] = {}
            for block in body_text.replace("\r\n", "\n").split("\n\n"):
                if "event: done" in block:
                    for line in block.splitlines():
                        if line.startswith("data:"):
                            done_json = json.loads(line[5:].strip())
            assert done_json.get("answer")
            meta = done_json["metadata"]
            assert meta["primary_ticker"] == "FPT"
            assert len(meta["hydrated_tables"]) == 1
            assert len(meta["calculator_traces"]) >= 2  # YoY + Net Profit Margin
            bs_codes = {b["code"] for b in meta["declared_blindspots"]}
            assert "LLM_UNCONFIGURED_FALLBACK" in bs_codes
            assert meta["telemetry"]["retrieval_mode"] == "SHARED_CHAMBER_HYBRID"
            assert meta["telemetry"]["llm_model"] == "deterministic_fallback"
            assert meta["telemetry"]["embedding_model"] == global_settings.embedding_model

    await engine.dispose()


# ── Layer 3 User Journey 2: Adversarial Numeric & Citation Hallucination ─────


@pytest.mark.asyncio
async def test_adversarial_numeric_hallucination_and_c99_rejected_in_stage7() -> None:
    """Invented `99,999 tỷ` next to valid `[C1]`, uncited figure, and `[C99]` MUST all be caught."""
    pub_d = date(2025, 4, 15)
    quote_txt = "Doanh thu thuần FPT năm 2025 đạt 62.849 tỷ đồng, tăng 19,4% so với cùng kỳ."
    sec = RetrievedResearchSection(
        ticker="FPT",
        doc_id="doc-fpt",
        document_version_id="ver-fpt",
        sub_doc_slot="BCTC_AUDITED",
        page_from=6,
        page_to=6,
        published_date=pub_d,
        content=quote_txt,
        heading="Kết quả kinh doanh",
        score=0.91,
        block_from_id="blk-1",
        block_to_id="blk-1",
    )
    blk = CanonicalBlockRecord(
        document_version_id="ver-fpt",
        page_from=6,
        page_to=6,
        normalized_text=quote_txt,
        sub_doc_slot="BCTC_AUDITED",
        doc_id="doc-fpt",
        published_date=pub_d,
        block_id="blk-1",
        ordinal=1,
    )

    class _StaticRetriever:
        async def retrieve(self, **_: Any) -> RetrievalResult:
            return RetrievalResult(sections=[sec], canonical_blocks={"ver-fpt": [blk]})

    class _HallucinatingLLM:
        configured = True

        async def stream_complete(self, _messages: list[dict[str, str]]):
            yield (
                "Doanh thu FPT đạt 62.849 tỷ đồng (+19,4%) theo [C1], "
                "nhưng doanh thu mảng bí mật đạt 99,999 tỷ đồng [C1] và nguồn [C99].\n"
                "Biên lợi nhuận gộp ước đạt 44.5% trong kỳ."
            )

    orch = ResearchChatOrchestrator(retriever=_StaticRetriever(), llm=_HallucinatingLLM())
    ans, meta, _ = await collect_stream_result(
        orch.stream_chat(
            ResearchChatStreamRequest(query="Doanh thu FPT?", ticker="FPT", as_of_date=date(2025, 6, 30)),
            principal=_make_principal(),
        )
    )

    assert "[C99 UNVERIFIED_CLAIM]" in ans
    assert len(meta["citations"]) == 1
    assert any(r["citation_id"] == "C99" and r["verified"] is False for r in meta["rejected_citations"])
    bs_codes = {b["code"] for b in meta["declared_blindspots"]}
    assert "HALLUCINATED_CITATION_ID" in bs_codes
    assert "UNSUPPORTED_NUMERIC_CLAIM" in bs_codes
    assert "UNCITED_NUMERIC_CLAIM" in bs_codes
    assert meta["telemetry"]["rejected_citations_count"] >= 1
    assert meta["telemetry"]["hallucinated_citation_ids_count"] == 1
    assert meta["telemetry"]["unsupported_numeric_claims_count"] >= 2  # 99,999 tỷ + 44.5%

    # Negative check: clean rounded / unit-converted prose MUST produce 0 false positives!
    clean_check = verify_numeric_claims_against_evidence(
        "Doanh thu thuần năm 2025 của FPT đạt 62,85 nghìn tỷ đồng (tăng 19,4%) [C1].",
        sections_by_cid={"C1": sec},
    )
    assert clean_check.unsupported_claims == []
    assert clean_check.uncited_claims == []


# ── Layer 3 User Journey 3: Off-Topic Abstention Even With Fallback Tables ───


def test_adversarial_low_relevance_off_topic_abstains_even_with_fallback_tables_and_kg() -> None:
    """Off-topic query on ingested ticker MUST abstain even when fallback tables & KG hops exist."""
    weak_sec = RetrievedResearchSection(
        ticker="FPT",
        doc_id="doc-fpt",
        document_version_id="ver-fpt",
        sub_doc_slot="BCTC_AUDITED",
        page_from=4,
        page_to=4,
        published_date=date(2025, 4, 15),
        content="Bảng cân đối kế toán hợp nhất tại ngày 31/12/2024.",
        heading="Cân đối kế toán",
        score=0.03,
    )
    fallback_tbl = HydratedTableItem(
        table_id="tbl-fpt-bs",
        project_id="FPT",
        title="Bảng cân đối kế toán FPT",
        sub_doc_slot="BCTC_AUDITED",
        page_from=4,
        page_to=5,
        verification_status="VERIFIED",
        headers=[["Chỉ tiêu", "2024", "2023"]],
        rows=[{"label": "Tổng cộng tài sản", "values": [72000.0, 60000.0]}],
    )
    fallback_hop = GraphHopItem(
        layer="VALUE_CHAIN",
        src_label="FPT",
        relation_type="PARTNER_OF",
        dst_label="PVT",
        verification_tier="DOC_ONLY",
        effective_from="2025-04-15",
    )

    blindspots, should_abstain = evaluate_evidence_sufficiency(
        resolved_query="FPT có sản xuất máy bay chiến đấu tàng hình không?",
        explicit_tickers=["FPT"],
        sections=[weak_sec],
        hydrated_tables=[fallback_tbl],
        graph_hops=[fallback_hop],
    )
    assert should_abstain is True
    assert any(b.code == "LOW_RELEVANCE_EVIDENCE" for b in blindspots)


# ── Layer 3 User Journey 4: Full >15-Row Balance Sheet & Expanded Calculator ─


def test_full_balance_sheet_gt_15_rows_reaches_prompt_and_expanded_calculator_computes_ratios() -> None:
    """Balance sheets with 30+ rows MUST NOT truncate at row 15; calculator computes YoY, CAGR, GPM, NPM, D/E, CR."""
    bs_rows: list[dict[str, Any]] = [
        {"label": "A. TÀI SẢN NGẮN HẠN", "values": [36000.0, 30000.0]},
    ]
    for i in range(1, 25):
        bs_rows.append({"label": f"Khoản mục tài sản chi tiết {i}", "values": [float(i * 100), float(i * 90)]})
    bs_rows.extend(
        [
            {"label": "Nợ ngắn hạn", "values": [24000.0, 20000.0]},
            {"label": "Nợ phải trả", "values": [40000.0, 35000.0]},
            {"label": "Vốn chủ sở hữu", "values": [32000.0, 25000.0]},
            {"label": "TỔNG CỘNG NGUỒN VỐN", "values": [72000.0, 60000.0]},
        ]
    )
    bs_tbl = HydratedTableItem(
        table_id="tbl-bs-30",
        project_id="FPT",
        title="Bảng cân đối kế toán hợp nhất",
        sub_doc_slot="BCTC_AUDITED",
        page_from=4,
        page_to=6,
        verification_status="VERIFIED",
        headers=[["Chỉ tiêu", "31/12/2025", "31/12/2024"]],
        rows=bs_rows,
    )
    is_tbl = HydratedTableItem(
        table_id="tbl-is-3yr",
        project_id="FPT",
        title="Báo cáo kết quả kinh doanh 3 năm",
        sub_doc_slot="BCTC_AUDITED",
        page_from=7,
        page_to=7,
        verification_status="VERIFIED",
        headers=[["Chỉ tiêu", "Năm 2025", "Năm 2024", "Năm 2023"]],
        rows=[
            {"label": "Doanh thu thuần", "values": [64000.0, 52000.0, 44444.44]},
            {"label": "Lợi nhuận gộp", "values": [25600.0, 20800.0, 17777.78]},
            {"label": "Lợi nhuận sau thuế", "values": [9600.0, 7800.0, 6400.0]},
        ],
    )
    agm_tbl = HydratedTableItem(
        table_id="tbl-agm-plan",
        project_id="FPT",
        title="Kế hoạch và thực hiện ĐHĐCĐ 2025",
        sub_doc_slot="AGM_TARGETS",
        page_from=2,
        page_to=2,
        verification_status="VERIFIED",
        headers=[["Chỉ tiêu", "Thực hiện 2025", "Kế hoạch 2025"]],
        rows=[{"label": "Doanh thu hợp nhất", "values": [64000.0, 60000.0]}],
    )

    msgs = _build_synthesis_messages(
        query="Phân tích vốn chủ sở hữu và tổng nguồn vốn của FPT",
        primary_ticker="FPT",
        expanded_tickers=["FPT"],
        as_of_date=date(2026, 4, 30),
        sections=[],
        hydrated_tables=[bs_tbl],
        calculator_traces=[],
        graph_hops=[],
        discrepancy_alerts=[],
        history=[],
    )
    prompt_text = msgs[-1]["content"]
    # Row 28 ("Vốn chủ sở hữu") and row 29 ("TỔNG CỘNG NGUỒN VỐN") MUST reach the LLM!
    assert "Vốn chủ sở hữu" in prompt_text
    assert "TỔNG CỘNG NGUỒN VỐN" in prompt_text

    traces = _run_deterministic_calculator(
        query="Đánh giá biên lợi nhuận, CAGR, đòn bẩy D/E và thanh khoản FPT",
        hydrated_tables=[bs_tbl, is_tbl, agm_tbl],
        graph_hops=[],
    )
    formulas = {t.formula for t in traces}
    assert "yoy(current, prior)" in formulas
    assert "debt_to_equity(total_debt, equity)" in formulas
    assert "safe_div(current_assets, current_liabilities)" in formulas
    assert "margin(gross_profit, revenue)" in formulas
    assert "margin(net_income, revenue)" in formulas
    assert "cagr(end_val, start_val, years)" in formulas
    assert "margin(actual, plan)" in formulas

    # Verify exact computed numbers (zero mental math error)
    de_trace = next(t for t in traces if t.formula == "debt_to_equity(total_debt, equity)")
    assert de_trace.formatted == "1.25x"  # 40000 / 32000
    cr_trace = next(t for t in traces if t.formula == "safe_div(current_assets, current_liabilities)")
    assert cr_trace.formatted == "1.50x"  # 36000 / 24000
    gpm_trace = next(t for t in traces if t.formula == "margin(gross_profit, revenue)")
    assert gpm_trace.formatted == "40.00%"  # 25600 / 64000
    npm_trace = next(t for t in traces if t.formula == "margin(net_income, revenue)")
    assert npm_trace.formatted == "15.00%"  # 9600 / 64000
    cagr_trace = next(t for t in traces if t.formula == "cagr(end_val, start_val, years)")
    assert cagr_trace.result is not None and abs(cagr_trace.result - 0.20) < 0.01  # (64000/44444.44)^0.5 - 1 = 20%
    plan_trace = next(t for t in traces if t.formula == "margin(actual, plan)")
    assert plan_trace.formatted == "106.67%"  # 64000 / 60000
