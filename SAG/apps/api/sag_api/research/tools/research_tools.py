"""Externalized 6-Tool Read-Only Bus for `/research-chat` (`sag_agent` compatible).

Every database-backed tool opens its OWN isolated short-lived `AsyncSession` via
`ctx.session_factory()` so `sag_agent.ToolExecutionMode.PARALLEL` (`asyncio.gather`)
can run multiple tools simultaneously with zero SQLAlchemy `AsyncSession` concurrency errors.
`as_of_date` and `principal` remain strictly server-locked inside `ResearchToolContext`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from sag_agent.tools import AgentTool, ToolContext, ToolRegistry, function_tool
from sag_agent.types import ToolExecutionMode, ToolResult, ToolRisk
from sag_api.research.calculator import CalculatorError, FinancialCalculator, FormatStyle
from sag_api.research.financial_metrics_store import (
    ALLOWED_SCREEN_METRICS,
    screen_universe_metrics,
)
from sag_api.research.kg_store import SQLAlchemyKGStore
from sag_api.research.tools.context import ResearchToolContext
from sag_api.services.reranker_client import rerank_and_grade_sections


def _require_ctx(tool_ctx: ToolContext) -> ResearchToolContext:
    data = tool_ctx.data
    if not isinstance(data, ResearchToolContext):
        raise RuntimeError("Research tool invoked without ResearchToolContext")
    return data


def _clamp_tickers(raw_tickers: Any, ctx: ResearchToolContext, *, max_count: int = 6) -> tuple[list[str], list[str]]:
    """Intersect LLM-requested tickers with PIT-authorized `available_tickers` + principal ACL."""
    if not isinstance(raw_tickers, (list, tuple)):
        return [], []
    allowed = ctx.principal.allowed_project_ids
    valid: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for item in raw_tickers:
        sym = str(item or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        if "*" not in allowed and sym not in allowed:
            unknown.append(sym)
            continue
        if ctx.available_tickers and sym not in ctx.available_tickers:
            unknown.append(sym)
            continue
        valid.append(sym)
        if len(valid) >= max_count:
            break
    return valid, unknown


def _format_table_as_compact_tsv(tbl: Mapping[str, Any], *, max_rows: int = 45) -> str:
    """Compact hydrated 2D table to TSV (~50% fewer tokens than JSON) while preserving `note_ref`."""
    tid = str(tbl.get("table_id") or "")
    tk = str(tbl.get("project_id") or "")
    title = str(tbl.get("title") or "Bảng tài chính")
    status = str(tbl.get("verification_status") or "TABLE_UNVERIFIED")
    unit_raw = str(tbl.get("unit_raw") or "")
    ccy = str(tbl.get("currency") or "VND")
    pfrom = tbl.get("page_from", 1)

    lines: list[str] = [
        f"[TBL:{tid}] ({tk}) {title} | trang {pfrom} | {ccy} {unit_raw} | {status}".strip()
    ]
    headers = tbl.get("headers") or []
    flat_hdrs = headers[0] if (headers and isinstance(headers[0], list)) else headers
    if flat_hdrs:
        lines.append("\t".join(str(h or "").strip() for h in flat_hdrs))

    rows = list(tbl.get("rows") or ())
    for row in rows[:max_rows]:
        if isinstance(row, Mapping):
            lbl = str(row.get("label") or "").strip()
            vals = row.get("values") or []
            cells = [lbl] + [
                "" if v is None else (f"{v:,.2f}" if isinstance(v, float) and not v.is_integer() else str(v))
                for v in vals
            ]
            lines.append("\t".join(cells))
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} dòng khác đã rút gọn)")
    footnotes = str(tbl.get("footnotes_text") or "").strip()
    if footnotes:
        lines.append(f"Ghi chú bảng: {footnotes[:320]}")
    return "\n".join(lines)


def _format_pct(val: Any) -> str:
    if isinstance(val, (int, float)):
        return f"{val * 100:.2f}%"
    return "-"


def _format_num(val: Any, suffix: str = "") -> str:
    if isinstance(val, (int, float)):
        return f"{val:,.2f}{suffix}"
    return "-"


# ── Tool 1: Quantitative Universe Screener (Core PG Read-Only PIT + PDF) ─────


@function_tool(
    name="screen_financial_universe",
    label="Sàng lọc cổ phiếu định lượng (Core PG PIT + BCTC)",
    description=(
        "Lọc và xếp hạng cổ phiếu trên toàn bộ danh mục PIT hợp lệ từ Core PostgreSQL (read-only) "
        "kết hợp lớp phủ BCTC PDF đã kiểm chứng. Dùng cho câu hỏi sàng lọc theo ngành, định giá "
        "(pe, pb, ev_ebitda, market_cap, fcf_yield), sinh lời (roe, roa, gross_margin, net_margin), "
        "đòn bẩy (debt_to_equity = nợ vay chịu lãi/VCSH, liabilities_to_equity = tổng nợ/VCSH), "
        "hoặc tăng trưởng (revenue_yoy, net_income_yoy, revenue_cagr_3y, cfo_to_net_income)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "sectors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tên ngành/phân ngành tiếng Việt cần lọc (ví dụ: ['Ngân hàng'], ['Thép'], ['Bán lẻ']).",
            },
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách mã cụ thể cần đối chiếu định lượng (để trống nếu lọc toàn bộ).",
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "op": {"type": "string", "enum": [">=", "<=", ">", "<", "=="]},
                        "value": {"type": "number"},
                    },
                    "required": ["metric", "op", "value"],
                },
                "description": (
                    "Bộ lọc định lượng. Lưu ý: các tỷ lệ phần trăm (roe, roa, gross_margin, net_margin, "
                    "revenue_yoy, net_income_yoy, fcf_yield) truyền dạng thập phân (ví dụ 15% = 0.15)."
                ),
            },
            "sort_by": {
                "type": "string",
                "description": "Chỉ tiêu sắp xếp chính (ví dụ: 'roe', 'pe', 'gross_margin').",
            },
            "sort_dir": {"type": "string", "enum": ["desc", "asc"], "default": "desc"},
            "use_default_quality_screen": {
                "type": "boolean",
                "default": False,
                "description": "Bật xếp hạng tổng hợp 4 trụ cột chất lượng khi người dùng hỏi cổ phiếu tốt/chất lượng.",
            },
            "prefer_annual": {"type": "boolean", "default": True},
            "scope": {
                "type": "string",
                "enum": ["sag_ingested", "core_universe"],
                "default": "sag_ingested",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        },
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=15.0,
)
async def screen_financial_universe_tool(
    arguments: Mapping[str, Any],
    tool_ctx: ToolContext,
) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()

    raw_sectors = [str(s).strip() for s in (arguments.get("sectors") or ()) if str(s).strip()]
    raw_tickers = [str(t).strip().upper() for t in (arguments.get("tickers") or ()) if str(t).strip()]
    raw_filters = [
        f
        for f in (arguments.get("filters") or ())
        if isinstance(f, Mapping) and str(f.get("metric") or "") in ALLOWED_SCREEN_METRICS
    ]
    sort_by = str(arguments.get("sort_by") or "").strip() or None
    if sort_by and sort_by not in ALLOWED_SCREEN_METRICS:
        sort_by = None
    sort_dir = "asc" if str(arguments.get("sort_dir") or "desc").lower() == "asc" else "desc"
    use_quality = bool(arguments.get("use_default_quality_screen", False))
    prefer_annual = bool(arguments.get("prefer_annual", True))
    scope = "core_universe" if str(arguments.get("scope") or "") == "core_universe" else "sag_ingested"
    limit = max(1, min(20, int(arguments.get("limit") or 10)))

    async with ctx.session_factory() as isolated_session:
        rows, total_matched, desc = await screen_universe_metrics(
            isolated_session,
            tenant_id=ctx.tenant_id,
            as_of_date=ctx.as_of_date,
            allowed_project_ids=ctx.principal.allowed_project_ids,
            core_reader=ctx.core_reader,
            scope=scope,
            sectors=raw_sectors or None,
            tickers=raw_tickers or None,
            filters=raw_filters or None,
            sort_by=sort_by,
            sort_dir=sort_dir,
            use_default_quality_screen=use_quality,
            prefer_annual=prefer_annual,
            limit=limit,
        )

    await ctx.evidence.register_screening(rows, desc)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await ctx.evidence.record_tool_trace(
        {
            "tool": "screen_financial_universe",
            "duration_ms": elapsed_ms,
            "matched_count": total_matched,
            "returned_count": len(rows),
            "sectors": raw_sectors,
            "sort_by": sort_by,
        }
    )

    if not rows:
        return ToolResult(
            content=f"Không có mã nào thỏa điều kiện lọc PIT ({desc}).",
            details={"matched_count": 0, "criteria": desc},
        )

    def _fmt_bn(val: Any) -> str:
        if isinstance(val, (int, float)):
            return f"{val / 1e9:,.1f}"
        return "-"

    header = (
        "Badge\tTicker\tFY\tNgành\tP/E\tP/B\tROE\tBiên gộp\tBiên ròng\t"
        "CFO/LNST\tFCF (tỷ)\tNợ vay/VCSH\tNợ ròng/VCSH\tTổng nợ/VCSH\tTiền+ĐSTC (tỷ)\tDT YoY\tNguồn"
    )
    lines = [
        f"Kết quả sàng lọc PIT (as_of_date={ctx.as_of_date.isoformat()}): {desc} (khớp {total_matched} mã).",
        header,
    ]
    for r in rows:
        tk = str(r.get("ticker") or "")
        fy = r.get("fiscal_year") or "-"
        fq = int(r.get("fiscal_quarter") or 0)
        period_lbl = f"Q{fq}/{fy}" if fq > 0 else f"FY{fy}"
        sec = str(r.get("sector") or "-")
        lines.append(
            "\t".join(
                [
                    f"[SCR:{tk}]",
                    tk,
                    period_lbl,
                    sec,
                    _format_num(r.get("pe"), "x"),
                    _format_num(r.get("pb"), "x"),
                    _format_pct(r.get("roe")),
                    _format_pct(r.get("gross_margin")),
                    _format_pct(r.get("net_margin")),
                    _format_num(r.get("cfo_to_net_income"), "x"),
                    _fmt_bn(r.get("fcf")),
                    _format_num(r.get("debt_to_equity"), "x"),
                    _format_num(r.get("net_debt_to_equity"), "x"),
                    _format_num(r.get("liabilities_to_equity"), "x"),
                    _fmt_bn(r.get("cash_and_st_investments")),
                    _format_pct(r.get("revenue_yoy")),
                    str(r.get("source_kind") or "CORE_DB_PIT"),
                ]
            )
        )
    return ToolResult(
        content="\n".join(lines),
        details={
            "matched_count": total_matched,
            "returned_count": len(rows),
            "criteria": desc,
            "duration_ms": elapsed_ms,
            "screening_preview": [
                {
                    "ticker": str(r.get("ticker") or ""),
                    "sector": str(r.get("sector") or ""),
                    "fiscal_year": r.get("fiscal_year"),
                    "fiscal_quarter": r.get("fiscal_quarter"),
                    "pe": r.get("pe"),
                    "pb": r.get("pb"),
                    "roe": r.get("roe"),
                    "gross_margin": r.get("gross_margin"),
                    "net_margin": r.get("net_margin"),
                    "cfo_to_net_income": r.get("cfo_to_net_income"),
                    "fcf": r.get("fcf"),
                    "net_debt_to_equity": r.get("net_debt_to_equity"),
                }
                for r in rows[:6]
            ],
        },
    )


# ── Tool 2: Push-Down Disclosure Evidence Search (`search_disclosures` + legacy alias) ─

DISCLOSURE_CHUNK_CHAR_LIMIT = 2000

CANONICAL_DISCLOSURE_SLOTS: frozenset[str] = frozenset(
    {"BCTC_AUDITED", "QUARTER_NOTES", "BCTN_CORE", "BCTN_PR", "AGM_TARGETS"}
)
_SLOT_ALIASES: dict[str, tuple[str, ...]] = {
    "BCTC_AUDITED": ("BCTC_AUDITED",),
    "BCTC": ("BCTC_AUDITED",),
    "AUDITED_FS": ("BCTC_AUDITED",),
    "QUARTER_NOTES": ("QUARTER_NOTES",),
    "LATEST_QUARTER": ("QUARTER_NOTES",),
    "QUARTER": ("QUARTER_NOTES",),
    "BCTN_CORE": ("BCTN_CORE",),
    "BCTN_PR": ("BCTN_PR",),
    "BCTN": ("BCTN_CORE", "BCTN_PR"),
    "ANNUAL_REPORT": ("BCTN_CORE", "BCTN_PR"),
    "AGM_TARGETS": ("AGM_TARGETS",),
    "AGM": ("AGM_TARGETS",),
    "DHDCD": ("AGM_TARGETS",),
}


def normalize_disclosure_slots(raw_slots: Any) -> list[str]:
    """Expand disclosure slot/doc_type filters into canonical Qdrant `sub_doc_slot` values (fail-closed)."""
    if not raw_slots:
        return []
    if isinstance(raw_slots, str):
        items = [raw_slots]
    elif isinstance(raw_slots, (list, tuple, set, frozenset)):
        items = list(raw_slots)
    else:
        raise ValueError("`sub_doc_slots` phải là mảng chuỗi hợp lệ.")
    expanded: list[str] = []
    for raw in items:
        key = str(raw or "").strip().upper()
        if not key:
            continue
        mapped = _SLOT_ALIASES.get(key)
        if mapped is None:
            valid_txt = ", ".join(sorted(CANONICAL_DISCLOSURE_SLOTS))
            raise ValueError(f"Loại tài liệu `{raw}` không hợp lệ. Chỉ chấp nhận: {valid_txt}.")
        for s in mapped:
            if s not in expanded:
                expanded.append(s)
    return expanded


async def _execute_disclosure_search(
    *,
    tool_name: str,
    ctx: ResearchToolContext,
    raw_tickers: Any,
    dense_queries: list[str],
    vas_terms: list[str],
    slots_filter: list[str],
    years_filter: list[int],
    quarters_filter: list[int],
    include_cross_ref: bool,
    top_k: int,
) -> ToolResult:
    from sag_api.research.chat_orchestrator import SAGRetrieverAdapter

    started = time.perf_counter()
    combined_queries = list(dict.fromkeys([*dense_queries, *vas_terms]))
    if not combined_queries:
        raise ValueError("Cần ít nhất câu hỏi truy vấn (`question`) hoặc thuật ngữ kế toán (`vas_terms`).")

    target_tickers, unknown_tks = _clamp_tickers(raw_tickers or [], ctx, max_count=6)
    if unknown_tks:
        async with ctx.evidence.lock:
            for u in unknown_tks:
                if u not in ctx.evidence.unknown_requested_tickers:
                    ctx.evidence.unknown_requested_tickers.append(u)

    if not target_tickers:
        if raw_tickers and unknown_tks:
            return ToolResult(
                content=(
                    f"STATUS=NOT_FOUND: Không có tài liệu BCTC PIT hợp lệ cho mã {', '.join(unknown_tks)} "
                    f"tại as_of_date={ctx.as_of_date.isoformat()}."
                ),
                details={
                    "status": "NOT_FOUND",
                    "crag_grade": "INCORRECT",
                    "returned_count": 0,
                    "unknown_tickers": unknown_tks,
                },
            )
        target_tickers = sorted(ctx.available_tickers)[:6]

    if not target_tickers:
        return ToolResult(
            content="STATUS=NOT_FOUND: Không có mã chứng khoán nào khả dụng trong phạm vi tài liệu của tổ chức.",
            details={"status": "NOT_FOUND", "crag_grade": "INCORRECT", "returned_count": 0},
        )

    recall_k = min(28, max(12, top_k * 3))
    raw_sections: list[Any] = []
    collected_blocks: dict[str, list[Any]] = {}
    hippo_hops = 0
    avail_cov: dict[str, list[str]] = {}
    degraded = False

    async with ctx.session_factory() as isolated_session:
        if ctx.retriever_override is not None:
            res = await ctx.retriever_override.retrieve(
                query=" | ".join(combined_queries),
                tickers=target_tickers,
                as_of_date=ctx.as_of_date,
                top_k=recall_k,
            )
            raw_sections.extend(res.sections)
            for vid, blks in (res.canonical_blocks or {}).items():
                collected_blocks.setdefault(str(vid), list(blks))
            hippo_hops += int(res.hipporag_hops or 0)
            if slots_filter:
                slot_set = set(slots_filter)
                raw_sections = [s for s in raw_sections if str(s.sub_doc_slot or "").upper() in slot_set]
            if years_filter:
                yr_set = set(years_filter)
                raw_sections = [s for s in raw_sections if s.fiscal_year in yr_set]
            if quarters_filter:
                q_set = set(quarters_filter)
                raw_sections = [s for s in raw_sections if s.fiscal_quarter in q_set]
        else:
            adapter = SAGRetrieverAdapter(isolated_session, engine_manager=ctx.engine_manager)
            res, disc_stats = await adapter.retrieve_disclosures(
                dense_queries=dense_queries,
                vas_terms=vas_terms,
                tickers=target_tickers,
                as_of_date=ctx.as_of_date,
                top_k=recall_k,
                principal=ctx.principal,
                sub_doc_slots=slots_filter or None,
                fiscal_years=years_filter or None,
                fiscal_quarters=quarters_filter or None,
                load_canonical_blocks=False,
            )
            raw_sections.extend(res.sections)
            for vid, blks in (res.canonical_blocks or {}).items():
                collected_blocks.setdefault(str(vid), list(blks))
            avail_cov = dict(disc_stats.get("available_coverage") or {})
            degraded = bool(disc_stats.get("degraded"))

            if include_cross_ref:
                xref_res = await adapter.retrieve_cross_ref(
                    query=dense_queries[0] if dense_queries else vas_terms[0],
                    target_tickers=target_tickers,
                    exclude_tickers=target_tickers,
                    as_of_date=ctx.as_of_date,
                    top_k=max(3, top_k // 2),
                    principal=ctx.principal,
                    load_canonical_blocks=False,
                )
                raw_sections.extend(xref_res.sections)
                for vid, blks in (xref_res.canonical_blocks or {}).items():
                    collected_blocks.setdefault(str(vid), list(blks))

    if not raw_sections:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await ctx.evidence.record_tool_trace(
            {
                "tool": tool_name,
                "duration_ms": elapsed_ms,
                "tickers": target_tickers,
                "queries": combined_queries,
                "sub_doc_slots": slots_filter,
                "fiscal_years": years_filter,
                "fiscal_quarters": quarters_filter,
                "recall_count": 0,
                "returned_count": 0,
                "status": "NOT_FOUND",
                "degraded": degraded,
            }
        )
        flt_desc_parts: list[str] = []
        if slots_filter:
            flt_desc_parts.append(f"sub_doc_slots={slots_filter}")
        if years_filter:
            flt_desc_parts.append(f"fiscal_years={years_filter}")
        if quarters_filter:
            flt_desc_parts.append(f"fiscal_quarters={quarters_filter}")
        flt_txt = f" khớp bộ lọc ({', '.join(flt_desc_parts)})" if flt_desc_parts else ""
        cov_lines = [f"{tk}: {', '.join(vals)}" for tk, vals in avail_cov.items() if vals]
        cov_txt = f" Các tài liệu đã công bố trong PIT: {'; '.join(cov_lines)}." if cov_lines else ""
        return ToolResult(
            content=(
                f"STATUS=NOT_FOUND: Không tìm thấy đoạn thuyết minh/tài liệu nào cho {', '.join(target_tickers)}"
                f"{flt_txt} tại PIT {ctx.as_of_date.isoformat()}.{cov_txt}"
            ),
            details={
                "status": "NOT_FOUND",
                "crag_grade": "INCORRECT",
                "returned_count": 0,
                "recall_count": 0,
                "duration_ms": elapsed_ms,
                "available_coverage": avail_cov,
                "degraded": degraded,
            },
        )

    rerank_query = dense_queries[0] if dense_queries else " ".join(vas_terms)
    rerank_out = await rerank_and_grade_sections(
        rerank_query,
        raw_sections,
        top_k=top_k,
        target_tickers=target_tickers,
        protect_table_cards=True,
        min_table_cards=1,
        reranker=ctx.reranker,
    )

    assigned = await ctx.evidence.register_sections(
        rerank_out.sections,
        canonical_blocks=collected_blocks,
        crag_grade=rerank_out.crag_grade,
        reranker_provider=rerank_out.reranker_provider,
        reranker_model=rerank_out.reranker_model,
        queries=combined_queries,
        hipporag_hops=hippo_hops,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await ctx.evidence.record_tool_trace(
        {
            "tool": tool_name,
            "duration_ms": elapsed_ms,
            "tickers": target_tickers,
            "queries": combined_queries,
            "sub_doc_slots": slots_filter,
            "fiscal_years": years_filter,
            "fiscal_quarters": quarters_filter,
            "recall_count": len(raw_sections),
            "returned_count": len(assigned),
            "crag_grade": rerank_out.crag_grade,
            "reranker_used": rerank_out.reranker_used,
            "domain_term_coverage": rerank_out.domain_term_coverage,
            "degraded": degraded,
        }
    )

    if not assigned:
        return ToolResult(
            content=(
                f"STATUS=NOT_FOUND | CRAG_GRADE=INCORRECT ({rerank_out.crag_reason}). Không tìm thấy đoạn văn bản "
                f"phù hợp cho {', '.join(target_tickers)}."
            ),
            details={"status": "NOT_FOUND", "crag_grade": "INCORRECT", "returned_count": 0},
        )

    table_hints: list[str] = []
    deg_tag = " | DEGRADED=PG_FALLBACK" if degraded else ""
    lines = [
        f"STATUS=OK | CRAG_GRADE={rerank_out.crag_grade} ({rerank_out.crag_reason}) | "
        f"domain_cov={rerank_out.domain_term_coverage:.2f} | "
        f"reranker={rerank_out.reranker_model or 'hybrid_rrf'}{deg_tag} | {len(assigned)} đoạn:"
    ]
    selected_documents: list[dict[str, Any]] = []
    for cid, sec in assigned:
        tid = getattr(sec, "table_id", None)
        if tid and str(tid) not in table_hints:
            table_hints.append(str(tid))
        origin = str(getattr(sec, "evidence_origin", "PRIMARY") or "PRIMARY")
        tk = str(getattr(sec, "ticker", "") or "")
        slot = str(getattr(sec, "sub_doc_slot", "") or "")
        fy = getattr(sec, "fiscal_year", None)
        fq = getattr(sec, "fiscal_quarter", None)
        period_tag = f" | Q{fq}/{fy}" if (fy and fq and fq > 0) else (f" | FY{fy}" if fy else "")
        page = getattr(sec, "page_from", 1)
        pub = getattr(sec, "published_date", "")
        heading = str(getattr(sec, "heading", "") or "").strip()
        body = str(getattr(sec, "content", "") or "").strip()[:DISCLOSURE_CHUNK_CHAR_LIMIT]
        tbl_tag = f" | table_id={tid}" if tid else ""
        lines.append(
            f"[{cid}] ({tk} | {origin} | {slot}{period_tag} | trang {page} | pub={pub}{tbl_tag}) {heading}\n{body}"
        )
        selected_documents.append(
            {
                "citation_id": cid,
                "ticker": tk,
                "sub_doc_slot": slot,
                "page": int(page or 1),
                "fiscal_year": fy,
                "fiscal_quarter": fq,
                "heading": heading[:120],
                "score": round(float(getattr(sec, "score", 0.0) or 0.0), 3),
                "evidence_origin": origin,
                "table_id": str(tid) if tid else None,
            }
        )

    if table_hints:
        lines.append(
            f"\nGợi ý bảng cấu trúc liên quan (có thể gọi `hydrate_financial_tables`): {', '.join(table_hints[:6])}"
        )
    return ToolResult(
        content="\n\n".join(lines),
        details={
            "status": "OK",
            "crag_grade": rerank_out.crag_grade,
            "crag_reason": rerank_out.crag_reason,
            "reranker_model": rerank_out.reranker_model,
            "reranker_used": rerank_out.reranker_used,
            "recall_count": len(raw_sections),
            "returned_count": len(assigned),
            "duration_ms": elapsed_ms,
            "tickers": target_tickers,
            "queries": combined_queries,
            "table_hints": table_hints,
            "selected_documents": selected_documents,
            "degraded": degraded,
        },
    )


@function_tool(
    name="search_disclosures",
    label="Tra cứu Thuyết minh BCTC, BCTN & ĐHĐCĐ (bộ lọc đẩy xuống Qdrant)",
    description=(
        "Truy vấn đoạn thuyết minh, chính sách kế toán, cơ cấu nợ xấu/danh mục tự doanh/tồn kho dự án, BCTN và "
        "tài liệu ĐHĐCĐ (khóa sổ PIT). Bộ lọc `sub_doc_slots`, `fiscal_year`, `fiscal_quarter` được đẩy thẳng "
        "xuống Qdrant (KHÔNG tự ý trả nhầm năm/quý khác; nếu trống trả `STATUS=NOT_FOUND` kèm danh sách kỳ sẵn có). "
        "Không dùng tool này để lấy số tổng doanh thu/lợi nhuận/ROE — hãy dùng `get_financial_statements` hoặc "
        "`get_sector_kpis` cho số liệu định lượng."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1..6 mã cổ phiếu mục tiêu.",
            },
            "question": {
                "type": "string",
                "description": "Câu hỏi ngữ nghĩa đầy đủ chủ thể + niên độ cần giải thích từ thuyết minh/tài liệu.",
            },
            "vas_terms": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
                "description": (
                    "1-5 cụm thuật ngữ chuẩn VAS TT200/TT49/TT210 hoặc mã thuyết minh "
                    "(vd: 'nợ nhóm 3', 'dự phòng rủi ro tín dụng', 'vốn khả dụng', 'V.06')."
                ),
            },
            "sub_doc_slots": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["BCTC_AUDITED", "QUARTER_NOTES", "BCTN_CORE", "BCTN_PR", "AGM_TARGETS"],
                },
                "description": "Tùy chọn khóa cứng loại tài liệu trong Qdrant.",
            },
            "fiscal_year": {"type": "integer", "description": "Tùy chọn khóa cứng năm tài chính (vd: 2025, 2026)."},
            "fiscal_quarter": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "description": "Tùy chọn khóa cứng quý tài chính (0 = cả năm, 1..4 = Quý 1..4).",
            },
            "include_cross_ref": {
                "type": "boolean",
                "default": False,
                "description": "Bật tìm kiếm chéo từ tài liệu đối tác/đối thủ nhắc tới mã mục tiêu.",
            },
            "top_k": {"type": "integer", "minimum": 3, "maximum": 12, "default": 6},
        },
        "required": ["question", "vas_terms"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=25.0,
)
async def search_disclosures_tool(
    arguments: Mapping[str, Any],
    tool_ctx: ToolContext,
) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    question = str(arguments.get("question") or "").strip()
    vas_terms = [str(t).strip() for t in (arguments.get("vas_terms") or ()) if str(t).strip()][:5]
    raw_slots = arguments.get("sub_doc_slots") or arguments.get("doc_types")
    slots = normalize_disclosure_slots(raw_slots)
    fy = arguments.get("fiscal_year")
    fq = arguments.get("fiscal_quarter")
    return await _execute_disclosure_search(
        tool_name="search_disclosures",
        ctx=ctx,
        raw_tickers=arguments.get("tickers"),
        dense_queries=[question] if question else [],
        vas_terms=vas_terms,
        slots_filter=slots,
        years_filter=[int(fy)] if isinstance(fy, int) else [],
        quarters_filter=[int(fq)] if isinstance(fq, int) else [],
        include_cross_ref=bool(arguments.get("include_cross_ref", False)),
        top_k=max(3, min(12, int(arguments.get("top_k") or 6))),
    )


@function_tool(
    name="search_corporate_filings",
    label="Tra cứu BCTC, Thuyết minh, BCTN & ĐHĐCĐ (Hybrid + Rerank + CRAG)",
    description=(
        "Tìm kiếm đoạn văn bản gốc và thẻ bảng trong BCTC kiểm toán, Thuyết minh, BCTN, Nghị quyết ĐHĐCĐ "
        "(khóa sổ PIT) với truy vấn 2 thanh ghi `bm25_vas_queries` + `dense_semantic_queries` và bộ lọc đẩy xuống."
    ),
    parameters={
        "type": "object",
        "properties": {
            "bm25_vas_queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 5,
                "description": "1-5 cụm từ khóa kế toán chuẩn VAS/TT200/TT49/TT210 hoặc mã thuyết minh (ví dụ 'V.20').",
            },
            "dense_semantic_queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
                "description": "1-3 câu truy vấn ngữ nghĩa độc lập, đã giải đại từ và ghi rõ mã cổ phiếu + niên độ.",
            },
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách mã mục tiêu (1..6 mã). Để trống nếu tìm trên toàn bộ danh mục.",
            },
            "sub_doc_slots": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tùy chọn lọc loại tài liệu (BCTC_AUDITED, BCTN_CORE, AGM_TARGETS, QUARTER_NOTES).",
            },
            "fiscal_years": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Tùy chọn lọc năm tài chính cụ thể (ví dụ [2024, 2025]).",
            },
            "fiscal_quarters": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Tùy chọn lọc quý tài chính (0 = năm, 1..4 = Q1..Q4).",
            },
            "include_cross_ref": {
                "type": "boolean",
                "default": False,
                "description": "Bật tìm kiếm chéo từ BCTC của đối tác/đối thủ nhắc tới mã mục tiêu.",
            },
            "top_k": {"type": "integer", "minimum": 3, "maximum": 12, "default": 6},
        },
        "required": ["bm25_vas_queries", "dense_semantic_queries"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=25.0,
)
async def search_corporate_filings_tool(
    arguments: Mapping[str, Any],
    tool_ctx: ToolContext,
) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    bm25_qs = [str(q).strip() for q in (arguments.get("bm25_vas_queries") or ()) if str(q).strip()][:5]
    dense_qs = [str(q).strip() for q in (arguments.get("dense_semantic_queries") or ()) if str(q).strip()][:3]
    slots = normalize_disclosure_slots(arguments.get("sub_doc_slots"))
    years = [int(y) for y in (arguments.get("fiscal_years") or ()) if isinstance(y, int)]
    quarters = [int(q) for q in (arguments.get("fiscal_quarters") or ()) if isinstance(q, int)]
    return await _execute_disclosure_search(
        tool_name="search_corporate_filings",
        ctx=ctx,
        raw_tickers=arguments.get("tickers"),
        dense_queries=dense_qs,
        vas_terms=bm25_qs,
        slots_filter=slots,
        years_filter=years,
        quarters_filter=quarters,
        include_cross_ref=bool(arguments.get("include_cross_ref", False)),
        top_k=max(3, min(12, int(arguments.get("top_k") or 6))),
    )


# ── Tool 3: Hydrate 2D Verified Financial Tables (Preserves Note Refs V.xx) ──


@function_tool(
    name="hydrate_financial_tables",
    label="Nạp ma trận bảng tài chính 2D (giữ nguyên mã Thuyết minh V.xx)",
    description=(
        "Nạp toàn bộ dòng và cột của bảng tài chính đã bóc tách (CĐKT, KQKD, LCTT, Thuyết minh) khóa sổ PIT, "
        "kèm trạng thái VERIFIED/TABLE_UNVERIFIED và giữ nguyên cột mã Thuyết minh (V.01, V.20...) để nhảy "
        "tiếp sang đọc thuyết minh chi tiết."
    ),
    parameters={
        "type": "object",
        "properties": {
            "table_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách `table_id` lấy được từ `search_corporate_filings`.",
            },
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách mã cổ phiếu cần lấy bảng tài chính chính (1..6 mã).",
            },
            "statement_kind": {
                "type": "string",
                "enum": ["BS", "IS", "CF", "NOTES", "ALL"],
                "default": "ALL",
            },
            "fiscal_year": {"type": "integer", "description": "Tùy chọn lọc theo năm tài chính."},
            "fiscal_quarter": {
                "type": "integer",
                "minimum": 0,
                "maximum": 4,
                "description": "Tùy chọn lọc theo quý tài chính (0 = cả năm, 1..4 = Quý 1..4).",
            },
            "sub_doc_slot": {
                "type": "string",
                "description": "Tùy chọn lọc slot tài liệu (BCTC_AUDITED, QUARTER_NOTES, AGM_TARGETS, BCTN_CORE).",
            },
        },
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=12.0,
)
async def hydrate_financial_tables_tool(
    arguments: Mapping[str, Any],
    tool_ctx: ToolContext,
) -> ToolResult:
    from sag_api.research.chat_orchestrator import SQLAlchemyTableStore

    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()

    table_ids = [str(tid).strip() for tid in (arguments.get("table_ids") or ()) if str(tid).strip()][:12]
    target_tickers, _unknown = _clamp_tickers(arguments.get("tickers") or [], ctx, max_count=6)
    stmt_kind = str(arguments.get("statement_kind") or "ALL").strip().upper()
    fiscal_year = arguments.get("fiscal_year")
    fy_filter = int(fiscal_year) if isinstance(fiscal_year, int) else None
    fiscal_quarter = arguments.get("fiscal_quarter")
    fq_filter = int(fiscal_quarter) if isinstance(fiscal_quarter, int) else None
    slot_raw = str(arguments.get("sub_doc_slot") or "").strip().upper() or None

    if not table_ids and not target_tickers:
        return ToolResult(content="Cần truyền ít nhất `table_ids` hoặc `tickers` để nạp bảng tài chính.")

    async with ctx.session_factory() as isolated_session:
        store = ctx.table_store_override or SQLAlchemyTableStore(isolated_session)
        tables = await store.get_tables(
            table_ids=table_ids,
            tickers=target_tickers,
            as_of_date=ctx.as_of_date,
            tenant_id=ctx.tenant_id,
            fiscal_year=fy_filter,
            fiscal_quarter=fq_filter,
            sub_doc_slot=slot_raw,
        )

    if fy_filter is not None:
        tables = [
            t
            for t in tables
            if t.get("fiscal_year") in (None, fy_filter)
            and (
                t.get("fiscal_year") == fy_filter
                or str(fy_filter) in str(t.get("title") or "")
                or any(str(fy_filter) in str(h) for h in (t.get("headers") or ()))
            )
        ]
    if fq_filter is not None:
        tables = [t for t in tables if t.get("fiscal_quarter") == fq_filter]

    if stmt_kind != "ALL":
        kind_kw = {
            "BS": ("cân đối", "tài sản", "nguồn vốn", "balance sheet", "tình hình tài chính"),
            "IS": ("kết quả", "doanh thu", "lợi nhuận", "income statement", "thu nhập"),
            "CF": ("lưu chuyển", "tiền tệ", "cash flow"),
            "NOTES": ("thuyết minh", "chi tiết", "notes"),
        }.get(stmt_kind, ())
        if kind_kw:
            kind_matched = [
                t
                for t in tables
                if any(kw in str(t.get("title") or "").lower() for kw in kind_kw)
            ]
            if kind_matched:
                tables = kind_matched

    registered = await ctx.evidence.register_tables(tables[:6])
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await ctx.evidence.record_tool_trace(
        {
            "tool": "hydrate_financial_tables",
            "duration_ms": elapsed_ms,
            "tickers": target_tickers,
            "fiscal_year": fy_filter,
            "fiscal_quarter": fq_filter,
            "returned_count": len(registered),
        }
    )

    if not registered:
        return ToolResult(
            content="STATUS=NOT_FOUND: Không tìm thấy bảng tài chính PIT hợp lệ khớp bộ lọc yêu cầu.",
            details={"status": "NOT_FOUND", "returned_count": 0, "duration_ms": elapsed_ms, "table_ids": []},
        )

    blocks = [_format_table_as_compact_tsv(tbl) for tbl in registered]
    return ToolResult(
        content="\n\n".join(blocks),
        details={
            "returned_count": len(registered),
            "duration_ms": elapsed_ms,
            "table_ids": [t["table_id"] for t in registered],
            "hydrated_tables": [
                {
                    "table_id": str(t.get("table_id") or ""),
                    "ticker": str(t.get("project_id") or ""),
                    "title": str(t.get("title") or "Bảng tài chính")[:100],
                    "sub_doc_slot": str(t.get("sub_doc_slot") or ""),
                    "page": int(t.get("page_from") or 0),
                    "verification_status": str(t.get("verification_status") or "TABLE_UNVERIFIED"),
                    "rows_count": len(t.get("rows") or ()),
                    "unit_raw": str(t.get("unit_raw") or ""),
                }
                for t in registered
            ],
        },
    )


# ── Tool 4: 3-Layer PIT Knowledge Graph Query ────────────────────────────────


@function_tool(
    name="query_knowledge_graph",
    label="Truy vấn Đồ thị Tri thức 3 tầng (Vĩ mô, Chuỗi giá trị, Đối thủ)",
    description=(
        "Truy vấn đồ thị tri thức khóa sổ PIT gồm 3 tầng: MACRO (độ nhạy tỷ giá/lãi suất/hàng hóa đã kiểm định "
        "HAC+FDR và đối chiếu thuyết minh kiểm toán), VALUE_CHAIN (nhà cung cấp/khách hàng/liên kết ngành), "
        "và TRUE_PEER (đối thủ thực chất). Dùng theo danh sách mã `tickers` hoặc theo yếu tố vĩ mô `macro_factor`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Mã cổ phiếu cần tra cứu chuỗi giá trị, đối thủ và độ nhạy vĩ mô.",
            },
            "macro_factor": {
                "type": "string",
                "description": (
                    "Tên yếu tố vĩ mô cần tìm danh sách cổ phiếu nhạy cảm "
                    "(ví dụ 'usd_vnd', 'interest_rate', 'brent')."
                ),
            },
            "direction": {
                "type": "string",
                "enum": ["any", "positive", "negative"],
                "default": "any",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        },
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=12.0,
)
async def query_knowledge_graph_tool(
    arguments: Mapping[str, Any],
    tool_ctx: ToolContext,
) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()

    if not ctx.enable_kg:
        return ToolResult(content="Đồ thị Tri thức (KG) đang tắt trong cấu hình phiên hiện tại.")

    target_tickers, _unknown = _clamp_tickers(arguments.get("tickers") or [], ctx, max_count=6)
    macro_factor = str(arguments.get("macro_factor") or "").strip()
    direction_str = str(arguments.get("direction") or "any").strip().lower()
    direction_filter = 1 if direction_str == "positive" else (-1 if direction_str == "negative" else None)
    limit = max(1, min(20, int(arguments.get("limit") or 10)))

    allowed_scope = ctx.available_tickers or ctx.principal.allowed_project_ids
    new_hops: list[dict[str, Any]] = []
    lines: list[str] = []

    async with ctx.session_factory() as isolated_session:
        store = ctx.kg_store_override or SQLAlchemyKGStore(isolated_session)

        for tk in target_tickers:
            sub = await store.get_subgraph_for_ticker(tk, ctx.as_of_date)
            for edge in (sub.get("edges") or ())[:limit]:
                layer = str(edge.get("layer") or "VALUE_CHAIN")
                tier = str(edge.get("verification_tier") or "DOC_ONLY")
                hop = {
                    "layer": layer if layer in {"MACRO", "VALUE_CHAIN", "TRUE_PEER"} else "VALUE_CHAIN",
                    "src_label": str(edge.get("src_node_id") or ""),
                    "relation_type": str(edge.get("relation_type") or ""),
                    "dst_label": str(edge.get("dst_node_id") or ""),
                    "beta": edge.get("beta"),
                    "p_value": edge.get("p_value"),
                    "fdr_q_value": edge.get("fdr_q_value"),
                    "tercile_stable": edge.get("tercile_stable"),
                    "verification_tier": tier,
                    "effective_from": str(edge.get("effective_from") or ctx.as_of_date.isoformat()),
                }
                new_hops.append(hop)
                beta_str = f" | beta={hop['beta']:.3f}" if isinstance(hop["beta"], (int, float)) else ""
                p_str = f" | p={hop['p_value']:.4f}" if isinstance(hop["p_value"], (int, float)) else ""
                lines.append(
                    f"[KG:{hop['layer']}|{tier}] {hop['src_label']} --{hop['relation_type']}--> "
                    f"{hop['dst_label']}{beta_str}{p_str} (eff={hop['effective_from']})"
                )
            for snap in (sub.get("macro_snapshots") or ())[:limit]:
                tier = str(snap.get("verification_tier") or "QUANT_ONLY")
                hop = {
                    "layer": "MACRO",
                    "src_label": f"MACRO:{snap.get('macro_factor', '')}",
                    "relation_type": "MACRO_SENSITIVE_TO",
                    "dst_label": str(snap.get("target_id") or f"TICKER:{tk}"),
                    "beta": snap.get("beta"),
                    "p_value": snap.get("p_value"),
                    "fdr_q_value": snap.get("fdr_q_value"),
                    "tercile_stable": snap.get("tercile_stable"),
                    "verification_tier": tier,
                    "effective_from": str(snap.get("effective_from") or ctx.as_of_date.isoformat()),
                }
                new_hops.append(hop)
                lines.append(
                    f"[KG:MACRO|{tier}] {hop['src_label']} -> {hop['dst_label']} "
                    f"(beta={hop['beta']}, p={hop['p_value']}, stance={snap.get('doc_stance')})"
                )

        if macro_factor and hasattr(store, "get_tickers_by_macro_factor"):
            mf_rows = await store.get_tickers_by_macro_factor(
                macro_factor,
                ctx.as_of_date,
                allowed_tickers=allowed_scope,
                direction_filter=direction_filter,
                limit=limit,
            )
            for row in mf_rows:
                tier = str(row.get("verification_tier") or "QUANT_ONLY")
                hop = {
                    "layer": "MACRO",
                    "src_label": f"MACRO:{row['macro_factor']}",
                    "relation_type": "MACRO_SENSITIVE_TO",
                    "dst_label": f"TICKER:{row['ticker']}",
                    "beta": row.get("beta"),
                    "p_value": row.get("p_value"),
                    "fdr_q_value": row.get("fdr_q_value"),
                    "tercile_stable": None,
                    "verification_tier": tier,
                    "effective_from": str(row.get("effective_from") or ctx.as_of_date.isoformat()),
                }
                new_hops.append(hop)
                dir_sign = row.get("direction_sign", 0)
                sign_lbl = "+" if dir_sign > 0 else ("-" if dir_sign < 0 else "0")
                lines.append(
                    f"[KG:MACRO|{tier}] {row['ticker']} nhạy {sign_lbl} với {row['macro_factor']} "
                    f"(beta={row.get('beta')}, p={row.get('p_value')}, eff={hop['effective_from']})"
                )

    added = await ctx.evidence.register_graph_hops(new_hops)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await ctx.evidence.record_tool_trace(
        {
            "tool": "query_knowledge_graph",
            "duration_ms": elapsed_ms,
            "tickers": target_tickers,
            "macro_factor": macro_factor or None,
            "returned_hops": len(added),
        }
    )

    if not lines:
        return ToolResult(
            content="Không có cạnh Đồ thị Tri thức PIT hợp lệ cho truy vấn này.",
            details={"returned_hops": 0, "duration_ms": elapsed_ms, "kg_hops": []},
        )
    return ToolResult(
        content="\n".join(lines[:30]),
        details={
            "returned_hops": len(added),
            "duration_ms": elapsed_ms,
            "kg_hops": [
                {
                    "layer": str(h.get("layer") or "VALUE_CHAIN"),
                    "src_label": str(h.get("src_label") or ""),
                    "relation_type": str(h.get("relation_type") or ""),
                    "dst_label": str(h.get("dst_label") or ""),
                    "beta": h.get("beta"),
                    "verification_tier": str(h.get("verification_tier") or "DOC_ONLY"),
                }
                for h in added[:8]
            ],
        },
    )


# ── Tool 5: Deterministic AST Financial Calculator (Zero Mental Math) ────────


@function_tool(
    name="run_financial_calculator",
    label="Máy tính tài chính tất định (AST Sandbox)",
    description=(
        "Tính toán chính xác bằng AST Python an toàn (cấm nhẩm tay): hỗ trợ các hàm `yoy(curr, prev)`, "
        "`cagr(end, start, years)`, `margin(profit, rev)`, `roe(ni, eq)`, `roa(ni, assets)`, "
        "`debt_to_equity(debt, eq)`, `safe_div(a, b)`, `bps_delta(new_ratio, old_ratio)` và biểu thức số học. "
        "Khóa lệch tiền tệ và từ chối bảng `TABLE_UNVERIFIED`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "formula": {
                "type": "string",
                "description": (
                    "Biểu thức công thức an toàn (ví dụ: 'yoy(rev_2025, rev_2024)' "
                    "hoặc '(debt - cash) / equity')."
                ),
            },
            "inputs": {
                "type": "object",
                "description": "Từ điển biến đầu vào {tên_biến: số hoặc {value, table_id, currency, unit_multiplier}}.",
            },
            "description": {"type": "string", "description": "Mô tả ngắn chỉ tiêu đang tính."},
            "format_style": {
                "type": "string",
                "enum": ["number", "percent", "signed_percent", "bps", "ratio"],
                "default": "number",
            },
            "ticker": {"type": "string", "description": "Mã cổ phiếu sở hữu phép tính (phục vụ kiểm chứng số học)."},
        },
        "required": ["formula", "inputs", "description"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=5.0,
)
async def run_financial_calculator_tool(
    arguments: Mapping[str, Any],
    tool_ctx: ToolContext,
) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()

    formula = str(arguments.get("formula") or "").strip()
    raw_inputs = arguments.get("inputs")
    description = str(arguments.get("description") or formula).strip()
    fmt_raw = str(arguments.get("format_style") or "number").strip().lower()
    fmt_style: FormatStyle = (
        fmt_raw if fmt_raw in {"number", "percent", "signed_percent", "bps", "ratio"} else "number"  # type: ignore[assignment]
    )
    owner_ticker = str(arguments.get("ticker") or "").strip().upper() or None

    if not isinstance(raw_inputs, Mapping) or not raw_inputs:
        raise ValueError("Lỗi máy tính: tham số `inputs` phải là object chứa ít nhất 1 biến số.")

    calc = FinancialCalculator()
    try:
        trace = calc.evaluate(
            formula=formula,
            inputs=dict(raw_inputs),
            description=description,
            format_style=fmt_style,
        )
    except CalculatorError as exc:
        raise ValueError(f"Máy tính từ chối công thức (fail-closed): {exc}") from exc

    trace_dict = trace.to_dict()
    if owner_ticker:
        trace_dict["ticker"] = owner_ticker
    trace_dict["format_style"] = fmt_style
    saved = await ctx.evidence.register_calculator_trace(trace_dict)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await ctx.evidence.record_tool_trace(
        {
            "tool": "run_financial_calculator",
            "duration_ms": elapsed_ms,
            "calc_id": saved["calc_id"],
            "formula": formula,
        }
    )
    return ToolResult(
        content=(
            f"[{saved['calc_id']}] {description}: {saved['formatted']} "
            f"(formula=`{formula}`, inputs={json.dumps(saved['inputs'], ensure_ascii=False)})"
        ),
        details=saved,
    )


# ── Tool 6: On-Demand PIT-Safe Session & User Memory Recall (5 Scopes) ───────


@function_tool(
    name="recall_session_memory",
    label="Tra cứu lại ngữ cảnh phiên, ký ức dài hạn, phiên liên kết & danh mục (PIT-safe)",
    description=(
        "Truy xuất ký ức theo 5 phạm vi đã qua khóa cách ly PIT (0 rò rỉ dữ liệu tương lai):\n"
        "- `current_session` (mặc định): nguyên văn lượt hỏi-đáp hoặc bảng lọc cũ trong phiên hiện tại.\n"
        "- `cross_session_memories`: sở thích, tiêu chí đầu tư, vị thế ngoài & luận điểm đã lưu qua các phiên.\n"
        "- `linked_sessions`: các phiên phân tích trước có liên kết mã/ngành/nhân tố vĩ mô.\n"
        "- `thesis_lineage`: lịch sử diễn tiến luận điểm đầu tư theo thời gian cho một mã.\n"
        "- `my_portfolio`: danh mục nền tảng live (`[PORT:<SYM>]`) + vị thế ngoài (`[MEM:M#]`, tắt khi backtest)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": [
                    "current_session",
                    "cross_session_memories",
                    "linked_sessions",
                    "thesis_lineage",
                    "my_portfolio",
                ],
                "default": "current_session",
            },
            "query": {"type": "string"},
            "tickers": {"type": "array", "items": {"type": "string"}},
            "query_terms": {"type": "array", "items": {"type": "string"}},
            "turn_indices": {"type": "array", "items": {"type": "integer"}},
            "include_screening": {"type": "boolean", "default": True},
            "limit": {"type": "integer", "minimum": 1, "maximum": 6, "default": 4},
        },
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=8.0,
)
async def recall_session_memory_tool(
    arguments: Mapping[str, Any],
    tool_ctx: ToolContext,
) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()
    scope = str(arguments.get("scope") or "current_session").strip().lower()
    limit = max(1, min(6, int(arguments.get("limit") or 4)))
    tickers = [str(t).strip().upper() for t in (arguments.get("tickers") or ()) if str(t).strip()]
    query_terms = [str(q).strip() for q in (arguments.get("query_terms") or ()) if str(q).strip()]
    q_text = str(arguments.get("query") or " ".join(query_terms) or " ".join(tickers)).strip()

    if scope == "current_session":
        recalled = ctx.session_ledger.recall(
            tickers=tickers or None,
            query_terms=query_terms or None,
            turn_indices=arguments.get("turn_indices") or None,
            include_screening=bool(arguments.get("include_screening", True)),
            limit=limit,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await ctx.evidence.record_tool_trace(
            {
                "tool": "recall_session_memory",
                "scope": "current_session",
                "duration_ms": elapsed_ms,
                "recalled_turns": len(recalled.get("recalled_turns") or ()),
            }
        )
        return ToolResult(
            content=json.dumps(recalled, ensure_ascii=False, indent=2),
            details={"scope": "current_session", "recalled_turns": len(recalled.get("recalled_turns") or ())},
        )

    if ctx.memory_mode == "incognito":
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await ctx.evidence.record_tool_trace(
            {"tool": "recall_session_memory", "scope": scope, "duration_ms": elapsed_ms, "incognito": True}
        )
        return ToolResult(
            content=json.dumps(
                {
                    "scope": scope,
                    "incognito": True,
                    "message": "Phiên đang ở chế độ ẩn danh (incognito); tắt toàn bộ truy xuất ký ức và danh mục.",
                },
                ensure_ascii=False,
            ),
            details={"scope": scope, "incognito": True},
        )

    from sqlalchemy import or_, select

    from sag_api.db.models.user_memory import (
        INF_DATETIME,
        UserMemoryEdge,
        UserMemoryEntity,
        UserMemoryEpisode,
        UserMemoryItem,
    )
    from sag_api.research.user_memory_pipeline import is_backtest_date
    from sag_api.research.user_memory_read import _format_portfolio_items, smart_read_user_memories

    is_bt = is_backtest_date(ctx.as_of_date)
    mem_enabled = str(ctx.user_memory_mode or "off").strip().lower() == "on"

    if scope in ("cross_session_memories", "linked_sessions", "thesis_lineage") and not mem_enabled:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await ctx.evidence.record_tool_trace(
            {
                "tool": "recall_session_memory",
                "scope": scope,
                "duration_ms": elapsed_ms,
                "user_memory_mode": ctx.user_memory_mode,
                "count": 0,
            }
        )
        return ToolResult(
            content=json.dumps(
                {
                    "scope": scope,
                    "user_memory_mode": ctx.user_memory_mode,
                    "items": [],
                    "message": (
                        f"Ký ức dài hạn đang ở chế độ `{ctx.user_memory_mode}`; "
                        "không tiêm ký ức lưu trữ vào ngữ cảnh trả lời."
                    ),
                },
                ensure_ascii=False,
            ),
            details={"scope": scope, "user_memory_mode": ctx.user_memory_mode, "count": 0},
        )

    if scope == "cross_session_memories":
        q_client = None
        embedder = None
        if ctx.engine_manager is not None:
            get_q = getattr(ctx.engine_manager, "get_search_unit_qdrant_client", None)
            if callable(get_q):
                try:
                    q_client = await get_q()
                except Exception:  # noqa: BLE001
                    q_client = None
            get_emb = getattr(ctx.engine_manager, "get_sag_embedding", None)
            if callable(get_emb):
                try:
                    embedder = await get_emb()
                except Exception:  # noqa: BLE001
                    embedder = None

        async with ctx.session_factory() as db_sess:
            sr = await smart_read_user_memories(
                db_sess,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                query=q_text or " ".join(tickers),
                as_of_date=ctx.as_of_date,
                memory_recall_need="cross_session_graph",
                explicit_tickers=tickers,
                current_session_id=ctx.current_session_id,
                memory_mode=ctx.memory_mode,
                user_memory_mode="on",
                qdrant_client=q_client,
                embedder=embedder,
                limit=limit,
            )
        all_mems = [*sr.pinned_memories, *sr.recalled_memories][:limit]
        registered_mems = await ctx.evidence.register_memories(all_mems)
        registered_sess = await ctx.evidence.register_linked_sessions(sr.linked_sessions[:2])
        payload = {
            "scope": scope,
            "as_of_date": ctx.as_of_date.isoformat(),
            "is_backtest": is_bt,
            "memories": [
                {
                    "badge": f"[MEM:{m['badge_id']}]",
                    "memory_type": m["memory_type"],
                    "content_text": m["content_text"],
                    "tickers": m.get("tickers", []),
                    "source_as_of_date": m.get("source_as_of_date"),
                    "staleness_status": m.get("staleness_status", "FRESH"),
                    "stale_warning": (
                        f"[CẦN ĐỐI CHIẾU LẠI: {m.get('staleness_status')}]"
                        if m.get("staleness_status", "FRESH") != "FRESH"
                        else None
                    ),
                }
                for m in registered_mems
            ],
            "linked_sessions": [
                {
                    "badge": f"[SESS:{s['badge_id']}]",
                    "session_id": s["session_id"],
                    "episode_summary": s["episode_summary"],
                    "tickers": s.get("tickers", []),
                    "macro_factors": s.get("macro_factors", []),
                    "staleness_status": s.get("staleness_status", "FRESH"),
                }
                for s in registered_sess
            ],
        }
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await ctx.evidence.record_tool_trace(
            {"tool": "recall_session_memory", "scope": scope, "duration_ms": elapsed_ms, "count": len(registered_mems)}
        )
        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            details={"scope": scope, "count": len(registered_mems)},
        )

    if scope == "linked_sessions":
        matched_eps: list[dict[str, Any]] = []
        seen_sids: set[str] = set()
        if ctx.current_session_id:
            seen_sids.add(ctx.current_session_id)

        async with ctx.session_factory() as db_sess:
            # 1) Prioritize W4 personal-graph session edges (catches Macro/Sector bridges like HPG <-> VHC!)
            if ctx.current_session_id:
                cur_ent = await db_sess.scalar(
                    select(UserMemoryEntity).where(
                        UserMemoryEntity.tenant_id == ctx.tenant_id,
                        UserMemoryEntity.user_id == ctx.user_id,
                        UserMemoryEntity.canonical_key == f"SESSION:{ctx.current_session_id}",
                    )
                )
                if cur_ent is not None:
                    link_rels = ("SESSION_CONTINUED_FROM", "SESSION_THESIS_EVOLUTION", "SESSION_RELATED_THEME")
                    link_edges = (
                        await db_sess.scalars(
                            select(UserMemoryEdge)
                            .where(
                                UserMemoryEdge.tenant_id == ctx.tenant_id,
                                UserMemoryEdge.user_id == ctx.user_id,
                                UserMemoryEdge.relation_type.in_(link_rels),
                                UserMemoryEdge.is_backtest_sandbox.is_(is_bt),
                                UserMemoryEdge.source_as_of_date <= ctx.as_of_date,
                                UserMemoryEdge.valid_to >= INF_DATETIME,
                                UserMemoryEdge.tx_to >= INF_DATETIME,
                                or_(
                                    UserMemoryEdge.src_entity_id == cur_ent.entity_id,
                                    UserMemoryEdge.dst_entity_id == cur_ent.entity_id,
                                ),
                            )
                            .order_by(UserMemoryEdge.weight.desc(), UserMemoryEdge.created_at.desc())
                            .limit(limit * 2)
                        )
                    ).all()
                    other_eids = [
                        e.dst_entity_id if e.src_entity_id == cur_ent.entity_id else e.src_entity_id
                        for e in link_edges
                    ]
                    if other_eids:
                        other_ents = {
                            ent.entity_id: ent
                            for ent in (
                                await db_sess.scalars(
                                    select(UserMemoryEntity).where(
                                        UserMemoryEntity.entity_id.in_(other_eids),
                                        UserMemoryEntity.tenant_id == ctx.tenant_id,
                                        UserMemoryEntity.user_id == ctx.user_id,
                                    )
                                )
                            ).all()
                        }
                        linked_sids = [
                            ent.canonical_key.split(":", 1)[1]
                            for ent in other_ents.values()
                            if ent.canonical_key.startswith("SESSION:")
                        ]
                        linked_eps_by_sid = (
                            {
                                ep.external_session_id: ep
                                for ep in (
                                    await db_sess.scalars(
                                        select(UserMemoryEpisode).where(
                                            UserMemoryEpisode.tenant_id == ctx.tenant_id,
                                            UserMemoryEpisode.user_id == ctx.user_id,
                                            UserMemoryEpisode.external_session_id.in_(linked_sids),
                                            UserMemoryEpisode.status == "ACTIVE",
                                            UserMemoryEpisode.is_backtest_sandbox.is_(is_bt),
                                            UserMemoryEpisode.source_as_of_date <= ctx.as_of_date,
                                        )
                                    )
                                ).all()
                            }
                            if linked_sids
                            else {}
                        )
                        for edge in link_edges:
                            oid = edge.dst_entity_id if edge.src_entity_id == cur_ent.entity_id else edge.src_entity_id
                            ent = other_ents.get(oid)
                            if ent is None or not ent.canonical_key.startswith("SESSION:"):
                                continue
                            sid = ent.canonical_key.split(":", 1)[1]
                            if sid in seen_sids:
                                continue
                            ep = linked_eps_by_sid.get(sid)
                            if ep is None:
                                continue
                            seen_sids.add(sid)
                            matched_eps.append(
                                {
                                    "episode_id": ep.episode_id,
                                    "session_id": ep.external_session_id,
                                    "session_title": ep.session_title,
                                    "episode_summary": ep.episode_summary,
                                    "tickers": list(ep.tickers_json or []),
                                    "macro_factors": list(ep.macro_factors_json or []),
                                    "sectors": list(ep.sectors_json or []),
                                    "relation_type": str(edge.relation_type),
                                    "link_score": round(float(edge.weight or 0.0), 4),
                                    "source_as_of_date": ep.source_as_of_date.isoformat(),
                                    "staleness_status": ep.staleness_status or "FRESH",
                                    "staleness_reason": ep.staleness_reason,
                                }
                            )
                            if len(matched_eps) >= limit:
                                break

            # 2) Supplement with direct PIT episode search by tickers/query terms
            if len(matched_eps) < limit:
                ep_rows = (
                    await db_sess.scalars(
                        select(UserMemoryEpisode)
                        .where(
                            UserMemoryEpisode.tenant_id == ctx.tenant_id,
                            UserMemoryEpisode.user_id == ctx.user_id,
                            UserMemoryEpisode.status == "ACTIVE",
                            UserMemoryEpisode.is_backtest_sandbox.is_(is_bt),
                            UserMemoryEpisode.source_as_of_date <= ctx.as_of_date,
                        )
                        .order_by(UserMemoryEpisode.source_as_of_date.desc(), UserMemoryEpisode.updated_at.desc())
                        .limit(30)
                    )
                ).all()
                wanted_tks = set(tickers)
                q_low = q_text.lower()
                for ep in ep_rows:
                    if ep.external_session_id in seen_sids:
                        continue
                    ep_tks = {str(t).upper() for t in (ep.tickers_json or [])}
                    blob = " ".join(
                        [
                            ep.session_title or "",
                            ep.episode_summary or "",
                            " ".join(ep.macro_factors_json or []),
                            " ".join(ep.sectors_json or []),
                        ]
                    ).lower()
                    if wanted_tks and not (ep_tks & wanted_tks):
                        if not q_low or not any(tok in blob for tok in q_low.split() if len(tok) >= 3):
                            continue
                    seen_sids.add(ep.external_session_id)
                    matched_eps.append(
                        {
                            "episode_id": ep.episode_id,
                            "session_id": ep.external_session_id,
                            "session_title": ep.session_title,
                            "episode_summary": ep.episode_summary,
                            "tickers": list(ep.tickers_json or []),
                            "macro_factors": list(ep.macro_factors_json or []),
                            "sectors": list(ep.sectors_json or []),
                            "source_as_of_date": ep.source_as_of_date.isoformat(),
                            "staleness_status": ep.staleness_status or "FRESH",
                            "staleness_reason": ep.staleness_reason,
                        }
                    )
                    if len(matched_eps) >= limit:
                        break
        registered_sess = await ctx.evidence.register_linked_sessions(matched_eps)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await ctx.evidence.record_tool_trace(
            {"tool": "recall_session_memory", "scope": scope, "duration_ms": elapsed_ms, "count": len(registered_sess)}
        )
        return ToolResult(
            content=json.dumps(
                {
                    "scope": scope,
                    "as_of_date": ctx.as_of_date.isoformat(),
                    "linked_sessions": [
                        {**s, "badge": f"[SESS:{s['badge_id']}]"} for s in registered_sess
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            details={"scope": scope, "count": len(registered_sess)},
        )

    if scope == "thesis_lineage":
        async with ctx.session_factory() as db_sess:
            rows = (
                await db_sess.scalars(
                    select(UserMemoryItem)
                    .where(
                        UserMemoryItem.tenant_id == ctx.tenant_id,
                        UserMemoryItem.user_id == ctx.user_id,
                        UserMemoryItem.memory_type == "INVESTMENT_THESIS",
                        UserMemoryItem.is_backtest_sandbox.is_(is_bt),
                        UserMemoryItem.source_as_of_date <= ctx.as_of_date,
                        UserMemoryItem.tx_to >= INF_DATETIME,
                    )
                    .order_by(UserMemoryItem.source_as_of_date.asc(), UserMemoryItem.valid_from.asc())
                    .limit(40)
                )
            ).all()
        wanted_tks = set(tickers)
        lineage_items = []
        for row in rows:
            row_tks = {str(t).upper() for t in (row.tickers_json or [])}
            if wanted_tks and not (row_tks & wanted_tks):
                continue
            lineage_items.append(
                {
                    "memory_id": row.memory_id,
                    "memory_type": row.memory_type,
                    "content_text": row.content_text,
                    "tickers": list(row.tickers_json or []),
                    "source_as_of_date": row.source_as_of_date.isoformat(),
                    "valid_from": row.valid_from.isoformat() if row.valid_from else None,
                    "valid_to": row.valid_to.isoformat() if row.valid_to else None,
                    "is_active": bool(
                        row.valid_to
                        and row.valid_to.year >= 9999
                        and row.tx_to
                        and row.tx_to.year >= 9999
                    ),
                    "supersedes_memory_id": row.supersedes_memory_id,
                    "staleness_status": row.staleness_status or "FRESH",
                    "staleness_reason": row.staleness_reason,
                }
            )
        lineage_items = lineage_items[-limit:]
        registered_mems = await ctx.evidence.register_memories(lineage_items)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await ctx.evidence.record_tool_trace(
            {"tool": "recall_session_memory", "scope": scope, "duration_ms": elapsed_ms, "count": len(registered_mems)}
        )
        return ToolResult(
            content=json.dumps(
                {
                    "scope": scope,
                    "as_of_date": ctx.as_of_date.isoformat(),
                    "lineage": [{**m, "badge": f"[MEM:{m['badge_id']}]"} for m in registered_mems],
                },
                ensure_ascii=False,
                indent=2,
            ),
            details={"scope": scope, "count": len(registered_mems)},
        )

    # scope == "my_portfolio"
    port_items, port_by_sym, port_used = _format_portfolio_items(
        ctx.user_portfolio_snapshot,
        is_backtest=is_bt,
        memory_mode=ctx.memory_mode,
    )
    if port_by_sym:
        await ctx.evidence.register_portfolio_snapshots(port_by_sym)

    ext_holdings_registered: list[dict[str, Any]] = []
    if mem_enabled:
        async with ctx.session_factory() as db_sess:
            sr = await smart_read_user_memories(
                db_sess,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                query=q_text or "danh mục vị thế",
                as_of_date=ctx.as_of_date,
                memory_recall_need="user_profile_light",
                explicit_tickers=tickers,
                memory_mode=ctx.memory_mode,
                user_memory_mode="on",
                limit=limit,
            )
            ext_only = [
                m
                for m in [*sr.pinned_memories, *sr.recalled_memories]
                if m.memory_type == "USER_EXTERNAL_HOLDING"
            ][:limit]
            if ext_only:
                ext_holdings_registered = await ctx.evidence.register_memories(ext_only)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await ctx.evidence.record_tool_trace(
        {
            "tool": "recall_session_memory",
            "scope": "my_portfolio",
            "duration_ms": elapsed_ms,
            "portfolio_used": port_used,
            "external_holdings": len(ext_holdings_registered),
        }
    )
    return ToolResult(
        content=json.dumps(
            {
                "scope": "my_portfolio",
                "as_of_date": ctx.as_of_date.isoformat(),
                "is_backtest": is_bt,
                "live_portfolio_disabled_reason": "PORTFOLIO_DISABLED_IN_BACKTEST" if is_bt else None,
                "platform_portfolio": port_items,
                "external_holdings": [
                    {**m, "badge": f"[MEM:{m['badge_id']}]"} for m in ext_holdings_registered
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        details={
            "scope": "my_portfolio",
            "portfolio_used": port_used,
            "external_holdings": len(ext_holdings_registered),
        },
    )


def build_research_tools() -> tuple[AgentTool, ...]:
    """Return the static 6-tool research bus tuple (constant order for LLM KV-cache prefix hits)."""
    return (
        screen_financial_universe_tool,
        search_corporate_filings_tool,
        hydrate_financial_tables_tool,
        query_knowledge_graph_tool,
        run_financial_calculator_tool,
        recall_session_memory_tool,
    )


def build_research_tool_registry() -> ToolRegistry:
    return ToolRegistry(build_research_tools())
