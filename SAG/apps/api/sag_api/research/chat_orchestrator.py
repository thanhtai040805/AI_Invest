"""SAG v2 Research Chat Orchestrator (7-Stage Grounded Pipeline).

Stages emitted over SSE:
  routing -> retrieval -> graph_hop -> table_hydration -> calculator -> synthesis -> verification -> done

Hard Invariants:
- Strict Point-in-Time enforcement (`as_of_date` / `valid_from_ts <= as_of_ts`).
- Multi-ticker & True-Peer expansion strictly capped at 6 tickers (fail-closed on explicit >6).
- Zero mental math: deterministic Python calculations emitting `CalculatorTraceItem`.
- `quote_hash` Post-Verifier fail-closed (`UNVERIFIED_CITATION` / `OUT_OF_PIT_WINDOW`).
- Graceful lazy imports of Track 1 & Track 2 modules so Track 3 tests run independently.
"""

from __future__ import annotations

import inspect
import json
import math
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.error_taxonomy import ErrorCode
from sag_api.core.errors import BadRequestError, ForbiddenError
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.core.token_usage import get_model_pricing, token_usage_scope
from sag_api.research.kg_store import KGStoreProtocol, SQLAlchemyKGStore
from sag_api.research.quote_verifier import (
    CanonicalBlockRecord,
    compute_quote_hash,
    load_canonical_blocks_for_versions,
    normalize_ws,
    verify_citations,
)
from sag_api.schemas.research_chat import (
    CalculatorTraceItem,
    CitationItem,
    DeclaredBlindspotItem,
    DiscrepancyAlertItem,
    GraphHopItem,
    HydratedTableItem,
    ResearchChatMetadata,
    ResearchChatStreamRequest,
    ResearchTelemetry,
)

log = get_logger("research_chat")

MAX_TICKERS_PER_REQUEST = 6

_PEER_INTENT_RE = re.compile(
    r"\b(?:đối thủ|cạnh tranh|so sánh|peer|peers|cùng ngành|cụm|chuỗi giá trị|nhà cung cấp|khách hàng|vs\.?)\b",
    re.IGNORECASE,
)
_CALC_INTENT_RE = re.compile(
    r"(?:biên|tỷ trọng|cagr|tăng trưởng|tác động|độ nhạy|giả định|nếu|%|bps|chênh lệch|vượt kế hoạch|trung vị)",
    re.IGNORECASE,
)
_CITED_QUOTE_RE = re.compile(r"\[(C\d+)\]")


@dataclass(frozen=True, slots=True)
class RetrievedResearchSection:
    """Normalized retrieved evidence unit with strict PIT metadata."""

    ticker: str
    doc_id: str
    document_version_id: str
    sub_doc_slot: str
    page_from: int
    page_to: int
    published_date: date
    content: str
    heading: str = ""
    table_id: str | None = None
    routing_slot: str | None = None
    leiden_nodes_visited: int = 0
    hipporag_hops: int = 0
    score: float = 0.0


@dataclass
class RetrievalResult:
    sections: list[RetrievedResearchSection] = field(default_factory=list)
    canonical_blocks: dict[str, list[CanonicalBlockRecord]] = field(default_factory=dict)
    routing_slot: str | None = None
    leiden_nodes_visited: int = 0
    hipporag_hops: int = 0


class ResearchRetrieverProtocol(Protocol):
    async def retrieve(
        self,
        *,
        query: str,
        tickers: Sequence[str],
        as_of_date: date,
        top_k: int,
    ) -> RetrievalResult:
        ...


class TableStoreProtocol(Protocol):
    async def get_tables(
        self,
        *,
        table_ids: Sequence[str],
        tickers: Sequence[str],
        as_of_date: date,
    ) -> list[dict[str, Any]]:
        ...

    async def get_table_by_id(
        self,
        table_id: str,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, Any] | None:
        ...


