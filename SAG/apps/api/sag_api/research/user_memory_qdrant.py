"""Isolated Qdrant collection manager for SAG-Native User Memory (`aiinvest_user_memories`).

Design constraints (strictly isolated from corporate `aiinvest_search_units`):
- Dense `content_vector` (`size=1024`, Cosine, `on_disk=True`) + Sparse `bm25_sparse`
  (`modifier="idf"`, reusing `compute_sparse_bm25_vector(..., avg_doc_len=48.0)`).
- `on_disk_payload=True`, `hnsw_config={"m": 0, "payload_m": 16, "on_disk": True}`,
  `quantization_config={"scalar": {"type": "int8", "always_ram": False}}` to respect the 1g
  `sag-qdrant` RAM budget.
- Exactly 7 payload indexes:
  1. `user_scope_key` (`is_tenant=True`, keyword)
  2. `memory_type` (keyword)
  3. `pit_sensitivity` (keyword)
  4. `is_backtest_sandbox` (bool)
  5. `valid_to_ts` (integer)
  6. `tx_to_ts` (integer)
  7. `source_as_of_date_ts` (integer)
- Windows-safe infinity sentinel `INF_TS = 253402300799` (never `.timestamp()` on year 9999).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import quote
from weakref import WeakKeyDictionary

import httpx

from sag_api.core.config import settings
from sag_api.core.logging import get_logger
from sag_api.db.models.user_memory import (
    INF_TS,
    UserMemoryItem,
    date_to_utc_ts,
    safe_utc_timestamp,
)
from sag_api.services.search_index_service import compute_sparse_bm25_vector

log = get_logger("user_memory_qdrant")

USER_MEMORY_SPARSE_AVG_DOC_LEN: float = 48.0

# Exact 7-index budget for `aiinvest_user_memories`
USER_MEMORY_PAYLOAD_INDEX_FIELDS: tuple[tuple[str, str | dict[str, Any]], ...] = (
    ("user_scope_key", {"type": "keyword", "is_tenant": True}),
    ("memory_type", "keyword"),
    ("pit_sensitivity", "keyword"),
    ("is_backtest_sandbox", "bool"),
    ("valid_to_ts", "integer"),
    ("tx_to_ts", "integer"),
    ("source_as_of_date_ts", "integer"),
)

_VERIFIED_MEM_COLLECTIONS: WeakKeyDictionary[Any, set[tuple[str, int]]] = WeakKeyDictionary()
_MEM_COLLECTION_LOCKS: WeakKeyDictionary[Any, dict[str, asyncio.Lock]] = WeakKeyDictionary()


def resolve_user_memory_collection_name(collection_name: str | None = None) -> str:
    name = (collection_name or getattr(settings, "user_memory_qdrant_collection", "") or "").strip()
    return name or "aiinvest_user_memories"


def make_user_scope_key(tenant_id: str, user_id: str) -> str:
    """Canonical strict multi-tenant + per-user partition key (`is_tenant=True`)."""
    return f"{tenant_id.strip()}::{user_id.strip()}"


def build_user_memory_payload(item: UserMemoryItem) -> dict[str, Any]:
    """Serialize `UserMemoryItem` to Qdrant payload with Windows-safe integer timestamps."""
    return {
        "memory_id": str(item.memory_id),
        "tenant_id": str(item.tenant_id),
        "user_id": str(item.user_id),
        "user_scope_key": make_user_scope_key(item.tenant_id, item.user_id),
        "scope": str(item.scope),
        "memory_type": str(item.memory_type),
        "pit_sensitivity": str(item.pit_sensitivity),
        "is_backtest_sandbox": bool(item.is_backtest_sandbox),
        "valid_from_ts": safe_utc_timestamp(item.valid_from, default_inf=False),
        "valid_to_ts": safe_utc_timestamp(item.valid_to, default_inf=True),
        "tx_from_ts": safe_utc_timestamp(item.tx_from, default_inf=False),
        "tx_to_ts": safe_utc_timestamp(item.tx_to, default_inf=True),
        "source_as_of_date_ts": date_to_utc_ts(item.source_as_of_date),
        "source_as_of_date": item.source_as_of_date.isoformat(),
        "tickers": list(item.tickers_json or []),
        "staleness_status": str(item.staleness_status or "FRESH"),
        "pinned_by_user": bool(item.pinned_by_user),
        "reinforcement_count": int(item.reinforcement_count or 1),
        "origin_session_id": item.origin_session_id,
        "sync_version": int(item.sync_version or 1),
    }


def build_user_memory_patch_payload(item: UserMemoryItem) -> dict[str, Any]:
    """Minimal payload patch sent on `PENDING_PATCH` (closing valid_to/tx_to, staleness, pin)."""
    return {
        "valid_to_ts": safe_utc_timestamp(item.valid_to, default_inf=True),
        "tx_to_ts": safe_utc_timestamp(item.tx_to, default_inf=True),
        "staleness_status": str(item.staleness_status or "FRESH"),
        "pinned_by_user": bool(item.pinned_by_user),
        "reinforcement_count": int(item.reinforcement_count or 1),
        "sync_version": int(item.sync_version or 1),
    }


def build_user_memory_qdrant_filter(
    *,
    tenant_id: str,
    user_id: str,
    as_of_date: date,
    is_backtest: bool,
    memory_types: Sequence[str] | None = None,
    active_only: bool = True,
) -> dict[str, Any]:
    """Build strict Qdrant filter enforcing tenant+user isolation, Tri-Temporal PIT, and Backtest Default-Deny."""
    as_of_ts = date_to_utc_ts(as_of_date)
    must: list[dict[str, Any]] = [
        {"key": "user_scope_key", "match": {"value": make_user_scope_key(tenant_id, user_id)}},
    ]
    if active_only:
        must.append({"key": "valid_to_ts", "range": {"gte": INF_TS}})
        must.append({"key": "tx_to_ts", "range": {"gte": INF_TS}})
    if memory_types:
        clean_types = [str(t) for t in memory_types if t]
        if len(clean_types) == 1:
            must.append({"key": "memory_type", "match": {"value": clean_types[0]}})
        elif clean_types:
            must.append({"key": "memory_type", "match": {"any": clean_types}})

    if is_backtest:
        # Backtest Default-Deny: allow ONLY ATEMPORAL_FORMAT (STYLE_PREF_SAFE) OR
        # backtest-sandbox memories with source_as_of_date <= request.as_of_date.
        must.append(
            {
                "should": [
                    {"key": "pit_sensitivity", "match": {"value": "ATEMPORAL_FORMAT"}},
                    {
                        "must": [
                            {"key": "is_backtest_sandbox", "match": {"value": True}},
                            {"key": "source_as_of_date_ts", "range": {"lte": as_of_ts}},
                        ]
                    },
                ]
            }
        )
    else:
        # Live mode: NEVER read backtest sandbox memories; enforce source_as_of_date <= as_of_date.
        must.append({"key": "is_backtest_sandbox", "match": {"value": False}})
        must.append({"key": "source_as_of_date_ts", "range": {"lte": as_of_ts}})

    return {"must": must}


async def ensure_user_memory_collection(
    qdrant_client: httpx.AsyncClient,
    *,
    collection_name: str | None = None,
    vector_dim: int = 1024,
) -> str:
    """Create `aiinvest_user_memories` with on-disk HNSW, always_ram=False, and exact 7 payload indexes."""
    col = resolve_user_memory_collection_name(collection_name)
    verified = _VERIFIED_MEM_COLLECTIONS.get(qdrant_client)
    if verified is not None and (col, vector_dim) in verified:
        return col

    locks_by_col = _MEM_COLLECTION_LOCKS.get(qdrant_client)
    if locks_by_col is None:
        locks_by_col = {}
        try:
            _MEM_COLLECTION_LOCKS[qdrant_client] = locks_by_col
        except TypeError:
            locks_by_col = {}
    lock = locks_by_col.setdefault(col, asyncio.Lock())

    async with lock:
        verified = _VERIFIED_MEM_COLLECTIONS.get(qdrant_client)
        if verified is not None and (col, vector_dim) in verified:
            return col

        col_q = quote(col, safe="")
        collection_ready = False
        existing_indexes: set[str] = set()
        info_res = await qdrant_client.get(f"/collections/{col_q}")
        if info_res.is_success:
            result_data = info_res.json().get("result") or {}
            if isinstance(result_data, dict):
                cfg = result_data.get("config") or {}
                existing_vectors = (cfg.get("params") or {}).get("vectors") or {}
                if isinstance(existing_vectors, dict) and "content_vector" in existing_vectors:
                    existing_dim = existing_vectors["content_vector"].get("size")
                    if existing_dim and int(existing_dim) != int(vector_dim):
                        raise ValueError(
                            f"Qdrant user memory collection {col} dimension mismatch: "
                            f"existing={existing_dim} vs required={vector_dim}"
                        )
                    collection_ready = True
                schema = result_data.get("payload_schema")
                if isinstance(schema, dict):
                    existing_indexes = set(schema.keys())

        if not collection_ready:
            create_body: dict[str, Any] = {
                "vectors": {
                    "content_vector": {
                        "size": int(vector_dim),
                        "distance": "Cosine",
                        "on_disk": True,
                    }
                },
                "sparse_vectors": {
                    "bm25_sparse": {
                        "modifier": "idf",
                    }
                },
                "on_disk_payload": True,
                "hnsw_config": {
                    "m": 0,
                    "payload_m": 16,
                    "on_disk": True,
                },
                "quantization_config": {
                    "scalar": {
                        "type": "int8",
                        "always_ram": False,
                    }
                },
            }
            put_res = await qdrant_client.put(f"/collections/{col_q}", json=create_body)
            if put_res.status_code >= 400 and put_res.status_code != 409:
                if "already exists" not in put_res.text.lower():
                    raise RuntimeError(
                        f"Failed to create Qdrant collection {col}: HTTP {put_res.status_code} - {put_res.text}"
                    )

        missing = [
            (fname, fschema)
            for fname, fschema in USER_MEMORY_PAYLOAD_INDEX_FIELDS
            if fname not in existing_indexes
        ]
        for fname, fschema in missing:
            idx_res = await qdrant_client.put(
                f"/collections/{col_q}/index?wait=true",
                json={"field_name": fname, "field_schema": fschema},
            )
            if idx_res.status_code >= 400 and idx_res.status_code != 409:
                if "already exists" not in idx_res.text.lower():
                    raise RuntimeError(
                        f"Failed to create Qdrant user memory index '{fname}': "
                        f"HTTP {idx_res.status_code} - {idx_res.text}"
                    )

        try:
            vset = _VERIFIED_MEM_COLLECTIONS.get(qdrant_client)
            if vset is None:
                vset = set()
                _VERIFIED_MEM_COLLECTIONS[qdrant_client] = vset
            vset.add((col, vector_dim))
        except TypeError:
            pass
    return col


async def upsert_user_memory_point(
    qdrant_client: httpx.AsyncClient,
    *,
    item: UserMemoryItem,
    dense_vector: Sequence[float],
    collection_name: str | None = None,
) -> None:
    """Upsert a single user memory point with dense vector + BM25 sparse vector (`avg_doc_len=48.0`)."""
    vec = [float(x) for x in dense_vector]
    col = await ensure_user_memory_collection(
        qdrant_client,
        collection_name=collection_name,
        vector_dim=len(vec) if vec else 1024,
    )
    sparse = compute_sparse_bm25_vector(item.content_text, avg_doc_len=USER_MEMORY_SPARSE_AVG_DOC_LEN)
    vectors_map: dict[str, Any] = {"content_vector": vec}
    if sparse.get("indices"):
        vectors_map["bm25_sparse"] = sparse
    point = {
        "id": str(item.qdrant_point_id),
        "vector": vectors_map,
        "payload": build_user_memory_payload(item),
    }
    col_q = quote(col, safe="")
    res = await qdrant_client.put(
        f"/collections/{col_q}/points?wait=true",
        json={"points": [point]},
    )
    if res.status_code >= 400:
        raise RuntimeError(f"Qdrant user memory upsert failed: HTTP {res.status_code} - {res.text[:240]}")


async def patch_user_memory_payload(
    qdrant_client: httpx.AsyncClient,
    *,
    point_id: str,
    payload_patch: Mapping[str, Any],
    collection_name: str | None = None,
) -> None:
    """Patch Qdrant payload in-place for `PENDING_PATCH` (closing `valid_to_ts`/`tx_to_ts` or staleness)."""
    col = resolve_user_memory_collection_name(collection_name)
    col_q = quote(col, safe="")
    res = await qdrant_client.post(
        f"/collections/{col_q}/points/payload?wait=true",
        json={
            "payload": dict(payload_patch),
            "points": [str(point_id)],
        },
    )
    if res.status_code >= 400:
        raise RuntimeError(f"Qdrant user memory patch failed: HTTP {res.status_code} - {res.text[:240]}")


async def delete_user_memory_points(
    qdrant_client: httpx.AsyncClient,
    *,
    point_ids: Sequence[str],
    collection_name: str | None = None,
) -> None:
    """Delete Qdrant points for `PENDING_DELETE`."""
    ids = [str(pid) for pid in point_ids if pid]
    if not ids:
        return
    col = resolve_user_memory_collection_name(collection_name)
    col_q = quote(col, safe="")
    res = await qdrant_client.post(
        f"/collections/{col_q}/points/delete?wait=true",
        json={"points": ids},
    )
    if res.status_code >= 400 and res.status_code != 404:
        raise RuntimeError(f"Qdrant user memory delete failed: HTTP {res.status_code} - {res.text[:240]}")


async def search_user_memories_hybrid(
    qdrant_client: httpx.AsyncClient,
    *,
    tenant_id: str,
    user_id: str,
    query_text: str,
    as_of_date: date,
    is_backtest: bool,
    dense_vector: Sequence[float] | None = None,
    memory_types: Sequence[str] | None = None,
    limit: int = 12,
    collection_name: str | None = None,
) -> list[dict[str, Any]]:
    """Run hybrid dense + BM25 sparse search returning raw `dense_cos`, `bm25_norm`, and ranks.

    Gracefully returns `[]` if Qdrant is unreachable or collection does not exist yet so
    Smart Read degrades cleanly to SQL-only without breaking `/stream`.
    """
    col = resolve_user_memory_collection_name(collection_name)
    col_q = quote(col, safe="")
    q_filter = build_user_memory_qdrant_filter(
        tenant_id=tenant_id,
        user_id=user_id,
        as_of_date=as_of_date,
        is_backtest=is_backtest,
        memory_types=memory_types,
        active_only=True,
    )
    sparse = compute_sparse_bm25_vector(query_text, avg_doc_len=USER_MEMORY_SPARSE_AVG_DOC_LEN)
    searches: list[tuple[str, dict[str, Any]]] = []
    if dense_vector:
        searches.append(
            (
                "dense",
                {
                    "query": [float(x) for x in dense_vector],
                    "using": "content_vector",
                    "filter": q_filter,
                    "limit": max(1, min(limit, 32)),
                    "with_payload": True,
                },
            )
        )
    if sparse.get("indices"):
        searches.append(
            (
                "sparse",
                {
                    "query": sparse,
                    "using": "bm25_sparse",
                    "filter": q_filter,
                    "limit": max(1, min(limit, 32)),
                    "with_payload": True,
                },
            )
        )
    if not searches:
        return []

    try:
        res = await qdrant_client.post(
            f"/collections/{col_q}/points/query/batch",
            json={"searches": [body for _, body in searches]},
        )
        if res.status_code == 404:
            return []
        if res.status_code >= 400:
            log.warning("Qdrant user memory hybrid query HTTP %s", res.status_code)
            return []
        data = res.json()
        raw_results = data.get("result")
        if not isinstance(raw_results, list) or len(raw_results) != len(searches):
            return []
    except Exception as exc:  # noqa: BLE001
        log.warning("Qdrant user memory hybrid query degraded to SQL-only (%s)", type(exc).__name__)
        return []

    by_mem_id: dict[str, dict[str, Any]] = {}
    max_bm25 = 0.0
    for (channel, _body), item in zip(searches, raw_results, strict=True):
        points = item.get("points") if isinstance(item, Mapping) else item
        if not isinstance(points, list):
            continue
        for rank, pt in enumerate(points, start=1):
            if not isinstance(pt, Mapping):
                continue
            payload = pt.get("payload") if isinstance(pt.get("payload"), Mapping) else {}
            mid = str(payload.get("memory_id") or pt.get("id") or "")
            if not mid:
                continue
            score = float(pt.get("score") or 0.0)
            entry = by_mem_id.setdefault(
                mid,
                {
                    "memory_id": mid,
                    "point_id": str(pt.get("id") or ""),
                    "payload": dict(payload),
                    "dense_cos": 0.0,
                    "bm25_raw": 0.0,
                    "bm25_norm": 0.0,
                    "dense_rank": None,
                    "sparse_rank": None,
                },
            )
            if channel == "dense":
                entry["dense_cos"] = max(float(entry["dense_cos"]), score)
                entry["dense_rank"] = rank if entry["dense_rank"] is None else min(entry["dense_rank"], rank)
            else:
                entry["bm25_raw"] = max(float(entry["bm25_raw"]), score)
                max_bm25 = max(max_bm25, score)
                entry["sparse_rank"] = rank if entry["sparse_rank"] is None else min(entry["sparse_rank"], rank)

    # Normalize BM25 raw score into [0, 1] using soft saturation so a single weak hit never inflates to 1.0
    for entry in by_mem_id.values():
        raw_b = float(entry["bm25_raw"])
        if raw_b > 0.0:
            # Sigmoid-like saturation: raw_b / (raw_b + 3.0) capped at 1.0, scaled cleanly
            entry["bm25_norm"] = round(min(1.0, raw_b / (raw_b + 3.0)), 4)
            if max_bm25 > 0.0 and raw_b >= 4.0:
                entry["bm25_norm"] = max(entry["bm25_norm"], round(min(1.0, raw_b / max_bm25), 4))

    return list(by_mem_id.values())
