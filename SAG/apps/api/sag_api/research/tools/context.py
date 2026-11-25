"""Immutable execution context & concurrency-safe evidence ledger for external research tools.

Concurrency invariant:
- Each external tool call opens its OWN short-lived read-only `AsyncSession` via
  `session_factory()` so `sag_agent.ToolExecutionMode.PARALLEL` (`asyncio.gather`) NEVER
  shares a single SQLAlchemy `AsyncSession` across concurrent coroutines.
- `as_of_date` and `principal` live strictly on `ResearchToolContext` (never exposed as
  parameters in any LLM tool JSON schema), preventing prompt-injection PIT or ACL escapes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.research.financial_metrics_store import CoreMetricsReaderProtocol
from sag_api.research.kg_store import KGStoreProtocol
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.services.reranker_client import CrossEncoderRerankerClient

SessionFactoryFn = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class ResearchEvidenceLedger:
    """Async-safe evidence accumulator updated by external research tools during a turn."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sections: list[Any] = field(default_factory=list)
    canonical_blocks: dict[str, list[Any]] = field(default_factory=dict)
    _section_keys: dict[tuple[str, int, str, str], str] = field(default_factory=dict)
    hydrated_tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    screening_rows: list[dict[str, Any]] = field(default_factory=list)
    screening_criteria: str = ""
    graph_hops: list[dict[str, Any]] = field(default_factory=list)
    _graph_hop_keys: set[tuple[str, str, str, str]] = field(default_factory=set)
    calculator_traces: list[dict[str, Any]] = field(default_factory=list)
    tool_traces: list[dict[str, Any]] = field(default_factory=list)
    crag_grades: list[str] = field(default_factory=list)
    reranker_provider: str | None = None
    reranker_model: str | None = None
    touched_tickers: list[str] = field(default_factory=list)
    unknown_requested_tickers: list[str] = field(default_factory=list)
    sub_queries: list[str] = field(default_factory=list)
    hipporag_hops: int = 0
    recalled_memories: dict[str, dict[str, Any]] = field(default_factory=dict)
    portfolio_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    linked_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _record_ticker_unlocked(self, ticker: str) -> None:
        sym = str(ticker or "").strip().upper()
        if sym and sym not in self.touched_tickers:
            self.touched_tickers.append(sym)

    async def register_sections(
        self,
        new_sections: Sequence[Any],
        *,
        canonical_blocks: Mapping[str, Sequence[Any]] | None = None,
        crag_grade: str | None = None,
        reranker_provider: str | None = None,
        reranker_model: str | None = None,
        queries: Sequence[str] = (),
        hipporag_hops: int = 0,
    ) -> list[tuple[str, Any]]:
        """Deduplicate sections and assign monotonic `[C1]`, `[C2]`... citation IDs."""
        assigned: list[tuple[str, Any]] = []
        async with self.lock:
            if canonical_blocks:
                for ver_id, blks in canonical_blocks.items():
                    if ver_id and blks:
                        self.canonical_blocks.setdefault(str(ver_id), list(blks))
            if crag_grade:
                self.crag_grades.append(crag_grade)
            if reranker_provider:
                self.reranker_provider = reranker_provider
            if reranker_model:
                self.reranker_model = reranker_model
            self.hipporag_hops += max(0, int(hipporag_hops or 0))
            for q in queries:
                clean_q = str(q or "").strip()
                if clean_q and clean_q not in self.sub_queries:
                    self.sub_queries.append(clean_q)

            for sec in new_sections:
                vid = str(getattr(sec, "document_version_id", "") or "")
                pfrom = int(getattr(sec, "page_from", 1) or 1)
                tid = str(getattr(sec, "table_id", "") or "")
                head_sig = str(getattr(sec, "content", "") or "").strip()[:120]
                key = (vid, pfrom, tid, head_sig)
                existing_cid = self._section_keys.get(key)
                if existing_cid is not None:
                    assigned.append((existing_cid, sec))
                    continue
                self.sections.append(sec)
                cid = f"C{len(self.sections)}"
                self._section_keys[key] = cid
                self._record_ticker_unlocked(getattr(sec, "ticker", ""))
                assigned.append((cid, sec))
        return assigned

    async def register_tables(self, tables: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async with self.lock:
            for tbl in tables:
                tid = str(tbl.get("table_id") or "").strip()
                if not tid:
                    continue
                entry = dict(tbl)
                self.hydrated_tables[tid] = entry
                if entry.get("project_id"):
                    self._record_ticker_unlocked(str(entry["project_id"]))
                out.append(entry)
        return out

    async def register_screening(self, rows: Sequence[Mapping[str, Any]], criteria: str) -> None:
        async with self.lock:
            self.screening_rows = [dict(r) for r in rows]
            self.screening_criteria = criteria
            for r in rows:
                if r.get("ticker"):
                    self._record_ticker_unlocked(str(r["ticker"]))

    async def register_graph_hops(self, hops: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        added: list[dict[str, Any]] = []
        async with self.lock:
            for hop in hops:
                key = (
                    str(hop.get("layer") or ""),
                    str(hop.get("src_label") or ""),
                    str(hop.get("relation_type") or ""),
                    str(hop.get("dst_label") or ""),
                )
                if key in self._graph_hop_keys:
                    continue
                self._graph_hop_keys.add(key)
                item = dict(hop)
                self.graph_hops.append(item)
                added.append(item)
        return added

    async def register_calculator_trace(self, trace: Mapping[str, Any]) -> dict[str, Any]:
        async with self.lock:
            calc_id = f"CALC-{len(self.calculator_traces) + 1}"
            item = {**dict(trace), "calc_id": calc_id}
            self.calculator_traces.append(item)
            return item

    async def record_tool_trace(self, entry: Mapping[str, Any]) -> None:
        async with self.lock:
            self.tool_traces.append(dict(entry))

    async def register_memories(self, memories: Sequence[Mapping[str, Any] | Any]) -> list[dict[str, Any]]:
        """Register recalled user memories with monotonic `[MEM:M#]` badge IDs for Stage 7."""
        registered: list[dict[str, Any]] = []
        async with self.lock:
            existing_by_mid = {
                str(v.get("memory_id") or ""): k
                for k, v in self.recalled_memories.items()
                if v.get("memory_id")
            }
            for raw in memories:
                item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
                mid = str(item.get("memory_id") or "")
                if mid and mid in existing_by_mid:
                    bid = existing_by_mid[mid]
                    item["badge_id"] = bid
                    self.recalled_memories[bid] = item
                    registered.append(item)
                    continue
                req_bid = str(item.get("badge_id") or "").strip()
                if req_bid and req_bid not in self.recalled_memories:
                    bid = req_bid
                else:
                    n = len(self.recalled_memories) + 1
                    while f"M{n}" in self.recalled_memories:
                        n += 1
                    bid = f"M{n}"
                item["badge_id"] = bid
                self.recalled_memories[bid] = item
                if mid:
                    existing_by_mid[mid] = bid
                registered.append(item)
        return registered

    async def register_portfolio_snapshots(
        self, snapshots: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Register live `[PORT:<SYM>]` portfolio snapshot entries for Stage 7."""
        async with self.lock:
            if isinstance(snapshots, Mapping):
                for sym, entry in snapshots.items():
                    clean_sym = str(sym or "").strip().upper()
                    if clean_sym:
                        self.portfolio_snapshots[clean_sym] = dict(entry)
            else:
                for entry in snapshots:
                    clean_sym = str(entry.get("symbol") or "").strip().upper()
                    if clean_sym:
                        self.portfolio_snapshots[clean_sym] = dict(entry)
            return dict(self.portfolio_snapshots)

    async def register_linked_sessions(self, sessions: Sequence[Mapping[str, Any] | Any]) -> list[dict[str, Any]]:
        """Register `[SESS:S#]` linked session episodes for Stage 7."""
        registered: list[dict[str, Any]] = []
        async with self.lock:
            existing_by_sid = {
                str(v.get("session_id") or ""): k
                for k, v in self.linked_sessions.items()
                if v.get("session_id")
            }
            for raw in sessions:
                item = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
                sid = str(item.get("session_id") or "")
                if sid and sid in existing_by_sid:
                    bid = existing_by_sid[sid]
                    item["badge_id"] = bid
                    self.linked_sessions[bid] = item
                    registered.append(item)
                    continue
                req_bid = str(item.get("badge_id") or "").strip()
                if req_bid and req_bid not in self.linked_sessions:
                    bid = req_bid
                else:
                    n = len(self.linked_sessions) + 1
                    while f"S{n}" in self.linked_sessions:
                        n += 1
                    bid = f"S{n}"
                item["badge_id"] = bid
                self.linked_sessions[bid] = item
                if sid:
                    existing_by_sid[sid] = bid
                registered.append(item)
        return registered

    def has_any_evidence(self) -> bool:
        return bool(
            self.sections
            or self.hydrated_tables
            or self.screening_rows
            or self.graph_hops
            or self.calculator_traces
        )

    def has_any_grounded_source(self) -> bool:
        return bool(
            self.has_any_evidence()
            or self.recalled_memories
            or self.portfolio_snapshots
            or self.linked_sessions
        )

    @property
    def latest_crag_grade(self) -> str | None:
        return self.crag_grades[-1] if self.crag_grades else None


@dataclass(frozen=True)
class ResearchToolContext:
    """Server-locked PIT/ACL execution context injected into every external research tool."""

    as_of_date: date
    principal: VerifiedPrincipal
    chat_mode: Literal["GENERAL", "MOAT"]
    session_factory: SessionFactoryFn
    session_ledger: FinancialZeroMemLedger
    evidence: ResearchEvidenceLedger
    available_tickers: frozenset[str] = frozenset()
    catalog_profiles: tuple[dict[str, Any], ...] = ()
    engine_manager: Any = None
    core_reader: CoreMetricsReaderProtocol | None = None
    kg_store_override: KGStoreProtocol | None = None
    table_store_override: Any = None
    retriever_override: Any = None
    reranker: CrossEncoderRerankerClient | None = None
    enable_kg: bool = True
    memory_mode: Literal["auto", "read_only", "incognito"] = "auto"
    user_memory_mode: str = "off"
    user_portfolio_snapshot: Any = None
    current_session_id: str | None = None
    tier0_memory_prompt: str = ""
    tier2_memory_prompt: str = ""
    include_recent_pairs: bool = True

    @property
    def tenant_id(self) -> str:
        return (self.principal.tenant_id or self.principal.organization_id or "aiinvest-default").strip()

    @property
    def user_id(self) -> str:
        return str(self.principal.subject or "").strip()
