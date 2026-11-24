"""Bounded read-only queries for Phase 2C SearchUnit points."""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from sag_api.services.search_index_service import (
    COMMON_STOPWORDS,
    normalize_numeric_separators,
)


class SearchIndexUnavailable(RuntimeError):
    """A sanitized failure while reading the canonical search index."""


@dataclass(frozen=True, slots=True)
class SearchUnitHit:
    point_id: str
    score: float
    payload: dict[str, Any]


_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_CONTINUOUS_SCRIPT_RUN_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\u0e00-\u0e7f\u0e80-\u0eff\u1780-\u17ff\u1000-\u109f]+"
)
_TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_SPARSE_QUERY_STOPWORDS = COMMON_STOPWORDS
_QUERY_FRAMING_STOPWORDS = frozenset(
    {
        "question", "answer", "response", "give", "discuss", "reference",
        "based", "according", "provide", "provided", "information", "please", "state",
        "tell", "explain", "describe", "whether",
    }
)


def _expand_continuous_script_tokens(tokens: list[str]) -> list[str]:
    """Split unspaced CJK/Thai/Lao/Khmer/Myanmar runs into overlapping character bigrams."""
    out: list[str] = []
    for tok in tokens:
        if not _CONTINUOUS_SCRIPT_RUN_RE.search(tok):
            out.append(tok)
            continue
        last_end = 0
        for match in _CONTINUOUS_SCRIPT_RUN_RE.finditer(tok):
            if match.start() > last_end:
                prefix = tok[last_end : match.start()]
                if prefix:
                    out.append(prefix)
            run = match.group(0)
            if len(run) == 1:
                out.append(run)
            else:
                for pos in range(len(run) - 1):
                    out.append(run[pos : pos + 2])
            last_end = match.end()
        if last_end < len(tok):
            suffix = tok[last_end:]
            if suffix:
                out.append(suffix)
    return out


def build_sparse_query_vector(query: str) -> dict[str, list[int] | list[float]]:
    """Encode query terms with Phase 2C's tokenizer and stable MD5 buckets.

    Uses sub-linear query TF and alphanumeric/entity boosting so repeated framing words
    never drown out discriminative entity, numeric, or domain anchors.
    """
    normalized_q = normalize_numeric_separators(query or "")
    normalized_q = re.sub(
        r"\b([a-z]{1,2})\s*&\s*([a-z]{1,2})\b",
        r"\1\2",
        normalized_q,
        flags=re.IGNORECASE,
    )
    orig_tokens = _TOKEN_PATTERN.findall(normalized_q)
    proper_tokens = {
        tok.lower()
        for idx, tok in enumerate(orig_tokens)
        if idx > 0 and any(ch.isupper() for ch in tok) and len(tok) >= 2
    }
    raw_tokens = [tok.lower() for tok in orig_tokens]
    if _CONTINUOUS_SCRIPT_RUN_RE.search(normalized_q):
        raw_tokens = _expand_continuous_script_tokens(raw_tokens)
    filtered_tokens = [t for t in raw_tokens if t not in _SPARSE_QUERY_STOPWORDS]
    active_tokens = filtered_tokens if filtered_tokens else raw_tokens

    # Expand glued alphanumeric identifiers (e.g. fy2018 -> fy2018, 2018)
    expanded_tokens: list[str] = list(active_tokens)
    for tok in active_tokens:
        if tok.isalnum() and not tok.isalpha() and not tok.isdigit() and len(tok) >= 3:
            for sub in re.findall(r"[a-z]+|\d{2,}", tok):
                if len(sub) >= 2 and sub not in _SPARSE_QUERY_STOPWORDS:
                    expanded_tokens.append(sub)

    counts = Counter(expanded_tokens)
    is_long_query = len(active_tokens) >= 6
    sparse: dict[int, float] = {}
    for token, frequency in counts.items():
        # Sub-linear TF saturation prevents repeated corpus-title words from dominating
        base_tf = 1.0 + 0.35 * math.log(frequency) if frequency > 1 else 1.0
        if is_long_query and token in _QUERY_FRAMING_STOPWORDS:
            weight = base_tf * 0.2
        elif is_long_query and (any(ch.isdigit() for ch in token) or token in proper_tokens):
            weight = base_tf * 2.2
        else:
            weight = base_tf

        token_id = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16) % 1_000_000
        sparse[token_id] = max(sparse.get(token_id, 0.0), round(float(weight), 4))
        if token.isascii() and token.isalpha():
            stem = (
                token[:-3]
                if len(token) >= 7 and token.endswith("ing")
                else token[:-2]
                if len(token) >= 6 and token.endswith("ed") and not token.endswith("eed")
                else token[:-1]
                if len(token) >= 5 and token.endswith("s") and not token.endswith(("ss", "us", "is"))
                else None
            )
            if stem and len(stem) >= 4:
                stem_id = int(hashlib.md5(stem.encode("utf-8")).hexdigest()[:8], 16) % 1_000_000
                sparse[stem_id] = max(sparse.get(stem_id, 0.0), round(float(weight) * 0.6, 4))
    indices = sorted(sparse)
    return {"indices": indices, "values": [sparse[index] for index in indices]}


