"""Brief-Centric Candidate Locator, Parallel Per-Claim Executor & Verdict Cards (`brief_executor.py`).

Implements Components (3), (4), and (5) of the SAG Brief-Centric Brain:
- Component (3) `locate_candidates`: 0-LLM-token universe & anchor-relative locator over `FactSet` +
  market caps (with offline `screen_universe_metrics` fallback when Core PG is absent).
- Component (4) `execute_brief_claims`: runs `premises_to_check` first, then executes independent
  claims concurrently (`0`-token Python quant engines + bounded-concurrency `doc:*` Mini-Reader LLM calls).
- Component (5) `ClaimVerdictCard` + code-owned 1-hop selective escalation on anomalies/contradictions,
  with hard budget termination (never relying on Flash self-stopping).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sag_agent.tools import ToolContext
from sag_agent.types import AgentMessage, CancellationToken, ModelProvider, ModelRequest, Usage
from sag_api.research.analytics import (
    KpiStatus,
    KpiValue,
    PeriodRef,
    PlanTarget,
    compute_kpi,
    compute_multi_quarter_trajectory,
    earnings_quality,
    extract_balance_sheet_note_breakdowns,
    extract_segment_drivers,
    latest_analysis_period,
    peer_compare,
    plan_vs_actual,
    red_flag_scan,
)
from sag_api.research.analytics.agm_targets import extract_plan_targets
from sag_api.research.analytics.kpis import KPI_BY_ID
from sag_api.research.brief import (
    MAX_DOC_SPECS_PER_CLAIM,
    CandidateCriterion,
    ClaimSpec,
    ResearchBrief,
    TraceSink,
    emit_trace,
    usage_to_dict,
)
from sag_api.research.facts import FactSet, LookupStatus, Template
from sag_api.research.facts.catalog import CATALOG_BY_ID, items_for_template, resolve_template
from sag_api.research.facts.store import FLAG_QUARTERLY_CF_UNRELIABLE
from sag_api.research.financial_metrics_store import screen_universe_metrics
from sag_api.research.tools.analyst_tools import _agm_tables, _fact_set, _register_kpi, fmt_value
from sag_api.research.tools.context import ResearchToolContext
from sag_api.research.tools.research_tools import (
    _execute_disclosure_search,
    hydrate_financial_tables_tool,
    normalize_disclosure_slots,
    query_knowledge_graph_tool,
)

logger = logging.getLogger(__name__)

VerdictStatus = Literal["CONFIRMED", "REFUTED", "PARTIAL", "NO_DATA"]

MAX_CANDIDATE_BATCH = 500
MAX_MINI_READER_CONCURRENCY = 5
MAX_ESCALATION_CLAIMS = 2

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_PERIOD_Q_PARSE_RE = re.compile(r"Q([1-4])/(\d{4})", re.IGNORECASE)
_PERIOD_FY_PARSE_RE = re.compile(r"FY(\d{4})", re.IGNORECASE)

_NEGATIVE_PREMISE_RE = re.compile(
    r"(?:\bsập\b|giảm\s+mạnh|lao\s+dốc|bốc\s+hơi|suy\s+sụp|đổ\s+vỡ|thua\s+lỗ|\blỗ\b|\bâm\b|cắt\s+cổ\s+tức)",
    re.IGNORECASE,
)
_POSITIVE_PREMISE_RE = re.compile(
    r"(?:tăng\s+bằng\s+lần|tăng\s+đột\s+biến|tăng\s+gấp\s+đôi|kỷ\s+lục|vượt\s+kế\s+hoạch)",
    re.IGNORECASE,
)

MINI_READER_STATIC_SYSTEM_PROMPT = (
    "Bạn là Chuyên gia Giải mã Cơ chế Kinh doanh, Quan hệ Hệ sinh thái & Đối chứng Thực chất "
    "(Buy-Side Mechanism & Forensic Disclosure Mini-Reader) của AIInvest.\n"
    "Nhiệm vụ: dựa trên các đoạn tài liệu `[C#]` được cung cấp, bóc tách ĐẦY ĐỦ cả CƠ CHẾ TẠO TIỀN, MỐI QUAN HỆ "
    "CHỦ THỂ lẫn ĐIỀU KIỆN / RỦI RO ĐÁNH ĐỔI cho ý (`claim`) và xuất ra ĐÚNG MỘT JSON (không viết văn bản ngoài JSON, "
    "không tự cộng trừ nhẩm).\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "1. LĂNG KÍNH 2 MẶT CHUẨN BUY-SIDE (tối đa 7 dòng `findings`; đặt `\"status\": \"CONFIRMED\"` khi đoạn `[C#]` có "
    "bằng chứng trực tiếp làm rõ cơ chế/câu hỏi, `\"REFUTED\"` khi số liệu bác bỏ thẳng giả định sai, `\"PARTIAL\"` "
    "khi mới rõ một vế): Với mỗi điểm dữ liệu đắt giá trong `[C#]`, hãy giữ trọn cả 2 mặt:\n"
    "   (a) Cỗ máy tạo tiền & Mạng lưới quan hệ: nhân sự chiến lược HĐQT/lãnh đạo bảo chứng, khách hàng/thị trường "
    "mở đường, công nghệ/sản phẩm giải bài toán cụ thể, chính sách định mức giá mới, và vai trò của công ty con/liên "
    "kết/đối tác hệ sinh thái;\n"
    "   (b) Đối chứng thực chất & Rủi ro đi kèm: bản chất pháp lý/kế toán của thay đổi hợp nhất (ai chuyển giao cho "
    "ai, tác động lên dòng cổ tức tiền mặt thật vs. bút toán sổ sách), áp lực chi phí đầu vào/vốn lưu động, phơi "
    "nhiễm bên liên quan hoặc điều kiện thận trọng mà Ban lãnh đạo nêu tại ĐHĐCĐ.\n"
    "2. Mọi con số hoặc sự kiện trong `findings` PHẢI có sẵn nguyên bản trong đoạn `[C#]`, giữ rõ mốc kỳ tài liệu "
    "(ví dụ `FY2025` vs `ĐHĐCĐ 2026` vs `Q3/2026` để không gán nhầm diễn biến năm cũ cho kỳ mới), và gắn mã `[C#]` "
    'ở cuối câu (ví dụ: `"Tại ĐHĐCĐ 2026, Ban lãnh đạo cho biết Chính phủ bỏ tính đơn giá nhân công theo lương cơ '
    'bản giúp biên lợi nhuận dự án CNTT trong nước cải thiện mạnh [C2]."`).\n'
    "3. TUYỆT ĐỐI KHÔNG tự tính nhẩm `% tăng/giảm` (`+141%`, `+68%`, `giảm 98%`), hệ số `gấp X lần` hay `chiếm Y%` "
    "từ hai cột số tuyệt đối nếu con số `%` đó chưa in sẵn nguyên văn trong `[C#]` (hãy ghi trực tiếp hai con số gốc, "
    "ví dụ `tăng từ 891 lên 2.149 [C3]`); KHÔNG tự chia 1.000 làm tròn đổi `triệu đồng` sang `tỷ đồng` (nếu bảng ghi "
    "`2.136.412 triệu đồng` thì giữ nguyên `2.136.412 triệu đồng [C#]`); và KHÔNG tự cộng khoản mục con vào số tổng.\n"
    '4. Nếu các đoạn `[C#]` không chứa thông tin trả lời cho ý này, đặt `"status": "NO_DATA"` và ghi rõ '
    "khoảng trống, KHÔNG suy đoán.\n"
    "5. Điền `anomaly_followup_query` (chuỗi từ khóa VAS/chủ đề ngắn < 12 từ) khi đoạn `[C#]` nhắc tới "
    "một mã thuyết minh khác, một văn bản/sự kiện chuyển giao vốn hoặc một biến động lớn chưa có lời "
    "giải thích nguyên nhân trong các đoạn hiện tại; còn lại để `null`.\n\n"
    "CẤU TRÚC JSON ĐẦU RA:\n"
    "{\n"
    '  "status": "CONFIRMED | REFUTED | PARTIAL | NO_DATA",\n'
    '  "findings": ["dòng nhân quả & số liệu 1 kèm [C#]", "dòng nhân quả & số liệu 2 kèm [C#]"],\n'
    '  "anomaly_followup_query": null\n'
    "}"
)


def _norm_ascii(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "d")
    return s.encode("ascii", "ignore").decode().lower().strip()


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


async def _collect_model_text(
    model: ModelProvider,
    messages: Sequence[AgentMessage],
    *,
    turn: int = 1,
) -> tuple[str, Usage]:
    req = ModelRequest(messages=tuple(messages), tools=(), turn=turn)
    parts: list[str] = []
    in_tok = out_tok = cached_tok = reas_tok = 0
    async for chunk in model.stream_turn(req, CancellationToken()):
        if chunk.usage is not None:
            in_tok = max(in_tok, chunk.usage.input_tokens)
            out_tok = max(out_tok, chunk.usage.output_tokens)
            cached_tok = max(cached_tok, chunk.usage.cached_tokens)
            reas_tok = max(reas_tok, chunk.usage.reasoning_tokens)
        if chunk.text_delta:
            parts.append(chunk.text_delta)
    return "".join(parts), Usage(
        requests=1,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_tokens=cached_tok,
        reasoning_tokens=reas_tok,
    )


@dataclass
class LocatedTargets:
    analysis_tickers: list[str]
    anchor_ticker: str | None = None
    anchor_metrics: dict[str, float | None] = field(default_factory=dict)
    candidate_rows: list[dict[str, Any]] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)
    coverage_note: str | None = None


@dataclass
class ClaimVerdictCard:
    claim_id: str
    is_premise: bool
    statement: str
    period: str | None
    status: VerdictStatus
    summary_lines: list[str] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)
    verbatim_quotes: list[str] = field(default_factory=list)
    escalation_query: str | None = None
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "is_premise": self.is_premise,
            "statement": self.statement,
            "period": self.period,
            "status": self.status,
            "summary_lines": list(self.summary_lines),
            "badges": list(self.badges),
            "verbatim_quotes": list(self.verbatim_quotes),
            "escalation_query": self.escalation_query,
            "escalated": self.escalated,
        }

    def render_for_synthesis(self) -> str:
        role_lbl = "TIỀN ĐỀ CẦN KIỂM TRA" if self.is_premise else "Ý PHÂN TÍCH"
        p_lbl = f" ({self.period})" if self.period else ""
        esc_lbl = " [Đã đào sâu thuyết minh vòng 2]" if self.escalated else ""
        head = f"### [{self.claim_id}] {role_lbl}{p_lbl} — Trạng thái: {self.status}{esc_lbl}\nĐề bài: {self.statement}"
        body = "\n".join(f"- {ln}" for ln in self.summary_lines) if self.summary_lines else "- Không có dữ liệu PIT."
        if self.verbatim_quotes:
            q_block = "\n".join(f"  > {q}" for q in self.verbatim_quotes[:4])
            return f"{head}\n{body}\n- Trích nguyên văn báo cáo gốc:\n{q_block}"
        return f"{head}\n{body}"


@dataclass
class BriefExecutionResult:
    located: LocatedTargets
    premise_cards: list[ClaimVerdictCard] = field(default_factory=list)
    claim_cards: list[ClaimVerdictCard] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    tool_calls_count: int = 0
    escalations_run: int = 0


def _resolve_period_ref(fs: FactSet, ticker: str, raw_period: str | None) -> PeriodRef | None:
    if raw_period:
        txt = raw_period.strip().upper()
        qm = _PERIOD_Q_PARSE_RE.search(txt)
        if qm:
            fq, fy = int(qm.group(1)), int(qm.group(2))
            ptype = "TTM" if txt.startswith("TTM") else "Q"
            if fs.get(ticker, "npat", ptype, fy, fq).status is LookupStatus.OK:
                return PeriodRef(ptype, fy, fq)
        fym = _PERIOD_FY_PARSE_RE.search(txt)
        if fym:
            fy = int(fym.group(1))
            if fs.get(ticker, "npat", "FY", fy, 0).status is LookupStatus.OK:
                return PeriodRef("FY", fy, 0)
    return latest_analysis_period(fs, ticker)


def _latest_stock_val(fs: FactSet, ticker: str, item: str) -> tuple[float | None, str]:
    lq = fs.latest_period(ticker, "Q")
    if lq is not None:
        r = fs.get(ticker, item, "Q", lq[0], lq[1])
        if r.status is LookupStatus.OK and r.fact is not None:
            return r.fact.value, r.fact.period_label
    lfy = fs.latest_period(ticker, "FY")
    if lfy is not None:
        r = fs.get(ticker, item, "FY", lfy[0], 0)
        if r.status is LookupStatus.OK and r.fact is not None:
            return r.fact.value, r.fact.period_label
    return None, "-"


_CASH_COMPOSITE_METRICS = frozenset(
    {"cash_to_market_cap", "net_cash_to_market_cap", "net_cash", "total_debt", "net_debt"}
)


def eval_candidate_metric(
    fs: FactSet,
    ticker: str,
    metric_id: str,
    market_caps: Mapping[str, float],
) -> tuple[float | None, str, str]:
    """Deterministically evaluate a `FactSet`, `KPI`, or composite valuation/cash metric (0 tokens).

    Returns `(numeric_value_or_None, unit_code, period_label)` so every number is shown with its period.
    """
    tk = ticker.strip().upper()
    if tk not in fs.templates:
        return None, "", "-"

    mcap = market_caps.get(tk)
    mcap_lbl = "PIT"
    if mcap is None and (date.today() - fs.as_of_date).days <= 3:
        # Never use vendor `vr_market_cap` on historical `as_of_date` (alphastock backfills fetch-date market cap)
        mcap, mcap_lbl = _latest_stock_val(fs, tk, "vr_market_cap")

    if metric_id == "market_cap":
        return mcap, "VND", mcap_lbl

    if metric_id in {"pe", "pb", "ev_ebitda"}:
        vr_val, vr_lbl = _latest_stock_val(fs, tk, f"vr_{metric_id}")
        if vr_val is not None and vr_val > 0:
            return vr_val, "X", vr_lbl
        if metric_id == "pe" and mcap and mcap > 0:
            p = latest_analysis_period(fs, tk)
            if p is not None:
                np_res = fs.get(tk, "npat", p.period_type, p.fiscal_year, p.fiscal_quarter)
                if np_res.status is LookupStatus.OK and np_res.fact and np_res.fact.value > 0:
                    return mcap / np_res.fact.value, "X", p.label
        if metric_id == "pb" and mcap and mcap > 0:
            eq, eq_lbl = _latest_stock_val(fs, tk, "equity")
            if eq and eq > 0:
                return mcap / eq, "X", eq_lbl
        return None, "X", "-"

    if metric_id in _CASH_COMPOSITE_METRICS:
        unit = "VND" if metric_id in {"net_cash", "net_debt", "total_debt"} else "RATIO"
        # Bank/insurer/securities cash & investments back deposits/technical reserves/client money:
        # "net cash vs market cap" is economically meaningless for them, so never rank them on it.
        if fs.templates[tk] is not Template.CORPORATE:
            return None, unit, "N/A (doanh nghiệp tài chính)"
        cash, c_lbl = _latest_stock_val(fs, tk, "cash_and_equivalents")
        if cash is None:
            c1, c_lbl = _latest_stock_val(fs, tk, "cash_only")
            c2, _ = _latest_stock_val(fs, tk, "cash_equivalents_only")
            if c1 is not None or c2 is not None:
                cash = (c1 or 0.0) + (c2 or 0.0)
        st_inv, _ = _latest_stock_val(fs, tk, "st_investments")
        st_d, _ = _latest_stock_val(fs, tk, "st_borrowings")
        lt_d, _ = _latest_stock_val(fs, tk, "lt_borrowings")
        liquid = (cash or 0.0) + (st_inv or 0.0) if (cash is not None or st_inv is not None) else None
        tot_debt = (st_d or 0.0) + (lt_d or 0.0) if (st_d is not None or lt_d is not None) else 0.0
        if metric_id == "total_debt":
            return (tot_debt if (st_d is not None or lt_d is not None) else None), unit, c_lbl
        if liquid is None:
            return None, unit, "-"
        net_cash = liquid - tot_debt
        if metric_id == "net_cash":
            return net_cash, unit, c_lbl
        if metric_id == "net_debt":
            return -net_cash, unit, c_lbl
        if not mcap or mcap <= 0:
            return None, unit, "-"
        lbl = f"{c_lbl}; vốn hóa {mcap_lbl}"
        if metric_id == "cash_to_market_cap":
            return liquid / mcap, unit, lbl
        return net_cash / mcap, unit, lbl

    if metric_id == "fcf_yield":
        p = latest_analysis_period(fs, tk)
        if p is not None and mcap and mcap > 0:
            fcf_kv = compute_kpi(fs, tk, "fcf", p)
            if fcf_kv.status is KpiStatus.OK and fcf_kv.value is not None:
                return fcf_kv.value / mcap, "RATIO", fcf_kv.period
        return None, "RATIO", "-"

    if metric_id == "liabilities_to_equity":
        liab, l_lbl = _latest_stock_val(fs, tk, "total_liabilities")
        eq, _ = _latest_stock_val(fs, tk, "equity")
        if liab is not None and eq and eq > 0:
            return liab / eq, "X", l_lbl
        return None, "X", "-"

    alias_kpi = {
        "revenue_yoy": "top_line_growth_yoy",
        "net_income_yoy": "npat_growth_yoy",
        "cfo_to_net_income": "cfo_to_npat",
    }.get(metric_id, metric_id)

    if alias_kpi in KPI_BY_ID:
        p = latest_analysis_period(fs, tk)
        if p is None:
            return None, KPI_BY_ID[alias_kpi].unit, "-"
        # Prefer TTM; fall back to latest single quarter (label makes the difference explicit)
        kv = compute_kpi(fs, tk, alias_kpi, p)
        if kv.status is KpiStatus.OK and kv.value is not None:
            return kv.value, kv.unit, kv.period
        lq = fs.latest_period(tk, "Q")
        if lq is not None:
            kv_q = compute_kpi(fs, tk, alias_kpi, PeriodRef("Q", lq[0], lq[1]))
            if kv_q.status is KpiStatus.OK and kv_q.value is not None:
                return kv_q.value, kv_q.unit, kv_q.period
        return None, KPI_BY_ID[alias_kpi].unit, "-"

    if metric_id in CATALOG_BY_ID:
        spec = CATALOG_BY_ID[metric_id]
        unit = spec.unit or ("VND" if spec.kind in ("FLOW", "STOCK") else "NUMBER")
        if spec.kind in ("STOCK", "VENDOR_RATIO"):
            val, v_lbl = _latest_stock_val(fs, tk, metric_id)
            return val, unit, v_lbl
        p = latest_analysis_period(fs, tk)
        if p is not None:
            res = fs.get(tk, metric_id, p.period_type, p.fiscal_year, p.fiscal_quarter)
            if res.status is LookupStatus.OK and res.fact is not None:
                return res.fact.value, unit, p.label
        return None, unit, "-"

    return None, "", "-"


def _check_criterion(
    cand_val: float | None,
    crit: CandidateCriterion,
    anchor_val: float | None,
) -> bool:
    if cand_val is None:
        return False
    target = anchor_val if crit.ref == "anchor" else crit.value
    if target is None:
        return False
    tol = crit.tolerance if crit.ref == "anchor" else 0.0
    if crit.op == ">=":
        return cand_val >= target - tol
    if crit.op == "<=":
        return cand_val <= target + tol
    if crit.op == ">":
        return cand_val > target - tol
    if crit.op == "<":
        return cand_val < target + tol
    return abs(cand_val - target) <= max(tol, 1e-6)


async def locate_candidates(
    brief: ResearchBrief,
    ctx: ResearchToolContext,
    *,
    pulse_bundle: Any = None,
) -> LocatedTargets:
    """Component (3): Resolve explicit tickers, sector/market bellwethers, or open-universe candidates (0 tokens)."""
    cand_spec = brief.entities.candidate_search
    anchor = brief.entities.anchor_ticker
    if cand_spec is None:
        base_tks = list(brief.entities.tickers)[:6]
        if (
            not base_tks
            and pulse_bundle is not None
            and getattr(pulse_bundle, "target_tickers", None)
            and (
                brief.answer_shape in {"sector_pulse", "market_outlook"}
                or any(ev.startswith("pulse") for c in [*brief.premises_to_check, *brief.claims] for ev in c.evidence)
            )
        ):
            base_tks = list(pulse_bundle.target_tickers)[:5]
        missing_docs = [t for t in base_tks if ctx.available_tickers and t not in ctx.available_tickers]
        cov_note = (
            f"Lưu ý phạm vi tài liệu: {', '.join(missing_docs)} có số liệu BCTC định lượng Core PG nhưng "
            "chưa index thuyết minh PDF chi tiết trong Qdrant."
            if missing_docs
            else None
        )
        return LocatedTargets(analysis_tickers=base_tks, coverage_note=cov_note)

    # Offline fallback when Core PG is absent (e.g. SQLite-only unit/E2E tests)
    if ctx.core_reader is None:
        async with ctx.session_factory() as session:
            rows, total_matched, desc = await screen_universe_metrics(
                session,
                tenant_id=ctx.tenant_id,
                as_of_date=ctx.as_of_date,
                allowed_project_ids=ctx.principal.allowed_project_ids,
                core_reader=None,
                sectors=cand_spec.sectors or None,
                sort_by=cand_spec.sort_by if cand_spec.sort_by else "roe",
                sort_dir=cand_spec.sort_dir,
                limit=cand_spec.limit,
            )
        await ctx.evidence.register_screening(rows, desc)
        picked = [str(r["ticker"]) for r in rows if r.get("ticker")]
        analysis_tks = list(dict.fromkeys(([anchor] if anchor else []) + picked))[:6]
        summary = [f"Sàng lọc nội bộ PIT ({desc}): khớp {total_matched} mã -> chọn {', '.join(picked) or 'không có'}."]
        return LocatedTargets(
            analysis_tickers=analysis_tks,
            anchor_ticker=anchor,
            candidate_rows=rows,
            summary_lines=summary,
        )

    # Universe = ALL Core PG issuers (not the ~20 filing-indexed org catalog), so "toàn sàn" really means toàn sàn
    try:
        all_profiles = list(await ctx.core_reader.fetch_issuer_profiles())
        profile_source = "CORE_PG_FULL_UNIVERSE"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Core PG issuer universe load failed; falling back to org catalog: %s", exc)
        all_profiles = list(ctx.catalog_profiles)
        profile_source = "ORG_CATALOG_FALLBACK_DEGRADED"
    allowed = ctx.principal.allowed_project_ids
    sec_norms = [_norm_ascii(s) for s in cand_spec.sectors if s.strip()]
    want_tmpl = Template(cand_spec.template) if cand_spec.template in set(Template) else None

    eligible_profiles: list[Mapping[str, Any]] = []
    for p in all_profiles:
        tk = str(p.get("ticker") or "").strip().upper()
        if not tk or ("*" not in allowed and tk not in allowed):
            continue
        if tk == anchor:
            eligible_profiles.append(p)
            continue
        ind_str = str(p.get("industry") or "")
        sec_str = str(p.get("sector") or "")
        p_tmpl = resolve_template(ind_str)
        if want_tmpl is not None and p_tmpl is not want_tmpl:
            continue
        if sec_norms:
            blob = f"{_norm_ascii(ind_str)} {_norm_ascii(sec_str)}"
            if not any(sn in blob for sn in sec_norms):
                continue
        eligible_profiles.append(p)

    if not eligible_profiles:
        return LocatedTargets(
            analysis_tickers=list(brief.entities.tickers)[:6],
            anchor_ticker=anchor,
            summary_lines=["Không có mã cổ phiếu nào trong phạm vi ngành/quyền truy cập để sàng lọc."],
        )

    eligible_n = len(eligible_profiles)
    all_elig_tickers = [str(p["ticker"]).strip().upper() for p in eligible_profiles if p.get("ticker")]

    if eligible_n > MAX_CANDIDATE_BATCH:
        # Pre-load PIT market caps across eligible tickers so cap sorting is strictly by market cap, never alphabetical
        mcaps = await ctx.core_reader.fetch_pit_market_caps(ctx.as_of_date, tickers=all_elig_tickers)
        eligible_profiles.sort(
            key=lambda p: (
                0 if str(p.get("ticker") or "").upper() == anchor else 1,
                0 if str(p.get("ticker") or "").upper() in ctx.available_tickers else 1,
                -float(mcaps.get(str(p.get("ticker") or "").upper()) or p.get("market_cap") or 0.0),
            )
        )
        batch_profiles = eligible_profiles[:MAX_CANDIDATE_BATCH]
        batch_tickers = [str(p["ticker"]).strip().upper() for p in batch_profiles if p.get("ticker")]
        if anchor and anchor not in batch_tickers:
            batch_tickers.insert(0, anchor)
        fs = await _fact_set(ctx, batch_tickers, lookback_years=2 if len(batch_tickers) > 30 else 6)
    else:
        eligible_profiles.sort(
            key=lambda p: (
                0 if str(p.get("ticker") or "").upper() == anchor else 1,
                0 if str(p.get("ticker") or "").upper() in ctx.available_tickers else 1,
                -float(p.get("market_cap") or 0.0),
            )
        )
        batch_profiles = eligible_profiles
        batch_tickers = list(all_elig_tickers)
        if anchor and anchor not in batch_tickers:
            batch_tickers.insert(0, anchor)
        fs, mcaps = await asyncio.gather(
            _fact_set(ctx, batch_tickers, lookback_years=2 if len(batch_tickers) > 30 else 6),
            ctx.core_reader.fetch_pit_market_caps(ctx.as_of_date, tickers=batch_tickers),
        )

    metric_keys = list(
        dict.fromkeys(
            [c.metric for c in cand_spec.criteria]
            + ([cand_spec.sort_by] if cand_spec.sort_by else [])
            + ["roe", "pe", "pb"]
        )
    )

    anchor_vals: dict[str, float | None] = {}
    anchor_units: dict[str, str] = {}
    anchor_periods: dict[str, str] = {}
    summary_lines: list[str] = []
    if eligible_n > MAX_CANDIDATE_BATCH:
        has_mcap = sum(
            1
            for p in eligible_profiles
            if (mcaps.get(str(p.get("ticker") or "").upper()) or float(p.get("market_cap") or 0.0)) > 0
        )
        order_basis = (
            "ưu tiên mã đã index rồi vốn hóa PIT lớn"
            if has_mcap >= eligible_n // 2
            else f"ưu tiên mã đã index rồi THỨ TỰ CHỮ CÁI vì {eligible_n - has_mcap}/{eligible_n} hồ sơ thiếu vốn hóa"
        )
        cap_note = f", giới hạn {MAX_CANDIDATE_BATCH} mã ({order_basis}) — các mã ngoài giới hạn CHƯA được xét"
    else:
        cap_note = ""
    summary_lines.append(
        f"Phạm vi định vị: đã đánh giá {len(batch_tickers)}/{eligible_n} mã đủ điều kiện ngành & quyền truy cập "
        f"(nguồn {profile_source}{cap_note})."
    )
    if anchor and anchor in fs.templates:
        anc_inputs: dict[str, Any] = {}
        anc_parts: list[str] = []
        for mk in metric_keys:
            val, unit, plbl = eval_candidate_metric(fs, anchor, mk, mcaps)
            anchor_vals[mk] = val
            anchor_units[mk] = unit
            anchor_periods[mk] = plbl
            if val is not None:
                anc_inputs[mk] = {"value": val, "unit": unit or "NUMBER"}
                anc_parts.append(f"{mk}={fmt_value(val, unit)} ({plbl})")
        saved_anc = await ctx.evidence.register_calculator_trace(
            {
                "formula": "anchor_benchmark",
                "description": f"Chuẩn đối chiếu từ mã gốc {anchor} (PIT {ctx.as_of_date.isoformat()})",
                "inputs": anc_inputs,
                "result": None,
                "formatted": "; ".join(anc_parts),
                "ticker": anchor,
                "format_style": "number",
                "source": "ANCHOR_BENCHMARK",
            }
        )
        summary_lines.append(
            f"[{saved_anc['calc_id']}] Chuẩn mã gốc {anchor}: " + (", ".join(anc_parts) or "chưa đủ số liệu")
        )

    prof_by_tk = {str(p["ticker"]).strip().upper(): p for p in batch_profiles}
    scored_candidates: list[dict[str, Any]] = []
    for tk in batch_tickers:
        if tk == anchor or tk not in fs.templates:
            continue
        vals: dict[str, float | None] = {}
        units: dict[str, str] = {}
        periods: dict[str, str] = {}
        for mk in metric_keys:
            v, u, plbl = eval_candidate_metric(fs, tk, mk, mcaps)
            vals[mk] = v
            units[mk] = u
            periods[mk] = plbl
        passed = 0
        crit_notes: list[str] = []
        for cr in cand_spec.criteria:
            ok = _check_criterion(vals.get(cr.metric), cr, anchor_vals.get(cr.metric))
            if ok:
                passed += 1
            u_cr = units.get(cr.metric, "")
            if cr.ref == "anchor":
                tol_txt = f" ± dung sai {fmt_value(cr.tolerance, u_cr)}" if cr.tolerance else ""
                ref_txt = f"{anchor} {fmt_value(anchor_vals.get(cr.metric), u_cr)}{tol_txt}"
            else:
                ref_txt = fmt_value(cr.value, u_cr)
            mark = "ĐẠT" if ok else "CHƯA ĐẠT"
            crit_notes.append(
                f"{cr.metric}={fmt_value(vals.get(cr.metric), u_cr)} [{periods.get(cr.metric, '-')}] "
                f"({cr.op} {ref_txt}: {mark})"
            )
        if cand_spec.criteria and passed == 0:
            continue
        p_info = prof_by_tk.get(tk, {})
        scored_candidates.append(
            {
                "ticker": tk,
                "company_name": p_info.get("company_name") or tk,
                "sector": p_info.get("industry") or p_info.get("sector") or "",
                "template": fs.templates[tk].value,
                "passed_count": passed,
                "total_criteria": len(cand_spec.criteria),
                "all_passed": passed == len(cand_spec.criteria),
                "metrics": vals,
                "units": units,
                "criteria_notes": crit_notes,
                "has_indexed_filings": tk in ctx.available_tickers,
            }
        )

    sort_key = cand_spec.sort_by or (cand_spec.criteria[-1].metric if cand_spec.criteria else "roe")
    reverse_sort = cand_spec.sort_dir != "asc"

    def _rank_tuple(row: Mapping[str, Any]) -> tuple[int, int, float]:
        sv = row["metrics"].get(sort_key)
        if sv is None:
            num_score = float("-inf")
        else:
            num_score = float(sv) if reverse_sort else -float(sv)
        return (1 if row["all_passed"] else 0, int(row["passed_count"]), num_score)

    scored_candidates.sort(key=_rank_tuple, reverse=True)
    strict_matches = [r for r in scored_candidates if r["all_passed"]]
    top_rows = (strict_matches if strict_matches else scored_candidates)[: cand_spec.limit]

    # Register screening rows + [CALC-*] traces so Stage 7 verifies every candidate number
    scr_payload: list[dict[str, Any]] = []
    for r in top_rows:
        tk = r["ticker"]
        m_vals = r["metrics"]
        m_units = r["units"]
        c_inputs: dict[str, dict[str, Any]] = {
            k: {"value": v, "unit": m_units.get(k) or "NUMBER"} for k, v in m_vals.items() if v is not None
        }
        c_inputs["evaluated_universe_count"] = {"value": float(len(batch_tickers)), "unit": "NUMBER"}
        c_inputs["strict_matched_count"] = {"value": float(len(strict_matches)), "unit": "NUMBER"}
        for cr in cand_spec.criteria:
            if cr.ref == "literal" and cr.value is not None:
                c_inputs[f"target_{cr.metric}"] = {
                    "value": float(cr.value),
                    "unit": m_units.get(cr.metric) or "NUMBER",
                }
        saved_c = await ctx.evidence.register_calculator_trace(
            {
                "formula": "candidate_locator_metrics",
                "description": f"Chỉ tiêu định vị ứng viên {tk} (PIT {ctx.as_of_date.isoformat()})",
                "inputs": c_inputs,
                "result": m_vals.get(sort_key),
                "formatted": fmt_value(m_vals.get(sort_key), m_units.get(sort_key, "")),
                "ticker": tk,
                "format_style": "percent" if m_units.get(sort_key) == "RATIO" else "number",
                "source": "CANDIDATE_LOCATOR",
            }
        )
        r["calc_id"] = saved_c["calc_id"]
        status_tag = (
            f"Đạt đủ {r['passed_count']}/{r['total_criteria']} tiêu chí"
            if r["all_passed"]
            else f"Đạt {r['passed_count']}/{r['total_criteria']} tiêu chí (gần nhất)"
        )
        summary_lines.append(
            f"[SCR:{tk}] [{saved_c['calc_id']}] {tk} ({r['company_name']} - {r['sector']}): "
            f"{status_tag} | " + "; ".join(r["criteria_notes"])
        )
        scr_payload.append(
            {
                "ticker": tk,
                "company_name": r["company_name"],
                "sector": r["sector"],
                "roe": m_vals.get("roe"),
                "pe": m_vals.get("pe"),
                "pb": m_vals.get("pb"),
                "valid_from": ctx.as_of_date.isoformat(),
                "source_kind": "CORE_FACTSET_LOCATOR",
            }
        )

    await ctx.evidence.register_screening(
        scr_payload,
        f"Định vị ứng viên trên {len(batch_tickers)} mã Core PG (khớp tuyệt đối: {len(strict_matches)} mã)",
    )

    # Optional qualitative disclosure search: restrict to quantitative winners when criteria exist
    # so unrelated indexed tickers (ACB, BID, BVH...) never pollute an open-universe quantitative screen!
    if cand_spec.qualitative_query and ctx.available_tickers:
        if cand_spec.criteria:
            qual_tickers = [r["ticker"] for r in top_rows if r["ticker"] in ctx.available_tickers]
        else:
            qual_tickers = sorted(ctx.available_tickers)
        if qual_tickers:
            disc_res = await _execute_disclosure_search(
                tool_name="search_disclosures",
                ctx=ctx,
                raw_tickers=qual_tickers,
                dense_queries=[cand_spec.qualitative_query],
                vas_terms=[cand_spec.qualitative_query],
                slots_filter=["BCTN_CORE", "AGM_TARGETS", "QUARTER_NOTES"],
                years_filter=[],
                quarters_filter=[],
                include_cross_ref=False,
                top_k=5,
            )
            if not cand_spec.criteria:
                for doc in disc_res.details.get("selected_documents") or ():
                    dtk = str(doc.get("ticker") or "").upper()
                    if (
                        dtk
                        and dtk != anchor
                        and all(r["ticker"] != dtk for r in top_rows)
                        and len(top_rows) < cand_spec.limit
                    ):
                        top_rows.append(
                            {
                                "ticker": dtk,
                                "company_name": dtk,
                                "sector": "",
                                "template": "CORPORATE",
                                "passed_count": 1,
                                "total_criteria": 1,
                                "all_passed": True,
                                "metrics": {},
                                "units": {},
                                "criteria_notes": [f"Khớp thuyết minh [{doc.get('citation_id')}]"],
                                "has_indexed_filings": True,
                            }
                        )

    picked_tickers = [r["ticker"] for r in top_rows]
    analysis_tickers = list(dict.fromkeys(([anchor] if anchor else []) + picked_tickers))[:6]
    no_pdf = [t for t in analysis_tickers if ctx.available_tickers and t not in ctx.available_tickers]
    cov_note = (
        f"Các mã {', '.join(no_pdf)} được kiểm chứng trên BCTC định lượng Core PG; "
        f"hiện kho thuyết minh PDF chi tiết mới phủ {len(ctx.available_tickers)} mã đã index."
        if no_pdf
        else None
    )
    if not top_rows:
        summary_lines.append("Không tìm thấy ứng viên nào thỏa tiêu chí định lượng trong phạm vi PIT hiện tại.")

    return LocatedTargets(
        analysis_tickers=analysis_tickers,
        anchor_ticker=anchor,
        anchor_metrics=anchor_vals,
        candidate_rows=top_rows,
        summary_lines=summary_lines,
        coverage_note=cov_note,
    )


async def _run_quant_specs_for_claim(
    *,
    claim: ClaimSpec,
    tickers: Sequence[str],
    fs: FactSet | None,
    located: LocatedTargets,
    ctx: ResearchToolContext,
    pulse_bundle: Any = None,
    pulse_cache_holder: dict[str, tuple[list[str], list[str]]] | None = None,
    on_tool_call: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_stage: Callable[[str, str, str], Awaitable[None]] | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Execute all 0-token deterministic specs (`pulse:*`, `fact:*`, `kpi:*`, `risk`, `peers`, `agm`, `kg`, `screen`).

    Returns `(summary_lines, badges, anomaly_escalation_query_or_None)`.
    """
    t_quant_start = time.perf_counter()
    lines: list[str] = []
    badges: list[str] = []
    escalation_hint: str | None = None

    pulse_specs = [ev for ev in claim.evidence if ev.startswith("pulse")]
    fact_items = [ev.split(":", 1)[1] for ev in claim.evidence if ev.startswith("fact:")]
    kpi_items = [ev.split(":", 1)[1] for ev in claim.evidence if ev.startswith("kpi:")]
    wants_risk = "risk" in claim.evidence
    wants_peers = "peers" in claim.evidence
    wants_agm = "agm" in claim.evidence
    wants_screen = "screen" in claim.evidence
    kg_specs = [ev for ev in claim.evidence if ev == "kg" or ev.startswith("kg:")]
    table_specs = [ev for ev in claim.evidence if ev.startswith("table:")]
    wants_mem = "mem" in claim.evidence
    has_fin_quant = bool(pulse_specs or fact_items or kpi_items or wants_risk or wants_peers or wants_agm)
    quant_call_id = f"quant-{claim.id}"
    if has_fin_quant:
        primary_tool = (
            "check_plan_vs_actual"
            if wants_agm
            else "compare_peers"
            if wants_peers
            else "scan_financial_risks"
            if wants_risk and not fact_items and not kpi_items
            else "get_sector_kpis"
            if kpi_items and not fact_items
            else "get_financial_statements"
        )
        spec_preview_parts: list[str] = []
        if pulse_specs:
            spec_preview_parts.append("Tổng hợp Ngành/Thị trường PIT")
        if fact_items:
            spec_preview_parts.append(f"BCTC ({', '.join(fact_items[:4])})")
        if kpi_items:
            spec_preview_parts.append(f"KPI ({', '.join(kpi_items[:4])})")
        if wants_risk:
            spec_preview_parts.append("Quét cờ rủi ro")
        if wants_peers:
            spec_preview_parts.append("So sánh cùng ngành")
        if wants_agm:
            spec_preview_parts.append("KH ĐHĐCĐ vs Thực hiện")
        if on_stage is not None:
            await on_stage("calculator", "running", "Đang đối chiếu số liệu báo cáo tài chính...")
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": quant_call_id,
                    "call_id": quant_call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": primary_tool,
                    "label": "Đối chiếu số liệu BCTC & chỉ số tài chính",
                    "stage": "calculator",
                    "status": "running",
                    "tickers": list(tickers[:6]),
                    "args_preview": " · ".join(spec_preview_parts),
                }
            )

    if pulse_specs and pulse_bundle is not None:
        from sag_api.research.market_pulse import register_pulse_evidence_into_ledger

        if pulse_cache_holder is not None and "registered" in pulse_cache_holder:
            p_lines, p_badges = pulse_cache_holder["registered"]
        else:
            p_lines, p_badges = await register_pulse_evidence_into_ledger(pulse_bundle, ctx.evidence)
            if pulse_cache_holder is not None:
                pulse_cache_holder["registered"] = (p_lines, p_badges)
        lines.extend(p_lines)
        for pb in p_badges:
            if pb not in badges:
                badges.append(pb)

    if wants_screen and located.summary_lines:
        lines.extend(located.summary_lines[:6])
        for r in located.candidate_rows:
            badges.append(f"[SCR:{r['ticker']}]")
            if r.get("calc_id"):
                badges.append(f"[{r['calc_id']}]")

    if wants_mem:
        for bid, mem in list(ctx.evidence.recalled_memories.items())[:4]:
            lines.append(f"[MEM:{bid}] {mem.get('content_text', '')}")
            badges.append(f"[MEM:{bid}]")
        for sym, pos in list(ctx.evidence.portfolio_snapshots.items())[:4]:
            lines.append(
                f"[PORT:{sym}] Tỷ trọng={pos.get('weight_pct')}% | Giá vốn={pos.get('avg_cost')} | "
                f"Lãi/lỗ={pos.get('unrealized_pnl_pct')}%"
            )
            badges.append(f"[PORT:{sym}]")
        if ctx.session_ledger.valid_turns:
            rec_session = ctx.session_ledger.recall(tickers=tickers, limit=2)
            for rt in rec_session.get("recalled_turns") or ():
                lines.append(
                    f"Ngữ cảnh phiên Lượt {rt['turn_index']}: Hỏi '{str(rt['user_query'])[:140]}' -> "
                    f"{str(rt['assistant_excerpt'])[:260]}"
                )

    if fs is not None and tickers:
        # 1) Facts + accounting-safe composite totals (prevents mental addition/subtraction errors!)
        if fact_items:
            for tk in tickers:
                if tk not in fs.templates:
                    continue
                tmpl = fs.templates[tk]
                applicable = {it.item_id for it in items_for_template(tmpl)}
                fact_objs = []
                item_summaries: list[str] = []
                for item_id in fact_items:
                    if item_id not in applicable:
                        continue
                    spec = CATALOG_BY_ID[item_id]
                    cf_note = ""
                    if spec.kind in ("STOCK", "VENDOR_RATIO"):
                        q_series = fs.series(tk, item_id, "Q")[-3:]
                        fy_series = fs.series(tk, item_id, "FY")[-2:]
                        chosen = q_series if q_series else fy_series
                    else:
                        raw_q = fs.series(tk, item_id, "Q")[-3:]
                        # Vendor quarterly cash flows are sparse/YTD-cumulative: never show them as single quarters
                        q_series = [f for f in raw_q if FLAG_QUARTERLY_CF_UNRELIABLE not in f.flags]
                        if len(q_series) < len(raw_q):
                            cf_note = (
                                " (Lưu ý: số quý của dòng tiền từ nhà cung cấp thưa/lũy kế nên đã loại; "
                                "chỉ dùng số năm FY)"
                            )
                        fy_series = fs.series(tk, item_id, "FY")[-2:]
                        ttm_series = fs.series(tk, item_id, "TTM")[-1:]
                        chosen = [*q_series, *fy_series[-1:], *ttm_series]
                    if not chosen:
                        item_summaries.append(f"{spec.label_vi}: chưa có dữ liệu PIT")
                        continue
                    fact_objs.extend(chosen)
                    pts = ", ".join(f"{f.period_label}={fmt_value(f.value, f.unit)}" for f in chosen[-3:])
                    note_guard = ""
                    if item_id == "st_borrowings":
                        note_guard = (
                            " (Lưu ý kế toán: chỉ tiêu này ĐÃ là TỔNG vay & nợ thuê tài chính ngắn hạn trên CĐKT, "
                            "đã bao gồm nợ dài hạn đến hạn trả — KHÔNG cộng thêm các khoản mục con từ thuyết minh)"
                        )
                    item_summaries.append(f"{spec.label_vi}: {pts}{note_guard}{cf_note}")

                # Accounting-safe deterministic composites so LLM never mentally adds/subtracts/divides
                lq = fs.latest_period(tk, "Q")
                common_size_inputs: dict[str, dict[str, Any]] = {}
                if lq is not None:
                    fy_q, fq_q = lq
                    ta_r = fs.get(tk, "total_assets", "Q", fy_q, fq_q)
                    if ta_r.status is LookupStatus.OK and ta_r.fact is not None and ta_r.fact.value > 0:
                        ta_val = ta_r.fact.value
                        cs_parts: list[str] = []
                        for bs_item in (
                            "cash_and_equivalents",
                            "st_investments",
                            "st_receivables",
                            "lt_receivables",
                            "inventory",
                            "st_borrowings",
                            "lt_borrowings",
                            "equity",
                        ):
                            if bs_item not in fact_items or bs_item not in applicable:
                                continue
                            bs_r = fs.get(tk, bs_item, "Q", fy_q, fq_q)
                            if bs_r.status is LookupStatus.OK and bs_r.fact is not None:
                                share = bs_r.fact.value / ta_val
                                common_size_inputs[f"{bs_item}_to_assets@Q{fq_q}/{fy_q}"] = {
                                    "value": share,
                                    "unit": "RATIO",
                                }
                                bs_lbl = CATALOG_BY_ID[bs_item].label_vi
                                cs_parts.append(f"{bs_lbl}/Tổng TS={fmt_value(share, 'RATIO')}")
                        if cs_parts:
                            item_summaries.append(f"Tỷ trọng trên Tổng tài sản Q{fq_q}/{fy_q}: " + ", ".join(cs_parts))
                    # Total interest-bearing debt = st_borrowings + lt_borrowings
                    if "st_borrowings" in fact_items or "lt_borrowings" in fact_items:
                        st_r = fs.get(tk, "st_borrowings", "Q", fy_q, fq_q)
                        lt_r = fs.get(tk, "lt_borrowings", "Q", fy_q, fq_q)
                        if st_r.status is LookupStatus.OK and st_r.fact is not None:
                            lt_v = lt_r.fact.value if (lt_r.status is LookupStatus.OK and lt_r.fact) else 0.0
                            tot_d = st_r.fact.value + lt_v
                            saved_td = await ctx.evidence.register_calculator_trace(
                                {
                                    "formula": "st_borrowings + lt_borrowings",
                                    "description": f"Tổng nợ vay chịu lãi (ngắn hạn + dài hạn) {tk} Q{fq_q}/{fy_q}",
                                    "inputs": {
                                        f"st_borrowings@Q{fq_q}/{fy_q}": {"value": st_r.fact.value, "unit": "VND"},
                                        f"lt_borrowings@Q{fq_q}/{fy_q}": {"value": lt_v, "unit": "VND"},
                                    },
                                    "result": tot_d,
                                    "formatted": fmt_value(tot_d, "VND"),
                                    "ticker": tk,
                                    "format_style": "number",
                                    "source": "DETERMINISTIC_COMPOSITE",
                                }
                            )
                            badges.append(f"[{saved_td['calc_id']}]")
                            item_summaries.append(
                                f"[{saved_td['calc_id']}] Tổng nợ vay chịu lãi {tk} Q{fq_q}/{fy_q} "
                                f"(vay ngắn hạn {fmt_value(st_r.fact.value, 'VND')} + vay dài hạn "
                                f"{fmt_value(lt_v, 'VND')}): {fmt_value(tot_d, 'VND')}"
                            )
                    # Liquidity context for short-term debt: cash alone hides term deposits (st_investments)
                    if "st_borrowings" in fact_items and "st_investments" in applicable:
                        c_r = fs.get(tk, "cash_and_equivalents", "Q", fy_q, fq_q)
                        i_r = fs.get(tk, "st_investments", "Q", fy_q, fq_q)
                        sd_r = fs.get(tk, "st_borrowings", "Q", fy_q, fq_q)
                        if (
                            c_r.status is LookupStatus.OK
                            and c_r.fact is not None
                            and sd_r.status is LookupStatus.OK
                            and sd_r.fact is not None
                        ):
                            inv_v = i_r.fact.value if (i_r.status is LookupStatus.OK and i_r.fact) else 0.0
                            liquid_v = c_r.fact.value + inv_v
                            cover = liquid_v / sd_r.fact.value if sd_r.fact.value > 0 else None
                            saved_lq = await ctx.evidence.register_calculator_trace(
                                {
                                    "formula": "(cash_and_equivalents + st_investments) / st_borrowings",
                                    "description": (
                                        f"Tiền + đầu tư tài chính ngắn hạn so với vay ngắn hạn {tk} Q{fq_q}/{fy_q}"
                                    ),
                                    "inputs": {
                                        f"cash_and_equivalents@Q{fq_q}/{fy_q}": {
                                            "value": c_r.fact.value,
                                            "unit": "VND",
                                        },
                                        f"st_investments@Q{fq_q}/{fy_q}": {"value": inv_v, "unit": "VND"},
                                        "liquid_assets": {"value": liquid_v, "unit": "VND"},
                                        f"st_borrowings@Q{fq_q}/{fy_q}": {"value": sd_r.fact.value, "unit": "VND"},
                                    },
                                    "result": cover,
                                    "formatted": fmt_value(cover, "X") if cover is not None else "",
                                    "ticker": tk,
                                    "format_style": "ratio",
                                    "source": "DETERMINISTIC_COMPOSITE",
                                }
                            )
                            badges.append(f"[{saved_lq['calc_id']}]")
                            cover_txt = f" = {fmt_value(cover, 'X')} vay ngắn hạn" if cover is not None else ""
                            item_summaries.append(
                                f"[{saved_lq['calc_id']}] Thanh khoản sẵn có {tk} Q{fq_q}/{fy_q}: tiền "
                                f"{fmt_value(c_r.fact.value, 'VND')} + đầu tư tài chính ngắn hạn "
                                f"{fmt_value(inv_v, 'VND')} = {fmt_value(liquid_v, 'VND')}{cover_txt}"
                            )
                    # Securities net FVTPL = fvtpl_gains - fvtpl_losses (prevents gain/loss sign flip!)
                    if tmpl is Template.SECURITIES and ("fvtpl_gains" in fact_items or "fvtpl_losses" in fact_items):
                        g_r = fs.get(tk, "fvtpl_gains", "Q", fy_q, fq_q)
                        l_r = fs.get(tk, "fvtpl_losses", "Q", fy_q, fq_q)
                        if g_r.status is LookupStatus.OK and l_r.status is LookupStatus.OK and g_r.fact and l_r.fact:
                            net_fvtpl = g_r.fact.value - l_r.fact.value
                            sign_lbl = "LÃI RÒNG" if net_fvtpl >= 0 else "LỖ RÒNG"
                            saved_fv = await ctx.evidence.register_calculator_trace(
                                {
                                    "formula": "fvtpl_gains - fvtpl_losses",
                                    "description": f"Kết quả thuần tự doanh FVTPL ({sign_lbl}) {tk} Q{fq_q}/{fy_q}",
                                    "inputs": {
                                        "fvtpl_gains": {"value": g_r.fact.value, "unit": "VND"},
                                        "fvtpl_losses": {"value": l_r.fact.value, "unit": "VND"},
                                    },
                                    "result": net_fvtpl,
                                    "formatted": fmt_value(net_fvtpl, "VND"),
                                    "ticker": tk,
                                    "format_style": "number",
                                    "source": "DETERMINISTIC_COMPOSITE",
                                }
                            )
                            badges.append(f"[{saved_fv['calc_id']}]")
                            item_summaries.append(
                                f"[{saved_fv['calc_id']}] Kết quả thuần FVTPL Q{fq_q}/{fy_q}: {sign_lbl} "
                                f"{fmt_value(net_fvtpl, 'VND')} (Lãi FVTPL {fmt_value(g_r.fact.value, 'VND')} "
                                f"trừ Lỗ FVTPL {fmt_value(l_r.fact.value, 'VND')})"
                            )

                if fact_objs:
                    trace_inputs: dict[str, Any] = {
                        f"{f.item}@{f.period_label}": {"value": f.value, "unit": f.unit} for f in fact_objs
                    }
                    trace_inputs.update(common_size_inputs)
                    saved_fs = await ctx.evidence.register_calculator_trace(
                        {
                            "formula": "fact_sheet_slice",
                            "description": f"Số liệu BCTC {tk} cho ý {claim.id} (PIT {ctx.as_of_date.isoformat()})",
                            "inputs": trace_inputs,
                            "result": None,
                            "formatted": "",
                            "ticker": tk,
                            "format_style": "number",
                            "source": "CORE_PG_FACTS",
                        }
                    )
                    cid = str(saved_fs["calc_id"])
                    badges.append(f"[{cid}]")
                    for s_ln in item_summaries:
                        lines.append(f"[{cid}] {tk} · {s_ln}" if not s_ln.startswith("[CALC-") else s_ln)
                elif item_summaries:
                    for s_ln in item_summaries:
                        lines.append(f"{tk} · {s_ln}")

        # 2) KPIs
        if kpi_items:
            for tk in tickers:
                if tk not in fs.templates:
                    continue
                p_ref = _resolve_period_ref(fs, tk, claim.period)
                if p_ref is None:
                    continue
                for kid in kpi_items:
                    if kid not in KPI_BY_ID:
                        continue
                    kv = compute_kpi(fs, tk, kid, p_ref)
                    if kv.status is KpiStatus.OK and kv.value is not None:
                        cid = await _register_kpi(ctx, kv)
                        badges.append(f"[{cid}]")
                        extra = f" ({kv.detail})" if kv.detail else ""
                        lines.append(
                            f"[{cid}] {tk} · {kv.label_vi} ({kv.period}): {fmt_value(kv.value, kv.unit)}{extra}"
                        )
                    elif kv.status is KpiStatus.INSUFFICIENT_DATA:
                        lines.append(f"{tk} · {kv.label_vi}: thiếu dữ liệu ({kv.detail})")

        # 3) Deterministic Risk & Earnings Quality Scan
        if wants_risk:
            for tk in tickers:
                if tk not in fs.templates:
                    continue
                eq = earnings_quality(fs, tk)
                rf = red_flag_scan(fs, tk)
                seen_codes: set[str] = set()
                tk_flags: list[str] = []
                for fl in [*rf.get("flags", []), *eq.get("signals", [])]:
                    if fl["code"] in seen_codes:
                        continue
                    seen_codes.add(fl["code"])
                    obs = fl.get("observed")
                    cid_tag = ""
                    if isinstance(obs, int | float):
                        saved_fl = await ctx.evidence.register_calculator_trace(
                            {
                                "formula": f"rule:{fl['code']}",
                                "description": f"{fl['message_vi']} ({tk} {fl['period']})",
                                "inputs": {
                                    f"ev_{i}": {"value": e.get("value")}
                                    for i, e in enumerate(fl.get("evidence", []))
                                    if isinstance(e, Mapping) and isinstance(e.get("value"), int | float)
                                },
                                "result": float(obs),
                                "formatted": f"{float(obs):.4g}",
                                "ticker": tk,
                                "format_style": "number",
                                "source": "DETERMINISTIC_RULE_ENGINE",
                            }
                        )
                        cid_tag = f"[{saved_fl['calc_id']}] "
                        badges.append(f"[{saved_fl['calc_id']}]")
                    sev_label = {
                        "HIGH": "Cảnh báo rủi ro cao",
                        "MEDIUM": "Cảnh báo rủi ro trung bình",
                        "LOW": "Lưu ý dữ liệu",
                    }.get(str(fl.get("severity") or "").upper(), "Lưu ý")
                    tk_flags.append(f"{cid_tag}{sev_label}: {fl['message_vi']}")
                    if fl["severity"] in ("HIGH", "MEDIUM") and escalation_hint is None:
                        escalation_hint = _flag_to_vas_followup(fl["code"])
                if tk_flags:
                    lines.extend(f"{tk} · {f_ln}" for f_ln in tk_flags[:4])
                else:
                    lines.append(f"{tk} · Quét rủi ro tất định: không phát hiện cờ cảnh báo trọng yếu.")

        # 4) Peer comparison (auto-resolves top same-sector peers when len(tickers) == 1!)
        if wants_peers and tickers:
            peer_group = list(tickers)
            peer_fs = fs
            if len(peer_group) == 1 and ctx.catalog_profiles:
                anchor_tk = peer_group[0]
                prof_by_tk = {
                    str(p.get("ticker") or "").strip().upper(): p
                    for p in ctx.catalog_profiles
                    if str(p.get("ticker") or "").strip()
                }
                anc_prof = prof_by_tk.get(anchor_tk) or {}
                anc_ind = _norm_ascii(str(anc_prof.get("industry") or anc_prof.get("sector") or ""))
                anc_sec = _norm_ascii(str(anc_prof.get("sector") or ""))
                allowed_p = ctx.principal.allowed_project_ids
                same_peers: list[tuple[int, float, str]] = []
                for cand_tk, p in prof_by_tk.items():
                    if cand_tk == anchor_tk:
                        continue
                    if "*" not in allowed_p and cand_tk not in allowed_p:
                        continue
                    p_ind = _norm_ascii(str(p.get("industry") or ""))
                    p_sec = _norm_ascii(str(p.get("sector") or ""))
                    tier_match = (
                        2
                        if (anc_ind and (p_ind == anc_ind or anc_ind in p_ind or p_ind in anc_ind))
                        else (1 if (anc_sec and p_sec == anc_sec) else 0)
                    )
                    if tier_match > 0:
                        mcap = float(p.get("market_cap") or 0.0)
                        same_peers.append((tier_match, mcap, cand_tk))
                same_peers.sort(key=lambda item: (-item[0], -item[1], item[2]))
                extra_peers = [tk_p for _, _, tk_p in same_peers[:3]]
                if extra_peers and ctx.core_reader is not None:
                    peer_group = [anchor_tk, *extra_peers]
                    try:
                        peer_fs = await _fact_set(ctx, peer_group)
                    except Exception:  # noqa: BLE001
                        peer_group = list(tickers)
                        peer_fs = fs
            if len(peer_group) >= 2 and peer_fs is not None:
                peer_p_ref = _resolve_period_ref(peer_fs, peer_group[0], claim.period) if claim.period else None
                pc = peer_compare(peer_fs, peer_group, kpi_items or None, period=peer_p_ref)
                if pc.get("status") == "OK":
                    for kid, row in list(pc["kpis"].items())[:6]:
                        row_parts: list[str] = []
                        lbl = kid
                        for tk in pc["tickers"]:
                            v = row.get(tk)
                            if not v or v.get("status") != "OK":
                                continue
                            lbl = v["label_vi"]
                            kv_obj = KpiValue(
                                kpi_id=v["kpi_id"],
                                label_vi=v["label_vi"],
                                ticker=tk,
                                period=v["period"],
                                value=v["value"],
                                unit=v["unit"],
                                formula=v["formula"],
                                status=KpiStatus.OK,
                                inputs=tuple(v["inputs"]),
                                flags=tuple(v["flags"]),
                            )
                            cid = await _register_kpi(ctx, kv_obj)
                            badges.append(f"[{cid}]")
                            rk = f" (#{v['rank']})" if "rank" in v else ""
                            row_parts.append(f"{tk}={fmt_value(v['value'], v['unit'])}{rk} [{cid}]")
                        if row_parts:
                            lines.append(f"So sánh {lbl}: " + " | ".join(row_parts))

        # 5) AGM Plan vs Actual
        if wants_agm:
            fy_plan = ctx.as_of_date.year
            for tk in tickers[:3]:
                if tk not in fs.templates:
                    continue
                tmpl = fs.templates[tk]
                applicable = {it.item_id for it in items_for_template(tmpl)}
                prior = {}
                for it in ("revenue_net", "pbt", "npat", "npat_parent"):
                    r = fs.get(tk, it, "FY", fy_plan - 1)
                    if r.status is LookupStatus.OK and r.fact is not None:
                        prior[it] = r.fact.value
                ex = extract_plan_targets(await _agm_tables(ctx, tk), fy_plan, prior_actuals=prior)
                targets = [
                    PlanTarget(it, cand.value, f"AGM_TARGETS {cand.table_id} tr.{cand.page}")
                    for it, cand in ex.targets.items()
                    if it in applicable
                ]
                if not targets:
                    lines.append(f"{tk} · Không có bảng kế hoạch ĐHĐCĐ năm {fy_plan} hợp lệ trong PIT.")
                    continue
                pva = plan_vs_actual(fs, tk, fy_plan, targets)
                for row in pva.get("rows", []):
                    if row.get("status") != "OK":
                        continue
                    lbl = CATALOG_BY_ID[row["item"]].label_vi
                    saved_p = await ctx.evidence.register_calculator_trace(
                        {
                            "formula": "ytd_actual / plan_target",
                            "description": f"% hoàn thành KH {lbl} {tk} {fy_plan}",
                            "inputs": {
                                f"plan:{row['item']}": {"value": row["target"], "unit": "VND"},
                                f"ytd:{row['item']}": {"value": row["ytd_actual"], "unit": "VND"},
                            },
                            "result": row["achievement"],
                            "formatted": f"{row['achievement'] * 100:.1f}%",
                            "ticker": tk,
                            "format_style": "percent",
                            "source": "DETERMINISTIC_PLAN_ENGINE",
                        }
                    )
                    badges.append(f"[{saved_p['calc_id']}]")
                    lines.append(
                        f"[{saved_p['calc_id']}] {tk} · KH {lbl} {fy_plan}: mục tiêu "
                        f"{fmt_value(row['target'], 'VND')} ({row['target_source']}), lũy kế {row['ytd_period']} "
                        f"đạt {fmt_value(row['ytd_actual'], 'VND')} ({row['achievement'] * 100:.1f}% KH, "
                        f"tiến độ {row['pace']})."
                    )

    if has_fin_quant and on_tool_result is not None:
        q_dur = int((time.perf_counter() - t_quant_start) * 1000)
        calc_badges = [b.strip("[]") for b in badges if b.startswith("[CALC-")]
        latest_calc_preview: dict[str, Any] | None = None
        if calc_badges and ctx.evidence.calculator_traces:
            first_cid = calc_badges[0]
            matched_tr = next(
                (t for t in ctx.evidence.calculator_traces if str(t.get("calc_id") or "") == first_cid),
                None,
            )
            if matched_tr and matched_tr.get("formatted"):
                latest_calc_preview = {
                    "calc_id": first_cid,
                    "formula": str(matched_tr.get("formula") or ""),
                    "description": str(matched_tr.get("description") or ""),
                    "formatted": str(matched_tr.get("formatted") or ""),
                }
        await on_tool_result(
            {
                "id": quant_call_id,
                "call_id": quant_call_id,
                "type": "tool_call",
                "turn": 1,
                "tool": primary_tool,
                "label": "Đối chiếu số liệu BCTC & chỉ số tài chính",
                "stage": "calculator",
                "status": "done",
                "duration_ms": q_dur,
                "returned_count": len(lines),
                "summary": (
                    f"Đã trích xuất {len(lines)} chỉ tiêu tài chính"
                    + (f" ({', '.join(calc_badges[:3])})" if calc_badges else "")
                ),
                "calc_result": latest_calc_preview,
            }
        )
        if on_stage is not None:
            await on_stage("calculator", "done", "Hoàn tất đối chiếu số liệu tài chính")

    # 6) Knowledge Graph 3-layer query (`0` LLM tokens)
    if kg_specs and ctx.enable_kg and tickers:

        async def _noop(_p: Any) -> None:
            return None

        kg_call_id = f"kg-{claim.id}"
        tctx = ToolContext(
            run_id="brief-kg",
            tool_call_id=kg_call_id,
            data=ctx,
            cancellation=CancellationToken(),
            _on_progress=_noop,
        )
        macro_hint = kg_specs[0].split(":", 1)[1].strip() if ":" in kg_specs[0] else ""
        kg_args: dict[str, Any] = {"tickers": list(tickers[:4])}
        if macro_hint:
            kg_args["macro_factor"] = macro_hint
        if on_stage is not None:
            await on_stage("graph_hop", "running", "Đang tra cứu liên kết ngành & vĩ mô...")
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": kg_call_id,
                    "call_id": kg_call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "query_knowledge_graph",
                    "label": "Đối chiếu chuỗi giá trị & yếu tố vĩ mô",
                    "stage": "graph_hop",
                    "status": "running",
                    "tickers": list(tickers[:4]),
                    "args_preview": f"Mã {', '.join(tickers[:4])}" + (f" · vĩ mô `{macro_hint}`" if macro_hint else ""),
                }
            )
        t_kg = time.perf_counter()
        try:
            kg_res = await query_knowledge_graph_tool.execute(kg_args, tctx)
            hops_list = list(kg_res.details.get("kg_hops") or ())[:5]
            for h in hops_list:
                b_tag = f"[KG:{h.get('layer')}|{h.get('verification_tier')}]"
                badges.append(b_tag)
                lines.append(f"{b_tag} {h.get('src_label')} --({h.get('relation_type')})--> {h.get('dst_label')}")
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": kg_call_id,
                        "call_id": kg_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "query_knowledge_graph",
                        "label": "Đối chiếu chuỗi giá trị & yếu tố vĩ mô",
                        "stage": "graph_hop",
                        "status": "done",
                        "duration_ms": int((time.perf_counter() - t_kg) * 1000),
                        "returned_count": len(hops_list),
                        "summary": f"Đối chiếu {len(hops_list)} mối liên hệ ngành & vĩ mô",
                        "kg_hops": hops_list,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            lines.append("Đồ thị tri thức 3 tầng: chưa có liên kết PIT phù hợp.")
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": kg_call_id,
                        "call_id": kg_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "query_knowledge_graph",
                        "label": "Đối chiếu chuỗi giá trị & yếu tố vĩ mô",
                        "stage": "graph_hop",
                        "status": "error",
                        "duration_ms": int((time.perf_counter() - t_kg) * 1000),
                        "summary": f"Chưa có dữ liệu liên kết ({type(exc).__name__})",
                    }
                )
        if on_stage is not None:
            await on_stage("graph_hop", "done", "Hoàn tất đối chiếu ngành & vĩ mô")

    # 7) Hydrated 2D PDF tables (`0` LLM tokens)
    if table_specs and tickers:

        async def _noop_t(_p: Any) -> None:
            return None

        stmt_kind = table_specs[0].split(":", 1)[1].strip() or "ALL"
        tbl_call_id = f"tbl-{claim.id}"
        tctx = ToolContext(
            run_id="brief-tbl",
            tool_call_id=tbl_call_id,
            data=ctx,
            cancellation=CancellationToken(),
            _on_progress=_noop_t,
        )
        if on_stage is not None:
            await on_stage("table_hydration", "running", "Đang trích xuất bảng báo cáo tài chính gốc...")
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": tbl_call_id,
                    "call_id": tbl_call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "hydrate_financial_tables",
                    "label": "Trích xuất bảng báo cáo tài chính gốc",
                    "stage": "table_hydration",
                    "status": "running",
                    "tickers": list(tickers[:3]),
                    "args_preview": f"Mã {', '.join(tickers[:3])} · loại `{stmt_kind}`",
                }
            )
        t_tbl = time.perf_counter()
        try:
            tbl_res = await hydrate_financial_tables_tool.execute(
                {"tickers": list(tickers[:3]), "statement_kind": stmt_kind},
                tctx,
            )
            tbl_list = list(tbl_res.details.get("hydrated_tables") or ())[:3]
            for tbl in tbl_list:
                tid = tbl.get("table_id")
                if tid:
                    badges.append(f"[TBL:{tid}]")
                    row_previews: list[str] = []
                    for rw in (tbl.get("rows") or ())[:5]:
                        if isinstance(rw, Mapping) and rw.get("label"):
                            vals_s = ", ".join(str(v) for v in (rw.get("values") or ())[:3] if v is not None)
                            if vals_s:
                                row_previews.append(f"{rw['label']}: {vals_s}")
                        elif isinstance(rw, list) and len(rw) >= 2:
                            r_lbl = str(rw[0].get("raw_text") if isinstance(rw[0], Mapping) else rw[0] or "").strip()
                            r_vals = [
                                str(c.get("raw_text") if isinstance(c, Mapping) else c or "").strip() for c in rw[1:4]
                            ]
                            r_vals = [v for v in r_vals if v and v != "-"]
                            if r_lbl and r_vals:
                                row_previews.append(f"{r_lbl}: {', '.join(r_vals)}")
                    preview_tail = f" -> {' | '.join(row_previews)}" if row_previews else ""
                    lines.append(
                        f"[TBL:{tid}] Bảng 2D {tbl.get('project_id')}: {tbl.get('title')} "
                        f"({tbl.get('verification_status')}){preview_tail}"
                    )
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": tbl_call_id,
                        "call_id": tbl_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "hydrate_financial_tables",
                        "label": "Trích xuất bảng báo cáo tài chính gốc",
                        "stage": "table_hydration",
                        "status": "done",
                        "duration_ms": int((time.perf_counter() - t_tbl) * 1000),
                        "returned_count": len(tbl_list),
                        "summary": f"Đã tải {len(tbl_list)} bảng số liệu kiểm toán",
                        "hydrated_tables": tbl_list,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": tbl_call_id,
                        "call_id": tbl_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "hydrate_financial_tables",
                        "label": "Trích xuất bảng báo cáo tài chính gốc",
                        "stage": "table_hydration",
                        "status": "error",
                        "duration_ms": int((time.perf_counter() - t_tbl) * 1000),
                        "summary": f"Không tải được bảng số liệu ({type(exc).__name__})",
                    }
                )
        if on_stage is not None:
            await on_stage("table_hydration", "done", "Hoàn tất trích xuất bảng BCTC")

    return lines, list(dict.fromkeys(badges)), escalation_hint


