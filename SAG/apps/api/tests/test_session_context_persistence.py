"""Integration tests for SAG-ECCE Chặng 3: Multi-View Persistence, Cold-Start Hydration,
Lazy Backfill, O(Delta) Sync, Qdrant `session_units_{project_id}`, and Fail-Closed Reconciliation.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from sag_api.core.config import settings
from sag_api.core.errors import ForbiddenError
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import (
    Agent,
    Message,
    SessionDecisionLedger,
    SessionGraphEdge,
    SessionUnit,
    SessionUnitMessage,
    Source,
    SourceProjectMapping,
    Thread,
)
from sag_api.enums import MessageRole
from sag_api.services import agent_domain
from sag_api.services.search_index_service import _SHARED_TEXT_EMBEDDING_CACHE
from sag_api.services.session_context import (
    SESSION_INDEX_CACHE,
    prepare_session_context_for_ask,
    reconcile_orphan_session_units,
    session_qdrant_collection_name,
    set_custom_session_qdrant_client_factory,
)
from sag_api.services.session_context.substrate import deterministic_ngram_vector


def _principal(*, org_id: str = "org-continuum", project_id: str = "proj-alpha") -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="user-thang",
        organization_id=org_id,
        allowed_project_ids=frozenset({project_id}),
        issuer="continuum-be",
        key_id="kid-1",
        token_id="jti-1",
        issued_at=1_760_000_000,
        expires_at=1_760_000_900,
        tenant_id=org_id,
        allowed_partition_ids=frozenset({project_id}),
    )


async def _make_fk_sqlite_engine() -> AsyncEngine:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _conn_record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


class _InMemoryQdrantEmulator:
    """Faithful in-memory Qdrant REST emulator for collections, indexes, points, scroll, and delete."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, Any]] = {}
        self.indexes: dict[str, dict[str, str]] = {}
        self.points: dict[str, dict[str, dict[str, Any]]] = {}
        self.fail_status_code: int | None = None

    def make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://qdrant.local",
            transport=httpx.MockTransport(self._handle),
        )

    @staticmethod
    def _matches_filter(payload: dict[str, Any], flt: dict[str, Any] | None) -> bool:
        if not flt:
            return True
        for cond in flt.get("must") or []:
            key = cond.get("key")
            match = cond.get("match") or {}
            val = payload.get(key)
            if "value" in match:
                expected = match["value"]
                if isinstance(val, list):
                    if expected not in val:
                        return False
                elif val != expected:
                    return False
            elif "any" in match:
                allowed = set(match["any"])
                if isinstance(val, list):
                    if not (set(val) & allowed):
                        return False
                elif val not in allowed:
                    return False
        return True

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail_status_code is not None:
            return httpx.Response(
                self.fail_status_code,
                json={"status": {"error": f"Simulated Qdrant HTTP {self.fail_status_code}"}},
            )
        parts = [p for p in request.url.path.split("/") if p]
        method = request.method.upper()
        body = json.loads(request.content.decode("utf-8")) if request.content else {}

        # /collections/{name}
        if len(parts) == 2 and parts[0] == "collections":
            col = parts[1]
            if method == "GET":
                if col not in self.collections:
                    return httpx.Response(404, json={"status": {"error": "Not found"}})
                return httpx.Response(
                    200,
                    json={"result": {"config": {"params": self.collections[col]}}},
                )
            if method == "PUT":
                self.collections[col] = {
                    "vectors": body.get("vectors") or {},
                    "sparse_vectors": body.get("sparse_vectors") or {},
                }
                self.indexes.setdefault(col, {})
                self.points.setdefault(col, {})
                return httpx.Response(200, json={"result": True})
            if method == "DELETE":
                self.collections.pop(col, None)
                self.indexes.pop(col, None)
                self.points.pop(col, None)
                return httpx.Response(200, json={"result": True})

        # /collections/{name}/index
        if len(parts) == 3 and parts[0] == "collections" and parts[2] == "index" and method == "PUT":
            col = parts[1]
            if col not in self.collections:
                return httpx.Response(404, json={"status": {"error": "Not found"}})
            self.indexes[col][str(body.get("field_name"))] = str(body.get("field_schema"))
            return httpx.Response(200, json={"result": {"status": "completed"}})

        # /collections/{name}/points
        if len(parts) == 3 and parts[0] == "collections" and parts[2] == "points" and method == "PUT":
            col = parts[1]
            if col not in self.collections:
                return httpx.Response(404, json={"status": {"error": "Collection missing"}})
            expected_dim = (
                self.collections[col]
                .get("vectors", {})
                .get("content_vector", {})
                .get("size")
            )
            for pt in body.get("points") or []:
                c_vec = (pt.get("vector") or {}).get("content_vector") or []
                if expected_dim and len(c_vec) != expected_dim:
                    return httpx.Response(400, json={"status": {"error": "Dimension mismatch"}})
                self.points[col][str(pt["id"])] = {
                    "id": str(pt["id"]),
                    "payload": dict(pt.get("payload") or {}),
                    "vector": dict(pt.get("vector") or {}),
                }
            return httpx.Response(200, json={"result": {"status": "completed"}})

        # /collections/{name}/points/scroll
        if (
            len(parts) == 4
            and parts[0] == "collections"
            and parts[2] == "points"
            and parts[3] == "scroll"
            and method == "POST"
        ):
            col = parts[1]
            if col not in self.collections:
                return httpx.Response(404, json={"status": {"error": "Not found"}})
            flt = body.get("filter")
            with_vec = bool(body.get("with_vector"))
            matched = [
                {
                    "id": pt["id"],
                    "payload": dict(pt["payload"]),
                    **({"vector": dict(pt["vector"])} if with_vec else {}),
                }
                for pt in self.points[col].values()
                if self._matches_filter(pt["payload"], flt)
            ]
            return httpx.Response(
                200,
                json={"result": {"points": matched, "next_page_offset": None}},
            )

        # /collections/{name}/points/delete
        if (
            len(parts) == 4
            and parts[0] == "collections"
            and parts[2] == "points"
            and parts[3] == "delete"
            and method == "POST"
        ):
            col = parts[1]
            if col not in self.collections:
                return httpx.Response(404, json={"status": {"error": "Not found"}})
            if "points" in body:
                for pid in body["points"]:
                    self.points[col].pop(str(pid), None)
            elif "filter" in body:
                flt = body["filter"]
                to_del = [
                    pid
                    for pid, pt in self.points[col].items()
                    if self._matches_filter(pt["payload"], flt)
                ]
                for pid in to_del:
                    self.points[col].pop(pid, None)
            return httpx.Response(200, json={"result": {"status": "completed"}})

        return httpx.Response(404, json={"status": {"error": f"Unhandled {method} {request.url.path}"}})