def build_search_filter(
    *,
    project_id: str,
    tenant_id: str,
    partition_id: str,
    document_version_ids: list[str],
    as_of_ts: int | None = None,
    sub_doc_slots: list[str] | None = None,
    doc_types: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    fiscal_quarters: list[int] | None = None,
    tree_version: str | None = None,
    routing_slot: str | None = None,
    membership_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build exact ACL filters plus optional PIT (valid_from_ts <= as_of_ts) and version-pinned tree membership."""
    versions = sorted(set(document_version_ids))
    if not project_id.strip() or not tenant_id.strip() or not partition_id.strip() or not versions:
        raise ValueError("A complete authorized SearchUnit scope is required")
    filter_body: dict[str, Any] = {
        "must": [
            {"key": "project_id", "match": {"value": project_id}},
            {"key": "tenant_id", "match": {"value": tenant_id}},
            {"key": "security_partition_id", "match": {"value": partition_id}},
            {"key": "document_version_id", "match": {"any": versions}},
        ]
    }
    if as_of_ts is not None:
        ts_int = int(as_of_ts)
        filter_body["must"].append({"key": "valid_from_ts", "range": {"lte": ts_int}})
        filter_body["must"].append(
            {
                "should": [
                    {"is_empty": {"key": "valid_to_ts"}},
                    {"key": "valid_to_ts", "range": {"gt": ts_int}},
                ]
            }
        )
    if sub_doc_slots:
        slots = sorted({s.strip() for s in sub_doc_slots if isinstance(s, str) and s.strip()})
        if slots:
            filter_body["must"].append({"key": "sub_doc_slot", "match": {"any": slots}})
    if doc_types:
        dtypes = sorted({d.strip() for d in doc_types if isinstance(d, str) and d.strip()})
        if dtypes:
            filter_body["must"].append({"key": "doc_type", "match": {"any": dtypes}})
    if fiscal_years:
        fys = sorted({int(y) for y in fiscal_years if y is not None})
        if fys:
            filter_body["must"].append({"key": "fiscal_year", "match": {"any": fys}})
    if fiscal_quarters:
        fqs = sorted({int(q) for q in fiscal_quarters if q is not None})
        if fqs:
            filter_body["must"].append({"key": "fiscal_quarter", "match": {"any": fqs}})
    has_tree_scope = tree_version is not None or routing_slot is not None or membership_node_ids is not None
    if has_tree_scope:
        nodes = sorted({node.strip() for node in membership_node_ids or [] if node.strip()})
        if not tree_version or routing_slot not in {"SLOT_A", "SLOT_B"} or not nodes or len(nodes) > 256:
            raise ValueError("A complete bounded tree membership filter is required")
        slot = "a" if routing_slot == "SLOT_A" else "b"
        filter_body["must"].append({"key": f"tree_version_{slot}", "match": {"value": tree_version}})
        filter_body["should"] = [
            {"key": f"primary_node_{slot}", "match": {"any": nodes}},
            {"key": f"secondary_node_ids_{slot}", "match": {"any": nodes}},
        ]
    return filter_body


def _norm_project_id(raw: str) -> str:
    s = raw.strip()
    if len(s) == 3 and s.isascii() and s.isalpha():
        return s.upper()
    return s


def build_cross_ref_search_filter(
    *,
    tenant_id: str,
    mentioned_tickers: list[str],
    exclude_project_ids: list[str],
    allowed_project_ids: list[str] | set[str] | frozenset[str] | None = None,
    allowed_document_version_ids: list[str] | None = None,
    as_of_ts: int | None = None,
) -> dict[str, Any]:
    """Build tenant-scoped, PIT-filtered CROSS-REF filter.

    Enforces `mentioned_tickers CONTAINS target` and `project_id != target`.
    """
    clean_tenant = (tenant_id or "").strip()
    targets = sorted({t.strip().upper() for t in mentioned_tickers if isinstance(t, str) and t.strip()})
    excludes = sorted({_norm_project_id(p) for p in exclude_project_ids if isinstance(p, str) and p.strip()})
    exclude_lookup = {e.lower() for e in excludes}
    if not clean_tenant or not targets:
        raise ValueError("A valid tenant_id and at least one mentioned_ticker are required")

    must_clauses: list[dict[str, Any]] = [
        {"key": "tenant_id", "match": {"value": clean_tenant}},
        {"key": "mentioned_tickers", "match": {"any": targets}},
    ]
    if allowed_project_ids is not None and "*" not in allowed_project_ids:
        allowed_clean = sorted(
            {
                _norm_project_id(p)
                for p in allowed_project_ids
                if isinstance(p, str) and p.strip() and _norm_project_id(p).lower() not in exclude_lookup
            }
        )
        if not allowed_clean:
            raise ValueError("No authorized projects remain for cross-ref search after exclusions")
        must_clauses.append({"key": "project_id", "match": {"any": allowed_clean}})
    if allowed_document_version_ids is not None:
        vers = sorted({v.strip() for v in allowed_document_version_ids if isinstance(v, str) and v.strip()})
        if not vers:
            raise ValueError("No active document versions available for cross-ref search")
        must_clauses.append({"key": "document_version_id", "match": {"any": vers}})
    if as_of_ts is not None:
        ts_int = int(as_of_ts)
        must_clauses.append({"key": "valid_from_ts", "range": {"lte": ts_int}})
        must_clauses.append(
            {
                "should": [
                    {"is_empty": {"key": "valid_to_ts"}},
                    {"key": "valid_to_ts", "range": {"gt": ts_int}},
                ]
            }
        )

    filter_body: dict[str, Any] = {"must": must_clauses}
    if excludes:
        filter_body["must_not"] = [{"key": "project_id", "match": {"any": excludes}}]
    return filter_body


def build_universe_search_filter(
    *,
    tenant_id: str,
    allowed_project_ids: list[str] | set[str] | frozenset[str] | None = None,
    allowed_document_version_ids: list[str] | None = None,
    as_of_ts: int | None = None,
    sub_doc_slots: list[str] | None = None,
) -> dict[str, Any]:
    """Build strictly tenant-scoped, PIT-filtered Open Universe filter across the shared Qdrant chamber."""
    clean_tenant = (tenant_id or "").strip()
    if not clean_tenant:
        raise ValueError("A valid tenant_id is required for universe search")

    must_clauses: list[dict[str, Any]] = [
        {"key": "tenant_id", "match": {"value": clean_tenant}},
    ]
    if allowed_project_ids is not None and "*" not in allowed_project_ids:
        allowed_clean = sorted(
            {_norm_project_id(p) for p in allowed_project_ids if isinstance(p, str) and p.strip()}
        )
        if not allowed_clean:
            raise ValueError("No authorized projects available for universe search")
        must_clauses.append({"key": "project_id", "match": {"any": allowed_clean}})
    if allowed_document_version_ids is not None:
        vers = sorted({v.strip() for v in allowed_document_version_ids if isinstance(v, str) and v.strip()})
        if not vers:
            raise ValueError("No active document versions available for universe search")
        must_clauses.append({"key": "document_version_id", "match": {"any": vers}})
    if as_of_ts is not None:
        ts_int = int(as_of_ts)
        must_clauses.append({"key": "valid_from_ts", "range": {"lte": ts_int}})
        must_clauses.append(
            {
                "should": [
                    {"is_empty": {"key": "valid_to_ts"}},
                    {"key": "valid_to_ts", "range": {"gt": ts_int}},
                ]
            }
        )
    if sub_doc_slots:
        slots = sorted({s.strip() for s in sub_doc_slots if isinstance(s, str) and s.strip()})
        if slots:
            must_clauses.append({"key": "sub_doc_slot", "match": {"any": slots}})
    return {"must": must_clauses}


class SearchUnitQdrantStore:
    """Thin adapter over Qdrant's points/query and exact-point read endpoints."""

    def __init__(self, client: httpx.AsyncClient, *, max_retries: int = 1) -> None:
        self._client = client
        self._max_retries = max(0, min(max_retries, 3))

    @staticmethod
    def _parse_points(points: list[Any]) -> list[SearchUnitHit]:
        hits: list[SearchUnitHit] = []
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("payload"), dict):
                raise SearchIndexUnavailable("Search index returned an invalid point")
            try:
                score = float(point["score"])
            except (KeyError, TypeError, ValueError) as error:
                raise SearchIndexUnavailable("Search index returned an invalid score") from error
            point_id = point.get("id")
            if not isinstance(point_id, (str, int)) or not math.isfinite(score):
                raise SearchIndexUnavailable("Search index returned an invalid point")
            hits.append(SearchUnitHit(str(point_id), score, point["payload"]))
        return hits

    async def search(
        self,
        collection: str,
        *,
        vector_name: str,
        query: list[float] | dict[str, list[int] | list[float]],
        search_filter: dict[str, Any],
        limit: int,
    ) -> list[SearchUnitHit]:
        if not limit:
            return []
        if vector_name == "bm25_sparse" and (
            not isinstance(query, dict) or not query.get("indices") or not query.get("values")
        ):
            return []
        path = f"/collections/{quote(collection, safe='')}/points/query"
        body = {
            "query": query,
            "using": vector_name,
            "filter": search_filter,
            "limit": limit,
            "with_payload": True,
            "with_vector": False,
        }
        data = await self._request("POST", path, json=body)
        points = (data.get("result") or {}).get("points")
        if not isinstance(points, list):
            raise SearchIndexUnavailable("Search index returned an invalid query response")
        return self._parse_points(points)

    async def search_batch(
        self,
        collection: str,
        *,
        searches: list[dict[str, Any]],
    ) -> list[list[SearchUnitHit]]:
        """Execute multiple vector/sparse sub-queries in a single Qdrant HTTP round-trip."""
        if not searches:
            return []
        active_indices: list[int] = []
        active_bodies: list[dict[str, Any]] = []
        for idx, spec in enumerate(searches):
            limit = int(spec.get("limit") or 0)
            vector_name = str(spec.get("vector_name") or "")
            query = spec.get("query")
            search_filter = spec.get("search_filter")
            if limit <= 0:
                continue
            if vector_name == "bm25_sparse" and (
                not isinstance(query, dict) or not query.get("indices") or not query.get("values")
            ):
                continue
            active_indices.append(idx)
            active_bodies.append(
                {
                    "query": query,
                    "using": vector_name,
                    "filter": search_filter,
                    "limit": limit,
                    "with_payload": True,
                    "with_vector": False,
                }
            )
        results: list[list[SearchUnitHit]] = [[] for _ in searches]
        if not active_bodies:
            return results
        path = f"/collections/{quote(collection, safe='')}/points/query/batch"
        data = await self._request("POST", path, json={"searches": active_bodies})
        batch_result = data.get("result")
        if not isinstance(batch_result, list) or len(batch_result) != len(active_bodies):
            raise SearchIndexUnavailable("Search index returned an invalid batch query response")
        for idx, item in zip(active_indices, batch_result, strict=True):
            points = (item or {}).get("points") if isinstance(item, dict) else None
            if not isinstance(points, list):
                raise SearchIndexUnavailable("Search index returned an invalid query response")
            results[idx] = self._parse_points(points)
        return results

    async def count(self, collection: str, *, search_filter: dict[str, Any]) -> int:
        """Check derived-index availability within the exact authorized scope."""
        path = f"/collections/{quote(collection, safe='')}/points/count"
        data = await self._request("POST", path, json={"filter": search_filter, "exact": True})
        result = data.get("result")
        count = result.get("count") if isinstance(result, dict) else None
        if type(count) is not int or count < 0:
            raise SearchIndexUnavailable("Search index returned an invalid count")
        return count

    async def get_point(self, collection: str, point_id: str) -> SearchUnitHit | None:
        path = f"/collections/{quote(collection, safe='')}/points/{quote(point_id, safe='')}"
        data = await self._request("GET", path, params={"with_payload": "true", "with_vector": "false"})
        point = data.get("result")
        if point is None:
            return None
        if not isinstance(point, dict) or not isinstance(point.get("payload"), dict):
            raise SearchIndexUnavailable("Search index returned an invalid point")
        try:
            score = float(point.get("score") or 0.0)
        except (TypeError, ValueError) as error:
            raise SearchIndexUnavailable("Search index returned an invalid point") from error
        if not math.isfinite(score):
            raise SearchIndexUnavailable("Search index returned an invalid point")
        return SearchUnitHit(str(point.get("id") or point_id), score, point["payload"])

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.InvalidURL:
                raise SearchIndexUnavailable("Search index request configuration is invalid") from None
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt < self._max_retries:
                    await asyncio.sleep(0.05 * (attempt + 1))
                    continue
                raise SearchIndexUnavailable("Search index is temporarily unavailable") from None
            except httpx.RequestError:
                raise SearchIndexUnavailable("Search index request failed") from None
            if response.status_code in _TRANSIENT_STATUS_CODES and attempt < self._max_retries:
                await asyncio.sleep(0.05 * (attempt + 1))
                continue
            if response.is_error:
                raise SearchIndexUnavailable("Search index query failed")
            try:
                data = response.json()
            except ValueError as error:
                raise SearchIndexUnavailable("Search index returned an invalid response") from error
            if not isinstance(data, dict):
                raise SearchIndexUnavailable("Search index returned an invalid response")
            return data
        raise SearchIndexUnavailable("Search index is temporarily unavailable")
