"""Unified Flexible Tool-Calling Research Agent Runner & `SentenceStreamGate`.

Bridges Tier 0 SiliconFlow `Kev-4b` routing + `FinancialZeroMemLedger` + the 6 external
research tools on top of `sag_agent.AgentRuntime` (`ToolExecutionMode.PARALLEL` with
isolated per-call read-only `AsyncSession`), while optimizing LLM KV-cache reuse via a
static prefix + static tool schemas, streaming live reasoning/tool/document-selection
activity events (`thought`, `tool_call`, `tool_result`), and preventing tool-preamble or
unverified `[C99]` leakage via `SentenceStreamGate`.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sag_agent.runtime import Agent, AgentRuntime, RuntimeConfig
from sag_agent.types import (
    AgentEvent,
    AgentMessage,
    CancellationToken,
    EventType,
    ModelProvider,
    ModelRequest,
    ToolExecutionMode,
)
from sag_api.research.playbooks import execute_playbook_prefetch, resolve_playbook
from sag_api.research.tools.context import ResearchToolContext

_INLINE_C_BADGE_RE = re.compile(r"\[C(\d+)\]")
_INLINE_CALC_BADGE_RE = re.compile(r"\[(CALC-\d+)\]")
_INLINE_MEM_BADGE_RE = re.compile(r"\[MEM:(M\d+)\]")
_INLINE_PORT_BADGE_RE = re.compile(r"\[PORT:([A-Z0-9._-]{1,16})\]")
_INLINE_SESS_BADGE_RE = re.compile(r"\[SESS:(S\d+)\]")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?:\n|(?<=[.!?])\s+)")
_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>\s*", re.IGNORECASE | re.DOTALL)
_META_PREAMBLE_LINE_RE = re.compile(
    r"^(?:tôi đã có (?:đủ|đầy đủ) dữ liệu|tôi cần (?:thêm|tra cứu|kiểm tra)|"
    r"dưới đây là phần tổng hợp|hãy tổng hợp câu trả lời|"
    r"để (?:trả lời|phân tích|kiểm tra)[^\n]{0,80},\s*tôi sẽ|bây giờ tôi sẽ tổng hợp)[^\n]*(?:\n+---\s*\n+|\n+)",
    re.IGNORECASE,
)


def strip_reasoning_tags(text: str) -> str:
    """Strip explicit `<think>...</think>` blocks, orphan leading `...</think>`, and meta-plan preambles."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    close_idx = cleaned.lower().find("</think>")
    if close_idx != -1:
        cleaned = cleaned[close_idx + len("</think>") :].lstrip()
    open_idx = cleaned.lower().find("<think>")
    if open_idx != -1:
        cleaned = cleaned[:open_idx]
    cleaned = cleaned.lstrip()
    cleaned = _META_PREAMBLE_LINE_RE.sub("", cleaned).strip()
    return cleaned