class _CountingHttpModelEmbedder:
    """Counting embedder simulating `configured_http` (`http:BAAI/bge-m3`, dim=64)."""

    def __init__(self, *, model: str = "BAAI/bge-m3", dim: int = 64) -> None:
        self.model = model
        self.dim = dim
        self.batches: list[list[str]] = []

    async def batch_generate(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [list(deterministic_ngram_vector(t, dim=self.dim)) for t in texts]


@pytest.fixture(autouse=True)
def _clean_persistence_caches():
    SESSION_INDEX_CACHE.clear()
    _SHARED_TEXT_EMBEDDING_CACHE.clear()
    set_custom_session_qdrant_client_factory(None)
    yield
    SESSION_INDEX_CACHE.clear()
    _SHARED_TEXT_EMBEDDING_CACHE.clear()
    set_custom_session_qdrant_client_factory(None)


@pytest.mark.asyncio
async def test_lazy_backfill_and_incremental_delta_sync_with_qdrant(monkeypatch) -> None:
    """Chặng 3.1: Lazy Backfill on legacy thread + O(Delta) append + trigger-safe EPISODE growth + Qdrant."""
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-thang-ch3"
    principal = _principal(project_id="proj-alpha")
    col_name = session_qdrant_collection_name("proj-alpha")
    now = datetime.now(UTC) - timedelta(minutes=5)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Continuum Architect")
        src_doc = Source(name="ADR-09 Payment Spec", sag_source_config_id="sag-adr-09")
        session.add_all([agent, src_doc])
        await session.flush()

        mapping = SourceProjectMapping(
            source_id=src_doc.id,
            organization_id=principal.organization_id,
            project_id="proj-alpha",
            state="CONFIRMED",
            mapping_version=1,
            confirmed_at=now,
            confirmed_by="admin-1",
            approval_ref="apr-ch3-1",
        )
        thread = Thread(agent_id=agent.id, owner_key=owner_key, title="Payment Core Session")
        session.add_all([mapping, thread])
        await session.flush()

        # Pre-existing legacy thread (0 session_units rows initially)
        m0 = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Đề xuất ban đầu: dịch vụ thanh toán lõi dùng MongoDB cho nhanh.",
            created_at=now,
        )
        m1 = Message(
            thread_id=thread.id,
            role=MessageRole.ASSISTANT,
            content="Theo tài liệu ADR-09, giao dịch tài chính bắt buộc chuẩn ACID khắt khe.",
            citations=[{"n": 1, "source_id": src_doc.id, "chunk_id": "chk-acid"}],
            created_at=now + timedelta(seconds=30),
        )
        m2 = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Chốt quyết định cuối cùng: thay MongoDB bằng PostgreSQL 18 cho dịch vụ thanh toán lõi.",
            created_at=now + timedelta(seconds=60),
        )
        session.add_all([m0, m1, m2])
        await session.commit()

        # 1. First ask triggers Lazy Backfill across all 4 tables + Qdrant!
        plan_1 = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Dịch vụ thanh toán lõi hiện chốt dùng database nào?",
            principal=principal,
            owner_key=owner_key,
        )
        trace_1 = plan_1.session_audit_trace
        assert trace_1 is not None
        assert trace_1["persistence_status"] == "lazy_backfill"
        assert trace_1["persisted_units_written"] == 7  # 3 TURN + 3 WINDOW + 1 EPISODE
        assert trace_1["qdrant_points_upserted"] == 7

        # Verify SessionUnit rows & Data Minimization (character spans in sentences_json, zero raw text!)
        units = list(
            (
                await session.execute(
                    select(SessionUnit)
                    .where(SessionUnit.thread_id == thread.id)
                    .order_by(SessionUnit.unit_kind.asc(), SessionUnit.ordinal.asc())
                )
            )
            .scalars()
            .all()
        )
        by_kind = {k: [u for u in units if u.unit_kind == k] for k in ("TURN", "WINDOW", "EPISODE")}
        assert len(by_kind["TURN"]) == 3
        assert len(by_kind["WINDOW"]) == 3
        assert len(by_kind["EPISODE"]) == 1
        assert by_kind["TURN"][0].epistemic_status == "SUPERSEDED"
        assert by_kind["TURN"][0].valid_to.year < 9999
        assert by_kind["TURN"][2].epistemic_status == "CONFIRMED"
        assert by_kind["TURN"][2].valid_to.year == 9999
        for sent_meta in by_kind["TURN"][2].sentences_json:
            assert "text" not in sent_meta
            assert isinstance(sent_meta["start"], int) and isinstance(sent_meta["end"], int)

        # Verify trigger-populated SessionUnitMessage links
        link_count = await session.scalar(
            select(func.count()).select_from(SessionUnitMessage).where(SessionUnitMessage.thread_id == thread.id)
        )
        # 3 turns (1+1+1) + 3 windows (1+2+3) + 1 episode (3) = 12 membership links!
        assert link_count == 12

        # Verify SessionDecisionLedger lineage (`supersedes_id` <-> `superseded_by_id`)
        decisions = list(
            (
                await session.execute(
                    select(SessionDecisionLedger)
                    .where(SessionDecisionLedger.thread_id == thread.id)
                    .order_by(SessionDecisionLedger.turn_ordinal.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(decisions) == 2
        assert decisions[0].epistemic_status == "SUPERSEDED"
        assert decisions[0].superseded_by_id == decisions[1].id
        assert decisions[1].epistemic_status == "CONFIRMED"
        assert decisions[1].supersedes_id == decisions[0].id

        # Verify SessionGraphEdge types (`ENTITY_MENTION`, `SENTENCE_IN_UNIT`, `ADJACENT`, `SUPERSEDES`, `SOURCE`)
        edge_types = set(
            (
                await session.execute(
                    select(SessionGraphEdge.edge_type).where(SessionGraphEdge.thread_id == thread.id)
                )
            )
            .scalars()
            .all()
        )
        assert {
            "ENTITY_MENTION",
            "SENTENCE_IN_UNIT",
            "ADJACENT_IN_EPISODE",
            "SUPERSEDES",
            "CITES_DOCUMENT_SOURCE",
        }.issubset(edge_types)

        # Verify Qdrant payload Data Minimization (ZERO raw conversation strings!)
        assert col_name in qdrant.points
        assert len(qdrant.points[col_name]) == 7
        raw_qdrant_dump = json.dumps(qdrant.points[col_name], ensure_ascii=False)
        assert "MongoDB cho nhanh" not in raw_qdrant_dump
        assert "ACID khắt khe" not in raw_qdrant_dump
        for pt in qdrant.points[col_name].values():
            assert "content" not in pt["payload"]
            assert "summary_text" not in pt["payload"]
            assert len(pt["vector"]["content_vector"]) == 256
            assert "indices" in pt["vector"]["bm25_sparse"]

        # 2. Persist assistant reply & run 2nd ask on warm cache -> O(Delta) sync + grown EPISODE replacement!
        await agent_domain.persist_answer(
            session_factory,
            thread.id,
            "Đã xác nhận dịch vụ thanh toán lõi dùng PostgreSQL 18.",
            [],
        )
        plan_2 = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Kiểm tra lại hạn mức kết nối connection pool?",
            principal=principal,
            owner_key=owner_key,
        )
        trace_2 = plan_2.session_audit_trace
        assert trace_2 is not None
        assert trace_2["persistence_status"] == "delta_synced"
        # 2 delta turns (prev user query + assistant answer) -> 2 TURN + 2 WINDOW + 1 grown EPISODE = 5 (< 11 total!)
        assert trace_2["persisted_units_written"] == 5
        ep_after = (
            await session.execute(
                select(SessionUnit).where(
                    SessionUnit.thread_id == thread.id,
                    SessionUnit.unit_kind == "EPISODE",
                )
            )
        ).scalar_one()
        assert len(ep_after.source_message_ids_json) == 5
        assert len(qdrant.points[col_name]) == 11  # 5 TURN + 5 WINDOW + 1 EPISODE

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_cold_start_worker_restart_hydrates_from_db_and_qdrant_with_zero_http_reembeds(
    monkeypatch,
) -> None:
    """Chặng 3.2: Cold-start worker restart hydrates prefix from DB + Qdrant with 0 HTTP turn re-embeds."""
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)
    http_embedder = _CountingHttpModelEmbedder(model="BAAI/bge-m3", dim=64)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-cold-start"
    principal = _principal(project_id="proj-cold")
    now = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Cold Start Agent")
        thread = Thread(agent_id="", owner_key=owner_key, title="Cold Start Thread")
        session.add(agent)
        await session.flush()
        thread.agent_id = agent.id
        session.add(thread)
        await session.flush()

        for idx, text_body in enumerate(
            [
                "Đề xuất dùng Redis Cluster cho tầng session cache.",
                "Cân nhắc thêm chi phí vận hành hạ tầng Redis Cluster.",
                "Chốt quyết định: dùng PostgreSQL UNLOGGED TABLE thay cho Redis Cluster để giảm chi phí.",
                "Ghi nhận cấu hình TTL dọn bảng UNLOGGED mỗi 15 phút.",
                "Câu hỏi hiện tại?",
            ]
        ):
            session.add(
                Message(
                    thread_id=thread.id,
                    role=MessageRole.USER if idx % 2 == 0 else MessageRole.ASSISTANT,
                    content=text_body,
                    created_at=now + timedelta(minutes=idx),
                )
            )
        await session.commit()
        msgs = list(
            (
                await session.execute(
                    select(Message).where(Message.thread_id == thread.id).order_by(Message.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        current_q_msg = msgs[-1]

        # Initial cold materialization (embeds 4 historical turns + their sentences + query)
        pack_initial = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=current_q_msg.id,
            query="Chúng ta đã chốt giải pháp gì thay cho Redis Cluster?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=1200,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert pack_initial.audit_trace.persistence_status == "lazy_backfill"
        assert len(http_embedder.batches) >= 2  # historical batch + query batch

        # Simulate complete worker process restart: clear BOTH RAM session cache AND text embedding LRU!
        SESSION_INDEX_CACHE.clear()
        _SHARED_TEXT_EMBEDDING_CACHE.clear()
        http_embedder.batches.clear()

        pack_hydrated = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=current_q_msg.id,
            query="Chúng ta đã chốt giải pháp gì thay cho Redis Cluster?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=1200,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert pack_hydrated.audit_trace.persistence_status == "hydrated_clean"
        assert pack_hydrated.audit_trace.persisted_units_written == 0
        # ZERO historical turns re-embedded over HTTP! Only the 1 live query string was embedded!
        assert http_embedder.batches == [["Chúng ta đã chốt giải pháp gì thay cho Redis Cluster?"]]
        hydrated_blob = json.dumps(pack_hydrated.history_messages, ensure_ascii=False)
        assert "UNLOGGED TABLE" in hydrated_blob

        # Now add 1 new historical turn + new ask message, simulate another worker restart:
        # ONLY the 1 new historical turn is embedded (`hydrated_delta`)!
        new_q_msg = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Truy vấn tiếp theo sau khi thêm 1 lượt mới?",
            created_at=now + timedelta(minutes=10),
        )
        session.add(new_q_msg)
        await session.commit()

        SESSION_INDEX_CACHE.clear()
        _SHARED_TEXT_EMBEDDING_CACHE.clear()
        http_embedder.batches.clear()

        pack_delta = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=new_q_msg.id,
            query="TTL dọn bảng UNLOGGED là bao nhiêu phút?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=1200,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert pack_delta.audit_trace.persistence_status == "hydrated_delta"
        # First batch embeds ONLY `current_q_msg` ("Câu hỏi hiện tại?"), NEVER the 4 earlier turns!
        flattened_embedded = [txt for batch in http_embedder.batches for txt in batch]
        assert not any("UNLOGGED TABLE" in txt for txt in flattened_embedded)
        assert any("Câu hỏi hiện tại?" in txt for txt in flattened_embedded)

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_middle_of_window_delete_via_raw_sql_and_api_reconciles_db_and_qdrant(
    monkeypatch,
) -> None:
    """Chặng 3.3: Deleting middle message B in A-B-C reconciles ordinals, lineage, windows, and Qdrant."""
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-del-ch3"
    principal = _principal(project_id="proj-del")
    col_name = session_qdrant_collection_name("proj-del")
    now = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Delete Reconcile Agent")
        session.add(agent)
        await session.flush()
        thread = Thread(agent_id=agent.id, owner_key=owner_key, title="Window ABC Thread")
        session.add(thread)
        await session.flush()

        msg_a = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Đề xuất dùng MongoDB cho module lưu vết kiểm toán.",
            created_at=now,
        )
        msg_b = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Chốt quyết định: thay MongoDB bằng PostgreSQL 18 cho module lưu vết kiểm toán.",
            created_at=now + timedelta(seconds=20),
        )
        msg_c = Message(
            thread_id=thread.id,
            role=MessageRole.ASSISTANT,
            content="Đã ghi nhận kế hoạch kiểm thử tải 5000 RPS vào thứ Sáu.",
            created_at=now + timedelta(seconds=40),
        )
        session.add_all([msg_a, msg_b, msg_c])
        await session.commit()

        _ = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Module lưu vết kiểm toán đang chốt dùng gì?",
            principal=principal,
            owner_key=owner_key,
        )
        # Before deleting B: A is SUPERSEDED by B
        dec_before = list(
            (
                await session.execute(
                    select(SessionDecisionLedger)
                    .where(SessionDecisionLedger.thread_id == thread.id)
                    .order_by(SessionDecisionLedger.turn_ordinal.asc())
                )
            )
            .scalars()
            .all()
        )
        assert dec_before[0].epistemic_status == "SUPERSEDED"
        assert any(
            pt["payload"]["message_from_id"] == msg_b.id for pt in qdrant.points[col_name].values()
        )

        # Part A: Raw SQL DELETE of middle message B (bypasses service layer!)
        await session.execute(text("DELETE FROM messages WHERE id = :mid"), {"mid": msg_b.id})
        await session.commit()

        # Immediately after raw SQL DELETE: ON DELETE CASCADE already removed B's unit & window A-B-C!
        remaining_b_units = (
            await session.execute(
                select(SessionUnit).where(SessionUnit.message_from_id == msg_b.id)
            )
        ).scalars().all()
        assert remaining_b_units == []

        # Next ask detects ordinal gap (`[0, 2]` vs `[0, 1]`) -> `reconciled_rebuild` restores A to PROPOSED
        # and purges B's stale points from Qdrant!
        plan_reconciled = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Đề xuất hiện còn hiệu lực cho module lưu vết kiểm toán là gì?",
            principal=principal,
            owner_key=owner_key,
        )
        assert plan_reconciled.session_audit_trace is not None
        assert plan_reconciled.session_audit_trace["persistence_status"] == "reconciled_rebuild"

        dec_after = list(
            (
                await session.execute(
                    select(SessionDecisionLedger)
                    .where(SessionDecisionLedger.thread_id == thread.id)
                    .order_by(SessionDecisionLedger.turn_ordinal.asc())
                )
            )
            .scalars()
            .all()
        )
        assert dec_after[0].epistemic_status == "PROPOSED"
        assert dec_after[0].superseded_by_id is None
        assert dec_after[0].valid_to.year == 9999
        assert all(
            pt["payload"]["message_from_id"] != msg_b.id
            and pt["payload"]["message_to_id"] != msg_b.id
            for pt in qdrant.points[col_name].values()
        )

        # Part B: API `delete_message` immediately purges derived DB rows & Qdrant points for the thread!
        await agent_domain.delete_message(
            session,
            agent.id,
            thread.id,
            msg_a.id,
            owner_key=owner_key,
        )
        db_units_after_api_del = await session.scalar(
            select(func.count()).select_from(SessionUnit).where(SessionUnit.thread_id == thread.id)
        )
        assert db_units_after_api_del == 0
        assert len(qdrant.points[col_name]) == 0

        # Re-populate via ask, then call `delete_thread` -> verifies full thread cleanup in DB + Qdrant
        _ = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Kiểm tra lại kế hoạch thứ Sáu?",
            principal=principal,
            owner_key=owner_key,
        )
        assert len(qdrant.points[col_name]) > 0
        await agent_domain.delete_thread(session, agent.id, thread.id, owner_key=owner_key)
        assert len(qdrant.points[col_name]) == 0

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_continuum_acl_revocation_purges_storage_and_cross_tenant_blocked_fail_closed(
    monkeypatch,
) -> None:
    """Chặng 3.4: Source revocation purges persisted units & Qdrant; cross-tenant principal raises 403."""
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-sec-ch3"
    principal_alpha = _principal(org_id="org-alpha", project_id="proj-alpha")
    col_name = session_qdrant_collection_name("proj-alpha")
    now = datetime(2026, 10, 28, 4, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Security Agent")
        src_secret = Source(name="Confidential M&A", sag_source_config_id="sag-mna-1")
        src_public = Source(name="Public Handbook", sag_source_config_id="sag-pub-1")
        session.add_all([agent, src_secret, src_public])
        await session.flush()

        map_secret = SourceProjectMapping(
            source_id=src_secret.id,
            organization_id="org-alpha",
            project_id="proj-alpha",
            state="CONFIRMED",
            mapping_version=1,
            confirmed_at=now,
            confirmed_by="admin-1",
            approval_ref="apr-sec-1",
        )
        map_public = SourceProjectMapping(
            source_id=src_public.id,
            organization_id="org-alpha",
            project_id="proj-alpha",
            state="CONFIRMED",
            mapping_version=1,
            confirmed_at=now,
            confirmed_by="admin-1",
            approval_ref="apr-pub-1",
        )
        thread = Thread(agent_id=agent.id, owner_key=owner_key, title="M&A Thread")
        session.add_all([map_secret, map_public, thread])
        await session.flush()

        m_user = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Tóm tắt định giá thương vụ M&A mã TITAN-900?",
            created_at=now,
        )
        m_secret = Message(
            thread_id=thread.id,
            role=MessageRole.ASSISTANT,
            content="Thương vụ TITAN-900 có mức định giá trần là 82.5 triệu USD.",
            citations=[{"n": 1, "source_id": src_secret.id, "chunk_id": "chk-mna"}],
            created_at=now + timedelta(seconds=15),
        )
        session.add_all([m_user, m_secret])
        await session.commit()

        # 1. While CONFIRMED, `m_secret` is indexed in DB & Qdrant
        _ = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Mức định giá trần của TITAN-900 là bao nhiêu?",
            principal=principal_alpha,
            owner_key=owner_key,
        )
        assert any(
            src_secret.id in pt["payload"]["cited_source_ids"]
            for pt in qdrant.points[col_name].values()
        )

        # 2. Temporary per-turn `@src_public` filter excludes `m_secret` from prompt WITHOUT wiping DB!
        plan_scoped = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Mức định giá trần của TITAN-900 là bao nhiêu?",
            source_ids=[src_public.id],
            principal=principal_alpha,
            owner_key=owner_key,
        )
        assert "82.5 triệu USD" not in json.dumps(plan_scoped.messages, ensure_ascii=False)
        still_persisted = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.message_from_id == m_secret.id)
        )
        assert still_persisted and still_persisted >= 1

        # 3. Cross-tenant / cross-project principal attempting access on `thread.id` -> ForbiddenError!
        attacker_principal = _principal(org_id="org-evil", project_id="proj-evil")
        with pytest.raises(ForbiddenError):
            await agent_domain.prepare_ask(
                session,
                agent=agent,
                thread=thread,
                query="Đọc lén TITAN-900?",
                principal=attacker_principal,
                owner_key=owner_key,
            )

        # 4. Project-level REVOCATION of `src_secret` -> purges `m_secret` from DB AND Qdrant!
        map_secret.state = "REVOKED"
        map_secret.revoked_at = datetime.now(UTC)
        map_secret.revoked_by = "sec-ops"
        map_secret.revocation_ref = "rev-sec-1"
        await session.commit()

        plan_revoked = await agent_domain.prepare_ask(
            session,
            agent=agent,
            thread=thread,
            query="Mức định giá trần của TITAN-900 là bao nhiêu?",
            principal=principal_alpha,
            owner_key=owner_key,
        )
        assert plan_revoked.session_audit_trace is not None
        assert plan_revoked.session_audit_trace["persistence_status"] == "reconciled_rebuild"
        assert "82.5 triệu USD" not in json.dumps(plan_revoked.messages, ensure_ascii=False)

        revoked_db_units = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.message_from_id == m_secret.id)
        )
        assert revoked_db_units == 0
        assert all(
            src_secret.id not in pt["payload"]["cited_source_ids"]
            for pt in qdrant.points[col_name].values()
        )

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_embedding_signature_drift_recreates_qdrant_and_orphan_reconciler(
    monkeypatch,
) -> None:
    """Chặng 3.5: Embedding model signature change recreates derived Qdrant dim & orphan reconciler."""
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-drift-ch3"
    principal = _principal(project_id="proj-drift")
    col_name = session_qdrant_collection_name("proj-drift")
    now = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Drift Agent")
        session.add(agent)
        await session.flush()
        thread = Thread(agent_id=agent.id, owner_key=owner_key, title="Model Drift Thread")
        session.add(thread)
        await session.flush()

        m0 = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Chốt ngưỡng cảnh báo độ trễ p95 của API là 250ms.",
            created_at=now,
        )
        m_ask = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Ngưỡng p95 là bao nhiêu?",
            created_at=now + timedelta(seconds=10),
        )
        session.add_all([m0, m_ask])
        await session.commit()

        # 1. Default deterministic_ngram (`dim=256`, `deterministic-ngram-v1`)
        pack_det = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask.id,
            query="Ngưỡng p95 là bao nhiêu?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=800,
            embedder=None,
            qdrant_client=q_client,
        )
        assert pack_det.audit_trace.persistence_status == "lazy_backfill"
        assert qdrant.collections[col_name]["vectors"]["content_vector"]["size"] == 256

        # 2. Switch to HTTP embedder (`http:BAAI/bge-m3`, `dim=64`) WITHOUT clearing warm cache!
        bge_embedder = _CountingHttpModelEmbedder(model="BAAI/bge-m3", dim=64)
        pack_bge = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask.id,
            query="Ngưỡng p95 là bao nhiêu?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=800,
            embedder=bge_embedder,
            qdrant_client=q_client,
        )
        assert pack_bge.audit_trace.persistence_status == "reconciled_rebuild"
        assert qdrant.collections[col_name]["vectors"]["content_vector"]["size"] == 64
        sigs = set(
            (
                await session.execute(
                    select(SessionUnit.embedding_model_signature).where(SessionUnit.thread_id == thread.id)
                )
            )
            .scalars()
            .all()
        )
        assert sigs == {"http:BAAI/bge-m3"}

        # 3. Inject 2 orphan Qdrant points and run `reconcile_orphan_session_units`
        valid_count_before = len(qdrant.points[col_name])
        for _idx in range(2):
            fake_pid = str(uuid.uuid4())
            qdrant.points[col_name][fake_pid] = {
                "id": fake_pid,
                "payload": {
                    "session_unit_id": str(uuid.uuid4()),
                    "thread_id": thread.id,
                    "project_id": "proj-drift",
                },
                "vector": {"content_vector": [0.0] * 64, "bm25_sparse": {"indices": [], "values": []}},
            }
        assert len(qdrant.points[col_name]) == valid_count_before + 2

        rec_result = await reconcile_orphan_session_units(
            session,
            q_client,
            project_id="proj-drift",
        )
        assert rec_result["scanned"] == valid_count_before + 2
        assert rec_result["orphans_deleted"] == 2
        assert rec_result["missing_repaired"] == 0
        assert len(qdrant.points[col_name]) == valid_count_before

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_http_model_a_to_http_model_b_drift_with_warm_cache_and_provenance_guard(
    monkeypatch,
) -> None:
    """PR #44 [P1]: Switching HTTP model A (64d) -> HTTP model B (32d) with warm caches calls B & verifies vectors."""
    from sag_api.services.session_context.persistence import (
        SessionPersistenceScope,
        sync_session_index_to_storage,
    )
    from sag_api.services.session_context.substrate import IncrementalSessionIndex

    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-http-ab"
    principal = _principal(project_id="proj-http-ab")
    col_name = session_qdrant_collection_name("proj-http-ab")
    now = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="HTTP Drift Agent")
        session.add(agent)
        await session.flush()
        thread = Thread(agent_id=agent.id, owner_key=owner_key, title="HTTP Model A -> B")
        session.add(thread)
        await session.flush()

        m0 = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Chốt quyết định kiến trúc: dùng cổng API Gateway Kong 3.8 cho cụm biên.",
            created_at=now,
        )
        m1 = Message(
            thread_id=thread.id,
            role=MessageRole.ASSISTANT,
            content="Đã lưu cấu hình rate-limit 2000 req/s trên Kong 3.8.",
            created_at=now + timedelta(seconds=15),
        )
        m_ask = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Cụm biên đang dùng API Gateway nào?",
            created_at=now + timedelta(seconds=30),
        )
        session.add_all([m0, m1, m_ask])
        await session.commit()

        # 1. Run HTTP Model A (`dim=64`) -> populates BOTH `_SHARED_TEXT_EMBEDDING_CACHE` and `SESSION_INDEX_CACHE`
        model_a = _CountingHttpModelEmbedder(model="provider/model-A", dim=64)
        pack_a = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask.id,
            query="Cụm biên đang dùng API Gateway nào?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=model_a,
            qdrant_client=q_client,
        )
        assert pack_a.audit_trace.persistence_status == "lazy_backfill"
        assert len(model_a.batches) >= 1
        assert qdrant.collections[col_name]["vectors"]["content_vector"]["size"] == 64

        # 2. Switch to HTTP Model B (`dim=32`) WITHOUT clearing `_SHARED_TEXT_EMBEDDING_CACHE` or `SESSION_INDEX_CACHE`!
        model_b = _CountingHttpModelEmbedder(model="provider/model-B", dim=32)
        pack_b = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask.id,
            query="Cụm biên đang dùng API Gateway nào?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=model_b,
            qdrant_client=q_client,
        )
        assert pack_b.audit_trace.persistence_status == "reconciled_rebuild"
        # Model B MUST be invoked for historical turns (never reusing Model A's 64-dim cache!)
        assert len(model_b.batches) >= 1
        flattened_b = [txt for batch in model_b.batches for txt in batch]
        assert any("Kong 3.8" in txt for txt in flattened_b)

        sigs_b = set(
            (
                await session.execute(
                    select(SessionUnit.embedding_model_signature).where(SessionUnit.thread_id == thread.id)
                )
            )
            .scalars()
            .all()
        )
        assert sigs_b == {"http:provider/model-B"}
        assert qdrant.collections[col_name]["vectors"]["content_vector"]["size"] == 32
        assert len(qdrant.points[col_name]) == 5  # 2 TURN + 2 WINDOW + 1 EPISODE
        for pt in qdrant.points[col_name].values():
            assert len(pt["vector"]["content_vector"]) == 32
            assert pt["payload"]["embedding_model_signature"] == "http:provider/model-B"

        # 3. Failing HTTP Model C (`503`) must NOT overwrite DB/Qdrant with fallback vectors
        class _FailingHttpEmbedder:
            model = "provider/model-C"
            dim = 48

            async def batch_generate(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("Upstream embedding provider 503")

        pack_c = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask.id,
            query="Cụm biên đang dùng API Gateway nào?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=_FailingHttpEmbedder(),
            qdrant_client=q_client,
        )
        assert pack_c.audit_trace.persistence_status == "degraded_unpersisted"
        sigs_after_c = set(
            (
                await session.execute(
                    select(SessionUnit.embedding_model_signature).where(SessionUnit.thread_id == thread.id)
                )
            )
            .scalars()
            .all()
        )
        assert sigs_after_c == {"http:provider/model-B"}
        assert qdrant.collections[col_name]["vectors"]["content_vector"]["size"] == 32

        # Direct provenance & dimension guard check on `sync_session_index_to_storage`
        bad_idx = IncrementalSessionIndex(thread.id)
        await bad_idx.append_messages(
            [{"id": m0.id, "role": "user", "content": m0.content, "created_at": now}],
            embedder=None,
        )
        with pytest.raises(ValueError, match="Refusing to persist"):
            await sync_session_index_to_storage(
                session,
                index=bad_idx,
                scope=SessionPersistenceScope(
                    tenant_id=principal.organization_id,
                    project_id="proj-http-ab",
                    thread_id=thread.id,
                    owner_key=owner_key,
                    security_partition_ids=("proj-http-ab",),
                ),
                embedding_sig="http:provider/model-B",
                mode="rebuild",
                embedder=model_b,
                qdrant_client=q_client,
            )

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_qdrant_503_outage_backfills_on_warm_retry_and_bidirectional_reconciler(
    monkeypatch,
) -> None:
    """PR #44 [P2 #1]: Qdrant 503 during backfill leaves durable pending state & heals on recovery."""
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)
    http_embedder = _CountingHttpModelEmbedder(model="BAAI/bge-m3", dim=64)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-qdrant-503"
    principal = _principal(project_id="proj-qdrant-503")
    col_name = session_qdrant_collection_name("proj-qdrant-503")
    now = datetime(2026, 10, 28, 7, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Qdrant Recovery Agent")
        session.add(agent)
        await session.flush()
        thread_1 = Thread(agent_id=agent.id, owner_key=owner_key, title="Qdrant Outage Thread 1")
        thread_2 = Thread(agent_id=agent.id, owner_key=owner_key, title="Qdrant Outage Thread 2")
        session.add_all([thread_1, thread_2])
        await session.flush()

        for idx, body_txt in enumerate(
            [
                "Chốt dùng Kafka KRaft 3.8 cho luồng đối soát giao dịch.",
                "Đã cấu hình replication factor = 3 cho topic đối soát.",
                "Luồng đối soát đang dùng broker nào?",
            ]
        ):
            session.add(
                Message(
                    thread_id=thread_1.id,
                    role=MessageRole.USER if idx % 2 == 0 else MessageRole.ASSISTANT,
                    content=body_txt,
                    created_at=now + timedelta(seconds=idx * 20),
                )
            )
        await session.commit()
        t1_msgs = list(
            (
                await session.execute(
                    select(Message).where(Message.thread_id == thread_1.id).order_by(Message.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        t1_ask = t1_msgs[-1]

        # Part A: Qdrant returns 503 during initial backfill -> PG saves with `qdrant_point_id IS NULL`
        qdrant.fail_status_code = 503
        pack_503 = await prepare_session_context_for_ask(
            session,
            thread_id=thread_1.id,
            exclude_message_id=t1_ask.id,
            query="Luồng đối soát đang dùng broker nào?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert pack_503.audit_trace.persistence_status == "lazy_backfill"
        assert pack_503.audit_trace.qdrant_points_upserted == 0
        assert col_name not in qdrant.points
        unsynced_cnt = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.thread_id == thread_1.id, SessionUnit.qdrant_point_id.is_(None))
        )
        assert unsynced_cnt == 5  # 2 TURN + 2 WINDOW + 1 EPISODE

        # Qdrant recovers (`200 OK`): next warm ask (with 0 new messages!) automatically backfills Qdrant!
        qdrant.fail_status_code = None
        pack_healed = await prepare_session_context_for_ask(
            session,
            thread_id=thread_1.id,
            exclude_message_id=t1_ask.id,
            query="Luồng đối soát đang dùng broker nào?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert pack_healed.audit_trace.persistence_status == "qdrant_backfilled"
        assert pack_healed.audit_trace.qdrant_points_upserted == 5
        assert len(qdrant.points[col_name]) == 5
        unsynced_after = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.thread_id == thread_1.id, SessionUnit.qdrant_point_id.is_(None))
        )
        assert unsynced_after == 0

        # Cold-start after recovery must now achieve 0 historical HTTP re-embeddings!
        SESSION_INDEX_CACHE.clear()
        _SHARED_TEXT_EMBEDDING_CACHE.clear()
        http_embedder.batches.clear()
        pack_cold = await prepare_session_context_for_ask(
            session,
            thread_id=thread_1.id,
            exclude_message_id=t1_ask.id,
            query="Luồng đối soát đang dùng broker nào?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert pack_cold.audit_trace.persistence_status == "hydrated_clean"
        flattened_t1_cold = [txt for batch in http_embedder.batches for txt in batch]
        assert not any("Kafka KRaft" in txt for txt in flattened_t1_cold)
        assert len(http_embedder.batches) <= 1

        # Part B: Thread 2 hits Qdrant 503, then `reconcile_orphan_session_units` heals missing points!
        t2_m0 = Message(
            thread_id=thread_2.id,
            role=MessageRole.USER,
            content="Chốt dùng ClickHouse 24.8 cho kho phân tích OLAP.",
            created_at=now + timedelta(minutes=5),
        )
        t2_ask = Message(
            thread_id=thread_2.id,
            role=MessageRole.USER,
            content="Kho phân tích OLAP dùng gì?",
            created_at=now + timedelta(minutes=6),
        )
        session.add_all([t2_m0, t2_ask])
        await session.commit()

        qdrant.fail_status_code = 503
        _ = await prepare_session_context_for_ask(
            session,
            thread_id=thread_2.id,
            exclude_message_id=t2_ask.id,
            query="Kho phân tích OLAP dùng gì?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert len(qdrant.points[col_name]) == 5  # Thread 2's 3 units are missing from Qdrant!

        # Simulate cold state + Qdrant recovery, then run bidirectional `reconcile_orphan_session_units`
        SESSION_INDEX_CACHE.clear()
        _SHARED_TEXT_EMBEDDING_CACHE.clear()
        qdrant.fail_status_code = None
        rec_stats = await reconcile_orphan_session_units(
            session,
            q_client,
            project_id="proj-qdrant-503",
            embedder=http_embedder,
        )
        assert rec_stats["orphans_deleted"] == 0
        assert rec_stats["missing_repaired"] == 3  # 1 TURN + 1 WINDOW + 1 EPISODE for thread_2
        assert len(qdrant.points[col_name]) == 8

        # Cold restart on Thread 2 now hydrates from Qdrant with 0 historical HTTP calls!
        SESSION_INDEX_CACHE.clear()
        _SHARED_TEXT_EMBEDDING_CACHE.clear()
        http_embedder.batches.clear()
        pack_t2_cold = await prepare_session_context_for_ask(
            session,
            thread_id=thread_2.id,
            exclude_message_id=t2_ask.id,
            query="Kho phân tích OLAP dùng gì?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert pack_t2_cold.audit_trace.persistence_status == "hydrated_clean"
        flattened_t2_cold = [txt for batch in http_embedder.batches for txt in batch]
        assert not any("ClickHouse 24.8" in txt for txt in flattened_t2_cold)
        assert len(http_embedder.batches) <= 1

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_warm_delta_db_failure_invalidates_cache_and_recovers_missing_turn(
    monkeypatch,
) -> None:
    """PR #44 [P2 #2]: Transient DB write failure on warm delta must NOT leave phantom cursor in RAM."""
    import sag_api.services.session_context.persistence as persistence_mod

    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-fault-ch3"
    principal = _principal(project_id="proj-fault")
    now = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Fault Injection Agent")
        session.add(agent)
        await session.flush()
        thread = Thread(agent_id=agent.id, owner_key=owner_key, title="Fault Injection Thread")
        session.add(thread)
        await session.flush()

        m0 = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Lượt 1: Chốt dùng Valkey 8 cho hàng đợi tác vụ nhanh.",
            created_at=now,
        )
        m_ask_1 = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Câu hỏi lượt 1?",
            created_at=now + timedelta(seconds=10),
        )
        session.add_all([m0, m_ask_1])
        await session.commit()

        # 1. Persist Turn 1 cleanly -> warm cache has 1 turn, DB has 1 TURN unit
        pack_1 = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask_1.id,
            query="Hàng đợi tác vụ nhanh dùng gì?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_1.audit_trace.persistence_status == "lazy_backfill"
        turn_cnt_1 = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.thread_id == thread.id, SessionUnit.unit_kind == "TURN")
        )
        assert turn_cnt_1 == 1

        # 2. Add Turn 2 (`m_ask_1` becomes history when `m_ask_2` is current ask) & inject 1-shot DB failure!
        m_ask_2 = Message(
            thread_id=thread.id,
            role=MessageRole.USER,
            content="Câu hỏi lượt 2?",
            created_at=now + timedelta(seconds=20),
        )
        session.add(m_ask_2)
        await session.commit()

        orig_sync = persistence_mod.sync_session_index_to_storage
        fail_once = {"armed": True}

        async def _flaky_sync(*args: Any, **kwargs: Any):
            if fail_once["armed"]:
                fail_once["armed"] = False
                raise RuntimeError("Simulated transient PostgreSQL write error")
            return await orig_sync(*args, **kwargs)

        monkeypatch.setattr(persistence_mod, "sync_session_index_to_storage", _flaky_sync)

        with pytest.raises(RuntimeError, match="Simulated transient PostgreSQL write error"):
            await prepare_session_context_for_ask(
                session,
                thread_id=thread.id,
                exclude_message_id=m_ask_2.id,
                query="Kiểm tra sau lỗi đồng bộ?",
                principal=principal,
                owner_key=owner_key,
                requested_source_ids=None,
                budget_tokens=900,
                qdrant_client=q_client,
            )

        # DB still only has 1 TURN unit (`m0`), and warm cache MUST have been invalidated!
        turn_cnt_during_fault = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.thread_id == thread.id, SessionUnit.unit_kind == "TURN")
        )
        assert turn_cnt_during_fault == 1
        assert SESSION_INDEX_CACHE.find_thread_entries(thread.id) == []

        # 3. Next call (after fault clears, with 0 new messages) MUST backfill missing Turn 2 (`hydrated_delta`)!
        pack_recovered = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask_2.id,
            query="Kiểm tra sau lỗi đồng bộ?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_recovered.audit_trace.persistence_status == "hydrated_delta"
        turn_cnt_recovered = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.thread_id == thread.id, SessionUnit.unit_kind == "TURN")
        )
        assert turn_cnt_recovered == 2

        # 4. Subsequent call with no new messages now cleanly reports `warm_clean` with RAM == DB == 2!
        pack_warm = await prepare_session_context_for_ask(
            session,
            thread_id=thread.id,
            exclude_message_id=m_ask_2.id,
            query="Kiểm tra sau lỗi đồng bộ?",
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_warm.audit_trace.persistence_status == "warm_clean"
        assert pack_warm.audit_trace.total_indexed_turns == 2

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_multi_project_principal_resolves_unique_source_mapping_and_skips_ambiguous_threads(
    monkeypatch,
) -> None:
    """PR #44 Review 2 [P1]: Multi-project principal never guesses `allowed_projects[0]`;
    resolves unique project via `SourceProjectMapping` (`CONFIRMED`) or stays `memory_only`.
    """
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-multi-proj"
    multi_principal = VerifiedPrincipal(
        subject="user-multi",
        organization_id="org-multi",
        allowed_project_ids=frozenset({"proj-alpha", "proj-beta"}),
        issuer="continuum-jwks",
        key_id="kid-multi",
        token_id="jti-multi",
        issued_at=1_760_000_000,
        expires_at=1_760_000_900,
        tenant_id="org-multi",
        allowed_partition_ids=frozenset({"proj-alpha", "proj-beta"}),
    )
    col_alpha = session_qdrant_collection_name("proj-alpha")
    col_beta = session_qdrant_collection_name("proj-beta")
    now = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Multi-Project Agent")
        src_alpha = Source(name="Alpha Spec", sag_source_config_id="sag-alpha-1")
        src_beta = Source(name="Beta Spec", sag_source_config_id="sag-beta-1")
        session.add_all([agent, src_alpha, src_beta])
        await session.flush()

        session.add_all(
            [
                SourceProjectMapping(
                    source_id=src_alpha.id,
                    organization_id="org-multi",
                    project_id="proj-alpha",
                    state="CONFIRMED",
                    mapping_version=1,
                    confirmed_at=now,
                    confirmed_by="admin",
                    approval_ref="apr-alpha",
                ),
                SourceProjectMapping(
                    source_id=src_beta.id,
                    organization_id="org-multi",
                    project_id="proj-beta",
                    state="CONFIRMED",
                    mapping_version=1,
                    confirmed_at=now,
                    confirmed_by="admin",
                    approval_ref="apr-beta",
                ),
            ]
        )
        thread_main = Thread(agent_id=agent.id, owner_key=owner_key, title="Multi Proj Main")
        thread_req = Thread(agent_id=agent.id, owner_key=owner_key, title="Multi Proj Req")
        session.add_all([thread_main, thread_req])
        await session.flush()

        # 1. Ambiguous thread (no cited sources, no `requested_source_ids`) -> MUST stay `memory_only`!
        u0 = Message(
            thread_id=thread_main.id,
            role=MessageRole.USER,
            content="Xin chào, hãy khởi tạo phiên làm việc.",
            created_at=now,
        )
        u_ask_1 = Message(
            thread_id=thread_main.id,
            role=MessageRole.USER,
            content="Tóm tắt chính sách của dự án Beta?",
            created_at=now + timedelta(seconds=10),
        )
        session.add_all([u0, u_ask_1])
        await session.commit()

        pack_ambig = await prepare_session_context_for_ask(
            session,
            thread_id=thread_main.id,
            exclude_message_id=u_ask_1.id,
            query=u_ask_1.content,
            principal=multi_principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_ambig.audit_trace.persistence_status == "memory_only"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SessionUnit)
                .where(SessionUnit.thread_id == thread_main.id)
            )
            == 0
        )
        assert col_alpha not in qdrant.points
        assert col_beta not in qdrant.points

        # 2. Assistant replies citing `src_beta` (`proj-beta`, NOT `allowed_projects[0]` `proj-alpha`!):
        #    next warm turn resolves uniquely to `proj-beta` and lazy-backfills ALL turns into `proj-beta`!
        a_beta = Message(
            thread_id=thread_main.id,
            role=MessageRole.ASSISTANT,
            content="Dự án Beta chốt chu kỳ kiểm toán nội bộ là 30 ngày.",
            citations=[{"n": 1, "source_id": src_beta.id, "chunk_id": "chk-beta-1"}],
            created_at=now + timedelta(seconds=20),
        )
        u_ask_2 = Message(
            thread_id=thread_main.id,
            role=MessageRole.USER,
            content="Chu kỳ kiểm toán của Beta là bao nhiêu ngày?",
            created_at=now + timedelta(seconds=30),
        )
        session.add_all([a_beta, u_ask_2])
        await session.commit()

        pack_beta = await prepare_session_context_for_ask(
            session,
            thread_id=thread_main.id,
            exclude_message_id=u_ask_2.id,
            query=u_ask_2.content,
            principal=multi_principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_beta.audit_trace.persistence_status == "lazy_backfill"
        persisted_rows = list(
            (
                await session.execute(
                    select(SessionUnit).where(SessionUnit.thread_id == thread_main.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(persisted_rows) > 0
        assert {r.project_id for r in persisted_rows} == {"proj-beta"}
        assert {tuple(r.security_partition_ids_json) for r in persisted_rows} == {("proj-beta",)}
        assert col_alpha not in qdrant.points
        assert len(qdrant.points[col_beta]) == len(persisted_rows)

        # 3. Adding a citation from `src_alpha` (`proj-alpha`) makes the thread cross-project ->
        #    purges `proj-beta` derived rows & Qdrant points and transitions cleanly to `memory_only`!
        a_alpha = Message(
            thread_id=thread_main.id,
            role=MessageRole.ASSISTANT,
            content="Dự án Alpha chốt chu kỳ đối soát là 7 ngày.",
            citations=[{"n": 1, "source_id": src_alpha.id, "chunk_id": "chk-alpha-1"}],
            created_at=now + timedelta(seconds=40),
        )
        u_ask_3 = Message(
            thread_id=thread_main.id,
            role=MessageRole.USER,
            content="So sánh chu kỳ của Beta và Alpha?",
            created_at=now + timedelta(seconds=50),
        )
        session.add_all([a_alpha, u_ask_3])
        await session.commit()

        pack_cross = await prepare_session_context_for_ask(
            session,
            thread_id=thread_main.id,
            exclude_message_id=u_ask_3.id,
            query=u_ask_3.content,
            principal=multi_principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_cross.audit_trace.persistence_status == "memory_only"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SessionUnit)
                .where(SessionUnit.thread_id == thread_main.id)
            )
            == 0
        )
        assert len(qdrant.points.get(col_beta, {})) == 0
        assert col_alpha not in qdrant.points

        # 4. Explicit `requested_source_ids`: multi-project `[src_alpha, src_beta]` stays `memory_only`,
        #    while single-project `[src_beta]` resolves uniquely to `proj-beta`!
        r0 = Message(
            thread_id=thread_req.id,
            role=MessageRole.USER,
            content="Khởi tạo yêu cầu rà soát tài liệu.",
            created_at=now,
        )
        r_ask = Message(
            thread_id=thread_req.id,
            role=MessageRole.USER,
            content="Tra cứu quy định?",
            created_at=now + timedelta(seconds=10),
        )
        session.add_all([r0, r_ask])
        await session.commit()

        pack_req_both = await prepare_session_context_for_ask(
            session,
            thread_id=thread_req.id,
            exclude_message_id=r_ask.id,
            query=r_ask.content,
            principal=multi_principal,
            owner_key=owner_key,
            requested_source_ids=[src_alpha.id, src_beta.id],
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_req_both.audit_trace.persistence_status == "memory_only"

        pack_req_beta = await prepare_session_context_for_ask(
            session,
            thread_id=thread_req.id,
            exclude_message_id=r_ask.id,
            query=r_ask.content,
            principal=multi_principal,
            owner_key=owner_key,
            requested_source_ids=[src_beta.id],
            budget_tokens=900,
            qdrant_client=q_client,
        )
        assert pack_req_beta.audit_trace.persistence_status == "lazy_backfill"
        req_proj_set = set(
            (
                await session.execute(
                    select(SessionUnit.project_id).where(SessionUnit.thread_id == thread_req.id)
                )
            )
            .scalars()
            .all()
        )
        assert req_proj_set == {"proj-beta"}

    await q_client.aclose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reconcile_orphan_session_units_enforces_acl_and_purges_revoked_units_without_repair(
    monkeypatch,
) -> None:
    """PR #44 Review 2 [P2]: `reconcile_orphan_session_units` checks active `SourceProjectMapping`
    (`state='CONFIRMED'`) BEFORE repairing missing Qdrant points and purges revoked units instead.
    """
    monkeypatch.setattr(settings, "session_context_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    qdrant = _InMemoryQdrantEmulator()
    q_client = qdrant.make_client()
    set_custom_session_qdrant_client_factory(lambda: q_client)
    http_embedder = _CountingHttpModelEmbedder(model="BAAI/bge-m3", dim=64)

    engine = await _make_fk_sqlite_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_key = "owner-acl-reconcile"
    principal = _principal(org_id="org-rec", project_id="proj-rec")
    col_name = session_qdrant_collection_name("proj-rec")
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)

    async with session_factory() as session:
        agent = Agent(owner_key=owner_key, name="Reconcile ACL Agent")
        src_rev_missing = Source(name="Revoked Missing Qdrant", sag_source_config_id="sag-rm-1")
        src_rev_synced = Source(name="Revoked Synced Qdrant", sag_source_config_id="sag-rs-1")
        src_valid = Source(name="Valid Confirmed Source", sag_source_config_id="sag-ok-1")
        session.add_all([agent, src_rev_missing, src_rev_synced, src_valid])
        await session.flush()

        map_rev_missing = SourceProjectMapping(
            source_id=src_rev_missing.id,
            organization_id="org-rec",
            project_id="proj-rec",
            state="CONFIRMED",
            mapping_version=1,
            confirmed_at=now,
            confirmed_by="admin",
            approval_ref="apr-rm",
        )
        map_rev_synced = SourceProjectMapping(
            source_id=src_rev_synced.id,
            organization_id="org-rec",
            project_id="proj-rec",
            state="CONFIRMED",
            mapping_version=1,
            confirmed_at=now,
            confirmed_by="admin",
            approval_ref="apr-rs",
        )
        map_valid = SourceProjectMapping(
            source_id=src_valid.id,
            organization_id="org-rec",
            project_id="proj-rec",
            state="CONFIRMED",
            mapping_version=1,
            confirmed_at=now,
            confirmed_by="admin",
            approval_ref="apr-ok",
        )
        t_rev_missing = Thread(agent_id=agent.id, owner_key=owner_key, title="Revoked Missing")
        t_rev_synced = Thread(agent_id=agent.id, owner_key=owner_key, title="Revoked Synced")
        t_valid = Thread(agent_id=agent.id, owner_key=owner_key, title="Valid Missing")
        session.add_all(
            [map_rev_missing, map_rev_synced, map_valid, t_rev_missing, t_rev_synced, t_valid]
        )
        await session.flush()

        # Thread 1: indexed during Qdrant 503 (`qdrant_point_id IS NULL`), cites `src_rev_missing`
        m1_u = Message(
            thread_id=t_rev_missing.id,
            role=MessageRole.USER,
            content="Cho biết mật khẩu kho khóa ORION-SECRET?",
            created_at=now,
        )
        m1_a = Message(
            thread_id=t_rev_missing.id,
            role=MessageRole.ASSISTANT,
            content="Kho khóa ORION-SECRET dùng mã nội bộ VLT-9988.",
            citations=[{"n": 1, "source_id": src_rev_missing.id, "chunk_id": "c-rm"}],
            created_at=now + timedelta(seconds=10),
        )
        m1_ask = Message(
            thread_id=t_rev_missing.id,
            role=MessageRole.USER,
            content="Kiểm tra kho khóa?",
            created_at=now + timedelta(seconds=20),
        )

        # Thread 2: indexed while Qdrant is healthy (points in Qdrant), cites `src_rev_synced`
        m2_u = Message(
            thread_id=t_rev_synced.id,
            role=MessageRole.USER,
            content="Cho biết hạn mức mật HELIOS-SECRET?",
            created_at=now,
        )
        m2_a = Message(
            thread_id=t_rev_synced.id,
            role=MessageRole.ASSISTANT,
            content="Hạn mức HELIOS-SECRET là 77 triệu USD.",
            citations=[{"n": 1, "source_id": src_rev_synced.id, "chunk_id": "c-rs"}],
            created_at=now + timedelta(seconds=10),
        )
        m2_ask = Message(
            thread_id=t_rev_synced.id,
            role=MessageRole.USER,
            content="Kiểm tra hạn mức?",
            created_at=now + timedelta(seconds=20),
        )

        # Thread 3: indexed during Qdrant 503 (`qdrant_point_id IS NULL`), cites `src_valid` (`CONFIRMED`)
        m3_u = Message(
            thread_id=t_valid.id,
            role=MessageRole.USER,
            content="Cho biết quy chuẩn công khai ATLAS-PUBLIC?",
            created_at=now,
        )
        m3_a = Message(
            thread_id=t_valid.id,
            role=MessageRole.ASSISTANT,
            content="Quy chuẩn ATLAS-PUBLIC dùng chuẩn ISO-27001.",
            citations=[{"n": 1, "source_id": src_valid.id, "chunk_id": "c-ok"}],
            created_at=now + timedelta(seconds=10),
        )
        m3_ask = Message(
            thread_id=t_valid.id,
            role=MessageRole.USER,
            content="Kiểm tra quy chuẩn?",
            created_at=now + timedelta(seconds=20),
        )
        session.add_all([m1_u, m1_a, m1_ask, m2_u, m2_a, m2_ask, m3_u, m3_a, m3_ask])
        await session.commit()

        # Sync Thread 2 while Qdrant is healthy (`5` points in Qdrant)
        _ = await prepare_session_context_for_ask(
            session,
            thread_id=t_rev_synced.id,
            exclude_message_id=m2_ask.id,
            query=m2_ask.content,
            principal=principal,
            owner_key=owner_key,
            requested_source_ids=None,
            budget_tokens=900,
            embedder=http_embedder,
            qdrant_client=q_client,
        )
        assert len(qdrant.points[col_name]) == 5

        # Sync Thread 1 & Thread 3 during Qdrant 503 outage (`qdrant_point_id IS NULL` in DB, 0 points added)
        qdrant.fail_status_code = 503
        for t_obj, ask_msg in ((t_rev_missing, m1_ask), (t_valid, m3_ask)):
            _ = await prepare_session_context_for_ask(
                session,
                thread_id=t_obj.id,
                exclude_message_id=ask_msg.id,
                query=ask_msg.content,
                principal=principal,
                owner_key=owner_key,
                requested_source_ids=None,
                budget_tokens=900,
                embedder=http_embedder,
                qdrant_client=q_client,
            )
        assert len(qdrant.points[col_name]) == 5

        # Revoke BOTH `src_rev_missing` and `src_rev_synced` BEFORE any next user ask!
        # Keep `SESSION_INDEX_CACHE` warm for `t_rev_missing` (tests warm-cache bypass guard)
        # and clear `_SHARED_TEXT_EMBEDDING_CACHE` + `http_embedder.batches` to verify 0 revoked embeds!
        for rev_map in (map_rev_missing, map_rev_synced):
            rev_map.state = "REVOKED"
            rev_map.revoked_at = datetime.now(UTC)
            rev_map.revoked_by = "sec-admin"
            rev_map.revocation_ref = "rev-audit-44"
        await session.commit()

        SESSION_INDEX_CACHE.invalidate_thread(t_valid.id)
        _SHARED_TEXT_EMBEDDING_CACHE.clear()
        http_embedder.batches.clear()
        qdrant.fail_status_code = None

        rec_stats = await reconcile_orphan_session_units(
            session,
            q_client,
            project_id="proj-rec",
            embedder=http_embedder,
        )
        # Thread 2's 5 revoked points were deleted as orphans; Thread 3's 5 valid units were repaired;
        # Thread 1's 5 revoked units were PURGED (NEVER repaired or re-embedded)!
        assert rec_stats["orphans_deleted"] == 5
        assert rec_stats["missing_repaired"] == 5

        flattened_embedded = [txt for batch in http_embedder.batches for txt in batch]
        assert not any("ORION-SECRET" in txt or "VLT-9988" in txt for txt in flattened_embedded)
        assert not any("HELIOS-SECRET" in txt or "77 triệu USD" in txt for txt in flattened_embedded)
        assert any("ATLAS-PUBLIC" in txt for txt in flattened_embedded)

        for revoked_tid in (t_rev_missing.id, t_rev_synced.id):
            leftover = await session.scalar(
                select(func.count())
                .select_from(SessionUnit)
                .where(SessionUnit.thread_id == revoked_tid)
            )
            assert leftover == 0
            assert SESSION_INDEX_CACHE.find_thread_entries(revoked_tid) == []

        valid_cnt = await session.scalar(
            select(func.count())
            .select_from(SessionUnit)
            .where(SessionUnit.thread_id == t_valid.id, SessionUnit.qdrant_point_id.is_not(None))
        )
        assert valid_cnt == 5
        assert len(qdrant.points[col_name]) == 5
        assert {pt["payload"]["thread_id"] for pt in qdrant.points[col_name].values()} == {t_valid.id}

    await q_client.aclose()
    await engine.dispose()
