"""Unified Flexible Tool-Calling Research Agent Runner & `SentenceStreamGate`.

Bridges Tier 0 SiliconFlow `Kev-4b` routing + `FinancialZeroMemLedger` + the 6 external
research tools on top of `sag_agent.AgentRuntime` (`ToolExecutionMode.PARALLEL` with
isolated per-call read-only `AsyncSession`), while optimizing LLM KV-cache reuse via a
static prefix + static tool schemas and preventing tool-preamble or unverified `[C99]`
leakage via `SentenceStreamGate`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sag_agent.runtime import Agent, AgentRuntime, RuntimeConfig
from sag_agent.types import (
    AgentEvent,
    AgentMessage,
    EventType,
    ModelProvider,
    ToolExecutionMode,
)
from sag_api.research.tools.context import ResearchToolContext
from sag_api.research.tools.research_tools import build_research_tools

_INLINE_C_BADGE_RE = re.compile(r"\[C(\d+)\]")
_INLINE_MEM_BADGE_RE = re.compile(r"\[MEM:(M\d+)\]")
_INLINE_PORT_BADGE_RE = re.compile(r"\[PORT:([A-Z0-9._-]{1,16})\]")
_INLINE_SESS_BADGE_RE = re.compile(r"\[SESS:(S\d+)\]")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?:\n|(?<=[.!?])\s+)")

_STATIC_RESEARCH_SYSTEM_PREFIX = (
    "Bạn là chuyên viên phân tích chứng khoán cấp cao (Institutional Equity Research Analyst) "
    "của AIInvest. Bạn hoạt động dưới kỷ luật ZERO-HALLUCINATION và Point-in-Time (PIT) tuyệt đối:\n"
    "1. Mọi con số tài chính mới (doanh thu, lợi nhuận, biên lợi nhuận, nợ vay, dòng tiền, P/E, P/B, ROE, beta) "
    "BẮT BUỘC phải xuất phát từ kết quả trả về của 6 công cụ bên ngoài trong lượt hiện tại — TUYỆT ĐỐI không tự nhớ "
    "hay bịa số từ kiến thức huấn luyện.\n"
    "2. Gắn thẻ nguồn ngay sau từng khẳng định: `[C1]`, `[C2]`... cho đoạn văn bản BCTC/Thuyết minh/BCTN; "
    "`[TBL:<table_id>]` cho bảng 2D; `[SCR:<TICKER>]` cho bảng sàng lọc định lượng; `[CALC-1]`... cho phép tính AST; "
    "`[KG:<LAYER>|<TIER>]` cho đồ thị tri thức. Không bao giờ phát minh mã `[C#]` chưa tồn tại.\n"
    "3. Không nhẩm toán: mọi phép tính YoY, CAGR, biên lợi nhuận, cơ cấu nợ, % hoàn thành kế hoạch ĐHĐCĐ "
    "phải gọi `run_financial_calculator`.\n"
    "4. Luôn sử dụng chiến lược truy vấn 2 thanh ghi khi gọi `search_corporate_filings`: `bm25_vas_queries` "
    "(thuật ngữ kế toán chuẩn VAS Thông tư 200/49/210, ví dụ 'phải thu ngắn hạn', 'vay và nợ thuê tài chính') "
    "và `dense_semantic_queries` (câu đầy đủ chủ thể + niên độ). Nếu CRAG trả `AMBIGUOUS`/`INCORRECT` hoặc bảng "
    "chính có cột mã thuyết minh (V.01, V.20...), hãy chủ động gọi tiếp tool ở lượt kế tiếp để tìm tới gốc.\n"
    "5. Nếu sau khi tra cứu vẫn thiếu dữ liệu PIT hợp lệ hoặc không có bằng chứng liên quan, phải tuyên bố từ chối "
    "minh bạch (`Không tìm thấy dữ liệu kiểm chứng trong phạm vi PIT hiện tại`) — tuyệt đối không suy diễn.\n"
    "6. Trình bày tiếng Việt chuyên nghiệp, súc tích, có cấu trúc rõ ràng; không bao giờ đưa lời khuyên mua/bán "
    "hay cam kết giá mục tiêu."
)

_TOOL_TO_SSE_STAGE: dict[str, tuple[str, str]] = {
    "screen_financial_universe": ("retrieval", "Đang sàng lọc định lượng toàn bộ danh mục PIT (Core PG + PDF)..."),
    "search_corporate_filings": ("retrieval", "Đang tra cứu BCTC/Thuyết minh/BCTN (Hybrid + Reranker + CRAG)..."),
    "hydrate_financial_tables": ("table_hydration", "Đang nạp ma trận bảng tài chính 2D & mã Thuyết minh..."),
    "query_knowledge_graph": ("graph_hop", "Đang truy vấn Đồ thị Tri thức 3 tầng (Vĩ mô / Chuỗi giá trị / Đối thủ)..."),
    "run_financial_calculator": ("calculator", "Đang tính toán chỉ tiêu tài chính qua AST Sandbox..."),
    "recall_session_memory": ("routing", "Đang tra cứu lại ngữ cảnh phiên hội thoại hợp lệ PIT..."),
}


@dataclass
class SentenceStreamGate:
    """Buffers turn deltas so tool preambles and hallucinated `[C99]`/`[MEM:M99]`/`[PORT:XYZ]` never leak."""

    valid_cid_ceiling: Callable[[], int]
    valid_memory_ids: Callable[[], set[str]] | None = None
    valid_portfolio_symbols: Callable[[], set[str]] | None = None
    valid_session_ids: Callable[[], set[str]] | None = None
    lookahead_chars: int = 220
    _buffer: str = ""
    _suppressed_for_tool: bool = False
    _flushed_any_prose: bool = False

    def reset_for_turn(self, *, tools_enabled: bool) -> None:
        self._buffer = ""
        self._suppressed_for_tool = False
        self._flushed_any_prose = False
        self._tools_enabled = tools_enabled

    def mark_pending_tool_call(self) -> None:
        self._suppressed_for_tool = True
        self._buffer = ""

    def _sanitize_sentence(self, text: str) -> str:
        max_cid = max(0, int(self.valid_cid_ceiling()))
        allowed_mems = set(self.valid_memory_ids()) if self.valid_memory_ids is not None else set()
        allowed_ports = (
            {str(s).strip().upper() for s in self.valid_portfolio_symbols() if str(s).strip()}
            if self.valid_portfolio_symbols is not None
            else set()
        )
        allowed_sess = set(self.valid_session_ids()) if self.valid_session_ids is not None else set()

        def _check_c(match: re.Match[str]) -> str:
            num = int(match.group(1))
            if 1 <= num <= max_cid:
                return match.group(0)
            return f"[C{num} UNVERIFIED_CLAIM]"

        def _check_mem(match: re.Match[str]) -> str:
            mid = match.group(1)
            if mid in allowed_mems:
                return match.group(0)
            return f"[MEM:{mid} UNVERIFIED_CLAIM]"

        def _check_port(match: re.Match[str]) -> str:
            sym = match.group(1).upper()
            if sym in allowed_ports:
                return match.group(0)
            return f"[PORT:{sym} UNVERIFIED_CLAIM]"

        def _check_sess(match: re.Match[str]) -> str:
            sid = match.group(1)
            if sid in allowed_sess:
                return match.group(0)
            return f"[SESS:{sid} UNVERIFIED_CLAIM]"

        out = _INLINE_C_BADGE_RE.sub(_check_c, text)
        out = _INLINE_MEM_BADGE_RE.sub(_check_mem, out)
        out = _INLINE_PORT_BADGE_RE.sub(_check_port, out)
        out = _INLINE_SESS_BADGE_RE.sub(_check_sess, out)
        return out

    def push_delta(self, delta: str) -> list[str]:
        """Accept streaming delta; return verified complete sentences safe to emit to SSE."""
        if self._suppressed_for_tool or not delta:
            return []
        self._buffer += delta

        # On tool-enabled turns, keep a short lookahead window before flushing so any brief
        # preamble before a tool call (e.g. "Để kiểm tra FPT, tôi sẽ tra cứu...") is held back.
        if getattr(self, "_tools_enabled", True) and not self._flushed_any_prose:
            if len(self._buffer) < self.lookahead_chars:
                return []

        parts = _SENTENCE_BOUNDARY_RE.split(self._buffer)
        if len(parts) <= 1:
            return []

        # Reconstruct exact substrings up to the last complete boundary
        last_match: re.Match[str] | None = None
        for m in _SENTENCE_BOUNDARY_RE.finditer(self._buffer):
            last_match = m
        if last_match is None:
            return []

        ready = self._buffer[: last_match.end()]
        self._buffer = self._buffer[last_match.end() :]
        self._flushed_any_prose = True
        return [self._sanitize_sentence(ready)]

    def flush_turn_end(self, *, has_tool_calls: bool) -> list[str]:
        if has_tool_calls or self._suppressed_for_tool:
            self._buffer = ""
            return []
        if not self._buffer:
            return []
        out = self._sanitize_sentence(self._buffer)
        self._buffer = ""
        self._flushed_any_prose = True
        return [out]


@dataclass
class AgentTurnOutcome:
    answer_text: str
    agent_turns: int = 0
    tool_calls_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    streamed_deltas: bool = False
    error_message: str | None = None
    extra_blindspots: list[dict[str, str]] = field(default_factory=list)


def build_compact_catalog_header(
    profiles: Sequence[Mapping[str, Any]],
    *,
    available_tickers: Sequence[str],
    max_entries: int = 80,
) -> str:
    """Compact deterministic issuer index appended inside the static-friendly system prompt."""
    if profiles:
        entries = []
        for p in list(profiles)[:max_entries]:
            tk = str(p.get("ticker") or "").upper()
            name = str(p.get("company_name") or tk)[:40]
            sec = str(p.get("sector") or "")[:24]
            entries.append(f"{tk} ({name}{f' - {sec}' if sec else ''})")
        return "Danh mục mã chứng khoán hợp lệ PIT trong phiên: " + "; ".join(entries)
    if available_tickers:
        return "Danh mục mã chứng khoán hợp lệ PIT: " + ", ".join(sorted(available_tickers)[:max_entries])
    return "Danh mục mã chứng khoán hợp lệ PIT: chưa có tài liệu nào được nạp."


def build_dynamic_user_prompt(
    *,
    query: str,
    route_decision: Mapping[str, Any],
    ctx: ResearchToolContext,
    explicit_tickers: Sequence[str] = (),
) -> str:
    """Assemble KV-cache-friendly dynamic 3-tier tail (Tier 0 Pinned + Tier 1 Adaptive Zero-Mem +
    Tier 2 Gated Memory + Route Hints + User Query).

    System Prompt + 6 tool schemas remain 100% static for maximum KV-cache prefix reuse.
    """
    blocks: list[str] = []
    if ctx.tier0_memory_prompt:
        blocks.append(ctx.tier0_memory_prompt)

    mem_block = ctx.session_ledger.to_prompt_context(
        include_recent_pairs=ctx.include_recent_pairs,
        max_recent_pairs=2 if ctx.include_recent_pairs else 0,
    )
    if mem_block:
        blocks.append(mem_block)

    if ctx.tier2_memory_prompt:
        blocks.append(ctx.tier2_memory_prompt)

    hint_lines = [
        f"- Ngày khóa sổ PIT (as_of_date): {ctx.as_of_date.isoformat()}",
        f"- Chế độ phân tích: {ctx.chat_mode}",
        f"- Mục tiêu nghiệp vụ (soft hint): {route_decision.get('task_action', 'fact_lookup')}",
        f"- Nguồn gợi ý (soft hint, được phép gọi mọi tool cần thiết): "
        f"{route_decision.get('evidence_source_hint', 'hybrid_all')}",
        f"- Định dạng trình bày mong muốn: {route_decision.get('output_style', 'fact_citation')}",
    ]
    if explicit_tickers:
        hint_lines.append(f"- Mã người dùng đã ghim/chọn trên giao diện: {', '.join(explicit_tickers)}")
    if route_decision.get("attach_investment_disclaimer"):
        hint_lines.append(
            "- CẢNH BÁO TUÂN THỦ: Người dùng hỏi ý kiến mua/bán — chỉ phân tích định lượng & rủi ro trung lập "
            "từ dữ liệu kiểm chứng, KHÔNG đưa khuyến nghị mua/bán hay mục tiêu giá, và ghi rõ tuyên bố miễn trừ."
        )
    if ctx.chat_mode == "MOAT":
        hint_lines.append(
            "- Chế độ MOAT Broker: ưu tiên đào sâu lợi thế cạnh tranh, chất lượng dòng tiền CFO/LNST, "
            "cơ cấu nợ vay & thuyết minh, đối chiếu kế hoạch ĐHĐCĐ với thực hiện kiểm toán và rủi ro vĩ mô."
        )

    blocks.append("<EXECUTION_CONTEXT>\n" + "\n".join(hint_lines) + "\n</EXECUTION_CONTEXT>")
    blocks.append(f"Câu hỏi của nhà đầu tư:\n{query}")
    return "\n\n".join(blocks)


async def run_research_agent_turn(
    *,
    query: str,
    model: ModelProvider,
    ctx: ResearchToolContext,
    route_decision: Mapping[str, Any],
    explicit_tickers: Sequence[str] = (),
    on_stage: Callable[[str, str, str], Awaitable[None]] | None = None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    overall_timeout_seconds: float = 45.0,
) -> AgentTurnOutcome:
    """Execute the unified `sag_agent` multi-hop research loop with `SentenceStreamGate`."""
    tools = build_research_tools()
    base_max_turns = max(2, min(6, int(route_decision.get("max_agent_turns") or 3)))
    # Allow 1 final synthesis turn after tool turns; add +1 headroom for CRAG self-correction
    effective_max_turns = min(6, base_max_turns + 1)

    catalog_header = build_compact_catalog_header(
        ctx.catalog_profiles,
        available_tickers=sorted(ctx.available_tickers),
    )
    instructions = f"{_STATIC_RESEARCH_SYSTEM_PREFIX}\n\n{catalog_header}"

    user_prompt = build_dynamic_user_prompt(
        query=query,
        route_decision=route_decision,
        ctx=ctx,
        explicit_tickers=explicit_tickers,
    )

    gate = SentenceStreamGate(
        valid_cid_ceiling=lambda: len(ctx.evidence.sections),
        valid_memory_ids=lambda: set(ctx.evidence.recalled_memories.keys()),
        valid_portfolio_symbols=lambda: set(ctx.evidence.portfolio_snapshots.keys()),
        valid_session_ids=lambda: set(ctx.evidence.linked_sessions.keys()),
    )
    streamed_any = False
    turns_seen = 0
    tool_calls_seen = 0
    current_turn_tools_enabled = True

    async def _handle_event(event: AgentEvent) -> None:
        nonlocal streamed_any, turns_seen, tool_calls_seen, current_turn_tools_enabled
        if event.type == EventType.TURN_STARTED:
            turns_seen = max(turns_seen, int(event.turn or 0))
            current_turn_tools_enabled = turns_seen <= effective_max_turns
            gate.reset_for_turn(tools_enabled=current_turn_tools_enabled)
            if turns_seen >= 2 and on_stage is not None:
                await on_stage("synthesis", "running", f"Đang tổng hợp & kiểm tra bằng chứng (bước {turns_seen})...")
        elif event.type == EventType.TOOL_STARTED:
            tool_calls_seen += 1
            tname = str(event.payload.get("name") or event.payload.get("tool_name") or "")
            stage_name, stage_detail = _TOOL_TO_SSE_STAGE.get(
                tname, ("retrieval", f"Đang chạy công cụ {tname}...")
            )
            if on_stage is not None:
                await on_stage(stage_name, "running", stage_detail)
        elif event.type == EventType.TOOL_COMPLETED:
            tname = str(event.payload.get("name") or event.payload.get("tool_name") or "")
            stage_name, _ = _TOOL_TO_SSE_STAGE.get(tname, ("retrieval", ""))
            if on_stage is not None:
                await on_stage(stage_name, "done", f"Hoàn tất {tname}")
        elif event.type == EventType.MESSAGE_DELTA:
            if event.payload.get("has_pending_tool_call"):
                gate.mark_pending_tool_call()
                return
            delta_txt = str(event.payload.get("delta") or "")
            if delta_txt and on_delta is not None:
                for chunk_out in gate.push_delta(delta_txt):
                    streamed_any = True
                    await on_delta(chunk_out)
        elif event.type == EventType.MESSAGE_COMPLETED:
            has_tc = bool(event.payload.get("has_tool_calls"))
            if on_delta is not None:
                for chunk_out in gate.flush_turn_end(has_tool_calls=has_tc):
                    streamed_any = True
                    await on_delta(chunk_out)

    agent = Agent(
        name="aiinvest_stock_research_agent",
        model=model,
        instructions=instructions,
        tools=tools,
        # Turn 1 MUST invoke at least one evidence tool so LLM never answers numbers from memory
        initial_tool_choice="required",
        max_turns=effective_max_turns,
        finalize_on_max_turns=True,
        final_instructions=(
            "Đã đạt giới hạn bước tra cứu. Hãy tổng hợp câu trả lời cuối cùng chính xác nhất chỉ từ "
            "các bằng chứng đã thu thập ở các bước trên, gắn đủ mã `[C#]`, `[TBL:*]`, `[SCR:*]`, "
            "`[CALC-*]`, `[KG:*]` và nêu rõ mọi khoảng trống dữ liệu còn thiếu."
        ),
    )

    runtime = AgentRuntime(
        RuntimeConfig(
            max_turns=effective_max_turns,
            tool_timeout_seconds=22.0,
            tool_execution=ToolExecutionMode.PARALLEL,
        )
    )
    try:
        async with asyncio.timeout(overall_timeout_seconds):
            async with runtime:
                handle = runtime.run(
                    agent,
                    AgentMessage(role="user", content=user_prompt),
                    context=ctx,
                )
                async for event in handle.events():
                    await _handle_event(event)
                result = await handle.result()
    except TimeoutError:
        return AgentTurnOutcome(
            answer_text="",
            agent_turns=turns_seen,
            tool_calls_count=tool_calls_seen,
            streamed_deltas=streamed_any,
            error_message=f"Agent loop timed out after {overall_timeout_seconds:.0f}s",
            extra_blindspots=[
                {
                    "code": "AGENT_LOOP_TIMEOUT",
                    "reason": f"Vòng lặp phân tích vượt quá ngân sách thời gian {overall_timeout_seconds:.0f}s.",
                    "scope": "agent_runner",
                }
            ],
        )

    return AgentTurnOutcome(
        answer_text=(result.output or "").strip(),
        agent_turns=max(turns_seen, result.usage.requests),
        tool_calls_count=tool_calls_seen,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cached_tokens=result.usage.cached_tokens,
        streamed_deltas=streamed_any,
        error_message=result.error.message if result.error is not None else None,
    )