class SQLAlchemyTableStore:
    """Reads `sag_extracted_tables` strictly enforcing `valid_from <= as_of_ts`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _row_to_dict(row_obj: Any) -> dict[str, Any]:
        from sag_api.research.table_engine import to_hydrated_table

        raw = {
            "table_id": str(row_obj.table_id),
            "project_id": str(row_obj.project_id),
            "document_version_id": str(row_obj.document_version_id),
            "sub_doc_slot": str(row_obj.sub_doc_slot or ""),
            "fiscal_year": row_obj.fiscal_year,
            "fiscal_quarter": int(row_obj.fiscal_quarter or 0),
            "table_title": str(row_obj.table_title or ""),
            "section_path": str(row_obj.section_path or ""),
            "page_from": int(row_obj.page_from or 0),
            "page_to": int(row_obj.page_to or 0),
            "currency": str(row_obj.currency or "VND"),
            "unit_raw": str(row_obj.unit_raw or ""),
            "unit_multiplier": float(row_obj.unit_multiplier or 1.0),
            "headers_json": row_obj.headers_json if isinstance(row_obj.headers_json, list) else [],
            "rows_json": row_obj.rows_json if isinstance(row_obj.rows_json, list) else [],
            "footnotes_text": row_obj.footnotes_text,
            "sanity_error_ratio": row_obj.sanity_error_ratio,
            "verification_status": str(row_obj.verification_status),
            "table_hash": str(row_obj.table_hash or ""),
            "valid_from": (
                row_obj.valid_from.isoformat()
                if isinstance(row_obj.valid_from, datetime)
                else str(row_obj.valid_from)
            ),
        }
        hydrated = to_hydrated_table(raw)
        hydrated.update(
            {
                "project_id": raw["project_id"],
                "document_version_id": raw["document_version_id"],
                "fiscal_year": raw["fiscal_year"],
                "fiscal_quarter": raw["fiscal_quarter"],
                "section_path": raw["section_path"],
                "valid_from": raw["valid_from"],
            }
        )
        return hydrated

    async def get_tables(
        self,
        *,
        table_ids: Sequence[str],
        tickers: Sequence[str],
        as_of_date: date,
    ) -> list[dict[str, Any]]:
        from sag_api.db.models.stock_research import SagExtractedTable

        as_of_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
        clean_ids = [tid for tid in table_ids if tid]
        clean_tickers = [t.strip().upper() for t in tickers if t.strip()]
        if not clean_ids and not clean_tickers:
            return []

        if clean_ids:
            stmt = (
                select(SagExtractedTable)
                .where(
                    SagExtractedTable.table_id.in_(clean_ids),
                    SagExtractedTable.valid_from <= as_of_dt,
                )
                .order_by(SagExtractedTable.fiscal_year.desc(), SagExtractedTable.page_from.asc())
            )
        else:
            stmt = (
                select(SagExtractedTable)
                .where(
                    SagExtractedTable.project_id.in_(clean_tickers),
                    SagExtractedTable.valid_from <= as_of_dt,
                )
                .order_by(SagExtractedTable.fiscal_year.desc(), SagExtractedTable.page_from.asc())
                .limit(12)
            )

        try:
            rows = (await self._session.scalars(stmt)).all()
        except Exception as err:  # noqa: BLE001
            log.warning("sag_extracted_tables lookup skipped: %s", type(err).__name__)
            return []
        return [self._row_to_dict(r) for r in rows]

    async def get_table_by_id(
        self,
        table_id: str,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, Any] | None:
        from sag_api.db.models.stock_research import SagExtractedTable

        cutoff = as_of_date or date.today()
        as_of_dt = datetime(cutoff.year, cutoff.month, cutoff.day, 23, 59, 59, tzinfo=UTC)
        stmt = (
            select(SagExtractedTable)
            .where(
                SagExtractedTable.table_id == table_id,
                SagExtractedTable.valid_from <= as_of_dt,
            )
            .limit(1)
        )
        try:
            row = (await self._session.scalars(stmt)).first()
        except Exception as err:  # noqa: BLE001
            log.warning("get_table_by_id failed: %s", type(err).__name__)
            return None
        return self._row_to_dict(row) if row is not None else None


class SAGRetrieverAdapter:
    """Production adapter bridging Track 1 canonical & legacy retrieval with strict PIT."""

    def __init__(
        self,
        session: AsyncSession,
        engine_manager: Any | None = None,
    ) -> None:
        self._session = session
        self._engine_manager = engine_manager

    @staticmethod
    def _extract_routing_telemetry(stats: Mapping[str, Any]) -> tuple[int, int]:
        leiden_nodes = int(stats.get("leiden_nodes_visited", 0) or 0)
        hipporag_hops = int(stats.get("hipporag_hops", 0) or 0)
        routing = stats.get("routing")
        if isinstance(routing, Mapping):
            if leiden_nodes == 0:
                groups = routing.get("groups") or ()
                leiden_nodes = sum(
                    len(g.get("selected_nodes") or ())
                    for g in groups
                    if isinstance(g, Mapping)
                )
            if hipporag_hops == 0:
                multi_hop = routing.get("multi_hop")
                if isinstance(multi_hop, Mapping):
                    expanded = int(multi_hop.get("graph_expanded_candidates", 0) or 0)
                    if expanded > 0:
                        hipporag_hops = 2 if multi_hop.get("eligible") else 1
        return leiden_nodes, hipporag_hops

    async def retrieve(
        self,
        *,
        query: str,
        tickers: Sequence[str],
        as_of_date: date,
        top_k: int,
        principal: VerifiedPrincipal | None = None,
    ) -> RetrievalResult:
        if self._engine_manager is None or not tickers:
            return RetrievalResult()

        from sag_api.db.models import Document, DocumentVersion, Source
        from sag_api.enums import SourceStatus
        from sag_api.services.retrieval_service import retrieve_relevant_sections
        from sag_api.services.search_unit_retrieval_service import retrieve_search_unit_sections
        from sag_api.services.source_service import versioned_source_ids

        # Resolve active SAG Sources whose Documents belong to requested project_ids (tickers)
        doc_stmt = (
            select(Document.source_id, Document.project_id)
            .where(
                Document.project_id.in_([t.upper() for t in tickers]),
                Document.is_active.is_(True),
                Document.source_id.is_not(None),
            )
            .distinct()
        )
        doc_rows = (await self._session.execute(doc_stmt)).all()
        source_ids = sorted({str(r[0]) for r in doc_rows if r[0]})
        source_ticker_map = {str(r[0]): str(r[1]) for r in doc_rows if r[0] and r[1]}
        if not source_ids:
            return RetrievalResult()

        src_stmt = select(Source).where(
            Source.id.in_(source_ids),
            Source.status == SourceStatus.ACTIVE,
        )
        sources = list((await self._session.scalars(src_stmt)).all())
        if not sources:
            return RetrievalResult()

        as_of_ts = int(datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC).timestamp())
        partitioned_ids = await versioned_source_ids(self._session, [s.id for s in sources])
        versioned_sources = [s for s in sources if s.id in partitioned_ids]
        legacy_sources = [s for s in sources if s.id not in partitioned_ids]

        combined_sections: list[Any] = []
        merged_stats: dict[str, Any] = {}
        if versioned_sources and principal is not None:
            v_outcome = await retrieve_search_unit_sections(
                self._engine_manager,
                versioned_sources,
                query,
                principal=principal,
                top_k=top_k,
                as_of_ts=as_of_ts,
            )
            combined_sections.extend(v_outcome.sections)
            merged_stats.update(v_outcome.stats or {})
        elif versioned_sources:
            legacy_sources.extend(versioned_sources)

        if legacy_sources:
            l_outcome = await retrieve_relevant_sections(
                self._engine_manager,
                legacy_sources,
                query,
                top_k=top_k,
            )
            combined_sections.extend(l_outcome.sections)
            if not merged_stats:
                merged_stats.update(l_outcome.stats or {})

        ver_ids = sorted({s.document_version_id for s in combined_sections if s.document_version_id})
        ver_meta: dict[str, tuple[date, str]] = {}
        if ver_ids:
            ver_rows = (
                await self._session.execute(
                    select(
                        DocumentVersion.id,
                        DocumentVersion.source_published_at,
                        DocumentVersion.valid_from,
                        DocumentVersion.metadata_json,
                    ).where(DocumentVersion.id.in_(ver_ids))
                )
            ).all()
            for vid, src_pub, valid_from, meta in ver_rows:
                dt = src_pub or valid_from
                pub_d = dt.date() if isinstance(dt, datetime) else date.fromisoformat(str(dt)[:10])
                slot = str(meta.get("sub_doc_slot") or "S01") if isinstance(meta, Mapping) else "S01"
                ver_meta[str(vid)] = (pub_d, slot)

        sections: list[RetrievedResearchSection] = []
        for sec in combined_sections:
            vid = str(sec.document_version_id or "")
            if not vid or vid not in ver_meta:
                continue
            pub_d, slot = ver_meta[vid]
            if pub_d > as_of_date:
                # Strict PIT defense-in-depth
                continue
            ticker = (
                str(sec.project_id).upper()
                if sec.project_id
                else source_ticker_map.get(str(sec.source_id or ""), tickers[0].upper())
            )
            sections.append(
                RetrievedResearchSection(
                    ticker=ticker,
                    doc_id=str(sec.document_id or ""),
                    document_version_id=vid,
                    sub_doc_slot=slot,
                    page_from=int(sec.page_from or 1),
                    page_to=int(sec.page_to or sec.page_from or 1),
                    published_date=pub_d,
                    content=sec.content,
                    heading=sec.heading,
                    routing_slot=slot,
                    score=float(sec.score),
                )
            )

        blocks = await load_canonical_blocks_for_versions(
            self._session,
            [s.document_version_id for s in sections],
        )
        leiden_nodes, hipporag_hops = self._extract_routing_telemetry(merged_stats)
        return RetrievalResult(
            sections=sections,
            canonical_blocks=blocks,
            routing_slot=sections[0].sub_doc_slot if sections else None,
            leiden_nodes_visited=leiden_nodes,
            hipporag_hops=hipporag_hops,
        )


def _extract_numeric_from_row_cell(cell: Any) -> float | None:
    if isinstance(cell, Mapping):
        val = cell.get("numeric_value")
        if val is not None:
            try:
                fv = float(val)
                return fv if math.isfinite(fv) else None
            except (TypeError, ValueError):
                return None
        return None
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        fv = float(cell)
        return fv if math.isfinite(fv) else None
    return None


def _run_deterministic_calculator(
    *,
    query: str,
    hydrated_tables: Sequence[HydratedTableItem],
    graph_hops: Sequence[GraphHopItem],
) -> list[CalculatorTraceItem]:
    """Run zero-mental-math deterministic calculations on verified tables & KG edges.

    Attempts Track 2 `sag_api.research.calculator` first if available, and augments
    with verified table YoY / margin / macro sensitivity traces (never from TABLE_UNVERIFIED).
    """
    traces: list[CalculatorTraceItem] = []

    # Optional integration with Track 2 AST-sandboxed FinancialCalculator
    calc_engine: Any | None = None
    try:
        from sag_api.research.calculator import FinancialCalculator

        calc_engine = FinancialCalculator()
    except ImportError:
        calc_engine = None

    calc_seq = len(traces) + 1
    for tbl in hydrated_tables:
        if tbl.verification_status == "TABLE_UNVERIFIED":
            # Hard invariant: NEVER interpolate or calculate from unverified tables!
            continue
        col_headers = (
            tbl.headers[0]
            if (tbl.headers and isinstance(tbl.headers[0], list))
            else tbl.headers
        )
        if len(col_headers) >= 3 and tbl.rows:
            # Find first two numeric columns (e.g., current period vs prior period)
            for row in tbl.rows[:5]:
                if isinstance(row, Mapping):
                    label = str(row.get("label") or "")
                    vals = row.get("values") or []
                    nums = [_extract_numeric_from_row_cell(v) for v in vals]
                elif isinstance(row, list) and len(row) >= 3:
                    label = str(row[0].get("raw_text") if isinstance(row[0], Mapping) else row[0])
                    nums = [_extract_numeric_from_row_cell(c) for c in row[1:]]
                else:
                    continue

                if len(nums) >= 2 and nums[0] is not None and nums[1] is not None and abs(nums[1]) > 1e-9:
                    curr, prev = nums[0], nums[1]
                    if calc_engine is not None:
                        try:
                            tr = calc_engine.evaluate(
                                formula="yoy(current, prior)",
                                inputs={
                                    "current": {
                                        "value": curr,
                                        "table_id": tbl.table_id,
                                        "row": label,
                                        "currency": tbl.currency,
                                        "unit_multiplier": tbl.unit_multiplier,
                                        "verification_status": tbl.verification_status,
                                    },
                                    "prior": {
                                        "value": prev,
                                        "table_id": tbl.table_id,
                                        "row": label,
                                        "currency": tbl.currency,
                                        "unit_multiplier": tbl.unit_multiplier,
                                        "verification_status": tbl.verification_status,
                                    },
                                },
                                description=f"Biến động kỳ {label} ({tbl.title})",
                                calc_id=f"CALC-{calc_seq}",
                                format_style="signed_percent",
                            )
                            traces.append(CalculatorTraceItem(**tr.to_dict()))
                            calc_seq += 1
                            break
                        except Exception:  # noqa: BLE001
                            pass
                    yoy = (curr - prev) / abs(prev)
                    traces.append(
                        CalculatorTraceItem(
                            calc_id=f"CALC-{calc_seq}",
                            formula=f"({curr} - {prev}) / abs({prev})",
                            description=f"Biến động kỳ {label} ({tbl.title})",
                            inputs={
                                "current": {"value": curr, "table_id": tbl.table_id, "row": label},
                                "prior": {"value": prev, "table_id": tbl.table_id, "row": label},
                            },
                            result=round(yoy, 6),
                            formatted=f"{yoy * 100:+.2f}%",
                        )
                    )
                    calc_seq += 1
                    break

    # Macro shock sensitivity trace when query asks about macro % move & CONFIRMED_BOTH hop exists
    shock_match = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*%", query)
    if shock_match and _CALC_INTENT_RE.search(query):
        shock_val = float(shock_match.group(1).replace(",", ".")) / 100.0
        for hop in graph_hops:
            if hop.layer == "MACRO" and hop.beta is not None and hop.verification_tier == "CONFIRMED_BOTH":
                if calc_engine is not None:
                    try:
                        tr = calc_engine.evaluate(
                            formula="beta * shock",
                            inputs={"beta": float(hop.beta), "shock": shock_val},
                            description=f"Ước tính độ nhạy chuẩn hóa {hop.src_label} -> {hop.dst_label}",
                            calc_id=f"CALC-{calc_seq}",
                            format_style="number",
                            decimals=4,
                        )
                        d = tr.to_dict()
                        d["formatted"] = f"{tr.result:+.4f} SD"
                        traces.append(CalculatorTraceItem(**d))
                        calc_seq += 1
                        break
                    except Exception:  # noqa: BLE001
                        pass
                impact = hop.beta * shock_val
                traces.append(
                    CalculatorTraceItem(
                        calc_id=f"CALC-{calc_seq}",
                        formula=f"beta ({hop.beta}) * shock ({shock_val})",
                        description=f"Ước tính độ nhạy chuẩn hóa {hop.src_label} -> {hop.dst_label}",
                        inputs={
                            "beta": {"value": hop.beta, "tier": hop.verification_tier},
                            "shock": {"value": shock_val},
                        },
                        result=round(impact, 6),
                        formatted=f"{impact:+.4f} SD",
                    )
                )
                calc_seq += 1
                break

    return traces


def _run_discrepancy_check(
    *,
    sections: Sequence[RetrievedResearchSection],
    hydrated_tables: Sequence[HydratedTableItem],
) -> list[DiscrepancyAlertItem]:
    """Run deterministic Quant-vs-Narrative Discrepancy Detector (Track 2)."""
    alerts: list[DiscrepancyAlertItem] = []
    if not sections and not hydrated_tables:
        return alerts
    try:
        from sag_api.research import discrepancy_detector as t2_disc

        detect_fn = getattr(t2_disc, "detect_discrepancies", None)
        if callable(detect_fn):
            narrative_text = "\n".join(
                f"<!-- PAGE {s.page_from} -->\n{s.content}"
                for s in sections
                if s.content.strip()
            )
            narrative_slot = sections[0].sub_doc_slot[:3] if sections else "S01"
            raw_alerts = detect_fn(
                narrative_text=narrative_text,
                narrative_slot=narrative_slot,
                hydrated_tables=[t.model_dump() for t in hydrated_tables],
            )
            for idx, a in enumerate(raw_alerts or (), 1):
                alerts.append(
                    DiscrepancyAlertItem(
                        alert_id=str(a.get("alert_id") or f"DISC-{idx}"),
                        severity=str(a.get("severity") or "MEDIUM"),  # type: ignore[arg-type]
                        metric=str(a.get("metric") or ""),
                        narrative_claim=str(a.get("narrative_claim") or ""),
                        audited_value=str(a.get("audited_value") or ""),
                        delta_pct=a.get("delta_pct"),
                        citations=list(a.get("citations") or []),
                    )
                )
    except ImportError:
        pass
    return alerts


def _parse_citations_from_answer(
    answer_text: str,
    sections: Sequence[RetrievedResearchSection],
) -> list[dict[str, Any]]:
    """Build candidate citations aligned with retrieved sections and inline `[C#]` references."""
    if not sections:
        return []
    cited_ids = set(_CITED_QUOTE_RE.findall(answer_text or ""))
    candidates: list[dict[str, Any]] = []
    for idx, sec in enumerate(sections, 1):
        cid = f"C{idx}"
        if cited_ids and cid not in cited_ids:
            continue
        snippet = normalize_ws(sec.content)[:280]
        if not snippet:
            continue
        candidates.append(
            {
                "citation_id": cid,
                "doc_id": sec.doc_id,
                "document_version_id": sec.document_version_id,
                "sub_doc_slot": sec.sub_doc_slot,
                "page": max(1, sec.page_from),
                "published_date": sec.published_date.isoformat(),
                "quote": snippet,
                "quote_hash": compute_quote_hash(snippet),
                "verified": False,
            }
        )
    return candidates


def _build_synthesis_messages(
    *,
    query: str,
    primary_ticker: str,
    expanded_tickers: Sequence[str],
    as_of_date: date,
    sections: Sequence[RetrievedResearchSection],
    hydrated_tables: Sequence[HydratedTableItem],
    calculator_traces: Sequence[CalculatorTraceItem],
    graph_hops: Sequence[GraphHopItem],
    discrepancy_alerts: Sequence[DiscrepancyAlertItem],
    history: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    ev_lines = [
        (
            f"[C{idx}] ({sec.ticker} | {sec.sub_doc_slot} | P{sec.page_from} | "
            f"pub={sec.published_date})\n{sec.content.strip()}"
        )
        for idx, sec in enumerate(sections, 1)
    ]
    tbl_lines = [
        (
            f"[TBL:{t.table_id}] {t.title} ({t.sub_doc_slot} P{t.page_from}-{t.page_to} | "
            f"{t.currency} {t.unit_raw} | status={t.verification_status})\n"
            + json.dumps({"headers": t.headers, "rows": t.rows[:15]}, ensure_ascii=False)
        )
        for t in hydrated_tables
    ]
    calc_lines = [
        f"[{c.calc_id}] {c.description}: {c.formula} = {c.formatted}"
        for c in calculator_traces
    ]
    kg_lines = [
        (
            f"[KG:{h.layer}|{h.verification_tier}] {h.src_label} -[{h.relation_type}]-> "
            f"{h.dst_label} (beta={h.beta}, p={h.p_value}, eff={h.effective_from})"
        )
        for h in graph_hops[:15]
    ]
    disc_lines = [
        f"[{d.alert_id}|{d.severity}] {d.metric}: narrative='{d.narrative_claim}' vs audited='{d.audited_value}'"
        for d in discrepancy_alerts
    ]

    system_prompt = (
        "You are an institutional equity research analyst for HOSE listed companies (MOAT & Financial Audit). "
        f"Point-in-Time boundary: {as_of_date.isoformat()}. NEVER reference facts after this date. "
        "Answer in the exact language of the user query (Vietnamese or English). "
        "Every factual claim MUST cite its exact evidence badge like [C1], [C2]. "
        "NEVER perform mental arithmetic; report ONLY exact computed numbers from [CALC-*] traces and verified tables. "
        "If evidence is missing for part of the question, explicitly state INSUFFICIENT_EVIDENCE "
        "for that scope instead of guessing."
    )
    user_context = (
        f"Primary Ticker: {primary_ticker} | Scope Tickers: {', '.join(expanded_tickers)} | As-Of Date: {as_of_date}\n"
        f"Question: {query}\n\n"
        f"=== VERIFIED DOCUMENT EXCERPTS ===\n" + ("\n\n".join(ev_lines) or "(none)") + "\n\n"
        "=== HYDRATED 2D TABLES ===\n" + ("\n\n".join(tbl_lines) or "(none)") + "\n\n"
        "=== PYTHON CALCULATOR TRACES ===\n" + ("\n".join(calc_lines) or "(none)") + "\n\n"
        "=== 3-LAYER KNOWLEDGE GRAPH HOPS ===\n" + ("\n".join(kg_lines) or "(none)") + "\n\n"
        "=== QUANT-VS-NARRATIVE DISCREPANCIES ===\n" + ("\n".join(disc_lines) or "(none)")
    )

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for msg in history[-6:]:
        role = msg.get("role", "user")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": str(msg.get("content", ""))})
    messages.append({"role": "user", "content": user_context})
    return messages


def _deterministic_grounded_fallback_answer(
    *,
    primary_ticker: str,
    as_of_date: date,
    sections: Sequence[RetrievedResearchSection],
    hydrated_tables: Sequence[HydratedTableItem],
    calculator_traces: Sequence[CalculatorTraceItem],
    graph_hops: Sequence[GraphHopItem],
) -> str:
    parts: list[str] = [f"Phân tích Point-in-Time ({as_of_date.isoformat()}) cho **{primary_ticker}**:"]
    for idx, sec in enumerate(sections[:4], 1):
        snippet = normalize_ws(sec.content)[:220]
        parts.append(f"- [{sec.sub_doc_slot}·P{sec.page_from}] {snippet} [C{idx}]")
    if hydrated_tables:
        for t in hydrated_tables[:2]:
            parts.append(
                f"- Bảng **{t.title}** ({t.sub_doc_slot}·P{t.page_from}, "
                f"trạng thái `{t.verification_status}`)."
            )
    if calculator_traces:
        for c in calculator_traces[:3]:
            parts.append(f"- [{c.calc_id}] {c.description}: **{c.formatted}**.")
    if graph_hops:
        for h in graph_hops[:3]:
            parts.append(
                f"- [KG: {h.verification_tier}] {h.src_label} -> {h.relation_type} -> {h.dst_label}."
            )
    return "\n".join(parts)


class ResearchChatOrchestrator:
    """Orchestrates the 7-stage SAG v2 Research Chat pipeline over SSE."""

    def __init__(
        self,
        *,
        session: AsyncSession | None = None,
        kg_store: KGStoreProtocol | None = None,
        retriever: ResearchRetrieverProtocol | None = None,
        table_store: TableStoreProtocol | None = None,
        llm: Any | None = None,
        engine_manager: Any | None = None,
    ) -> None:
        self._session = session
        if kg_store is not None:
            self._kg_store: KGStoreProtocol | None = kg_store
        elif session is not None:
            self._kg_store = SQLAlchemyKGStore(session)
        else:
            self._kg_store = None

        if retriever is not None:
            self._retriever: ResearchRetrieverProtocol | None = retriever
        elif session is not None:
            self._retriever = SAGRetrieverAdapter(session, engine_manager=engine_manager)
        else:
            self._retriever = None

        if table_store is not None:
            self._table_store: TableStoreProtocol | None = table_store
        elif session is not None:
            self._table_store = SQLAlchemyTableStore(session)
        else:
            self._table_store = None

        self._llm = llm

    @staticmethod
    def validate_and_resolve_tickers(
        *,
        primary_ticker: str,
        compare_tickers: Sequence[str],
        principal: VerifiedPrincipal,
    ) -> list[str]:
        """Enforce BFF scope authorization and explicit 6-ticker ceiling (fail-closed)."""
        primary = primary_ticker.strip().upper()
        if not primary:
            raise BadRequestError("ticker is required", code=ErrorCode.VALIDATION_ERROR)

        ordered: list[str] = [primary]
        for raw in compare_tickers:
            t = raw.strip().upper()
            if t and t not in ordered:
                ordered.append(t)

        if len(ordered) > MAX_TICKERS_PER_REQUEST:
            raise BadRequestError(
                f"Maximum {MAX_TICKERS_PER_REQUEST} tickers allowed per research chat request (got {len(ordered)}).",
                code=ErrorCode.VALIDATION_ERROR,
            )

        allowed = principal.allowed_project_ids
        if "*" not in allowed:
            unauthorized = [t for t in ordered if t not in allowed]
            if unauthorized:
                raise ForbiddenError(
                    f"Ticker scope not authorized by Continuum principal: {', '.join(unauthorized)}"
                )
        return ordered

    async def stream_chat(
        self,
        request: ResearchChatStreamRequest,
        *,
        principal: VerifiedPrincipal,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield `(event_name, payload)` tuples for SSE `EventSourceResponse`."""
        started = time.perf_counter()
        as_of_date = request.as_of_date or date.today()
        primary = request.ticker.strip().upper()

        # ── Stage 1: Routing ─────────────────────────────────────────────────
        yield (
            "stage",
            {
                "stage": "routing",
                "status": "running",
                "detail": {"ticker": primary, "as_of_date": as_of_date.isoformat()},
            },
        )

        expanded_tickers = self.validate_and_resolve_tickers(
            primary_ticker=primary,
            compare_tickers=request.compare_tickers,
            principal=principal,
        )
        should_expand_peers = (
            request.enable_kg
            and self._kg_store is not None
            and (bool(_PEER_INTENT_RE.search(request.query)) or principal.conversation_scope == "all")
            and len(expanded_tickers) < MAX_TICKERS_PER_REQUEST
        )
        if should_expand_peers and self._kg_store is not None:
            peers = await self._kg_store.get_true_peers(
                primary,
                as_of_date,
                limit=MAX_TICKERS_PER_REQUEST - len(expanded_tickers),
            )
            allowed = principal.allowed_project_ids
            for p in peers:
                pt = str(p.get("ticker") or "").strip().upper()
                if not pt or pt in expanded_tickers:
                    continue
                if "*" not in allowed and pt not in allowed:
                    continue
                expanded_tickers.append(pt)
                if len(expanded_tickers) >= MAX_TICKERS_PER_REQUEST:
                    break

        yield (
            "stage",
            {
                "stage": "routing",
                "status": "done",
                "detail": {"primary_ticker": primary, "expanded_tickers": expanded_tickers},
            },
        )

        # ── Stage 2: Retrieval (Strict PIT) ──────────────────────────────────
        yield (
            "stage",
            {"stage": "retrieval", "status": "running", "detail": {"tickers": expanded_tickers}},
        )
        top_k = int(getattr(settings, "research_search_top_k", 10) or 10)
        if self._retriever is not None:
            retrieve_kwargs: dict[str, Any] = {
                "query": request.query,
                "tickers": expanded_tickers,
                "as_of_date": as_of_date,
                "top_k": top_k,
            }
            sig = inspect.signature(self._retriever.retrieve)
            if "principal" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                retrieve_kwargs["principal"] = principal
            retrieval = await self._retriever.retrieve(**retrieve_kwargs)
        else:
            retrieval = RetrievalResult()

        # Defense-in-depth PIT filtering on retrieved sections
        pit_sections: list[RetrievedResearchSection] = []
        pit_filtered_count = 0
        for sec in retrieval.sections:
            if sec.published_date <= as_of_date:
                pit_sections.append(sec)
            else:
                pit_filtered_count += 1

        blindspots: list[DeclaredBlindspotItem] = []
        if pit_filtered_count > 0:
            blindspots.append(
                DeclaredBlindspotItem(
                    code="OUT_OF_PIT_WINDOW",
                    reason=(
                        f"Filtered {pit_filtered_count} post-cutoff document sections "
                        f"(published_date > {as_of_date})."
                    ),
                    scope="retrieval",
                )
            )
        yield (
            "stage",
            {
                "stage": "retrieval",
                "status": "done",
                "detail": {"section_count": len(pit_sections), "pit_filtered": pit_filtered_count},
            },
        )

        # ── Stage 3: 3-Layer KG Graph Hop ────────────────────────────────────
        yield ("stage", {"stage": "graph_hop", "status": "running", "detail": {"enable_kg": request.enable_kg}})
        graph_hops: list[GraphHopItem] = []
        if request.enable_kg and self._kg_store is not None:
            subgraph = await self._kg_store.get_subgraph_for_ticker(primary, as_of_date)
            node_labels = {str(n["node_id"]): str(n.get("label") or n["node_id"]) for n in subgraph.get("nodes", [])}
            for e in subgraph.get("edges", []):
                eff = str(e.get("effective_from") or as_of_date.isoformat())[:10]
                if date.fromisoformat(eff) > as_of_date:
                    continue
                src_id = str(e.get("src_node_id") or "")
                dst_id = str(e.get("dst_node_id") or "")
                graph_hops.append(
                    GraphHopItem(
                        layer=str(e.get("layer") or "VALUE_CHAIN"),  # type: ignore[arg-type]
                        src_label=node_labels.get(src_id, src_id),
                        relation_type=str(e.get("relation_type") or ""),
                        dst_label=node_labels.get(dst_id, dst_id),
                        beta=e.get("beta"),
                        p_value=e.get("p_value"),
                        fdr_q_value=e.get("fdr_q_value"),
                        tercile_stable=e.get("tercile_stable"),
                        verification_tier=str(e.get("verification_tier") or "DOC_ONLY"),
                        effective_from=eff,
                    )
                )
            for s in subgraph.get("macro_snapshots", []):
                eff = str(s.get("effective_from") or as_of_date.isoformat())[:10]
                if date.fromisoformat(eff) > as_of_date:
                    continue
                factor = str(s.get("macro_factor") or "")
                tgt = str(s.get("target_id") or "")
                # Avoid duplicating if already added as MACRO edge
                if any(
                    h.layer == "MACRO" and factor in h.src_label and tgt in h.dst_label
                    for h in graph_hops
                ):
                    continue
                graph_hops.append(
                    GraphHopItem(
                        layer="MACRO",
                        src_label=f"MACRO:{factor}",
                        relation_type="MACRO_SENSITIVE_TO",
                        dst_label=node_labels.get(tgt, tgt),
                        beta=s.get("beta"),
                        p_value=s.get("p_value"),
                        fdr_q_value=s.get("fdr_q_value"),
                        tercile_stable=s.get("tercile_stable"),
                        verification_tier=str(s.get("verification_tier") or "QUANT_ONLY"),
                        effective_from=eff,
                    )
                )
        yield ("stage", {"stage": "graph_hop", "status": "done", "detail": {"hop_count": len(graph_hops)}})

        # ── Stage 4: Table Hydration & Discrepancy Detection ─────────────────
        yield ("stage", {"stage": "table_hydration", "status": "running", "detail": {}})
        ref_table_ids = [s.table_id for s in pit_sections if s.table_id]
        raw_tables: list[dict[str, Any]] = []
        if self._table_store is not None:
            raw_tables = await self._table_store.get_tables(
                table_ids=ref_table_ids,
                tickers=expanded_tickers,
                as_of_date=as_of_date,
            )

        hydrated_tables: list[HydratedTableItem] = []
        for t in raw_tables:
            item = HydratedTableItem(
                table_id=str(t["table_id"]),
                title=str(t.get("title") or ""),
                sub_doc_slot=str(t.get("sub_doc_slot") or ""),
                page_from=int(t.get("page_from") or 0),
                page_to=int(t.get("page_to") or 0),
                currency=str(t.get("currency") or "VND"),
                unit_raw=str(t.get("unit_raw") or ""),
                unit_multiplier=float(t.get("unit_multiplier") or 1.0),
                verification_status=str(t.get("verification_status") or "TABLE_UNVERIFIED"),
                sanity_error_ratio=t.get("sanity_error_ratio"),
                headers=list(t.get("headers") or []),
                rows=list(t.get("rows") or []),
                footnotes_text=t.get("footnotes_text"),
            )
            hydrated_tables.append(item)
            if item.verification_status == "TABLE_UNVERIFIED":
                blindspots.append(
                    DeclaredBlindspotItem(
                        code="MISSING_TABLE_VERIFIED",
                        reason=(
                            f"Table '{item.title}' ({item.table_id}) failed arithmetic sanity gate "
                            "(TABLE_UNVERIFIED); excluded from calculator."
                        ),
                        scope=item.table_id,
                    )
                )

        discrepancy_alerts = _run_discrepancy_check(
            sections=pit_sections,
            hydrated_tables=hydrated_tables,
        )
        yield (
            "stage",
            {
                "stage": "table_hydration",
                "status": "done",
                "detail": {"table_count": len(hydrated_tables), "discrepancy_count": len(discrepancy_alerts)},
            },
        )

        # ── Stage 5: Deterministic Python Calculator ─────────────────────────
        yield ("stage", {"stage": "calculator", "status": "running", "detail": {}})
        calculator_traces = _run_deterministic_calculator(
            query=request.query,
            hydrated_tables=hydrated_tables,
            graph_hops=graph_hops,
        )
        yield (
            "stage",
            {"stage": "calculator", "status": "done", "detail": {"trace_count": len(calculator_traces)}},
        )

        # ── Stage 6: Grounded Synthesis (Abstention + Streaming) ─────────────
        yield ("stage", {"stage": "synthesis", "status": "running", "detail": {"model": settings.llm_model}})

        has_any_evidence = bool(pit_sections or hydrated_tables or graph_hops)
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0

        if not has_any_evidence:
            blindspots.append(
                DeclaredBlindspotItem(
                    code="NO_DOC_COVERAGE",
                    reason=(
                        "No Point-in-Time documents, verified tables, or KG evidence available for "
                        f"{', '.join(expanded_tickers)} as of {as_of_date}."
                    ),
                    scope=",".join(expanded_tickers),
                )
            )
            draft_answer = (
                f"INSUFFICIENT_EVIDENCE: Không có tài liệu, bảng số hoặc đồ thị tri thức hợp lệ tại thời điểm PIT "
                f"{as_of_date.isoformat()} cho {', '.join(expanded_tickers)} để trả lời câu hỏi này."
            )
            yield ("delta", {"text": draft_answer})
        else:
            messages = _build_synthesis_messages(
                query=request.query,
                primary_ticker=primary,
                expanded_tickers=expanded_tickers,
                as_of_date=as_of_date,
                sections=pit_sections,
                hydrated_tables=hydrated_tables,
                calculator_traces=calculator_traces,
                graph_hops=graph_hops,
                discrepancy_alerts=discrepancy_alerts,
                history=[m.model_dump() for m in request.history],
            )
            parts: list[str] = []
            if self._llm is not None and getattr(self._llm, "configured", True):
                with token_usage_scope(
                    component="research_chat_synthesis",
                    project_id=primary,
                    tenant_id=principal.tenant_id,
                ) as ledger:
                    async for token in self._llm.stream_complete(messages):
                        if token:
                            parts.append(token)
                            yield ("delta", {"text": token})
                summary = ledger.to_summary()
                totals = summary.get("totals", {})
                input_tokens = int(totals.get("llm_input_tokens", 0) or 0)
                output_tokens = int(totals.get("llm_output_tokens", 0) or 0)
                cost_usd = float(summary.get("cost_usd", 0.0) or 0.0)
                if input_tokens == 0 and parts:
                    input_tokens = max(1, sum(len(m["content"]) for m in messages) // 4)
                    output_tokens = max(1, len("".join(parts)) // 4)
                    rates = get_model_pricing(settings.llm_model)
                    cost_usd = round(
                        (
                            input_tokens * float(rates.get("llm_input_per_1m", 0.0))
                            + output_tokens * float(rates.get("llm_output_per_1m", 0.0))
                        )
                        / 1_000_000.0,
                        8,
                    )
                draft_answer = "".join(parts)
            else:
                draft_answer = _deterministic_grounded_fallback_answer(
                    primary_ticker=primary,
                    as_of_date=as_of_date,
                    sections=pit_sections,
                    hydrated_tables=hydrated_tables,
                    calculator_traces=calculator_traces,
                    graph_hops=graph_hops,
                )
                yield ("delta", {"text": draft_answer})

        yield ("stage", {"stage": "synthesis", "status": "done", "detail": {}})

        # ── Stage 7: Post-Synthesis `quote_hash` Verification ────────────────
        yield ("stage", {"stage": "verification", "status": "running", "detail": {}})
        candidate_citations = _parse_citations_from_answer(draft_answer, pit_sections)
        blocks_by_version = dict(retrieval.canonical_blocks)
        if not blocks_by_version and self._session is not None and candidate_citations:
            blocks_by_version = await load_canonical_blocks_for_versions(
                self._session,
                [c["document_version_id"] for c in candidate_citations],
            )

        ver_res = verify_citations(
            candidate_citations,
            blocks_by_version,
            as_of_date,
            answer_text=draft_answer,
        )
        for bs in ver_res.blindspots:
            blindspots.append(DeclaredBlindspotItem(**bs))

        final_citations = [CitationItem(**c) for c in ver_res.verified_citations]
        final_answer = ver_res.sanitized_answer or draft_answer

        yield (
            "stage",
            {
                "stage": "verification",
                "status": "done",
                "detail": {
                    "verified_citations": len(ver_res.verified_citations),
                    "rejected_citations": len(ver_res.rejected_citations),
                    "verified_rate": ver_res.verified_rate,
                },
            },
        )

        latency_ms = int((time.perf_counter() - started) * 1000)
        metadata = ResearchChatMetadata(
            mode="RESEARCH_CHAT_V2",
            primary_ticker=primary,
            expanded_tickers=expanded_tickers,
            as_of_date=as_of_date.isoformat(),
            enable_kg=request.enable_kg,
            citations=final_citations,
            hydrated_tables=hydrated_tables,
            calculator_traces=calculator_traces,
            graph_hops=graph_hops,
            discrepancy_alerts=discrepancy_alerts,
            declared_blindspots=blindspots,
            telemetry=ResearchTelemetry(
                routing_slot=retrieval.routing_slot or (pit_sections[0].sub_doc_slot if pit_sections else None),
                leiden_nodes_visited=retrieval.leiden_nodes_visited,
                hipporag_hops=retrieval.hipporag_hops,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            ),
        )
        yield ("done", {"answer": final_answer, "metadata": metadata.model_dump(mode="json")})


async def collect_stream_result(
    stream: AsyncIterator[tuple[str, dict[str, Any]]],
) -> tuple[str, dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Helper for testing or non-streaming callers to consume SSE stages."""
    events: list[tuple[str, dict[str, Any]]] = []
    done_payload: dict[str, Any] = {}
    async for ev, payload in stream:
        events.append((ev, payload))
        if ev == "done":
            done_payload = payload
    return str(done_payload.get("answer", "")), dict(done_payload.get("metadata") or {}), events
