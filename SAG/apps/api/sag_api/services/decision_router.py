"""Decision routing through SiliconFlow's System One API."""

from __future__ import annotations

import hashlib
import re
import time
from collections import OrderedDict
from collections.abc import Mapping
from threading import RLock
from typing import Any

import httpx

from sag_api.core.config import settings
from sag_api.core.logging import get_logger
from sag_api.services.retrieval_plan import (
    VALID_ANSWER_SHAPES,
    extract_query_cardinality_and_shape,
)

log = get_logger("decision_router")
CHAT_HIGH_CONFIDENCE = 0.65
VALID_MEMORY_SCOPES = frozenset(
    {"none", "session_history", "project_documents", "hybrid_both", "full_session_audit"}
)
VALID_TEMPORAL_MODES = frozenset(
    {"current_effective", "change_lineage", "past_point", "atemporal"}
)
_DECISION_QUESTIONS = {
    "intent_type": {
        "type": "choice",
        "instructions": "What is the primary intent of this user message in `query`?",
        "criteria": {
            "chit_chat": (
                "Greetings, saying hello or goodbye, thank you, who are you, casual pleasantries, or small talk"
            ),
            "factual_lookup": (
                "Questions asking for information, company policy, procedures, facts, code, or documentation"
            ),
            "complex_reasoning": "Comparison, synthesis, multi-step explanation, or complex analytical query",
        },
    },
    "domain_topic": {
        "type": "choice",
        "instructions": "Which domain does `query` belong to?",
        "criteria": {
            "technical": "Programming, software, engineering, system architecture, bugs, APIs",
            "business_hr": "Human resources, company policy, finance, contracts, legal, operations, reimbursement",
            "general": "General conversation, casual talk, or common knowledge",
        },
    },
    "memory_scope": {
        "type": "choice",
        "instructions": "Which evidence source does `query` primarily require?",
        "criteria": {
            "none": "Casual greetings, pleasantries, or standalone requests needing no prior chat or documents",
            "session_history": (
                "Recalling earlier chat turns, user preferences, assigned owners, open tasks, bugs, or team decisions"
            ),
            "project_documents": (
                "Looking up uploaded documents, policies, specifications, contracts, or canonical knowledge base"
            ),
            "hybrid_both": (
                "Comparing, cross-checking, or combining chat decisions/discussions with uploaded project documents"
            ),
            "full_session_audit": (
                "Exhaustive summary or complete list of all decisions, tasks, or topics across the entire conversation"
            ),
        },
    },
    "temporal_mode": {
        "type": "choice",
        "instructions": "How does `query` treat time and decision updates?",
        "criteria": {
            "current_effective": "Needs the latest active/confirmed decision, current owner, or current working state",
            "change_lineage": "Asks why, how, or when a decision/plan changed from an earlier option to a newer one",
            "past_point": "Asks specifically when something happened or what was said at a past meeting/date",
            "atemporal": "General factual, conceptual, or procedural question without temporal state transitions",
        },
    },
    "answer_shape": {
        "type": "choice",
        "instructions": "What output shape and target unit does `query` expect?",
        "criteria": {
            "fact_answer": "Direct factual answer, explanation, or procedural steps from relevant passages",
            "multi_facet_compare": (
                "Comparison, contrast, or multi-aspect synthesis across multiple entities or options"
            ),
            "document_list": "List, find, or show multiple relevant files, documents, policies, or reports",
            "exhaustive_count": "Count how many files, documents, items, or records match a condition",
            "summarize_set": "Summarize or synthesize an overview across a set of documents or topics",
            "open_document": "Open or display a single specific file or document",
        },
    },
}

_FULL_AUDIT_RE = re.compile(
    r"(?i)(?:(?:tổng\s+hợp|liệt\s+kê|điểm\s+lại|tóm\s+tắt|rút\s+ra)\s+(?:tất\s+cả|toàn\s+bộ|mọi)"
    r".*(?:từ\s+đầu|cuộc\s+trò\s+chuyện|buổi\s+chat|phiên|quyết\s+định|công\s+việc)"
    r"|(?:summarize|list|compile)\s+(?:all|every).*(?:from\s+the\s+start|entire\s+session|entire\s+conversation|decisions))"
)
_SESSION_CUE_RE = re.compile(
    r"(?i)(?:\b(?:chúng\s+ta|chúng\s+mình|tụi\s+mình|mình\s+vừa|mình\s+đã|vừa\s+bàn|lúc\s+nãy|lúc\s+đầu|ban\s+đầu|"
    r"buổi\s+họp|phiên\s+trước|cuộc\s+chat|chốt|thống\s+nhất|phương\s+án\s+(?:nào|đó|trên|mới|cũ)|"
    r"tiếp\s+tục\s+công\s+việc|ai\s+(?:nhận|phụ\s+trách|làm|đảm\s+nhiệm)|anh\s+\w+|chị\s+\w+|"
    r"we\s+(?:agreed|discussed|chose|decided)|earlier\s+in\s+chat|our\s+decision|"
    r"continue\s+(?:yesterday|where\s+we\s+left))\b)"
)
_DOC_CUE_RE = re.compile(
    r"(?i)(?:\b(?:tài\s+liệu|quy\s+định|quy\s+chuẩn|chính\s+sách|hợp\s+đồng|báo\s+cáo|biên\s+bản|"
    r"điều\s+\d+|khoản\s+\d+|mục\s+\d+|adr[-_ ]?\d+|rfc[-_ ]?\d+|spec|specification|policy|document|contract)\b)"
)
_LINEAGE_CUE_RE = re.compile(
    r"(?i)(?:(?:vì\s+sao|tại\s+sao|lý\s+do\s+(?:gì|nào)).*(?:chuyển|đổi|bỏ|thay|quay\s+lại)"
    r"|(?:chuyển\s+từ|đổi\s+từ|thay\s+đổi\s+từ).*(?:sang|qua)"
    r"|(?:lịch\s+sử\s+thay\s+đổi|quá\s+trình\s+thay\s+đổi|why\s+did\s+we\s+(?:switch|change|drop|revert)|"
    r"switched\s+from\s+.+\s+to))"
)
_TEMPORAL_TARGET_RE = re.compile(
    r"(?i)(?:\b(?:khi\s+nào|vào\s+ngày\s+nào|ngày\s+nào|ngày\s+mấy|lúc\s+mấy\s+giờ|bao\s+lâu|trước\s+hay\s+sau|"
    r"thứ\s+tự\s+thời\s+gian|when\s+did|what\s+date|how\s+long|before\s+or\s+after)\b)"
)
_EFFECTIVE_STATE_RE = re.compile(
    r"(?i)(?:\b(?:chốt|quyết\s+định\s+(?:cuối|hiện\s+tại|hiện\s+hành|chọn)|cuối\s+cùng\s+(?:chọn|dùng)|"
    r"đang\s+dùng|hiện\s+tại|hiện\s+giờ|ai\s+(?:nhận|phụ\s+trách|đảm\s+nhiệm)|còn\s+lỗi\s+nào|"
    r"việc\s+gì\s+đang\s+dở|tiếp\s+tục\s+công\s+việc|which\s+\w+\s+did\s+we\s+(?:choose|pick|select)|"
    r"current\s+(?:decision|status|owner)|final\s+decision|who\s+(?:owns|is\s+responsible|took))\b)"
)
_PAST_ANCHOR_RE = re.compile(
    r"(?i)(?:\b(?:hôm\s+qua|tuần\s+trước|tháng\s+trước|buổi\s+họp\s+trước|đầu\s+phiên|đầu\s+buổi|"
    r"yesterday|last\s+week|last\s+meeting|earlier\s+session)\b)"
)


