"""Phase 1 End-to-End Cross-Track Integration Tests (Track 1 + Track 2 + Track 3 + FastAPI).

Exercises the real FastAPI `sag_api.main.create_app()` endpoints:
- `POST /api/v1/research-chat/stream`
- `GET /api/v1/research-chat/kg/{ticker}`
- `GET /api/v1/research-chat/tables/{table_id}`

Wires together:
- Track 1: SQLAlchemy ORM (`Source`, `SourceProjectMapping`, `Document`, `DocumentVersion`,
  `CanonicalBlock`, `SearchUnit`, `IngestionRun`, `StageRun`, `SagExtractedTable`,
  `KgNode`, `KgEdge`, `MacroSensitivitySnapshot`), canonical `retrieve_search_unit_sections`
  with `as_of_ts`, `generate_search_unit_point_id`, `apply_litellm_completion_policy`
- Track 2: `build_matrix_from_html_table`, `ExtractedTableRecord`, `FinancialCalculator`,
  `detect_discrepancies`
- Track 3: `SQLAlchemyKGStore`, `SQLAlchemyTableStore`, `SAGRetrieverAdapter`,
  `ResearchChatOrchestrator`, `verify_citations`, `require_research_bff_principal`
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_api.core import config as config_mod
from sag_api.core.db import get_session
from sag_api.core.litellm_policy import apply_litellm_completion_policy
from sag_api.db.base import Base
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    IngestionRun,
    SagExtractedTable,
    SearchUnit,
    Source,
    SourceProjectMapping,
    StageRun,
)
from sag_api.enums import SourceStatus
from sag_api.main import create_app
from sag_api.research import kg_macro_coupling as e2
from sag_api.research.chat_orchestrator import ResearchChatOrchestrator
from sag_api.research.kg_store import (
    SQLAlchemyKGStore,
    build_kg_nodes_from_universe,
    snapshots_to_kg_edges,
)
from sag_api.research.quote_verifier import compute_quote_hash
from sag_api.research.table_engine import (
    ExtractedTableRecord,
    build_matrix_from_html_table,
)
from sag_api.services import search_unit_retrieval_service as surs
from sag_api.services.search_index_service import generate_search_unit_point_id


class _FakeQdrantEmbedder:
    async def generate(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


class _FakeEngineManager:
    """EngineManager stub backed by an in-memory Qdrant MockTransport enforcing `valid_from_ts` PIT."""

    def __init__(self, qdrant_client: httpx.AsyncClient) -> None:
        self._qdrant_client = qdrant_client
        self.observed_filters: list[dict[str, Any]] = []

    async def get_search_unit_qdrant_client(self) -> httpx.AsyncClient:
        return self._qdrant_client

    async def get_sag_embedding(self, source_config_id: str | None = None, source: Any = None) -> _FakeQdrantEmbedder:
        return _FakeQdrantEmbedder()


class _FakeLLM:
    configured = True

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_complete(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        for word in self._text.split(" "):
            yield word + " "


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    cur_ev = "message"
    cur_data: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if cur_data:
                events.append((cur_ev, json.loads("\n".join(cur_data))))
                cur_ev = "message"
                cur_data = []
            continue
        if line.startswith("event:"):
            cur_ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            cur_data.append(line.split(":", 1)[1].strip())
    if cur_data:
        events.append((cur_ev, json.loads("\n".join(cur_data))))
    return events


async def _seed_e2e_db(session: AsyncSession) -> dict[str, Any]:
    """Seed Track 1 ORM + SearchUnit/Manifest + Track 2 ExtractedTable + Track 3 SQLAlchemyKGStore."""
    src1 = Source(
        id="src-sym1",
        name="SYM1 Annual Filings",
        sag_source_config_id="cfg-sym1",
        status=SourceStatus.ACTIVE,
    )
    src3 = Source(
        id="src-sym3",
        name="SYM3 Future Filings",
        sag_source_config_id="cfg-sym3",
        status=SourceStatus.ACTIVE,
    )
    session.add_all([src1, src3])
    await session.flush()

    ver1_dt = datetime(2026, 4, 13, 8, 0, tzinfo=UTC)
    ver3_dt = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
    session.add_all(
        [
            SourceProjectMapping(
                id="map-sym1",
                source_id="src-sym1",
                organization_id="org-e2e",
                project_id="SYM1",
                mapping_version=1,
                state="CONFIRMED",
                confirmed_at=ver1_dt,
                confirmed_by="e2e-admin",
                approval_ref="ref-sym1",
            ),
            SourceProjectMapping(
                id="map-sym3",
                source_id="src-sym3",
                organization_id="org-e2e",
                project_id="SYM3",
                mapping_version=1,
                state="CONFIRMED",
                confirmed_at=ver3_dt,
                confirmed_by="e2e-admin",
                approval_ref="ref-sym3",
            ),
        ]
    )

    doc1 = Document(
        id="doc-sym1",
        tenant_id="org-e2e",
        source_id="src-sym1",
        project_id="SYM1",
        filename="SYM1_BCTC_2025.pdf",
        storage_path="/tmp/sym1.pdf",
        is_active=True,
    )
    doc3 = Document(
        id="doc-sym3",
        tenant_id="org-e2e",
        source_id="src-sym3",
        project_id="SYM3",
        filename="SYM3_Q4_2026.pdf",
        storage_path="/tmp/sym3.pdf",
        is_active=True,
    )
    session.add_all([doc1, doc3])
    await session.flush()

    ver1 = DocumentVersion(
        id="ver-sym1-2025",
        document_id="doc-sym1",
        version_no=1,
        file_hash="a" * 64,
        source_published_at=ver1_dt,
        valid_from=ver1_dt,
        status="READY",
        search_status="READY",
        search_ready_at=ver1_dt,
        metadata_json={"sub_doc_slot": "S07_REV_COGS_DETAIL", "security_partition_id": "SYM1"},
    )
    ver3 = DocumentVersion(
        id="ver-sym3-2026",
        document_id="doc-sym3",
        version_no=1,
        file_hash="b" * 64,
        source_published_at=ver3_dt,
        valid_from=ver3_dt,
        status="READY",
        search_status="READY",
        search_ready_at=ver3_dt,
        metadata_json={"sub_doc_slot": "S07_REV_COGS_DETAIL", "security_partition_id": "SYM3"},
    )
    session.add_all([ver1, ver3])
    await session.flush()

    quote_txt = (
        "Doanh thu thuần đạt 950 tỷ đồng trong năm 2025, biên gộp chịu áp lực từ tỷ giá USD/VND tăng."
    )
    su1_content = f"Thuyết minh 28: {quote_txt}"
    su1_hash = hashlib.sha256(su1_content.encode("utf-8")).hexdigest()
    blk1 = CanonicalBlock(
        id="blk-sym1-168",
        document_version_id="ver-sym1-2025",
        ordinal=1,
        block_type="paragraph",
        page_from=168,
        page_to=168,
        section_path="S07_REV_COGS_DETAIL",
        source_anchor="page=168",
        normalized_text=su1_content,
        content_hash=su1_hash,
    )
    su3_content = "Báo cáo tương lai tháng 12/2026 không được rò rỉ về quá khứ."
    su3_hash = hashlib.sha256(su3_content.encode("utf-8")).hexdigest()
    blk3 = CanonicalBlock(
        id="blk-sym3-10",
        document_version_id="ver-sym3-2026",
        ordinal=1,
        block_type="paragraph",
        page_from=10,
        page_to=10,
        section_path="S07_REV_COGS_DETAIL",
        source_anchor="page=10",
        normalized_text=su3_content,
        content_hash=su3_hash,
    )
    session.add_all([blk1, blk3])
    await session.flush()

    session.add_all(
        [
            SearchUnit(
                id="su-sym1-168",
                document_version_id="ver-sym1-2025",
                block_from_id="blk-sym1-168",
                block_to_id="blk-sym1-168",
                security_partition_id="SYM1",
                content_hash=su1_hash,
                token_count=24,
                page_from=168,
                page_to=168,
                section_path="S07_REV_COGS_DETAIL",
            ),
            SearchUnit(
                id="su-sym3-10",
                document_version_id="ver-sym3-2026",
                block_from_id="blk-sym3-10",
                block_to_id="blk-sym3-10",
                security_partition_id="SYM3",
                content_hash=su3_hash,
                token_count=16,
                page_from=10,
                page_to=10,
                section_path="S07_REV_COGS_DETAIL",
            ),
        ]
    )
    await session.flush()

    run1_start = datetime(2026, 4, 13, 8, 1, tzinfo=UTC)
    run3_start = datetime(2026, 12, 1, 8, 1, tzinfo=UTC)
    session.add_all(
        [
            IngestionRun(
                id="run-sym1",
                tenant_id="org-e2e",
                project_id="SYM1",
                document_version_id="ver-sym1-2025",
                idempotency_key="idem-sym1",
                payload_hash="1" * 64,
                content_hash="1" * 64,
                current_stage="INDEX_SEARCH",
                status="SUCCESS",
                started_at=run1_start,
                completed_at=run1_start,
            ),
            IngestionRun(
                id="run-sym3",
                tenant_id="org-e2e",
                project_id="SYM3",
                document_version_id="ver-sym3-2026",
                idempotency_key="idem-sym3",
                payload_hash="3" * 64,
                content_hash="3" * 64,
                current_stage="INDEX_SEARCH",
                status="SUCCESS",
                started_at=run3_start,
                completed_at=run3_start,
            ),
        ]
    )
    await session.flush()

    csum1 = "d" * 64
    csum3 = "e" * 64
    session.add_all(
        [
            StageRun(
                id="stg-sym1",
                run_id="run-sym1",
                stage="INDEX_SEARCH",
                status="SUCCESS",
                created_at=run1_start,
                metrics_json={
                    "manifest_verified": True,
                    "collection_name": "search_units_SYM1",
                    "search_unit_count": 1,
                    "pg_count": 1,
                    "qdrant_count": 1,
                    "qdrant_indexed_count": 1,
                    "manifest_checksum": csum1,
                    "qdrant_checksum": csum1,
                },
            ),
            StageRun(
                id="stg-sym3",
                run_id="run-sym3",
                stage="INDEX_SEARCH",
                status="SUCCESS",
                created_at=run3_start,
                metrics_json={
                    "manifest_verified": True,
                    "collection_name": "search_units_SYM3",
                    "search_unit_count": 1,
                    "pg_count": 1,
                    "qdrant_count": 1,
                    "qdrant_indexed_count": 1,
                    "manifest_checksum": csum3,
                    "qdrant_checksum": csum3,
                },
            ),
        ]
    )
    await session.flush()

    # 2. Track 2 Table Engine: VERIFIED + TABLE_UNVERIFIED + Future table
    html_verified = """
    <table>
      <tr><th>Chỉ tiêu</th><th>2025</th><th>2024</th></tr>
      <tr><td>Doanh thu thuần</td><td>1.000</td><td>800</td></tr>
      <tr><td>Giá vốn hàng bán</td><td>200</td><td>200</td></tr>
      <tr><td>Tổng cộng</td><td>1.200</td><td>1.000</td></tr>
    </table>
    """
    extracted_ok = build_matrix_from_html_table(html_verified, context_text="Đơn vị: tỷ đồng")
    assert extracted_ok["sanity_gate"]["verification_status"] == "VERIFIED"

    rec_ok = ExtractedTableRecord.from_extraction(
        project_id="SYM1",
        document_version_id="ver-sym1-2025",
        sub_doc_slot="S06_INCOME_STATEMENT",
        fiscal_year=2025,
        table_title="Kết quả kinh doanh 2025",
        page_from=42,
        page_to=42,
        extracted=extracted_ok,
        valid_from=ver1_dt,
        footnotes_text="Kiểm toán chấp nhận toàn phần.",
        table_id="tbl-sym1-verified",
    )

    html_bad = """
    <table>
      <tr><th>Chỉ tiêu</th><th>2025</th><th>2024</th></tr>
      <tr><td>Lợi nhuận gộp</td><td>500</td><td>400</td></tr>
      <tr><td>Chi phí bán hàng</td><td>100</td><td>100</td></tr>
      <tr><td>Tổng cộng</td><td>9.999</td><td>9.999</td></tr>
    </table>
    """
    extracted_bad = build_matrix_from_html_table(html_bad, context_text="Đơn vị: tỷ đồng")
    assert extracted_bad["sanity_gate"]["verification_status"] == "TABLE_UNVERIFIED"
    rec_bad = ExtractedTableRecord.from_extraction(
        project_id="SYM1",
        document_version_id="ver-sym1-2025",
        sub_doc_slot="S07_REV_COGS_DETAIL",
        fiscal_year=2025,
        table_title="Bảng lỗi tổng kiểm toán",
        page_from=45,
        page_to=45,
        extracted=extracted_bad,
        valid_from=ver1_dt,
        table_id="tbl-sym1-unverified",
    )

    rec_future = ExtractedTableRecord.from_extraction(
        project_id="SYM1",
        document_version_id="ver-sym3-2026",
        sub_doc_slot="S06_INCOME_STATEMENT",
        fiscal_year=2026,
        table_title="Bảng tương lai Q4/2026",
        page_from=12,
        page_to=12,
        extracted=extracted_ok,
        valid_from=ver3_dt,
        table_id="tbl-sym1-future",
    )

    for rec in (rec_ok, rec_bad, rec_future):
        session.add(
            SagExtractedTable(
                table_id=rec.table_id,
                project_id=rec.project_id,
                document_version_id=rec.document_version_id,
                sub_doc_slot=rec.sub_doc_slot,
                fiscal_year=rec.fiscal_year,
                fiscal_quarter=rec.fiscal_quarter,
                table_title=rec.table_title,
                section_path=rec.section_path,
                page_from=rec.page_from,
                page_to=rec.page_to,
                currency=rec.currency,
                unit_raw=rec.unit_raw,
                unit_multiplier=rec.unit_multiplier,
                headers_json=rec.headers,
                rows_json=rec.rows,
                footnotes_text=rec.footnotes_text,
                sanity_error_ratio=rec.sanity_error_ratio,
                verification_status=rec.verification_status,
                table_hash=rec.table_hash,
                valid_from=rec.valid_from,
            )
        )
    await session.flush()

    # 3. Track 3 SQLAlchemyKGStore: nodes, CONFIRMED_BOTH macro edge, TRUE_PEER_OF edge, future edge
    kg_store = SQLAlchemyKGStore(session)
    stocks_df = pd.DataFrame(
        [
            {"symbol": "SYM1", "name": "Alpha Corp", "sector": "LOGISTICS", "industry": "Shipping"},
            {"symbol": "SYM2", "name": "Beta Peer Corp", "sector": "LOGISTICS", "industry": "Shipping"},
            {"symbol": "SYM3", "name": "Future Corp", "sector": "LOGISTICS", "industry": "Shipping"},
        ]
    )
    nodes = build_kg_nodes_from_universe(stocks_df, ["usd_vnd_exchange"], date(2026, 4, 13))
    await kg_store.upsert_nodes(nodes)

    snaps = e2.reconcile_macro_with_audited_docs(
        [
            {
                "entity_node_id": "TICKER:SYM1",
                "entity_level": "COMPANY",
                "indicator_name": "usd_vnd_exchange",
                "channel": "FUNDAMENTAL",
                "target_metric": "GPM",
                "lag_periods": 0,
                "beta_value": -0.45,
                "corr_value": -0.61,
                "p_value": 0.008,
                "fdr_q": 0.024,
                "direction_sign": -1,
                "tercile_stable": True,
                "effective_from": "2026-04-13",
            }
        ],
        {
            "SYM1": {
                "usd_vnd_exchange": {
                    "page_no": 168,
                    "stated_sign": -1,
                    "has_quant_sensitivity": True,
                    "quote": quote_txt,
                    "quote_hash": compute_quote_hash(quote_txt),
                    "effective_from": "2026-04-13",
                }
            }
        },
        date(2026, 4, 13),
    )
    await kg_store.upsert_macro_snapshots(snaps)
    await kg_store.upsert_edges(snapshots_to_kg_edges(snaps, date(2026, 4, 13)))
    await kg_store.upsert_edges(
        [
            {
                "edge_id": "peer-sym1-sym2",
                "src_node_id": "TICKER:SYM1",
                "dst_node_id": "TICKER:SYM2",
                "relation_type": "TRUE_PEER_OF",
                "layer": "TRUE_PEER",
                "weight": 0.89,
                "verification_tier": "CLUSTER",
                "effective_from": "2026-04-13",
                "valid_to": "9999-12-31",
            },
            {
                "edge_id": "peer-sym1-sym3-future",
                "src_node_id": "TICKER:SYM1",
                "dst_node_id": "TICKER:SYM3",
                "relation_type": "TRUE_PEER_OF",
                "layer": "TRUE_PEER",
                "weight": 0.95,
                "verification_tier": "CLUSTER",
                "effective_from": "2026-12-01",
                "valid_to": "9999-12-31",
            },
        ]
    )
    await session.commit()
    return {
        "su1_content": su1_content,
        "su1_hash": su1_hash,
        "ver1_ts": int(ver1_dt.timestamp()),
    }


def _build_qdrant_mock_transport(
    *,
    su1_content: str,
    su1_hash: str,
    ver1_ts: int,
    observed_filters: list[dict[str, Any]],
) -> httpx.MockTransport:
    pid_sym1 = generate_search_unit_point_id("search_units_SYM1", "su-sym1-168")
    point_sym1 = {
        "id": pid_sym1,
        "score": 0.94,
        "payload": {
            "search_unit_id": "su-sym1-168",
            "document_version_id": "ver-sym1-2025",
            "project_id": "SYM1",
            "tenant_id": "org-e2e",
            "security_partition_id": "SYM1",
            "content": su1_content,
            "content_hash": su1_hash,
            "page_from": 168,
            "page_to": 168,
            "section_path": "S07_REV_COGS_DETAIL",
            "valid_from_ts": ver1_ts,
        },
    }

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        if path.endswith("/points/count"):
            flt = body.get("filter") or {}
            observed_filters.append(flt)
            if "/search_units_SYM1/" in path:
                return httpx.Response(200, json={"result": {"count": 1}})
            return httpx.Response(200, json={"result": {"count": 0}})
        if path.endswith("/points/query/batch"):
            reqs = body.get("searches") or []
            if "/search_units_SYM1/" in path:
                out = []
                for req in reqs:
                    flt = req.get("filter") or {}
                    observed_filters.append(flt)
                    must = flt.get("must") or []
                    vf_conds = [c for c in must if c.get("key") == "valid_from_ts"]
                    allowed = True
                    if vf_conds:
                        lte_val = vf_conds[0].get("range", {}).get("lte")
                        if lte_val is not None and ver1_ts > int(lte_val):
                            allowed = False
                    out.append({"points": [point_sym1] if allowed else []})
                return httpx.Response(200, json={"result": out})
            return httpx.Response(200, json={"result": [{"points": []} for _ in reqs]})
        if "/points/" in path:
            if pid_sym1 in path:
                return httpx.Response(200, json={"result": point_sym1})
            return httpx.Response(404, json={"status": "not found"})
        return httpx.Response(404, json={})

    return httpx.MockTransport(respond)


@pytest.mark.asyncio
async def test_phase1_full_e2e_app_wiring_normal_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise FastAPI `app` E2E across Track 1 ORM/SearchUnits + Track 2 Table/Calc/Disc + Track 3 KG/Verifier."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as seed_session:
        seeded = await _seed_e2e_db(seed_session)

    # Verify Track 1 DeepSeek-V4-Flash LiteLLM policy alongside the E2E flow
    policy = apply_litellm_completion_policy(
        config_mod.settings,
        {"model": "openai/deepseek-ai/DeepSeek-V4-Flash-0731", "temperature": 0.2},
    )
    assert policy["temperature"] == 0.2
    assert policy["extra_body"]["thinking"] == {"type": "disabled"}

    # Patch SessionLocal used inside Track 1 canonical search_unit_retrieval_service
    monkeypatch.setattr(surs, "SessionLocal", session_factory)

    observed_filters: list[dict[str, Any]] = []
    qdrant_transport = _build_qdrant_mock_transport(
        su1_content=seeded["su1_content"],
        su1_hash=seeded["su1_hash"],
        ver1_ts=seeded["ver1_ts"],
        observed_filters=observed_filters,
    )
    async with httpx.AsyncClient(base_url="https://qdrant.invalid", transport=qdrant_transport) as qclient:
        app = create_app()
        app.state.engine_manager = _FakeEngineManager(qclient)
        app.state.llm = _FakeLLM(
            "Theo thuyết minh [C1], doanh thu tăng +25.00% và tỷ giá tác động biên gộp."
        )

        async def _override_get_session() -> AsyncIterator[AsyncSession]:
            async with session_factory() as s:
                yield s

        app.dependency_overrides[get_session] = _override_get_session
        monkeypatch.setattr(config_mod.settings, "bff_service_token", "phase1-e2e-bff-token-0123456789abcdef")

        headers_multi = {
            "X-SAG-BFF-Service-Token": "phase1-e2e-bff-token-0123456789abcdef",
            "X-Continuum-User-Id": "analyst-e2e",
            "X-Continuum-Organization-Id": "org-e2e",
            "X-Continuum-Project-Id": "all",
            "X-Continuum-Security-Partition": "all",
            "X-Continuum-Allowed-Project-Ids": "SYM1,SYM2,SYM3",
        }
        headers_sym1 = {
            "X-SAG-BFF-Service-Token": "phase1-e2e-bff-token-0123456789abcdef",
            "X-Continuum-User-Id": "analyst-e2e",
            "X-Continuum-Organization-Id": "org-e2e",
            "X-Continuum-Project-Id": "SYM1",
            "X-Continuum-Security-Partition": "SYM1",
        }

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Normal E2E SSE stream at PIT = 2026-11-20 (SYM1 + True-Peer SYM2, SYM3 future blocked)
            resp = await client.post(
                "/api/v1/research-chat/stream",
                headers=headers_multi,
                json={
                    "query": "So sánh peer và tính tác động nếu tỷ giá tăng 5% tới biên lợi nhuận của SYM1",
                    "ticker": "SYM1",
                    "as_of_date": "2026-11-20",
                    "enable_kg": True,
                },
            )
            assert resp.status_code == 200
            events = _parse_sse(resp.text)
            stage_names = [p["stage"] for ev, p in events if ev == "stage"]
            for expected_stage in (
                "routing",
                "retrieval",
                "graph_hop",
                "table_hydration",
                "calculator",
                "synthesis",
                "verification",
            ):
                assert expected_stage in stage_names

            done_payload = next(p for ev, p in events if ev == "done")
            meta = done_payload["metadata"]

            assert meta["mode"] == "RESEARCH_CHAT_V2"
            assert meta["primary_ticker"] == "SYM1"
            assert "SYM2" in meta["expanded_tickers"]
            assert "SYM3" not in meta["expanded_tickers"]

            # Verify Track 1 Qdrant filter actually received `valid_from_ts <= as_of_ts`!
            assert any(
                any(c.get("key") == "valid_from_ts" for c in (flt.get("must") or []))
                for flt in observed_filters
            )

            # Track 1 quote_hash Post-Verifier: C1 verified against real CanonicalBlock in DB
            assert len(meta["citations"]) == 1
            assert meta["citations"][0]["verified"] is True
            assert meta["citations"][0]["sub_doc_slot"] == "S07_REV_COGS_DETAIL"
            assert meta["citations"][0]["page"] == 168

            # Track 2 Hydrated Tables: VERIFIED + TABLE_UNVERIFIED present, future tbl-sym1-future blocked!
            table_ids = {t["table_id"] for t in meta["hydrated_tables"]}
            assert "tbl-sym1-verified" in table_ids
            assert "tbl-sym1-unverified" in table_ids
            assert "tbl-sym1-future" not in table_ids

            # Track 2 Calculator: ran on 2D-header VERIFIED table (YoY) + CONFIRMED_BOTH macro shock
            assert len(meta["calculator_traces"]) >= 2
            calc_inputs_text = json.dumps(meta["calculator_traces"])
            assert "tbl-sym1-verified" in calc_inputs_text
            assert "tbl-sym1-unverified" not in calc_inputs_text

            # Track 2 Discrepancy Detector: narrative 950 vs audited 1000 (5% delta >= 1.5% threshold)
            assert len(meta["discrepancy_alerts"]) >= 1
            assert meta["discrepancy_alerts"][0]["metric"] == "Doanh thu thuần"

            # Blindspots: TABLE_UNVERIFIED blocked
            blind_codes = {b["code"] for b in meta["declared_blindspots"]}
            assert "MISSING_TABLE_VERIFIED" in blind_codes

            # 2. Strict PIT Zero-Leak at as_of_date = 2025-06-30 -> 0 docs, 0 tables, 0 KG edges -> NO_DOC_COVERAGE
            resp_past = await client.post(
                "/api/v1/research-chat/stream",
                headers=headers_sym1,
                json={
                    "query": "Doanh thu SYM1?",
                    "ticker": "SYM1",
                    "as_of_date": "2025-06-30",
                    "enable_kg": True,
                },
            )
            assert resp_past.status_code == 200
            past_done = next(p for ev, p in _parse_sse(resp_past.text) if ev == "done")
            past_meta = past_done["metadata"]
            assert past_meta["citations"] == []
            assert past_meta["hydrated_tables"] == []
            assert past_meta["graph_hops"] == []
            assert any(b["code"] == "NO_DOC_COVERAGE" for b in past_meta["declared_blindspots"])
            assert "INSUFFICIENT_EVIDENCE" in past_done["answer"]

            # 3. GET /api/v1/research-chat/kg/SYM1 PIT enforcement
            kg_ok = await client.get("/api/v1/research-chat/kg/SYM1?as_of_date=2026-11-20", headers=headers_sym1)
            assert kg_ok.status_code == 200
            kg_body = kg_ok.json()
            assert [p["ticker"] for p in kg_body["true_peers"]] == ["SYM2"]
            assert any(s["verification_tier"] == "CONFIRMED_BOTH" for s in kg_body["macro_snapshots"])

            kg_past = await client.get("/api/v1/research-chat/kg/SYM1?as_of_date=2025-06-30", headers=headers_sym1)
            assert kg_past.status_code == 200
            assert kg_past.json()["edges"] == []
            assert kg_past.json()["true_peers"] == []

            # 4. GET /api/v1/research-chat/tables/{table_id} normal + PIT fail-closed 404
            tbl_ok = await client.get(
                "/api/v1/research-chat/tables/tbl-sym1-verified?as_of_date=2026-11-20",
                headers=headers_sym1,
            )
            assert tbl_ok.status_code == 200
            tbl_json = tbl_ok.json()
            assert tbl_json["verification_status"] == "VERIFIED"
            assert tbl_json["headers"] == [["Chỉ tiêu", "2025", "2024"]]
            assert tbl_json["rows"][0]["label"] == "Doanh thu thuần"

            tbl_future_blocked = await client.get(
                "/api/v1/research-chat/tables/tbl-sym1-future?as_of_date=2026-11-20",
                headers=headers_sym1,
            )
            assert tbl_future_blocked.status_code == 404

            # 5. Fail-closed Auth & 6-ticker ceiling checks
            bad_auth = await client.get("/api/v1/research-chat/kg/SYM1")
            assert bad_auth.status_code in (401, 403)

            too_many = await client.post(
                "/api/v1/research-chat/stream",
                headers=headers_multi,
                json={
                    "query": "So sánh",
                    "ticker": "SYM1",
                    "compare_tickers": ["SYM2", "SYM3", "SYM4", "SYM5", "SYM6", "SYM7"],
                },
            )
            assert too_many.status_code == 400

    await engine.dispose()
    assert ResearchChatOrchestrator is not None
