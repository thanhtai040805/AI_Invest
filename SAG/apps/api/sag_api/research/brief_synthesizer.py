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
_INTERNAL_CLAIM_ID_RE = re.compile(r"(?:\s*\[(?:P|K)\d+\])+")
_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_REASONING_JSON_CUTOFF_RE = re.compile(
    r"(?:```(?:json)?|(?:^|\n)\s*\{\s*\"[a-z_]+\"\s*:)",
    re.IGNORECASE,
)

_REASONING_INLINE_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"xuất\s+ra\s+(?:ĐÚNG\s+MỘT\s+)?JSON\s*(?:'Đề\s+bài\s+nghiên\s+cứu')?", re.IGNORECASE),
        "xây dựng khung phân tích",
    ),
    (re.compile(r"tạo\s+(?:một\s+)?JSON\s*(?:mặc\s+định)?", re.IGNORECASE), "chuẩn bị phản hồi"),
    (re.compile(r"(?<![^\W_])JSON(?![^\W_])"), "khung phân tích"),
    (re.compile(r"`?premises_to_check`?(?![^\W_])", re.IGNORECASE), "kiểm chứng giả định"),
    (re.compile(r"`?user_concern`?(?![^\W_])", re.IGNORECASE), "mối quan tâm cốt lõi"),
    (re.compile(r"`?answer_shape`?(?![^\W_])", re.IGNORECASE), "cách tiếp cận trả lời"),
    (re.compile(r"`?verdict_with_reasons`?(?![^\W_])", re.IGNORECASE), "đánh giá kèm luận cứ"),
    (re.compile(r"`?health_check`?(?![^\W_])", re.IGNORECASE), "kiểm tra sức khỏe tài chính"),
    (re.compile(r"`?direct_fact`?(?![^\W_])", re.IGNORECASE), "tra cứu số liệu trực tiếp"),
    (re.compile(r"`?candidate_ranking`?(?![^\W_])", re.IGNORECASE), "sàng lọc doanh nghiệp"),
    (re.compile(r"`?peer_head_to_head`?(?![^\W_])", re.IGNORECASE), "so sánh cùng ngành"),
    (re.compile(r"`?sector_pulse`?(?![^\W_])", re.IGNORECASE), "bức tranh phân hóa ngành"),
    (re.compile(r"`?market_outlook`?(?![^\W_])", re.IGNORECASE), "toàn cảnh thị trường"),
    (re.compile(r"`?premise_audit_and_explanation`?(?![^\W_])", re.IGNORECASE), "kiểm chứng giả định"),
    (
        re.compile(r"`?needs_clarification`?\s*(?::\s*(?:true|false))?(?![^\W_])", re.IGNORECASE),
        "trạng thái cần làm rõ",
    ),
    (re.compile(r"`?clarification_needed`?(?![^\W_])", re.IGNORECASE), "cần làm rõ yêu cầu"),
    (re.compile(r"`?clarifying_question`?(?![^\W_])", re.IGNORECASE), "câu hỏi làm rõ"),
    (re.compile(r"`?candidate_search`?(?![^\W_])", re.IGNORECASE), "sàng lọc toàn sàn"),
    (re.compile(r"`?anchor_ticker`?(?![^\W_])", re.IGNORECASE), "mã chuẩn đối chiếu"),
    (re.compile(r"`?budget_tier`?(?![^\W_])", re.IGNORECASE), "mức độ phân tích"),
    (re.compile(r"(?<![^\W_])Budget\s+tier\s*:\s*\w+(?![^\W_])", re.IGNORECASE), ""),
    (re.compile(r"`?(?:fact|kpi):[a-z0-9_]+`?(?![^\W_])", re.IGNORECASE), "chỉ tiêu tài chính"),
    (re.compile(r"`?pulse:(?:market|sector)(?:\|[^`\n\r]*)?`?", re.IGNORECASE), "bức tranh ngành và thị trường"),
    (
        re.compile(
            r"`?doc:(?:QUARTER_NOTES|BCTC_AUDITED|BCTN_CORE|BCTN_PR|AGM_TARGETS)(?:,[A-Z_]+)*(?:\|[^`\n\r]*)?`?",
            re.IGNORECASE,
        ),
        "thuyết minh báo cáo",
    ),
    (re.compile(r"`?doc:`?", re.IGNORECASE), "thuyết minh "),
    (re.compile(r"(?<![^\W_])premises(?![^\W_])", re.IGNORECASE), "giả định"),
    (re.compile(r"(?<![^\W_])entities(?![^\W_])", re.IGNORECASE), "đối tượng phân tích"),
    (re.compile(r"(?<![^\W_])claims(?![^\W_])", re.IGNORECASE), "luận điểm"),
    (re.compile(r"(?<![^\W_])evidence(?![^\W_])", re.IGNORECASE), "dữ liệu đối chiếu"),
    (re.compile(r"(?<![^\W_])fact(?![^\W_])", re.IGNORECASE), "số liệu BCTC"),
    (re.compile(r"(?<![^\W_])kpi(?![^\W_])", re.IGNORECASE), "chỉ số tài chính"),
    (re.compile(r"(?<![^\W_])Stage\s*7(?![^\W_])", re.IGNORECASE), "kiểm chứng chéo"),
)