def infer_ecce_routing_signals(query: str, *, coarse_intent: str = "AMBIGUOUS") -> tuple[str, str]:
    """Deterministic Zero-Mem/ECCE signal inference separating temporal filters from targets."""
    cleaned = query.strip()
    if not cleaned or coarse_intent == "CHAT":
        return "none", "atemporal"

    if _FULL_AUDIT_RE.search(cleaned):
        memory_scope = "full_session_audit"
    else:
        has_session = bool(_SESSION_CUE_RE.search(cleaned))
        has_doc = bool(_DOC_CUE_RE.search(cleaned))
        if has_session and has_doc:
            memory_scope = "hybrid_both"
        elif has_session:
            memory_scope = "session_history"
        else:
            memory_scope = "project_documents"

    # Zero-Mem _requires_temporal_route principle:
    # A past time anchor ("tuần trước ai nhận task X?") is a filter on an entity/state lookup,
    # NOT a temporal target unless the question explicitly asks *when / how long / before-or-after*.
    if _LINEAGE_CUE_RE.search(cleaned):
        temporal_mode = "change_lineage"
    elif _TEMPORAL_TARGET_RE.search(cleaned):
        temporal_mode = "past_point"
    elif _EFFECTIVE_STATE_RE.search(cleaned):
        temporal_mode = "current_effective"
    elif _PAST_ANCHOR_RE.search(cleaned):
        temporal_mode = "past_point"
    else:
        temporal_mode = "atemporal"

    return memory_scope, temporal_mode


