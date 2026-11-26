"""Brief-Centric Synthesis, Stage 7 Surgical Repair & End-to-End Turn Runner (`brief_synthesizer.py`).

Implements Components (6) and (7) of the SAG Brief-Centric Brain:
- Component (6) `synthesize_from_verdict_cards`: 1 streaming LLM call whose input is ONLY the
  `ResearchBrief` + compact `ClaimVerdictCard`s (never raw multi-turn tool dumps), answering
  `user_concern` naturally without forcing a rigid 4-section template.
- Component (7) `repair_stage7_flagged_lines`: runs ONLY when Stage 7 (`verify_citations` or
  `verify_numeric_claims_against_evidence`) flags unsupported/uncited numbers or rejected citations,
  sending solely the flagged lines + verified `[CALC-*]`/`[C#]` cards for 1 surgical fix pass.
- `run_brief_centric_turn`: orchestrates Steps (1) -> (6) and returns `AgentTurnOutcome` plus
  verdict cards for Stage 7 + optional Step (7) repair in `ResearchChatOrchestrator`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sag_agent.types import AgentMessage, CancellationToken, ModelProvider, ModelRequest, Usage
from sag_api.research.agent_runner import (
    AgentTurnOutcome,
    SentenceStreamGate,
    strip_reasoning_tags,
)
from sag_api.research.brief import (
    ResearchBrief,
    TraceSink,
    create_and_validate_brief,
    emit_trace,
    usage_to_dict,
)
from sag_api.research.brief_executor import (
    BriefExecutionResult,
    ClaimVerdictCard,
    execute_brief_claims,
    locate_candidates,
)
from sag_api.research.tools.context import ResearchToolContext

logger = logging.getLogger(__name__)

_INLINE_CALC_BADGE_RE = re.compile(r"\[(CALC-\d+)\]")
_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

SYNTHESIS_STATIC_SYSTEM_PROMPT = (
    "Bạn là Chuyên gia Phân tích Đầu tư Cấp cao (Senior Institutional Equity Analyst) của AIInvest.\n"
    "Nhiệm vụ: viết câu trả lời sắc sảo, đi thẳng vào mối quan tâm thật sự của nhà đầu tư (`user_concern`) "
    "dựa HOÀN TOÀN trên Đề bài và các Thẻ kết luận đã kiểm chứng bên dưới.\n\n"
    "NGUYÊN TẮC BẮT BUỘC:\n"
    "1. Hình thức linh hoạt theo đề bài (`answer_shape`), KHÔNG ép khung 4 mục cứng nhắc:\n"
    "   - Mở đầu bằng kết luận trực diện trả lời ngay câu hỏi / nỗi lo của người hỏi.\n"
    "   - Nếu có Thẻ tiền đề (`TIỀN ĐỀ CẦN KIỂM TRA`) mang trạng thái `REFUTED`, PHẢI đính chính thẳng thắn "
    "giả định chưa đúng của người hỏi ngay ở câu mở đầu bằng số liệu thực tế trước khi giải thích diễn biến thật.\n"
    "   - Nếu là câu hỏi tìm kiếm / so sánh ứng viên (`candidate_ranking` / `peer_head_to_head`), trình bày "
    "chuẩn đối chiếu (nếu có mã gốc) và bảng so sánh gọn giữa các ứng viên, nêu rõ tiêu chí đạt / chưa đạt.\n"
    "   - Trình bày bảng Markdown gọn khi có từ 3 chỉ tiêu hoặc nhiều kỳ/nhiều mã trở lên để dễ đọc.\n"
    "2. KỶ LUẬT SỐ HỌC & TRÍCH DẪN TUYỆT ĐỐI (ZERO MENTAL MATH):\n"
    "   - CHỈ dùng các con số đã xuất hiện nguyên văn trong các Thẻ kết luận bên dưới.\n"
    "   - TUYỆT ĐỐI KHÔNG tự cộng nhẩm, trừ nhẩm hoặc nhân chia thêm bất kỳ con số mới nào ngoài thẻ "
    "(đặc biệt: KHÔNG cộng các khoản mục con trong thuyết minh nợ vay vào tổng vay ngắn hạn trên CĐKT).\n"
    "   - Mọi câu có số liệu tài chính BẮT BUỘC gắn kèm mã nguồn gốc có sẵn trong thẻ (`[CALC-*]`, `[C#]`, "
    "`[TBL:*]`, `[SCR:*]`, `[KG:*]`, `[MEM:*]`, `[PORT:*]`). Không tự bịa mã không tồn tại.\n"
    "3. TRUNG THỰC VỚI KHOẢNG TRỐNG (`NO_DATA`):\n"
    "   - Ý nào thẻ ghi `NO_DATA` hoặc thiếu thuyết minh PIT thì nói thẳng là chưa có dữ liệu kiểm chứng trong "
    "phạm vi PIT hiện tại — tuyệt đối không suy đoán lấp chỗ trống.\n"
    "   - Không đưa khuyến nghị mua/bán hoặc giá mục tiêu."
)

REPAIR_STATIC_SYSTEM_PROMPT = (
    "Bạn là Kiểm toán viên Chuẩn hóa Báo cáo (Stage 7 Forensic Auditor & Surgical Editor) của AIInvest.\n"
    "Một vài dòng trong bản nháp vừa bị hệ thống kiểm toán Stage 7 gắn cờ (do tự cộng nhẩm ra số không khớp "
    "bằng chứng, thiếu mã trích dẫn `[CALC-*]`/`[C#]`, hoặc gắn nhầm mã trích dẫn).\n\n"
    "NHIỆM VỤ:\n"
    "- Chỉ sửa ĐÚNG các dòng bị gắn cờ được gửi kèm bên dưới dựa trên Thẻ kết luận & Danh sách `[CALC-*]`/`[C#]` gốc.\n"
    "- Giữ nguyên định dạng đầu dòng (bullet `- `, `* `, tiêu đề `###`, hoặc dòng bảng `|...|`).\n"
    "- Thay thế mọi con số tự cộng nhẩm / sai lệch bằng ĐÚNG con số chuẩn trong `[CALC-*]` hoặc `[C#]` kèm mã thẻ.\n"
    "- Nếu một con số không có trong bằng chứng gốc, hãy xóa con số chưa kiểm chứng đó hoặc ghi rõ chưa có số PIT.\n"
    "- Xuất ra ĐÚNG MỘT JSON theo mẫu:\n"
    '{"repairs": [{"idx": 0, "fixed_line": "dòng đã sửa chuẩn xác kèm [CALC-*] hoặc [C#]"}]}'
)


@dataclass
class BriefTurnResult:
    outcome: AgentTurnOutcome
    brief: ResearchBrief | None = None
    verdict_cards: list[ClaimVerdictCard] = field(default_factory=list)


def _parse_json_obj(raw: str) -> dict[str, Any] | None:
    cleaned = _THINK_RE.sub("", raw or "").strip()
    close_idx = cleaned.lower().find("</think>")
    if close_idx != -1:
        cleaned = cleaned[close_idx + len("</think>") :].strip()
    cleaned = _CODE_FENCE_RE.sub("", cleaned).strip()
    for candidate in (cleaned, cleaned[cleaned.find("{") : cleaned.rfind("}") + 1] if "{" in cleaned else ""):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def build_synthesis_user_prompt(
    *,
    query: str,
    brief: ResearchBrief,
    exec_result: BriefExecutionResult,
    ctx: ResearchToolContext,
    pit_adjustments: Sequence[str] = (),
    attach_disclaimer: bool = False,
) -> str:
    blocks: list[str] = []
    if ctx.tier0_memory_prompt:
        blocks.append(ctx.tier0_memory_prompt)
    if ctx.tier2_memory_prompt:
        blocks.append(ctx.tier2_memory_prompt)

    meta_lines = [
        f"- Câu hỏi gốc của nhà đầu tư: {query.strip()}",
        f"- Ngày khóa sổ PIT (`as_of_date`): {ctx.as_of_date.isoformat()}",
        f"- Mối quan tâm cốt lõi (`user_concern`): {brief.user_concern}",
        f"- Hình dáng câu trả lời (`answer_shape`): {brief.answer_shape}",
    ]
    if exec_result.located.analysis_tickers:
        meta_lines.append(f"- Đối tượng phân tích: {', '.join(exec_result.located.analysis_tickers)}")
    if exec_result.located.anchor_ticker:
        meta_lines.append(f"- Mã chuẩn đối chiếu (`anchor_ticker`): {exec_result.located.anchor_ticker}")
    if brief.assumptions:
        meta_lines.append("- Giả định phân tích rõ ràng: " + "; ".join(brief.assumptions))
    if pit_adjustments:
        meta_lines.append("- Điều chỉnh khóa sổ PIT: " + "; ".join(pit_adjustments))
    if attach_disclaimer:
        meta_lines.append(
            "- LƯU Ý TUÂN THỦ: Người hỏi đề cập mua/bán — chỉ phân tích khách quan số liệu & rủi ro, "
            "kèm tuyên bố miễn trừ trách nhiệm đầu tư."
        )
    blocks.append("<BRIEF_METADATA>\n" + "\n".join(meta_lines) + "\n</BRIEF_METADATA>")

    all_cards = [*exec_result.premise_cards, *exec_result.claim_cards]
    cards_txt = "\n\n".join(card.render_for_synthesis() for card in all_cards)
    blocks.append("<VERIFIED_CLAIM_CARDS>\n" + cards_txt + "\n</VERIFIED_CLAIM_CARDS>")
    return "\n\n".join(blocks)


async def synthesize_from_verdict_cards(
    *,
    query: str,
    brief: ResearchBrief,
    exec_result: BriefExecutionResult,
    ctx: ResearchToolContext,
    model: ModelProvider,
    pit_adjustments: Sequence[str] = (),
    attach_disclaimer: bool = False,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    timeout_seconds: float = 45.0,
    trace: TraceSink | None = None,
) -> tuple[str, Usage, bool, bool]:
    """Component (6): Stream final answer strictly from `ResearchBrief` + `ClaimVerdictCard`s."""
    user_prompt = build_synthesis_user_prompt(
        query=query,
        brief=brief,
        exec_result=exec_result,
        ctx=ctx,
        pit_adjustments=pit_adjustments,
        attach_disclaimer=attach_disclaimer,
    )
    gate = SentenceStreamGate(
        valid_cid_ceiling=lambda: len(ctx.evidence.sections),
        valid_calc_ids=lambda: {
            str(c.get("calc_id") or "")
            for c in ctx.evidence.calculator_traces
            if c.get("calc_id")
        },
        valid_memory_ids=lambda: set(ctx.evidence.recalled_memories.keys()),
        valid_portfolio_symbols=lambda: set(ctx.evidence.portfolio_snapshots.keys()),
        valid_session_ids=lambda: set(ctx.evidence.linked_sessions.keys()),
    )
    gate.reset_for_turn(tools_enabled=False)

    req = ModelRequest(
        messages=(
            AgentMessage(role="system", content=SYNTHESIS_STATIC_SYSTEM_PROMPT),
            AgentMessage(role="user", content=user_prompt),
        ),
        tools=(),
        turn=1,
    )
    emitted_chunks: list[str] = []
    raw_model_parts: list[str] = []
    streamed_any = False
    timed_out = False
    in_tok = out_tok = cached_tok = reas_tok = 0

    try:
        async with asyncio.timeout(max(3.0, timeout_seconds)):
            async for chunk in model.stream_turn(req, CancellationToken()):
                if chunk.usage is not None:
                    in_tok = max(in_tok, chunk.usage.input_tokens)
                    out_tok = max(out_tok, chunk.usage.output_tokens)
                    cached_tok = max(cached_tok, chunk.usage.cached_tokens)
                    reas_tok = max(reas_tok, chunk.usage.reasoning_tokens)
                if chunk.text_delta:
                    raw_model_parts.append(chunk.text_delta)
                    out_sentences = gate.push_delta(chunk.text_delta)
                    for s_out in out_sentences:
                        emitted_chunks.append(s_out)
                        if on_delta is not None:
                            streamed_any = True
                            await on_delta(s_out)
            for tail_out in gate.flush_turn_end(has_tool_calls=False):
                emitted_chunks.append(tail_out)
                if on_delta is not None:
                    streamed_any = True
                    await on_delta(tail_out)
    except TimeoutError:
        timed_out = True
        for tail_out in gate.flush_turn_end(has_tool_calls=False):
            emitted_chunks.append(tail_out)
            if on_delta is not None:
                streamed_any = True
                await on_delta(tail_out)

    valid_calcs = {
        str(c.get("calc_id") or "")
        for c in ctx.evidence.calculator_traces
        if c.get("calc_id")
    }
    raw_answer = strip_reasoning_tags("".join(emitted_chunks).strip())
    cleaned_answer = _INLINE_CALC_BADGE_RE.sub(
        lambda m: m.group(0) if m.group(1) in valid_calcs else "",
        raw_answer,
    )
    usage = Usage(
        requests=1,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_tokens=cached_tok,
        reasoning_tokens=reas_tok,
    )
    emit_trace(
        trace,
        "synthesis",
        system_prompt_chars=len(SYNTHESIS_STATIC_SYSTEM_PROMPT),
        user_prompt=user_prompt,
        raw_model_output="".join(raw_model_parts),
        gated_answer=cleaned_answer,
        usage=usage_to_dict(usage),
        timed_out=timed_out,
    )
    return cleaned_answer, usage, streamed_any, timed_out


async def repair_stage7_flagged_lines(
    *,
    answer_text: str,
    unsupported_claims: Sequence[Mapping[str, Any]],
    uncited_claims: Sequence[Mapping[str, Any]],
    rejected_citations: Sequence[Mapping[str, Any]],
    verdict_cards: Sequence[ClaimVerdictCard],
    calculator_traces: Sequence[Any],
    sections_by_cid: Mapping[str, Any],
    model: ModelProvider,
    timeout_seconds: float = 20.0,
    trace: TraceSink | None = None,
) -> tuple[str, Usage, bool]:
    """Component (7): 1-pass surgical repair of ONLY the lines flagged by Stage 7.

    Returns `(repaired_text, repair_usage, was_modified)`. Caller MUST re-run Stage 7 afterwards!
    """
    if not answer_text.strip() or (not unsupported_claims and not uncited_claims and not rejected_citations):
        return answer_text, Usage(), False

    lines = answer_text.splitlines()
    flagged_indices: dict[int, list[str]] = {}

    _badge_strip_re = re.compile(
        r"\[(?:C\d+(?:\s+UNVERIFIED_CLAIM)?|TBL:[^\]]+|CALC-\d+|KG:[^\]]+|SCR:[^\]]+|MEM:[^\]]+|PORT:[^\]]+)\]"
    )

    def _norm_for_match(s: str) -> str:
        s_clean = _badge_strip_re.sub(" ", s or "")
        s_clean = re.sub(r"[*_`|#>]+", " ", s_clean)
        return re.sub(r"\s+", " ", s_clean).strip().lower()

    norm_lines = [_norm_for_match(ln) for ln in lines]

    def _match_line_index(excerpt: str, claim_txt: str = "") -> int | None:
        probe = _norm_for_match(excerpt)[:70]
        raw_num = claim_txt.strip().split()[0] if claim_txt.strip() else ""
        compact_claim = re.sub(r"\s+", "", claim_txt.strip()).lower()
        if probe:
            for idx, n_ln in enumerate(norm_lines):
                if probe in n_ln or (len(n_ln) >= 18 and n_ln[:50] in probe):
                    return idx
        # Space-insensitive claim token match (handles Stage 7 '28.5 %' vs table cell '28.5%')
        if compact_claim:
            for idx, ln in enumerate(lines):
                if compact_claim in re.sub(r"\s+", "", ln).lower():
                    return idx
        if raw_num:
            for idx, ln in enumerate(lines):
                if raw_num in ln:
                    return idx
        return None

    unmatched_flags: list[dict[str, str]] = []

    for item in unsupported_claims:
        c_txt = str(item.get("claim_text") or "")
        exc = str(item.get("sentence_excerpt") or "")
        l_idx = _match_line_index(exc, c_txt)
        if l_idx is not None:
            flagged_indices.setdefault(l_idx, []).append(
                f"Số liệu `{c_txt}` không khớp bằng chứng gốc (có thể do tự cộng/trừ/chia nhẩm hoặc sai đơn vị)."
            )
        else:
            unmatched_flags.append({"kind": "UNSUPPORTED", "claim_text": c_txt, "sentence_excerpt": exc})

    for item in uncited_claims:
        c_txt = str(item.get("claim_text") or "")
        exc = str(item.get("sentence_excerpt") or "")
        l_idx = _match_line_index(exc, c_txt)
        if l_idx is not None:
            flagged_indices.setdefault(l_idx, []).append(
                f"Số liệu `{c_txt}` thiếu thẻ trích dẫn `[CALC-*]` / `[C#]` / `[TBL:*]` / `[SCR:*]`."
            )
        else:
            unmatched_flags.append({"kind": "UNCITED", "claim_text": c_txt, "sentence_excerpt": exc})

    for idx, ln in enumerate(lines):
        if "UNVERIFIED_CLAIM" in ln:
            flagged_indices.setdefault(idx, []).append(
                "Chứa mã trích dẫn không hợp lệ hoặc không khớp nội dung (`UNVERIFIED_CLAIM`)."
            )

    if not flagged_indices:
        emit_trace(
            trace,
            "stage7.repair",
            outcome="SKIPPED_NO_LINE_MATCHED",
            unmatched_flags=unmatched_flags,
            modified=False,
        )
        return answer_text, Usage(), False

    target_items = [
        {"idx": l_idx, "flagged_line": lines[l_idx], "issues": issues}
        for l_idx, issues in sorted(flagged_indices.items())[:6]
    ]
    dropped_targets = [l_idx for l_idx, _ in sorted(flagged_indices.items())[6:]]

    ev_lines: list[str] = []
    if verdict_cards:
        ev_lines.append("=== THẺ KẾT LUẬN ĐÃ KIỂM CHỨNG ===")
        for card in verdict_cards:
            ev_lines.append(card.render_for_synthesis())
    if calculator_traces:
        ev_lines.append("=== CHỈ TIÊU [CALC-*] GỐC ===")
        for c in list(calculator_traces)[:28]:
            cid = getattr(c, "calc_id", None) or (c.get("calc_id") if isinstance(c, Mapping) else "")
            desc = getattr(c, "description", None) or (c.get("description") if isinstance(c, Mapping) else "")
            fmt = getattr(c, "formatted", None) or (c.get("formatted") if isinstance(c, Mapping) else "")
            inps = getattr(c, "inputs", None) or (c.get("inputs") if isinstance(c, Mapping) else {})
            if fmt:
                ev_lines.append(f"[{cid}] {desc}: {fmt}")
            elif isinstance(inps, Mapping) and inps:
                kv = "; ".join(
                    f"{k}={v.get('value') if isinstance(v, Mapping) else v}"
                    for k, v in list(inps.items())[:12]
                )
                ev_lines.append(f"[{cid}] {desc}: {kv}")
    if sections_by_cid:
        ev_lines.append("=== ĐOẠN TÀI LIỆU [C#] GỐC ===")
        for cid, sec in list(sections_by_cid.items())[:8]:
            tk = getattr(sec, "ticker", "") if not isinstance(sec, Mapping) else sec.get("ticker", "")
            body = getattr(sec, "content", "") if not isinstance(sec, Mapping) else sec.get("content", "")
            ev_lines.append(f"[{cid}] ({tk}) {str(body).strip()[:320]}")

    repair_user_msg = (
        "CÁC DÒNG CẦN SỬA:\n"
        + json.dumps(target_items, ensure_ascii=False, indent=2)
        + "\n\nBẰNG CHỨNG GỐC HỢP LỆ:\n"
        + "\n".join(ev_lines)
    )
    req = ModelRequest(
        messages=(
            AgentMessage(role="system", content=REPAIR_STATIC_SYSTEM_PROMPT),
            AgentMessage(role="user", content=repair_user_msg),
        ),
        tools=(),
        turn=1,
    )
    parts: list[str] = []
    in_tok = out_tok = cached_tok = reas_tok = 0
    try:
        async with asyncio.timeout(max(3.0, timeout_seconds)):
            async for chunk in model.stream_turn(req, CancellationToken()):
                if chunk.usage is not None:
                    in_tok = max(in_tok, chunk.usage.input_tokens)
                    out_tok = max(out_tok, chunk.usage.output_tokens)
                    cached_tok = max(cached_tok, chunk.usage.cached_tokens)
                    reas_tok = max(reas_tok, chunk.usage.reasoning_tokens)
                if chunk.text_delta:
                    parts.append(chunk.text_delta)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stage 7 surgical repair call failed: %s", exc)
        partial_u = Usage(
            requests=1,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_tokens=cached_tok,
        )
        emit_trace(
            trace,
            "stage7.repair",
            outcome="LLM_CALL_FAILED",
            error=f"{type(exc).__name__}: {exc}",
            targets=target_items,
            unmatched_flags=unmatched_flags,
            raw_output="".join(parts),
            modified=False,
        )
        return answer_text, partial_u, False

    usage = Usage(
        requests=1,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_tokens=cached_tok,
        reasoning_tokens=reas_tok,
    )
    raw_repair = "".join(parts)
    parsed = _parse_json_obj(raw_repair)
    if not parsed or not isinstance(parsed.get("repairs"), Sequence):
        emit_trace(
            trace,
            "stage7.repair",
            outcome="NON_JSON_OUTPUT",
            targets=target_items,
            dropped_targets_over_cap=dropped_targets,
            unmatched_flags=unmatched_flags,
            user_prompt=repair_user_msg,
            raw_output=raw_repair,
            usage=usage_to_dict(usage),
            modified=False,
        )
        return answer_text, usage, False

    modified = False
    repaired_lines = list(lines)
    valid_idxs = {item["idx"] for item in target_items}
    applied: list[dict[str, Any]] = []
    for rep in parsed["repairs"]:
        if not isinstance(rep, Mapping):
            continue
        r_idx = rep.get("idx")
        fixed = rep.get("fixed_line")
        if isinstance(r_idx, int) and r_idx in valid_idxs and isinstance(fixed, str):
            clean_fixed = fixed.strip("\r\n")
            if clean_fixed and clean_fixed != repaired_lines[r_idx]:
                applied.append({"idx": r_idx, "before": repaired_lines[r_idx], "after": clean_fixed})
                repaired_lines[r_idx] = clean_fixed
                modified = True

    emit_trace(
        trace,
        "stage7.repair",
        outcome="APPLIED" if modified else "NO_CHANGE",
        targets=target_items,
        dropped_targets_over_cap=dropped_targets,
        unmatched_flags=unmatched_flags,
        user_prompt=repair_user_msg,
        raw_output=raw_repair,
        applied=applied,
        usage=usage_to_dict(usage),
        modified=modified,
    )
    return "\n".join(repaired_lines), usage, modified


async def run_brief_centric_turn(
    *,
    query: str,
    model: ModelProvider,
    ctx: ResearchToolContext,
    route_decision: Mapping[str, Any],
    explicit_tickers: Sequence[str] = (),
    on_stage: Callable[[str, str, str], Awaitable[None]] | None = None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    on_thought: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_call: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    overall_timeout_seconds: float = 125.0,
    trace: TraceSink | None = None,
) -> BriefTurnResult:
    """Execute the full Brief-Centric Brain pipeline: (1) Brief -> (2) Validate -> (3) Locate ->
    (4) Parallel Claims -> (5) Verdict Cards -> (6) Synthesis.
    """
    started = time.perf_counter()
    overall_deadline_mono = asyncio.get_running_loop().time() + max(5.0, overall_timeout_seconds)
    activity_log: list[dict[str, Any]] = []
    extra_blindspots: list[dict[str, str]] = []

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    async def _record_thought(evt: dict[str, Any]) -> None:
        item = {**evt, "timestamp_ms": _elapsed_ms()}
        activity_log.append(item)
        if on_thought is not None:
            await on_thought(item)

    async def _record_tool_call(evt: dict[str, Any]) -> None:
        item = {**evt, "timestamp_ms": _elapsed_ms()}
        activity_log.append(item)
        if on_tool_call is not None:
            await on_tool_call(item)

    async def _record_tool_result(evt: dict[str, Any]) -> None:
        item = {**evt, "completed_at_ms": _elapsed_ms()}
        activity_log.append(item)
        if on_tool_result is not None:
            await on_tool_result(item)

    session_ctx = ctx.session_ledger.to_prompt_context(
        include_recent_pairs=ctx.include_recent_pairs,
        max_recent_pairs=2 if ctx.include_recent_pairs else 0,
    )
    mem_ctx = "\n".join(b for b in (ctx.tier0_memory_prompt, ctx.tier2_memory_prompt) if b)

    # ── Steps (1) & (2): Create & Deterministically Validate ResearchBrief ────
    if on_stage is not None:
        await on_stage("routing", "running", "Đang lập đề bài phân tích & kiểm tra ràng buộc PIT...")

    brief, val_out, brief_usage = await create_and_validate_brief(
        query=query,
        model=model,
        as_of_date=ctx.as_of_date,
        allowed_project_ids=ctx.principal.allowed_project_ids,
        available_tickers=sorted(ctx.available_tickers),
        catalog_profiles=ctx.catalog_profiles,
        explicit_tickers=explicit_tickers,
        chat_mode=ctx.chat_mode,
        session_context=session_ctx,
        memory_context=mem_ctx,
        trace=trace,
    )
    total_usage = brief_usage

    if val_out.denied_tickers:
        extra_blindspots.append(
            {
                "code": "ACL_DENIED_TICKERS",
                "reason": f"Một số mã nằm ngoài phạm vi truy cập: {', '.join(val_out.denied_tickers)}.",
                "scope": ",".join(val_out.denied_tickers),
            }
        )
    if val_out.fallback_used:
        extra_blindspots.append(
            {
                "code": "BRIEF_FALLBACK_DEGRADED",
                "reason": (
                    f"Không lập được đề bài hợp lệ từ mô hình sau 2 lần; dùng phương án dự phòng "
                    f"`{val_out.fallback_mode}`. Lỗi kiểm tra: {val_out.fallback_reason or 'không rõ'}"
                )[:500],
                "scope": "brief",
            }
        )
    if val_out.dropped_specs:
        extra_blindspots.append(
            {
                "code": "BRIEF_SPEC_DROPPED",
                "reason": (
                    "Một số yêu cầu trong đề bài bị loại vì hệ thống chưa kiểm chứng được: "
                    + "; ".join(val_out.dropped_specs[:4])
                )[:500],
                "scope": "brief",
            }
        )

    all_claims_preview = [f"[{p.id}] {p.statement}" for p in brief.premises_to_check] + [
        f"[{c.id}] {c.statement}" for c in brief.claims
    ]
    await _record_thought(
        {
            "id": "brief-1",
            "type": "thought",
            "turn": 1,
            "kind": "plan",
            "title": "Đề bài phân tích trung tâm",
            "text": (
                f"Trọng tâm: {brief.user_concern}\n"
                + (
                    "\n".join(f"• {ln}" for ln in all_claims_preview[:6])
                    if all_claims_preview
                    else "Cần làm rõ tiêu chí."
                )
            ),
        }
    )

    # Pure ambiguity -> ask 1 concise clarifying question immediately (0 extra LLM calls!)
    if brief.needs_clarification and brief.clarifying_question:
        q_reply = brief.clarifying_question.strip()
        streamed = False
        if on_delta is not None:
            streamed = True
            await on_delta(q_reply)
        outcome = AgentTurnOutcome(
            answer_text=q_reply,
            agent_turns=total_usage.requests,
            tool_calls_count=0,
            input_tokens=total_usage.input_tokens,
            output_tokens=total_usage.output_tokens,
            cached_tokens=total_usage.cached_tokens,
            streamed_deltas=streamed,
            extra_blindspots=extra_blindspots,
            activity_log=activity_log,
            playbook_id="brief_clarification",
            playbook_mode="BRIEF_CENTRIC",
        )
        return BriefTurnResult(outcome=outcome, brief=brief, verdict_cards=[])

    # ── Step (3): Candidate Locator (0 LLM tokens) ────────────────────────────
    if on_stage is not None:
        await on_stage("retrieval", "running", "Đang định vị đối tượng & thu thập bằng chứng song song...")

    located = await locate_candidates(brief, ctx)
    emit_trace(
        trace,
        "locator",
        analysis_tickers=list(located.analysis_tickers),
        anchor_ticker=located.anchor_ticker,
        anchor_metrics=dict(located.anchor_metrics),
        summary_lines=list(located.summary_lines),
        coverage_note=located.coverage_note,
        candidate_rows=[
            {
                "ticker": r.get("ticker"),
                "all_passed": r.get("all_passed"),
                "passed_count": r.get("passed_count"),
                "total_criteria": r.get("total_criteria"),
                "metrics": r.get("metrics"),
                "criteria_notes": r.get("criteria_notes"),
                "has_indexed_filings": r.get("has_indexed_filings"),
            }
            for r in located.candidate_rows
        ],
    )
    if brief.entities.candidate_search is not None:
        await _record_thought(
            {
                "id": "locator-1",
                "type": "thought",
                "turn": 1,
                "kind": "plan",
                "title": "Định vị đối tượng phân tích (0 LLM token)",
                "text": "\n".join(located.summary_lines[:5]) or "Không tìm thấy ứng viên khớp bộ lọc.",
            }
        )

    # ── Steps (4) & (5): Premise Check + Parallel Claim Execution + Verdict Cards ─
    exec_budget = max(2.0, (overall_deadline_mono - asyncio.get_running_loop().time()) * 0.68)
    try:
        async with asyncio.timeout(exec_budget):
            exec_res = await execute_brief_claims(
                brief=brief,
                located=located,
                ctx=ctx,
                model=model,
                overall_deadline_mono=overall_deadline_mono,
                on_thought=_record_thought,
                on_tool_call=_record_tool_call,
                on_tool_result=_record_tool_result,
                trace=trace,
            )
    except TimeoutError:
        extra_blindspots.append(
            {
                "code": "BRIEF_EVIDENCE_BUDGET_REACHED",
                "reason": "Bước thu thập bằng chứng chạm ngưỡng thời gian; tổng hợp từ phần bằng chứng đã hoàn tất.",
                "scope": "brief_executor",
            }
        )
        exec_res = BriefExecutionResult(located=located)

    total_usage = total_usage.plus(exec_res.usage)

    # ── Step (6): Compact Synthesis from Claim Verdict Cards ──────────────────
    if on_stage is not None:
        await on_stage("synthesis", "running", "Đang tổng hợp câu trả lời từ thẻ kết luận đã kiểm chứng...")

    rem_synth = max(4.0, overall_deadline_mono - asyncio.get_running_loop().time())
    answer_text, synth_usage, streamed_any, synth_timed_out = await synthesize_from_verdict_cards(
        query=query,
        brief=brief,
        exec_result=exec_res,
        ctx=ctx,
        model=model,
        pit_adjustments=val_out.pit_adjustments,
        attach_disclaimer=bool(route_decision.get("attach_investment_disclaimer")),
        on_delta=on_delta,
        timeout_seconds=rem_synth,
        trace=trace,
    )
    total_usage = total_usage.plus(synth_usage)

    if synth_timed_out and answer_text:
        extra_blindspots.append(
            {
                "code": "AGENT_SYNTHESIS_TIMEOUT_PARTIAL",
                "reason": (
                    f"Bước tổng hợp chạm ngưỡng thời gian {overall_timeout_seconds:.0f}s; "
                    "đã giữ phần nội dung đã xuất."
                ),
                "scope": "brief_synthesizer",
            }
        )

    all_cards = [*exec_res.premise_cards, *exec_res.claim_cards]
    all_specs = [*brief.premises_to_check, *brief.claims]
    ev_specs_used = sorted({ev.split(":", 1)[0] for c in all_specs for ev in c.evidence})
    outcome = AgentTurnOutcome(
        answer_text=answer_text,
        agent_turns=total_usage.requests,
        tool_calls_count=exec_res.tool_calls_count,
        input_tokens=total_usage.input_tokens,
        output_tokens=total_usage.output_tokens,
        cached_tokens=total_usage.cached_tokens,
        streamed_deltas=streamed_any,
        error_message=None if answer_text else "Synthesis returned empty output",
        extra_blindspots=extra_blindspots,
        activity_log=activity_log,
        playbook_id=f"brief:{brief.answer_shape}",
        playbook_mode="BRIEF_CENTRIC",
        prefetched_tools=["locate_candidates"] if brief.entities.candidate_search else [],
        exposed_tools=ev_specs_used,
    )
    return BriefTurnResult(outcome=outcome, brief=brief, verdict_cards=all_cards)