def humanize_visible_reasoning(text: str) -> str:
    """Deterministically scrub internal schema/JSON identifiers from `<think>` reasoning segments."""
    out = text
    cut_m = _REASONING_JSON_CUTOFF_RE.search(out)
    if cut_m is not None:
        out = out[: cut_m.start()]
    for pat, repl in _REASONING_INLINE_SUBS:
        out = pat.sub(repl, out)
    out = _INTERNAL_CLAIM_ID_RE.sub("", out)
    return out


class _ReasoningWordStreamBuffer:
    """Buffer streaming reasoning deltas to word boundaries with protocol-level JSON fence/brace cutoff."""

    def __init__(self) -> None:
        self._buf = ""
        self._json_cut = False

    def reset(self) -> None:
        self._buf = ""
        self._json_cut = False

    def push(self, delta: str) -> str:
        if not delta or self._json_cut:
            return ""
        self._buf += delta
        cut_m = _REASONING_JSON_CUTOFF_RE.search(self._buf)
        if cut_m is not None:
            head = self._buf[: cut_m.start()]
            self._buf = ""
            self._json_cut = True
            return humanize_visible_reasoning(head)
        # Hold back trailing backtick or '{' in case a JSON fence/object starts across chunks
        safe_end = len(self._buf)
        while safe_end > 0 and self._buf[safe_end - 1] in "`{":
            safe_end -= 1
        last_ws = -1
        for idx in range(safe_end - 1, -1, -1):
            if self._buf[idx].isspace():
                last_ws = idx
                break
        if last_ws == -1:
            return ""
        ready = self._buf[: last_ws + 1]
        self._buf = self._buf[last_ws + 1 :]
        return humanize_visible_reasoning(ready)

    def flush(self) -> str:
        if not self._buf or self._json_cut:
            self._buf = ""
            return ""
        rem = self._buf
        self._buf = ""
        return humanize_visible_reasoning(rem)


_ANSWER_SHAPE_VN: dict[str, str] = {
    "direct_fact": "Tra cứu số liệu trực tiếp",
    "verdict_with_reasons": "Đánh giá trực diện kèm luận cứ",
    "health_check": "Kiểm tra tổng thể sức khỏe tài chính",
    "candidate_ranking": "Sàng lọc và xếp hạng doanh nghiệp",
    "premise_audit_and_explanation": "Kiểm chứng giả định trước khi giải thích nguyên nhân",
    "peer_head_to_head": "So sánh đối đầu giữa các doanh nghiệp cùng ngành",
    "sector_pulse": "Phân tích độ rộng, phân hóa toàn ngành & động lực doanh nghiệp đầu ngành",
    "market_outlook": "Toàn cảnh nhịp đập thị trường, xoay trục ngành & động lực vĩ mô",
    "clarification_needed": "Cần làm rõ nhu cầu",
}