def _flag_to_vas_followup(flag_code: str) -> str:
    mapping = {
        "LOW_CASH_CONVERSION": "lưu chuyển tiền hoạt động kinh doanh phải thu tồn kho",
        "HIGH_ACCRUALS": "phải thu ngắn hạn giao dịch bên liên quan",
        "SHORT_TERM_REFINANCING_RISK": "vay và nợ thuê tài chính ngắn hạn kỳ hạn lãi suất",
        "HIGH_NET_LEVERAGE": "vay ngắn hạn vay dài hạn trái phiếu đến hạn trả",
        "RECEIVABLES_OUTPACE_REVENUE": "phải thu ngắn hạn khách hàng phải thu khác bên liên quan",
        "INVENTORY_OUTPACE_REVENUE": "hàng tồn kho dự phòng giảm giá hàng tồn kho",
        "NPL_ABOVE_3PCT": "chất lượng nợ cho vay nợ nhóm 3 nhóm 4 nhóm 5",
        "UNREALISED_PROFIT_DEPENDENCE": "tài sản tài chính FVTPL đánh giá lại danh mục tự doanh",
    }
    return mapping.get(flag_code, "thuyết minh báo cáo tài chính khoản mục trọng yếu")


def _evaluate_premise_direction(
    claim: ClaimSpec,
    fs: FactSet | None,
    tickers: Sequence[str],
) -> tuple[VerdictStatus | None, str | None, dict[str, Any] | None]:
    """Deterministically verify directional premises (e.g. 'NIM ACB sập', 'lãi tăng gấp đôi') on FactSet.

    Returns `(status, Vietnamese_summary_or_None, calc_trace_payload_or_None)` so every compared figure
    can be registered in `ctx.evidence` for Stage 7 numeric grounding.
    """
    if fs is None or not tickers:
        return None, None, None
    tk = tickers[0]
    if tk not in fs.templates:
        return None, None, None
    stmt = claim.statement.lower()
    neg_claim = bool(_NEGATIVE_PREMISE_RE.search(stmt))
    pos_claim = bool(_POSITIVE_PREMISE_RE.search(stmt))
    if not neg_claim and not pos_claim:
        return None, None, None

    # Vendor ratios often have a single quarter; fall back to the equivalent computed KPI series
    proxy_kpi = {"vr_nim": "nii_to_avg_assets", "vr_cir": "cir", "vr_ldr": "loan_to_deposit"}
    ev_to_check = list(claim.evidence)
    for ev in claim.evidence:
        if ev.startswith("fact:"):
            item_id = ev.split(":", 1)[1]
            proxy = proxy_kpi.get(item_id)
            if proxy and len(fs.series(tk, item_id, "Q")) < 2 and f"kpi:{proxy}" not in ev_to_check:
                ev_to_check.append(f"kpi:{proxy}")

    # Check first fact or KPI in premise evidence that has >= 2 comparable periods
    for ev in ev_to_check:
        if ev.startswith("fact:"):
            item_id = ev.split(":", 1)[1]
            q_ser = fs.series(tk, item_id, "Q")
            if len(q_ser) < 2:
                continue
            cur, prev = q_ser[-1], q_ser[-2]
            lbl_vi = CATALOG_BY_ID[item_id].label_vi
            if "lỗ" in stmt or "âm" in stmt:
                if cur.value > 0:
                    payload = {
                        "formula": f"{item_id}({cur.period_label})",
                        "description": f"Kiểm chứng tiền đề lỗ/âm cho {lbl_vi} {tk} {cur.period_label}",
                        "inputs": {f"{item_id}@{cur.period_label}": {"value": cur.value, "unit": cur.unit}},
                        "result": cur.value,
                        "formatted": fmt_value(cur.value, cur.unit),
                        "ticker": tk,
                        "format_style": "percent" if cur.unit == "RATIO" else "number",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    return (
                        "REFUTED",
                        f"BÁC BỎ TIỀN ĐỀ: {lbl_vi} {tk} kỳ {cur.period_label} "
                        f"đạt dương {fmt_value(cur.value, cur.unit)} (không lỗ/âm).",
                        payload,
                    )
            if neg_claim and prev.value > 0:
                rel_chg = (cur.value - prev.value) / abs(prev.value)
                abs_chg = cur.value - prev.value
                # For percentage ratios like NIM (~0.035), a 'collapse' implies >= 35 bps or >=10% rel drop
                is_collapse = (abs_chg <= -0.0035 and rel_chg <= -0.10) if cur.unit == "RATIO" else (rel_chg <= -0.15)
                payload = {
                    "formula": (
                        f"({item_id}@{cur.period_label} - {item_id}@{prev.period_label}) / abs({prev.period_label})"
                    ),
                    "description": (
                        f"So sánh tiền đề giảm mạnh {lbl_vi} {tk} ({prev.period_label} -> {cur.period_label})"
                    ),
                    "inputs": {
                        f"{item_id}@{cur.period_label}": {"value": cur.value, "unit": cur.unit},
                        f"{item_id}@{prev.period_label}": {"value": prev.value, "unit": prev.unit},
                    },
                    "result": rel_chg,
                    "formatted": f"{rel_chg * 100:+.1f}%",
                    "ticker": tk,
                    "format_style": "percent",
                    "source": "PREMISE_DIRECTION_CHECK",
                }
                if not is_collapse:
                    return (
                        "REFUTED",
                        f"BÁC BỎ TIỀN ĐỀ: {lbl_vi} {tk} kỳ {cur.period_label} ở mức "
                        f"{fmt_value(cur.value, cur.unit)} so với kỳ liền trước {prev.period_label} là "
                        f"{fmt_value(prev.value, prev.unit)} (biến động {rel_chg * 100:+.1f}%, "
                        "KHÔNG sụt giảm mạnh / sập như tiền đề câu hỏi).",
                        payload,
                    )
                return ("CONFIRMED", None, payload)
            if pos_claim:
                # Prefer same-quarter prior year for growth claims ("tăng gấp đôi"), else previous quarter
                yoy_matches = [
                    f
                    for f in q_ser[:-1]
                    if f.fiscal_year == cur.fiscal_year - 1 and f.fiscal_quarter == cur.fiscal_quarter
                ]
                base = yoy_matches[-1] if yoy_matches else prev
                if base.value > 0:
                    rel_chg = (cur.value - base.value) / abs(base.value)
                    thresh = 0.80 if ("gấp đôi" in stmt or "bằng lần" in stmt) else 0.30
                    payload = {
                        "formula": (
                            f"({item_id}@{cur.period_label} - {item_id}@{base.period_label}) / abs({base.period_label})"
                        ),
                        "description": (
                            f"Kiểm chứng tiền đề tăng mạnh {lbl_vi} {tk} ({base.period_label} -> {cur.period_label})"
                        ),
                        "inputs": {
                            f"{item_id}@{cur.period_label}": {"value": cur.value, "unit": cur.unit},
                            f"{item_id}@{base.period_label}": {"value": base.value, "unit": base.unit},
                        },
                        "result": rel_chg,
                        "formatted": f"{rel_chg * 100:+.1f}%",
                        "ticker": tk,
                        "format_style": "percent",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    if rel_chg < thresh:
                        return (
                            "REFUTED",
                            f"BÁC BỎ TIỀN ĐỀ: {lbl_vi} {tk} kỳ {cur.period_label} đạt "
                            f"{fmt_value(cur.value, cur.unit)} so với {base.period_label} là "
                            f"{fmt_value(base.value, base.unit)} (biến động {rel_chg * 100:+.1f}%, "
                            "KHÔNG đạt mức tăng mạnh như tiền đề).",
                            payload,
                        )
                    return ("CONFIRMED", None, payload)
        if ev.startswith("kpi:"):
            kid = ev.split(":", 1)[1]
            lq = fs.latest_period(tk, "Q")
            if lq is not None and kid in KPI_BY_ID:
                cur_ref = PeriodRef("Q", lq[0], lq[1])
                cur_kv = compute_kpi(fs, tk, kid, cur_ref)
                if (
                    pos_claim
                    and kid.endswith("_growth_yoy")
                    and cur_kv.status is KpiStatus.OK
                    and cur_kv.value is not None
                ):
                    thresh = 0.80 if ("gấp đôi" in stmt or "bằng lần" in stmt) else 0.30
                    payload = {
                        "formula": f"{kid}({cur_ref.label})",
                        "description": f"Kiểm chứng tiền đề tăng trưởng {cur_kv.label_vi} {tk} {cur_ref.label}",
                        "inputs": {f"{kid}@{cur_ref.label}": {"value": cur_kv.value, "unit": cur_kv.unit}},
                        "result": cur_kv.value,
                        "formatted": fmt_value(cur_kv.value, cur_kv.unit),
                        "ticker": tk,
                        "format_style": "percent",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    if cur_kv.value < thresh:
                        return (
                            "REFUTED",
                            f"BÁC BỎ TIỀN ĐỀ: {cur_kv.label_vi} {tk} {cur_ref.label} đạt "
                            f"{fmt_value(cur_kv.value, cur_kv.unit)} (không đạt mức tăng như tiền đề).",
                            payload,
                        )
                    return ("CONFIRMED", None, payload)
                prev_q = (lq[0] - 1, 4) if lq[1] == 1 else (lq[0], lq[1] - 1)
                prev_ref = PeriodRef("Q", prev_q[0], prev_q[1])
                prev_kv = compute_kpi(fs, tk, kid, prev_ref)
                if (
                    cur_kv.status is KpiStatus.OK
                    and prev_kv.status is KpiStatus.OK
                    and cur_kv.value is not None
                    and prev_kv.value is not None
                    and neg_claim
                    and prev_kv.value > 0
                ):
                    rel_chg = (cur_kv.value - prev_kv.value) / abs(prev_kv.value)
                    payload = {
                        "formula": f"({kid}@{cur_ref.label} - {kid}@{prev_ref.label}) / abs({prev_ref.label})",
                        "description": (
                            f"So sánh tiền đề giảm mạnh {cur_kv.label_vi} {tk} ({prev_ref.label} -> {cur_ref.label})"
                        ),
                        "inputs": {
                            f"{kid}@{cur_ref.label}": {"value": cur_kv.value, "unit": cur_kv.unit},
                            f"{kid}@{prev_ref.label}": {"value": prev_kv.value, "unit": prev_kv.unit},
                        },
                        "result": rel_chg,
                        "formatted": f"{rel_chg * 100:+.1f}%",
                        "ticker": tk,
                        "format_style": "percent",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    if rel_chg > -0.12:
                        return (
                            "REFUTED",
                            f"BÁC BỎ TIỀN ĐỀ: {cur_kv.label_vi} {tk} {cur_ref.label} đạt "
                            f"{fmt_value(cur_kv.value, cur_kv.unit)} so với kỳ trước {prev_ref.label} là "
                            f"{fmt_value(prev_kv.value, prev_kv.unit)} ({rel_chg * 100:+.1f}%, không sập).",
                            payload,
                        )
                    return ("CONFIRMED", None, payload)
    return None, None, None


_LEAKED_FACET_CODE_RE = re.compile(r"\bF[1-8]_[A-Z0-9_]+\b")


def _parse_doc_spec(doc_ev: str, default_period: str | None) -> tuple[list[str], list[int], list[int], str]:
    from sag_api.research.market_pulse import _SIGNAL_LABEL_VI

    rest = doc_ev.split(":", 1)[1] if ":" in doc_ev else doc_ev
    slot_part, query_part = (
        rest.split("|", 1) if "|" in rest else ("QUARTER_NOTES,BCTC_AUDITED,BCTN_CORE,AGM_TARGETS", rest)
    )
    slots = normalize_disclosure_slots([s.strip() for s in slot_part.split(",") if s.strip()])
    years: list[int] = []
    quarters: list[int] = []
    if default_period:
        qm = _PERIOD_Q_PARSE_RE.search(default_period)
        fym = _PERIOD_FY_PARSE_RE.search(default_period)
        if qm and "QUARTER_NOTES" in slots and len(slots) == 1:
            years = [int(qm.group(2))]
            quarters = [int(qm.group(1))]
        elif fym and "QUARTER_NOTES" not in slots:
            years = [int(fym.group(1))]
    leaked_facets = _LEAKED_FACET_CODE_RE.findall(query_part)
    cleaned_q = re.sub(r"\s+", " ", _LEAKED_FACET_CODE_RE.sub(" ", query_part)).strip()
    if not cleaned_q and leaked_facets:
        cleaned_q = _SIGNAL_LABEL_VI.get(leaked_facets[0], "cơ chế kinh doanh và thuyết minh trọng yếu")
    return slots, years, quarters, cleaned_q


async def _inject_pointer_seed_sections(
    *,
    ctx: ResearchToolContext,
    doc_tickers: Sequence[str],
    slots_list: Sequence[str],
    claim_statement: str,
    pulse_bundle: Any,
) -> tuple[str, list[str]]:
    """Pointer-Guided Seed Injection (`0` LLM tokens, `100%` CanonicalBlock SHA-256 verified).

    Surfaces high-signal `StructuralPointer` passages (`AGM_QA`, `CONSOLIDATION_SCOPE`, `SEGMENT_MDNA`,
    `STRATEGIC_CAPEX`) directly from SAG's deterministic index when broad/sector/market queries lack
    narrow lexical keywords or when Qdrant is unconfigured/misses the AGM/Note section.
    """
    if pulse_bundle is None or not getattr(pulse_bundle, "structural_pointers", None):
        return "", []
    from datetime import datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from sag_api.db.models import Document, DocumentVersion, KnowledgeUnit
    from sag_api.research.chat_orchestrator import RetrievedResearchSection, load_canonical_blocks_for_versions

    want_tks = {t.upper() for t in doc_tickers if t}
    slots_up = {s.upper() for s in slots_list if s}
    stmt_low = (claim_statement or "").lower()
    is_causal_or_story_claim = any(
        k in stmt_low
        for k in (
            "động lực",
            "biên",
            "chiến lược",
            "cạnh tranh",
            "thị trường",
            "hợp nhất",
            "liên kết",
            "công ty con",
            "đhđcđ",
            "cổ đông",
            "lãnh đạo",
            "rủi ro",
            "tăng trưởng",
            "đầu tư",
        )
    )
    matched_ptrs = [
        p
        for p in pulse_bundle.structural_pointers
        if p.ticker in want_tks
        and (
            not slots_up
            or p.sub_doc_slot in slots_up
            or (is_causal_or_story_claim and p.sub_doc_slot in ("AGM_TARGETS", "BCTN_CORE", "BCTC_AUDITED"))
        )
    ][:5]
    if not matched_ptrs:
        return "", []

    ver_ids = sorted({p.document_version_id for p in matched_ptrs if p.document_version_id})
    if not ver_ids:
        return "", []

    try:
        async with ctx.session_factory() as sess:
            if not isinstance(sess, AsyncSession):
                return "", []
            blocks_map = await load_canonical_blocks_for_versions(sess, ver_ids, as_of_date=ctx.as_of_date)
            ver_meta_rows = (
                await sess.execute(
                    select(
                        DocumentVersion.id,
                        DocumentVersion.document_id,
                        DocumentVersion.source_published_at,
                        DocumentVersion.valid_from,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(DocumentVersion.id.in_(ver_ids), Document.is_active.is_(True))
                )
            ).all()
            ku_rows = {
                str(u.id): u
                for u in (
                    await sess.scalars(
                        select(KnowledgeUnit).where(
                            KnowledgeUnit.id.in_([p.unit_id for p in matched_ptrs]),
                            KnowledgeUnit.is_current.is_(True),
                        )
                    )
                ).all()
            }
    except Exception:  # noqa: BLE001
        return "", []

    ver_doc_pub: dict[str, tuple[str, date]] = {}
    for vid, did, spub, vfrom in ver_meta_rows:
        dt = spub or vfrom
        pd_val = dt.date() if isinstance(dt, datetime) else date.fromisoformat(str(dt)[:10])
        if pd_val <= ctx.as_of_date:
            ver_doc_pub[str(vid)] = (str(did or ""), pd_val)

    from sag_api.research.epistemic_foundation import is_residual_english_mirror
    from sag_api.research.market_pulse import _format_slot_period_stamp

    new_secs: list[RetrievedResearchSection] = []
    for p in matched_ptrs:
        dp = ver_doc_pub.get(p.document_version_id)
        ku_obj = ku_rows.get(p.unit_id)
        if dp is None or ku_obj is None:
            continue
        full_txt = str(ku_obj.text or "").strip()
        p_from_val = p.page_from
        p_to_val = p.page_from
        b_from = p.block_ids[0] if p.block_ids else None
        b_to = p.block_ids[-1] if p.block_ids else None
        if p.block_ids and p.document_version_id in blocks_map:
            want_bids = set(p.block_ids)
            stitched_blks = [
                vb
                for vb in blocks_map[p.document_version_id]
                if vb.block_id in want_bids
                and not (p.sub_doc_slot and is_residual_english_mirror(vb.normalized_text))
            ]
            if stitched_blks:
                full_txt = "\n\n".join(vb.normalized_text.strip() for vb in stitched_blks)[:2800]
                p_from_val = min(vb.page_from for vb in stitched_blks)
                p_to_val = max(vb.page_to for vb in stitched_blks)
                b_from = stitched_blks[0].block_id or b_from
                b_to = stitched_blks[-1].block_id or b_to
        if not full_txt:
            continue
        stamp = _format_slot_period_stamp(p.sub_doc_slot, p.fiscal_year, p.fiscal_quarter)
        hd_stamped = f"[{stamp}] {p.section_path}" if stamp and stamp not in p.section_path else p.section_path
        new_secs.append(
            RetrievedResearchSection(
                ticker=p.ticker,
                doc_id=dp[0],
                document_version_id=p.document_version_id,
                sub_doc_slot=p.sub_doc_slot,
                page_from=p_from_val,
                page_to=p_to_val,
                published_date=dp[1],
                content=full_txt,
                heading=hd_stamped,
                table_id=None,
                routing_slot=p.sub_doc_slot,
                hipporag_hops=1,
                score=0.82,
                block_from_id=b_from,
                block_to_id=b_to,
                fiscal_year=p.fiscal_year,
                fiscal_quarter=p.fiscal_quarter,
                evidence_origin="PRIMARY",
            )
        )
    if not new_secs:
        return "", []

    assigned = await ctx.evidence.register_sections(new_secs, canonical_blocks=blocks_map, hipporag_hops=1)
    fmt_blocks: list[str] = []
    cids: list[str] = []
    for cid, sec in assigned:
        tag = f"[{cid}]"
        if tag not in cids:
            cids.append(tag)
        fmt_blocks.append(
            f"[{cid}] ({sec.ticker} | {sec.sub_doc_slot} | tr.{sec.page_from} | {sec.heading}):\n{sec.content[:1800]}"
        )
    return "\n\n".join(fmt_blocks), cids


async def _run_doc_mini_reader(
    *,
    claim: ClaimSpec,
    doc_specs: Sequence[str],
    tickers: Sequence[str],
    quant_context_lines: Sequence[str],
    ctx: ResearchToolContext,
    model: ModelProvider,
    reader_sem: asyncio.Semaphore,
    pulse_bundle: Any = None,
    on_tool_call: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    trace: TraceSink | None = None,
) -> tuple[VerdictStatus, list[str], list[str], list[str], str | None, Usage, int]:
    """Retrieve targeted `[C#]` sections for `claim` and run 1 compact Mini-Reader extraction call."""
    search_log: list[dict[str, Any]] = []

    def _trace_reader(**payload: Any) -> None:
        emit_trace(trace, "mini_reader", claim_id=claim.id, statement=claim.statement, searches=search_log, **payload)

    doc_tickers = [t for t in tickers if not ctx.available_tickers or t in ctx.available_tickers][:4]
    if not doc_tickers:
        no_cov = (
            f"Chưa có tài liệu thuyết minh PDF đã index trong Qdrant cho {', '.join(tickers) or 'phạm vi này'} "
            f"tại PIT {ctx.as_of_date.isoformat()}."
        )
        _trace_reader(outcome="NO_DOC_COVERAGE", status="NO_DATA", findings=[no_cov])
        return ("NO_DATA", [no_cov], [], [], None, Usage(), 0)

    gathered_cids: list[str] = []
    snippet_blocks: list[str] = []
    tool_calls = 0
    enable_xref = len(doc_tickers) <= 2

    async def _run_single_dspec(idx: int, dspec: str) -> tuple[str | None, list[str], dict[str, Any], int]:
        slots, years, quarters, q_txt = _parse_doc_spec(dspec, claim.period)
        call_id = f"doc-{claim.id}-{idx}"
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": call_id,
                    "call_id": call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "search_disclosures",
                    "label": "Tra cứu thuyết minh & tài liệu công bố",
                    "stage": "retrieval",
                    "status": "running",
                    "tickers": doc_tickers,
                    "queries": [q_txt],
                    "sub_doc_slots": slots,
                    "args_preview": f"{', '.join(doc_tickers)} · {','.join(slots)} · {q_txt[:60]}",
                }
            )
        t0 = time.perf_counter()
        res = await _execute_disclosure_search(
            tool_name="search_disclosures",
            ctx=ctx,
            raw_tickers=doc_tickers,
            dense_queries=[f"{claim.statement} {q_txt}"],
            vas_terms=[q_txt],
            slots_filter=slots,
            years_filter=years,
            quarters_filter=quarters,
            include_cross_ref=enable_xref,
            top_k=7,
        )
        local_calls = 1
        widened = False
        if res.details.get("returned_count", 0) == 0 and (years or quarters):
            widened = True
            res = await _execute_disclosure_search(
                tool_name="search_disclosures",
                ctx=ctx,
                raw_tickers=doc_tickers,
                dense_queries=[f"{claim.statement} {q_txt}"],
                vas_terms=[q_txt],
                slots_filter=slots,
                years_filter=[],
                quarters_filter=[],
                include_cross_ref=enable_xref,
                top_k=7,
            )
            local_calls += 1

        dur_ms = int((time.perf_counter() - t0) * 1000)
        if on_tool_result is not None:
            await on_tool_result(
                {
                    "id": call_id,
                    "call_id": call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "search_disclosures",
                    "label": "Tra cứu thuyết minh & tài liệu công bố",
                    "stage": "retrieval",
                    "status": "done",
                    "duration_ms": dur_ms,
                    "summary": f"Trích lọc {res.details.get('returned_count', 0)} đoạn thuyết minh liên quan",
                    "crag_grade": res.details.get("crag_grade"),
                    "returned_count": res.details.get("returned_count", 0),
                    "selected_documents": list(res.details.get("selected_documents") or ())[:8],
                }
            )
        local_cids: list[str] = []
        snip_txt: str | None = None
        if res.details.get("returned_count", 0) > 0:
            snip_txt = res.content
            for doc in res.details.get("selected_documents") or ():
                cid = str(doc.get("citation_id") or "")
                if cid and f"[{cid}]" not in local_cids:
                    local_cids.append(f"[{cid}]")
        log_entry = {
            "doc_spec": dspec,
            "tickers": doc_tickers,
            "slots": slots,
            "fiscal_years": years,
            "fiscal_quarters": quarters,
            "query": q_txt,
            "widened_period_filter": widened,
            "returned_count": res.details.get("returned_count", 0),
            "crag_grade": res.details.get("crag_grade"),
            "selected_documents": list(res.details.get("selected_documents") or ())[:8],
            "duration_ms": dur_ms,
        }
        return snip_txt, local_cids, log_entry, local_calls

    dspec_outcomes = await asyncio.gather(
        *(_run_single_dspec(idx, ds) for idx, ds in enumerate(doc_specs[:MAX_DOC_SPECS_PER_CLAIM], 1))
    )
    for snip_txt, local_cids, log_entry, local_calls in dspec_outcomes:
        tool_calls += local_calls
        search_log.append(log_entry)
        if snip_txt:
            snippet_blocks.append(snip_txt)
        for lc in local_cids:
            if lc not in gathered_cids:
                gathered_cids.append(lc)

    # Pointer-Guided Seed Injection: guarantee high-signal `AGM_QA` / `CONSOLIDATION_SCOPE` / `SEGMENT_MDNA`
    # passages enter the reader window with verified `CanonicalBlock` provenance even on zero-seed queries!
    all_req_slots = [s for ds in doc_specs[:MAX_DOC_SPECS_PER_CLAIM] for s in _parse_doc_spec(ds, claim.period)[0]]
    seed_txt, seed_cids = await _inject_pointer_seed_sections(
        ctx=ctx,
        doc_tickers=doc_tickers,
        slots_list=all_req_slots,
        claim_statement=claim.statement,
        pulse_bundle=pulse_bundle,
    )
    if seed_txt:
        snippet_blocks.append(seed_txt)
        for scid in seed_cids:
            if scid not in gathered_cids:
                gathered_cids.append(scid)

    if not snippet_blocks:
        no_snip = f"Không tìm thấy đoạn thuyết minh phù hợp trong phạm vi PIT {ctx.as_of_date.isoformat()}."
        _trace_reader(outcome="NO_SNIPPETS", status="NO_DATA", findings=[no_snip])
        return ("NO_DATA", [no_snip], [], [], None, Usage(), tool_calls)

    def _collect_verbatim_excerpts(prefer_text: str) -> list[str]:
        cited_nums = [int(m.group(1)) for m in re.finditer(r"\[C(\d+)\]", prefer_text)]
        fallback_nums = [int(b.strip("[]C")) for b in gathered_cids if b.startswith("[C") and b.strip("[]C").isdigit()]
        # Preserve citation priority while maximizing slot diversity (QUARTER_NOTES + BCTN_CORE + AGM_TARGETS)
        all_nums = list(dict.fromkeys([*cited_nums, *fallback_nums]))
        selected_nums: list[int] = []
        seen_slots: set[str] = set()
        for c_num in all_nums:
            if 1 <= c_num <= len(ctx.evidence.sections):
                sl = str(getattr(ctx.evidence.sections[c_num - 1], "sub_doc_slot", "") or "")
                if sl and sl not in seen_slots:
                    seen_slots.add(sl)
                    selected_nums.append(c_num)
                if len(selected_nums) >= 5:
                    break
        for c_num in all_nums:
            if len(selected_nums) >= 5:
                break
            if c_num not in selected_nums:
                selected_nums.append(c_num)

        excerpts: list[str] = []
        for c_num in selected_nums:
            if 1 <= c_num <= len(ctx.evidence.sections):
                sec = ctx.evidence.sections[c_num - 1]
                tk_s = getattr(sec, "ticker", "") or ""
                slot_s = getattr(sec, "sub_doc_slot", "") or ""
                pg_s = getattr(sec, "page_from", 1) or 1
                raw_body = re.sub(r"\s+", " ", str(getattr(sec, "content", "") or "")).strip()
                if raw_body:
                    excerpts.append(f"[C{c_num}] ({tk_s} {slot_s} tr.{pg_s}): {raw_body[:1100]}")
        return excerpts

    quant_hint = "\n".join(quant_context_lines[:3]) if quant_context_lines else "Không có số định lượng đi kèm."
    user_msg = (
        f"Ngày khóa sổ PIT: {ctx.as_of_date.isoformat()}\n"
        f"Ý cần kiểm chứng (`{claim.id}`): {claim.statement}\n"
        f"Ngữ cảnh số liệu BCTC chuẩn hóa đã biết:\n{quant_hint}\n\n"
        f"Các đoạn tài liệu gốc vừa truy xuất:\n" + "\n\n".join(snippet_blocks)
    )
    try:
        async with reader_sem:
            async with asyncio.timeout(30.0):
                raw_json, usage = await _collect_model_text(
                    model,
                    (
                        AgentMessage(role="system", content=MINI_READER_STATIC_SYSTEM_PROMPT),
                        AgentMessage(role="user", content=user_msg),
                    ),
                    turn=1,
                )
    except TimeoutError:
        fb_quotes = _collect_verbatim_excerpts("")
        fb_lines = [q[:420] for q in fb_quotes[:3]] or ["Đã trích xuất trực tiếp đoạn báo cáo gốc liên quan."]
        _trace_reader(
            outcome="READER_TIMEOUT_VERBATIM_FALLBACK",
            status="PARTIAL",
            findings=fb_lines,
            gathered_citations=gathered_cids,
        )
        return ("PARTIAL", fb_lines, gathered_cids, fb_quotes, None, Usage(), tool_calls)

    parsed = _parse_json_obj(raw_json)
    if not parsed:
        # Clean fallback if Mini-Reader returned plain text instead of JSON
        clean_txt = _THINK_RE.sub("", raw_json).strip()
        _trace_reader(
            outcome="NON_JSON_OUTPUT",
            user_prompt=user_msg,
            raw_output=raw_json,
            usage=usage_to_dict(usage),
            status="PARTIAL" if clean_txt else "NO_DATA",
        )
        if clean_txt:
            return (
                "PARTIAL",
                [clean_txt[:500]],
                gathered_cids,
                _collect_verbatim_excerpts(clean_txt),
                None,
                usage,
                tool_calls,
            )
        return ("NO_DATA", ["Không trích xuất được dữ kiện từ đoạn tài liệu."], [], [], None, usage, tool_calls)

    raw_status = str(parsed.get("status") or "PARTIAL").strip().upper()
    status: VerdictStatus = (
        raw_status if raw_status in ("CONFIRMED", "REFUTED", "PARTIAL", "NO_DATA") else "PARTIAL"  # type: ignore[assignment]
    )
    findings = [str(f).strip() for f in (parsed.get("findings") or ()) if isinstance(f, str) and f.strip()]
    followup = str(parsed.get("anomaly_followup_query") or "").strip() or None
    if not findings and status != "NO_DATA":
        status = "NO_DATA"
        findings = ["Tài liệu truy xuất không chứa chi tiết thuyết minh khớp trực tiếp với câu hỏi."]
    verbatim_quotes = _collect_verbatim_excerpts("\n".join(findings)) if status != "NO_DATA" else []
    _trace_reader(
        outcome="OK",
        user_prompt=user_msg,
        raw_output=raw_json,
        usage=usage_to_dict(usage),
        model_status=raw_status,
        status=status,
        findings=findings[:7],
        anomaly_followup_query=followup,
        gathered_citations=gathered_cids,
    )
    return status, findings[:7], gathered_cids, verbatim_quotes, followup, usage, tool_calls


async def execute_brief_claims(
    *,
    brief: ResearchBrief,
    located: LocatedTargets,
    ctx: ResearchToolContext,
    model: ModelProvider,
    pulse_bundle: Any = None,
    dropped_by_claim_id: Mapping[str, Sequence[str]] | None = None,
    overall_deadline_mono: float | None = None,
    exec_deadline_mono: float | None = None,
    partial_sink: BriefExecutionResult | None = None,
    on_stage: Callable[[str, str, str], Awaitable[None]] | None = None,
    on_thought: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_call: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    trace: TraceSink | None = None,
) -> BriefExecutionResult:
    """Components (4) & (5): Premise-first + Parallel Per-Claim Execution + Code-Owned 1-Hop Escalation."""
    tickers = located.analysis_tickers
    pulse_cache_holder: dict[str, tuple[list[str], list[str]]] = {}
    quant_anomaly_by_claim: dict[str, str] = {}
    fs: FactSet | None = None
    if ctx.core_reader is not None and tickers:
        try:
            fs = await _fact_set(ctx, tickers)
        except Exception:  # noqa: BLE001
            fs = None

    # Deterministic 0-Token Business-Driver, Segment-Margin, Note-Anatomy & Multi-Slot Strategic Sweep:
    # Unlocks `sag_extracted_tables` (`4. Báo cáo bộ phận`, `6. Đầu tư tài chính / Liên kết`) + 4Q trajectory
    # + `BCTN_CORE` / `AGM_TARGETS` strategic drivers automatically on company analysis turns!
    driver_lines_by_claim: dict[str, list[str]] = {}
    driver_badges_by_claim: dict[str, list[str]] = {}
    auto_strategic_doc_claim_id: str | None = None
    is_broad_company_analysis = (
        bool(brief.claims)
        and len(tickers) <= 3
        and brief.entities.candidate_search is None
        and brief.answer_shape
        in {
            "health_check",
            "verdict_with_reasons",
            "premise_audit_and_explanation",
            "peer_head_to_head",
        }
    )
    if is_broad_company_analysis:
        from sag_api.research.chat_orchestrator import SQLAlchemyTableStore

        primary_driver_claim = next(
            (
                cl
                for cl in brief.claims
                if any(
                    k in ev
                    for ev in cl.evidence
                    for k in ("revenue", "top_line", "npat", "roe", "gross_margin", "doc:")
                )
            ),
            brief.claims[0],
        )
        bs_driver_claim = next(
            (
                cl
                for cl in brief.claims
                if any(
                    k in ev
                    for ev in cl.evidence
                    for k in (
                        "st_investments",
                        "cash",
                        "borrowings",
                        "debt",
                        "total_assets",
                        "current_ratio",
                        "equity",
                    )
                )
            ),
            primary_driver_claim,
        )

        # Check if any doc-bearing claim already searches BCTN_CORE or AGM_TARGETS; if not, complement that doc claim!
        doc_bearing_claims = [cl for cl in brief.claims if any(ev.startswith("doc:") for ev in cl.evidence)]
        has_bctn_or_agm_doc = any(
            ev.startswith("doc:") and ("BCTN_CORE" in ev or "AGM_TARGETS" in ev)
            for cl in doc_bearing_claims
            for ev in cl.evidence
        )
        if (
            doc_bearing_claims
            and not has_bctn_or_agm_doc
            and any(not ctx.available_tickers or t in ctx.available_tickers for t in tickers[:2])
        ):
            auto_strategic_doc_claim_id = doc_bearing_claims[0].id

        # 0) Automatic 3-Layer Relationship Graph Sweep (`0` LLM tokens) when no claim explicitly called `kg`!
        has_explicit_kg_spec = any(
            ev == "kg" or ev.startswith("kg:")
            for cl in [*brief.premises_to_check, *brief.claims]
            for ev in cl.evidence
        )
        if ctx.enable_kg and tickers and not has_explicit_kg_spec:
            eco_claim = next(
                (
                    cl
                    for cl in brief.claims
                    if any(
                        kw in cl.statement.lower()
                        for kw in (
                            "công ty con",
                            "liên kết",
                            "hợp nhất",
                            "hệ sinh thái",
                            "quan hệ",
                            "đối thủ",
                            "bảo hiểm",
                            "chứng khoán",
                            "rủi ro",
                        )
                    )
                ),
                brief.claims[-1],
            )

            async def _noop_auto_kg(_p: Any) -> None:
                return None

            auto_kg_cid = f"kg-auto-{eco_claim.id}"
            tctx_kg = ToolContext(
                run_id="brief-auto-kg",
                tool_call_id=auto_kg_cid,
                data=ctx,
                cancellation=CancellationToken(),
                _on_progress=_noop_auto_kg,
            )
            if on_tool_call is not None:
                await on_tool_call(
                    {
                        "id": auto_kg_cid,
                        "call_id": auto_kg_cid,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "query_knowledge_graph",
                        "label": "Quét bản đồ quan hệ hệ sinh thái, đối thủ & vĩ mô 3 tầng",
                        "stage": "graph_hop",
                        "status": "running",
                        "tickers": list(tickers[:2]),
                        "args_preview": f"Mã {', '.join(tickers[:2])} · 3 tầng (Hệ sinh thái, Đối thủ, Vĩ mô)",
                    }
                )
            t_akg = time.perf_counter()
            try:
                kg_auto_res = await query_knowledge_graph_tool.execute(
                    {"tickers": list(tickers[:2]), "limit": 8},
                    tctx_kg,
                )
                auto_hops = list(kg_auto_res.details.get("kg_hops") or ())[:8]
                for ah in auto_hops:
                    b_tag = f"[KG:{ah.get('layer')}|{ah.get('verification_tier')}]"
                    qual_tail = (
                        " (ghi nhận độ nhạy tại thuyết minh rủi ro BCTC)"
                        if ah.get("note_channel") == "AUDITED_NOTE" and ah.get("beta") is None
                        else (f" (beta={ah['beta']:.2f})" if isinstance(ah.get("beta"), (int, float)) else "")
                    )
                    driver_lines_by_claim.setdefault(eco_claim.id, []).append(
                        f"{b_tag} {ah.get('src_label')} --({ah.get('relation_type')})--> "
                        f"{ah.get('dst_label')}{qual_tail}"
                    )
                    driver_badges_by_claim.setdefault(eco_claim.id, []).append(b_tag)
                if on_tool_result is not None:
                    await on_tool_result(
                        {
                            "id": auto_kg_cid,
                            "call_id": auto_kg_cid,
                            "type": "tool_call",
                            "turn": 1,
                            "tool": "query_knowledge_graph",
                            "label": "Quét bản đồ quan hệ hệ sinh thái, đối thủ & vĩ mô 3 tầng",
                            "stage": "graph_hop",
                            "status": "done",
                            "duration_ms": int((time.perf_counter() - t_akg) * 1000),
                            "returned_count": len(auto_hops),
                            "summary": f"Đã nạp {len(auto_hops)} mối liên hệ 3 tầng (Hệ sinh thái, Đối thủ, Vĩ mô)",
                            "kg_hops": auto_hops,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Auto 3-layer KG sweep skipped: %s", exc)

        for tk_drv in tickers[:2]:
            # 1) Multi-quarter trajectory from FactSet (0 SQL, 0 LLM)
            if fs is not None and tk_drv in fs.templates:
                traj = compute_multi_quarter_trajectory(fs, tk_drv)
                if traj is not None:
                    saved_tr = await ctx.evidence.register_calculator_trace(traj["calc_trace"])
                    tr_cid = str(saved_tr["calc_id"])
                    driver_lines_by_claim.setdefault(primary_driver_claim.id, []).append(
                        f"[{tr_cid}] {traj['summary_line']}"
                    )
                    driver_badges_by_claim.setdefault(primary_driver_claim.id, []).append(f"[{tr_cid}]")

            # 2) Segment & Balance-Sheet Note Breakdowns from `sag_extracted_tables` (1 PIT SQL query, 0 LLM)
            if ctx.available_tickers and tk_drv not in ctx.available_tickers and ctx.table_store_override is None:
                continue
            tk_tables: list[dict[str, Any]] = []
            try:
                if ctx.table_store_override is not None:
                    tk_tables = await ctx.table_store_override.get_tables(
                        table_ids=[],
                        tickers=[tk_drv],
                        as_of_date=ctx.as_of_date,
                        tenant_id=ctx.tenant_id,
                        limit=60,
                    )
                else:
                    async with ctx.session_factory() as tbl_sess:
                        if tbl_sess is not None:
                            t_store = SQLAlchemyTableStore(tbl_sess)
                            tk_tables = await t_store.get_tables(
                                table_ids=[],
                                tickers=[tk_drv],
                                as_of_date=ctx.as_of_date,
                                tenant_id=ctx.tenant_id,
                                limit=60,
                            )
            except Exception as exc:  # noqa: BLE001
                logger.debug("BusinessDriverEngine table load skipped for %s: %s", tk_drv, exc)
                tk_tables = []

            if tk_tables:
                seg_drivers = extract_segment_drivers(tk_tables, tk_drv, fs=fs)
                note_drivers = extract_balance_sheet_note_breakdowns(fs, tk_tables, tk_drv)
                used_tids = {d["table_id"] for d in [*seg_drivers, *note_drivers] if d.get("table_id")}
                if used_tids:
                    await ctx.evidence.register_tables(
                        [t for t in tk_tables if str(t.get("table_id") or "") in used_tids][:6]
                    )
                for sd in seg_drivers[:5]:
                    saved_sd = await ctx.evidence.register_calculator_trace(sd["calc_trace"])
                    sd_cid = str(saved_sd["calc_id"])
                    driver_lines_by_claim.setdefault(primary_driver_claim.id, []).append(
                        f"[{sd_cid}] {tk_drv} · {sd['summary_line']}"
                    )
                    driver_badges_by_claim.setdefault(primary_driver_claim.id, []).append(f"[{sd_cid}]")
                    bps_d = sd.get("margin_delta_bps")
                    if isinstance(bps_d, (int, float)) and abs(float(bps_d)) >= 150:
                        quant_anomaly_by_claim.setdefault(
                            primary_driver_claim.id,
                            f"nguyên nhân biến động biên lợi nhuận mảng {sd.get('segment_name') or ''} thảo luận ĐHĐCĐ",
                        )
                    elif sd.get("kind") == "segment_scope_shift":
                        quant_anomaly_by_claim.setdefault(
                            primary_driver_claim.id,
                            "phạm vi hợp nhất chuyển giao quyền kiểm soát công ty liên kết cổ tức ĐHĐCĐ",
                        )
                for nd in note_drivers[:4]:
                    saved_nd = await ctx.evidence.register_calculator_trace(nd["calc_trace"])
                    nd_cid = str(saved_nd["calc_id"])
                    target_cid = (
                        primary_driver_claim.id if nd.get("kind") == "associates_rollforward" else bs_driver_claim.id
                    )
                    driver_lines_by_claim.setdefault(target_cid, []).append(
                        f"[{nd_cid}] {tk_drv} · {nd['summary_line']}"
                    )
                    driver_badges_by_claim.setdefault(target_cid, []).append(f"[{nd_cid}]")
                    if nd.get("kind") == "associates_rollforward":
                        quant_anomaly_by_claim.setdefault(
                            target_cid,
                            "cấu trúc tập đoàn chuyển giao quyền kiểm soát công ty liên kết cổ tức ĐHĐCĐ",
                        )

    # Deterministic 0-Token Accounting / Growth Divergence Auto-Probe:
    # When an indexed ticker exhibits |rev_yoy| >= 15%, |npat_yoy| >= 15%, or opposite-sign revenue vs NPAT YoY
    # AND no claim already checks `QUARTER_NOTES` / `BCTC_AUDITED`, auto-attach a variance explanation doc probe!
    auto_variance_claim_id: str | None = None
    has_existing_doc_probe = any(
        ev.startswith("doc:") and ("QUARTER_NOTES" in ev or "BCTC_AUDITED" in ev)
        for cl in brief.claims
        for ev in cl.evidence
    )
    if not has_existing_doc_probe and fs is not None and brief.claims:
        indexed_tks = [t for t in tickers if not ctx.available_tickers or t in ctx.available_tickers]
        needs_variance_probe = False
        for tk_chk in indexed_tks[:2]:
            lq_chk = fs.latest_period(tk_chk, "Q")
            if lq_chk is None:
                continue
            p_chk = PeriodRef("Q", lq_chk[0], lq_chk[1])
            rev_yoy = compute_kpi(fs, tk_chk, "top_line_growth_yoy", p_chk)
            npat_yoy = compute_kpi(fs, tk_chk, "npat_growth_yoy", p_chk)
            rv = rev_yoy.value if rev_yoy.status is KpiStatus.OK else None
            nv = npat_yoy.value if npat_yoy.status is KpiStatus.OK else None
            if (
                (rv is not None and abs(rv) >= 0.15)
                or (nv is not None and abs(nv) >= 0.15)
                or (rv is not None and nv is not None and (rv * nv) < 0)
            ):
                needs_variance_probe = True
                break
        if needs_variance_probe:
            target_cl = next(
                (
                    cl
                    for cl in brief.claims
                    if any(
                        k in ev
                        for ev in cl.evidence
                        for k in ("revenue", "top_line", "npat", "operating_profit", "pbt")
                    )
                ),
                brief.claims[0],
            )
            auto_variance_claim_id = target_cl.id

    reader_sem = asyncio.Semaphore(MAX_MINI_READER_CONCURRENCY)
    total_usage = Usage()
    total_tool_calls = 0

    def _card_thought_payload(card: ClaimVerdictCard) -> dict[str, Any]:
        role_title = "Kiểm chứng giả định" if card.is_premise else "Kết luận kiểm chứng"
        esc_suffix = " · Đã đối chiếu sâu" if card.escalated else ""
        return {
            "id": f"card-{card.claim_id}",
            "type": "thought",
            "turn": 1,
            "kind": "verdict_card",
            "verdict_status": card.status,
            "is_premise": card.is_premise,
            "period": card.period,
            "claim_statement": card.statement,
            "badges": list(card.badges[:12]),
            "escalated": card.escalated,
            "status": "done",
            "title": f"{role_title}{esc_suffix}",
            "text": "\n".join(card.summary_lines[:6])[:1200],
        }

    async def _evaluate_one_claim(
        claim: ClaimSpec,
        *,
        is_premise: bool,
        refuted_premises: Mapping[str, str],
    ) -> tuple[ClaimVerdictCard, Usage, int]:
        quant_lines, badges, risk_followup = await _run_quant_specs_for_claim(
            claim=claim,
            tickers=tickers,
            fs=fs,
            located=located,
            ctx=ctx,
            pulse_bundle=pulse_bundle,
            pulse_cache_holder=pulse_cache_holder,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_stage=on_stage,
        )
        summary_lines = list(quant_lines)
        if claim.id in driver_lines_by_claim:
            summary_lines.extend(driver_lines_by_claim[claim.id])
            for db_tag in driver_badges_by_claim.get(claim.id, ()):
                if db_tag not in badges:
                    badges.append(db_tag)
        c_usage = Usage()
        c_tools = 0
        doc_followup: str | None = None

        refuted_deps = [pid for pid in claim.depends_on if pid in refuted_premises]
        if refuted_deps:
            summary_lines.insert(
                0,
                f"Lưu ý: Tiền đề {', '.join(refuted_deps)} đã bị bác bỏ bởi số liệu PIT; "
                "dưới đây là số liệu thực tế của doanh nghiệp để giải thích đúng bản chất.",
            )

        prem_status: VerdictStatus | None = None
        if is_premise:
            prem_status, prem_msg, prem_calc = _evaluate_premise_direction(claim, fs, tickers)
            if prem_calc is not None:
                saved_p = await ctx.evidence.register_calculator_trace(prem_calc)
                p_badge = f"[{saved_p['calc_id']}]"
                if p_badge not in badges:
                    badges.append(p_badge)
                if prem_msg:
                    prem_msg = f"{p_badge} {prem_msg}"
            if prem_msg:
                summary_lines.insert(0, prem_msg)

        doc_specs = [ev for ev in claim.evidence if ev.startswith("doc:")]
        if not is_premise and claim.id == auto_variance_claim_id and not doc_specs:
            doc_specs = [
                "doc:QUARTER_NOTES,BCTC_AUDITED|"
                "giải trình biến động doanh thu lợi nhuận cơ cấu hợp nhất công ty con liên kết bộ phận"
            ]
        if (
            not is_premise
            and doc_specs
            and claim.id == auto_strategic_doc_claim_id
            and len(doc_specs) < MAX_DOC_SPECS_PER_CLAIM
        ):
            doc_specs = [
                *doc_specs,
                "doc:BCTN_CORE,AGM_TARGETS|"
                "phân tích kết quả theo khối chuyển đổi số thị trường trọng điểm chiến lược nhà máy AI thảo luận ĐHĐCĐ",
            ]
        doc_status: VerdictStatus | None = None
        verbatim_quotes: list[str] = []
        # Skip speculative causal doc searches when a premise this claim depends on was already REFUTED
        if doc_specs and not refuted_deps:
            d_st, d_lines, d_badges, d_quotes, d_follow, d_usage, d_tools = await _run_doc_mini_reader(
                claim=claim,
                doc_specs=doc_specs,
                tickers=tickers,
                quant_context_lines=quant_lines,
                ctx=ctx,
                model=model,
                reader_sem=reader_sem,
                pulse_bundle=pulse_bundle,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                trace=trace,
            )
            doc_status = d_st
            verbatim_quotes.extend(d_quotes)
            summary_lines.extend(d_lines)
            for b in d_badges:
                if b not in badges:
                    badges.append(b)
            doc_followup = d_follow
            c_usage = c_usage.plus(d_usage)
            c_tools += d_tools

        if located.coverage_note and doc_specs:
            summary_lines.append(located.coverage_note)

        claim_dropped = list((dropped_by_claim_id or {}).get(claim.id) or ())
        if claim_dropped:
            summary_lines.insert(
                0,
                "Lưu ý giới hạn dữ liệu: một phần chỉ tiêu yêu cầu chưa có trực tiếp trong kho PIT ("
                + "; ".join(claim_dropped[:2])
                + ").",
            )

        substantive_quant_lines = [
            ln
            for ln in quant_lines
            if "Quét rủi ro tất định: không phát hiện cờ cảnh báo trọng yếu." not in ln
            and "chưa có dữ liệu PIT" not in ln
            and "thiếu dữ liệu (" not in ln
        ]
        has_substantive_quant = bool(substantive_quant_lines) or (
            bool(quant_lines) and not claim_dropped and claim.evidence == ["risk"]
        )

        # Resolve final deterministic card status
        is_pure_screen_premise = (
            is_premise
            and "screen" in claim.evidence
            and not any(ev.startswith(("fact:", "kpi:", "doc:")) for ev in claim.evidence)
        )
        is_directional_premise = is_premise and bool(
            _NEGATIVE_PREMISE_RE.search(claim.statement) or _POSITIVE_PREMISE_RE.search(claim.statement)
        )
        if is_pure_screen_premise and prem_status is None:
            if any(r.get("all_passed") for r in located.candidate_rows):
                final_status: VerdictStatus = "CONFIRMED"
            else:
                final_status = "REFUTED"
                summary_lines.insert(
                    0,
                    "BÁC BỎ TIỀN ĐỀ: không có ứng viên nào trong phạm vi định vị đạt đủ tiêu chí lọc.",
                )
        elif is_directional_premise and prem_status is None and doc_status not in ("CONFIRMED", "REFUTED"):
            # A directional premise is NEVER "confirmed" just because 1 period exists: it needs a 2-period check
            final_status = "NO_DATA"
            summary_lines.insert(
                0,
                "CHƯA KIỂM CHỨNG ĐƯỢC TIỀN ĐỀ: không đủ số liệu so sánh 2 kỳ liên tiếp trong phạm vi PIT; "
                "không được coi tiền đề là đúng.",
            )
        elif prem_status == "REFUTED":
            final_status = "REFUTED"
        elif doc_status == "REFUTED":
            final_status = "REFUTED"
        elif prem_status == "CONFIRMED":
            final_status = "CONFIRMED" if not claim_dropped else "PARTIAL"
        elif has_substantive_quant and (not doc_specs or doc_status in ("CONFIRMED", "PARTIAL")):
            final_status = "PARTIAL" if (doc_status == "PARTIAL" or claim_dropped) else "CONFIRMED"
        elif has_substantive_quant and doc_status == "NO_DATA":
            final_status = "PARTIAL"
        elif doc_status in ("CONFIRMED", "PARTIAL"):
            final_status = "PARTIAL" if claim_dropped else doc_status
        elif quant_lines and claim_dropped:
            final_status = "PARTIAL" if any(b.startswith("[") for b in badges) else "NO_DATA"
        elif quant_lines:
            final_status = "PARTIAL"
        else:
            final_status = "NO_DATA"

        covered_slots_now = {str(getattr(s, "sub_doc_slot", "") or "").upper() for s in ctx.evidence.sections}
        auto_anom_q = quant_anomaly_by_claim.get(claim.id)
        if auto_anom_q and ("AGM_TARGETS" in covered_slots_now and "BCTC_AUDITED" in covered_slots_now):
            auto_anom_q = None
        esc_q = doc_followup or auto_anom_q or (risk_followup if not doc_specs else None)
        card = ClaimVerdictCard(
            claim_id=claim.id,
            is_premise=is_premise,
            statement=claim.statement,
            period=claim.period,
            status=final_status,
            summary_lines=summary_lines,
            badges=badges,
            verbatim_quotes=verbatim_quotes,
            escalation_query=esc_q,
        )
        if on_thought is not None:
            await on_thought(_card_thought_payload(card))
        if partial_sink is not None:
            if is_premise:
                partial_sink.premise_cards.append(card)
            else:
                partial_sink.claim_cards.append(card)
            partial_sink.usage = partial_sink.usage.plus(c_usage)
            partial_sink.tool_calls_count += c_tools
        return card, c_usage, c_tools

    # 1) Smart Wave-1 Parallelization:
    #    Only directional premises (`_NEGATIVE_PREMISE_RE` | `_POSITIVE_PREMISE_RE`) can ever evaluate to
    #    `REFUTED` in `_evaluate_premise_direction()`. Descriptive non-directional premises cannot refute
    #    downstream claims, so claims depending solely on non-directional premises run concurrently in Wave 1!
    premise_cards: list[ClaimVerdictCard] = []
    refuted_map: dict[str, str] = {}
    falsifiable_pids = {
        p.id
        for p in brief.premises_to_check
        if _NEGATIVE_PREMISE_RE.search(p.statement) or _POSITIVE_PREMISE_RE.search(p.statement)
    }
    indep_claims = [c for c in brief.claims if not (set(c.depends_on) & falsifiable_pids)]
    dep_claims = [c for c in brief.claims if set(c.depends_on) & falsifiable_pids]
    cards_by_cid: dict[str, ClaimVerdictCard] = {}

    if brief.premises_to_check or indep_claims:
        wave1_tasks = [
            *(_evaluate_one_claim(p, is_premise=True, refuted_premises={}) for p in brief.premises_to_check),
            *(_evaluate_one_claim(c, is_premise=False, refuted_premises={}) for c in indep_claims),
        ]
        wave1_outcomes = await asyncio.gather(*wave1_tasks)
        n_prem = len(brief.premises_to_check)
        for p_card, p_usage, p_tools in wave1_outcomes[:n_prem]:
            premise_cards.append(p_card)
            total_usage = total_usage.plus(p_usage)
            total_tool_calls += p_tools
            if p_card.status == "REFUTED":
                refuted_map[p_card.claim_id] = p_card.summary_lines[0] if p_card.summary_lines else "Bác bỏ"
        for c_card, c_usage, c_tools in wave1_outcomes[n_prem:]:
            cards_by_cid[c_card.claim_id] = c_card
            total_usage = total_usage.plus(c_usage)
            total_tool_calls += c_tools

    if dep_claims:
        dep_outcomes = await asyncio.gather(
            *(_evaluate_one_claim(c, is_premise=False, refuted_premises=refuted_map) for c in dep_claims)
        )
        for c_card, c_usage, c_tools in dep_outcomes:
            cards_by_cid[c_card.claim_id] = c_card
            total_usage = total_usage.plus(c_usage)
            total_tool_calls += c_tools

    claim_cards: list[ClaimVerdictCard] = [cards_by_cid[c.id] for c in brief.claims if c.id in cards_by_cid]

    # 3) Code-Controlled Selective 1-Hop Escalation (HARD CAP: max 1 round, max 2 claims, >=38s overall & >=28s exec)
    escalations_run = 0
    now_mono = asyncio.get_running_loop().time()
    remaining_s = (overall_deadline_mono - now_mono) if overall_deadline_mono is not None else 60.0
    remaining_exec_s = (exec_deadline_mono - now_mono) if exec_deadline_mono is not None else 60.0
    escalation_candidates = [
        c
        for c in claim_cards
        if c.escalation_query
        and c.status != "REFUTED"
        and len(tickers) <= 2
        and brief.answer_shape not in ("sector_pulse", "market_outlook")
        and any(not ctx.available_tickers or t in ctx.available_tickers for t in tickers)
    ][:MAX_ESCALATION_CLAIMS]
    will_escalate = bool(escalation_candidates) and remaining_s >= 38.0 and remaining_exec_s >= 28.0
    emit_trace(
        trace,
        "escalation.decision",
        requested=[{"claim_id": c.claim_id, "query": c.escalation_query} for c in claim_cards if c.escalation_query],
        selected=[c.claim_id for c in escalation_candidates],
        remaining_seconds=round(remaining_s, 1),
        ran=will_escalate,
    )

    if will_escalate:
        if on_thought is not None:
            for c_esc in escalation_candidates:
                await on_thought(
                    {
                        "id": f"escalate-{c_esc.claim_id}",
                        "type": "thought",
                        "turn": 2,
                        "kind": "escalation",
                        "status": "done",
                        "title": "Đào sâu thuyết minh bổ sung",
                        "text": (
                            f"Phát hiện chi tiết cần đối chiếu thêm ({c_esc.escalation_query}) "
                            "— tra cứu thuyết minh bổ sung."
                        ),
                    }
                )

        async def _run_followup(card: ClaimVerdictCard) -> tuple[list[str], list[str], list[str], Usage, int]:
            assert card.escalation_query is not None
            probe_claim = ClaimSpec(
                id=f"{card.claim_id}-hop2",
                statement=f"Đào sâu nguyên nhân nhân quả & giải trình ĐHĐCĐ cho {card.claim_id}: {card.statement}",
                evidence=[f"doc:AGM_TARGETS,BCTC_AUDITED,BCTN_CORE,QUARTER_NOTES|{card.escalation_query}"],
                period=card.period,
            )
            _, f_lines, f_badges, f_quotes, _, f_usage, f_tools = await _run_doc_mini_reader(
                claim=probe_claim,
                doc_specs=probe_claim.evidence,
                tickers=tickers,
                quant_context_lines=card.summary_lines[:2],
                ctx=ctx,
                model=model,
                reader_sem=reader_sem,
                pulse_bundle=pulse_bundle,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                trace=trace,
            )
            return f_lines, f_badges, f_quotes, f_usage, f_tools

        hop2_outcomes = await asyncio.gather(*(_run_followup(c) for c in escalation_candidates))
        for card, (f_lines, f_badges, f_quotes, f_usage, f_tools) in zip(
            escalation_candidates, hop2_outcomes, strict=True
        ):
            total_usage = total_usage.plus(f_usage)
            total_tool_calls += f_tools
            if f_lines and f_badges:
                escalations_run += 1
                card.escalated = True
                card.summary_lines.extend(f"[Đào sâu thuyết minh] {ln}" for ln in f_lines[:3])
                for q_item in f_quotes:
                    if q_item not in card.verbatim_quotes:
                        card.verbatim_quotes.append(q_item)
                for b in f_badges:
                    if b not in card.badges:
                        card.badges.append(b)
                if card.status == "NO_DATA":
                    card.status = "PARTIAL"
                if on_thought is not None:
                    await on_thought(_card_thought_payload(card))

    # Final full verdict cards = exact synthesis input (never truncated in the trace)
    spec_by_id = {s.id: s for s in [*brief.premises_to_check, *brief.claims]}
    claim_ids = {c.id for c in brief.claims}
    for card in [*premise_cards, *claim_cards]:
        spec = spec_by_id.get(card.claim_id)
        deps = list(spec.depends_on) if spec is not None else []
        emit_trace(
            trace,
            "claim.card",
            claim_id=card.claim_id,
            is_premise=card.is_premise,
            statement=card.statement,
            evidence_specs=list(spec.evidence) if spec is not None else [],
            depends_on=deps,
            # Claims run in parallel: only premise dependencies are enforced, claim->claim ones are not
            unenforced_claim_dependencies=[d for d in deps if d in claim_ids],
            status=card.status,
            escalated=card.escalated,
            escalation_query=card.escalation_query,
            badges=list(card.badges),
            summary_lines=list(card.summary_lines),
            rendered_for_synthesis=card.render_for_synthesis(),
        )

    return BriefExecutionResult(
        located=located,
        premise_cards=premise_cards,
        claim_cards=claim_cards,
        usage=total_usage,
        tool_calls_count=total_tool_calls,
        escalations_run=escalations_run,
    )
