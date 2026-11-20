"""Unified token metering, per-component/per-file attribution, and cost estimation for SAG.

Tracks exact provider-reported token counts (with deterministic tiktoken fallback only when an
upstream gateway omits ``usage``) across all SAG stages:
  - ``search_index_embedding``: Phase 2C SearchUnit + CanonicalBlock batch vectorization
  - ``knowledge_foundation_embedding``: Phase 5A Foundation KnowledgeUnit dense embedding
  - ``knowledge_e2_enrichment``: Historical Phase 5B E2 bucket (removed from active pipeline; kept for legacy DB rows)
  - ``legacy_event_extraction``: Optional legacy event extraction token usage
  - ``decision_router``: Pre-retrieve SystemOne / Kev-4b decision routing
  - ``query_embedding``: Search/RAG query vectorization
  - ``answer_generation``: Grounded RAG answer synthesis (streaming & non-streaming)
  - ``agent_turn`` / ``llm_completion``: Agent tool-calling turns and auxiliary LLM calls
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# Default serverless pricing per 1,000,000 tokens in USD (SiliconFlow / OpenAI-compatible).
# Operators can override or extend entries via SAG_TOKEN_PRICING_JSON.
DEFAULT_MODEL_PRICING_PER_1M_USD: dict[str, dict[str, float]] = {
    "qwen/qwen3-embedding-0.6b": {"embedding_input_per_1m": 0.01},
    "qwen/qwen3-embedding-4b": {"embedding_input_per_1m": 0.02},
    "qwen/qwen3-embedding-8b": {"embedding_input_per_1m": 0.04},
    "baai/bge-m3": {"embedding_input_per_1m": 0.01},
    "baai/bge-large-en-v1.5": {"embedding_input_per_1m": 0.01},
    "baai/bge-large-zh-v1.5": {"embedding_input_per_1m": 0.01},
    "text-embedding-3-small": {"embedding_input_per_1m": 0.02},
    "text-embedding-3-large": {"embedding_input_per_1m": 0.13},
    "kev-4b": {
        "llm_input_per_1m": 0.04,
        "llm_cached_input_per_1m": 0.04,
        "llm_output_per_1m": 0.04,
    },
    "qwen/qwen3-8b": {
        "llm_input_per_1m": 0.06,
        "llm_cached_input_per_1m": 0.06,
        "llm_output_per_1m": 0.06,
    },
    "qwen/qwen3-14b": {
        "llm_input_per_1m": 0.14,
        "llm_cached_input_per_1m": 0.14,
        "llm_output_per_1m": 0.14,
    },
    "qwen/qwen3-32b": {
        "llm_input_per_1m": 0.29,
        "llm_cached_input_per_1m": 0.29,
        "llm_output_per_1m": 0.29,
    },
    "deepseek-ai/deepseek-v3.2": {
        "llm_input_per_1m": 0.27,
        "llm_cached_input_per_1m": 0.07,
        "llm_output_per_1m": 0.42,
    },
    "deepseek-ai/deepseek-v3": {
        "llm_input_per_1m": 0.27,
        "llm_cached_input_per_1m": 0.07,
        "llm_output_per_1m": 0.42,
    },
}


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_model_key(model: str | None) -> str:
    raw = (model or "unknown").strip().casefold()
    if raw.startswith("openai/"):
        raw = raw[len("openai/") :]
    return raw or "unknown"


def _canonical_model_label(model: str | None) -> str:
    raw = (model or "unknown").strip()
    if raw.casefold().startswith("openai/"):
        raw = raw[len("openai/") :]
    return raw or "unknown"


def get_model_pricing(model: str | None) -> dict[str, float]:
    key = _normalize_model_key(model)
    table = dict(DEFAULT_MODEL_PRICING_PER_1M_USD)
    override_raw = os.getenv("SAG_TOKEN_PRICING_JSON", "").strip()
    if override_raw:
        try:
            parsed = json.loads(override_raw)
            if isinstance(parsed, Mapping):
                for k, v in parsed.items():
                    if isinstance(v, Mapping):
                        table[_normalize_model_key(str(k))] = {
                            str(pk): float(pv) for pk, pv in v.items() if isinstance(pv, (int, float))
                        }
        except (ValueError, TypeError):
            pass
    if key in table:
        return table[key]
    for candidate_key, rates in table.items():
        if key.endswith("/" + candidate_key) or candidate_key.endswith("/" + key):
            return rates
    return {}


def compute_bucket_cost_usd(model: str | None, bucket: Mapping[str, Any]) -> float:
    rates = get_model_pricing(model)
    if not rates:
        return 0.0
    emb_in = int(bucket.get("embedding_input_tokens", 0) or 0)
    llm_in = int(bucket.get("llm_input_tokens", 0) or 0)
    llm_cached = min(llm_in, int(bucket.get("llm_cached_input_tokens", 0) or 0))
    llm_uncached = max(0, llm_in - llm_cached)
    llm_out = int(bucket.get("llm_output_tokens", 0) or 0)

    emb_rate = float(rates.get("embedding_input_per_1m", 0.0))
    in_rate = float(rates.get("llm_input_per_1m", 0.0))
    cached_rate = float(rates.get("llm_cached_input_per_1m", in_rate))
    out_rate = float(rates.get("llm_output_per_1m", 0.0))

    cost = (
        emb_in * emb_rate
        + llm_uncached * in_rate
        + llm_cached * cached_rate
        + llm_out * out_rate
    ) / 1_000_000.0
    return round(cost, 8)


@lru_cache(maxsize=1)
def _tiktoken_encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001
        return None


def estimate_text_tokens(texts: Sequence[str] | str | None) -> int:
    if not texts:
        return 0
    items = [texts] if isinstance(texts, str) else [t for t in texts if t]
    if not items:
        return 0
    enc = _tiktoken_encoding()
    if enc is not None:
        return sum(len(enc.encode(t)) for t in items)
    return sum(max(1, len(t.encode("utf-8")) // 4) for t in items)


def extract_embedding_usage(raw_usage: Any, texts: Sequence[str] | str | None = None) -> dict[str, Any]:
    prompt_tokens = int(_attr(raw_usage, "prompt_tokens", 0) or _attr(raw_usage, "total_tokens", 0) or 0)
    if prompt_tokens > 0:
        return {
            "embedding_input_tokens": prompt_tokens,
            "total_tokens": prompt_tokens,
            "usage_source": "provider",
        }
    est = estimate_text_tokens(texts)
    return {
        "embedding_input_tokens": est,
        "total_tokens": est,
        "usage_source": "estimated",
    }


def extract_chat_usage(
    raw_usage: Any,
    messages: Sequence[Mapping[str, Any]] | None = None,
    completion_text: str = "",
) -> dict[str, Any]:
    if raw_usage is not None:
        prompt_tokens = int(_attr(raw_usage, "prompt_tokens", 0) or _attr(raw_usage, "input_tokens", 0) or 0)
        completion_tokens = int(
            _attr(raw_usage, "completion_tokens", 0) or _attr(raw_usage, "output_tokens", 0) or 0
        )
        total_tokens = int(_attr(raw_usage, "total_tokens", 0) or (prompt_tokens + completion_tokens))
        prompt_details = _attr(raw_usage, "prompt_tokens_details")
        completion_details = _attr(raw_usage, "completion_tokens_details")
        cached_tokens = int(
            _attr(prompt_details, "cached_tokens", 0)
            or _attr(raw_usage, "prompt_cache_hit_tokens", 0)
            or _attr(raw_usage, "cached_tokens", 0)
            or 0
        )
        reasoning_tokens = int(
            _attr(completion_details, "reasoning_tokens", 0)
            or _attr(raw_usage, "reasoning_tokens", 0)
            or 0
        )
        if prompt_tokens > 0 or completion_tokens > 0 or total_tokens > 0:
            if total_tokens <= 0:
                total_tokens = prompt_tokens + completion_tokens
            return {
                "llm_input_tokens": prompt_tokens,
                "llm_cached_input_tokens": min(prompt_tokens, cached_tokens),
                "llm_uncached_input_tokens": max(0, prompt_tokens - min(prompt_tokens, cached_tokens)),
                "llm_output_tokens": completion_tokens,
                "llm_reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
                "usage_source": "provider",
            }

    msg_texts: list[str] = []
    for m in messages or ():
        content = m.get("content") if isinstance(m, Mapping) else getattr(m, "content", None)
        if isinstance(content, str):
            msg_texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    msg_texts.append(part["text"])
    est_in = estimate_text_tokens(msg_texts) + (4 * len(messages) if messages else 0)
    est_out = estimate_text_tokens(completion_text)
    return {
        "llm_input_tokens": est_in,
        "llm_cached_input_tokens": 0,
        "llm_uncached_input_tokens": est_in,
        "llm_output_tokens": est_out,
        "llm_reasoning_tokens": 0,
        "total_tokens": est_in + est_out,
        "usage_source": "estimated",
    }


def empty_token_bucket() -> dict[str, Any]:
    return {
        "embedding_input_tokens": 0,
        "llm_input_tokens": 0,
        "llm_cached_input_tokens": 0,
        "llm_uncached_input_tokens": 0,
        "llm_output_tokens": 0,
        "llm_reasoning_tokens": 0,
        "total_tokens": 0,
        "embedding_calls": 0,
        "llm_calls": 0,
        "cache_hits": 0,
        "estimated_calls": 0,
        "cost_usd": 0.0,
    }


def _accumulate_bucket(
    target: dict[str, Any],
    delta: Mapping[str, Any],
    *,
    model: str | None = None,
) -> None:
    emb_in = int(delta.get("embedding_input_tokens", 0) or 0)
    llm_in = int(delta.get("llm_input_tokens", 0) or 0)
    llm_cached = min(llm_in, int(delta.get("llm_cached_input_tokens", 0) or 0))
    llm_uncached = max(0, llm_in - llm_cached)
    llm_out = int(delta.get("llm_output_tokens", 0) or 0)
    llm_reason = int(delta.get("llm_reasoning_tokens", 0) or 0)
    total = int(delta.get("total_tokens", 0) or (emb_in + llm_in + llm_out))

    target["embedding_input_tokens"] = int(target.get("embedding_input_tokens", 0)) + emb_in
    target["llm_input_tokens"] = int(target.get("llm_input_tokens", 0)) + llm_in
    target["llm_cached_input_tokens"] = int(target.get("llm_cached_input_tokens", 0)) + llm_cached
    target["llm_uncached_input_tokens"] = int(target.get("llm_uncached_input_tokens", 0)) + llm_uncached
    target["llm_output_tokens"] = int(target.get("llm_output_tokens", 0)) + llm_out
    target["llm_reasoning_tokens"] = int(target.get("llm_reasoning_tokens", 0)) + llm_reason
    target["total_tokens"] = int(target.get("total_tokens", 0)) + total
    target["embedding_calls"] = int(target.get("embedding_calls", 0)) + int(delta.get("embedding_calls", 0) or 0)
    target["llm_calls"] = int(target.get("llm_calls", 0)) + int(delta.get("llm_calls", 0) or 0)
    target["cache_hits"] = int(target.get("cache_hits", 0)) + int(delta.get("cache_hits", 0) or 0)
    target["estimated_calls"] = int(target.get("estimated_calls", 0)) + int(delta.get("estimated_calls", 0) or 0)

    if model is not None:
        delta_cost = compute_bucket_cost_usd(
            model,
            {
                "embedding_input_tokens": emb_in,
                "llm_input_tokens": llm_in,
                "llm_cached_input_tokens": llm_cached,
                "llm_output_tokens": llm_out,
            },
        )
    else:
        delta_cost = float(delta.get("cost_usd", 0.0) or 0.0)
    target["cost_usd"] = round(float(target.get("cost_usd", 0.0)) + delta_cost, 8)


class TokenLedger:
    """Thread-safe hierarchical token accumulator by token type, component, and model."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: dict[str, Any] = empty_token_bucket()
        self._by_component: dict[str, dict[str, Any]] = {}
        self._by_model: dict[str, dict[str, Any]] = {}
        self._metadata: dict[str, Any] = {}

    def set_metadata(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if v is not None:
                    self._metadata[k] = v

    def record_event(
        self,
        *,
        component: str,
        model: str | None,
        delta: Mapping[str, Any],
    ) -> None:
        model_label = _canonical_model_label(model)
        comp_key = (component or "unspecified").strip() or "unspecified"
        with self._lock:
            _accumulate_bucket(self._totals, delta, model=model_label)
            comp_bucket = self._by_component.setdefault(comp_key, empty_token_bucket())
            _accumulate_bucket(comp_bucket, delta, model=model_label)
            if model_label != "unknown" or int(delta.get("total_tokens", 0) or 0) > 0:
                mod_bucket = self._by_model.setdefault(model_label, empty_token_bucket())
                _accumulate_bucket(mod_bucket, delta, model=model_label)

    def merge_summary(self, summary: Mapping[str, Any] | None) -> None:
        if not summary or not isinstance(summary, Mapping):
            return
        by_comp = summary.get("by_component")
        by_mod = summary.get("by_model")
        totals = summary.get("totals") or summary.get("by_token_type")
        with self._lock:
            if isinstance(totals, Mapping):
                _accumulate_bucket(self._totals, totals)
            if isinstance(by_comp, Mapping):
                for comp_key, bucket in by_comp.items():
                    if isinstance(bucket, Mapping):
                        comp_target = self._by_component.setdefault(str(comp_key), empty_token_bucket())
                        _accumulate_bucket(comp_target, bucket)
            if isinstance(by_mod, Mapping):
                for mod_key, bucket in by_mod.items():
                    if isinstance(bucket, Mapping):
                        mod_target = self._by_model.setdefault(str(mod_key), empty_token_bucket())
                        _accumulate_bucket(mod_target, bucket)
            meta = summary.get("metadata")
            if isinstance(meta, Mapping):
                for k, v in meta.items():
                    if v is not None:
                        self._metadata[str(k)] = v

    def to_summary(self) -> dict[str, Any]:
        with self._lock:
            totals = dict(self._totals)
            by_comp = {k: dict(v) for k, v in sorted(self._by_component.items())}
            by_mod = {k: dict(v) for k, v in sorted(self._by_model.items())}
            meta = dict(self._metadata)
        out: dict[str, Any] = {
            "totals": totals,
            "by_token_type": {
                "embedding_input_tokens": totals["embedding_input_tokens"],
                "llm_input_tokens": totals["llm_input_tokens"],
                "llm_cached_input_tokens": totals["llm_cached_input_tokens"],
                "llm_uncached_input_tokens": totals["llm_uncached_input_tokens"],
                "llm_output_tokens": totals["llm_output_tokens"],
                "llm_reasoning_tokens": totals["llm_reasoning_tokens"],
                "total_tokens": totals["total_tokens"],
            },
            "by_component": by_comp,
            "by_model": by_mod,
            "cost_usd": totals["cost_usd"],
        }
        if meta:
            out["metadata"] = meta
        return out


def merge_token_summaries(*summaries: Mapping[str, Any] | None) -> dict[str, Any]:
    ledger = TokenLedger()
    for s in summaries:
        ledger.merge_summary(s)
    return ledger.to_summary()


@dataclass
class _ScopeFrame:
    ledger: TokenLedger = field(default_factory=TokenLedger)
    component: str | None = None
    project_id: str | None = None
    tenant_id: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None


_ACTIVE_SCOPES: contextvars.ContextVar[tuple[_ScopeFrame, ...]] = contextvars.ContextVar(
    "sag_token_usage_scopes",
    default=(),
)


class RuntimeTokenRegistry:
    """In-process aggregate token registry partitioned globally and per project."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._global = TokenLedger()
        self._by_project: dict[str, TokenLedger] = {}

    def record(
        self,
        *,
        component: str,
        model: str | None,
        delta: Mapping[str, Any],
        project_id: str | None = None,
    ) -> None:
        self._global.record_event(component=component, model=model, delta=delta)
        if project_id:
            with self._lock:
                proj_ledger = self._by_project.get(project_id)
                if proj_ledger is None:
                    proj_ledger = TokenLedger()
                    self._by_project[project_id] = proj_ledger
            proj_ledger.record_event(component=component, model=model, delta=delta)

    def get_global_summary(self) -> dict[str, Any]:
        return self._global.to_summary()

    def get_project_summary(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            proj_ledger = self._by_project.get(project_id)
        return proj_ledger.to_summary() if proj_ledger is not None else TokenLedger().to_summary()

    def reset(self, project_id: str | None = None) -> None:
        with self._lock:
            if project_id is None:
                self._global = TokenLedger()
                self._by_project.clear()
            else:
                self._by_project.pop(project_id, None)


RUNTIME_TOKEN_REGISTRY = RuntimeTokenRegistry()


def reset_runtime_token_usage(project_id: str | None = None) -> None:
    RUNTIME_TOKEN_REGISTRY.reset(project_id=project_id)


def get_runtime_token_usage(project_id: str | None = None) -> dict[str, Any]:
    if project_id is not None:
        return RUNTIME_TOKEN_REGISTRY.get_project_summary(project_id)
    return RUNTIME_TOKEN_REGISTRY.get_global_summary()


@contextmanager
def token_usage_scope(
    *,
    component: str | None = None,
    project_id: str | None = None,
    tenant_id: str | None = None,
    document_id: str | None = None,
    document_version_id: str | None = None,
) -> Iterator[TokenLedger]:
    stack = _ACTIVE_SCOPES.get()
    parent = stack[-1] if stack else None
    frame = _ScopeFrame(
        component=component or (parent.component if parent else None),
        project_id=project_id or (parent.project_id if parent else None),
        tenant_id=tenant_id or (parent.tenant_id if parent else None),
        document_id=document_id or (parent.document_id if parent else None),
        document_version_id=document_version_id or (parent.document_version_id if parent else None),
    )
    token = _ACTIVE_SCOPES.set((*stack, frame))
    try:
        yield frame.ledger
    finally:
        _ACTIVE_SCOPES.reset(token)


def _dispatch_event(
    *,
    default_component: str,
    model: str | None,
    delta: Mapping[str, Any],
    component: str | None = None,
    project_id: str | None = None,
) -> None:
    stack = _ACTIVE_SCOPES.get()
    resolved_comp = component
    resolved_proj = project_id
    if stack:
        for frame in reversed(stack):
            if not resolved_comp and frame.component and frame.component != "search_request":
                resolved_comp = frame.component
            if not resolved_proj and frame.project_id:
                resolved_proj = frame.project_id
    final_comp = resolved_comp or default_component
    for frame in stack:
        frame.ledger.record_event(component=final_comp, model=model, delta=delta)
    RUNTIME_TOKEN_REGISTRY.record(
        component=final_comp,
        model=model,
        delta=delta,
        project_id=resolved_proj,
    )


def record_embedding_usage(
    *,
    model: str | None,
    usage: Mapping[str, Any],
    component: str | None = None,
    project_id: str | None = None,
    calls: int = 1,
) -> None:
    emb_tokens = int(usage.get("embedding_input_tokens", 0) or usage.get("total_tokens", 0) or 0)
    is_est = 1 if usage.get("usage_source") == "estimated" else 0
    _dispatch_event(
        default_component="embedding",
        model=model,
        component=component,
        project_id=project_id,
        delta={
            "embedding_input_tokens": emb_tokens,
            "total_tokens": emb_tokens,
            "embedding_calls": calls,
            "estimated_calls": is_est,
        },
    )


def record_embedding_cache_hit(
    *,
    model: str | None,
    count: int = 1,
    component: str | None = None,
    project_id: str | None = None,
) -> None:
    if count <= 0:
        return
    _dispatch_event(
        default_component="embedding",
        model=model,
        component=component,
        project_id=project_id,
        delta={"cache_hits": count},
    )


def wrap_embedding_client_with_cache_and_metering(emb: Any, *, default_model: str | None = None) -> None:
    """Idempotently attach shared text-embedding cache and exact provider token metering."""
    if emb is None or getattr(emb, "_sag_token_metered_cache_wrapped", False):
        return
    from sag_api.services.search_index_service import (
        get_cached_text_embedding,
        put_cached_text_embedding,
    )

    emb_model = getattr(emb, "model", None) or default_model
    raw_client = emb if hasattr(emb, "_create_embeddings") else None
    if raw_client is None:
        for attr_name in ("inner", "_inner", "client", "_client", "_delegate"):
            cand = getattr(emb, attr_name, None)
            if cand is not None and hasattr(cand, "_create_embeddings"):
                raw_client = cand
                break

    if raw_client is not None and not getattr(raw_client, "_sag_token_usage_wrapped", False):
        orig_create_embeddings = raw_client._create_embeddings

        async def _metered_create_embeddings(input_data: str | list[str], *, label: str) -> Any:
            response = await orig_create_embeddings(input_data, label=label)
            usage = extract_embedding_usage(getattr(response, "usage", None), input_data)
            record_embedding_usage(
                model=getattr(raw_client, "model", None) or emb_model,
                usage=usage,
            )
            return response

        raw_client._create_embeddings = _metered_create_embeddings
        raw_client._sag_token_usage_wrapped = True

    # If EngineManager already wrapped emb with _sag_shared_cache_wrapped earlier, unwrap or wrap cleanly:
    orig_generate = getattr(emb, "_sag_orig_generate", emb.generate)
    orig_batch_generate = getattr(emb, "_sag_orig_batch_generate", emb.batch_generate)
    emb._sag_orig_generate = orig_generate
    emb._sag_orig_batch_generate = orig_batch_generate

    async def _cached_generate(text: str) -> list[float]:
        cached = get_cached_text_embedding(text)
        if cached is not None:
            record_embedding_cache_hit(model=emb_model, count=1)
            return cached
        vec = await orig_generate(text)
        if raw_client is None:
            record_embedding_usage(
                model=emb_model,
                usage=extract_embedding_usage(None, text),
            )
        if vec:
            put_cached_text_embedding(text, vec)
        return vec

    async def _cached_batch_generate(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float] | None] = [None] * len(texts)
        missing_by_text: dict[str, list[int]] = {}
        cache_hits = 0
        for idx, txt in enumerate(texts):
            cached = get_cached_text_embedding(txt)
            if cached is not None:
                results[idx] = cached
                cache_hits += 1
            else:
                missing_by_text.setdefault(txt, []).append(idx)
        if cache_hits:
            record_embedding_cache_hit(model=emb_model, count=cache_hits)
        if missing_by_text:
            unique_missing = list(missing_by_text.keys())
            fetched = await orig_batch_generate(unique_missing)
            if raw_client is None:
                record_embedding_usage(
                    model=emb_model,
                    usage=extract_embedding_usage(None, unique_missing),
                )
            for txt, vec in zip(unique_missing, fetched, strict=True):
                if vec:
                    put_cached_text_embedding(txt, vec)
                for m_idx in missing_by_text[txt]:
                    results[m_idx] = vec
        return [vec or [] for vec in results]

    emb.generate = _cached_generate
    emb.batch_generate = _cached_batch_generate
    emb._sag_shared_cache_wrapped = True
    emb._sag_token_metered_cache_wrapped = True


def record_llm_usage(
    *,
    model: str | None,
    usage: Mapping[str, Any],
    component: str | None = None,
    project_id: str | None = None,
    calls: int = 1,
) -> None:
    llm_in = int(usage.get("llm_input_tokens", 0) or 0)
    llm_cached = min(llm_in, int(usage.get("llm_cached_input_tokens", 0) or 0))
    llm_out = int(usage.get("llm_output_tokens", 0) or 0)
    llm_reason = int(usage.get("llm_reasoning_tokens", 0) or 0)
    total = int(usage.get("total_tokens", 0) or (llm_in + llm_out))
    is_est = 1 if usage.get("usage_source") == "estimated" else 0
    _dispatch_event(
        default_component="llm_completion",
        model=model,
        component=component,
        project_id=project_id,
        delta={
            "llm_input_tokens": llm_in,
            "llm_cached_input_tokens": llm_cached,
            "llm_uncached_input_tokens": max(0, llm_in - llm_cached),
            "llm_output_tokens": llm_out,
            "llm_reasoning_tokens": llm_reason,
            "total_tokens": total,
            "llm_calls": calls,
            "estimated_calls": is_est,
        },
    )


async def sync_document_token_usage(session: Any, document_version_id: str) -> dict[str, Any]:
    """Deterministically rollup all persisted stage & KnowledgeJob token usage for one DocumentVersion."""
    from sqlalchemy import select, update

    from sag_api.db.models import Document, DocumentVersion, KnowledgeJob

    version = await session.get(DocumentVersion, document_version_id)
    if version is None:
        return TokenLedger().to_summary()

    meta = dict(version.metadata_json or {})
    file_ledger = TokenLedger()
    if isinstance(meta.get("search_index_token_usage"), Mapping):
        file_ledger.merge_summary(meta["search_index_token_usage"])
    if isinstance(meta.get("legacy_extraction_token_usage"), Mapping):
        file_ledger.merge_summary(meta["legacy_extraction_token_usage"])

    job_usages = (
        await session.scalars(
            select(KnowledgeJob.usage_json).where(
                KnowledgeJob.document_version_id == document_version_id,
                KnowledgeJob.usage_json.is_not(None),
            )
        )
    ).all()
    for ju in job_usages:
        if isinstance(ju, Mapping):
            file_ledger.merge_summary(ju)

    summary = file_ledger.to_summary()
    meta["token_usage"] = summary
    version.metadata_json = meta
    session.add(version)

    total_tokens = int(summary["totals"]["total_tokens"])
    await session.execute(
        update(Document)
        .where(Document.id == version.document_id)
        .values(
            token_usage=total_tokens,
            token_usage_breakdown=summary,
        )
        .execution_options(synchronize_session=False)
    )
    return summary


async def build_project_token_usage_report(
    session: Any,
    *,
    tenant_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Build a complete per-file, per-component, per-token-type, and project-wide token report."""
    from sqlalchemy import func, select

    from sag_api.db.models import Document, DocumentVersion, KnowledgeJob, KnowledgeUnit, SearchUnit

    docs = (
        await session.scalars(
            select(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.project_id == project_id,
                Document.is_active.is_(True),
            )
            .order_by(Document.logical_source_id, Document.filename, Document.id)
        )
    ).all()

    doc_ids = [d.id for d in docs]
    versions_by_doc: dict[str, DocumentVersion] = {}
    if doc_ids:
        versions = (
            await session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id.in_(doc_ids))
                .order_by(DocumentVersion.version_no)
            )
        ).all()
        for v in versions:
            versions_by_doc[v.document_id] = v

    ver_ids = [v.id for v in versions_by_doc.values()]
    su_counts: dict[str, int] = {}
    ku_stats: dict[str, dict[str, int]] = {}
    jobs_by_ver: dict[str, list[dict[str, Any]]] = {}

    if ver_ids:
        for vid, cnt in (
            await session.execute(
                select(SearchUnit.document_version_id, func.count(SearchUnit.id))
                .where(SearchUnit.document_version_id.in_(ver_ids))
                .group_by(SearchUnit.document_version_id)
            )
        ).all():
            su_counts[str(vid)] = int(cnt)

        ku_rows = (
            await session.execute(
                select(KnowledgeUnit.document_version_id, KnowledgeUnit.features_json).where(
                    KnowledgeUnit.document_version_id.in_(ver_ids),
                    KnowledgeUnit.is_current.is_(True),
                )
            )
        ).all()
        for vid, feat in ku_rows:
            st = ku_stats.setdefault(str(vid), {"total": 0, "e2_enriched": 0, "e2_pending": 0})
            st["total"] += 1
            fdict = feat or {}
            if fdict.get("e2_extractor_version"):
                st["e2_enriched"] += 1
            if fdict.get("e2_reason"):
                st["e2_pending"] += 1

        kjob_rows = (
            await session.execute(
                select(
                    KnowledgeJob.document_version_id,
                    KnowledgeJob.kind,
                    KnowledgeJob.status,
                    KnowledgeJob.attempts,
                    KnowledgeJob.usage_json,
                ).where(KnowledgeJob.document_version_id.in_(ver_ids))
            )
        ).all()
        for vid, kind, status, attempts, ujson in kjob_rows:
            jobs_by_ver.setdefault(str(vid), []).append(
                {
                    "kind": kind,
                    "status": status,
                    "attempts": int(attempts or 0),
                    "usage_json": ujson,
                }
            )

    ingestion_ledger = TokenLedger()
    per_file: list[dict[str, Any]] = []

    for doc in docs:
        ver = versions_by_doc.get(doc.id)
        file_ledger = TokenLedger()
        if ver is not None:
            meta = ver.metadata_json or {}
            if isinstance(meta.get("search_index_token_usage"), Mapping):
                file_ledger.merge_summary(meta["search_index_token_usage"])
            if isinstance(meta.get("legacy_extraction_token_usage"), Mapping):
                file_ledger.merge_summary(meta["legacy_extraction_token_usage"])
            for j in jobs_by_ver.get(ver.id, ()):
                if isinstance(j["usage_json"], Mapping):
                    file_ledger.merge_summary(j["usage_json"])
        elif isinstance(doc.token_usage_breakdown, Mapping):
            file_ledger.merge_summary(doc.token_usage_breakdown)

        fsummary = file_ledger.to_summary()
        ingestion_ledger.merge_summary(fsummary)
        vid = ver.id if ver is not None else None
        kst = ku_stats.get(vid or "", {"total": 0, "e2_enriched": 0, "e2_pending": 0})
        vjobs = jobs_by_ver.get(vid or "", [])
        e2_jobs_total = sum(1 for j in vjobs if j["kind"] == "E2")
        e2_jobs_succeeded = sum(1 for j in vjobs if j["kind"] == "E2" and j["status"] == "SUCCEEDED")

        per_file.append(
            {
                "document_id": doc.id,
                "document_version_id": vid,
                "filename": doc.filename,
                "logical_source_id": doc.logical_source_id or doc.filename,
                "size_bytes": int(doc.size_bytes or 0),
                "search_units": su_counts.get(vid or "", int(doc.chunk_count or 0)),
                "knowledge_units": kst["total"],
                "e2_units_selected": kst["e2_enriched"] + kst["e2_pending"],
                "e2_units_enriched": kst["e2_enriched"],
                "e2_jobs_total": e2_jobs_total,
                "e2_jobs_succeeded": e2_jobs_succeeded,
                "by_token_type": fsummary["by_token_type"],
                "by_component": fsummary["by_component"],
                "by_model": fsummary["by_model"],
                "totals": fsummary["totals"],
                "cost_usd": fsummary["cost_usd"],
                "metadata": fsummary.get("metadata", {}),
            }
        )

    ingestion_summary = ingestion_ledger.to_summary()
    runtime_proj = RUNTIME_TOKEN_REGISTRY.get_project_summary(project_id)

    # Extract query/generation-only runtime components so we never double-count ingestion/E2
    # events that were already durably reconstructed from PostgreSQL per file.
    file_stage_names = {
        "search_index_embedding",
        "knowledge_foundation_embedding",
        "knowledge_e2_enrichment",
        "legacy_event_extraction",
    }
    query_ledger = TokenLedger()
    runtime_by_comp = runtime_proj.get("by_component", {})
    runtime_by_model = runtime_proj.get("by_model", {})
    # Reconstruct query_ledger from non-file components in RUNTIME_TOKEN_REGISTRY
    for comp_name, bucket in runtime_by_comp.items():
        if comp_name in file_stage_names:
            continue
        query_ledger.merge_summary(
            {
                "totals": bucket,
                "by_component": {comp_name: bucket},
            }
        )
    # Attach exact non-ingestion model deltas (= runtime_by_model - ingestion_by_model floored at 0)
    query_by_model: dict[str, dict[str, Any]] = {}
    ing_by_model = ingestion_summary.get("by_model", {})
    for mod_name, rbucket in runtime_by_model.items():
        ibucket = ing_by_model.get(mod_name, {})
        diff_bucket = empty_token_bucket()
        has_any = False
        for key in (
            "embedding_input_tokens",
            "llm_input_tokens",
            "llm_cached_input_tokens",
            "llm_uncached_input_tokens",
            "llm_output_tokens",
            "llm_reasoning_tokens",
            "total_tokens",
            "embedding_calls",
            "llm_calls",
            "cache_hits",
            "estimated_calls",
        ):
            val = max(0, int(rbucket.get(key, 0) or 0) - int(ibucket.get(key, 0) or 0))
            diff_bucket[key] = val
            if val > 0:
                has_any = True
        if has_any:
            diff_bucket["cost_usd"] = compute_bucket_cost_usd(mod_name, diff_bucket)
            query_by_model[mod_name] = diff_bucket

    query_summary = query_ledger.to_summary()
    if query_by_model:
        query_summary["by_model"] = query_by_model

    grand_total = merge_token_summaries(ingestion_summary, query_summary)

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "file_count": len(per_file),
        "pricing_per_1m_usd": {
            mod: get_model_pricing(mod) for mod in sorted(grand_total.get("by_model", {}))
        },
        "ingestion_and_knowledge": ingestion_summary,
        "query_and_generation": query_summary,
        "grand_total": grand_total,
        "files": per_file,
    }