SYNTHESIS_STATIC_SYSTEM_PROMPT = (
    "Bạn là Chuyên gia Phân tích Đầu tư Cấp cao (Senior Institutional Equity Analyst) của AIInvest.\n"
    "Nhiệm vụ: viết câu trả lời sắc sảo, đi thẳng vào mối quan tâm cốt lõi của nhà đầu tư "
    "dựa HOÀN TOÀN trên trọng tâm đề bài và các Thẻ kết luận đã kiểm chứng bên dưới.\n"
    "QUY TẮC HIỂN THỊ SUY LUẬN (VISIBLE ANALYST THINKING — ĐƯỢC HIỂN THỊ TRỰC TIẾP CHO NHÀ ĐẦU TƯ):\n"
    "- Phần suy luận (`<think>` / reasoning) hiển thị trực tiếp cho Môi giới & Nhà đầu tư đọc: viết 3–5 ý phân tích "
    "tài chính mạch lạc bằng tiếng Việt tự nhiên (dưới 140 từ), tập trung đúc kết số liệu và đối chiếu các luận điểm.\n"
    "- TUYỆT ĐỐI CẤM nhắc tới tên thẻ/mã biến tiếng Anh nội bộ nào (như `BRIEF_METADATA`, `VERIFIED_CLAIM_CARDS`, "
    "`CONFIRMED`, `REFUTED`, `PARTIAL`, `NO_DATA`, `P1`, `K1`, `Stage 7`, `JSON`, `prompt`, hay bất kỳ cụm tiếng Anh "
    "nối gạch dưới `snake_case` nào) trong phần suy luận!\n\n"
    "NGUYÊN TẮC BẮT BUỘC:\n"
    "1. Hình thức trình bày linh hoạt theo bản chất câu hỏi, KHÔNG ép khung 4 mục cứng nhắc:\n"
    "   - Mở đầu bằng kết luận trực diện trả lời ngay câu hỏi / nỗi lo của người hỏi.\n"
    "   - Nếu có Thẻ tiền đề (`TIỀN ĐỀ CẦN KIỂM TRA`) mang trạng thái bác bỏ (`REFUTED`), PHẢI đính chính thẳng thắn "
    "giả định chưa đúng của người hỏi ngay ở câu mở đầu bằng số liệu thực tế trước khi giải thích diễn biến thật.\n"
    "   - Nếu là câu hỏi sàng lọc hoặc so sánh các doanh nghiệp cùng ngành, trình bày chuẩn đối chiếu "
    "(nếu có mã gốc) và bảng so sánh gọn giữa các ứng viên, nêu rõ tiêu chí đạt / chưa đạt.\n"
    "   - Trình bày bảng Markdown gọn khi có từ 3 chỉ tiêu hoặc nhiều kỳ/nhiều mã trở lên để dễ đọc.\n"
    "2. KỶ LUẬT SỐ HỌC & TRÍCH DẪN TUYỆT ĐỐI (ZERO MENTAL MATH):\n"
    "   - CHỈ dùng các con số đã xuất hiện nguyên văn trong các Thẻ kết luận bên dưới.\n"
    "   - TUYỆT ĐỐI KHÔNG tự cộng nhẩm, trừ nhẩm, KHÔNG tự lấy hai con số chia nhau để viết thêm `% tăng/giảm` "
    "(`+141%`, `+68%`, `giảm 98%`), hệ số `gấp ~X lần` hay `chiếm Y%` chưa in sẵn trong `[CALC-*]` hoặc nguyên văn "
    "`[C#]` (ví dụ bảng chỉ có `891` và `2.149` thì viết `tăng từ 891 lên 2.149 [C#]`, CẤM tự tính `+141%`); KHÔNG tự "
    "chia 1.000 làm tròn đổi `triệu đồng` sang `tỷ đồng` (giữ nguyên con số + đơn vị gốc trong `[C#]`); và KHÔNG tự "
    "bịa khoảng ước lượng (`15-18%`).\n"
    "   - Mọi câu và mọi hàng trong bảng Markdown có số liệu tài chính BẮT BUỘC gắn kèm mã nguồn gốc trực tiếp ngay "
    "trên hàng/câu đó (`[CALC-*]`, `[C#]`, `[TBL:*]`, `[SCR:*]`, `[KG:*]`, `[MEM:*]`, `[PORT:*]`) — KHÔNG để dòng bảng "
    "chứa số liệu trơ trọi rồi mới ghi `*Nguồn: [C#]*` ở dòng dưới; khi nhắc tới ngưỡng kỳ vọng của nhà đầu tư "
    "(như `10-15%/năm`) phải gắn `[MEM:M#]`, KHÔNG gắn `[CALC-*]` của doanh nghiệp vào con số mục tiêu cá nhân! "
    "Khi gợi ý mở rộng sàng lọc sang các mã khác ngoài thẻ, chỉ nêu tên mã, TUYỆT ĐỐI KHÔNG kèm con số ước lượng "
    "(như `ROE ~16%`) chưa có trong thẻ. TUYỆT ĐỐI KHÔNG gắn mã ý nội bộ của đề bài (`[P1]`, `[K1]`..`[K6]`).\n"
    "3. KỂ CÂU CHUYỆN KINH DOANH THUYẾT PHỤC (THESIS-FIRST, KHÔNG ĐỌC VẸT BẢNG KẾ TOÁN):\n"
    "   - Con số kế toán chỉ là cái bóng kết quả; sức thuyết phục của bài phân tích nằm ở **CỖ MÁY KINH DOANH** "
    "đằng sau con số! Thay vì liệt kê rời rạc từng tỷ số kế toán, hãy dẫn dắt mạch lạc theo cơ chế tạo tiền:\n"
    "     + **Cỗ máy tăng trưởng & Vũ khí cạnh tranh thực địa:** Đơn hàng ký mới/backlog, thị trường mở đường "
    "(Nhật, Hàn, nội địa...), nhân sự chiến lược trong HĐQT/Ban điều hành bảo chứng niềm tin khách hàng, sản phẩm "
    "& công nghệ giải bài toán cụ thể của khách hàng (như chuyển đổi hệ thống di sản sang hợp đồng trọn gói, mở chuỗi, "
    "thu xếp vốn hệ sinh thái, chiến dịch hút CASA), hoặc thay đổi định mức/chính sách của Nhà nước mở khóa biên lợi "
    "nhuận (`[C#]`) — đặt ngay cạnh bảng số liệu bộ phận `[CALC-*]` để số liệu và câu chuyện soi sáng cho nhau!\n"
    "     + **Bản chất sự kiện cấu trúc & Tiền thật của cổ đông:** Khi có biến động hợp nhất / chuyển giao vốn Nhà "
    "nước / lập công ty con hay liên kết mới, phải giải thích rõ *ai chuyển giao cho ai, theo văn bản nào, tác động "
    "thực tế tới Bảng cân đối, lợi nhuận cổ đông công ty mẹ và dòng tiền cổ tức tiền mặt hàng năm ra sao* (`[C#]`), "
    "đồng thời đặt số bề mặt cạnh số tương đương (like-for-like).\n"
    "     + **Khớp đúng cung thời gian (`2025 -> 2026 Inflection Arc`):** Chú ý nhãn kỳ tài liệu (`[BCTN thực hiện "
    "FY2025]` vs `[ĐHĐCĐ 2026]` vs `[BCTC Q3/2026]`). Tuyệt đối KHÔNG lấy nguyên nhân giảm biên của năm 2025 gán nhầm "
    "cho cú bật biên của 9T/2026 — hãy nối thành mạch chuyển pha tự nhiên (*'Năm 2025 biên từng nén do..., nhưng bước "
    "sang 2026 đã đảo chiều tăng nhờ...'*).\n"
    "     + **Kỷ luật phân bổ vốn & Mũi nhọn mới:** Phản ánh trực diện quan điểm trả lời chất vấn ĐHĐCĐ của Ban lãnh "
    "đạo về kỷ luật đầu tư CAPEX (như khai thác hiệu suất nhà máy hiện hữu trước khi mở mới) và đặt cược dài hạn.\n"
    "   - VỚI CÂU HỎI NGÀNH (`sector_pulse`) HOẶC TOÀN THỊ TRƯỜNG (`market_outlook`):\n"
    "     + Mở đầu bằng bức tranh định lượng thực tế (nhịp đập VN-Index, thanh khoản, khối ngoại, độ rộng lợi nhuận "
    "toàn ngành/toàn sàn và bảng phân hóa nhóm dẫn dắt vs. suy yếu kèm `[CALC-*]` / `[SCR:*]`).\n"
    "     + Kết nối trực tiếp sang các cơ chế chính sách, chuyển dịch cấu trúc và góc nhìn Ban lãnh đạo từ thuyết minh "
    "& ĐHĐCĐ của các doanh nghiệp đầu ngành (`[C#]`) để giải thích *tại sao* có sự phân hóa đó.\n"
    "     + TÁCH BẠCH MINH BẠCH PHẠM VI MẪU: Nêu rõ đâu là độ rộng định lượng trên toàn bộ `N` mã của ngành/sàn "
    "(từ Core PG), và đâu là dẫn chứng thuyết minh sâu từ nhóm `M` doanh nghiệp đầu ngành đã công bố hồ sơ PDF — "
    "không ngoại suy phát ngôn riêng lẻ của vài mã đầu ngành thành toàn bộ các mã chưa nạp PDF.\n"
    "   - KỶ LUẬT ĐA LƯỢT & KHẨU VỊ CÁ NHÂN:\n"
    "     + Mỗi lượt trả lời PHẢI viết mới trực diện cho câu hỏi hiện tại từ `<VERIFIED_CLAIM_CARDS>` của lượt này; "
    "TUYỆT ĐỐI KHÔNG chép lại bảng biểu hay bố cục y hệt lượt trước, và CHỈ gắn các mã `[CALC-*]`, `[C#]`, `[TBL:*]`, "
    "`[SCR:*]`, `[MEM:M#]` có mặt trong lượt hiện tại!\n"
    "     + Nếu câu hỏi là phân tích khách quan về doanh nghiệp ('Phân tích chi tiết FPT quý gần nhất'), hãy trình bày "
    "khách quan bức tranh kinh doanh & tài chính trước (không biến tiêu đề thành 'phù hợp với mục tiêu của bạn'); "
    "khi câu hỏi hỏi về mức độ phù hợp nắm giữ / khẩu vị cá nhân / lọc cổ phiếu, hãy đối chiếu trực diện từng tiêu chí "
    "và gắn mã `[MEM:M#]` ngay tại mệnh đề nhắc tới con số mục tiêu của nhà đầu tư (như `10-15%/năm [MEM:M1]`, "
    "`trên 5 năm [MEM:M1]`).\n"
    "4. TRUNG THỰC VỚI KHOẢNG TRỐNG DỮ LIỆU:\n"
    "   - Ý nào thẻ ghi chưa có dữ liệu (`NO_DATA`) hoặc một phần chỉ tiêu chưa có trong kho PIT (`PARTIAL`, "
    "ví dụ chưa có hệ số beta biến động giá cổ phiếu), phải nói thẳng chỉ tiêu nào đã kiểm chứng được và chỉ tiêu nào "
    "chưa có dữ liệu PIT — tuyệt đối không suy diễn từ 'không có cờ cảnh báo BCTC' sang 'biến động giá thấp'.\n"
    "   - Không đưa khuyến nghị mua/bán hoặc giá mục tiêu."
)