def _fallback_route(query: str, reason: str) -> dict[str, Any]:
    memory_scope, temporal_mode = infer_ecce_routing_signals(query, coarse_intent="AMBIGUOUS")
    shape_signals = extract_query_cardinality_and_shape(query)
    return {
        "query": query,
        "coarse_intent": "AMBIGUOUS",
        "is_chitchat": False,
        "need_retrieval": True,
        "suggested_strategy": "multi",
        "domain": "general",
        "memory_scope": memory_scope,
        "temporal_mode": temporal_mode,
        "answer_shape": shape_signals.answer_shape.value,
        "confidence": 0.0,
        "latency_ms": 0.0,
        "model": "fallback",
        "fallback_used": True,
        "fallback_reason": reason,
        "reason_codes": [reason.upper()],
    }


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def _route_from_answers(
    query: str,
    answers: dict,
    model: str,
    latency_ms: float,
    provider: str,
) -> dict[str, Any]:
    intent_result = answers.get("intent_type")
    if not isinstance(intent_result, dict):
        return _fallback_route(query, f"{provider}_invalid_response")

    intent_choice = str(intent_result.get("choice", "factual_lookup")).strip().lower()
    intent_confidence = _confidence(intent_result.get("confidence", 0.5))
    domain_result = answers.get("domain_topic")
    domain_choice = str(domain_result.get("choice", "general")) if isinstance(domain_result, dict) else "general"
    if domain_choice not in {"technical", "business_hr", "general"}:
        domain_choice = "general"

    is_chat_intent = intent_choice in {"chit_chat", "chat", "greeting"}
    is_command = intent_choice in {"command", "tool_call", "action"}
    if is_chat_intent and intent_confidence >= CHAT_HIGH_CONFIDENCE:
        coarse_intent, reason_codes = "CHAT", ["HIGH_CONFIDENCE_CHAT"]
    elif is_chat_intent:
        coarse_intent, reason_codes = "AMBIGUOUS", ["CHAT_CONFIDENCE_LOW"]
    elif is_command:
        coarse_intent, reason_codes = "COMMAND", ["COMMAND_INTENT"]
    elif intent_confidence < CHAT_HIGH_CONFIDENCE:
        coarse_intent, reason_codes = "AMBIGUOUS", ["INTENT_CONFIDENCE_LOW"]
    else:
        coarse_intent, reason_codes = "KNOWLEDGE", ["KNOWLEDGE_INTENT"]

    inferred_scope, inferred_temporal = infer_ecce_routing_signals(query, coarse_intent=coarse_intent)
    scope_result = answers.get("memory_scope")
    scope_choice = (
        str(scope_result.get("choice", "")).strip().lower()
        if isinstance(scope_result, dict)
        else ""
    )
    if coarse_intent == "CHAT":
        memory_scope = "none"
    elif scope_choice in VALID_MEMORY_SCOPES and scope_choice != "none":
        memory_scope = scope_choice
    else:
        memory_scope = inferred_scope

    temporal_result = answers.get("temporal_mode")
    temporal_choice = (
        str(temporal_result.get("choice", "")).strip().lower()
        if isinstance(temporal_result, dict)
        else ""
    )
    if coarse_intent == "CHAT":
        temporal_mode = "atemporal"
    elif temporal_choice in VALID_TEMPORAL_MODES:
        temporal_conf = (
            _confidence(temporal_result.get("confidence", 0.5))
            if isinstance(temporal_result, dict)
            else 0.0
        )
        if temporal_conf < 0.70 and inferred_temporal in {"change_lineage", "current_effective"}:
            temporal_mode = inferred_temporal
        else:
            temporal_mode = temporal_choice
    else:
        temporal_mode = inferred_temporal

    shape_signals = extract_query_cardinality_and_shape(query)
    shape_result = answers.get("answer_shape")
    shape_choice = (
        str(shape_result.get("choice", "")).strip().lower()
        if isinstance(shape_result, dict)
        else ""
    )
    if (
        shape_signals.explicit_count is not None
        or shape_signals.answer_shape.value != "fact_answer"
    ):
        answer_shape = shape_signals.answer_shape.value
    elif shape_choice in VALID_ANSWER_SHAPES:
        answer_shape = shape_choice
    else:
        answer_shape = shape_signals.answer_shape.value

    return {
        "query": query,
        "coarse_intent": coarse_intent,
        "is_chitchat": coarse_intent == "CHAT",
        "need_retrieval": coarse_intent != "CHAT",
        "suggested_strategy": (
            "multi"
            if intent_choice == "complex_reasoning"
            or coarse_intent == "AMBIGUOUS"
            or memory_scope in {"hybrid_both", "full_session_audit"}
            or temporal_mode == "change_lineage"
            or answer_shape in {"multi_facet_compare", "summarize_set"}
            else "vector"
        ),
        "domain": domain_choice,
        "memory_scope": memory_scope,
        "temporal_mode": temporal_mode,
        "answer_shape": answer_shape,
        "confidence": intent_confidence,
        "latency_ms": latency_ms,
        "model": model,
        "fallback_used": False,
        "fallback_reason": None,
        "reason_codes": reason_codes,
    }


_LIVE_KEV4B_CACHE: dict[str, dict[str, Any]] | None = None

_DECISION_ROUTE_CACHE: OrderedDict[tuple[str, str], tuple[dict[str, Any], str]] = OrderedDict()
_DECISION_ROUTE_CACHE_MAX = 512
_DECISION_ROUTE_CACHE_LOCK = RLock()

# ── Phase 1: Async 7-Axis Financial Research Decision Gate (Zero Regex) ──────

RESEARCH_SHORT_CIRCUIT_MIN_CONF = 0.78
_RESEARCH_ROUTE_CACHE_TTL_SECONDS = 300.0
_RESEARCH_ROUTE_CACHE: OrderedDict[str, tuple[float, dict[str, Any], str]] = OrderedDict()
_RESEARCH_ROUTE_CACHE_LOCK = RLock()

_VALID_RESEARCH_DOMAIN_CLASSES = frozenset(
    {
        "chitchat_casual",
        "out_of_domain",
        "system_meta_help",
        "general_finance_education",
        "reformat_or_recall_chat",
        "stock_research_data",
    }
)
_SHORT_CIRCUIT_DOMAIN_CLASSES = frozenset(
    {
        "chitchat_casual",
        "out_of_domain",
        "system_meta_help",
        "general_finance_education",
        "reformat_or_recall_chat",
    }
)
_VALID_RESEARCH_TASK_ACTIONS = frozenset(
    {
        "direct_reply",
        "fact_lookup",
        "screening_ranking",
        "peer_comparison",
        "trend_attribution",
        "deep_moat_thesis",
        "macro_and_value_chain",
        "audit_discrepancy",
        "compute_or_scenario",
        "clarify_request",
    }
)
_VALID_RESEARCH_EVIDENCE_HINTS = frozenset(
    {
        "none",
        "session_memory_only",
        "quantitative_metrics_only",
        "filings_and_tables",
        "knowledge_graph_macro",
        "hybrid_all",
    }
)
_VALID_RESEARCH_BUDGET_TIERS = frozenset(
    {
        "zero_tool",
        "light_1_2_steps",
        "standard_2_3_steps",
        "deep_4_6_steps",
    }
)
_VALID_RESEARCH_TEMPORAL_SCOPES = frozenset(
    {
        "atemporal",
        "latest_pit",
        "specific_period",
        "multi_year_trend",
        "session_change_lineage",
    }
)
_VALID_RESEARCH_AMBIGUITY_GUARDS = frozenset(
    {
        "clear",
        "needs_coreference_from_memory",
        "underspecified_ask_clarify",
        "investment_advice_disclaimer",
        "realtime_price_unavailable",
    }
)
_VALID_RESEARCH_OUTPUT_STYLES = frozenset(
    {
        "conversational_short",
        "fact_citation",
        "ranking_table",
        "comparison_matrix",
        "executive_memo",
        "formula_breakdown",
    }
)
_VALID_RESEARCH_MEMORY_RECALL_NEEDS = frozenset(
    {
        "none",
        "current_session_only",
        "user_profile_light",
        "cross_session_graph",
    }
)

