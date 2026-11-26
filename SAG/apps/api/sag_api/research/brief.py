"""Brief-Centric Research Planner & Deterministic Validator (`brief.py`).

Implements Components (1) and (2) of the SAG Brief-Centric Brain:
1. `create_research_brief`: 1 compact LLM call converting any investor query into a falsifiable
   `ResearchBrief` (concern, answer shape, target/anchor/candidate entities, premises to check
   first, atomic claims with typed evidence requirements, and ambiguity detection).
2. `validate_research_brief`: 0-LLM-token deterministic validator enforcing JSON schema, ACL,
   PIT period bounds (`<= as_of_date`), canonical `FactSet` / `KPI` / disclosure slot vocabulary,
   claim count budgets, anchor-vs-candidate invariants, and clarification gating.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sag_agent.types import AgentMessage, CancellationToken, ModelProvider, ModelRequest, Usage
from sag_api.research.analytics.kpis import KPI_BY_ID
from sag_api.research.facts.catalog import CATALOG_BY_ID, resolve_template
from sag_api.research.tools.research_tools import CANONICAL_DISCLOSURE_SLOTS, normalize_disclosure_slots

BudgetTier = Literal["fast", "standard", "deep"]
CriterionOp = Literal[">=", "<=", ">", "<", "=="]
CriterionRef = Literal["literal", "anchor"]

CLAIM_BUDGET_BY_TIER: dict[str, int] = {
    "fast": 3,
    "standard": 5,
    "deep": 6,
}
MAX_PREMISES = 2

# Composite metrics computable deterministically in `locate_candidates` on top of FactSet + market_cap
COMPOSITE_CANDIDATE_METRICS: frozenset[str] = frozenset(
    {
        "market_cap",
        "pe",
        "pb",
        "ev_ebitda",
        "fcf_yield",
        "cash_to_market_cap",
        "net_cash_to_market_cap",
        "net_cash",
        "total_debt",
        "net_debt",
        "revenue_yoy",
        "net_income_yoy",
        "cfo_to_net_income",
        "liabilities_to_equity",
    }
)

_FACT_SYNONYMS: dict[str, str] = {
    "revenue": "revenue_net",
    "net_revenue": "revenue_net",
    "sales": "revenue_net",
    "net_income": "npat",
    "net_profit": "npat",
    "profit_after_tax": "npat",
    "lnst": "npat",
    "lntt": "pbt",
    "profit_before_tax": "pbt",
    "ebt": "pbt",
    "assets": "total_assets",
    "liabilities": "total_liabilities",
    "cash": "cash_and_equivalents",
    "short_term_investments": "st_investments",
    "short_term_receivables": "st_receivables",
    "receivables": "st_receivables",
    "inventories": "inventory",
    "st_debt": "st_borrowings",
    "short_term_debt": "st_borrowings",
    "short_term_borrowings": "st_borrowings",
    "lt_debt": "lt_borrowings",
    "long_term_debt": "lt_borrowings",
    "long_term_borrowings": "lt_borrowings",
    "customer_advances": "advances_from_customers",
    "cip": "construction_in_progress",
    "loans": "customer_loans_gross",
    "customer_loans": "customer_loans_gross",
    "deposits": "customer_deposits",
    "provisions": "credit_provision_expense",
    "llr_balance": "loan_loss_reserve",
    "nim": "vr_nim",
    "npl": "vr_npl_ratio",
    "npl_ratio": "vr_npl_ratio",
    "llr": "vr_npl_coverage",
    "npl_coverage": "vr_npl_coverage",
    "casa": "vr_casa_ratio",
    "casa_ratio": "vr_casa_ratio",
    "cof": "vr_cost_of_funds",
    "ldr": "vr_ldr",
    "pe": "vr_pe",
    "pb": "vr_pb",
    "ev_ebitda": "vr_ev_ebitda",
    "market_cap": "vr_market_cap",
    "eps": "eps_basic",
    "eps_ttm": "vr_eps_ttm",
    "bvps": "vr_bvps",
}

_KPI_SYNONYMS: dict[str, str] = {
    "revenue_growth": "top_line_growth_yoy",
    "revenue_yoy": "top_line_growth_yoy",
    "toi_growth": "top_line_growth_yoy",
    "net_income_yoy": "npat_growth_yoy",
    "npat_yoy": "npat_growth_yoy",
    "profit_growth": "npat_growth_yoy",
    "earnings_growth": "npat_growth_yoy",
    "gpm": "gross_margin",
    "npm": "net_margin",
    "de": "debt_to_equity",
    "d_e": "debt_to_equity",
    "net_de": "net_debt_to_equity",
    "cfo_npat": "cfo_to_npat",
    "cfo_to_net_income": "cfo_to_npat",
    "cash_conversion": "cfo_to_npat",
    "nim": "nii_to_avg_assets",
    "nim_proxy": "nii_to_avg_assets",
    "ldr": "loan_to_deposit",
    "loan_growth": "loan_growth_yoy",
    "credit_growth": "loan_growth_yoy",
    "deposit_growth": "deposit_growth_yoy",
    "llr_to_loans": "reserve_to_loans",
}

_METRIC_SYNONYMS: dict[str, str] = {
    **_FACT_SYNONYMS,
    "npl": "vr_npl_ratio",
    "npl_ratio": "vr_npl_ratio",
    "npl_coverage": "vr_npl_coverage",
    "llr": "vr_npl_coverage",
    "casa": "vr_casa_ratio",
    "nim": "vr_nim",
    "net_cash_market_cap": "net_cash_to_market_cap",
    "net_cash_to_mcap": "net_cash_to_market_cap",
    "cash_to_mcap": "cash_to_market_cap",
    "cash_market_cap": "cash_to_market_cap",
    "profit_growth": "npat_growth_yoy",
    "earnings_growth": "npat_growth_yoy",
    "net_income_growth": "npat_growth_yoy",
    "revenue_growth": "top_line_growth_yoy",
}

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TICKER_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,4})\b")
_PERIOD_Q_RE = re.compile(r"(?:Q\s*([1-4])\s*[/\- ]?\s*(20\d{2})|(20\d{2})\s*[/\- ]?\s*Q\s*([1-4]))", re.IGNORECASE)
_PERIOD_FY_RE = re.compile(r"(?:FY\s*|NĂM\s*)?(20\d{2})", re.IGNORECASE)

BRIEF_STATIC_SYSTEM_PROMPT = (
    "Bạn là Chuyên gia Trưởng Lập Đề Bài Phân Tích Đầu Tư (Chief Research Brief Architect) của AIInvest.\n"
    "Nhiệm vụ DUY NHẤT của bạn: đọc câu hỏi của nhà đầu tư và xuất ra ĐÚNG MỘT JSON 'Đề bài nghiên cứu' "
    "(KHÔNG giải thích ngoài JSON, KHÔNG tự trả lời câu hỏi, KHÔNG nhẩm tính số liệu).\n\n"
    "NGUYÊN TẮC CỐT LÕI:\n"
    "1. Hiểu đúng nỗi lo / mục đích thật sự (`user_concern`) và hình dáng câu trả lời phù hợp nhất "
    '(`answer_shape`: "direct_fact" | "verdict_with_reasons" | "health_check" | "candidate_ranking" | '
    '"premise_audit_and_explanation" | "peer_head_to_head" | "clarification_needed").\n'
    "2. Kiểm tra giả định trước khi giải thích (`premises_to_check`):\n"
    "   - NẾU câu hỏi cài sẵn một tiền đề chưa chắc đúng ('Tại sao NIM ACB sập trong Q3?', 'Vì sao FPT lỗ?', "
    "'Tại sao VHM cắt cổ tức?'), BẮT BUỘC đưa tiền đề đó vào `premises_to_check` (ví dụ P1: kiểm tra xem NIM ACB Q3 "
    "có thực sự giảm mạnh không) TRƯỚC các ý tìm nguyên nhân trong `claims` (và đặt `\"depends_on\": [\"P1\"]` "
    "cho các ý giải thích nguyên nhân).\n"
    "   - Nếu câu hỏi không cài tiền đề, để `\"premises_to_check\": []`.\n"
    "3. Định vị đối tượng (`entities`):\n"
    "   - Câu hỏi về mã cụ thể ('FRT...', 'VHM quý này ổn không?'): điền `\"tickers\": [\"FRT\"]`, "
    "`\"anchor_ticker\": null`, `\"candidate_search\": null`.\n"
    "   - Câu hỏi lấy 1 mã làm chuẩn để tìm mã khác ('Tìm ngân hàng an toàn như ACB mà tăng trưởng tốt hơn', "
    "'Cổ phiếu nào giống FPT nhưng rẻ hơn?'): BẮT BUỘC đặt `\"anchor_ticker\": \"ACB\"`, `\"tickers\": [\"ACB\"]`, "
    "VÀ điền `\"candidate_search\"` với các `criteria` so sánh với `\"ref\": \"anchor\"` (ví dụ an toàn ngân hàng: "
    "`vr_npl_ratio <= anchor`, `vr_npl_coverage >= anchor` hoặc `equity_to_assets >= anchor`; tăng trưởng tốt hơn: "
    "`npat_growth_yoy > anchor` hoặc `loan_growth_yoy > anchor`) — TUYỆT ĐỐI KHÔNG chỉ phân tích mỗi mã chuẩn!\n"
    "   - Câu hỏi tìm cổ phiếu toàn sàn không nêu mã ('Cổ phiếu nào trên sàn đang rẻ so với tiền mặt?', "
    "'Doanh nghiệp thép nào biên gộp cao nhất?'): đặt `\"tickers\": []`, `\"anchor_ticker\": null`, và điền "
    "`\"candidate_search\"` với `criteria` định lượng (ví dụ rẻ so với tiền mặt: `net_cash_to_market_cap >= 0.25` "
    "hoặc `cash_to_market_cap >= 0.30`, `sort_by: \"net_cash_to_market_cap\"`, `sort_dir: \"desc\"`).\n"
    "4. Xử lý mơ hồ (`ambiguities` & `needs_clarification`):\n"
    "   - CHỈ đặt `\"needs_clarification\": true` (kèm 1 câu hỏi gọn ở `clarifying_question`) khi câu hỏi KHÔNG có "
    "mã cổ phiếu, KHÔNG có ngành, KHÔNG có góc nhìn/tiêu chí tài chính nào, và KHÔNG có tiêu chí cá nhân trong bộ nhớ "
    "(ví dụ: 'Cổ phiếu nào tốt?', 'Mua mã gì bây giờ?').\n"
    "   - Nếu ĐÃ có mã cụ thể ('VHM quý này ổn không?') hoặc ĐÃ có góc nhìn ('rẻ so với tiền mặt', "
    "'an toàn như ACB mà tăng trưởng tốt hơn'), TUYỆT ĐỐI đặt `\"needs_clarification\": false` — tự ghi giả định hợp "
    "lý vào `assumptions` và lập các `claims` kiểm tra toàn diện (lợi nhuận lõi, dòng tiền CFO/LNST, nợ vay ngắn hạn/"
    "thanh khoản, thuyết minh trọng yếu).\n"
    "5. Từ điển bằng chứng hợp lệ trong `evidence` của mỗi ý (1..4 mục mỗi ý, ưu tiên `fact:*`/`kpi:*`/`risk` "
    "0-token trước, chỉ dùng `doc:*` khi thực sự cần đọc thuyết minh/BCTN/ĐHĐCĐ):\n"
    "   - `fact:<id>`: `revenue_net`, `gross_profit`, `operating_profit`, `pbt`, `npat`, `npat_parent`, `cfo`, "
    "`capex`, `total_assets`, `equity`, `cash_and_equivalents`, `st_investments`, `inventory`, `st_receivables`, "
    "`st_borrowings`, `lt_borrowings`, `advances_from_customers`, `interest_expense`; Ngân hàng: `nii`, "
    "`net_fee_income`, `toi`, `opex`, `ppop`, `credit_provision_expense`, `customer_loans_gross`, `loan_loss_reserve`, "
    "`customer_deposits`, `vr_nim`, `vr_npl_ratio`, `vr_npl_coverage`, `vr_casa_ratio`, `vr_ldr`, `vr_cir`; "
    "Chứng khoán: `brokerage_revenue`, `margin_lending_income`, `fvtpl_gains`, `fvtpl_losses`, `realised_profit`, "
    "`unrealised_profit`, `margin_loans`, `fvtpl_assets`, `afs_assets`, `htm_assets`; Bảo hiểm: "
    "`gross_written_premium`, `net_premium`, `insurance_claims`, `technical_reserves`; Định giá: `vr_pe`, `vr_pb`, "
    "`vr_ev_ebitda`, `vr_market_cap`, `vr_roe`, `vr_roa`.\n"
    "   - `kpi:<id>`: `top_line_growth_yoy`, `npat_growth_yoy`, `roe`, `roa`, `gross_margin`, `net_margin`, "
    "`equity_to_assets`, `cfo_to_npat`, `fcf`, `accruals_ratio`, `debt_to_equity`, `net_debt_to_equity`, "
    "`current_ratio`, `st_debt_to_liquid_assets`, `interest_coverage`, `inventory_days`, `receivable_days`, "
    "`inventory_growth_yoy`, `receivables_growth_yoy`, `nii_to_avg_assets`, `cir`, `credit_cost`, `provision_to_ppop`, "
    "`loan_to_deposit`, `reserve_to_loans`, `loan_growth_yoy`, `deposit_growth_yoy`, `margin_to_equity`, "
    "`fvtpl_to_equity`, `unrealised_profit_share`, `claims_ratio`.\n"
    "   - Công cụ tất định khác: `risk` (quét cờ đỏ & chất lượng lợi nhuận), `peers` (xếp hạng cùng ngành), "
    "`agm` (kế hoạch ĐHĐCĐ vs thực hiện), `kg` (đồ thị vĩ mô/chuỗi giá trị/đối thủ), `screen` (sàng lọc), "
    "`calc` (tính tỷ lệ bổ sung), `mem` (ký ức người dùng).\n"
    "   - Đọc thuyết minh/tài liệu: `doc:<SLOT>|<cụm từ khóa VAS hoặc chủ đề cần tìm>`, với `<SLOT>` thuộc "
    "`QUARTER_NOTES`, `BCTC_AUDITED`, `BCTN_CORE`, `BCTN_PR`, `AGM_TARGETS` (ví dụ: "
    "`\"doc:QUARTER_NOTES|vay ngắn hạn lãi suất mục đích vay\"`, "
    "`\"doc:BCTC_AUDITED|giao dịch bên liên quan phải thu\"`).\n\n"
    "CẤU TRÚC JSON BẮT BUỘC:\n"
    "{\n"
    '  "user_concern": "chuỗi 1 câu ngắn gọn",\n'
    '  "answer_shape": "verdict_with_reasons",\n'
    '  "entities": {"tickers": ["MÃ"], "anchor_ticker": null, "candidate_search": null},\n'
    '  "premises_to_check": [\n'
    '    {"id": "P1", "statement": "...", "evidence": ["fact:...", "kpi:..."], "period": "Q3/2026", "depends_on": []}\n'
    "  ],\n"
    '  "claims": [\n'
    '    {"id": "K1", "statement": "...", "evidence": ["fact:...", "risk"], "period": "Q3/2026", "depends_on": []}\n'
    "  ],\n"
    '  "ambiguities": [],\n'
    '  "assumptions": [],\n'
    '  "needs_clarification": false,\n'
    '  "clarifying_question": null,\n'
    '  "budget_tier": "standard"\n'
    "}\n"
    "Nếu có `candidate_search`, dùng đúng cấu trúc:\n"
    '"candidate_search": {\n'
    '  "sectors": ["Ngân hàng"],\n'
    '  "template": "BANK",\n'
    '  "criteria": [\n'
    '    {"metric": "vr_npl_ratio", "op": "<=", "value": null, "ref": "anchor", "tolerance": 0.004},\n'
    '    {"metric": "npat_growth_yoy", "op": ">", "value": null, "ref": "anchor", "tolerance": 0.0}\n'
    "  ],\n"
    '  "sort_by": "npat_growth_yoy",\n'
    '  "sort_dir": "desc",\n'
    '  "qualitative_query": null,\n'
    '  "limit": 4\n'
    "}\n"
    "(Lưu ý: nếu lọc theo ngưỡng số cố định thì đặt `\"ref\": \"literal\"` và `\"value\": 0.25` dạng thập phân cho %)."
)


@dataclass
class CandidateCriterion:
    metric: str
    op: CriterionOp
    value: float | None = None
    ref: CriterionRef = "literal"
    tolerance: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "op": self.op,
            "value": self.value,
            "ref": self.ref,
            "tolerance": self.tolerance,
        }


@dataclass
class CandidateSearchSpec:
    sectors: list[str] = field(default_factory=list)
    template: str | None = None
    criteria: list[CandidateCriterion] = field(default_factory=list)
    sort_by: str | None = None
    sort_dir: Literal["desc", "asc"] = "desc"
    qualitative_query: str | None = None
    limit: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "sectors": list(self.sectors),
            "template": self.template,
            "criteria": [c.to_dict() for c in self.criteria],
            "sort_by": self.sort_by,
            "sort_dir": self.sort_dir,
            "qualitative_query": self.qualitative_query,
            "limit": self.limit,
        }


@dataclass
class BriefEntities:
    tickers: list[str] = field(default_factory=list)
    anchor_ticker: str | None = None
    candidate_search: CandidateSearchSpec | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tickers": list(self.tickers),
            "anchor_ticker": self.anchor_ticker,
            "candidate_search": self.candidate_search.to_dict() if self.candidate_search else None,
        }


@dataclass
class ClaimSpec:
    id: str
    statement: str
    evidence: list[str] = field(default_factory=list)
    period: str | None = None
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "evidence": list(self.evidence),
            "period": self.period,
            "depends_on": list(self.depends_on),
        }


@dataclass
class ResearchBrief:
    user_concern: str
    answer_shape: str
    entities: BriefEntities
    premises_to_check: list[ClaimSpec] = field(default_factory=list)
    claims: list[ClaimSpec] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    clarifying_question: str | None = None
    budget_tier: BudgetTier = "standard"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_concern": self.user_concern,
            "answer_shape": self.answer_shape,
            "entities": self.entities.to_dict(),
            "premises_to_check": [p.to_dict() for p in self.premises_to_check],
            "claims": [c.to_dict() for c in self.claims],
            "ambiguities": list(self.ambiguities),
            "assumptions": list(self.assumptions),
            "needs_clarification": self.needs_clarification,
            "clarifying_question": self.clarifying_question,
            "budget_tier": self.budget_tier,
        }


@dataclass
class BriefValidationOutcome:
    ok: bool
    brief: ResearchBrief | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    denied_tickers: list[str] = field(default_factory=list)
    unknown_tickers: list[str] = field(default_factory=list)
    pit_adjustments: list[str] = field(default_factory=list)


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    cleaned = _THINK_RE.sub("", raw_text or "").strip()
    close_idx = cleaned.lower().find("</think>")
    if close_idx != -1:
        cleaned = cleaned[close_idx + len("</think>") :].strip()
    cleaned = _CODE_FENCE_RE.sub("", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        parsed = json.loads(cleaned[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Đầu ra không phải JSON object hợp lệ.")


def latest_closed_quarter_for_pit(as_of_date: date) -> tuple[int, int]:
    """Return `(fiscal_year, fiscal_quarter)` of the latest quarter whose period_end <= `as_of_date`."""
    y = as_of_date.year
    if as_of_date >= date(y, 12, 31):
        return (y, 4)
    if as_of_date >= date(y, 9, 30):
        return (y, 3)
    if as_of_date >= date(y, 6, 30):
        return (y, 2)
    if as_of_date >= date(y, 3, 31):
        return (y, 1)
    return (y - 1, 4)


def clamp_period_to_pit(raw_period: str | None, as_of_date: date) -> tuple[str | None, str | None]:
    """Clamp requested fiscal period so its period_end never exceeds `as_of_date`.

    Returns `(normalized_period, adjustment_warning_or_None)`.
    """
    if not raw_period:
        return None, None
    txt = str(raw_period).strip()
    if not txt or txt.upper() in {"LATEST", "CURRENT", "HIỆN TẠI", "GẦN NHẤT"}:
        return None, None
    upper = txt.upper()
    if upper.startswith("TTM"):
        q_match = _PERIOD_Q_RE.search(upper)
        if q_match:
            fq = int(q_match.group(1) or q_match.group(4))
            fy = int(q_match.group(2) or q_match.group(3))
            q_end = _quarter_end_date(fy, fq)
            if q_end > as_of_date:
                ly, lq = latest_closed_quarter_for_pit(as_of_date)
                clamped = f"TTM Q{lq}/{ly}"
                return clamped, f"Kỳ `{txt}` vượt PIT {as_of_date.isoformat()} -> hạ về `{clamped}`."
            return f"TTM Q{fq}/{fy}", None
        return "TTM", None

    q_match = _PERIOD_Q_RE.search(upper)
    if q_match:
        fq = int(q_match.group(1) or q_match.group(4))
        fy = int(q_match.group(2) or q_match.group(3))
        q_end = _quarter_end_date(fy, fq)
        if q_end > as_of_date:
            ly, lq = latest_closed_quarter_for_pit(as_of_date)
            clamped = f"Q{lq}/{ly}"
            return clamped, f"Kỳ `{txt}` vượt PIT {as_of_date.isoformat()} -> hạ về `{clamped}`."
        return f"Q{fq}/{fy}", None

    fy_match = _PERIOD_FY_RE.search(upper)
    if fy_match:
        fy = int(fy_match.group(1))
        fy_end = date(fy, 12, 31)
        if fy_end > as_of_date:
            # If user asked for full-year FY that has not ended yet, clamp to latest closed FY if explicitly annual,
            # or TTM if unspecified; keep explicit FY{last_closed_fy} so annual cash-flow/AGM logic stays clean.
            last_fy = as_of_date.year if as_of_date >= date(as_of_date.year, 12, 31) else as_of_date.year - 1
            clamped = f"FY{last_fy}"
            return clamped, f"Kỳ năm `{txt}` chưa kết thúc tại PIT {as_of_date.isoformat()} -> hạ về `{clamped}`."
        return f"FY{fy}", None

    return txt[:24], None


def _quarter_end_date(fy: int, fq: int) -> date:
    if fq == 1:
        return date(fy, 3, 31)
    if fq == 2:
        return date(fy, 6, 30)
    if fq == 3:
        return date(fy, 9, 30)
    return date(fy, 12, 31)


def canonicalize_metric_id(raw_metric: str) -> str | None:
    """Resolve metric alias into canonical `FactSet`, `KPI`, or composite candidate metric ID."""
    clean = str(raw_metric or "").strip().lower()
    if clean.startswith(("fact:", "kpi:")):
        clean = clean.split(":", 1)[1].strip()
    clean = re.sub(r"[^a-z0-9_]", "_", clean).strip("_")
    if not clean:
        return None
    mapped = _METRIC_SYNONYMS.get(clean, clean)
    if mapped in CATALOG_BY_ID or mapped in KPI_BY_ID or mapped in COMPOSITE_CANDIDATE_METRICS:
        return mapped
    mapped_kpi = _KPI_SYNONYMS.get(clean, clean)
    if mapped_kpi in KPI_BY_ID:
        return mapped_kpi
    return None


def canonicalize_evidence_spec(raw_ev: Any) -> tuple[str | None, str | None]:
    """Normalize a single evidence spec (`fact:*`, `kpi:*`, `doc:SLOT|query`, `risk`, `agm`, etc.).

    Returns `(canonical_spec_or_None, warning_or_None)`.
    """
    txt = str(raw_ev or "").strip()
    if not txt:
        return None, None
    low = txt.lower()

    prefix_head = low.split(":", 1)[0].strip() if ":" in low else low
    hint = txt.split(":", 1)[1].strip() if ":" in txt else ""

    if prefix_head in {"risk", "risks", "red_flags", "earnings_quality", "scan_financial_risks"}:
        return "risk", None
    if prefix_head in {"peers", "peer", "compare_peers"}:
        return "peers", None
    if prefix_head in {"agm", "plan", "plan_vs_actual", "check_plan_vs_actual"}:
        return "agm", None
    if prefix_head in {"kg", "graph", "macro", "value_chain", "query_knowledge_graph"}:
        return (f"kg:{hint[:80]}" if hint else "kg"), None
    if prefix_head in {"screen", "screener", "screen_financial_universe"}:
        return "screen", None
    if prefix_head in {"mem", "memory", "session_memory", "recall_session_memory"}:
        return "mem", None
    if prefix_head == "calc":
        return (f"calc:{hint[:120]}" if hint else "calc"), None
    if prefix_head == "table":
        stmt = hint.upper() if hint else "ALL"
        if stmt not in {"BS", "IS", "CF", "NOTES", "ALL"}:
            stmt = "ALL"
        return f"table:{stmt}", None

    if low.startswith("fact:"):
        raw_id = low.split(":", 1)[1].strip()
        mapped_fact = _METRIC_SYNONYMS.get(raw_id, _FACT_SYNONYMS.get(raw_id, raw_id))
        if mapped_fact in CATALOG_BY_ID:
            return f"fact:{mapped_fact}", None
        mapped_kpi = _KPI_SYNONYMS.get(raw_id, raw_id)
        if mapped_kpi in KPI_BY_ID:
            return f"kpi:{mapped_kpi}", None
        if mapped_fact in COMPOSITE_CANDIDATE_METRICS:
            return f"calc:{mapped_fact}", None
        return None, f"Chỉ tiêu `{txt}` không nằm trong danh mục chuẩn FactSet/KPI."

    if low.startswith("kpi:"):
        raw_id = low.split(":", 1)[1].strip()
        mapped_kpi = _KPI_SYNONYMS.get(raw_id, raw_id)
        if mapped_kpi in KPI_BY_ID:
            return f"kpi:{mapped_kpi}", None
        mapped_fact = _METRIC_SYNONYMS.get(raw_id, _FACT_SYNONYMS.get(raw_id, raw_id))
        if mapped_fact in CATALOG_BY_ID:
            return f"fact:{mapped_fact}", None
        if mapped_fact in COMPOSITE_CANDIDATE_METRICS:
            return f"calc:{mapped_fact}", None
        return None, f"KPI `{txt}` không nằm trong danh mục KPI chuẩn."

    if low.startswith("doc"):
        rest = txt.split(":", 1)[1].strip() if ":" in txt else ""
        if "|" in rest:
            slot_part, query_part = rest.split("|", 1)
        else:
            slot_part, query_part = "QUARTER_NOTES,BCTC_AUDITED", rest
        raw_slot_tokens = [s.strip() for s in slot_part.split(",") if s.strip()]
        valid_slots: list[str] = []
        for tok in raw_slot_tokens:
            if tok.upper() in {"ANY", "ALL", "*"}:
                valid_slots.extend(["QUARTER_NOTES", "BCTC_AUDITED"])
                continue
            try:
                valid_slots.extend(normalize_disclosure_slots([tok]))
            except ValueError:
                continue
        dedup_slots = list(dict.fromkeys(s for s in valid_slots if s in CANONICAL_DISCLOSURE_SLOTS))
        if not dedup_slots:
            dedup_slots = ["QUARTER_NOTES", "BCTC_AUDITED"]
        clean_q = re.sub(r"\s+", " ", query_part).strip() or "thuyết minh báo cáo tài chính"
        return f"doc:{','.join(dedup_slots)}|{clean_q[:160]}", None

    # Bare canonical fact or KPI name without prefix
    if low in CATALOG_BY_ID or low in _FACT_SYNONYMS:
        return f"fact:{_FACT_SYNONYMS.get(low, low)}", None
    if low in KPI_BY_ID or low in _KPI_SYNONYMS:
        return f"kpi:{_KPI_SYNONYMS.get(low, low)}", None

    return None, f"Loại bằng chứng `{txt}` không hợp lệ."


def validate_research_brief(
    raw: str | Mapping[str, Any],
    *,
    as_of_date: date,
    allowed_project_ids: frozenset[str] | set[str] = frozenset({"*"}),
    known_universe_tickers: frozenset[str] | set[str] | None = None,
    catalog_profiles: Sequence[Mapping[str, Any]] = (),
    chat_mode: str = "GENERAL",
    explicit_tickers: Sequence[str] = (),
) -> BriefValidationOutcome:
    """Deterministically validate, canonicalize, and PIT/ACL-clamp a `ResearchBrief` (0 LLM tokens)."""
    errors: list[str] = []
    warnings: list[str] = []
    denied_tickers: list[str] = []
    unknown_tickers: list[str] = []
    pit_adjustments: list[str] = []

    if isinstance(raw, str):
        try:
            payload = _extract_json_object(raw)
        except Exception as exc:  # noqa: BLE001
            return BriefValidationOutcome(ok=False, errors=[f"Lỗi cú pháp JSON đề bài: {exc}"])
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        return BriefValidationOutcome(ok=False, errors=["Đề bài phải là chuỗi JSON hoặc dict."])

    user_concern = str(payload.get("user_concern") or "").strip()
    if not user_concern:
        errors.append("Thiếu `user_concern` (mối quan tâm cốt lõi của người hỏi).")

    answer_shape = str(payload.get("answer_shape") or "verdict_with_reasons").strip() or "verdict_with_reasons"
    raw_tier = str(payload.get("budget_tier") or ("deep" if chat_mode == "MOAT" else "standard")).strip().lower()
    budget_tier: BudgetTier = (
        raw_tier if raw_tier in ("fast", "standard", "deep") else ("deep" if chat_mode == "MOAT" else "standard")  # type: ignore[assignment]
    )
    if chat_mode == "MOAT" and budget_tier == "fast":
        budget_tier = "standard"

    profile_by_tk: dict[str, Mapping[str, Any]] = {
        str(p.get("ticker") or "").strip().upper(): p
        for p in catalog_profiles
        if str(p.get("ticker") or "").strip()
    }
    known_set: set[str] | None = None
    if known_universe_tickers:
        known_set = {t.strip().upper() for t in known_universe_tickers if t.strip()} | set(profile_by_tk)
    elif profile_by_tk:
        known_set = set(profile_by_tk)

    def _clean_ticker(sym_raw: Any) -> str | None:
        sym = str(sym_raw or "").strip().upper()
        if not sym or not re.fullmatch(r"[A-Z0-9]{2,10}", sym):
            return None
        if "*" not in allowed_project_ids and sym not in allowed_project_ids:
            if sym not in denied_tickers:
                denied_tickers.append(sym)
            return None
        if known_set is not None and sym not in known_set:
            if sym not in unknown_tickers:
                unknown_tickers.append(sym)
        return sym

    ent_raw = payload.get("entities") if isinstance(payload.get("entities"), Mapping) else {}
    raw_tks = ent_raw.get("tickers") if isinstance(ent_raw.get("tickers"), Sequence) and not isinstance(
        ent_raw.get("tickers"), str
    ) else []
    tickers: list[str] = []
    for t in [*explicit_tickers, *raw_tks]:
        ct = _clean_ticker(t)
        if ct and ct not in tickers:
            tickers.append(ct)
        if len(tickers) >= 6:
            break

    anchor_ticker = _clean_ticker(ent_raw.get("anchor_ticker"))
    if anchor_ticker and anchor_ticker not in tickers and len(tickers) < 6:
        tickers.insert(0, anchor_ticker)

    cand_raw = ent_raw.get("candidate_search")
    cand_spec: CandidateSearchSpec | None = None
    if isinstance(cand_raw, Mapping):
        raw_sectors = [
            str(s).strip()
            for s in (cand_raw.get("sectors") or ())
            if str(s).strip()
        ] if isinstance(cand_raw.get("sectors"), Sequence) and not isinstance(cand_raw.get("sectors"), str) else []
        raw_tmpl = str(cand_raw.get("template") or "").strip().upper() or None
        if raw_tmpl not in {"CORPORATE", "BANK", "SECURITIES", "INSURANCE"}:
            raw_tmpl = None
        if anchor_ticker and anchor_ticker in profile_by_tk and not raw_tmpl:
            raw_tmpl = resolve_template(str(profile_by_tk[anchor_ticker].get("industry") or "")).value
        if anchor_ticker and anchor_ticker in profile_by_tk and not raw_sectors:
            anc_prof = profile_by_tk[anchor_ticker]
            ind = str(anc_prof.get("industry") or anc_prof.get("sector") or "").strip()
            if ind:
                raw_sectors = [ind]

        criteria: list[CandidateCriterion] = []
        raw_crit_list = cand_raw.get("criteria") if isinstance(cand_raw.get("criteria"), Sequence) else []
        for cr in raw_crit_list:
            if not isinstance(cr, Mapping):
                continue
            m_id = canonicalize_metric_id(str(cr.get("metric") or ""))
            if m_id is None:
                warnings.append(f"Bỏ tiêu chí lọc không xác định `{cr.get('metric')}`.")
                continue
            op_raw = str(cr.get("op") or ">=").strip()
            op: CriterionOp = op_raw if op_raw in (">=", "<=", ">", "<", "==") else ">="  # type: ignore[assignment]
            default_ref = "anchor" if cr.get("value") is None and anchor_ticker else "literal"
            ref_raw = str(cr.get("ref") or default_ref).strip().lower()
            ref: CriterionRef = "anchor" if (ref_raw == "anchor" and anchor_ticker) else "literal"
            val_raw = cr.get("value")
            val_num: float | None = None
            if isinstance(val_raw, int | float) and not isinstance(val_raw, bool):
                val_num = float(val_raw)
            if ref == "literal" and val_num is None:
                warnings.append(f"Tiêu chí `{m_id}` thiếu ngưỡng `value` -> bỏ qua.")
                continue
            tol_raw = cr.get("tolerance", 0.0)
            tol = float(tol_raw) if isinstance(tol_raw, int | float) and not isinstance(tol_raw, bool) else 0.0
            criteria.append(CandidateCriterion(metric=m_id, op=op, value=val_num, ref=ref, tolerance=max(0.0, tol)))

        sort_by = canonicalize_metric_id(str(cand_raw.get("sort_by") or "")) if cand_raw.get("sort_by") else None
        if sort_by is None and criteria:
            sort_by = criteria[-1].metric
        sort_dir: Literal["desc", "asc"] = (
            "asc" if str(cand_raw.get("sort_dir") or "desc").strip().lower() == "asc" else "desc"
        )
        qual_q = str(cand_raw.get("qualitative_query") or "").strip() or None
        lim_raw = cand_raw.get("limit", 4)
        lim = max(1, min(5, int(lim_raw))) if isinstance(lim_raw, int | float) else 4
        cand_spec = CandidateSearchSpec(
            sectors=raw_sectors,
            template=raw_tmpl,
            criteria=criteria,
            sort_by=sort_by,
            sort_dir=sort_dir,
            qualitative_query=qual_q,
            limit=lim,
        )

    if anchor_ticker and cand_spec is None:
        errors.append(
            f"Đã đặt `anchor_ticker={anchor_ticker}` nhưng thiếu `candidate_search` để tìm ứng viên đối chiếu."
        )

    def _parse_claim_list(raw_items: Any, *, prefix: str, cap: int) -> list[ClaimSpec]:
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, str):
            return []
        parsed_claims: list[ClaimSpec] = []
        for idx, item in enumerate(raw_items, 1):
            if len(parsed_claims) >= cap:
                warnings.append(f"Cắt bớt số ý `{prefix}` theo trần ngân sách ({cap}).")
                break
            if isinstance(item, str):
                # Tolerate shorthand string premise by attaching default KPI/fact check if possible
                stmt = item.strip()
                if not stmt:
                    continue
                cid = f"{prefix}{idx}"
                ev_list = ["kpi:roe", "risk"]
                parsed_claims.append(ClaimSpec(id=cid, statement=stmt, evidence=ev_list))
                continue
            if not isinstance(item, Mapping):
                continue
            stmt = str(item.get("statement") or "").strip()
            if not stmt:
                continue
            cid = str(item.get("id") or f"{prefix}{idx}").strip() or f"{prefix}{idx}"
            raw_ev_seq = item.get("evidence")
            if isinstance(raw_ev_seq, str):
                raw_ev_seq = [raw_ev_seq]
            elif not isinstance(raw_ev_seq, Sequence):
                raw_ev_seq = []
            ev_clean: list[str] = []
            for ev in raw_ev_seq:
                canon, warn = canonicalize_evidence_spec(ev)
                if warn:
                    warnings.append(warn)
                if canon and canon not in ev_clean:
                    ev_clean.append(canon)
                if len(ev_clean) >= 5:
                    break
            if not ev_clean:
                errors.append(f"Ý `{cid}` ('{stmt[:60]}') không có loại bằng chứng hợp lệ nào.")
                continue
            norm_period, p_warn = clamp_period_to_pit(
                str(item.get("period")) if item.get("period") is not None else None,
                as_of_date,
            )
            if p_warn:
                pit_adjustments.append(p_warn)
            deps_raw = item.get("depends_on")
            deps = (
                [str(d).strip() for d in deps_raw if str(d).strip()]
                if isinstance(deps_raw, Sequence) and not isinstance(deps_raw, str)
                else []
            )
            parsed_claims.append(
                ClaimSpec(
                    id=cid,
                    statement=stmt,
                    evidence=ev_clean,
                    period=norm_period,
                    depends_on=deps,
                )
            )
        return parsed_claims

    premises = _parse_claim_list(payload.get("premises_to_check"), prefix="P", cap=MAX_PREMISES)
    claims = _parse_claim_list(
        payload.get("claims"),
        prefix="K",
        cap=CLAIM_BUDGET_BY_TIER[budget_tier],
    )

    ambiguities = [
        str(a).strip()
        for a in (payload.get("ambiguities") or ())
        if isinstance(a, str) and a.strip()
    ] if isinstance(payload.get("ambiguities"), Sequence) and not isinstance(payload.get("ambiguities"), str) else []

    assumptions = [
        str(a).strip()
        for a in (payload.get("assumptions") or ())
        if isinstance(a, str) and a.strip()
    ] if isinstance(payload.get("assumptions"), Sequence) and not isinstance(payload.get("assumptions"), str) else []

    needs_clarification = bool(payload.get("needs_clarification", False))
    clarifying_question = str(payload.get("clarifying_question") or "").strip() or None

    has_actionable_target = bool(
        tickers
        or anchor_ticker
        or (
            cand_spec is not None
            and (cand_spec.criteria or cand_spec.qualitative_query or cand_spec.sectors)
        )
    )
    if has_actionable_target:
        # Code guardrail: NEVER block with a clarification question when concrete tickers or screening criteria exist!
        needs_clarification = False
        clarifying_question = None
    elif not claims and not premises and ambiguities:
        needs_clarification = True

    if needs_clarification:
        answer_shape = "clarification_needed"
        if not clarifying_question:
            amb_txt = ambiguities[0] if ambiguities else "tăng trưởng lợi nhuận, định giá rẻ hay cổ tức tiền mặt"
            clarifying_question = (
                f"Để lọc và kiểm chứng đúng trọng tâm, bạn ưu tiên tiêu chí nào nhất ({amb_txt}) "
                "hoặc đang quan tâm nhóm ngành cụ thể nào?"
            )
    elif not claims and not premises:
        errors.append("Đề bài không có `claims` hay `premises_to_check` nào để kiểm chứng.")

    if errors:
        return BriefValidationOutcome(
            ok=False,
            errors=errors,
            warnings=warnings,
            denied_tickers=denied_tickers,
            unknown_tickers=unknown_tickers,
            pit_adjustments=pit_adjustments,
        )

    brief = ResearchBrief(
        user_concern=user_concern,
        answer_shape=answer_shape,
        entities=BriefEntities(
            tickers=tickers,
            anchor_ticker=anchor_ticker,
            candidate_search=cand_spec,
        ),
        premises_to_check=premises,
        claims=claims,
        ambiguities=ambiguities,
        assumptions=assumptions,
        needs_clarification=needs_clarification,
        clarifying_question=clarifying_question,
        budget_tier=budget_tier,
    )
    return BriefValidationOutcome(
        ok=True,
        brief=brief,
        warnings=warnings,
        denied_tickers=denied_tickers,
        unknown_tickers=unknown_tickers,
        pit_adjustments=pit_adjustments,
    )


def build_brief_user_prompt(
    *,
    query: str,
    as_of_date: date,
    chat_mode: str = "GENERAL",
    explicit_tickers: Sequence[str] = (),
    available_tickers: Sequence[str] = (),
    catalog_profiles: Sequence[Mapping[str, Any]] = (),
    session_context: str = "",
    memory_context: str = "",
) -> str:
    """Build a compact (~250-450 token) user prompt for Step (1) Brief generation."""
    profile_map = {
        str(p.get("ticker") or "").strip().upper(): p
        for p in catalog_profiles
        if str(p.get("ticker") or "").strip()
    }
    candidate_tokens = set(t.strip().upper() for t in explicit_tickers if t.strip())
    for m in _TICKER_TOKEN_RE.finditer(query.upper()):
        tok = m.group(1)
        if tok in profile_map or tok in available_tickers:
            candidate_tokens.add(tok)

    matched_profiles: list[str] = []
    for tk in sorted(candidate_tokens):
        p = profile_map.get(tk)
        if p is not None:
            ind = str(p.get("industry") or p.get("sector") or "")
            tmpl = resolve_template(ind).value
            matched_profiles.append(f"{tk} ({p.get('company_name') or tk} | ngành: {ind} | mẫu BCTC: {tmpl})")
        elif tk in available_tickers:
            matched_profiles.append(f"{tk} (đã nạp tài liệu thuyết minh)")

    lines = [
        f"- Ngày khóa sổ PIT (`as_of_date`): {as_of_date.isoformat()}",
        f"- Chế độ: {chat_mode}",
    ]
    if explicit_tickers:
        lines.append(f"- Mã người dùng ghim trên giao diện: {', '.join(explicit_tickers)}")
    if matched_profiles:
        lines.append("- Hồ sơ mã nhận diện trong câu hỏi: " + "; ".join(matched_profiles))
    if available_tickers:
        lines.append(
            f"- Các mã đã có tài liệu thuyết minh PDF/Qdrant ({len(available_tickers)} mã): "
            + ", ".join(sorted(available_tickers)[:40])
            + " (Lưu ý: số liệu BCTC định lượng `FactSet`/`KPI` phủ toàn bộ ~400 mã trên sàn trong Core PG)."
        )
    if session_context:
        lines.append(f"- Ngữ cảnh hội thoại gần nhất:\n{session_context[:900]}")
    if memory_context:
        lines.append(f"- Bộ nhớ tiêu chí/danh mục cá nhân:\n{memory_context[:700]}")
    lines.append(f"\nCâu hỏi của nhà đầu tư:\n{query.strip()}")
    return "\n".join(lines)


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


def build_fallback_brief(
    *,
    query: str,
    as_of_date: date,
    explicit_tickers: Sequence[str] = (),
    catalog_profiles: Sequence[Mapping[str, Any]] = (),
    allowed_project_ids: frozenset[str] | set[str] = frozenset({"*"}),
    chat_mode: str = "GENERAL",
) -> ResearchBrief:
    """Deterministic 0-LLM fallback brief if LLM brief generation fails after 1 retry."""
    profile_map = {
        str(p.get("ticker") or "").strip().upper(): p
        for p in catalog_profiles
        if str(p.get("ticker") or "").strip()
    }
    detected: list[str] = []
    for t in explicit_tickers:
        sym = t.strip().upper()
        if sym and ("*" in allowed_project_ids or sym in allowed_project_ids) and sym not in detected:
            detected.append(sym)
    for m in _TICKER_TOKEN_RE.finditer(query.upper()):
        sym = m.group(1)
        if sym in profile_map and ("*" in allowed_project_ids or sym in allowed_project_ids) and sym not in detected:
            detected.append(sym)

    if not detected:
        return ResearchBrief(
            user_concern=query.strip()[:180],
            answer_shape="candidate_ranking",
            entities=BriefEntities(
                tickers=[],
                candidate_search=CandidateSearchSpec(
                    criteria=[CandidateCriterion(metric="roe", op=">=", value=0.12, ref="literal")],
                    sort_by="roe",
                    sort_dir="desc",
                    limit=4,
                ),
            ),
            claims=[
                ClaimSpec(
                    id="K1",
                    statement="Sàng lọc các doanh nghiệp đầu ngành theo hiệu quả sinh lời và bảng cân đối tại PIT",
                    evidence=["screen", "kpi:roe", "risk"],
                )
            ],
            assumptions=["Áp dụng tiêu chí hiệu quả sinh lời ROE >= 12% và kiểm tra rủi ro tài chính tại PIT."],
            budget_tier="standard",
        )

    ly, lq = latest_closed_quarter_for_pit(as_of_date)
    p_lbl = f"Q{lq}/{ly}"
    return ResearchBrief(
        user_concern=query.strip()[:180],
        answer_shape="verdict_with_reasons",
        entities=BriefEntities(tickers=detected[:4]),
        claims=[
            ClaimSpec(
                id="K1",
                statement=f"Kết quả kinh doanh, biên sinh lời và sức khỏe bảng cân đối của {', '.join(detected[:4])}",
                evidence=["fact:npat", "fact:equity", "kpi:roe", "risk"],
                period=p_lbl,
            ),
            ClaimSpec(
                id="K2",
                statement=f"Thuyết minh trọng yếu liên quan trực tiếp tới câu hỏi cho {', '.join(detected[:4])}",
                evidence=[f"doc:QUARTER_NOTES,BCTC_AUDITED|{query.strip()[:100]}"],
                period=p_lbl,
            ),
        ],
        budget_tier="deep" if chat_mode == "MOAT" else "standard",
    )


async def create_and_validate_brief(
    *,
    query: str,
    model: ModelProvider,
    as_of_date: date,
    allowed_project_ids: frozenset[str] | set[str] = frozenset({"*"}),
    known_universe_tickers: frozenset[str] | set[str] | None = None,
    available_tickers: Sequence[str] = (),
    catalog_profiles: Sequence[Mapping[str, Any]] = (),
    explicit_tickers: Sequence[str] = (),
    chat_mode: str = "GENERAL",
    session_context: str = "",
    memory_context: str = "",
) -> tuple[ResearchBrief, BriefValidationOutcome, Usage]:
    """Run Step (1) Brief LLM + Step (2) 0-token Validator (with at most 1 repair retry on schema error)."""
    user_prompt = build_brief_user_prompt(
        query=query,
        as_of_date=as_of_date,
        chat_mode=chat_mode,
        explicit_tickers=explicit_tickers,
        available_tickers=available_tickers,
        catalog_profiles=catalog_profiles,
        session_context=session_context,
        memory_context=memory_context,
    )
    base_messages = [
        AgentMessage(role="system", content=BRIEF_STATIC_SYSTEM_PROMPT),
        AgentMessage(role="user", content=user_prompt),
    ]
    raw_out, usage = await _collect_model_text(model, base_messages, turn=1)
    outcome = validate_research_brief(
        raw_out,
        as_of_date=as_of_date,
        allowed_project_ids=allowed_project_ids,
        known_universe_tickers=known_universe_tickers,
        catalog_profiles=catalog_profiles,
        chat_mode=chat_mode,
        explicit_tickers=explicit_tickers,
    )
    if outcome.ok and outcome.brief is not None:
        return outcome.brief, outcome, usage

    # At most 1 compact repair retry per approved spec Section 3.(2)
    retry_messages = [
        *base_messages,
        AgentMessage(role="assistant", content=raw_out[:1600] or "{}"),
        AgentMessage(
            role="user",
            content=(
                "JSON đề bài vừa rồi chưa đạt kiểm tra máy móc vì: "
                + "; ".join(outcome.errors)
                + ". Hãy xuất lại ĐÚNG MỘT JSON hợp lệ đã sửa các lỗi trên."
            ),
        ),
    ]
    raw_retry, retry_usage = await _collect_model_text(model, retry_messages, turn=2)
    total_usage = usage.plus(retry_usage)
    retry_outcome = validate_research_brief(
        raw_retry,
        as_of_date=as_of_date,
        allowed_project_ids=allowed_project_ids,
        known_universe_tickers=known_universe_tickers,
        catalog_profiles=catalog_profiles,
        chat_mode=chat_mode,
        explicit_tickers=explicit_tickers,
    )
    if retry_outcome.ok and retry_outcome.brief is not None:
        return retry_outcome.brief, retry_outcome, total_usage

    fb = build_fallback_brief(
        query=query,
        as_of_date=as_of_date,
        explicit_tickers=explicit_tickers,
        catalog_profiles=catalog_profiles,
        allowed_project_ids=allowed_project_ids,
        chat_mode=chat_mode,
    )
    retry_outcome.warnings.append("Sử dụng đề bài dự phòng tất định sau 2 lần kiểm tra JSON không đạt.")
    retry_outcome.brief = fb
    return fb, retry_outcome, total_usage