REPAIR_STATIC_SYSTEM_PROMPT = (
    "Bạn là Kiểm toán viên Chuẩn hóa Báo cáo (Stage 7 Forensic Auditor & Surgical Editor) của AIInvest.\n"
    "Một vài dòng trong bản nháp vừa bị hệ thống kiểm toán Stage 7 gắn cờ (do tự chia nhẩm ra `% tăng/giảm` không có "
    "trong báo cáo gốc, tự làm tròn đổi `triệu đồng` sang `tỷ đồng`, thiếu mã `[CALC-*]`/`[C#]`, hoặc gắn nhầm mã).\n\n"
    "NHIỆM VỤ:\n"
    "- Chỉ sửa ĐÚNG các dòng bị gắn cờ bên dưới dựa TUYỆT ĐỐI trên `[CALC-*]` gốc và nguyên văn `[C#]` gốc.\n"
    "- Giữ nguyên định dạng đầu dòng (bullet `- `, `* `, tiêu đề `###`, hoặc dòng bảng `|...|` đúng số cột).\n"
    "- Với mọi con số `%` hoặc hệ số bị gắn cờ `không khớp bằng chứng gốc` (ví dụ `+141%`, `+68%`, `giảm 98%`), BẮT "
    "BUỘC XÓA BỎ con số `%` tự tính nhẩm đó và chỉ giữ con số tuyệt đối có thật trong `[C#]`/`[CALC-*]` (nếu là cột "
    "`Tăng trưởng` trong bảng mà `[C#]` không có cột `%`, ghi `—` hoặc chênh lệch mô tả không kèm số `%`).\n"
    "- Với con số tiền tệ bị gắn cờ do tự đổi đơn vị làm tròn (`2.136 tỷ` từ bảng `triệu đồng`), đổi lại đúng nguyên "
    "văn con số + đơn vị trong `[C#]` (hoặc xóa nếu không tìm thấy nguyên văn).\n"
    "- Xuất ra ĐÚNG MỘT JSON theo mẫu:\n"
    '{"repairs": [{"idx": 0, "fixed_line": "dòng đã sửa chuẩn xác kèm [CALC-*] hoặc [C#]"}]}'
)


