"""Qdrant vector adapter for the zleap-sag storage contract."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import httpx
from zleap.sag.core.adapters import registry
from zleap.sag.core.adapters.capabilities import Capability
from zleap.sag.core.adapters.models import (
    BulkResult,
    FailedItem,
    Filter,
    VectorHit,
    VectorQuery,
    VectorRecord,
)
from zleap.sag.exceptions import ConfigError, StorageError, StorageSchemaMismatchError


@dataclass(frozen=True, slots=True)
class QdrantVectorConfig:
    provider: str = "qdrant"
    url: str = "http://localhost:6333"
    api_key: str | None = None
    timeout: float = 30.0


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _point_id(collection: str, record_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sag:qdrant:{collection}:{record_id}"))


def _field_name(field: str | None) -> str:
    return "_sag_id" if field == "_id" else str(field)


def _filter_condition_to_qdrant(expression: Filter) -> dict[str, Any]:
    op = expression.operator
    if op == "eq":
        return {"key": _field_name(expression.field), "match": {"value": _json_value(expression.value)}}
    if op == "in":
        return {
            "key": _field_name(expression.field),
            "match": {"any": [_json_value(value) for value in expression.value]},
        }
    if op == "range":
        return {"key": _field_name(expression.field), "range": _json_value(expression.value)}
    if op in {"and", "or", "not", "exists"}:
        return _filter_to_qdrant(expression)
    raise ValueError(f"Unsupported filter operator: {op!r}")


def _filter_to_qdrant(expression: Filter | None) -> dict[str, Any] | None:
    if expression is None:
        return None
    op = expression.operator
    if op == "exists":
        return {"must_not": [{"is_empty": {"key": _field_name(expression.field)}}]}
    if op in {"eq", "in", "range"}:
        return {"must": [_filter_condition_to_qdrant(expression)]}
    children = [_filter_condition_to_qdrant(child) for child in expression.children]
    if op == "and":
        return {"must": children}
    if op == "or":
        return {"should": children}
    if op == "not":
        return {"must_not": children}
    raise ValueError(f"Unsupported filter operator: {op!r}")


def _record_from_point(point: dict[str, Any]) -> VectorRecord:
    payload = dict(point.get("payload") or {})
    record_id = str(payload.pop("_sag_id", point.get("id", "")))
    raw_vectors = point.get("vector") or {}
    if isinstance(raw_vectors, list):
        raw_vectors = {"vector": raw_vectors}
    vectors = {
        str(name): [float(value) for value in values]
        for name, values in raw_vectors.items()
        if isinstance(values, list)
    }
    return VectorRecord(record_id, payload, vectors)


class QdrantVectorStore:
    """Qdrant adapter using the stable REST API and named vectors."""

    # zleap-sag 0.12's schema facade recognizes these built-in storage modes.
    # The actual selected provider remains qdrant in EngineConfig/registry.
    provider = "es"
    capabilities = frozenset({Capability.VECTOR_KNN, Capability.FILTERED_KNN})

    def __init__(
        self,
        config: QdrantVectorConfig | None = None,
        *,
        storage_mode: str = "normal",
        embedding_dimensions: int | None = None,
    ) -> None:
        if config is None:
            raise ConfigError("QdrantVectorStore requires QdrantVectorConfig")
        self._config = config
        self._storage_mode = storage_mode
        self._embedding_dimensions = embedding_dimensions
        self._client: httpx.AsyncClient | None = None
        self._payload_index_lock = asyncio.Lock()
        self._payload_indexes_ready: set[str] = set()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"api-key": self._config.api_key} if self._config.api_key else None
            self._client = httpx.AsyncClient(
                base_url=self._config.url.rstrip("/"),
                headers=headers,
                timeout=self._config.timeout,
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._get_client().request(method, path, **kwargs)
        if response.is_error:
            detail = response.text[:500]
            raise StorageError(f"Qdrant request failed ({response.status_code}): {detail}")
        if not response.content:
            return {}
        return response.json()

    @staticmethod
    def _path(collection: str, suffix: str = "") -> str:
        return f"/collections/{quote(collection, safe='')}{suffix}"

    async def _collection_info(self, collection: str) -> dict[str, Any] | None:
        response = await self._get_client().get(self._path(collection))
        if response.status_code == 404:
            return None
        if response.is_error:
            raise StorageError(f"Qdrant collection lookup failed ({response.status_code})")
        return response.json().get("result") or {}

    async def schema_object_names(self) -> frozenset[str]:
        data = await self._request("GET", "/collections")
        return frozenset(
            str(item["name"])
            for item in (data.get("result") or {}).get("collections", [])
            if item.get("name")
        )

    @staticmethod
    def _vector_schema(info: dict[str, Any]) -> dict[str, dict[str, Any]]:
        vectors = ((info.get("config") or {}).get("params") or {}).get("vectors") or {}
        if "size" in vectors:
            return {"vector": vectors}
        return {str(name): value for name, value in vectors.items() if isinstance(value, dict)}

    async def validate_schema_object(self, name: str, dimensions: int | None) -> None:
        info = await self._collection_info(name)
        if info is None:
            return
        from zleap.sag.core.storage.index_schemas import INDEX_SCHEMAS

        expected_fields = INDEX_SCHEMAS.get(name)
        if expected_fields is None:
            return
        actual = self._vector_schema(info)
        diffs: list[str] = []
        for field in expected_fields.vector_fields:
            config = actual.get(field)
            if config is None:
                diffs.append(f"{name}.{field}: missing Qdrant vector")
                continue
            if dimensions is not None and int(config.get("size", 0)) != dimensions:
                diffs.append(
                    f"{name}.{field}: dimension {config.get('size')} != {dimensions}"
                )
            if str(config.get("distance", "")).lower() != "cosine":
                diffs.append(f"{name}.{field}: distance is not cosine")
        if diffs:
            raise StorageSchemaMismatchError(f"Qdrant collection {name} schema mismatch", diffs=diffs)
        await self._ensure_payload_indexes(name, info=info)

    @staticmethod
    def _payload_index_schemas(name: str) -> dict[str, str]:
        from zleap.sag.core.storage.index_schemas import INDEX_SCHEMAS

        schema = INDEX_SCHEMAS.get(name)
        if schema is None:
            return {}
        fields: dict[str, str] = {}
        for field in schema.keyword_fields + schema.text_keyword_fields + schema.array_fields:
            fields[field] = "keyword"
        for field in schema.bool_fields:
            fields[field] = "bool"
        for field in schema.datetime_fields:
            fields[field] = "datetime"
        for field in schema.int_fields:
            fields[field] = "integer"
        return fields

    async def _ensure_payload_indexes(
        self,
        name: str,
        *,
        info: dict[str, Any] | None = None,
    ) -> None:
        expected = self._payload_index_schemas(name)
        if not expected or name in self._payload_indexes_ready:
            return
        async with self._payload_index_lock:
            if name in self._payload_indexes_ready:
                return
            collection_info = info or await self._collection_info(name)
            if collection_info is None:
                return
            actual = collection_info.get("payload_schema") or {}
            for field, field_schema in expected.items():
                existing = actual.get(field)
                if isinstance(existing, dict):
                    actual_schema = existing.get("data_type")
                    if actual_schema and actual_schema != field_schema:
                        raise StorageSchemaMismatchError(
                            f"Qdrant collection {name} payload index mismatch",
                            diffs=[f"{field}: {actual_schema} != {field_schema}"],
                        )
                    continue
                await self._request(
                    "PUT",
                    self._path(name, "/index?wait=true"),
                    json={"field_name": field, "field_schema": field_schema},
                )
            self._payload_indexes_ready.add(name)

    async def create_schema_object(self, name: str, dimensions: int) -> None:
        from zleap.sag.core.storage.index_schemas import INDEX_SCHEMAS

        schema = INDEX_SCHEMAS[name]
        await self._request(
            "PUT",
            self._path(name),
            json={
                "vectors": {
                    field: {"size": dimensions, "distance": "Cosine"}
                    for field in schema.vector_fields
                },
                "on_disk_payload": True,
            },
        )
        await self._ensure_payload_indexes(name)

    async def _vector_dimensions(self, collection: str) -> dict[str, int]:
        info = await self._collection_info(collection)
        if info is None:
            return {}
        return {
            name: int(config["size"])
            for name, config in self._vector_schema(info).items()
            if config.get("size")
        }

    async def upsert(self, collection: str, records: list[VectorRecord]) -> BulkResult:
        if not records:
            return BulkResult()
        dimensions = await self._vector_dimensions(collection)
        points: list[dict[str, Any]] = []
        for record in records:
            vectors = {
                name: [float(value) for value in values]
                for name, values in record.vectors.items()
                if name in dimensions
            }
            if not vectors and dimensions:
                # ponytail: Qdrant requires a vector for each point; payload-only
                # lite records use zero vectors because they are never searched.
                vectors = {name: [0.0] * size for name, size in dimensions.items()}
            payload = {"_sag_id": record.id, **_json_value(record.payload)}
            points.append(
                {
                    "id": _point_id(collection, record.id),
                    "vector": vectors,
                    "payload": payload,
                }
            )
        try:
            await self._request(
                "PUT",
                self._path(collection, "/points?wait=true"),
                json={"points": points},
            )
        except Exception as exc:  # noqa: BLE001 - normalize per-item contract
            detail = str(exc)
            return BulkResult(failed_items=tuple(FailedItem(record.id, detail) for record in records))
        return BulkResult(succeeded_ids=tuple(record.id for record in records))

    async def get_many(self, collection: str, ids: list[str]) -> list[VectorRecord]:
        if not ids:
            return []
        data = await self._request(
            "POST",
            self._path(collection, "/points"),
            json={
                "ids": [_point_id(collection, record_id) for record_id in ids],
                "with_payload": True,
                "with_vector": True,
            },
        )
        found = {
            record.id: record
            for record in (_record_from_point(point) for point in data.get("result", []))
        }
        return [found[record_id] for record_id in ids if record_id in found]

    async def fetch_vector_fields(
        self,
        collection: str,
        ids: list[str],
        fields: list[str],
        *,
        routing: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        del routing
        records = await self.get_many(collection, ids)
        return {
            record.id: {
                field: record.vectors.get(field, record.payload.get(field))
                for field in fields
            }
            for record in records
        }

    async def _scroll(
        self,
        collection: str,
        *,
        filters: Filter | None,
        limit: int,
        include_vectors: bool,
    ) -> list[VectorRecord]:
        if filters is not None:
            await self._ensure_payload_indexes(collection)
        records: list[VectorRecord] = []
        offset: str | None = None
        while len(records) < limit:
            body: dict[str, Any] = {
                "limit": min(max(limit - len(records), 100), 1000),
                "with_payload": True,
                "with_vector": include_vectors,
            }
            if offset is not None:
                body["offset"] = offset
            qdrant_filter = _filter_to_qdrant(filters)
            if qdrant_filter:
                body["filter"] = qdrant_filter
            data = await self._request("POST", self._path(collection, "/points/scroll"), json=body)
            result = data.get("result") or {}
            page = result.get("points") or []
            records.extend(_record_from_point(point) for point in page)
            offset = result.get("next_page_offset")
            if not page or offset is None:
                break
        return records[:limit]

    async def query(self, collection: str, request: VectorQuery) -> list[VectorHit]:
        if request.text:
            raise StorageError("Qdrant provider does not support lexical search")
        if request.filters is not None:
            await self._ensure_payload_indexes(collection)
        if request.vector is not None:
            body: dict[str, Any] = {
                "query": [float(value) for value in request.vector],
                "using": request.vector_field,
                "limit": request.limit,
                "with_payload": True,
                "with_vector": request.include_vectors,
            }
            qdrant_filter = _filter_to_qdrant(request.filters)
            if qdrant_filter:
                body["filter"] = qdrant_filter
            data = await self._request("POST", self._path(collection, "/points/query"), json=body)
            points = (data.get("result") or {}).get("points", [])
            hits: list[VectorHit] = []
            for point in points:
                record = _record_from_point(point)
                score = float(point.get("score") or 0.0)
                hits.append(
                    VectorHit(
                        id=record.id,
                        score=(score + 1.0) / 2.0,
                        payload=record.payload,
                        vectors=record.vectors if request.include_vectors else {},
                    )
                )
            return hits

        records = await self._scroll(
            collection,
            filters=request.filters,
            limit=max(request.limit, request.limit * 10 if request.distinct_field else request.limit),
            include_vectors=request.include_vectors,
        )
        if request.sort:
            for field, direction in reversed(request.sort):
                records.sort(
                    key=lambda record: (record.payload.get(field) is None, record.payload.get(field)),
                    reverse=direction == "desc",
                )
        if request.distinct_field:
            seen: set[Any] = set()
            records = [
                record
                for record in records
                if (value := record.payload.get(request.distinct_field)) not in seen
                and not seen.add(value)
            ]
        return [VectorHit(record.id, 0.0, record.payload, record.vectors) for record in records[: request.limit]]

    async def fetch_relation_vectors(
        self,
        index: str,
        *,
        filters: Filter | None = None,
        size: int = 10000,
    ) -> list[dict[str, Any]]:
        records = await self._scroll(
            index,
            filters=filters,
            limit=size,
            include_vectors=True,
        )
        return [
            {**record.payload, "vector": record.vectors.get("vector", [])}
            for record in records
            if record.payload.get("event_id") and record.payload.get("entity_id")
        ]

    async def delete(self, collection: str, ids: list[str]) -> BulkResult:
        if not ids:
            return BulkResult()
        try:
            await self._request(
                "POST",
                self._path(collection, "/points/delete?wait=true"),
                json={"points": [_point_id(collection, record_id) for record_id in ids]},
            )
        except StorageError as exc:
            if "404" not in str(exc):
                return BulkResult(failed_items=tuple(FailedItem(record_id, str(exc)) for record_id in ids))
        return BulkResult(succeeded_ids=tuple(ids))

    async def publish(self, collections: list[str]) -> None:
        del collections

    async def optimize(self) -> bool:
        return True

    async def ping(self, timeout: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self._request("GET", "/collections"), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - expose one storage error type
            raise StorageError(f"Qdrant connection failed: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


registry.register("vector", "qdrant", QdrantVectorStore)


__all__ = ["QdrantVectorConfig", "QdrantVectorStore"]
