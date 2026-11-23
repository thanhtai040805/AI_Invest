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
_AT_TICKER_RE = re.compile(r"@([A-Z]{2,5})\b")
_UPPER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{3})(?![A-Za-z0-9_])")
_FOLLOWUP_ANAPHORA_RE = re.compile(
    r"(?:\b(?:nó|công\s+ty\s+này|doanh\s+nghiệp\s+này|mã\s+này|cổ\s+phiếu\s+này|"
    r"còn\s+(?:năm|quý|mảng|phần|về)|thế\s+còn|vậy\s+còn|chi\s+tiết\s+hơn|cụ\s+thể\s+hơn|"
    r"tại\s+sao\s+(?:vậy|thế)|ý\s+trên|phần\s+trên|so\s+với\s+năm\s+(?:trước|ngoái)|"
    r"it|they|their|that\s+company|what\s+about|how\s+about|elaborate|more\s+detail)\b)",
    re.IGNORECASE | re.UNICODE,
)
_PR_QUERY_INTENT_RE = re.compile(
    r"(?:thư\s+chủ\s+tịch|thông\s+điệp|ban\s+lãnh\s+đạo|tầm\s+nhìn|sứ\s+mệnh|cam\s+kết|"
    r"khác\s+biệt|chênh\s+lệch|nói\s+và\s+làm|discrepancy|chairman|ceo\s+letter|management\s+letter)",
    re.IGNORECASE | re.UNICODE,
)
_BRIDGE_NOTE_REF_RE = re.compile(
    r"(?<!\w)(?:Thuyết\s*minh|TM|Note|Notes)\s*(?:số\s*)?(\d{1,2}(?:\.\d{1,2})?[a-z]?)(?!\w)",
    re.IGNORECASE | re.UNICODE,
)
_NON_TICKER_ACRONYMS = frozenset(
    {
        "ROE", "ROA", "ROI", "NIM", "CIR", "CAR", "NPL", "LDR", "EPS", "VND", "USD", "EUR",
        "JPY", "CNY", "GBP", "AUD", "KRW", "SGD", "GDP", "CPI", "PMI", "FDI", "ODA", "PPP",
        "FED", "SBV", "IMF", "WTO", "FTA", "ESG", "GRI", "GHG", "ISO", "FSC", "GMP", "AGM",
        "CEO", "CFO", "COO", "CTO", "CIO", "BKS", "NAV", "IRR", "NPV", "DCF", "IPO", "KPI",
        "OKR", "VAS", "API", "SQL", "PDF", "OCR", "PIT", "BFF", "SSE", "LLM", "RAG", "RRF",
        "YOY", "QOQ", "YTD", "TTM", "AFS", "HTM", "BCT", "CTY", "TNC", "TND", "VAT", "TCT",
        "VCS", "BPS", "DWT", "TEU", "MWP", "KWH", "MWH", "TON", "VNĐ", "HĐQ", "DTT", "VĐL",
        "NPT", "SME", "OEM", "ODM", "GPU", "CPU", "BPO", "ERP", "ICT", "KCN", "BDS", "BĐS",
        "LPG", "LNG", "HRC", "BOF", "EAF", "PPA", "EVN", "BOT", "EPC", "FOB", "CIF", "WTI",
        "LNST", "LNTT", "TNDN", "GTGT", "HĐQT", "NHNN", "CTCP", "TNHH", "TSCĐ", "TSNH",
        "TSDH", "VCSH", "KHDN", "KHCN", "OPEC", "SAAS", "FMCG", "BCTC", "BCTN", "HOSE",
        "HNX", "VN30", "MOAT", "CAGR", "CASA", "WACC", "ESOP", "CAPEX", "OPEX", "IFRS",
        "TT200", "EBITDA", "EBIT", "FVTPL", "P&L", "THE", "AND", "FOR", "NOT", "BUT", "ALL",
        "ANY", "NEW", "OLD", "TOP", "MAX", "MIN", "AVG", "SUM", "PCT", "NET", "TAX", "REV",
        "PAT", "PBT", "NWC", "FCF", "OCF", "D&A", "SGA", "COGS", "GPM", "NPM", "OPM", "ROIC",
        "ROCE", "EVS", "PEG", "PER", "PBR", "PSR", "DYR", "CĐKT", "KQKD", "LCTT", "EVFTA",
    }
)
_BRIDGE_ENTITY_RE = re.compile(
    r"(?:Công\s+ty\s+(?:TNHH|Cổ\s+phần|CP)(?:\s+MTV)?|CTCP|Tập\s+đoàn)"
    r"\s+[A-ZĐÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ]"
    r"(?:(?!\s+(?:được|là|đã|đang|sẽ|chiếm|đóng\s+góp|nắm\s+giữ|sở\s+hữu|trình\s+bày|thực\s+hiện|tại\s+thuyết\s+minh)\b)"
    r"[^\n,.;:()]){1,52}",
    re.UNICODE,
)


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
    block_from_id: str | None = None
    block_to_id: str | None = None
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None


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
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from sag_api.db.models import Document, DocumentVersion
        from sag_api.db.models.stock_research import SagExtractedTable

        as_of_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
        clean_ids = [tid for tid in table_ids if tid]
        clean_tickers = [t.strip().upper() for t in tickers if t.strip()]
        if not clean_ids and not clean_tickers:
            return []

        if clean_ids:
            stmt = select(SagExtractedTable).where(
                SagExtractedTable.table_id.in_(clean_ids),
                SagExtractedTable.valid_from <= as_of_dt,
            )
            if tenant_id:
                stmt = (
                    stmt.join(DocumentVersion, DocumentVersion.id == SagExtractedTable.document_version_id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(Document.tenant_id == tenant_id)
                )
            stmt = stmt.order_by(SagExtractedTable.fiscal_year.desc(), SagExtractedTable.page_from.asc())
        else:
            from sqlalchemy import case

            # F2: Fallback caps at 4 summary tables (fairly balanced across multi-ticker queries),
            # prioritizing VERIFIED first while keeping TABLE_UNVERIFIED blindspots transparent.
            verified_first = case((SagExtractedTable.verification_status == "VERIFIED", 0), else_=1)
            fetch_cap = max(4, len(clean_tickers) * 2)
            stmt = select(SagExtractedTable).where(
                SagExtractedTable.project_id.in_(clean_tickers),
                SagExtractedTable.valid_from <= as_of_dt,
            )
            if tenant_id:
                stmt = (
                    stmt.join(DocumentVersion, DocumentVersion.id == SagExtractedTable.document_version_id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(Document.tenant_id == tenant_id)
                )
            stmt = stmt.order_by(
                verified_first.asc(),
                SagExtractedTable.fiscal_year.desc(),
                SagExtractedTable.page_from.asc(),
            ).limit(fetch_cap)

        try:
            rows = list((await self._session.scalars(stmt)).all())
        except Exception as err:  # noqa: BLE001
            log.warning("sag_extracted_tables lookup skipped: %s", type(err).__name__)
            return []
        if not clean_ids and len(clean_tickers) >= 2 and len(rows) > 4:
            # Round-robin balance fallback tables across requested tickers so 1 ticker cannot monopolize
            by_tk: dict[str, list[Any]] = {tk: [] for tk in clean_tickers}
            for r in rows:
                by_tk.setdefault(str(r.project_id).upper(), []).append(r)
            balanced_rows: list[Any] = []
            max_cap = min(6, max(4, len(clean_tickers)))
            for depth in range(max_cap):
                for tk in clean_tickers:
                    bucket = by_tk.get(tk, [])
                    if depth < len(bucket):
                        balanced_rows.append(bucket[depth])
                        if len(balanced_rows) >= max_cap:
                            break
                if len(balanced_rows) >= max_cap:
                    break
            rows = balanced_rows
        return [self._row_to_dict(r) for r in rows]

    async def get_table_by_id(
        self,
        table_id: str,
        *,
        as_of_date: date | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        from sag_api.db.models import Document, DocumentVersion
        from sag_api.db.models.stock_research import SagExtractedTable

        cutoff = as_of_date or date.today()
        as_of_dt = datetime(cutoff.year, cutoff.month, cutoff.day, 23, 59, 59, tzinfo=UTC)
        stmt = select(SagExtractedTable).where(
            SagExtractedTable.table_id == table_id,
            SagExtractedTable.valid_from <= as_of_dt,
        )
        if tenant_id:
            stmt = (
                stmt.join(DocumentVersion, DocumentVersion.id == SagExtractedTable.document_version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Document.tenant_id == tenant_id)
            )
        stmt = stmt.limit(1)
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
        norm_tickers = sorted({t.strip().upper() for t in tickers if t and t.strip()})
        doc_filters = [
            Document.project_id.in_(norm_tickers),
            Document.is_active.is_(True),
            Document.source_id.is_not(None),
        ]
        if principal is not None and principal.tenant_id:
            doc_filters.append(Document.tenant_id == principal.tenant_id)
        doc_stmt = select(Document.source_id, Document.project_id).where(*doc_filters).distinct()
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

        # Materialize concrete project/partition scope when org-scoped wildcard "*" is used
        # so downstream SQL IN(...) clauses and Leiden tree routing match exact ticker partitions.
        effective_principal = principal
        if principal is not None and (
            "*" in principal.allowed_project_ids or "*" in principal.allowed_partition_ids
        ):
            concrete_scope = frozenset(norm_tickers)
            effective_principal = VerifiedPrincipal(
                subject=principal.subject,
                organization_id=principal.organization_id,
                allowed_project_ids=(
                    concrete_scope
                    if "*" in principal.allowed_project_ids
                    else principal.allowed_project_ids
                ),
                issuer=principal.issuer,
                key_id=principal.key_id,
                token_id=principal.token_id,
                issued_at=principal.issued_at,
                expires_at=principal.expires_at,
                tenant_id=principal.tenant_id,
                allowed_partition_ids=(
                    concrete_scope
                    if "*" in principal.allowed_partition_ids
                    else principal.allowed_partition_ids
                ),
                conversation_scope=principal.conversation_scope,
            )

        combined_sections: list[Any] = []
        merged_stats: dict[str, Any] = {}
        if versioned_sources and effective_principal is not None:
            v_outcome = await retrieve_search_unit_sections(
                self._engine_manager,
                versioned_sources,
                query,
                principal=effective_principal,
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
        ver_meta: dict[str, tuple[date, str, int | None, int | None]] = {}
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
                slot = str(meta.get("sub_doc_slot") or "BCTN_CORE") if isinstance(meta, Mapping) else "BCTN_CORE"
                fy = meta.get("fiscal_year") if isinstance(meta, Mapping) else None
                fq = meta.get("fiscal_quarter") if isinstance(meta, Mapping) else None
                ver_meta[str(vid)] = (
                    pub_d,
                    slot,
                    int(fy) if fy is not None else None,
                    int(fq) if fq is not None else None,
                )

        sections: list[RetrievedResearchSection] = []
        for sec in combined_sections:
            vid = str(sec.document_version_id or "")
            if not vid or vid not in ver_meta:
                continue
            pub_d, slot, ver_fy, ver_fq = ver_meta[vid]
            if pub_d > as_of_date:
                # Strict PIT defense-in-depth
                continue
            ticker = (
                str(sec.project_id).upper()
                if sec.project_id
                else source_ticker_map.get(str(sec.source_id or ""), norm_tickers[0])
            )
            sec_slot = str(getattr(sec, "sub_doc_slot", None) or slot)
            sec_table_id = getattr(sec, "table_id", None)
            if not sec_table_id:
                anc = str(getattr(sec, "anchor", None) or "")
                if anc.startswith("table_card:"):
                    parts = anc.split(":")
                    if len(parts) >= 2 and parts[1]:
                        sec_table_id = parts[1]
            sections.append(
                RetrievedResearchSection(
                    ticker=ticker,
                    doc_id=str(sec.document_id or ""),
                    document_version_id=vid,
                    sub_doc_slot=sec_slot,
                    page_from=int(sec.page_from or 1),
                    page_to=int(sec.page_to or sec.page_from or 1),
                    published_date=pub_d,
                    content=sec.content,
                    heading=sec.heading,
                    table_id=str(sec_table_id) if sec_table_id else None,
                    routing_slot=sec_slot,
                    score=float(sec.score),
                    block_from_id=getattr(sec, "block_from_id", None),
                    block_to_id=getattr(sec, "block_to_id", None),
                    fiscal_year=(
                        getattr(sec, "fiscal_year", None)
                        if getattr(sec, "fiscal_year", None) is not None
                        else ver_fy
                    ),
                    fiscal_quarter=(
                        getattr(sec, "fiscal_quarter", None)
                        if getattr(sec, "fiscal_quarter", None) is not None
                        else ver_fq
                    ),
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
    """Run zero-mental-math deterministic calculations on VERIFIED tables & KG edges.

    N4c hard invariants:
    - NEVER calculate from `TABLE_UNVERIFIED` or `PARTIAL` tables (strictly `VERIFIED` only).
    - NEVER compute YoY across `code` (`Mã số`), `note_ref` (`Thuyết minh`), `component_amount`
      (`Giá gốc` vs `Dự phòng`), `ratio_pct`, or `count` columns — ONLY across 2 distinct
      `period_amount` columns!
    - Rank candidate rows by query keyword overlap instead of blindly taking `rows[0]`.
    """
    from sag_api.research.table_engine import classify_table_columns

    traces: list[CalculatorTraceItem] = []

    calc_engine: Any | None = None
    try:
        from sag_api.research.calculator import FinancialCalculator

        calc_engine = FinancialCalculator()
    except ImportError:
        calc_engine = None

    query_terms = {
        tok.lower()
        for tok in re.findall(r"[^\W\d_]{2,}", query or "", flags=re.UNICODE)
    }

    def _row_priority_score(label: str) -> tuple[int, int]:
        low = label.lower()
        overlap = sum(1 for t in query_terms if t in low)
        is_key_line = int(
            any(
                kw in low
                for kw in (
                    "doanh thu thuần",
                    "lợi nhuận sau thuế",
                    "lợi nhuận trước thuế",
                    "lợi nhuận gộp",
                    "tổng cộng tài sản",
                    "tổng tài sản",
                    "vốn chủ sở hữu",
                    "dòng tiền thuần",
                )
            )
            and "chưa phân phối" not in low
        )
        return (overlap, is_key_line)

    calc_seq = len(traces) + 1
    for tbl in hydrated_tables:
        if tbl.verification_status != "VERIFIED":
            continue
        col_headers = (
            tbl.headers[0]
            if (tbl.headers and isinstance(tbl.headers[0], list))
            else tbl.headers
        )
        if len(col_headers) < 3 or not tbl.rows:
            continue

        col_roles = list(tbl.column_roles) if tbl.column_roles else []
        if len(col_roles) != len(col_headers):
            col_roles, _ = classify_table_columns(col_headers, tbl.rows)

        # Pick strictly 2 distinct `period_amount` value-column indices (0-based into row['values'])
        period_val_indices = [
            c - 1
            for c in range(1, min(len(col_headers), len(col_roles)))
            if col_roles[c] == "period_amount"
        ]
        if len(period_val_indices) < 2:
            continue
        idx_curr, idx_prev = period_val_indices[0], period_val_indices[1]
        if str(col_headers[idx_curr + 1]).strip().lower() == str(col_headers[idx_prev + 1]).strip().lower():
            continue

        # Score & rank rows by query relevance rather than blindly grabbing rows[0]
        ranked_rows: list[tuple[tuple[int, int], int, str, list[float | None]]] = []
        for r_idx, row in enumerate(tbl.rows[:25]):
            if isinstance(row, Mapping):
                label = str(row.get("label") or "")
                vals = row.get("values") or []
                nums = [_extract_numeric_from_row_cell(v) for v in vals]
            elif isinstance(row, list) and len(row) >= 3:
                label = str(row[0].get("raw_text") if isinstance(row[0], Mapping) else row[0])
                nums = [_extract_numeric_from_row_cell(c) for c in row[1:]]
            else:
                continue
            if (
                max(idx_curr, idx_prev) < len(nums)
                and nums[idx_curr] is not None
                and nums[idx_prev] is not None
                and abs(nums[idx_prev]) > 1e-9
            ):
                ranked_rows.append((_row_priority_score(label), -r_idx, label, nums))

        if not ranked_rows:
            continue
        ranked_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, _, label, nums = ranked_rows[0]
        curr, prev = nums[idx_curr], nums[idx_prev]
        assert curr is not None and prev is not None
        hdr_curr = str(col_headers[idx_curr + 1]).strip()
        hdr_prev = str(col_headers[idx_prev + 1]).strip()
        desc = f"Biến động {hdr_curr} vs {hdr_prev} — {label} ({tbl.title})"
        if calc_engine is not None:
            try:
                tr = calc_engine.evaluate(
                    formula="yoy(current, prior)",
                    inputs={
                        "current": {
                            "value": curr,
                            "table_id": tbl.table_id,
                            "row": label,
                            "column": hdr_curr,
                            "currency": tbl.currency,
                            "unit_multiplier": tbl.unit_multiplier,
                            "verification_status": tbl.verification_status,
                        },
                        "prior": {
                            "value": prev,
                            "table_id": tbl.table_id,
                            "row": label,
                            "column": hdr_prev,
                            "currency": tbl.currency,
                            "unit_multiplier": tbl.unit_multiplier,
                            "verification_status": tbl.verification_status,
                        },
                    },
                    description=desc,
                    calc_id=f"CALC-{calc_seq}",
                    format_style="signed_percent",
                )
                traces.append(CalculatorTraceItem(**tr.to_dict()))
                calc_seq += 1
                continue
            except Exception:  # noqa: BLE001
                pass
        yoy = (curr - prev) / abs(prev)
        traces.append(
            CalculatorTraceItem(
                calc_id=f"CALC-{calc_seq}",
                formula=f"({curr} - {prev}) / abs({prev})",
                description=desc,
                inputs={
                    "current": {"value": curr, "table_id": tbl.table_id, "row": label, "column": hdr_curr},
                    "prior": {"value": prev, "table_id": tbl.table_id, "row": label, "column": hdr_prev},
                },
                result=round(yoy, 6),
                formatted=f"{yoy * 100:+.2f}%",
            )
        )
        calc_seq += 1

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


_AUDITED_NON_NARRATIVE_SLOTS = frozenset({"BCTC_AUDITED", "QUARTER_NOTES"})


def _run_discrepancy_check(
    *,
    sections: Sequence[RetrievedResearchSection],
    hydrated_tables: Sequence[HydratedTableItem],
) -> list[DiscrepancyAlertItem]:
    """Run deterministic Quant-vs-Narrative Discrepancy Detector (Track 2).

    F8b-c: Strictly excludes `BCTC_AUDITED` / `QUARTER_NOTES` from narrative claims so audited
    financial statements are NEVER compared against themselves, and preserves Natural 4-Class slot names.
    """
    alerts: list[DiscrepancyAlertItem] = []
    if not sections or not hydrated_tables:
        return alerts
    narrative_sections = [
        s
        for s in sections
        if s.content.strip() and s.sub_doc_slot.strip().upper() not in _AUDITED_NON_NARRATIVE_SLOTS
    ]
    if not narrative_sections:
        return alerts
    try:
        from sag_api.research import discrepancy_detector as t2_disc

        detect_fn = getattr(t2_disc, "detect_discrepancies", None)
        if callable(detect_fn):
            narrative_text = "\n".join(
                f"<!-- PAGE {s.page_from} -->\n{s.content}"
                for s in narrative_sections
            )
            raw_slot = narrative_sections[0].sub_doc_slot.strip() or "BCTN_PR"
            narrative_slot = raw_slot[:3] if re.match(r"^S\d{2}(?:_|$)", raw_slot) else raw_slot
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


def _pick_verbatim_block_quote(
    sec: RetrievedResearchSection,
    blocks_by_version: Mapping[str, Sequence[CanonicalBlockRecord]] | None,
) -> tuple[str, int]:
    """F1: Extract a verbatim single-CanonicalBlock quote & exact page so `quote_verifier` achieves >=99% PASS."""
    blocks = (blocks_by_version or {}).get(sec.document_version_id, ())
    if blocks:
        # 1. Exact block span lookup via block_from_id .. block_to_id when available
        span_blocks: list[CanonicalBlockRecord] = []
        if sec.block_from_id:
            in_span = False
            for blk in blocks:
                if blk.block_id == sec.block_from_id:
                    in_span = True
                if in_span:
                    span_blocks.append(blk)
                if in_span and (not sec.block_to_id or blk.block_id == sec.block_to_id):
                    break
        # 2. Fallback: blocks on overlapping page range whose text appears in sec.content
        if not span_blocks:
            norm_sec = normalize_ws(sec.content)
            for blk in blocks:
                if blk.page_to < sec.page_from or blk.page_from > max(sec.page_from, sec.page_to):
                    continue
                nb = normalize_ws(blk.normalized_text)
                if nb and (nb[:80] in norm_sec or norm_sec[:80] in nb):
                    span_blocks.append(blk)
        # Prefer a substantive non-heading block (>= 25 chars) inside the span
        for blk in span_blocks:
            nb = normalize_ws(blk.normalized_text)
            if len(nb) >= 25:
                return nb[:280], max(1, int(blk.page_from))
        if span_blocks:
            nb = normalize_ws(span_blocks[0].normalized_text)
            if nb:
                return nb[:280], max(1, int(span_blocks[0].page_from))

    # Fallback when canonical_blocks aren't preloaded: avoid slicing across `\n\n` multi-block joins
    raw_parts = [p.strip() for p in (sec.content or "").split("\n\n") if p.strip()]
    best_part = next((p for p in raw_parts if len(normalize_ws(p)) >= 25), raw_parts[0] if raw_parts else "")
    return normalize_ws(best_part)[:280], max(1, int(sec.page_from))


def _parse_citations_from_answer(
    answer_text: str,
    sections: Sequence[RetrievedResearchSection],
    *,
    blocks_by_version: Mapping[str, Sequence[CanonicalBlockRecord]] | None = None,
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
        snippet, exact_page = _pick_verbatim_block_quote(sec, blocks_by_version)
        if not snippet:
            continue
        cit_entry: dict[str, Any] = {
            "citation_id": cid,
            "doc_id": sec.doc_id,
            "document_version_id": sec.document_version_id,
            "sub_doc_slot": sec.sub_doc_slot,
            "page": exact_page,
            "page_to": max(exact_page, int(sec.page_to)),
            "published_date": sec.published_date.isoformat(),
            "quote": snippet,
            "quote_hash": compute_quote_hash(snippet),
            "verified": False,
        }
        if sec.table_id:
            cit_entry["table_id"] = sec.table_id
        candidates.append(cit_entry)
    return candidates


# ── Pre-Retrieval, Dynamic Scope, Multi-Hop & Sufficiency Helpers (Phase 4) ──


def extract_query_tickers(
    text: str,
    *,
    available_tickers: set[str] | None = None,
    alias_to_ticker: Mapping[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Extract `(matched_known_tickers, unknown_explicit_tickers)` dynamically (0% hardcoded).

    - Recognizes `@TICKER` mentions, known DB tickers (`available_tickers`), company-name aliases
      from DB/KG (`alias_to_ticker`), and uppercase 3-letter HOSE-style tickers not in `_NON_TICKER_ACRONYMS`.
    """
    known = {t.upper() for t in (available_tickers or ())}
    aliases = {k.lower(): v.upper() for k, v in (alias_to_ticker or {}).items() if len(k) >= 3}
    matched: list[str] = []
    unknown: list[str] = []
    raw = text or ""
    low = raw.lower()

    def _add(sym: str, *, explicit: bool) -> None:
        s = sym.strip().upper()
        if not s:
            return
        # Check known DB tickers BEFORE acronym filter so a real ingested ticker is never dropped
        if known:
            if s in known:
                if s not in matched:
                    matched.append(s)
                return
            if s in _NON_TICKER_ACRONYMS:
                return
            if explicit and s not in unknown:
                unknown.append(s)
        else:
            if s in _NON_TICKER_ACRONYMS:
                return
            if s not in matched:
                matched.append(s)

    # 1. Explicit `@TICKER` tags
    for sym in _AT_TICKER_RE.findall(raw.upper()):
        _add(sym, explicit=True)

    # 2. Match known tickers (case-insensitive word boundary for known DB tickers)
    for k in sorted(known):
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(k)}(?![A-Za-z0-9_])", raw, re.IGNORECASE):
            _add(k, explicit=True)

    # 3. Match company-name aliases from DB/KG
    for alias, sym in aliases.items():
        if alias in low:
            _add(sym, explicit=True)

    # 4. Detect uppercase 3-letter ticker mentions (so asking about uningested e.g. "VCB" fails closed)
    for cand in _UPPER_TOKEN_RE.findall(raw):
        if cand in _NON_TICKER_ACRONYMS:
            continue
        _add(cand, explicit=True)

    return matched, unknown


def rewrite_contextual_query(
    query: str,
    history: Sequence[Mapping[str, str]],
    *,
    active_tickers: Sequence[str] = (),
) -> str:
    """Resolve multi-turn anaphora & short follow-ups using conversation history before retrieval."""
    clean_q = normalize_ws(query)
    if not clean_q or not history:
        return clean_q

    has_anaphora = bool(_FOLLOWUP_ANAPHORA_RE.search(clean_q))
    is_short_followup = len(clean_q) < 48 and not _UPPER_TOKEN_RE.search(clean_q)
    if not (has_anaphora or is_short_followup):
        return clean_q

    last_user_msgs = [
        normalize_ws(str(m.get("content") or ""))
        for m in history
        if m.get("role") == "user" and str(m.get("content") or "").strip()
    ]
    if not last_user_msgs:
        return clean_q

    prev_q = last_user_msgs[-1][:180]
    ticker_hint = f" ({', '.join(active_tickers)})" if active_tickers else ""
    if prev_q.lower() in clean_q.lower():
        return clean_q
    return f"{clean_q}{ticker_hint} | {prev_q}"


def apply_slot_ranking_prior(
    sections: Sequence[RetrievedResearchSection],
    *,
    query: str,
    mode: str = "GENERAL",
) -> list[RetrievedResearchSection]:
    """4a: Soft ranking prior by sub_doc_slot — NEVER hard-excludes `BCTN_PR` in `GENERAL` mode."""
    if not sections:
        return []
    asks_pr = bool(_PR_QUERY_INTENT_RE.search(query or ""))
    mode_u = (mode or "GENERAL").upper()

    def _eff_score(sec: RetrievedResearchSection) -> float:
        base = float(sec.score) if sec.score > 0 else 0.5
        slot = (sec.sub_doc_slot or "").upper()
        if slot == "BCTN_PR":
            mult = 1.10 if asks_pr else (0.95 if mode_u == "GENERAL" else 0.85)
        elif slot in ("BCTC_AUDITED", "QUARTER_NOTES", "AGM_TARGETS"):
            mult = 1.08 if mode_u == "MOAT" else 1.03
        elif slot == "BCTN_CORE":
            mult = 1.05 if mode_u == "MOAT" else 1.02
        else:
            mult = 1.0
        return base * mult

    return sorted(sections, key=_eff_score, reverse=True)


def compute_adaptive_top_k(
    *,
    resolved_query: str,
    tickers: Sequence[str],
    sub_queries: Sequence[str],
    base_top_k: int = 10,
) -> int:
    """4d: Adaptive retrieval budget — ~10 for single-fact local queries; 18-24 for multi-hop/multi-ticker."""
    from sag_api.services.query_analysis import extract_query_features

    feats = extract_query_features(resolved_query)
    is_multi = feats.multi_hop or len(sub_queries) >= 2 or len(tickers) >= 2
    if not is_multi:
        return max(10, base_top_k)
    scaled = max(18, base_top_k + 6 * max(1, len(tickers) - 1) + 2 * max(0, len(sub_queries) - 1))
    return min(24, scaled)


def balance_sections_by_ticker_and_subquery(
    sections: Sequence[RetrievedResearchSection],
    *,
    tickers: Sequence[str],
    sub_queries: Sequence[str],
    limit: int,
) -> list[RetrievedResearchSection]:
    """4d: Balanced per-ticker & per-subquery round-robin allocation preventing single-ticker/clause monopoly."""
    if len(sections) <= 1 or limit <= 0:
        return list(sections[:limit])

    def _interleave_by_subqueries(items: list[RetrievedResearchSection]) -> list[RetrievedResearchSection]:
        if len(items) <= 1 or len(sub_queries) < 2:
            return items
        sub_term_sets = [
            {tok.lower() for tok in re.findall(r"[^\W\d_]{2,}", sq, flags=re.UNICODE)}
            for sq in sub_queries
        ]
        sq_buckets: list[list[RetrievedResearchSection]] = [[] for _ in sub_term_sets]
        for sec in items:
            blob = f"{sec.heading} {sec.content[:400]}".lower()
            scores = [sum(1 for t in ts if t in blob) for ts in sub_term_sets]
            best_b = max(range(len(scores)), key=lambda i: scores[i])
            sq_buckets[best_b].append(sec)
        ordered_sq: list[RetrievedResearchSection] = []
        seen_sq: set[tuple[str, int, str]] = set()
        max_d = max((len(b) for b in sq_buckets), default=0)
        for d in range(max_d):
            for b in sq_buckets:
                if d < len(b):
                    s = b[d]
                    sk = (s.document_version_id, s.page_from, normalize_ws(s.content)[:80])
                    if sk not in seen_sq:
                        seen_sq.add(sk)
                        ordered_sq.append(s)
        return ordered_sq

    norm_tickers = [t.upper() for t in tickers if t]
    if len(norm_tickers) >= 2:
        buckets: dict[str, list[RetrievedResearchSection]] = {t: [] for t in norm_tickers}
        extras: list[RetrievedResearchSection] = []
        for sec in sections:
            tk = sec.ticker.upper()
            if tk in buckets:
                buckets[tk].append(sec)
            else:
                extras.append(sec)
        if len(sub_queries) >= 2:
            for tk in norm_tickers:
                buckets[tk] = _interleave_by_subqueries(buckets[tk])
        selected: list[RetrievedResearchSection] = []
        seen_keys: set[tuple[str, int, str]] = set()
        max_depth = max((len(v) for v in buckets.values()), default=0)
        for depth in range(max_depth):
            for tk in norm_tickers:
                if depth < len(buckets[tk]):
                    s = buckets[tk][depth]
                    sk = (s.document_version_id, s.page_from, normalize_ws(s.content)[:80])
                    if sk not in seen_keys:
                        seen_keys.add(sk)
                        selected.append(s)
                        if len(selected) >= limit:
                            return selected
        for s in extras:
            sk = (s.document_version_id, s.page_from, normalize_ws(s.content)[:80])
            if sk not in seen_keys:
                seen_keys.add(sk)
                selected.append(s)
                if len(selected) >= limit:
                    break
        return selected

    if len(sub_queries) >= 2:
        return _interleave_by_subqueries(list(sections))[:limit]

    return list(sections[:limit])


def extract_missing_bridge_queries(
    hop1_sections: Sequence[RetrievedResearchSection],
    *,
    query: str,
    max_bridges: int = 2,
) -> list[str]:
    """4c: Detect missing note refs (`Thuyết minh số X` / `TM X`) & bridge entities in Hop-1."""
    if not hop1_sections:
        return []
    covered_headings = " | ".join(
        f"{s.heading} {s.sub_doc_slot}" for s in hop1_sections
    ).lower()
    query_low = normalize_ws(query).lower()
    missing: list[str] = []
    seen_refs: set[str] = set()
    # 1. Explicit note cross-references (`Thuyết minh số X` / `TM X`)
    scan_blobs = [query, *(s.content[:1200] for s in hop1_sections[:6])]
    for blob in scan_blobs:
        for m in _BRIDGE_NOTE_REF_RE.finditer(blob or ""):
            ref_num = m.group(1).strip().lower()
            if not ref_num or ref_num in seen_refs:
                continue
            seen_refs.add(ref_num)
            if re.search(rf"(?:^|\b|thuyết\s*minh\s*(?:số\s*)?){re.escape(ref_num)}(?:\.|\b)", covered_headings):
                continue
            missing.append(f"Thuyết minh số {ref_num} {normalize_ws(query)[:120]}")
            if len(missing) >= max_bridges:
                return missing

    # 2. Bridge corporate entities revealed in Hop-1 (subsidiaries/JVs/counterparties) not yet in query/headings
    for sec in hop1_sections[:5]:
        for m in _BRIDGE_ENTITY_RE.finditer(sec.content[:1200] or ""):
            ent = normalize_ws(m.group(0)).strip(" ,.;:()")
            ent_low = ent.lower()
            if len(ent) < 10 or len(ent) > 60:
                continue
            if ent_low in seen_refs or ent_low in query_low or ent_low in covered_headings:
                continue
            seen_refs.add(ent_low)
            missing.append(f"{ent} {normalize_ws(query)[:100]}")
            if len(missing) >= max_bridges:
                return missing
    return missing


def evaluate_evidence_sufficiency(
    *,
    resolved_query: str,
    explicit_tickers: Sequence[str],
    sections: Sequence[RetrievedResearchSection],
    hydrated_tables: Sequence[HydratedTableItem],
    graph_hops: Sequence[GraphHopItem],
    sub_queries: Sequence[str] = (),
) -> tuple[list[DeclaredBlindspotItem], bool]:
    """4e: Relevance floor / evidence sufficiency gate before synthesis.

    Checks per-ticker coverage, per-subquery coverage, and low-relevance floor.
    Returns `(extra_blindspots, should_abstain_low_relevance)`.
    """
    extra_bs: list[DeclaredBlindspotItem] = []
    present_tickers = {s.ticker.upper() for s in sections if s.ticker}
    for t in hydrated_tables:
        if t.title:
            for tk in explicit_tickers:
                if tk.upper() in t.title.upper():
                    present_tickers.add(tk.upper())
    for h in graph_hops:
        hop_blob = f"{h.src_label} {h.dst_label}".upper()
        for tk in explicit_tickers:
            if tk.upper() in hop_blob:
                present_tickers.add(tk.upper())

    for tk in explicit_tickers:
        tk_u = tk.upper()
        if tk_u and tk_u not in present_tickers:
            extra_bs.append(
                DeclaredBlindspotItem(
                    code="INSUFFICIENT_EVIDENCE",
                    reason=(
                        f"Không tìm thấy đoạn bằng chứng phù hợp trong tài liệu đã nạp cho mã {tk_u}."
                    ),
                    scope=tk_u,
                )
            )

    # Check uncovered subqueries when compound query was decomposed into >= 2 clauses
    if len(sub_queries) >= 2 and (sections or hydrated_tables):
        from sag_api.services.query_analysis import analyze_query

        ev_corpus = " ".join(
            [
                *(f"{s.heading} {s.content[:600]}" for s in sections),
                *(t.title for t in hydrated_tables),
            ]
        ).lower()
        for idx_sq, sq in enumerate(sub_queries, 1):
            sq_terms = [t.lower() for t in analyze_query(sq).scoring_terms if len(t) >= 3]
            if sq_terms and not any(t in ev_corpus for t in sq_terms):
                extra_bs.append(
                    DeclaredBlindspotItem(
                        code="INSUFFICIENT_EVIDENCE",
                        reason=(
                            f"Không tìm thấy bằng chứng khớp trực tiếp cho ý phụ '{normalize_ws(sq)[:90]}'."
                        ),
                        scope=f"subquery:{idx_sq}",
                    )
                )

    # Low-relevance floor check when sections exist but scores are tiny and lexical overlap is zero
    if sections and not hydrated_tables and not graph_hops:
        scores = [float(s.score) for s in sections]
        max_score = max(scores, default=0.0)
        if 0.0 < max_score < 0.08:
            from sag_api.services.query_analysis import analyze_query

            q_terms = [t.lower() for t in analyze_query(resolved_query).scoring_terms if len(t) >= 3]
            combined = " ".join(f"{s.heading} {s.content[:500]}" for s in sections[:5]).lower()
            overlap = sum(1 for t in q_terms if t in combined)
            if q_terms and overlap == 0:
                extra_bs.append(
                    DeclaredBlindspotItem(
                        code="LOW_RELEVANCE_EVIDENCE",
                        reason=(
                            f"Độ phù hợp tối đa ({max_score:.3f}) dưới ngưỡng tin cậy "
                            "và không khớp từ khóa chính của câu hỏi."
                        ),
                        scope="retrieval",
                    )
                )
                return extra_bs, True

    return extra_bs, False


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
    mode: str = "GENERAL",
    blindspots: Sequence[DeclaredBlindspotItem] = (),
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
    bs_lines = [f"- [{b.code}] ({b.scope}): {b.reason}" for b in blindspots]

    if (mode or "GENERAL").upper() == "MOAT":
        persona = (
            "You are an institutional equity research analyst for HOSE listed companies operating in MOAT BROKER mode. "
            "Structure your answer with institutional rigor: (1) Direct executive answer with exact figures, "
            "(2) Economic Moat / Capital Allocation / Cash Flow Quality analysis grounded in audited filings, "
            "(3) Management-vs-Audited Discrepancy & Risk Audit (highlight any [DISC-*] alerts explicitly). "
        )
    else:
        persona = (
            "You are a grounded multi-hop equity research assistant for HOSE corporate filings. "
            "Answer naturally, clearly, and directly based strictly on retrieved evidence across relevant companies. "
        )

    system_prompt = (
        persona
        + f"Point-in-Time boundary: {as_of_date.isoformat()}. NEVER reference facts after this date. "
        "Answer in the exact language of the user query (Vietnamese or English). "
        "Every factual claim MUST cite its exact evidence badge like [C1], [C2] "
        "(ONLY use badges present in VERIFIED DOCUMENT EXCERPTS). "
        "NEVER perform mental arithmetic; report ONLY exact computed numbers from [CALC-*] traces and verified tables. "
        "If evidence is missing for part of the question or listed in DECLARED BLINDSPOTS, "
        "explicitly state INSUFFICIENT_EVIDENCE for that scope instead of guessing."
    )
    scope_label = ", ".join(expanded_tickers) if expanded_tickers else "(universe)"
    user_context = (
        f"Mode: {mode} | Primary Ticker: {primary_ticker or '(auto)'} | "
        f"Scope Tickers: {scope_label} | As-Of Date: {as_of_date}\n"
        f"Question: {query}\n\n"
        f"=== VERIFIED DOCUMENT EXCERPTS ===\n" + ("\n\n".join(ev_lines) or "(none)") + "\n\n"
        "=== HYDRATED 2D TABLES ===\n" + ("\n\n".join(tbl_lines) or "(none)") + "\n\n"
        "=== PYTHON CALCULATOR TRACES ===\n" + ("\n".join(calc_lines) or "(none)") + "\n\n"
        "=== 3-LAYER KNOWLEDGE GRAPH HOPS ===\n" + ("\n".join(kg_lines) or "(none)") + "\n\n"
        "=== QUANT-VS-NARRATIVE DISCREPANCIES ===\n" + ("\n".join(disc_lines) or "(none)") + "\n\n"
        "=== DECLARED BLINDSPOTS ===\n" + ("\n".join(bs_lines) or "(none)")
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
    subject_label = primary_ticker or ", ".join(sorted({s.ticker for s in sections if s.ticker})) or "HOSE"
    parts: list[str] = [f"Phân tích Point-in-Time ({as_of_date.isoformat()}) cho **{subject_label}**:"]
    for idx, sec in enumerate(sections[:4], 1):
        snippet = normalize_ws(sec.content)[:220]
        parts.append(f"- [{sec.ticker}·{sec.sub_doc_slot}·P{sec.page_from}] {snippet} [C{idx}]")
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
        primary_ticker: str | None = None,
        compare_tickers: Sequence[str] = (),
        principal: VerifiedPrincipal,
    ) -> list[str]:
        """Enforce BFF scope authorization and explicit 6-ticker ceiling (fail-closed).

        `primary_ticker` is optional (`None`) for free-form ChatGPT-style general chat;
        when omitted and `compare_tickers` is empty, returns `[]` so the dynamic scope
        resolver can infer tickers from the query, conversation history, or org corpus.
        """
        ordered: list[str] = []
        primary = (primary_ticker or "").strip().upper()
        if primary:
            ordered.append(primary)
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
        if ordered and "*" not in allowed:
            unauthorized = [t for t in ordered if t not in allowed]
            if unauthorized:
                raise ForbiddenError(
                    f"Ticker scope not authorized by Continuum principal: {', '.join(unauthorized)}"
                )
        return ordered

    async def _discover_available_tickers(
        self,
        *,
        principal: VerifiedPrincipal,
        as_of_date: date | None = None,
    ) -> tuple[set[str], dict[str, str]]:
        """Dynamically discover org-scoped ingested tickers & KG company-name aliases (0% hardcoded)."""
        if self._session is None:
            return set(), {}

        from sag_api.db.models import Document

        available: set[str] = set()
        alias_to_ticker: dict[str, str] = {}
        try:
            filters = [Document.is_active.is_(True), Document.project_id.is_not(None)]
            if principal.tenant_id:
                filters.append(Document.tenant_id == principal.tenant_id)
            rows = (await self._session.execute(select(Document.project_id).where(*filters).distinct())).all()
            for (pid,) in rows:
                if pid:
                    sym = str(pid).strip().upper()
                    if "*" in principal.allowed_project_ids or sym in principal.allowed_project_ids:
                        available.add(sym)
        except Exception:  # noqa: BLE001
            pass

        try:
            from sag_api.db.models.stock_research import KgNode

            kg_filters: list[Any] = [KgNode.ticker.is_not(None)]
            if as_of_date is not None:
                kg_filters.extend([KgNode.effective_from <= as_of_date, KgNode.valid_to > as_of_date])
            kg_rows = (
                await self._session.execute(
                    select(KgNode.ticker, KgNode.label).where(*kg_filters).limit(2000)
                )
            ).all()
            for tk, lbl in kg_rows:
                sym = str(tk or "").strip().upper()
                if not sym:
                    continue
                if available and sym not in available:
                    continue
                if "*" not in principal.allowed_project_ids and sym not in principal.allowed_project_ids:
                    continue
                clean_lbl = normalize_ws(str(lbl or ""))
                if len(clean_lbl) >= 4:
                    alias_to_ticker[clean_lbl.lower()] = sym
        except Exception:  # noqa: BLE001
            pass

        return available, alias_to_ticker

    async def _route_universe_tickers_thematically(
        self,
        *,
        query: str,
        available_tickers: set[str],
        principal: VerifiedPrincipal,
        as_of_date: date,
        limit: int = MAX_TICKERS_PER_REQUEST,
    ) -> list[str]:
        """4b: Dynamic thematic Universe routing via Leiden TreeRoutingProfile + PIT KG (0% hardcoded)."""
        if not available_tickers:
            return []
        if len(available_tickers) <= limit or self._session is None:
            return sorted(available_tickers)[:limit]

        from sag_api.services.query_analysis import analyze_query

        q_terms = {t.lower() for t in analyze_query(query).scoring_terms if len(t) >= 2}
        if not q_terms:
            return sorted(available_tickers)[:limit]

        scores: dict[str, float] = {tk: 0.0 for tk in available_tickers}
        sorted_avail = sorted(available_tickers)

        # 1. Leiden TreeRoutingProfile thematic match (sparse_json + entities_json)
        try:
            from sag_api.db.models.routing_rag import TreeRoutingProfile

            trp_filters: list[Any] = [TreeRoutingProfile.project_id.in_(sorted_avail)]
            if principal.tenant_id:
                trp_filters.append(TreeRoutingProfile.tenant_id == principal.tenant_id)
            trp_rows = (
                await self._session.execute(
                    select(
                        TreeRoutingProfile.project_id,
                        TreeRoutingProfile.sparse_json,
                        TreeRoutingProfile.entities_json,
                    )
                    .where(*trp_filters)
                    .limit(2000)
                )
            ).all()
            for pid, sparse_j, ent_j in trp_rows:
                sym = str(pid or "").upper()
                if sym not in scores:
                    continue
                blob_parts: list[str] = []
                if isinstance(sparse_j, list):
                    for item in sparse_j[:60]:
                        if isinstance(item, str):
                            blob_parts.append(item)
                        elif isinstance(item, Mapping):
                            blob_parts.append(str(item.get("term") or item.get("token") or ""))
                        elif isinstance(item, (list, tuple)) and item:
                            blob_parts.append(str(item[0]))
                if isinstance(ent_j, list):
                    blob_parts.extend(str(e) for e in ent_j[:40])
                blob = " ".join(blob_parts).lower()
                if blob:
                    hits = sum(1 for t in q_terms if t in blob)
                    if hits:
                        scores[sym] += float(hits) * 1.5
        except Exception:  # noqa: BLE001
            pass

        # 2. PIT 3-Layer KG thematic match (`KgNode.label` & `KgEdge` evidence/relations)
        try:
            from sag_api.db.models.stock_research import KgEdge, KgNode

            node_rows = (
                await self._session.execute(
                    select(KgNode.node_id, KgNode.ticker, KgNode.label)
                    .where(
                        KgNode.effective_from <= as_of_date,
                        KgNode.valid_to > as_of_date,
                    )
                    .limit(3000)
                )
            ).all()
            node_ticker_map: dict[str, str] = {}
            matched_node_ids: set[str] = set()
            for nid, tk, lbl in node_rows:
                sym = str(tk or "").strip().upper()
                lbl_low = str(lbl or "").lower()
                if sym in scores:
                    node_ticker_map[str(nid)] = sym
                hits = sum(1 for t in q_terms if t in lbl_low)
                if hits:
                    matched_node_ids.add(str(nid))
                    if sym in scores:
                        scores[sym] += float(hits) * 2.5

            if matched_node_ids and node_ticker_map:
                edge_rows = (
                    await self._session.execute(
                        select(KgEdge.src_node_id, KgEdge.dst_node_id, KgEdge.relation_type)
                        .where(
                            KgEdge.effective_from <= as_of_date,
                            KgEdge.valid_to > as_of_date,
                        )
                        .limit(3000)
                    )
                ).all()
                for src_id, dst_id, _rel in edge_rows:
                    s_id, d_id = str(src_id), str(dst_id)
                    if s_id in matched_node_ids and d_id in node_ticker_map:
                        scores[node_ticker_map[d_id]] += 2.0
                    if d_id in matched_node_ids and s_id in node_ticker_map:
                        scores[node_ticker_map[s_id]] += 2.0
        except Exception:  # noqa: BLE001
            pass

        # 3. CanonicalBlock section_path fallback when tree & KG have no thematic scores yet
        if max(scores.values(), default=0.0) <= 0.0:
            try:
                from sag_api.db.models import CanonicalBlock, Document, DocumentVersion

                blk_filters: list[Any] = [
                    Document.project_id.in_(sorted_avail),
                    Document.is_active.is_(True),
                ]
                if principal.tenant_id:
                    blk_filters.append(Document.tenant_id == principal.tenant_id)
                blk_rows = (
                    await self._session.execute(
                        select(Document.project_id, CanonicalBlock.section_path, CanonicalBlock.normalized_text)
                        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                        .join(CanonicalBlock, CanonicalBlock.document_version_id == DocumentVersion.id)
                        .where(*blk_filters)
                        .limit(500)
                    )
                ).all()
                for pid, sp, txt in blk_rows:
                    sym = str(pid or "").upper()
                    if sym not in scores:
                        continue
                    blob = f"{sp or ''} {str(txt or '')[:240]}".lower()
                    hits = sum(1 for t in q_terms if t in blob)
                    if hits:
                        scores[sym] += float(hits)
            except Exception:  # noqa: BLE001
                pass

        ranked = sorted(sorted_avail, key=lambda tk: (-scores.get(tk, 0.0), tk))
        return ranked[:limit]

    async def _invoke_retriever(
        self,
        *,
        query: str,
        tickers: Sequence[str],
        as_of_date: date,
        top_k: int,
        principal: VerifiedPrincipal,
    ) -> RetrievalResult:
        if self._retriever is None or not tickers:
            return RetrievalResult()
        retrieve_kwargs: dict[str, Any] = {
            "query": query,
            "tickers": list(tickers),
            "as_of_date": as_of_date,
            "top_k": top_k,
        }
        sig = inspect.signature(self._retriever.retrieve)
        if "principal" in sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            retrieve_kwargs["principal"] = principal
        return await self._retriever.retrieve(**retrieve_kwargs)

    async def stream_chat(
        self,
        request: ResearchChatStreamRequest,
        *,
        principal: VerifiedPrincipal,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield `(event_name, payload)` tuples for SSE `EventSourceResponse`."""
        from sag_api.services.query_analysis import decompose_query_clauses

        started = time.perf_counter()
        as_of_date = request.as_of_date or date.today()
        chat_mode = str(getattr(request, "mode", "GENERAL") or "GENERAL").upper()
        history_dicts = [m.model_dump() for m in request.history]
        blindspots: list[DeclaredBlindspotItem] = []

        # ── Stage 1: Routing (Contextual Rewrite + Dynamic Scope Resolver) ───
        yield (
            "stage",
            {
                "stage": "routing",
                "status": "running",
                "detail": {
                    "ticker": (request.ticker or "").strip().upper() or None,
                    "mode": chat_mode,
                    "as_of_date": as_of_date.isoformat(),
                },
            },
        )

        explicit_tickers = self.validate_and_resolve_tickers(
            primary_ticker=request.ticker,
            compare_tickers=request.compare_tickers,
            principal=principal,
        )
        available_tickers, alias_to_ticker = await self._discover_available_tickers(
            principal=principal,
            as_of_date=as_of_date,
        )

        # Extract tickers mentioned directly in the current query
        q_matched, q_unknown = extract_query_tickers(
            request.query,
            available_tickers=available_tickers if available_tickers else None,
            alias_to_ticker=alias_to_ticker,
        )
        # Merge explicitly requested tickers + query-detected known tickers
        for tk in q_matched:
            if tk not in explicit_tickers and len(explicit_tickers) < MAX_TICKERS_PER_REQUEST:
                if "*" in principal.allowed_project_ids or tk in principal.allowed_project_ids:
                    explicit_tickers.append(tk)

        # Carry over active tickers from recent conversation history for follow-ups when no ticker was named
        history_tickers: list[str] = []
        if not explicit_tickers and history_dicts:
            for msg in reversed(history_dicts[-6:]):
                h_matched, _ = extract_query_tickers(
                    str(msg.get("content") or ""),
                    available_tickers=available_tickers if available_tickers else None,
                    alias_to_ticker=alias_to_ticker,
                )
                for tk in h_matched:
                    if tk not in history_tickers:
                        history_tickers.append(tk)
                if history_tickers:
                    break

        is_followup_turn = bool(
            history_dicts
            and (
                _FOLLOWUP_ANAPHORA_RE.search(request.query or "")
                or (len(normalize_ws(request.query)) < 48 and not q_matched and not q_unknown)
            )
        )
        resolved_query = rewrite_contextual_query(
            request.query,
            history_dicts,
            active_tickers=explicit_tickers or (history_tickers if is_followup_turn else ()),
        )
        sub_queries = list(decompose_query_clauses(resolved_query))

        # Check fail-closed NO_DOC_COVERAGE when user explicitly asks about an uningested ticker
        uncovered_requested: list[str] = []
        if available_tickers:
            for tk in [*explicit_tickers, *q_unknown]:
                if tk not in available_tickers and tk not in uncovered_requested:
                    uncovered_requested.append(tk)

        expanded_tickers: list[str] = [
            tk for tk in explicit_tickers if (not available_tickers or tk in available_tickers)
        ]

        # If no ticker specified in current turn: carry history on follow-up, else Universe/Thematic routing
        if not expanded_tickers and not uncovered_requested:
            if is_followup_turn and history_tickers:
                expanded_tickers = history_tickers[:MAX_TICKERS_PER_REQUEST]
            elif available_tickers:
                # 4b: Dynamic Universe / Thematic routing via Leiden Tree + KG (capped at MAX_TICKERS_PER_REQUEST)
                expanded_tickers = await self._route_universe_tickers_thematically(
                    query=resolved_query,
                    available_tickers=available_tickers,
                    principal=principal,
                    as_of_date=as_of_date,
                    limit=MAX_TICKERS_PER_REQUEST,
                )

        primary = expanded_tickers[0] if expanded_tickers else (request.ticker or "").strip().upper()

        should_expand_peers = (
            bool(primary)
            and request.enable_kg
            and self._kg_store is not None
            and (bool(_PEER_INTENT_RE.search(resolved_query)) or (chat_mode == "MOAT" and bool(request.ticker)))
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

        if uncovered_requested:
            avail_list = ", ".join(sorted(available_tickers)[:20]) if available_tickers else "(none)"
            blindspots.append(
                DeclaredBlindspotItem(
                    code="NO_DOC_COVERAGE",
                    reason=(
                        f"Các mã {', '.join(uncovered_requested)} chưa có tài liệu trong kho dữ liệu "
                        f"(các mã đã nạp hiện có: {avail_list})."
                    ),
                    scope=",".join(uncovered_requested),
                )
            )

        yield (
            "stage",
            {
                "stage": "routing",
                "status": "done",
                "detail": {
                    "primary_ticker": primary,
                    "expanded_tickers": expanded_tickers,
                    "chat_mode": chat_mode,
                    "resolved_query": resolved_query,
                    "sub_queries": sub_queries,
                },
            },
        )

        # ── Stage 2: Retrieval (Adaptive Budget + Soft Prior + 1-Round Bridge Hop-2) ──
        yield (
            "stage",
            {"stage": "retrieval", "status": "running", "detail": {"tickers": expanded_tickers}},
        )
        base_top_k = int(getattr(settings, "research_search_top_k", 10) or 10)
        adaptive_top_k = compute_adaptive_top_k(
            resolved_query=resolved_query,
            tickers=expanded_tickers,
            sub_queries=sub_queries,
            base_top_k=base_top_k,
        )
        retrieval = await self._invoke_retriever(
            query=resolved_query,
            tickers=expanded_tickers,
            as_of_date=as_of_date,
            top_k=adaptive_top_k,
            principal=principal,
        )

        # Defense-in-depth PIT filtering on Hop-1 sections
        pit_sections: list[RetrievedResearchSection] = []
        pit_filtered_count = 0
        for sec in retrieval.sections:
            if sec.published_date <= as_of_date:
                pit_sections.append(sec)
            else:
                pit_filtered_count += 1

        all_blocks_by_version: dict[str, list[CanonicalBlockRecord]] = {
            k: list(v) for k, v in retrieval.canonical_blocks.items()
        }
        hipporag_hops = retrieval.hipporag_hops

        # 4c: Bounded 1-round conditional bridge Hop-2 for missing explicit note cross-references
        bridge_queries = extract_missing_bridge_queries(pit_sections, query=resolved_query, max_bridges=2)
        if bridge_queries and self._retriever is not None and expanded_tickers:
            seen_sec_keys = {
                (s.document_version_id, s.page_from, normalize_ws(s.content)[:80])
                for s in pit_sections
            }
            hop2_added = 0
            for bq in bridge_queries:
                try:
                    hop2_res = await self._invoke_retriever(
                        query=bq,
                        tickers=expanded_tickers,
                        as_of_date=as_of_date,
                        top_k=min(6, adaptive_top_k),
                        principal=principal,
                    )
                except Exception:  # noqa: BLE001
                    continue
                for ver_id, blks in hop2_res.canonical_blocks.items():
                    all_blocks_by_version.setdefault(ver_id, list(blks))
                for sec in hop2_res.sections:
                    if sec.published_date > as_of_date:
                        pit_filtered_count += 1
                        continue
                    sk = (sec.document_version_id, sec.page_from, normalize_ws(sec.content)[:80])
                    if sk not in seen_sec_keys:
                        seen_sec_keys.add(sk)
                        pit_sections.append(sec)
                        hop2_added += 1
            if hop2_added > 0:
                hipporag_hops = max(hipporag_hops, 2)

        # 4a + 4d: Apply soft slot ranking prior (never hard-excluding BCTN_PR in GENERAL) & balanced diversity
        pit_sections = apply_slot_ranking_prior(pit_sections, query=resolved_query, mode=chat_mode)
        pit_sections = balance_sections_by_ticker_and_subquery(
            pit_sections,
            tickers=expanded_tickers,
            sub_queries=sub_queries,
            limit=adaptive_top_k,
        )

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
                "detail": {
                    "section_count": len(pit_sections),
                    "pit_filtered": pit_filtered_count,
                    "adaptive_top_k": adaptive_top_k,
                    "bridge_queries": bridge_queries,
                },
            },
        )

        # ── Stage 3: 3-Layer KG Graph Hop (Multi-Ticker Aware) ───────────────
        yield ("stage", {"stage": "graph_hop", "status": "running", "detail": {"enable_kg": request.enable_kg}})
        graph_hops: list[GraphHopItem] = []
        kg_target_tickers = expanded_tickers[:MAX_TICKERS_PER_REQUEST] if expanded_tickers else (
            [primary] if primary else []
        )
        if request.enable_kg and self._kg_store is not None and kg_target_tickers:
            seen_hops: set[tuple[str, str, str, str, str]] = set()
            for kg_tk in kg_target_tickers:
                subgraph = await self._kg_store.get_subgraph_for_ticker(kg_tk, as_of_date)
                node_labels = {
                    str(n["node_id"]): str(n.get("label") or n["node_id"])
                    for n in subgraph.get("nodes", [])
                }
                for e in subgraph.get("edges", []):
                    eff = str(e.get("effective_from") or as_of_date.isoformat())[:10]
                    if date.fromisoformat(eff) > as_of_date:
                        continue
                    src_id = str(e.get("src_node_id") or "")
                    dst_id = str(e.get("dst_node_id") or "")
                    layer_val = str(e.get("layer") or "VALUE_CHAIN")
                    src_lbl = node_labels.get(src_id, src_id)
                    rel_val = str(e.get("relation_type") or "")
                    dst_lbl = node_labels.get(dst_id, dst_id)
                    hop_key = (layer_val, src_lbl, rel_val, dst_lbl, eff)
                    if hop_key in seen_hops:
                        continue
                    seen_hops.add(hop_key)
                    graph_hops.append(
                        GraphHopItem(
                            layer=layer_val,  # type: ignore[arg-type]
                            src_label=src_lbl,
                            relation_type=rel_val,
                            dst_label=dst_lbl,
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
                    src_lbl = f"MACRO:{factor}"
                    dst_lbl = node_labels.get(tgt, tgt)
                    hop_key = ("MACRO", src_lbl, "MACRO_SENSITIVE_TO", dst_lbl, eff)
                    if hop_key in seen_hops or any(
                        h.layer == "MACRO" and factor in h.src_label and tgt in h.dst_label
                        for h in graph_hops
                    ):
                        continue
                    seen_hops.add(hop_key)
                    graph_hops.append(
                        GraphHopItem(
                            layer="MACRO",
                            src_label=src_lbl,
                            relation_type="MACRO_SENSITIVE_TO",
                            dst_label=dst_lbl,
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
        ref_table_ids = list(dict.fromkeys(s.table_id for s in pit_sections if s.table_id))
        raw_tables: list[dict[str, Any]] = []
        if self._table_store is not None and (ref_table_ids or expanded_tickers):
            tbl_kwargs: dict[str, Any] = {
                "table_ids": ref_table_ids,
                "tickers": expanded_tickers,
                "as_of_date": as_of_date,
            }
            tbl_sig = inspect.signature(self._table_store.get_tables)
            if "tenant_id" in tbl_sig.parameters and principal.tenant_id:
                tbl_kwargs["tenant_id"] = principal.tenant_id
            raw_tables = await self._table_store.get_tables(**tbl_kwargs)

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
                column_roles=list(t.get("column_roles") or []),
                amount_col_indices=list(t.get("amount_col_indices") or []),
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
            query=resolved_query,
            hydrated_tables=hydrated_tables,
            graph_hops=graph_hops,
        )
        yield (
            "stage",
            {"stage": "calculator", "status": "done", "detail": {"trace_count": len(calculator_traces)}},
        )

        # ── Stage 6: Relevance Floor Gate + Grounded Synthesis ───────────────
        yield ("stage", {"stage": "synthesis", "status": "running", "detail": {"model": settings.llm_model}})

        sufficiency_bs, abstain_low_relevance = evaluate_evidence_sufficiency(
            resolved_query=resolved_query,
            explicit_tickers=[tk for tk in explicit_tickers if tk in expanded_tickers],
            sections=pit_sections,
            hydrated_tables=hydrated_tables,
            graph_hops=graph_hops,
            sub_queries=sub_queries,
        )
        blindspots.extend(sufficiency_bs)

        has_any_evidence = bool(pit_sections or hydrated_tables or graph_hops) and not abstain_low_relevance
        input_tokens = 0
        output_tokens = 0
        cost_usd = 0.0

        if not has_any_evidence:
            if not any(b.code in ("NO_DOC_COVERAGE", "LOW_RELEVANCE_EVIDENCE") for b in blindspots):
                avail_hint = (
                    f" (các mã đã nạp: {', '.join(sorted(available_tickers)[:20])})"
                    if available_tickers
                    else ""
                )
                scope_str = ",".join(expanded_tickers or uncovered_requested) or "universe"
                blindspots.append(
                    DeclaredBlindspotItem(
                        code="NO_DOC_COVERAGE",
                        reason=(
                            "No Point-in-Time documents, verified tables, or KG evidence available for "
                            f"{scope_str} as of {as_of_date}.{avail_hint}"
                        ),
                        scope=scope_str,
                    )
                )
            avail_suffix = (
                f" Các mã đã nạp trong hệ thống hiện có: {', '.join(sorted(available_tickers)[:20])}."
                if available_tickers
                else ""
            )
            scope_display = ", ".join(expanded_tickers or uncovered_requested) or "phạm vi yêu cầu"
            draft_answer = (
                "INSUFFICIENT_EVIDENCE: Không có tài liệu, bảng số hoặc đồ thị tri thức đủ độ phù hợp "
                f"tại thời điểm PIT {as_of_date.isoformat()} cho {scope_display} "
                f"để trả lời câu hỏi này.{avail_suffix}"
            )
            yield ("delta", {"text": draft_answer})
        else:
            messages = _build_synthesis_messages(
                query=resolved_query,
                primary_ticker=primary,
                expanded_tickers=expanded_tickers,
                as_of_date=as_of_date,
                sections=pit_sections,
                hydrated_tables=hydrated_tables,
                calculator_traces=calculator_traces,
                graph_hops=graph_hops,
                discrepancy_alerts=discrepancy_alerts,
                history=history_dicts,
                mode=chat_mode,
                blindspots=blindspots,
            )
            parts: list[str] = []
            if self._llm is not None and getattr(self._llm, "configured", True):
                with token_usage_scope(
                    component="research_chat_synthesis",
                    project_id=primary or "UNIVERSE",
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
        blocks_by_version = dict(all_blocks_by_version)
        if not blocks_by_version and self._session is not None and pit_sections:
            blocks_by_version = await load_canonical_blocks_for_versions(
                self._session,
                [s.document_version_id for s in pit_sections],
            )
        candidate_citations = _parse_citations_from_answer(
            draft_answer,
            pit_sections,
            blocks_by_version=blocks_by_version,
        )
        valid_cids = {f"C{i}" for i in range(1, len(pit_sections) + 1)}

        ver_res = verify_citations(
            candidate_citations,
            blocks_by_version,
            as_of_date,
            answer_text=draft_answer,
            hydrated_tables=[t.model_dump() for t in hydrated_tables],
            valid_citation_ids=valid_cids,
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
            chat_mode=chat_mode if chat_mode in ("GENERAL", "MOAT") else "GENERAL",  # type: ignore[arg-type]
            primary_ticker=primary,
            expanded_tickers=expanded_tickers,
            resolved_query=resolved_query,
            sub_queries=sub_queries,
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
                hipporag_hops=hipporag_hops,
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
