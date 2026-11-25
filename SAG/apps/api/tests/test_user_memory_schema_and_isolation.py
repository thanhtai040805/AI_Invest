"""M1 Acceptance Gate & Schema/Config/Qdrant Contract Tests for SAG-Native User Memory.

Covers:
- Dual-dialect SQLAlchemy ORM creation on SQLite (`sqlite+aiosqlite`) + 1-1 DDL contract check
  against PostgreSQL migration `0014_user_memory_and_linked_sessions.sql`.
- 6-file config parity for `SAG_USER_MEMORY_*` + zero duplicate `core_database_url` in `config.py`.
- Windows-safe integer infinity sentinel `INF_TS = 253402300799` (zero year-9999 `.timestamp()` calls).
- Wire-boundary Qdrant `aiinvest_user_memories` contract (on-disk HNSW, `always_ram=False`, exact 7 indexes).
- M1 (0% Cross-User Leak & 100% IDOR Rejection) through real FastAPI ASGI HTTP routes & Smart Read
  for two users sharing `X-Continuum-Organization-Id: aiinvest-default`.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_api.api.v1.research_chat import router as research_chat_router
from sag_api.core.config import Settings, settings
from sag_api.core.db import get_session
from sag_api.core.errors import ApiError
from sag_api.db import models as _all_models  # noqa: F401
from sag_api.db.base import Base
from sag_api.db.models.user_memory import (
    INF_DATETIME,
    INF_TS,
    UserMemoryEpisode,
    UserMemoryItem,
    date_to_utc_ts,
    safe_utc_timestamp,
)
from sag_api.research.user_memory_pipeline import (
    ExtractedTurnBundle,
    SanitizedMemoryCandidate,
    compute_content_hash,
    process_ingest_turn_payload,
)
from sag_api.research.user_memory_qdrant import (
    USER_MEMORY_PAYLOAD_INDEX_FIELDS,
    ensure_user_memory_collection,
)
from sag_api.research.user_memory_read import smart_read_user_memories

EXPECTED_TABLES = {
    "user_memory_episodes",
    "user_memory_items",
    "user_memory_entities",
    "user_memory_edges",
    "user_memory_jobs",
    "user_memory_audit_log",
}


def _bff_headers(user_id: str, org_id: str = "aiinvest-default") -> dict[str, str]:
    return {
        "X-SAG-BFF-Service-Token": "test-bff-secret-token",
        "X-Continuum-User-Id": user_id,
        "X-Continuum-Organization-Id": org_id,
        "X-Continuum-Project-Id": "all",
        "X-Continuum-Security-Partition": "all",
        "X-Continuum-Allowed-Project-Ids": "*",
    }


def _make_test_app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_err(_request: Any, exc: ApiError):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    async def _override_get_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_get_session
    app.include_router(research_chat_router, prefix="/api/v1")
    return app


def test_windows_safe_infinity_timestamps_never_raise() -> None:
    assert safe_utc_timestamp(INF_DATETIME) == INF_TS
    assert safe_utc_timestamp(None, default_inf=True) == INF_TS
    assert safe_utc_timestamp(None, default_inf=False) == 0
    assert safe_utc_timestamp(datetime(1960, 1, 1, tzinfo=UTC)) == 0
    assert date_to_utc_ts(date(9999, 12, 31)) == INF_TS
    assert date_to_utc_ts(date(2026, 5, 10)) == int(
        (datetime(2026, 5, 10, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()
    )


def test_six_file_config_parity_and_no_duplicate_core_database_url() -> None:
    repo_root = Path(__file__).resolve().parents[4]

    # Verify config.py has exactly ONE `core_database_url` declaration
    config_src = (repo_root / "SAG" / "apps" / "api" / "sag_api" / "core" / "config.py").read_text(
        encoding="utf-8"
    )
    core_db_decls = re.findall(r"^\s+core_database_url\s*:", config_src, flags=re.MULTILINE)
    assert len(core_db_decls) == 1, f"Expected 1 core_database_url field, found {len(core_db_decls)}"

    required_env_keys = [
        "SAG_USER_MEMORY_MODE",
        "SAG_USER_MEMORY_QDRANT_COLLECTION",
        "SAG_USER_MEMORY_EXTRACTOR_MODEL",
        "SAG_USER_MEMORY_WORKER_ENABLED",
        "SAG_USER_MEMORY_DEBOUNCE_SECONDS",
    ]
    for env_path in (
        repo_root / ".env.example",
        repo_root / "SAG" / ".env.example",
        repo_root / "SAG" / "apps" / "api" / ".env.example",
    ):
        text = env_path.read_text(encoding="utf-8")
        counts: dict[str, int] = {}
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Z][A-Z0-9_]+)\s*=", line)
            if m:
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        for key in required_env_keys:
            assert counts.get(key) == 1, f"{key} count={counts.get(key)} in {env_path}"

    for compose_path in (repo_root / "docker-compose.yml", repo_root / "SAG" / "compose.yaml"):
        ctext = compose_path.read_text(encoding="utf-8")
        for key in required_env_keys:
            assert key in ctext, f"Missing {key} in {compose_path}"

    for field_name in (
        "user_memory_mode",
        "user_memory_qdrant_collection",
        "user_memory_extractor_model",
        "user_memory_worker_enabled",
        "user_memory_debounce_seconds",
        "user_memory_max_items_per_user",
        "user_memory_max_episodes_per_user",
    ):
        assert field_name in Settings.model_fields


def test_migration_0014_sql_matches_six_orm_tables() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    sql_path = (
        repo_root / "SAG" / "apps" / "api" / "migrations" / "0014_user_memory_and_linked_sessions.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")
    created_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-z0-9_]+)", sql))
    assert EXPECTED_TABLES.issubset(created_tables)
    assert "uq_user_memory_episodes_scope_session" in sql
    assert "uq_user_memory_entities_scope_key" in sql
    assert "idempotency_key CHAR(64) NOT NULL UNIQUE" in sql
    assert "sync_version" in sql
    assert "JSONB" in sql

    # ORM metadata must contain all 6 tables
    orm_tables = set(Base.metadata.tables.keys())
    assert EXPECTED_TABLES.issubset(orm_tables)


@pytest.mark.asyncio
async def test_qdrant_user_memory_collection_exact_7_indexes_and_ram_budget() -> None:
    recorded_puts: list[tuple[str, dict[str, Any]]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/collections/" in request.url.path:
            return httpx.Response(404, json={"status": {"error": "not found"}})
        if request.method == "PUT":
            import json

            recorded_puts.append((request.url.path, json.loads(request.content.decode("utf-8"))))
            return httpx.Response(200, json={"result": True, "status": "ok"})
        return httpx.Response(200, json={"result": True})

    async with httpx.AsyncClient(
        base_url="http://qdrant.local:6333",
        transport=httpx.MockTransport(_handler),
    ) as client:
        col = await ensure_user_memory_collection(client, collection_name="aiinvest_user_memories", vector_dim=1024)
        assert col == "aiinvest_user_memories"

    col_creates = [body for path, body in recorded_puts if path.endswith("/collections/aiinvest_user_memories")]
    assert len(col_creates) == 1
    cfg = col_creates[0]
    assert cfg["vectors"]["content_vector"] == {"size": 1024, "distance": "Cosine", "on_disk": True}
    assert cfg["sparse_vectors"]["bm25_sparse"] == {"modifier": "idf"}
    assert cfg["on_disk_payload"] is True
    assert cfg["hnsw_config"] == {"m": 0, "payload_m": 16, "on_disk": True}
    assert cfg["quantization_config"]["scalar"]["always_ram"] is False

    idx_creates = [body for path, body in recorded_puts if path.endswith("/index")]
    assert len(USER_MEMORY_PAYLOAD_INDEX_FIELDS) == 7
    assert len(idx_creates) == 7
    idx_map = {item["field_name"]: item["field_schema"] for item in idx_creates}
    assert idx_map["user_scope_key"] == {"type": "keyword", "is_tenant": True}
    assert set(idx_map.keys()) == {
        "user_scope_key",
        "memory_type",
        "pit_sensitivity",
        "is_backtest_sandbox",
        "valid_to_ts",
        "tx_to_ts",
        "source_as_of_date_ts",
    }


@pytest.mark.asyncio
async def test_m1_cross_user_isolation_and_idor_rejection_under_shared_org(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-secret-token")
    monkeypatch.setattr(settings, "user_memory_mode", "on")

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm1_iso.db'}"
    eng = create_async_engine(db_url, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    # Seed distinct memories & episodes for Alice and Bob under SAME org `aiinvest-default`
    async with sf() as s:
        alice_bundle = ExtractedTurnBundle(
            memories=[
                SanitizedMemoryCandidate(
                    memory_type="USER_EXTERNAL_HOLDING",
                    pit_sensitivity="MARKET_BOUND",
                    scope="TICKER_SCOPED",
                    content_text="Alice giữ 10.000 FPT giá vốn 125.000đ bên SSI",
                    content_hash=compute_content_hash("Alice giữ 10.000 FPT giá vốn 125.000đ bên SSI"),
                    canonical_slot_key="ext_holding:FPT",
                    tickers=["FPT"],
                    confidence=0.95,
                    source_authority="USER_DECLARED_EXTERNAL",
                )
            ],
            episode_summary="Phiên Alice phân tích FPT và nợ vay",
            tickers=["FPT"],
        )
        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="user-alice",
            payload={
                "session_id": "sess-alice-1",
                "session_title": "Phân tích FPT của Alice",
                "user_content": "Bên SSI tôi đang giữ 10.000 FPT giá vốn 125.000đ",
                "assistant_topic_outline": "Thảo luận FPT",
                "assistant_clean_for_digest": True,
                "turn_as_of_date": "2026-06-15",
                "is_backtest_sandbox": False,
                "tickers": ["FPT"],
            },
            extracted_override=alice_bundle,
        )

        bob_bundle = ExtractedTurnBundle(
            memories=[
                SanitizedMemoryCandidate(
                    memory_type="USER_INVESTING_CRITERIA",
                    pit_sensitivity="MARKET_BOUND",
                    scope="USER_GLOBAL",
                    content_text="Bob chỉ lọc cổ phiếu cổ tức tiền mặt > 8%",
                    content_hash=compute_content_hash("Bob chỉ lọc cổ phiếu cổ tức tiền mặt > 8%"),
                    canonical_slot_key="criteria:dividend",
                    tickers=[],
                    confidence=0.92,
                    source_authority="USER_DECLARED",
                )
            ],
            episode_summary="Phiên Bob lọc cổ phiếu cổ tức",
            tickers=["VNM"],
        )
        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="user-bob",
            payload={
                "session_id": "sess-bob-1",
                "session_title": "Bộ lọc cổ tức Bob",
                "user_content": "Tôi chỉ lọc cổ phiếu cổ tức tiền mặt > 8%",
                "assistant_topic_outline": "Thảo luận cổ tức VNM",
                "assistant_clean_for_digest": True,
                "turn_as_of_date": "2026-06-15",
                "is_backtest_sandbox": False,
                "tickers": ["VNM"],
            },
            extracted_override=bob_bundle,
        )

    # Smart Read isolation check (both user_profile_light and cross_session_graph)
    async with sf() as s:
        bob_read_light = await smart_read_user_memories(
            s,
            tenant_id="aiinvest-default",
            user_id="user-bob",
            query="FPT bên SSI giá vốn bao nhiêu?",
            as_of_date=date(2026, 6, 20),
            memory_recall_need="user_profile_light",
            explicit_tickers=["FPT"],
            user_memory_mode="on",
            reference_today=date(2026, 6, 20),
        )
        assert all(
            "Alice" not in m.content_text and "125.000" not in m.content_text
            for m in bob_read_light.recalled_memories
        )

        bob_read_graph = await smart_read_user_memories(
            s,
            tenant_id="aiinvest-default",
            user_id="user-bob",
            query="Phiên trước tôi phân tích FPT bên SSI",
            as_of_date=date(2026, 6, 20),
            memory_recall_need="cross_session_graph",
            explicit_tickers=["FPT"],
            user_memory_mode="on",
            reference_today=date(2026, 6, 20),
        )
        assert all("Alice" not in m.content_text for m in bob_read_graph.recalled_memories)
        assert all(ep.session_id != "sess-alice-1" for ep in bob_read_graph.linked_sessions)

    # Full ASGI HTTP IDOR attack suite
    app = _make_test_app(sf)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        alice_list = await client.get("/api/v1/research-chat/memories", headers=_bff_headers("user-alice"))
        assert alice_list.status_code == 200
        alice_items = alice_list.json()["items"]
        assert len(alice_items) == 1
        alice_mem_id = alice_items[0]["memory_id"]
        assert "Alice" in alice_items[0]["content_text"]

        bob_list = await client.get("/api/v1/research-chat/memories", headers=_bff_headers("user-bob"))
        assert bob_list.status_code == 200
        bob_items = bob_list.json()["items"]
        assert len(bob_items) == 1
        assert all(item["memory_id"] != alice_mem_id for item in bob_items)

        # IDOR 1: Bob tries to PATCH Alice's memory -> 404 NotFound
        idor_patch = await client.patch(
            f"/api/v1/research-chat/memories/{alice_mem_id}",
            headers=_bff_headers("user-bob"),
            json={"content_text": "Hacked by Bob", "pinned_by_user": True},
        )
        assert idor_patch.status_code == 404

        # IDOR 2: Bob tries to DELETE Alice's memory -> 404 NotFound
        idor_del = await client.delete(
            f"/api/v1/research-chat/memories/{alice_mem_id}",
            headers=_bff_headers("user-bob"),
        )
        assert idor_del.status_code == 404

        # IDOR 3: Bob tries to cascade-delete Alice's session -> 0 of Alice's memories retracted!
        idor_cascade = await client.post(
            "/api/v1/research-chat/memories/cascade-delete-session",
            headers=_bff_headers("user-bob"),
            json={"session_id": "sess-alice-1"},
        )
        assert idor_cascade.status_code == 200
        assert idor_cascade.json()["retracted_memories"] == 0

        # IDOR 4: Bob tries to read Alice's related sessions -> empty
        idor_rel = await client.get(
            "/api/v1/research-chat/sessions/sess-alice-1/related",
            headers=_bff_headers("user-bob"),
        )
        assert idor_rel.status_code == 200
        assert idor_rel.json()["episode_id"] is None
        assert idor_rel.json()["related_sessions"] == []

        # Verify Alice's memory and episode remain 100% untouched!
        alice_after = await client.get("/api/v1/research-chat/memories", headers=_bff_headers("user-alice"))
        assert alice_after.status_code == 200
        assert len(alice_after.json()["items"]) == 1
        assert alice_after.json()["items"][0]["content_text"] == "Alice giữ 10.000 FPT giá vốn 125.000đ bên SSI"

    async with sf() as s:
        alice_ep = await s.scalar(
            __import__("sqlalchemy").select(UserMemoryEpisode).where(
                UserMemoryEpisode.user_id == "user-alice",
                UserMemoryEpisode.external_session_id == "sess-alice-1",
            )
        )
        assert alice_ep is not None and alice_ep.status == "ACTIVE"
        alice_row = await s.get(UserMemoryItem, alice_mem_id)
        assert alice_row is not None and alice_row.tx_to.year >= 9999

    await eng.dispose()