@dataclass
class BriefTurnResult:
    outcome: AgentTurnOutcome
    brief: ResearchBrief | None = None
    verdict_cards: list[ClaimVerdictCard] = field(default_factory=list)
    rolling_summary: str | None = None


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

    shape_vn = _ANSWER_SHAPE_VN.get(brief.answer_shape, "Đánh giá phân tích trọng tâm")
    meta_lines = [
        f"- Câu hỏi gốc của nhà đầu tư: {query.strip()}",
        f"- Dữ liệu tính đến ngày: {ctx.as_of_date.isoformat()}",
        f"- Mối quan tâm cốt lõi: {brief.user_concern}",
        f"- Cách tiếp cận trình bày: {shape_vn}",
    ]
    if exec_result.located.analysis_tickers:
        meta_lines.append(f"- Đối tượng phân tích: {', '.join(exec_result.located.analysis_tickers)}")
    if exec_result.located.anchor_ticker:
        meta_lines.append(f"- Mã chuẩn đối chiếu: {exec_result.located.anchor_ticker}")
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
    history_messages: Sequence[AgentMessage] = (),
    pit_adjustments: Sequence[str] = (),
    attach_disclaimer: bool = False,
    enable_thinking: bool = True,
    on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
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
            str(c.get("calc_id") or "") for c in ctx.evidence.calculator_traces if c.get("calc_id")
        },
        valid_memory_ids=lambda: set(ctx.evidence.recalled_memories.keys()),
        valid_portfolio_symbols=lambda: set(ctx.evidence.portfolio_snapshots.keys()),
        valid_session_ids=lambda: set(ctx.evidence.linked_sessions.keys()),
    )
    gate.reset_for_turn(tools_enabled=False)

    req = ModelRequest(
        messages=(
            AgentMessage(role="system", content=SYNTHESIS_STATIC_SYSTEM_PROMPT),
            *history_messages,
            AgentMessage(role="user", content=user_prompt),
        ),
        tools=(),
        turn=1,
        metadata={"enable_thinking": True} if enable_thinking else {},
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
                if chunk.reasoning_delta and on_reasoning_delta is not None:
                    await on_reasoning_delta(chunk.reasoning_delta)
                if chunk.text_delta:
                    raw_model_parts.append(chunk.text_delta)
                    out_sentences = gate.push_delta(chunk.text_delta)
                    if on_reasoning_delta is not None:
                        for t_extracted in gate.drain_extracted_thoughts():
                            if t_extracted:
                                await on_reasoning_delta(t_extracted)
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
            if on_reasoning_delta is not None:
                for t_extracted in gate.drain_extracted_thoughts():
                    if t_extracted:
                        await on_reasoning_delta(t_extracted)
    except TimeoutError:
        timed_out = True
        for tail_out in gate.flush_turn_end(has_tool_calls=False):
            emitted_chunks.append(tail_out)
            if on_delta is not None:
                streamed_any = True
                await on_delta(tail_out)

    valid_calcs = {str(c.get("calc_id") or "") for c in ctx.evidence.calculator_traces if c.get("calc_id")}
    raw_answer = strip_reasoning_tags("".join(emitted_chunks).strip())
    cleaned_answer = _INLINE_CALC_BADGE_RE.sub(
        lambda m: m.group(0) if m.group(1) in valid_calcs else "",
        raw_answer,
    )
    cleaned_answer = _INTERNAL_CLAIM_ID_RE.sub("", cleaned_answer)
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
        raw_num_vn = raw_num.replace(".", ",")
        compact_claim = re.sub(r"\s+", "", claim_txt.strip()).lower().replace(",", ".")
        if probe:
            for idx, n_ln in enumerate(norm_lines):
                if probe in n_ln or (len(n_ln) >= 18 and n_ln[:50] in probe):
                    return idx
        # Space- and decimal-locale-insensitive claim match (handles '3.43 %' vs VN table cell '3,43%')
        if compact_claim:
            for idx, ln in enumerate(lines):
                if compact_claim in re.sub(r"\s+", "", ln).lower().replace(",", "."):
                    return idx
        if raw_num:
            for idx, ln in enumerate(lines):
                if raw_num in ln or (raw_num_vn and raw_num_vn in ln):
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
        for l_idx, issues in sorted(flagged_indices.items())[:12]
    ]
    dropped_targets = [l_idx for l_idx, _ in sorted(flagged_indices.items())[12:]]

    ev_lines: list[str] = []
    # Authoritative Stage-7 numeric ground truth ONLY (`[CALC-*]` + verbatim `[C#]` quotes).
    # Never feed `verdict_cards.summary_lines` here because a Mini-Reader mental-% summary line is
    # precisely what Stage 7 may have flagged!
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
                    f"{k}={v.get('value') if isinstance(v, Mapping) else v}" for k, v in list(inps.items())[:12]
                )
                ev_lines.append(f"[{cid}] {desc}: {kv}")
    if sections_by_cid:
        ev_lines.append("=== ĐOẠN TÀI LIỆU [C#] GỐC ===")
        cited_in_draft = list(dict.fromkeys(re.findall(r"\[(C\d+)(?:\s+UNVERIFIED_CLAIM)?\]", answer_text or "")))
        ordered_cids = [c for c in cited_in_draft if c in sections_by_cid] + [
            c for c in sections_by_cid if c not in cited_in_draft
        ]
        for cid in ordered_cids[:18]:
            sec = sections_by_cid[cid]
            tk = getattr(sec, "ticker", "") if not isinstance(sec, Mapping) else sec.get("ticker", "")
            body = getattr(sec, "content", "") if not isinstance(sec, Mapping) else sec.get("content", "")
            ev_lines.append(f"[{cid}] ({tk}) {str(body).strip()[:1800]}")
    elif verdict_cards:
        ev_lines.append("=== TRÍCH ĐOẠN NGUYÊN VĂN TRONG THẺ ===")
        for card in verdict_cards:
            for vq in getattr(card, "verbatim_quotes", ()) or ():
                ev_lines.append(str(vq))

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