_CROSS_SESSION_CUE_RE = re.compile(
    r"(?:phiên\s+trước|lần\s+trước|hôm\s+trước|tuần\s+trước|tháng\s+trước|trước\s+đây|"
    r"luận\s+điểm\s+cũ|nhớ\s+lại|tôi\s+(?:đã\s+)?từng\s+(?:nói|phân\s+tích|hỏi|lo\s+ngại)|"
    r"lịch\s+sử\s+(?:phân\s+tích|luận\s+điểm)|bộ\s+nhớ|các\s+phiên\s+(?:liên\s+quan|cũ)|"
    r"previous\s+session|last\s+time|cross\s+session|thesis\s+lineage)",
    re.IGNORECASE | re.UNICODE,
)
_USER_PROFILE_CUE_RE = re.compile(
    r"(?:tiêu\s+chí\s+của\s+(?:tôi|mình)|khẩu\s+vị|gu\s+đầu\s+tư|danh\s+mục\s+của\s+(?:tôi|mình)|"
    r"tôi\s+đang\s+(?:giữ|nắm)|watchlist|như\s+thường\s+lệ|theo\s+phong\s+cách|"
    r"bên\s+(?:ssi|vps|tcbs|vndirect|hsc|mbs)|lọc\s+(?:theo\s+)?tiêu\s+chí|cổ\s+phiếu\s+của\s+(?:tôi|mình)|"
    r"my\s+(?:portfolio|criteria|holdings|watchlist|style))",
    re.IGNORECASE | re.UNICODE,
)
_SESSION_FOLLOWUP_CUE_RE = re.compile(
    r"(?:mã\s+đó|mã\s+này|mã\s+trên|ở\s+trên|lượt\s+trước|câu\s+trước|vừa\s+rồi|"
    r"tóm\s+tắt\s+lại|dịch\s+lại|so\s+với\s+nó|của\s+nó|tiếp\s+tục|chi\s+tiết\s+hơn)",
    re.IGNORECASE | re.UNICODE,
)


def infer_deterministic_memory_recall_need(
    query: str,
    *,
    session_state: Mapping[str, Any] | None = None,
    has_user_memories: bool = False,
) -> str:
    """Deterministic R0 fallback for `memory_recall_need` when `decision_provider == 'disabled'`."""
    q = (query or "").strip()
    if not q:
        return "none"
    if _CROSS_SESSION_CUE_RE.search(q):
        return "cross_session_graph"
    if _USER_PROFILE_CUE_RE.search(q):
        return "user_profile_light"
    has_prior = bool(session_state.get("has_prior_turns")) if session_state else False
    if has_prior and _SESSION_FOLLOWUP_CUE_RE.search(q):
        return "current_session_only"
    if has_user_memories or (session_state and bool(session_state.get("has_user_memories"))):
        return "user_profile_light"
    return "none"

_RESEARCH_DECISION_QUESTIONS: dict[str, dict[str, Any]] = {
    "domain_class": {
        "type": "choice",
        "instructions": "Classify whether `query` needs live Vietnamese stock/BCTC/market data or is a zero-data turn.",
        "criteria": {
            "chitchat_casual": (
                "Greetings, thanks, jokes, emotional banter, or casual conversation without finance requests"
            ),
            "out_of_domain": "Non-finance topics unrelated to investing, accounting, macroeconomics, or this workspace",
            "system_meta_help": (
                "Asking about assistant capabilities, available tools, how to use this app, or formatting help"
            ),
            "general_finance_education": (
                "Pure textbook finance/accounting concepts, formulas, or VAS/IFRS rules "
                "with no specific listed company lookup"
            ),
            "reformat_or_recall_chat": (
                "Summarizing, translating, reformatting, or recalling only prior messages in this chat session"
            ),
            "stock_research_data": (
                "Any lookup, screening, comparison, financial table/note audit, macro/value-chain analysis, "
                "or thesis on listed companies, sectors, filings, or financial metrics"
            ),
        },
    },
    "task_action": {
        "type": "choice",
        "instructions": "Identify the primary analytical action requested by `query`.",
        "criteria": {
            "direct_reply": "Casual reply, greeting, system help, or pure textbook explanation",
            "fact_lookup": "Focused factual check on one company, metric, note, or filing disclosure",
            "screening_ranking": "Filtering, ranking, or finding top/bottom companies across a universe or sector",
            "peer_comparison": "Side-by-side comparison across two or more companies or peers",
            "trend_attribution": "Explaining why a financial metric changed across quarters or years",
            "deep_moat_thesis": "Deep competitive moat, governance, risk, capital allocation, or investment memo",
            "macro_and_value_chain": (
                "Macro factor sensitivity, FX/rates/commodity impact, or supply-chain/partner links"
            ),
            "audit_discrepancy": "Cross-checking management narrative/AGM plan against audited financial statements",
            "compute_or_scenario": "Explicit ratio calculation, CAGR/YoY derivation, or numerical stress/scenario test",
            "clarify_request": "Missing critical target company/metric/timeframe even with session context",
        },
    },
    "evidence_source_hint": {
        "type": "choice",
        "instructions": "Suggest the primary evidence modality (soft hint; tools remain open).",
        "criteria": {
            "none": "No external retrieval or memory needed",
            "session_memory_only": "Only prior conversation turns in this session are needed",
            "quantitative_metrics_only": "Mainly quantitative valuation/ratio screening across tickers or sectors",
            "filings_and_tables": "Corporate filings, audited financial statements, footnotes, or AGM resolutions",
            "knowledge_graph_macro": "Macro sensitivity betas, true peers, or supplier/customer value-chain graph",
            "hybrid_all": "Combines screening/metrics, audited filings/tables, and macro/value-chain graph",
        },
    },
    "reasoning_budget_tier": {
        "type": "choice",
        "instructions": "Select the soft tool-calling reasoning budget tier for `query`.",
        "criteria": {
            "zero_tool": "Pure zero-data conversational, meta, or textbook reply",
            "light_1_2_steps": "Direct single-company lookup, simple calculation, or straightforward screen",
            "standard_2_3_steps": "Multi-aspect lookup, peer comparison, trend attribution, or screen + filing check",
            "deep_4_6_steps": "Deep MOAT broker memo, multi-hop footnote audit, or narrative-vs-numbers cross-check",
        },
    },
    "temporal_scope": {
        "type": "choice",
        "instructions": "How does `query` anchor time relative to filings and session history?",
        "criteria": {
            "atemporal": "Concept, greeting, or timeless definition",
            "latest_pit": "Most recent audited/reported period available as of the PIT cutoff",
            "specific_period": "Explicit fiscal year, quarter, or specific reporting date",
            "multi_year_trend": "Multi-year evolution, CAGR, YoY trajectory, or cycle comparison",
            "session_change_lineage": "Tracking or recalling what changed across earlier turns of this conversation",
        },
    },
    "ambiguity_and_guard": {
        "type": "choice",
        "instructions": "Detect coreference dependency, underspecification, or compliance boundary.",
        "criteria": {
            "clear": "Self-contained request with clear entities and scope",
            "needs_coreference_from_memory": (
                "Uses pronouns or ellipsis referring back to tickers/tables from earlier turns"
            ),
            "underspecified_ask_clarify": "Too ambiguous to answer reliably without user clarification",
            "investment_advice_disclaimer": (
                "Asks direct buy/sell/target-price/timing advice requiring compliance boundary"
            ),
            "realtime_price_unavailable": (
                "Asks live intraday tick/order-book price or tomorrow's ceiling/floor prediction"
            ),
        },
    },
    "output_style": {
        "type": "choice",
        "instructions": "Preferred presentation structure for the final answer.",
        "criteria": {
            "conversational_short": "Brief direct conversational answer",
            "fact_citation": "Concise grounded bullets with verbatim citations",
            "ranking_table": "Structured ranked screening table with key metrics",
            "comparison_matrix": "Side-by-side peer comparison matrix and takeaways",
            "executive_memo": "Structured deep research / MOAT analyst report",
            "formula_breakdown": "Step-by-step transparent mathematical derivation",
        },
    },
    "memory_recall_need": {
        "type": "choice",
        "instructions": (
            "Determine whether `query` needs prior session turns, saved user profile/criteria, "
            "or cross-session memory graph."
        ),
        "criteria": {
            "none": "Self-contained question needing neither prior turns nor saved user profile/memories",
            "current_session_only": (
                "Follow-up, coreference, or reformat referencing only earlier turns in this session"
            ),
            "user_profile_light": (
                "Needs user's saved formatting preferences, investing criteria, external holdings, "
                "or portfolio context"
            ),
            "cross_session_graph": (
                "Recalls past sessions, prior investment theses, linked themes, or user stance changes "
                "across sessions"
            ),
        },
    },
}


