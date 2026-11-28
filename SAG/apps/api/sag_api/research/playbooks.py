"""Deterministic Router-to-Playbook Resolver & Zero-LLM-Token Pre-Fetch Engine (`playbooks.py`).

Bridges Tier-0 `Kev-4b` `route_decision` (`task_action`, `evidence_source_hint`, `confidence`,
`memory_recall_need`) to:
1. A focused 4–6 tool subset (ordered canonically for KV-cache prefix stability), preventing the LLM
   from improvising across 12 tools or wasting turns re-querying headline numbers via text RAG.
2. Low-confidence / fail-open safety valve: when `task_action` confidence `< 0.70` or `fallback_used`,
   unions the top-2 candidate playbooks (and legacy `search_corporate_filings` when router fallback is active).
3. Parallel 0-LLM-token deterministic pre-fetch (`FactSet`, sector KPIs, risk scan, peer matrix, AGM
   plan-vs-actual, 3-layer KG) injected into `<PREFETCHED_DETERMINISTIC_EVIDENCE>` before Turn 1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sag_agent.tools import AgentTool, ToolContext
from sag_agent.types import CancellationToken
from sag_api.research.tools.analyst_tools import (
    _fact_set,
    check_plan_vs_actual_tool,
    compare_peers_tool,
    get_financial_statements_tool,
    get_sector_kpis_tool,
    scan_financial_risks_tool,
)
from sag_api.research.tools.context import ResearchToolContext
from sag_api.research.tools.research_tools import (
    hydrate_financial_tables_tool,
    query_knowledge_graph_tool,
    recall_session_memory_tool,
    run_financial_calculator_tool,
    screen_financial_universe_tool,
    search_corporate_filings_tool,
    search_disclosures_tool,
)

PLAYBOOK_CONFIDENCE_THRESHOLD = 0.70

# Master canonical order — every playbook subset preserves this exact relative ordering for KV-cache stability
MASTER_TOOLS_ORDERED: tuple[AgentTool, ...] = (
    get_financial_statements_tool,
    get_sector_kpis_tool,
    scan_financial_risks_tool,
    compare_peers_tool,
    check_plan_vs_actual_tool,
    screen_financial_universe_tool,
    search_disclosures_tool,
    hydrate_financial_tables_tool,
    query_knowledge_graph_tool,
    run_financial_calculator_tool,
    recall_session_memory_tool,
    search_corporate_filings_tool,
)
_TOOLS_BY_NAME: dict[str, AgentTool] = {t.spec.name: t for t in MASTER_TOOLS_ORDERED}


@dataclass(frozen=True, slots=True)
class PlaybookSpec:
    playbook_id: str
    prefetch_steps: tuple[str, ...]
    tool_names: tuple[str, ...]
    max_turns_general: int
    max_turns_moat: int
    require_qualitative_turn: bool
    guidance_vi: str


PLAYBOOKS: dict[str, PlaybookSpec] = {
    "fact_lookup": PlaybookSpec(
        playbook_id="fact_lookup",
        prefetch_steps=("get_financial_statements", "get_sector_kpis"),
        tool_names=(
            "get_financial_statements",
            "get_sector_kpis",
            "search_disclosures",
            "hydrate_financial_tables",
            "run_financial_calculator",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=False,
        guidance_vi=(
            "Tra cứu chỉ tiêu cụ thể: dùng ngay bảng BCTC & KPI đã tiền nạp `[CALC-*]`; chỉ gọi "
            "`search_disclosures` nếu cần chi tiết thuyết minh."
        ),
    ),
    "screening_ranking": PlaybookSpec(
        playbook_id="screening_ranking",
        prefetch_steps=("screen_financial_universe",),
        tool_names=(
            "get_sector_kpis",
            "compare_peers",
            "screen_financial_universe",
            "search_disclosures",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=False,
        guidance_vi=(
            "Sàng lọc & xếp hạng doanh nghiệp: gọi `screen_financial_universe` với bộ lọc định lượng cụ thể, "
            "sau đó `get_sector_kpis` / `compare_peers` trên nhóm dẫn đầu."
        ),
    ),
    "peer_comparison": PlaybookSpec(
        playbook_id="peer_comparison",
        prefetch_steps=("compare_peers", "get_sector_kpis", "scan_financial_risks", "check_plan_vs_actual"),
        tool_names=(
            "get_sector_kpis",
            "scan_financial_risks",
            "compare_peers",
            "screen_financial_universe",
            "search_disclosures",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=True,
        guidance_vi=(
            "So sánh đồng ngành: ma trận KPI & cờ rủi ro đã tiền nạp `[CALC-*]`. Ở Lượt 1 hãy gọi song song "
            "`search_disclosures` cho từng mã để lấy thuyết minh đặc thù ngành (nợ nhóm 3-5, CASA, FVTPL, tồn kho)."
        ),
    ),
    "trend_attribution": PlaybookSpec(
        playbook_id="trend_attribution",
        prefetch_steps=("get_financial_statements", "get_sector_kpis", "scan_financial_risks"),
        tool_names=(
            "get_financial_statements",
            "scan_financial_risks",
            "search_disclosures",
            "hydrate_financial_tables",
            "run_financial_calculator",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=True,
        guidance_vi=(
            "Giải thích biến động qua các kỳ: chuỗi số liệu & chất lượng lợi nhuận đã tiền nạp `[CALC-*]`. "
            "Ở Lượt 1 hãy gọi `search_disclosures` (`QUARTER_NOTES` / `BCTC_AUDITED`) để trích nguyên nhân."
        ),
    ),
    "audit_discrepancy": PlaybookSpec(
        playbook_id="audit_discrepancy",
        prefetch_steps=("check_plan_vs_actual", "scan_financial_risks", "get_financial_statements"),
        tool_names=(
            "check_plan_vs_actual",
            "scan_financial_risks",
            "search_disclosures",
            "hydrate_financial_tables",
            "run_financial_calculator",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=True,
        guidance_vi=(
            "Đối chiếu Kế hoạch ĐHĐCĐ / Thông điệp lãnh đạo vs Thực hiện kiểm toán: tiến độ KH và cờ bất thường đã "
            "tiền nạp `[CALC-*]`. Ở Lượt 1 gọi `search_disclosures` vào `AGM_TARGETS` / `BCTN_PR` / `QUARTER_NOTES`."
        ),
    ),
    "macro_and_value_chain": PlaybookSpec(
        playbook_id="macro_and_value_chain",
        prefetch_steps=("query_knowledge_graph", "get_sector_kpis"),
        tool_names=(
            "get_financial_statements",
            "get_sector_kpis",
            "screen_financial_universe",
            "search_disclosures",
            "query_knowledge_graph",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=True,
        guidance_vi=(
            "Độ nhạy vĩ mô & chuỗi giá trị: liên kết KG 3 tầng và KPI đã tiền nạp. Gọi `search_disclosures` để "
            "xác nhận thuyết minh rủi ro tỷ giá/lãi suất/nguyên liệu đầu vào."
        ),
    ),
    "deep_moat_thesis": PlaybookSpec(
        playbook_id="deep_moat_thesis",
        prefetch_steps=(
            "get_financial_statements",
            "get_sector_kpis",
            "scan_financial_risks",
            "check_plan_vs_actual",
            "compare_peers",
            "query_knowledge_graph",
        ),
        tool_names=(
            "compare_peers",
            "check_plan_vs_actual",
            "search_disclosures",
            "hydrate_financial_tables",
            "query_knowledge_graph",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=True,
        guidance_vi=(
            "Báo cáo phân tích chuyên sâu chuẩn Broker (MOAT): toàn bộ BCTC chuẩn hóa, KPI đặc thù ngành, "
            "cờ chất lượng lợi nhuận, tiến độ ĐHĐCĐ và so sánh đồng ngành ĐÃ TIỀN NẠP sẵn (`[CALC-*]`, `[KG:*]`). "
            "KHÔNG tốn lượt tìm lại doanh thu/LNST/ROE; ở Lượt 1 chỉ gọi song song `search_disclosures` "
            "(1–3 lệnh chuẩn lọc `sub_doc_slots`/`fiscal_year`/`fiscal_quarter`) lấy thuyết minh sâu rồi tổng hợp."
        ),
    ),
    "compute_or_scenario": PlaybookSpec(
        playbook_id="compute_or_scenario",
        prefetch_steps=("get_financial_statements", "get_sector_kpis"),
        tool_names=(
            "get_financial_statements",
            "get_sector_kpis",
            "run_financial_calculator",
            "search_disclosures",
        ),
        max_turns_general=2,
        max_turns_moat=2,
        require_qualitative_turn=False,
        guidance_vi=(
            "Tính toán kịch bản / độ nhạy định lượng: lấy biến đầu vào từ BCTC chuẩn hóa `[CALC-*]` đã tiền nạp "
            "và gọi `run_financial_calculator` cho công thức kịch bản giả định."
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class ResolvedPlaybook:
    playbook_id: str
    secondary_playbook_id: str | None
    selection_mode: str  # EXACT_PLAYBOOK | LOW_CONFIDENCE_UNION
    prefetch_steps: tuple[str, ...]
    exposed_tools: tuple[AgentTool, ...]
    exposed_tool_names: tuple[str, ...]
    max_turns: int
    require_qualitative_turn: bool
    guidance_vi: str


def _infer_secondary_playbook(
    primary_id: str,
    route_decision: Mapping[str, Any],
    *,
    chat_mode: str,
    explicit_tickers: Sequence[str],
) -> str:
    hint = str(route_decision.get("evidence_source_hint") or "").strip().lower()
    if hint == "quantitative_metrics_only" and primary_id != "screening_ranking":
        return "screening_ranking"
    if hint == "knowledge_graph_macro" and primary_id != "macro_and_value_chain":
        return "macro_and_value_chain"
    if chat_mode == "MOAT" and primary_id != "deep_moat_thesis":
        return "deep_moat_thesis"
    if len(explicit_tickers) >= 2 and primary_id != "peer_comparison":
        return "peer_comparison"
    return "trend_attribution" if primary_id != "trend_attribution" else "fact_lookup"


def resolve_playbook(
    route_decision: Mapping[str, Any],
    *,
    chat_mode: str,
    explicit_tickers: Sequence[str] = (),
) -> ResolvedPlaybook:
    raw_action = str(route_decision.get("task_action") or "").strip().lower()
    is_moat = (chat_mode or "GENERAL").strip().upper() == "MOAT"
    primary_id = raw_action if raw_action in PLAYBOOKS else ("deep_moat_thesis" if is_moat else "fact_lookup")
    spec = PLAYBOOKS[primary_id]

    axis_confs = route_decision.get("axis_confidences")
    task_conf = (
        float(axis_confs.get("task_action"))
        if isinstance(axis_confs, Mapping) and isinstance(axis_confs.get("task_action"), int | float)
        else float(route_decision.get("confidence") or 0.0)
    )
    fallback_used = bool(route_decision.get("fallback_used"))
    mem_need = str(route_decision.get("memory_recall_need") or "none").strip().lower()

    wanted_names: set[str] = set(spec.tool_names)
    prefetch_list: list[str] = list(spec.prefetch_steps)
    secondary_id: str | None = None

    if fallback_used or task_conf < PLAYBOOK_CONFIDENCE_THRESHOLD:
        selection_mode = "LOW_CONFIDENCE_UNION"
        secondary_id = _infer_secondary_playbook(
            primary_id, route_decision, chat_mode=chat_mode, explicit_tickers=explicit_tickers
        )
        sec_spec = PLAYBOOKS[secondary_id]
        wanted_names.update(sec_spec.tool_names)
        for st in sec_spec.prefetch_steps:
            if st not in prefetch_list:
                prefetch_list.append(st)
        if fallback_used:
            # Router-disabled / fail-open safety: keep legacy filing & calculator/table tools available
            wanted_names.update(
                {
                    "screen_financial_universe",
                    "search_corporate_filings",
                    "hydrate_financial_tables",
                    "query_knowledge_graph",
                    "run_financial_calculator",
                    "recall_session_memory",
                }
            )
    else:
        selection_mode = "EXACT_PLAYBOOK"

    if mem_need not in ("", "none"):
        wanted_names.add("recall_session_memory")

    ordered_tools = tuple(t for t in MASTER_TOOLS_ORDERED if t.spec.name in wanted_names)
    ordered_names = tuple(t.spec.name for t in ordered_tools)
    max_turns = spec.max_turns_moat if is_moat else spec.max_turns_general
    if not explicit_tickers and max_turns < 3:
        max_turns = 3

    return ResolvedPlaybook(
        playbook_id=primary_id,
        secondary_playbook_id=secondary_id,
        selection_mode=selection_mode,
        prefetch_steps=tuple(prefetch_list),
        exposed_tools=ordered_tools,
        exposed_tool_names=ordered_names,
        max_turns=max_turns,
        require_qualitative_turn=spec.require_qualitative_turn,
        guidance_vi=spec.guidance_vi,
    )


async def execute_playbook_prefetch(
    playbook: ResolvedPlaybook,
    *,
    ctx: ResearchToolContext,
    explicit_tickers: Sequence[str],
) -> tuple[str, list[str]]:
    """Run deterministic 0-LLM-token pre-fetch steps in parallel and format prompt block."""
    tickers = [str(t).strip().upper() for t in explicit_tickers if str(t).strip()][:6]

    async def _noop_progress(_p: Any) -> None:
        return None

    def _tctx(step_name: str) -> ToolContext:
        return ToolContext(
            run_id="prefetch",
            tool_call_id=f"prefetch-{step_name}",
            data=ctx,
            cancellation=CancellationToken(),
            _on_progress=_noop_progress,
        )

    coros: list[tuple[str, Any]] = []

    if tickers and ctx.core_reader is not None:
        # Warm the per-turn FactSet cache ONCE so parallel engines do 0 duplicate Core PG round-trips
        try:
            await _fact_set(ctx, tickers)
        except Exception:  # noqa: BLE001
            return "", []

        for step in playbook.prefetch_steps:
            if step == "get_financial_statements":
                coros.append(
                    (
                        step,
                        get_financial_statements_tool.execute(
                            {"tickers": tickers, "view": "recent_quarters", "periods": 5},
                            _tctx(step),
                        ),
                    )
                )
            elif step == "get_sector_kpis":
                coros.append((step, get_sector_kpis_tool.execute({"tickers": tickers}, _tctx(step))))
            elif step == "scan_financial_risks":
                coros.append((step, scan_financial_risks_tool.execute({"tickers": tickers}, _tctx(step))))
            elif step == "compare_peers" and len(tickers) >= 2:
                coros.append((step, compare_peers_tool.execute({"tickers": tickers}, _tctx(step))))
            elif step == "check_plan_vs_actual":
                coros.append((step, check_plan_vs_actual_tool.execute({"tickers": tickers}, _tctx(step))))

    if (
        "query_knowledge_graph" in playbook.prefetch_steps
        and tickers
        and ctx.enable_kg
        and (ctx.kg_store_override is not None or ctx.engine_manager is not None)
    ):
        coros.append(
            (
                "query_knowledge_graph",
                query_knowledge_graph_tool.execute({"tickers": tickers}, _tctx("query_knowledge_graph")),
            )
        )

    if (
        "screen_financial_universe" in playbook.prefetch_steps
        and not tickers
        and ctx.core_reader is not None
    ):
        coros.append(
            (
                "screen_financial_universe",
                screen_financial_universe_tool.execute(
                    {"use_default_quality_screen": True, "limit": 10},
                    _tctx("screen_financial_universe"),
                ),
            )
        )

    if not coros:
        return "", []

    results = await asyncio.gather(*(c for _, c in coros), return_exceptions=True)
    sections: list[str] = []
    executed: list[str] = []
    for (step_name, _), out in zip(coros, results, strict=True):
        if isinstance(out, BaseException):
            continue
        txt = str(out.content or "").strip()
        if not txt or txt.startswith("MIXED_TEMPLATES"):
            continue
        executed.append(step_name)
        sections.append(f"### [{step_name}]\n{txt}")

    if not sections:
        return "", []

    block = (
        "<PREFETCHED_DETERMINISTIC_EVIDENCE>\n"
        "Dữ liệu định lượng & kiểm toán tất định dưới đây ĐÃ ĐƯỢC TÍNH SẴN (0 LLM token, khóa sổ PIT). "
        "Hãy sử dụng trực tiếp và trích dẫn đúng mã `[CALC-*]`, `[SCR:*]`, `[KG:*]` — KHÔNG tốn lượt tìm lại "
        "các con số tổng quát này trong văn bản:\n\n"
        + "\n\n".join(sections)
        + "\n</PREFETCHED_DETERMINISTIC_EVIDENCE>"
    )
    return block, executed