_STATIC_RESEARCH_SYSTEM_PREFIX = (
    "Bạn là Chuyên viên Phân tích Nghiên cứu Chứng khoán Tổ chức cấp cao "
    "(Senior Institutional Equity Research Analyst) của AIInvest, chuyên sâu thị trường vốn Việt Nam "
    "(chuẩn mực VAS Thông tư 200/49/210/232). Kỷ luật ZERO-HALLUCINATION và Point-in-Time (PIT) tuyệt đối:\n"
    "1. KỶ LUẬT DỮ LIỆU & TRÍCH DẪN:\n"
    "   - Mọi con số tài chính mới (doanh thu, lợi nhuận, biên, nợ vay, dòng tiền, P/E, P/B, ROE, NIM, NPL, beta) "
    "BẮT BUỘC xuất phát từ `<PREFETCHED_DETERMINISTIC_EVIDENCE>` hoặc kết quả công cụ trong lượt hiện tại — "
    "TUYỆT ĐỐI không tự nhớ hay bịa số.\n"
    "   - Gắn thẻ nguồn ngay sau từng khẳng định: `[C1]`, `[C2]`... cho đoạn BCTC/Thuyết minh/BCTN/ĐHĐCĐ; "
    "`[TBL:<table_id>]` cho bảng 2D; `[SCR:<TICKER>]` cho sàng lọc định lượng; `[CALC-1]`... cho số liệu/KPI tất định "
    "và phép tính AST; `[KG:<LAYER>|<TIER>]` cho đồ thị tri thức. Không bao giờ phát minh mã `[C#]` chưa tồn tại.\n"
    "   - Không nhẩm toán: ưu tiên tuyệt đối dùng ngay KPI/tăng trưởng/tiến độ kế hoạch đã có mã `[CALC-*]` từ các "
    "công cụ tất định (`get_financial_statements`, `get_sector_kpis`, `scan_financial_risks`, `compare_peers`, "
    "`check_plan_vs_actual`); chỉ gọi `run_financial_calculator` cho công thức kịch bản chưa có sẵn.\n"
    "2. CHIẾN LƯỢC PHÂN TÍCH ĐỊNH HƯỚNG PLAYBOOK (TỐI ĐA 1–2 LƯỢT GỌI TOOL):\n"
    "   - Nếu khối `<PREFETCHED_DETERMINISTIC_EVIDENCE>` đã cung cấp bảng BCTC, KPI đặc thù ngành, cờ rủi ro và "
    "tiến độ ĐHĐCĐ, TUYỆT ĐỐI KHÔNG tìm lại số doanh thu/lợi nhuận/ROE tổng quát bằng tìm kiếm văn bản.\n"
    "   - Dùng `search_disclosures` (khóa cứng `sub_doc_slots`, `fiscal_year`, `fiscal_quarter` + `vas_terms` chuẩn "
    "VAS như 'nợ nhóm 3', 'dự phòng rủi ro tín dụng', 'vốn khả dụng', 'phải thu ngắn hạn', 'V.06') để lấy thuyết minh "
    "bóc tách chất lượng tài sản, danh mục dự án/tự doanh, chính sách kế toán và thông điệp ban lãnh đạo.\n"
    "   - Nếu `search_disclosures` trả `STATUS=NOT_FOUND` kèm danh sách kỳ đã công bố trong PIT, KHÔNG lặp lại truy "
    "vấn vào kỳ trống đó — chuyển ngay sang kỳ hợp lệ gần nhất trong PIT hoặc khai báo thẳng khoảng trống PIT.\n"
    "   - Gom toàn bộ các lệnh `search_disclosures` / `hydrate_financial_tables` độc lập chạy **song song 1 lượt**.\n"
    "3. TIÊU CHUẨN CHẤT LƯỢNG PHÂN TÍCH BROKER TỔ CHỨC (CẤM KẾT LUẬN CHUNG CHUNG):\n"
    "   - Mở đầu ngay bằng **Kết luận Điều hành (Broker Takeaway / So What?)** trong 2–3 ý sắc nét: bản chất "
    "tăng trưởng (lõi bền vững vs đột biến tài chính/hoàn nhập), điểm nổi bật về bảng cân đối/dòng tiền, "
    "và 1–2 rủi ro hoặc nút thắt trọng yếu nhà đầu tư phải theo dõi.\n"
    "   - Phân tích đúng **lăng kính đặc thù ngành** từ số liệu thực tế đã truy xuất:\n"
    "     * Ngân hàng: quy mô tín dụng/huy động, Thu nhập lãi thuần (NII) & NIM, CASA, chất lượng tài sản "
    "(nợ xấu nhóm 3–5, bao phủ nợ xấu LLR, chi phí dự phòng rủi ro tín dụng), CAR & kế hoạch cổ tức/tăng vốn.\n"
    "     * Chứng khoán: tỷ lệ vốn khả dụng (ATTC vs ngưỡng 180%), dư nợ cho vay margin & margin/VCSH, "
    "danh mục tự doanh FVTPL/AFS (lãi đã thực hiện vs đánh giá lại chưa thực hiện), nguồn vốn vay ngắn hạn.\n"
    "     * Sản xuất / Thép / Hóa chất / Vận tải biển: sản lượng & tiến độ công suất dự án trọng điểm "
    "(VD: Dung Quất 2, đội tàu), diễn biến biên gộp GPM theo chu kỳ giá đầu vào/đầu ra, CAPEX & áp lực nợ vay.\n"
    "     * Công nghệ / Bán lẻ / Tiêu dùng: tách bạch doanh thu & biên từng mảng/chuỗi cốt lõi, chất lượng "
    "dòng tiền (CFO/LNST), tiền mặt ròng (Net Cash), tốc độ mở rộng điểm bán hoặc giá trị hợp đồng ký mới.\n"
    "   - Chủ động **đối chất** lời ban lãnh đạo trong BCTN/ĐHĐCĐ với số liệu kiểm toán BCTC/Thuyết minh "
    "và nêu tín hiệu cảnh báo: CFO < LNST, phải thu/tồn kho phình to, đòn bẩy ngắn hạn tăng, kế hoạch quá tham vọng.\n"
    "4. CẤU TRÚC TRÌNH BÀY & TUÂN THỦ:\n"
    "   - Cấu trúc rõ ràng bằng tiếng Việt chuyên nghiệp: (1) **Kết luận điều hành (Broker Takeaway)**, "
    "(2) **Bảng tổng hợp số liệu & đối chiếu Kế hoạch / Kỳ trước**, "
    "(3) **Phân tích động lực lõi & chất lượng lợi nhuận**, "
    "(4) **Rủi ro trọng yếu, Biến số cần theo dõi (Watchlist) & Khoảng trống dữ liệu PIT**.\n"
    "   - Nếu thiếu dữ liệu kiểm chứng trong phạm vi PIT, khai báo thẳng thắn (`Không tìm thấy dữ liệu kiểm chứng "
    "trong phạm vi PIT hiện tại`) thay vì suy diễn. KHÔNG đưa khuyến nghị mua/bán hay giá mục tiêu."
)

_TOOL_TO_SSE_STAGE: dict[str, tuple[str, str]] = {
    "screen_financial_universe": ("retrieval", "Sàng lọc định lượng danh mục PIT (Core PG + BCTC)"),
    "search_disclosures": ("retrieval", "Tra cứu Thuyết minh, BCTN & ĐHĐCĐ (bộ lọc đẩy xuống Qdrant)"),
    "search_corporate_filings": ("retrieval", "Tra cứu BCTC, Thuyết minh, BCTN & Nghị quyết ĐHĐCĐ"),
    "hydrate_financial_tables": ("table_hydration", "Nạp ma trận bảng tài chính 2D (giữ mã Thuyết minh)"),
    "query_knowledge_graph": ("graph_hop", "Truy vấn Đồ thị Tri thức 3 tầng (Vĩ mô / Chuỗi giá trị / Đối thủ)"),
    "run_financial_calculator": ("calculator", "Tính toán tài chính chính xác qua AST Sandbox"),
    "recall_session_memory": ("routing", "Truy xuất ngữ cảnh phiên & ký ức PIT-safe"),
    "get_financial_statements": ("calculator", "Nạp bảng số liệu BCTC chuẩn hóa (Core PG PIT)"),
    "get_sector_kpis": ("calculator", "Tính bộ chỉ số tài chính theo chuẩn mực ngành"),
    "scan_financial_risks": ("calculator", "Quét chất lượng lợi nhuận & dấu hiệu rủi ro tài chính"),
    "compare_peers": ("calculator", "So sánh đồng ngành cùng chuẩn mực & xếp hạng KPI"),
    "check_plan_vs_actual": ("calculator", "Đối chiếu kế hoạch ĐHĐCĐ với thực hiện lũy kế"),
}