def budget_tier_to_max_turns(tier: str, *, chat_mode: str = "GENERAL") -> int:
    """Convert soft reasoning budget tier to maximum agent tool-calling turns."""
    clean_mode = (chat_mode or "GENERAL").strip().upper()
    mapping = {
        "zero_tool": 0,
        "light_1_2_steps": 2,
        "standard_2_3_steps": 3,
        "deep_4_6_steps": 5,
    }
    base = mapping.get(tier, 3)
    if clean_mode == "MOAT" and base > 0:
        return max(base, 5)
    return base


def _research_cache_key(
    *,
    model: str,
    query: str,
    chat_mode: str,
    active_tickers: tuple[str, ...],
    has_prior_turns: bool,
    last_turn_summary: str = "",
    has_user_memories: bool = False,
) -> str:
    summary_hash = (
        hashlib.sha256(last_turn_summary.strip().encode("utf-8")).hexdigest()[:16]
        if last_turn_summary.strip()
        else ""
    )
    raw = "|".join(
        [
            model.strip(),
            " ".join(query.strip().lower().split()),
            chat_mode.strip().upper(),
            ",".join(sorted(active_tickers)),
            "1" if has_prior_turns else "0",
            summary_hash,
            "1" if has_user_memories else "0",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_cached_research_route(cache_key: str) -> tuple[dict[str, Any], str] | None:
    now = time.monotonic()
    with _RESEARCH_ROUTE_CACHE_LOCK:
        entry = _RESEARCH_ROUTE_CACHE.get(cache_key)
        if entry is None:
            return None
        expires_at, answers, resolved_model = entry
        if now >= expires_at:
            _RESEARCH_ROUTE_CACHE.pop(cache_key, None)
            return None
        _RESEARCH_ROUTE_CACHE.move_to_end(cache_key)
        return dict(answers), resolved_model


def _put_cached_research_route(cache_key: str, answers: dict[str, Any], resolved_model: str) -> None:
    expires_at = time.monotonic() + _RESEARCH_ROUTE_CACHE_TTL_SECONDS
    with _RESEARCH_ROUTE_CACHE_LOCK:
        _RESEARCH_ROUTE_CACHE[cache_key] = (expires_at, dict(answers), resolved_model)
        _RESEARCH_ROUTE_CACHE.move_to_end(cache_key)
        while len(_RESEARCH_ROUTE_CACHE) > _DECISION_ROUTE_CACHE_MAX:
            _RESEARCH_ROUTE_CACHE.popitem(last=False)


def _research_fail_open_route(
    query: str,
    *,
    chat_mode: str,
    reason: str,
    latency_ms: float = 0.0,
    session_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail-open strictly to substantive research agent (0 regex overrides)."""
    is_moat = (chat_mode or "GENERAL").strip().upper() == "MOAT"
    budget_tier = "deep_4_6_steps" if is_moat else "standard_2_3_steps"
    mem_need = infer_deterministic_memory_recall_need(
        query,
        session_state=session_state,
        has_user_memories=bool(session_state.get("has_user_memories")) if session_state else False,
    )
    return {
        "query": query,
        "domain_class": "stock_research_data",
        "task_action": "deep_moat_thesis" if is_moat else "fact_lookup",
        "evidence_source_hint": "hybrid_all",
        "reasoning_budget_tier": budget_tier,
        "temporal_scope": "latest_pit",
        "ambiguity_and_guard": "clear",
        "output_style": "executive_memo" if is_moat else "fact_citation",
        "memory_recall_need": mem_need,
        "max_agent_turns": budget_tier_to_max_turns(budget_tier, chat_mode=chat_mode),
        "is_short_circuit": False,
        "short_circuit_kind": None,
        "eval_eligible": True,
        "attach_investment_disclaimer": False,
        "need_retrieval": True,
        "confidence": 0.0,
        "axis_confidences": {},
        "latency_ms": latency_ms,
        "model": "fail_open",
        "fallback_used": True,
        "fallback_reason": reason,
        "reason_codes": [reason.upper()],
    }


def _extract_choice(
    answers: Mapping[str, Any],
    axis: str,
    valid: frozenset[str],
    default: str,
) -> tuple[str, float]:
    raw = answers.get(axis)
    if not isinstance(raw, Mapping):
        return default, 0.0
    choice = str(raw.get("choice") or "").strip().lower()
    conf = _confidence(raw.get("confidence", 0.5))
    if choice not in valid:
        return default, 0.0
    return choice, conf


def _research_route_from_answers(
    query: str,
    answers: Mapping[str, Any],
    *,
    chat_mode: str,
    model: str,
    latency_ms: float,
    session_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(answers.get("domain_class"), Mapping):
        return _research_fail_open_route(
            query,
            chat_mode=chat_mode,
            reason="siliconflow_invalid_research_response",
            latency_ms=latency_ms,
            session_state=session_state,
        )

    is_moat = (chat_mode or "GENERAL").strip().upper() == "MOAT"
    domain_class, domain_conf = _extract_choice(
        answers, "domain_class", _VALID_RESEARCH_DOMAIN_CLASSES, "stock_research_data"
    )
    task_action, task_conf = _extract_choice(
        answers,
        "task_action",
        _VALID_RESEARCH_TASK_ACTIONS,
        "deep_moat_thesis" if is_moat else "fact_lookup",
    )
    evidence_hint, evidence_conf = _extract_choice(
        answers, "evidence_source_hint", _VALID_RESEARCH_EVIDENCE_HINTS, "hybrid_all"
    )
    budget_tier, budget_conf = _extract_choice(
        answers,
        "reasoning_budget_tier",
        _VALID_RESEARCH_BUDGET_TIERS,
        "deep_4_6_steps" if is_moat else "standard_2_3_steps",
    )
    temporal_scope, temporal_conf = _extract_choice(
        answers, "temporal_scope", _VALID_RESEARCH_TEMPORAL_SCOPES, "latest_pit"
    )
    ambiguity_guard, guard_conf = _extract_choice(
        answers, "ambiguity_and_guard", _VALID_RESEARCH_AMBIGUITY_GUARDS, "clear"
    )
    output_style, style_conf = _extract_choice(
        answers,
        "output_style",
        _VALID_RESEARCH_OUTPUT_STYLES,
        "executive_memo" if is_moat else "fact_citation",
    )
    memory_recall_need, mem_conf = _extract_choice(
        answers,
        "memory_recall_need",
        _VALID_RESEARCH_MEMORY_RECALL_NEEDS,
        "none",
    )
    if "memory_recall_need" not in answers:
        memory_recall_need = infer_deterministic_memory_recall_need(
            query,
            session_state=session_state,
            has_user_memories=bool(session_state.get("has_user_memories")) if session_state else False,
        )

    reason_codes: list[str] = []
    is_short_circuit = False
    short_circuit_kind: str | None = None

    # Oracle-gap fail-open rule: ONLY skip retrieval when confidence >= 0.78 on a 0-data class.
    if domain_class in _SHORT_CIRCUIT_DOMAIN_CLASSES and domain_conf >= RESEARCH_SHORT_CIRCUIT_MIN_CONF:
        is_short_circuit = True
        short_circuit_kind = domain_class
        budget_tier = "zero_tool"
        reason_codes.append(f"SHORT_CIRCUIT_{domain_class.upper()}")
    elif (
        ambiguity_guard in {"investment_advice_disclaimer", "realtime_price_unavailable"}
        and domain_class != "stock_research_data"
        and guard_conf >= RESEARCH_SHORT_CIRCUIT_MIN_CONF
    ):
        is_short_circuit = True
        short_circuit_kind = ambiguity_guard
        budget_tier = "zero_tool"
        reason_codes.append(f"BOUNDARY_REFUSAL_{ambiguity_guard.upper()}")
    else:
        if domain_class in _SHORT_CIRCUIT_DOMAIN_CLASSES:
            reason_codes.append("LOW_CONFIDENCE_FAIL_OPEN_TO_RESEARCH")
            domain_class = "stock_research_data"
        else:
            reason_codes.append("STOCK_RESEARCH_DATA")
        has_active_tickers = bool(
            session_state
            and (
                session_state.get("active_tickers")
                or session_state.get("active_episode_tickers")
            )
        )
        if task_action == "clarify_request" and (
            ambiguity_guard == "clear" or has_active_tickers or len(query.strip()) >= 40
        ):
            task_action = "deep_moat_thesis" if is_moat else "fact_lookup"
            if ambiguity_guard == "underspecified_ask_clarify" and has_active_tickers:
                ambiguity_guard = "clear"
            reason_codes.append("NORMALIZED_CLARIFY_TO_RESEARCH")
        if budget_tier == "zero_tool":
            budget_tier = "standard_2_3_steps"
        if is_moat and budget_tier in {"light_1_2_steps", "standard_2_3_steps"}:
            budget_tier = "deep_4_6_steps"

    attach_disclaimer = ambiguity_guard == "investment_advice_disclaimer"
    max_turns = 0 if is_short_circuit else budget_tier_to_max_turns(budget_tier, chat_mode=chat_mode)
    return {
        "query": query,
        "domain_class": domain_class,
        "task_action": task_action,
        "evidence_source_hint": evidence_hint,
        "reasoning_budget_tier": budget_tier,
        "temporal_scope": temporal_scope,
        "ambiguity_and_guard": ambiguity_guard,
        "output_style": output_style,
        "memory_recall_need": memory_recall_need,
        "max_agent_turns": max_turns,
        "is_short_circuit": is_short_circuit,
        "short_circuit_kind": short_circuit_kind,
        "eval_eligible": not is_short_circuit,
        "attach_investment_disclaimer": attach_disclaimer,
        "need_retrieval": not is_short_circuit,
        "confidence": domain_conf,
        "axis_confidences": {
            "domain_class": domain_conf,
            "task_action": task_conf,
            "evidence_source_hint": evidence_conf,
            "reasoning_budget_tier": budget_conf,
            "temporal_scope": temporal_conf,
            "ambiguity_and_guard": guard_conf,
            "output_style": style_conf,
            "memory_recall_need": mem_conf,
        },
        "latency_ms": latency_ms,
        "model": model,
        "fallback_used": False,
        "fallback_reason": None,
        "reason_codes": reason_codes,
    }


async def aroute_research_query(
    query: str,
    *,
    chat_mode: str = "GENERAL",
    session_state: Mapping[str, Any] | None = None,
    http_client: httpx.AsyncClient | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Async non-blocking 7-axis SiliconFlow `Kev-4b` Decision Gate for `/research-chat`.

    Never uses regex overrides; fails open safely into the Unified Tool-Calling Agent on error
    or low confidence (`< 0.78`).
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return {
            "query": query,
            "domain_class": "chitchat_casual",
            "task_action": "direct_reply",
            "evidence_source_hint": "none",
            "reasoning_budget_tier": "zero_tool",
            "temporal_scope": "atemporal",
            "ambiguity_and_guard": "clear",
            "output_style": "conversational_short",
            "memory_recall_need": "none",
            "max_agent_turns": 0,
            "is_short_circuit": True,
            "short_circuit_kind": "chitchat_casual",
            "eval_eligible": False,
            "attach_investment_disclaimer": False,
            "need_retrieval": False,
            "confidence": 1.0,
            "axis_confidences": {"domain_class": 1.0},
            "latency_ms": 0.0,
            "model": "rule",
            "fallback_used": False,
            "fallback_reason": None,
            "reason_codes": ["EMPTY_QUERY"],
        }

    if settings.decision_provider == "disabled":
        return _research_fail_open_route(
            cleaned,
            chat_mode=chat_mode,
            reason="decision_router_disabled",
            session_state=session_state,
        )

    if not settings.siliconflow_api_key:
        return _research_fail_open_route(
            cleaned,
            chat_mode=chat_mode,
            reason="siliconflow_api_key_missing",
            session_state=session_state,
        )

    active_tickers: tuple[str, ...] = ()
    has_prior_turns = False
    last_turn_summary = ""
    has_user_memories = False
    state: dict[str, Any] = {
        "query": cleaned,
        "chat_mode": (chat_mode or "GENERAL").strip().upper(),
    }
    if session_state:
        raw_tks = session_state.get("active_episode_tickers") or session_state.get("active_tickers") or ()
        active_tickers = tuple(
            sorted({str(t).strip().upper() for t in raw_tks if str(t).strip()})[:8]
        )
        has_prior_turns = bool(session_state.get("has_prior_turns"))
        has_user_memories = bool(session_state.get("has_user_memories"))
        if active_tickers:
            state["active_tickers"] = list(active_tickers)
        if session_state.get("all_session_tickers"):
            state["session_tickers"] = list(session_state["all_session_tickers"])[:12]
        if has_prior_turns:
            state["has_prior_turns"] = True
        if has_user_memories:
            state["has_user_memories"] = True
        if session_state.get("last_turn_summary"):
            last_turn_summary = str(session_state["last_turn_summary"])[:240]
            state["last_turn_summary"] = last_turn_summary

    start_time = time.perf_counter()
    cache_key = _research_cache_key(
        model=settings.decision_model,
        query=cleaned,
        chat_mode=chat_mode,
        active_tickers=active_tickers,
        has_prior_turns=has_prior_turns,
        last_turn_summary=last_turn_summary,
        has_user_memories=has_user_memories,
    )
    use_cache = http_client is None and settings.siliconflow_api_key != "sf-test-key"
    if use_cache:
        cached = _get_cached_research_route(cache_key)
        if cached is not None:
            cached_answers, cached_model = cached
            try:
                from sag_api.core.token_usage import record_llm_cache_hit

                record_llm_cache_hit(model=cached_model, count=1, component="research_decision_router")
            except Exception:  # noqa: BLE001
                pass
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return _research_route_from_answers(
                cleaned,
                cached_answers,
                chat_mode=chat_mode,
                model=cached_model,
                latency_ms=latency_ms,
                session_state=session_state,
            )

    url = f"{settings.siliconflow_base_url.rstrip('/')}/systemone"
    headers = {
        "Authorization": f"Bearer {settings.siliconflow_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.decision_model,
        "state": state,
        "questions": _RESEARCH_DECISION_QUESTIONS,
    }
    eff_timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else getattr(settings, "decision_timeout_seconds", 4.5)
    )
    timeout = httpx.Timeout(eff_timeout, connect=min(2.5, eff_timeout))

    try:
        if http_client is not None:
            response = await http_client.post(url, headers=headers, json=body, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        payload = response.json()
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            return _research_fail_open_route(
                cleaned,
                chat_mode=chat_mode,
                reason="siliconflow_invalid_response",
                latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
                session_state=session_state,
            )
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        resolved_model = str(payload.get("model") or settings.decision_model)
        _record_decision_token_usage(resolved_model, payload.get("usage"), cleaned)
        if use_cache:
            _put_cached_research_route(cache_key, answers, resolved_model)
        return _research_route_from_answers(
            cleaned,
            answers,
            chat_mode=chat_mode,
            model=resolved_model,
            latency_ms=latency_ms,
            session_state=session_state,
        )
    except httpx.HTTPStatusError as exc:
        log.warning("SiliconFlow research decision gate failed (HTTP %d)", exc.response.status_code)
        return _research_fail_open_route(
            cleaned,
            chat_mode=chat_mode,
            reason=f"siliconflow_http_{exc.response.status_code}",
            latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
            session_state=session_state,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("SiliconFlow research decision gate failed (%s)", type(exc).__name__)
        return _research_fail_open_route(
            cleaned,
            chat_mode=chat_mode,
            reason="siliconflow_request_failed",
            latency_ms=round((time.perf_counter() - start_time) * 1000, 2),
            session_state=session_state,
        )


def _get_cached_decision_answers(model: str, query_text: str) -> tuple[dict[str, Any], str] | None:
    key = (model, query_text)
    with _DECISION_ROUTE_CACHE_LOCK:
        hit = _DECISION_ROUTE_CACHE.get(key)
        if hit is not None:
            _DECISION_ROUTE_CACHE.move_to_end(key)
        return hit


def _put_cached_decision_answers(model: str, query_text: str, answers: dict[str, Any], resolved_model: str) -> None:
    key = (model, query_text)
    with _DECISION_ROUTE_CACHE_LOCK:
        _DECISION_ROUTE_CACHE[key] = (dict(answers), resolved_model)
        _DECISION_ROUTE_CACHE.move_to_end(key)
        while len(_DECISION_ROUTE_CACHE) > _DECISION_ROUTE_CACHE_MAX:
            _DECISION_ROUTE_CACHE.popitem(last=False)


def _record_decision_token_usage(model: str, raw_usage: Any, query_text: str) -> None:
    try:
        from sag_api.core.token_usage import extract_chat_usage, record_llm_usage

        usage = extract_chat_usage(
            raw_usage,
            messages=[{"role": "user", "content": query_text}],
        )
        record_llm_usage(
            model=model,
            usage=usage,
            component="decision_router",
        )
    except Exception:  # noqa: BLE001
        pass


_ORIG_HTTPX_POST = httpx.post


def route_query(query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify with the selected optional provider; failures keep retrieval enabled."""
    cleaned = query.strip()
    if not cleaned:
        return {
            "query": query,
            "coarse_intent": "CHAT",
            "is_chitchat": True,
            "need_retrieval": False,
            "suggested_strategy": "vector",
            "domain": "general",
            "memory_scope": "none",
            "temporal_mode": "atemporal",
            "answer_shape": "fact_answer",
            "confidence": 1.0,
            "latency_ms": 0.0,
            "model": "rule",
            "fallback_used": False,
            "fallback_reason": None,
            "reason_codes": ["EMPTY_QUERY"],
        }

    if settings.decision_provider == "disabled":
        return _fallback_route(query, "decision_router_disabled")

    state: dict[str, Any] = {"query": cleaned}
    if context:
        state.update({key: value for key, value in context.items() if key != "query"})

    if not settings.siliconflow_api_key:
        return _fallback_route(query, "siliconflow_api_key_missing")

    start_time = time.perf_counter()
    use_in_memory_cache = (
        httpx.post is _ORIG_HTTPX_POST
        and not context
        and settings.siliconflow_api_key != "sf-test-key"
    )
    if use_in_memory_cache:
        cached = _get_cached_decision_answers(settings.decision_model, cleaned)
        if cached is not None:
            cached_answers, cached_model = cached
            try:
                from sag_api.core.token_usage import record_llm_cache_hit

                record_llm_cache_hit(model=cached_model, count=1, component="decision_router")
            except Exception:  # noqa: BLE001
                pass
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return _route_from_answers(query, cached_answers, cached_model, latency_ms, "siliconflow")

    try:
        response = httpx.post(
            f"{settings.siliconflow_base_url.rstrip('/')}/systemone",
            headers={
                "Authorization": f"Bearer {settings.siliconflow_api_key}",
                "Content-Type": "application/json",
            },
            json={"model": settings.decision_model, "state": state, "questions": _DECISION_QUESTIONS},
            timeout=httpx.Timeout(10.0, connect=3.0),
        )
        response.raise_for_status()
        payload = response.json()
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            return _fallback_route(query, "siliconflow_invalid_response")
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        model = str(payload.get("model") or settings.decision_model)
        _record_decision_token_usage(model, payload.get("usage"), cleaned)
        if use_in_memory_cache:
            _put_cached_decision_answers(settings.decision_model, cleaned, answers, model)
        return _route_from_answers(query, answers, model, latency_ms, "siliconflow")
    except httpx.HTTPStatusError as exc:
        # Status codes help diagnose provider configuration without exposing
        # the response body or request credentials in application logs.
        log.warning("SiliconFlow Decision request failed (HTTP %d)", exc.response.status_code)
        return _fallback_route(query, "siliconflow_request_failed")
    except Exception as exc:  # noqa: BLE001 - provider failure must not block retrieval
        log.warning("SiliconFlow Decision request failed (%s)", type(exc).__name__)
        return _fallback_route(query, "siliconflow_request_failed")