def upsert_activity_log_item(activity_log: list[dict[str, Any]], item: dict[str, Any]) -> None:
    """Coalesce streaming `append=True` thoughts and update `tool_call`/`verdict_card` items in place."""
    if item.get("replace_text") and item.get("id"):
        step_id = item["id"]
        existing = next((entry for entry in activity_log if entry.get("id") == step_id), None)
        if existing is not None:
            existing["text"] = str(item.get("text") or "")
            return
    if item.get("append") and item.get("id"):
        step_id = item["id"]
        existing = next((entry for entry in activity_log if entry.get("id") == step_id), None)
        if existing is not None:
            existing["text"] = f"{existing.get('text') or ''}{item.get('text') or ''}"
            if item.get("timestamp_ms") is not None:
                existing["completed_at_ms"] = item["timestamp_ms"]
        else:
            activity_log.append({**item, "append": False})
        return

    match_key = item.get("call_id") or item.get("id")
    if match_key:
        for idx, existing in enumerate(activity_log):
            if (existing.get("call_id") or existing.get("id")) == match_key:
                activity_log[idx] = {**existing, **item, "append": False}
                return
    activity_log.append({**item, "append": False})


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
    first_delta_ms: int | None = None

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    async def _record_thought(evt: dict[str, Any]) -> None:
        item = {**evt, "timestamp_ms": _elapsed_ms()}
        upsert_activity_log_item(activity_log, item)
        if on_thought is not None:
            await on_thought(item)

    async def _record_tool_call(evt: dict[str, Any]) -> None:
        item = {**evt, "timestamp_ms": _elapsed_ms()}
        upsert_activity_log_item(activity_log, item)
        if on_tool_call is not None:
            await on_tool_call(item)

    async def _record_tool_result(evt: dict[str, Any]) -> None:
        item = {**evt, "completed_at_ms": _elapsed_ms()}
        upsert_activity_log_item(activity_log, item)
        if on_tool_result is not None:
            await on_tool_result(item)

    async def _tracked_on_delta(text_chunk: str) -> None:
        nonlocal first_delta_ms
        if first_delta_ms is None and text_chunk.strip():
            first_delta_ms = _elapsed_ms()
        if on_delta is not None:
            await on_delta(text_chunk)

    brief_r_buf = _ReasoningWordStreamBuffer()
    synth_r_buf = _ReasoningWordStreamBuffer()

    async def _emit_brief_r(clean_chunk: str) -> None:
        if not clean_chunk:
            return
        await _record_thought(
            {
                "id": "brief-reasoning-1",
                "type": "thought",
                "turn": 1,
                "kind": "reasoning",
                "title": "Xác định trọng tâm phân tích",
                "text": clean_chunk,
                "append": True,
                "status": "done",
            }
        )

    async def _on_brief_reasoning(r_delta: str) -> None:
        await _emit_brief_r(brief_r_buf.push(r_delta))

    async def _on_brief_reasoning_reset() -> None:
        brief_r_buf.reset()
        await _record_thought(
            {
                "id": "brief-reasoning-1",
                "type": "thought",
                "turn": 1,
                "kind": "reasoning",
                "title": "Xác định trọng tâm phân tích",
                "text": "",
                "replace_text": True,
                "status": "running",
            }
        )

    async def _emit_synth_r(clean_chunk: str) -> None:
        if not clean_chunk:
            return
        await _record_thought(
            {
                "id": "synthesis-reasoning-1",
                "type": "thought",
                "turn": 2,
                "kind": "reasoning",
                "title": "Tổng hợp luận điểm & số liệu",
                "text": clean_chunk,
                "append": True,
                "status": "done",
            }
        )

    async def _on_synth_reasoning(r_delta: str) -> None:
        await _emit_synth_r(synth_r_buf.push(r_delta))

    from sag_api.research.session_memory import (
        build_conversation_prefix_messages,
        build_synthesis_history_messages,
    )

    history_msgs, rolling_summary, _ = build_conversation_prefix_messages(
        ctx.session_ledger,
        verbatim_token_budget=16_000,
        keep_recent_token_budget=10_000,
    )
    synth_history_msgs = build_synthesis_history_messages(ctx.session_ledger)
    # Compact state pointer only when quarantined turns or screening leaderboard exist
    session_ctx = (
        ctx.session_ledger.to_prompt_context(include_recent_pairs=False, max_recent_pairs=0)
        if (ctx.session_ledger.quarantined_future_turns > 0 or ctx.session_ledger.last_screening_leaderboard)
        else ""
    )
    mem_ctx = "\n".join(b for b in (ctx.tier0_memory_prompt, ctx.tier2_memory_prompt) if b)
    q_low = (query or "").lower()
    has_pivot_cue = any(
        cue in q_low
        for cue in (
            "bỏ ",
            "quên ",
            "đổi sang",
            "chuyển sang",
            "lọc giúp",
            "sàng lọc",
            "tìm giúp",
            "cổ phiếu nào",
            "ngân hàng nào",
            "mã nào",
        )
    )
    session_fallback_tickers = (
        tuple(ctx.session_ledger.active_episode_tickers[:2])
        if (not explicit_tickers and ctx.session_ledger.active_episode_tickers and not has_pivot_cue)
        else ()
    )

    # ── Step (0): Deterministic Scope-Gated Pulse & Structural Pointer Map (`0` LLM tokens) ─
    from sag_api.research.market_pulse import build_scope_gated_briefing

    pulse_bundle = await build_scope_gated_briefing(
        ctx,
        query=query,
        explicit_tickers=explicit_tickers,
        fallback_tickers=session_fallback_tickers,
    )

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
        fallback_tickers=session_fallback_tickers,
        chat_mode=ctx.chat_mode,
        session_context=session_ctx,
        memory_context=mem_ctx,
        scope_briefing_context=pulse_bundle.briefing_text,
        scope_lens=pulse_bundle.lens,
        bellwether_tickers=pulse_bundle.bellwether_tickers,
        history_messages=history_msgs,
        enable_thinking=True,
        on_reasoning_delta=_on_brief_reasoning,
        on_reasoning_reset=_on_brief_reasoning_reset,
        trace=trace,
    )
    total_usage = brief_usage
    await _emit_brief_r(brief_r_buf.flush())
    if on_stage is not None:
        await on_stage("routing", "done", "Đã khóa đề bài phân tích & ràng buộc PIT")

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

    all_claims_preview = [p.statement for p in brief.premises_to_check if p.statement] + [
        c.statement for c in brief.claims if c.statement
    ]
    if not brief.needs_clarification and all_claims_preview:
        await _record_thought(
            {
                "id": "brief-1",
                "type": "thought",
                "turn": 1,
                "kind": "plan",
                "title": "Khung kiểm chứng dữ liệu",
                "text": (f"Trọng tâm: {brief.user_concern}\n" + "\n".join(f"• {ln}" for ln in all_claims_preview[:6])),
            }
        )

    # Pure ambiguity -> ask 1 concise clarifying question immediately (0 extra LLM calls!)
    if brief.needs_clarification and brief.clarifying_question:
        q_reply = brief.clarifying_question.strip()
        streamed = False
        if on_delta is not None:
            streamed = True
            await _tracked_on_delta(q_reply)
        outcome = AgentTurnOutcome(
            answer_text=q_reply,
            agent_turns=total_usage.requests,
            tool_calls_count=0,
            input_tokens=total_usage.input_tokens,
            output_tokens=total_usage.output_tokens,
            cached_tokens=total_usage.cached_tokens,
            reasoning_tokens=total_usage.reasoning_tokens,
            thinking_duration_ms=first_delta_ms if first_delta_ms is not None else _elapsed_ms(),
            streamed_deltas=streamed,
            extra_blindspots=extra_blindspots,
            activity_log=activity_log,
            playbook_id="brief_clarification",
            playbook_mode="BRIEF_CENTRIC",
        )
        return BriefTurnResult(
            outcome=outcome,
            brief=brief,
            verdict_cards=[],
            rolling_summary=rolling_summary,
        )

    # ── Step (3): Candidate Locator (0 LLM tokens) ────────────────────────────
    if on_stage is not None:
        await on_stage("retrieval", "running", "Đang định vị đối tượng & thu thập bằng chứng song song...")

    located = await locate_candidates(brief, ctx, pulse_bundle=pulse_bundle)
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
        preview_rows = [
            {
                "ticker": str(r.get("ticker") or ""),
                "pe": (r.get("metrics") or {}).get("pe"),
                "pb": (r.get("metrics") or {}).get("pb"),
                "roe": (r.get("metrics") or {}).get("roe"),
            }
            for r in located.candidate_rows[:5]
            if r.get("ticker")
        ]
        await _record_thought(
            {
                "id": "locator-1",
                "type": "thought",
                "turn": 1,
                "kind": "plan",
                "status": "done",
                "tickers": list(located.analysis_tickers),
                "screening_preview": preview_rows,
                "title": "Sàng lọc doanh nghiệp toàn sàn",
                "text": "\n".join(located.summary_lines[:5]) or "Không tìm thấy ứng viên khớp bộ lọc.",
            }
        )
    elif located.analysis_tickers:
        cov_txt = f"\n• {located.coverage_note}" if located.coverage_note else ""
        await _record_thought(
            {
                "id": "locator-1",
                "type": "thought",
                "turn": 1,
                "kind": "plan",
                "status": "done",
                "tickers": list(located.analysis_tickers),
                "title": "Xác định doanh nghiệp & kỳ dữ liệu",
                "text": (
                    f"Mã phân tích: {', '.join(located.analysis_tickers)} · "
                    f"Dữ liệu tính đến {ctx.as_of_date.isoformat()}{cov_txt}"
                ),
            }
        )

    # ── Steps (4) & (5): Premise Check + Parallel Claim Execution + Verdict Cards ─
    now_exec_start = asyncio.get_running_loop().time()
    exec_budget = max(2.0, (overall_deadline_mono - now_exec_start) * 0.68)
    partial_exec = BriefExecutionResult(located=located)
    try:
        async with asyncio.timeout(exec_budget):
            exec_res = await execute_brief_claims(
                brief=brief,
                located=located,
                ctx=ctx,
                model=model,
                pulse_bundle=pulse_bundle,
                dropped_by_claim_id=val_out.dropped_by_claim_id,
                overall_deadline_mono=overall_deadline_mono,
                exec_deadline_mono=now_exec_start + exec_budget,
                partial_sink=partial_exec,
                on_stage=on_stage,
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
        order_p = {p.id: i for i, p in enumerate(brief.premises_to_check)}
        order_c = {c.id: i for i, c in enumerate(brief.claims)}
        partial_exec.premise_cards.sort(key=lambda x: order_p.get(x.claim_id, 999))
        partial_exec.claim_cards.sort(key=lambda x: order_c.get(x.claim_id, 999))
        exec_res = partial_exec

    total_usage = total_usage.plus(exec_res.usage)
    if on_stage is not None:
        await on_stage("retrieval", "done", "Đã hoàn tất thu thập bằng chứng & thẻ kết luận")

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
        history_messages=synth_history_msgs,
        pit_adjustments=val_out.pit_adjustments,
        attach_disclaimer=bool(route_decision.get("attach_investment_disclaimer")),
        enable_thinking=True,
        on_reasoning_delta=_on_synth_reasoning,
        on_delta=_tracked_on_delta,
        timeout_seconds=rem_synth,
        trace=trace,
    )
    total_usage = total_usage.plus(synth_usage)
    await _emit_synth_r(synth_r_buf.flush())

    if synth_timed_out and answer_text:
        extra_blindspots.append(
            {
                "code": "AGENT_SYNTHESIS_TIMEOUT_PARTIAL",
                "reason": (
                    f"Bước tổng hợp chạm ngưỡng thời gian {overall_timeout_seconds:.0f}s; đã giữ phần nội dung đã xuất."
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
        reasoning_tokens=total_usage.reasoning_tokens,
        thinking_duration_ms=first_delta_ms if first_delta_ms is not None else _elapsed_ms(),
        streamed_deltas=streamed_any,
        error_message=None if answer_text else "Synthesis returned empty output",
        extra_blindspots=extra_blindspots,
        activity_log=activity_log,
        playbook_id=f"brief:{brief.answer_shape}",
        playbook_mode="BRIEF_CENTRIC",
        prefetched_tools=["locate_candidates"] if brief.entities.candidate_search else [],
        exposed_tools=ev_specs_used,
    )
    return BriefTurnResult(
        outcome=outcome,
        brief=brief,
        verdict_cards=all_cards,
        rolling_summary=rolling_summary,
    )