def _summarize_tool_args(tool_name: str, arguments: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    """Build a crisp Vietnamese argument preview + ticker/query lists for live UI streaming."""
    tickers = [
        str(t).strip().upper()
        for t in (arguments.get("tickers") or ())
        if str(t).strip()
    ]
    if not tickers and arguments.get("ticker"):
        tk = str(arguments.get("ticker")).strip().upper()
        if tk:
            tickers = [tk]

    queries: list[str] = []
    if tool_name in {"search_disclosures", "search_corporate_filings"}:
        vas_qs = [
            str(q).strip()
            for q in (arguments.get("vas_terms") or arguments.get("bm25_vas_queries") or ())
            if str(q).strip()
        ]
        q_single = str(arguments.get("question") or "").strip()
        dense_qs = [q_single] if q_single else [
            str(q).strip() for q in (arguments.get("dense_semantic_queries") or ()) if str(q).strip()
        ]
        queries = list(dict.fromkeys([*vas_qs, *dense_qs]))[:4]
        slots = [str(s).strip().upper() for s in (arguments.get("sub_doc_slots") or ()) if str(s).strip()]
        fy = arguments.get("fiscal_year")
        fq = arguments.get("fiscal_quarter")
        period_lbl = f" · Q{fq}/{fy}" if (fy and fq) else (f" · FY{fy}" if fy else "")
        tk_txt = ", ".join(tickers) if tickers else "toàn danh mục"
        slot_txt = f" · {', '.join(slots)}" if slots else ""
        return f"Mã {tk_txt}{slot_txt}{period_lbl} ({len(vas_qs)} VAS + {len(dense_qs)} ngữ nghĩa)", tickers, queries

    if tool_name == "hydrate_financial_tables":
        tids = [str(t).strip() for t in (arguments.get("table_ids") or ()) if str(t).strip()]
        stmt = str(arguments.get("statement_kind") or "ALL").upper()
        fy = arguments.get("fiscal_year")
        parts: list[str] = []
        if tickers:
            parts.append(f"Mã {', '.join(tickers)}")
        if tids:
            parts.append(f"{len(tids)} mã bảng")
        if stmt != "ALL":
            parts.append(f"loại {stmt}")
        if fy:
            parts.append(f"FY{fy}")
        return " · ".join(parts) or "Nạp bảng tài chính 2D", tickers, tids[:4]

    if tool_name == "query_knowledge_graph":
        mf = str(arguments.get("macro_factor") or "").strip()
        parts = []
        if tickers:
            parts.append(f"Mã {', '.join(tickers)}")
        if mf:
            parts.append(f"yếu tố vĩ mô `{mf}`")
        return " · ".join(parts) or "Truy vấn đồ thị 3 tầng", tickers, ([mf] if mf else [])

    if tool_name == "run_financial_calculator":
        desc = str(arguments.get("description") or "").strip()
        formula = str(arguments.get("formula") or "").strip()
        preview = f"{desc}: `{formula}`" if desc and desc != formula else f"`{formula}`"
        return preview[:160], tickers, ([formula] if formula else [])

    if tool_name == "screen_financial_universe":
        sectors = [str(s).strip() for s in (arguments.get("sectors") or ()) if str(s).strip()]
        sort_by = str(arguments.get("sort_by") or "").strip()
        parts = []
        if tickers:
            parts.append(f"Mã {', '.join(tickers)}")
        if sectors:
            parts.append(f"ngành {', '.join(sectors)}")
        if sort_by:
            parts.append(f"xếp theo `{sort_by}`")
        if arguments.get("use_default_quality_screen"):
            parts.append("4 trụ cột chất lượng")
        return " · ".join(parts) or "Sàng lọc toàn danh mục PIT", tickers, sectors[:3]

    if tool_name == "recall_session_memory":
        scope = str(arguments.get("scope") or "current_session")
        return f"Phạm vi: {scope}", tickers, []

    if tool_name in {
        "get_financial_statements",
        "get_sector_kpis",
        "scan_financial_risks",
        "compare_peers",
        "check_plan_vs_actual",
    }:
        tk_txt = ", ".join(tickers) if tickers else "toàn danh mục"
        fy = arguments.get("fiscal_year")
        view = arguments.get("view")
        extra = f" · FY{fy}" if fy else (f" · {view}" if view else "")
        return f"Mã {tk_txt}{extra}", tickers, []

    return tool_name, tickers, queries


def _summarize_tool_result(tool_name: str, details: Mapping[str, Any], error_msg: str | None = None) -> str:
    """Build a crisp Vietnamese one-line outcome summary for a completed/failed tool."""
    if error_msg:
        return f"Lỗi thực thi: {error_msg[:140]}"
    if tool_name in {
        "get_financial_statements",
        "get_sector_kpis",
        "scan_financial_risks",
        "compare_peers",
        "check_plan_vs_actual",
    }:
        cnt = int(details.get("returned_count") or 0)
        return f"Tính toán tất định {cnt} chỉ tiêu/cờ ([CALC-*] đã khóa sổ PIT)"
    if tool_name in {"search_disclosures", "search_corporate_filings"}:
        if str(details.get("status") or "") == "NOT_FOUND" or int(details.get("returned_count") or 0) == 0:
            return "NOT_FOUND · Không có đoạn thuyết minh khớp bộ lọc PIT"
        ret = int(details.get("returned_count") or 0)
        rec = int(details.get("recall_count") or ret)
        grade = str(details.get("crag_grade") or "").upper()
        reranker = str(details.get("reranker_model") or "")
        short_rr = reranker.split("/")[-1] if reranker else "Hybrid RRF"
        grade_part = f" · CRAG {grade}" if grade else ""
        return f"Chọn lọc {ret}/{rec} đoạn{grade_part} · {short_rr}"
    if tool_name == "hydrate_financial_tables":
        ret = int(details.get("returned_count") or 0)
        tbls = details.get("hydrated_tables") or []
        verified = sum(
            1 for t in tbls if isinstance(t, Mapping) and str(t.get("verification_status") or "") == "VERIFIED"
        )
        if ret == 0:
            return "Không có bảng 2D khớp bộ lọc"
        return f"Đã nạp {ret} bảng 2D ({verified} VERIFIED)"
    if tool_name == "query_knowledge_graph":
        hops = int(details.get("returned_hops") or 0)
        return f"Truy xuất {hops} cạnh tri thức (Vĩ mô / Chuỗi giá trị / Đối thủ)"
    if tool_name == "run_financial_calculator":
        cid = str(details.get("calc_id") or "CALC")
        fmt = str(details.get("formatted") or "")
        desc = str(details.get("description") or "")
        return f"[{cid}] {desc}: {fmt}" if fmt else f"Hoàn tất {cid}"
    if tool_name == "screen_financial_universe":
        matched = int(details.get("matched_count") or 0)
        ret = int(details.get("returned_count") or 0)
        return f"Khớp {matched} mã · trả về {ret} mã định lượng PIT"
    if tool_name == "recall_session_memory":
        scope = str(details.get("scope") or "current_session")
        cnt = details.get("count", details.get("recalled_turns", 0))
        return f"Truy xuất {cnt} mục ({scope})"
    return "Hoàn tất"


@dataclass
class SentenceStreamGate:
    """Buffers turn deltas so tool preambles, `<think>` blocks, and hallucinated badges never leak into prose."""

    valid_cid_ceiling: Callable[[], int]
    valid_calc_ids: Callable[[], set[str]] | None = None
    valid_memory_ids: Callable[[], set[str]] | None = None
    valid_portfolio_symbols: Callable[[], set[str]] | None = None
    valid_session_ids: Callable[[], set[str]] | None = None
    lookahead_chars: int = 220
    _buffer: str = ""
    _suppressed_for_tool: bool = False
    _flushed_any_prose: bool = False
    _in_think_block: bool = False
    _extracted_thoughts: list[str] = field(default_factory=list)

    def reset_for_turn(self, *, tools_enabled: bool) -> None:
        self._buffer = ""
        self._suppressed_for_tool = False
        self._flushed_any_prose = False
        self._in_think_block = False
        self._tools_enabled = tools_enabled
        self._extracted_thoughts = []

    def mark_pending_tool_call(self) -> str:
        """Suppress prose output for this turn and return any buffered planning text as a thought."""
        self._suppressed_for_tool = True
        captured = strip_reasoning_tags(self._buffer)
        self._buffer = ""
        return captured

    def drain_extracted_thoughts(self) -> list[str]:
        if not self._extracted_thoughts:
            return []
        out = list(self._extracted_thoughts)
        self._extracted_thoughts.clear()
        return out

    def _strip_buffered_think(self) -> bool:
        """Extract `<think>...</think>` or orphan leading `...</think>` into `_extracted_thoughts`.

        Returns True while still inside an unclosed `<think>` block so callers hold flushing prose.
        """
        buf = self._buffer
        if not buf:
            return self._in_think_block

        for match in _THINK_BLOCK_RE.finditer(buf):
            inner = (match.group(1) or "").strip()
            if inner:
                self._extracted_thoughts.append(inner)
        buf = _THINK_BLOCK_RE.sub("", buf)

        low = buf.lower()
        close_idx = low.find("</think>")
        if close_idx != -1:
            orphan = buf[:close_idx].strip()
            if orphan:
                self._extracted_thoughts.append(orphan)
            buf = buf[close_idx + len("</think>") :].lstrip()
            self._in_think_block = False
            low = buf.lower()

        open_idx = low.find("<think>")
        if open_idx != -1:
            buf = buf[:open_idx]
            self._in_think_block = True
        elif self._in_think_block:
            buf = ""

        self._buffer = buf
        return self._in_think_block

    def _sanitize_sentence(self, text: str) -> str:
        max_cid = max(0, int(self.valid_cid_ceiling()))
        allowed_calcs = set(self.valid_calc_ids()) if self.valid_calc_ids is not None else None
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

        def _check_calc(match: re.Match[str]) -> str:
            cid = match.group(1)
            if allowed_calcs is None or cid in allowed_calcs:
                return match.group(0)
            return ""

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
        out = _INLINE_CALC_BADGE_RE.sub(_check_calc, out)
        out = _INLINE_MEM_BADGE_RE.sub(_check_mem, out)
        out = _INLINE_PORT_BADGE_RE.sub(_check_port, out)
        out = _INLINE_SESS_BADGE_RE.sub(_check_sess, out)
        return out

    def push_delta(self, delta: str) -> list[str]:
        """Accept streaming delta; return verified complete sentences safe to emit to SSE."""
        if self._suppressed_for_tool or not delta:
            return []
        self._buffer += delta
        if self._strip_buffered_think():
            return []

        if not self._flushed_any_prose:
            self._buffer = _META_PREAMBLE_LINE_RE.sub("", self._buffer.lstrip())

        # On tool-enabled turns, hold 100% of buffered text until turn completion (`flush_turn_end`)
        # or `mark_pending_tool_call()`, so even long >220-char planning monologues never leak to SSE.
        if getattr(self, "_tools_enabled", True):
            return []

        parts = _SENTENCE_BOUNDARY_RE.split(self._buffer)
        if len(parts) <= 1:
            return []

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
        self._strip_buffered_think()
        if not self._flushed_any_prose:
            self._buffer = _META_PREAMBLE_LINE_RE.sub("", self._buffer.lstrip())
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
    activity_log: list[dict[str, Any]] = field(default_factory=list)
    playbook_id: str | None = None
    playbook_mode: str | None = None
    prefetched_tools: list[str] = field(default_factory=list)
    exposed_tools: list[str] = field(default_factory=list)


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
    playbook_guidance: str = "",
    prefetched_block: str = "",
) -> str:
    """Assemble KV-cache-friendly dynamic 3-tier tail (Tier 0 Pinned + Tier 1 Adaptive Zero-Mem +
    Tier 2 Gated Memory + Playbook Guidance + 0-Token Pre-Fetched Evidence + User Query).
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
        f"- Playbook nghiệp vụ: {route_decision.get('task_action', 'fact_lookup')}",
        f"- Định dạng trình bày mong muốn: {route_decision.get('output_style', 'fact_citation')}",
    ]
    if playbook_guidance:
        hint_lines.append(f"- Chỉ dẫn Playbook: {playbook_guidance}")
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
    if prefetched_block:
        blocks.append(prefetched_block)
    blocks.append(f"Câu hỏi của nhà đầu tư:\n{query}")
    return "\n\n".join(blocks)


async def _direct_synthesis_fallback(
    *,
    model: ModelProvider,
    instructions: str,
    query: str,
    ctx: ResearchToolContext,
    on_delta: Callable[[str], Awaitable[None]] | None,
) -> tuple[str, int, int, int, bool]:
    """One-shot no-tool broker synthesis rescue if the multi-turn loop ended with empty prose."""
    ev = ctx.evidence
    digest_parts: list[str] = [f"Câu hỏi gốc: {query}\nNgày khóa sổ PIT: {ctx.as_of_date.isoformat()}"]
    if ev.sections:
        sec_lines = ["=== ĐOẠN BCTC / BCTN / ĐHĐCĐ ĐÃ TRUY XUẤT ==="]
        for idx, sec in enumerate(ev.sections[:14], 1):
            tk = str(getattr(sec, "ticker", "") or "")
            slot = str(getattr(sec, "sub_doc_slot", "") or "")
            page = getattr(sec, "page_from", 1)
            heading = str(getattr(sec, "heading", "") or "").strip()
            body = str(getattr(sec, "content", "") or "").strip()[:900]
            sec_lines.append(f"[C{idx}] ({tk} | {slot} | trang {page}) {heading}\n{body}")
        digest_parts.append("\n\n".join(sec_lines))
    if ev.hydrated_tables:
        tbl_lines = ["=== BẢNG TÀI CHÍNH 2D ==="]
        for tid, t in list(ev.hydrated_tables.items())[:4]:
            title = str(t.get("title") or "")
            status = str(t.get("verification_status") or "")
            rows_preview = []
            for r in list(t.get("rows") or ())[:20]:
                if isinstance(r, Mapping):
                    vals = ", ".join(str(v) for v in (r.get("values") or ()) if v is not None)
                    rows_preview.append(f"  - {r.get('label', '')}: {vals}")
            tbl_lines.append(f"[TBL:{tid}] {title} ({status})\n" + "\n".join(rows_preview))
        digest_parts.append("\n\n".join(tbl_lines))
    if ev.screening_rows:
        scr_lines = ["=== DỮ LIỆU ĐỊNH LƯỢNG SÀNG LỌC ==="]
        for r in ev.screening_rows[:8]:
            tk = str(r.get("ticker") or "")
            scr_lines.append(
                f"[SCR:{tk}] FY={r.get('fiscal_year')} PE={r.get('pe')} PB={r.get('pb')} "
                f"ROE={r.get('roe')} GM={r.get('gross_margin')} NM={r.get('net_margin')}"
            )
        digest_parts.append("\n".join(scr_lines))
    if ev.calculator_traces:
        calc_lines = ["=== KẾT QUẢ TÍNH TOÁN TẤT ĐỊNH & BẢNG BCTC CHUẨN HÓA ==="]
        for c in ev.calculator_traces:
            fmt = str(c.get("formatted") or "")
            if fmt:
                calc_lines.append(
                    f"[{c.get('calc_id')}] {c.get('description')}: {fmt} (`{c.get('formula')}`)"
                )
            elif isinstance(c.get("inputs"), Mapping):
                inp_items = list(c["inputs"].items())[:24]
                kv_txt = "; ".join(
                    f"{k}={v.get('value') if isinstance(v, Mapping) else v}" for k, v in inp_items
                )
                calc_lines.append(f"[{c.get('calc_id')}] {c.get('description')}: {kv_txt}")
        digest_parts.append("\n".join(calc_lines))
    if ev.graph_hops:
        kg_lines = ["=== ĐỒ THỊ TRI THỨC 3 TẦNG ==="]
        for h in ev.graph_hops[:10]:
            kg_lines.append(
                f"[KG:{h.get('layer')}|{h.get('verification_tier')}] {h.get('src_label')} -> {h.get('dst_label')}"
            )
        digest_parts.append("\n".join(kg_lines))

    digest_parts.append(
        "Hãy tổng hợp báo cáo phân tích Broker hoàn chỉnh chỉ từ các bằng chứng trên, giữ đúng mã `[C#]`, "
        "`[TBL:*]`, `[SCR:*]`, `[CALC-*]`, `[KG:*]` và nêu rõ khoảng trống dữ liệu PIT nếu có."
    )
    req = ModelRequest(
        messages=(
            AgentMessage(role="system", content=instructions),
            AgentMessage(role="user", content="\n\n".join(digest_parts)),
        ),
        tools=(),
        turn=1,
    )
    parts: list[str] = []
    in_tok = out_tok = cached_tok = 0
    streamed = False
    async for chunk in model.stream_turn(req, CancellationToken()):
        if chunk.usage is not None:
            in_tok = max(in_tok, chunk.usage.input_tokens)
            out_tok = max(out_tok, chunk.usage.output_tokens)
            cached_tok = max(cached_tok, chunk.usage.cached_tokens)
        if chunk.text_delta:
            parts.append(chunk.text_delta)
    cleaned = strip_reasoning_tags("".join(parts))
    if cleaned and on_delta is not None:
        streamed = True
        await on_delta(cleaned)
    return cleaned, in_tok, out_tok, cached_tok, streamed


async def run_research_agent_turn(
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
) -> AgentTurnOutcome:
    """Execute the unified `sag_agent` multi-hop research loop with Playbook pre-fetch & tool scoping."""
    playbook = resolve_playbook(
        route_decision,
        chat_mode=ctx.chat_mode,
        explicit_tickers=explicit_tickers,
    )
    tools = playbook.exposed_tools
    raw_max_turns = int(route_decision.get("max_agent_turns") or 3)
    if bool(route_decision.get("fallback_used")):
        turn_cap = 4 if ctx.chat_mode == "MOAT" else 3
        effective_max_turns = max(2, min(turn_cap, raw_max_turns))
    else:
        effective_max_turns = playbook.max_turns

    prefetched_block, prefetched_tools = await execute_playbook_prefetch(
        playbook,
        ctx=ctx,
        explicit_tickers=explicit_tickers,
    )

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
        playbook_guidance=playbook.guidance_vi,
        prefetched_block=prefetched_block,
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
    run_started = time.perf_counter()
    streamed_any = False
    turns_seen = 0
    tool_calls_seen = 0
    current_turn_tools_enabled = True
    activity_log: list[dict[str, Any]] = []
    tool_entries_by_call_id: dict[str, dict[str, Any]] = {}
    turn_tool_names: dict[int, list[str]] = {}
    reasoning_buffer_by_turn: dict[int, list[str]] = {}
    thought_seq = 0

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - run_started) * 1000)

    async def _emit_thought(
        *,
        turn: int,
        kind: str,
        title: str,
        text: str,
        append: bool = False,
        step_id: str | None = None,
    ) -> None:
        nonlocal thought_seq
        clean_text = text if append else text.strip()
        if not clean_text and not title:
            return
        if step_id is None:
            thought_seq += 1
            step_id = f"thought-{thought_seq}"
        evt = {
            "id": step_id,
            "type": "thought",
            "turn": turn,
            "kind": kind,
            "title": title,
            "text": clean_text,
            "append": append,
            "timestamp_ms": _elapsed_ms(),
        }
        if not append:
            activity_log.append(evt)
        else:
            existing = next((item for item in activity_log if item.get("id") == step_id), None)
            if existing is not None:
                existing["text"] = str(existing.get("text") or "") + clean_text
                existing["timestamp_ms"] = evt["timestamp_ms"]
            else:
                activity_log.append({**evt, "append": False})
        if on_thought is not None:
            await on_thought(evt)

    async def _flush_gate_thoughts(turn: int) -> None:
        for t_txt in gate.drain_extracted_thoughts():
            if t_txt:
                await _emit_thought(
                    turn=turn,
                    kind="reasoning",
                    title=f"Suy luận nội bộ · Lượt {turn}",
                    text=t_txt[:1800],
                )

    async def _handle_event(event: AgentEvent) -> None:
        nonlocal streamed_any, turns_seen, tool_calls_seen, current_turn_tools_enabled
        ev_turn = int(event.turn or turns_seen or 1)

        if event.type == EventType.TURN_STARTED:
            turns_seen = max(turns_seen, ev_turn)
            tool_cnt = int(event.payload.get("tool_count") or 0)
            current_turn_tools_enabled = turns_seen <= effective_max_turns and tool_cnt > 0
            gate.reset_for_turn(tools_enabled=current_turn_tools_enabled)
            if turns_seen == 1:
                tk_scope = ", ".join(explicit_tickers) if explicit_tickers else "tự nhận diện từ câu hỏi"
                await _emit_thought(
                    turn=1,
                    kind="plan",
                    title="Lượt 1 · Lập kế hoạch tra cứu đa nguồn",
                    text=(
                        f"Đang xác định công cụ cần gọi (BCTC/Thuyết minh, Bảng 2D, Sàng lọc, Đồ thị 3 tầng) "
                        f"cho phạm vi: {tk_scope} · khóa sổ PIT {ctx.as_of_date.isoformat()}."
                    ),
                )
            elif not current_turn_tools_enabled:
                await _emit_thought(
                    turn=turns_seen,
                    kind="plan",
                    title=f"Lượt {turns_seen} · Tổng hợp báo cáo phân tích Broker",
                    text=(
                        f"Đã thu thập {len(ctx.evidence.sections)} đoạn văn bản, "
                        f"{len(ctx.evidence.hydrated_tables)} bảng 2D, "
                        f"{len(ctx.evidence.calculator_traces)} phép tính AST và "
                        f"{len(ctx.evidence.graph_hops)} liên kết KG — đang biên soạn kết luận & kiểm chứng."
                    ),
                )
                if on_stage is not None:
                    await on_stage("synthesis", "running", f"Đang tổng hợp báo cáo phân tích (lượt {turns_seen})...")
            else:
                await _emit_thought(
                    turn=turns_seen,
                    kind="plan",
                    title=f"Lượt {turns_seen} · Đánh giá đủ bằng chứng hay đào sâu thêm",
                    text=(
                        f"Hiện có {len(ctx.evidence.sections)} đoạn nguồn, "
                        f"{len(ctx.evidence.hydrated_tables)} bảng 2D, "
                        f"{len(ctx.evidence.calculator_traces)} phép tính — "
                        "kiểm tra khoảng trống dữ liệu hoặc viết báo cáo."
                    ),
                )
                if on_stage is not None:
                    await on_stage(
                        "synthesis",
                        "running",
                        f"Đang rà soát bằng chứng & tính toán (lượt {turns_seen})...",
                    )

        elif event.type == EventType.REASONING_DELTA:
            r_delta = str(event.payload.get("delta") or "")
            if r_delta:
                reasoning_buffer_by_turn.setdefault(ev_turn, []).append(r_delta)
                await _emit_thought(
                    turn=ev_turn,
                    kind="reasoning",
                    title=f"Suy luận mô hình · Lượt {ev_turn}",
                    text=r_delta,
                    append=True,
                    step_id=f"reasoning-turn-{ev_turn}",
                )

        elif event.type == EventType.TOOL_STARTED:
            tool_calls_seen += 1
            call_id = str(event.payload.get("tool_call_id") or f"call-{tool_calls_seen}")
            tname = str(event.payload.get("name") or event.payload.get("tool_name") or "")
            tlabel = str(event.payload.get("label") or "")
            raw_args = event.payload.get("arguments")
            args_map = raw_args if isinstance(raw_args, Mapping) else {}
            stage_name, stage_default_label = _TOOL_TO_SSE_STAGE.get(
                tname, ("retrieval", f"Chạy công cụ {tname}")
            )
            display_label = tlabel or stage_default_label
            args_preview, arg_tickers, arg_queries = _summarize_tool_args(tname, args_map)
            turn_tool_names.setdefault(ev_turn, []).append(display_label)

            call_evt: dict[str, Any] = {
                "id": call_id,
                "call_id": call_id,
                "type": "tool_call",
                "turn": ev_turn,
                "tool": tname,
                "label": display_label,
                "stage": stage_name,
                "status": "running",
                "tickers": arg_tickers,
                "queries": arg_queries,
                "sub_doc_slots": [
                    str(s).strip().upper()
                    for s in (args_map.get("sub_doc_slots") or ())
                    if str(s).strip()
                ],
                "args_preview": args_preview,
                "timestamp_ms": _elapsed_ms(),
            }
            tool_entries_by_call_id[call_id] = call_evt
            activity_log.append(call_evt)

            if on_tool_call is not None:
                await on_tool_call(call_evt)
            if on_stage is not None:
                await on_stage(stage_name, "running", f"{display_label}: {args_preview}")

        elif event.type == EventType.TOOL_COMPLETED:
            call_id = str(event.payload.get("tool_call_id") or "")
            tname = str(event.payload.get("name") or event.payload.get("tool_name") or "")
            duration_ms = int(event.payload.get("duration_ms") or 0)
            details = event.payload.get("details")
            det_map = details if isinstance(details, Mapping) else {}
            stage_name, stage_default_label = _TOOL_TO_SSE_STAGE.get(tname, ("retrieval", tname))
            existing = tool_entries_by_call_id.get(call_id)
            display_label = str(existing.get("label") if existing else stage_default_label)
            summary = _summarize_tool_result(tname, det_map)

            result_fields: dict[str, Any] = {
                "status": "done",
                "duration_ms": duration_ms,
                "summary": summary,
                "crag_grade": det_map.get("crag_grade"),
                "reranker_model": det_map.get("reranker_model"),
                "recall_count": det_map.get("recall_count"),
                "returned_count": det_map.get("returned_count", det_map.get("returned_hops")),
                "selected_documents": list(det_map.get("selected_documents") or ())[:12],
                "hydrated_tables": list(det_map.get("hydrated_tables") or ())[:6],
                "kg_hops": list(det_map.get("kg_hops") or ())[:8],
                "screening_preview": list(det_map.get("screening_preview") or ())[:6],
                "calc_result": (
                    {
                        "calc_id": det_map.get("calc_id"),
                        "formula": det_map.get("formula"),
                        "description": det_map.get("description"),
                        "formatted": det_map.get("formatted"),
                    }
                    if tname == "run_financial_calculator" and det_map.get("calc_id")
                    else None
                ),
                "completed_at_ms": _elapsed_ms(),
            }
            if existing is not None:
                existing.update(result_fields)
                res_evt = dict(existing)
            else:
                res_evt = {
                    "id": call_id or f"tool-res-{tool_calls_seen}",
                    "call_id": call_id,
                    "type": "tool_call",
                    "turn": ev_turn,
                    "tool": tname,
                    "label": display_label,
                    "stage": stage_name,
                    **result_fields,
                }
                activity_log.append(res_evt)

            if on_tool_result is not None:
                await on_tool_result(res_evt)
            if on_stage is not None:
                await on_stage(stage_name, "done", summary)

        elif event.type == EventType.TOOL_FAILED:
            call_id = str(event.payload.get("tool_call_id") or "")
            tname = str(event.payload.get("name") or "")
            duration_ms = int(event.payload.get("duration_ms") or 0)
            err_obj = event.payload.get("error")
            err_msg = (
                str(err_obj.get("message") or err_obj.get("code") or "Tool error")
                if isinstance(err_obj, Mapping)
                else str(err_obj or "Tool error")
            )
            stage_name, stage_default_label = _TOOL_TO_SSE_STAGE.get(tname, ("retrieval", tname))
            existing = tool_entries_by_call_id.get(call_id)
            display_label = str(existing.get("label") if existing else stage_default_label)
            summary = _summarize_tool_result(tname, {}, error_msg=err_msg)
            fail_fields = {
                "status": "error",
                "duration_ms": duration_ms,
                "summary": summary,
                "error": err_msg,
                "completed_at_ms": _elapsed_ms(),
            }
            if existing is not None:
                existing.update(fail_fields)
                fail_evt = dict(existing)
            else:
                fail_evt = {
                    "id": call_id or f"tool-fail-{tool_calls_seen}",
                    "call_id": call_id,
                    "type": "tool_call",
                    "turn": ev_turn,
                    "tool": tname,
                    "label": display_label,
                    "stage": stage_name,
                    **fail_fields,
                }
                activity_log.append(fail_evt)
            if on_tool_result is not None:
                await on_tool_result(fail_evt)
            if on_stage is not None:
                await on_stage(stage_name, "error", summary)

        elif event.type == EventType.MESSAGE_DELTA:
            if event.payload.get("has_pending_tool_call"):
                captured_plan = gate.mark_pending_tool_call()
                await _flush_gate_thoughts(ev_turn)
                if captured_plan and len(captured_plan) >= 12:
                    await _emit_thought(
                        turn=ev_turn,
                        kind="reasoning",
                        title=f"Kế hoạch gọi công cụ · Lượt {ev_turn}",
                        text=captured_plan[:600],
                    )
                return
            delta_txt = str(event.payload.get("delta") or "")
            if delta_txt:
                out_chunks = gate.push_delta(delta_txt)
                await _flush_gate_thoughts(ev_turn)
                if on_delta is not None:
                    for chunk_out in out_chunks:
                        streamed_any = True
                        await on_delta(chunk_out)

        elif event.type == EventType.MESSAGE_COMPLETED:
            has_tc = bool(event.payload.get("has_tool_calls"))
            out_chunks = gate.flush_turn_end(has_tool_calls=has_tc)
            await _flush_gate_thoughts(ev_turn)
            if on_delta is not None:
                for chunk_out in out_chunks:
                    streamed_any = True
                    await on_delta(chunk_out)

    if prefetched_tools:
        await _emit_thought(
            turn=0,
            kind="plan",
            title=f"Playbook `{playbook.playbook_id}` · Tiền nạp tất định (0 LLM token)",
            text=(
                f"Đã tiền nạp song song: {', '.join(prefetched_tools)} "
                f"({len(ctx.evidence.calculator_traces)} chỉ tiêu `[CALC-*]` khóa sổ PIT). "
                f"Công cụ mở cho mô hình ({len(tools)}): {', '.join(playbook.exposed_tool_names)}."
            ),
        )

    init_choice = (
        "required"
        if (playbook.require_qualitative_turn or not prefetched_tools)
        else None
    )

    agent = Agent(
        name="aiinvest_stock_research_agent",
        model=model,
        instructions=instructions,
        tools=tools,
        # Force Turn 1 tool call when qualitative notes are required or 0 pre-fetch evidence exists
        initial_tool_choice=init_choice,
        max_turns=effective_max_turns,
        finalize_on_max_turns=True,
        final_instructions=(
            "Đã hoàn tất bước thu thập dữ liệu. Hãy tổng hợp ngay báo cáo phân tích Broker hoàn chỉnh chỉ từ "
            "các bằng chứng đã thu thập và tiền nạp ở trên (KHÔNG gọi thêm công cụ). Trình bày đủ: Kết luận điều hành, "
            "Bảng đối chiếu số liệu, Phân tích động lực & chất lượng lợi nhuận theo đặc thù ngành, Rủi ro/Watchlist; "
            "gắn đủ mã `[C#]`, `[TBL:*]`, `[SCR:*]`, `[CALC-*]`, `[KG:*]` và nêu rõ mọi khoảng trống PIT."
        ),
    )

    runtime = AgentRuntime(
        RuntimeConfig(
            max_turns=effective_max_turns,
            tool_timeout_seconds=30.0,
            tool_execution=ToolExecutionMode.PARALLEL,
        )
    )
    def _sanitize_calc_badges(text_in: str) -> str:
        if not text_in:
            return text_in
        valid_calcs = {
            str(c.get("calc_id") or "")
            for c in ctx.evidence.calculator_traces
            if c.get("calc_id")
        }
        return _INLINE_CALC_BADGE_RE.sub(
            lambda m: m.group(0) if m.group(1) in valid_calcs else "",
            text_in,
        )

    tool_loop_budget = max(0.5, overall_timeout_seconds * 0.68)
    synthesis_reserve = max(0.4, overall_timeout_seconds - tool_loop_budget)
    handle = None
    timed_out_loop = False
    result = None

    try:
        async with asyncio.timeout(tool_loop_budget):
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
        timed_out_loop = True

    if result is not None:
        cleaned_answer = _sanitize_calc_badges(strip_reasoning_tags((result.output or "").strip()))
        in_tok = result.usage.input_tokens
        out_tok = result.usage.output_tokens
        cached_tok = result.usage.cached_tokens
        req_count = result.usage.requests
        err_msg = result.error.message if result.error is not None else None
    else:
        cleaned_answer = ""
        partial_usage = handle.context.usage if handle is not None else None
        in_tok = partial_usage.input_tokens if partial_usage is not None else 0
        out_tok = partial_usage.output_tokens if partial_usage is not None else 0
        cached_tok = partial_usage.cached_tokens if partial_usage is not None else 0
        req_count = partial_usage.requests if partial_usage is not None else turns_seen
        err_msg = f"Tool loop reached budget ({tool_loop_budget:.1f}s/{overall_timeout_seconds:.1f}s)"

    extra_blindspots: list[dict[str, str]] = []

    # Safety net: if the multi-turn loop timed out or finished with empty prose despite gathered evidence,
    # run one clean direct synthesis call inside the reserved synthesis window so investors receive a full
    # grounded broker report AND token/cost telemetry is never lost.
    if not cleaned_answer and not streamed_any and ctx.evidence.has_any_evidence():
        try:
            await _emit_thought(
                turn=turns_seen + 1,
                kind="plan",
                title="Tổng hợp từ tập bằng chứng đã kiểm chứng",
                text="Đang tổng hợp báo cáo phân tích đầy đủ từ toàn bộ bằng chứng và phép tính đã thu thập...",
            )
            async with asyncio.timeout(synthesis_reserve if timed_out_loop else 35.0):
                rescued, r_in, r_out, r_cached, r_streamed = await _direct_synthesis_fallback(
                    model=model,
                    instructions=instructions,
                    query=query,
                    ctx=ctx,
                    on_delta=on_delta,
                )
            if rescued:
                cleaned_answer = _sanitize_calc_badges(rescued)
                in_tok += r_in
                out_tok += r_out
                cached_tok += r_cached
                streamed_any = streamed_any or r_streamed
        except Exception:  # noqa: BLE001
            pass

    if timed_out_loop:
        extra_blindspots.append(
            {
                "code": "AGENT_TOOL_LOOP_BUDGET_REACHED" if cleaned_answer else "AGENT_LOOP_TIMEOUT",
                "reason": (
                    f"Vòng lặp công cụ chạm ngưỡng {tool_loop_budget:.0f}s/"
                    f"{overall_timeout_seconds:.0f}s; đã chuyển sang tổng hợp trực tiếp từ bằng chứng đã thu thập."
                    if cleaned_answer
                    else f"Vòng lặp phân tích vượt quá ngân sách thời gian {overall_timeout_seconds:.0f}s."
                ),
                "scope": "agent_runner",
            }
        )

    return AgentTurnOutcome(
        answer_text=cleaned_answer,
        agent_turns=max(turns_seen, req_count),
        tool_calls_count=tool_calls_seen,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_tokens=cached_tok,
        streamed_deltas=streamed_any,
        error_message=None if cleaned_answer else err_msg,
        extra_blindspots=extra_blindspots,
        activity_log=activity_log,
        playbook_id=playbook.playbook_id,
        playbook_mode=playbook.selection_mode,
        prefetched_tools=prefetched_tools,
        exposed_tools=list(playbook.exposed_tool_names),
    )
