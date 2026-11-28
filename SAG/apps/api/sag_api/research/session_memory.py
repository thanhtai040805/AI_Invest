"""PIT-Safe Financial Zero-Mem Ledger for `/research-chat` (0-LLM-Token, 0-Regex).

Replaces the removed legacy IT-standup `session_context/` module for Stock Research Chat with:
1. **Cross-Turn PIT Quarantine**: Any prior turn whose `turn_as_of_date > request_as_of_date`
   (or missing `turn_as_of_date` during a historical backtest) has its Assistant answer and
   quantitative metadata completely quarantined (`0%` future leakage into prompts or tools).
2. **0-Token Deterministic Structured State**: Builds `active_episode_tickers`,
   `all_session_tickers`, `last_screening_leaderboard`, and compact `episode_fact_cards`
   strictly from verified Tool/BFF metadata of prior PIT-valid turns — never marking older
   audited ticker analyses as `SUPERSEDED`.
3. **Pair-Preserving Recent Window**: Keeps the last `2..3` PIT-valid `(user, assistant)`
   pairs strictly intact within a bounded token budget (never dropping a paired `user` turn
   while leaving an orphaned `assistant` message).
4. **On-Demand Deep Recall**: Supports the external `recall_session_memory` tool so long
   20..30-turn sessions can fetch verbatim earlier turns or screening snapshots without
   bloating the default synthesis context.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

_COREFERENCE_FOLLOWUP_RE = re.compile(
    r"(?:\b(?:mã\s+đó|mã\s+này|công\s+ty\s+đó|công\s+ty\s+này|doanh\s+nghiệp\s+đó|doanh\s+nghiệp\s+này|"
    r"mã\s+trên|nhóm\s+trên|các\s+mã\s+trên|bảng\s+trên|ở\s+trên|như\s+trên|"
    r"lượt\s+trước|câu\s+trước|phần\s+trước|vừa\s+rồi|vừa\s+nãy|tóm\s+tắt\s+lại|"
    r"dịch\s+lại|viết\s+lại|định\s+dạng\s+lại|rút\s+gọn\s+lại|so\s+với\s+nó|"
    r"của\s+nó|chi\s+tiết\s+hơn|giải\s+thích\s+thêm|tiếp\s+tục|còn\s+thì\s+sao|"
    r"above|previous|earlier|summarize\s+that|translate\s+that|follow\s*up)\b)",
    re.IGNORECASE | re.UNICODE,
)
_INLINE_TICKER_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{3})(?![A-Za-z0-9_])")
_NON_TICKER_CAP_WORDS = frozenset(
    {
        "VND", "USD", "EUR", "GDP", "CPI", "ROE", "ROA", "NIM", "CIR", "CAR", "CASA",
        "NPL", "LDR", "CFO", "CFI", "CFF", "FCF", "EPS", "BPS", "YOY", "QOQ", "MOM",
        "CAGR", "EBIT", "EBT", "PBT", "PAT", "VAS", "IFRS", "BCTC", "BCTN", "LCTT",
        "KQKD", "CDKT", "PIT", "HOSE", "HNX", "VSD", "SSC", "SBV", "FED", "ADB",
        "IMF", "ESG", "MOAT", "AGM", "SSI", "VPS", "TOP", "MAX", "MIN", "AVG", "NAV",
        "TTM", "LTM", "YTD", "MTD", "HRC", "LNG", "FDI", "PMI", "BĐS", "BDS",
    }
)


def _coerce_iso_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        cleaned = raw.strip()[:10]
        if len(cleaned) == 10:
            try:
                return date.fromisoformat(cleaned)
            except ValueError:
                return None
    return None


def _norm_ticker_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        sym = str(item or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _estimate_tokens(text: str) -> int:
    # Conservative Vietnamese/UTF-8 heuristic (~3.2 chars/token)
    return max(1, (len(text) + 2) // 3)


def _compact_assistant_excerpt(content: str, *, max_chars: int = 1200) -> str:
    clean = (content or "").strip()
    if len(clean) <= max_chars:
        return clean
    head_budget = int(max_chars * 0.72)
    tail_budget = max_chars - head_budget - 20
    return f"{clean[:head_budget].rstrip()}\n...[lược bớt]...\n{clean[-tail_budget:].lstrip()}"


def _msg_attr(msg: Any, key: str, default: Any = None) -> Any:
    if isinstance(msg, Mapping):
        return msg.get(key, default)
    return getattr(msg, key, default)


@dataclass(frozen=True)
class SessionTurnRecord:
    turn_index: int
    user_content: str
    assistant_content: str
    turn_as_of_date: date | None
    primary_ticker: str | None
    tickers: tuple[str, ...]
    screened_tickers: tuple[str, ...]
    screening_summary: tuple[dict[str, Any], ...]
    route_intent: str | None
    eval_eligible: bool
    quarantined: bool
    quarantine_reason: str | None = None
    user_concern: str | None = None
    answer_shape: str | None = None
    needs_clarification: bool = False
    clarifying_question: str | None = None
    session_rolling_summary: str | None = None


@dataclass
class FinancialZeroMemLedger:
    """PIT-quarantined, pair-preserving session memory ledger for `/research-chat`."""

    request_as_of_date: date
    valid_turns: list[SessionTurnRecord] = field(default_factory=list)
    quarantined_turns: list[SessionTurnRecord] = field(default_factory=list)
    active_episode_tickers: list[str] = field(default_factory=list)
    all_session_tickers: list[str] = field(default_factory=list)
    last_screening_leaderboard: list[dict[str, Any]] = field(default_factory=list)
    last_screening_turn_index: int | None = None
    recent_pairs: list[SessionTurnRecord] = field(default_factory=list)
    fact_cards: list[dict[str, Any]] = field(default_factory=list)

    @property
    def quarantined_future_turns(self) -> int:
        return len(self.quarantined_turns)

    @property
    def has_prior_turns(self) -> bool:
        return bool(self.valid_turns)

    def to_router_state(self) -> dict[str, Any]:
        """Compact state fed to Tier 0 SiliconFlow `Kev-4b` (0 heavy prose)."""
        last_summary = ""
        if self.valid_turns:
            last = self.valid_turns[-1]
            tks = ",".join(last.tickers) if last.tickers else "none"
            extra = ""
            if last.clarifying_question:
                extra = f" | asked_clarification={last.clarifying_question[:140]}"
            elif last.user_concern:
                extra = f" | concern={last.user_concern[:120]}"
            last_summary = (
                f"Q={last.user_content[:120]} | tickers={tks} | intent={last.route_intent or 'research'}{extra}"
            )
        return {
            "active_tickers": list(self.active_episode_tickers),
            "active_episode_tickers": list(self.active_episode_tickers),
            "all_session_tickers": list(self.all_session_tickers),
            "has_prior_turns": self.has_prior_turns,
            "has_screening_leaderboard": bool(self.last_screening_leaderboard),
            "last_turn_summary": last_summary,
            "quarantined_future_turns": self.quarantined_future_turns,
        }

    def to_prompt_context(
        self,
        *,
        include_recent_pairs: bool = True,
        max_recent_pairs: int | None = None,
    ) -> str:
        """Render bounded, pair-intact, non-authoritative session context block.

        Adaptive Zero-Mem:
        - Always emits the compact State Pointer (`active_episode_tickers`, `all_session_tickers`,
          `turn_count`, `last_screening` summary — ~30-80 tokens).
        - ONLY injects verbatim `recent_pairs` (<= 2 pairs when adaptive) when
          `include_recent_pairs=True` (same-topic follow-up, coreference, or `current_session_only`).
        - On an independent topic/ticker shift (`include_recent_pairs=False`), emits 0 prose tokens
          from old assistant answers while reminding the agent that `recall_session_memory` is
          available on demand.
        """
        return render_session_context_block(
            self,
            include_recent_pairs=include_recent_pairs,
            max_recent_pairs=max_recent_pairs,
        )

    def recall(
        self,
        *,
        tickers: Sequence[str] | None = None,
        query_terms: Sequence[str] | None = None,
        turn_indices: Sequence[int] | None = None,
        include_screening: bool = True,
        limit: int = 4,
    ) -> dict[str, Any]:
        """Deterministic PIT-safe deep recall across ALL valid turns of the current session."""
        wanted_tks = {str(t).strip().upper() for t in (tickers or ()) if str(t).strip()}
        wanted_terms = [str(q).strip().lower() for q in (query_terms or ()) if str(q).strip()]
        wanted_turns = {int(i) for i in (turn_indices or ()) if isinstance(i, int)}

        scored: list[tuple[int, int, SessionTurnRecord]] = []
        for turn in self.valid_turns:
            score = 0
            if wanted_turns and turn.turn_index in wanted_turns:
                score += 10
            if wanted_tks and (wanted_tks & set(turn.tickers) or wanted_tks & set(turn.screened_tickers)):
                score += 5
            if wanted_terms:
                hay = f"{turn.user_content}\n{turn.assistant_content}".lower()
                hits = sum(1 for term in wanted_terms if term in hay)
                score += hits * 2
            if not wanted_tks and not wanted_terms and not wanted_turns:
                score = 1
            if score > 0:
                scored.append((score, turn.turn_index, turn))

        scored.sort(key=lambda item: (-item[0], -item[1]))
        selected = [t for _s, _idx, t in scored[: max(1, min(limit, 8))]]
        selected.sort(key=lambda t: t.turn_index)

        out: dict[str, Any] = {
            "request_as_of_date": self.request_as_of_date.isoformat(),
            "total_valid_turns": len(self.valid_turns),
            "quarantined_future_turns": self.quarantined_future_turns,
            "all_session_tickers": list(self.all_session_tickers),
            "recalled_turns": [
                {
                    "turn_index": t.turn_index,
                    "turn_as_of_date": t.turn_as_of_date.isoformat() if t.turn_as_of_date else None,
                    "tickers": list(t.tickers),
                    "screened_tickers": list(t.screened_tickers),
                    "user_query": t.user_content,
                    "assistant_excerpt": _compact_assistant_excerpt(t.assistant_content, max_chars=2400),
                }
                for t in selected
            ],
        }
        if include_screening and self.last_screening_leaderboard:
            out["last_screening_turn_index"] = self.last_screening_turn_index
            out["last_screening_leaderboard"] = list(self.last_screening_leaderboard[:15])
        return out


def build_financial_zeromem_ledger(
    history: Sequence[Any],
    *,
    request_as_of_date: date,
    reference_today: date | None = None,
    max_recent_pairs: int = 3,
    max_recent_tokens: int = 1600,
) -> FinancialZeroMemLedger:
    """Build PIT-safe `FinancialZeroMemLedger` from BFF-projected chat history (0 LLM calls)."""
    today = reference_today or date.today()
    is_historical_backtest = (today - request_as_of_date).days > 1

    # Step 1: Pair chronological user/assistant messages cleanly (never orphaning an assistant turn)
    paired_raw: list[tuple[Any | None, Any | None]] = []
    pending_user: Any | None = None
    for msg in history or ():
        role = str(_msg_attr(msg, "role", "") or "").strip().lower()
        content = str(_msg_attr(msg, "content", "") or "").strip()
        if not content:
            continue
        if role == "user":
            if pending_user is not None:
                paired_raw.append((pending_user, None))
            pending_user = msg
        elif role == "assistant":
            paired_raw.append((pending_user, msg))
            pending_user = None
    if pending_user is not None:
        paired_raw.append((pending_user, None))

    ledger = FinancialZeroMemLedger(request_as_of_date=request_as_of_date)
    all_tks_seen: set[str] = set()

    for idx, (u_msg, a_msg) in enumerate(paired_raw, start=1):
        u_text = str(_msg_attr(u_msg, "content", "") or "").strip() if u_msg is not None else ""
        a_text = str(_msg_attr(a_msg, "content", "") or "").strip() if a_msg is not None else ""

        turn_as_of = (
            _coerce_iso_date(_msg_attr(a_msg, "turn_as_of_date"))
            if a_msg is not None
            else None
        ) or (
            _coerce_iso_date(_msg_attr(u_msg, "turn_as_of_date"))
            if u_msg is not None
            else None
        )

        quarantined = False
        q_reason: str | None = None
        if turn_as_of is not None and turn_as_of > request_as_of_date:
            quarantined = True
            q_reason = f"turn_as_of_date={turn_as_of.isoformat()} > request_as_of_date={request_as_of_date.isoformat()}"
        elif turn_as_of is None and is_historical_backtest and a_msg is not None:
            quarantined = True
            q_reason = "untagged_historical_turn_quarantined_for_pit_backtest"

        primary_tk = (
            str(_msg_attr(a_msg, "primary_ticker") or "").strip().upper()
            if a_msg is not None
            else ""
        ) or (
            str(_msg_attr(u_msg, "primary_ticker") or "").strip().upper()
            if u_msg is not None
            else ""
        ) or None

        merged_tks = _norm_ticker_list(
            ([primary_tk] if primary_tk else [])
            + list(_msg_attr(a_msg, "expanded_tickers", ()) or () if a_msg is not None else ())
            + list(_msg_attr(a_msg, "cited_tickers", ()) or () if a_msg is not None else ())
            + list(_msg_attr(u_msg, "expanded_tickers", ()) or () if u_msg is not None else ())
        )
        screened_tks = _norm_ticker_list(
            _msg_attr(a_msg, "screened_tickers", ()) if a_msg is not None else ()
        )
        raw_scr_summary = (
            _msg_attr(a_msg, "screening_summary", ()) if a_msg is not None else ()
        ) or ()
        scr_summary = tuple(
            dict(item) for item in raw_scr_summary if isinstance(item, Mapping)
        )[:15]
        if not screened_tks and scr_summary:
            screened_tks = _norm_ticker_list([r.get("ticker") for r in scr_summary])

        route_intent = (
            str(_msg_attr(a_msg, "route_intent") or "").strip() or None
            if a_msg is not None
            else None
        )
        raw_eval_eligible = _msg_attr(a_msg, "eval_eligible") if a_msg is not None else None
        eval_eligible = True if raw_eval_eligible is None else bool(raw_eval_eligible)
        u_concern = (
            str(_msg_attr(a_msg, "user_concern") or "").strip() or None
            if (a_msg is not None and not quarantined)
            else None
        )
        a_shape = (
            str(_msg_attr(a_msg, "answer_shape") or "").strip() or None
            if (a_msg is not None and not quarantined)
            else None
        )
        needs_clar = (
            bool(_msg_attr(a_msg, "needs_clarification"))
            if (a_msg is not None and not quarantined)
            else False
        )
        clar_q = (
            str(_msg_attr(a_msg, "clarifying_question") or "").strip() or None
            if (a_msg is not None and not quarantined)
            else None
        )
        rolling_sum = (
            str(_msg_attr(a_msg, "session_rolling_summary") or "").strip() or None
            if (a_msg is not None and not quarantined)
            else None
        )

        record = SessionTurnRecord(
            turn_index=idx,
            user_content=u_text,
            assistant_content="" if quarantined else a_text,
            turn_as_of_date=turn_as_of,
            primary_ticker=None if quarantined else primary_tk,
            tickers=() if quarantined else tuple(merged_tks),
            screened_tickers=() if quarantined else tuple(screened_tks),
            screening_summary=() if quarantined else scr_summary,
            route_intent=route_intent,
            eval_eligible=eval_eligible,
            quarantined=quarantined,
            quarantine_reason=q_reason,
            user_concern=u_concern,
            answer_shape=a_shape,
            needs_clarification=needs_clar,
            clarifying_question=clar_q,
            session_rolling_summary=rolling_sum,
        )

        if quarantined:
            ledger.quarantined_turns.append(record)
            continue

        ledger.valid_turns.append(record)
        if record.tickers:
            ledger.active_episode_tickers = list(record.tickers)
            for sym in record.tickers:
                if sym not in all_tks_seen:
                    all_tks_seen.add(sym)
                    ledger.all_session_tickers.append(sym)
        if record.screening_summary:
            ledger.last_screening_leaderboard = list(record.screening_summary)
            ledger.last_screening_turn_index = record.turn_index
            for sym in record.screened_tickers:
                if sym not in all_tks_seen:
                    all_tks_seen.add(sym)
                    ledger.all_session_tickers.append(sym)

        lead_source = record.assistant_content.splitlines()[0] if record.assistant_content else ""
        ledger.fact_cards.append(
            {
                "turn_index": record.turn_index,
                "turn_as_of_date": record.turn_as_of_date.isoformat() if record.turn_as_of_date else None,
                "tickers": list(record.tickers),
                "screened_tickers": list(record.screened_tickers),
                "route_intent": record.route_intent,
                "user_query": record.user_content[:180],
                "assistant_lead": lead_source[:220],
            }
        )

    # Step 2: Select recent intact (user, assistant) pairs within `max_recent_tokens`
    selected_recent: list[SessionTurnRecord] = []
    used_tokens = 0
    for turn in reversed(ledger.valid_turns[-max(1, max_recent_pairs) :]):
        pair_text = f"{turn.user_content}\n{_compact_assistant_excerpt(turn.assistant_content, max_chars=1100)}"
        pair_tokens = _estimate_tokens(pair_text)
        if selected_recent and (used_tokens + pair_tokens) > max_recent_tokens:
            break
        selected_recent.append(turn)
        used_tokens += pair_tokens
    selected_recent.reverse()
    ledger.recent_pairs = selected_recent
    return ledger


def render_session_context_block(
    ledger: FinancialZeroMemLedger,
    *,
    include_recent_pairs: bool = True,
    max_recent_pairs: int | None = None,
) -> str:
    """Render Adaptive Zero-Mem session context block.

    When `include_recent_pairs=False` (topic/ticker shift), emits ONLY the compact State Pointer
    (~30-80 tokens) and 0 prose tokens from prior assistant answers to avoid Entity Bleed and
    redundant prefill waste.
    """
    if not ledger.valid_turns and not ledger.quarantined_turns:
        return ""

    if not include_recent_pairs:
        lines: list[str] = [
            "<SESSION_CONTEXT_NON_AUTHORITATIVE_FOR_NEW_NUMBERS>",
            f"- Con trỏ phiên (Adaptive Zero-Mem, {len(ledger.valid_turns)} lượt hợp lệ PIT; "
            "đổi chủ đề/mã mới — dùng `recall_session_memory` nếu cần xem lại lượt cũ).",
        ]
        if ledger.quarantined_turns:
            lines.append(
                f"- Đã cách ly PIT {len(ledger.quarantined_turns)} lượt lịch sử có ngày hiệu lực "
                f"vượt quá as_of_date={ledger.request_as_of_date.isoformat()}."
            )
        if ledger.active_episode_tickers:
            lines.append(f"- active_episode_tickers: {', '.join(ledger.active_episode_tickers)}")
        if ledger.all_session_tickers:
            lines.append(f"- all_session_tickers: {', '.join(ledger.all_session_tickers)}")
        if ledger.last_screening_leaderboard:
            top_items = [
                f"#{idx} {row.get('ticker', '?')}"
                for idx, row in enumerate(ledger.last_screening_leaderboard[:6], 1)
            ]
            lines.append(
                f"- last_screening (Lượt {ledger.last_screening_turn_index}): " + ", ".join(top_items)
            )
        lines.append("</SESSION_CONTEXT_NON_AUTHORITATIVE_FOR_NEW_NUMBERS>")
        return "\n".join(lines)

    lines = [
        "<SESSION_CONTEXT_NON_AUTHORITATIVE_FOR_NEW_NUMBERS>",
        "Ghi chú kỷ luật: Nội dung dưới đây chỉ dùng để hiểu mạch hội thoại, đại từ tham chiếu và "
        "danh sách mã đã xem trong phiên. Ngoại trừ yêu cầu thuần tóm tắt/dịch lại câu trước, "
        "MỌI con số tài chính trong câu trả lời mới BẮT BUỘC phải lấy từ kết quả Tool của lượt hiện tại.",
    ]
    if ledger.quarantined_turns:
        lines.append(
            f"- Đã cách ly PIT {len(ledger.quarantined_turns)} lượt lịch sử có ngày hiệu lực "
            f"vượt quá as_of_date={ledger.request_as_of_date.isoformat()}."
        )
    if ledger.active_episode_tickers:
        active_str = ", ".join(ledger.active_episode_tickers)
        lines.append(f"- Mã đang trao đổi gần nhất (active_episode_tickers): {active_str}")
    if ledger.all_session_tickers:
        all_str = ", ".join(ledger.all_session_tickers)
        lines.append(f"- Toàn bộ mã đã phân tích trong phiên (all_session_tickers): {all_str}")
    if ledger.last_screening_leaderboard:
        top_items = [
            f"#{idx} {row.get('ticker', '?')} (FY{row.get('fiscal_year', '?')})"
            for idx, row in enumerate(ledger.last_screening_leaderboard[:8], 1)
        ]
        lines.append(
            f"- Bảng lọc gần nhất (Lượt {ledger.last_screening_turn_index}): " + "; ".join(top_items)
        )

    pairs_to_emit = list(ledger.recent_pairs)
    if max_recent_pairs is not None:
        pairs_to_emit = pairs_to_emit[-max(0, int(max_recent_pairs)) :]

    recent_indices = {t.turn_index for t in pairs_to_emit}
    older_cards = [c for c in ledger.fact_cards if c["turn_index"] not in recent_indices]
    if older_cards:
        lines.append("\n[Tóm tắt các lượt trước trong phiên — dùng tool `recall_session_memory` nếu cần chi tiết]:")
        for card in older_cards[-10:]:
            tks = ", ".join(card["tickers"]) if card["tickers"] else "không khóa mã"
            u_q = card["user_query"]
            a_lead = card["assistant_lead"]
            lines.append(f'  * Lượt {card["turn_index"]} ({tks}): User hỏi "{u_q}" -> {a_lead}')

    if pairs_to_emit:
        lines.append("\n[Các cặp hỏi-đáp gần nhất hợp lệ PIT]:")
        for turn in pairs_to_emit:
            lines.append(f"  User (Lượt {turn.turn_index}): {turn.user_content}")
            if turn.assistant_content:
                excerpt = _compact_assistant_excerpt(turn.assistant_content, max_chars=1100)
                lines.append(f"  Assistant (Lượt {turn.turn_index}): {excerpt}")

    lines.append("</SESSION_CONTEXT_NON_AUTHORITATIVE_FOR_NEW_NUMBERS>")
    return "\n".join(lines)


def should_include_recent_pairs(
    *,
    query: str,
    ledger: FinancialZeroMemLedger,
    explicit_tickers: Sequence[str] = (),
    query_tickers: Sequence[str] = (),
    route_decision: Mapping[str, Any] | None = None,
) -> bool:
    """Topic-Continuity Gate for Adaptive Zero-Mem.

    Returns True ONLY when the current turn continues the same topic/ticker or depends on
    prior-turn coreference; returns False on an independent topic/ticker shift so 0 prose
    tokens from old answers are injected into the prompt.
    """
    if not ledger.valid_turns:
        return False

    if route_decision:
        if route_decision.get("memory_recall_need") == "current_session_only":
            return True
        if route_decision.get("domain_class") == "reformat_or_recall_chat":
            return True
        if route_decision.get("ambiguity_and_guard") == "needs_coreference_from_memory":
            return True
        if route_decision.get("temporal_scope") == "session_change_lineage":
            return True

    q_clean = (query or "").strip()
    if _COREFERENCE_FOLLOWUP_RE.search(q_clean):
        return True

    req_tickers: set[str] = {
        str(t).strip().upper()
        for t in (*explicit_tickers, *query_tickers)
        if str(t).strip()
    }
    if not req_tickers:
        for m in _INLINE_TICKER_RE.finditer(q_clean):
            cand = m.group(1)
            if cand not in _NON_TICKER_CAP_WORDS:
                req_tickers.add(cand)

    active_set = set(ledger.active_episode_tickers)
    if req_tickers and active_set:
        if req_tickers & active_set:
            return True
        # Disjoint ticker(s) with no coreference marker -> independent topic shift!
        return False

    if req_tickers and not active_set:
        return False

    # Short elliptic query without explicit tickers -> likely follow-up to the last turn
    if len(q_clean) < 48:
        return True

    # Self-contained longer query with no ticker & no coreference -> check lexical overlap with last turn
    last_turn = ledger.valid_turns[-1]
    q_words = {w.lower() for w in re.findall(r"[^\W\d_]{3,}", q_clean, flags=re.UNICODE)}
    prev_words = {w.lower() for w in re.findall(r"[^\W\d_]{3,}", last_turn.user_content, flags=re.UNICODE)}
    return len(q_words & prev_words) >= 2


# Alias preserving both naming conventions
build_financial_zero_mem_ledger = build_financial_zeromem_ledger


def _render_turn_checkpoint_entry(turn: SessionTurnRecord) -> str:
    """Format one older PIT-valid turn into a high-density narrative checkpoint entry (0 loss of intent/nuance)."""
    tks_part = f" [Mã: {', '.join(turn.tickers)}]" if turn.tickers else ""
    scr_part = f" [Lọc: {', '.join(turn.screened_tickers[:8])}]" if turn.screened_tickers else ""
    concern_part = f" | Trọng tâm: {turn.user_concern}" if turn.user_concern else ""
    u_excerpt = _compact_assistant_excerpt(turn.user_content, max_chars=420)
    if turn.clarifying_question:
        a_excerpt = f"Hệ thống hỏi làm rõ: {turn.clarifying_question}"
    else:
        a_excerpt = _compact_assistant_excerpt(turn.assistant_content, max_chars=520)
    return (
        f"- Lượt {turn.turn_index}{tks_part}{scr_part}{concern_part}:\n"
        f"  + Nhà đầu tư: {u_excerpt}\n"
        f"  + Tóm tắt phản hồi: {a_excerpt}"
    )


def build_conversation_prefix_messages(
    ledger: FinancialZeroMemLedger,
    *,
    verbatim_token_budget: int = 16_000,
    keep_recent_token_budget: int = 10_000,
) -> tuple[list[Any], str | None, bool]:
    """Antigravity-style 2-Tier In-Session Conversation Prefix Builder (PIT-Quarantined + KV-Cache Aligned).

    Tier 1 (`total_tokens <= verbatim_token_budget`, ~15–25 full turns):
      Emits 100% intact chronological `(user, assistant)` `AgentMessage` pairs right after `system`.
      Maximizes DeepInfra KV Prefix Cache hits ($0.0144/1M tokens) and preserves 100% of unusual side
      questions, multi-part clarifying questions, subtle constraints, and explicit reversals/pivots.

    Tier 2 (`total_tokens > verbatim_token_budget`, marathon 25–60+ turn sessions):
      Compacts older turns `[1..K]` into a rich `<CONVERSATION_CHECKPOINT_SUMMARY>` (reusing any cached
      `session_rolling_summary` from prior assistant metadata and incrementally appending newly aged-out
      turns), followed by intact verbatim recent turns `[K+1..N]`. Raw verbatim earlier turns remain
      100% retrievable via `ledger.recall(...)`.

    Returns `(prefix_messages, rolling_summary_or_None, summary_was_refreshed)`.
    """
    from sag_agent.types import AgentMessage

    if not ledger.valid_turns:
        return [], None, False

    complete_turns = [t for t in ledger.valid_turns if t.user_content and t.assistant_content]
    if not complete_turns:
        return [], None, False

    turn_tokens = [
        _estimate_tokens(t.user_content) + _estimate_tokens(t.assistant_content) + 8
        for t in complete_turns
    ]
    total_tokens = sum(turn_tokens)

    if total_tokens <= max(1, verbatim_token_budget):
        msgs: list[Any] = []
        for t in complete_turns:
            msgs.append(AgentMessage(role="user", content=t.user_content))
            msgs.append(AgentMessage(role="assistant", content=t.assistant_content))
        return msgs, None, False

    # Exceeds verbatim budget -> split into older_turns [0..split_idx) + recent_turns [split_idx..N)
    effective_recent_budget = max(1, min(keep_recent_token_budget, verbatim_token_budget - 20))
    kept_recent: list[SessionTurnRecord] = []
    used_recent = 0
    for t, tok in zip(reversed(complete_turns), reversed(turn_tokens), strict=True):
        if kept_recent and (used_recent + tok) > effective_recent_budget:
            break
        kept_recent.append(t)
        used_recent += tok
    kept_recent.reverse()
    if len(kept_recent) == len(complete_turns) and len(complete_turns) >= 2:
        kept_recent = complete_turns[-max(1, len(complete_turns) - 1) :]

    split_count = len(complete_turns) - len(kept_recent)
    older_turns = complete_turns[:split_count]

    # Reuse the latest cached rolling summary inside older_turns if available
    cached_base: str | None = None
    cached_idx = -1
    for i in range(len(older_turns) - 1, -1, -1):
        if older_turns[i].session_rolling_summary:
            cached_base = older_turns[i].session_rolling_summary
            cached_idx = i
            break

    delta_turns = older_turns[cached_idx + 1 :]
    refreshed = bool(delta_turns) or cached_base is None
    if cached_base and not delta_turns:
        rolling_summary = cached_base
    else:
        parts: list[str] = []
        if cached_base:
            parts.append(cached_base.strip())
        for t in delta_turns:
            parts.append(_render_turn_checkpoint_entry(t))
        rolling_summary = "\n".join(parts)
        if len(rolling_summary) > 10_000:
            rolling_summary = "...[tóm tắt các lượt đầu]...\n" + rolling_summary[-9_600:]

    header_lines = [
        "<CONVERSATION_CHECKPOINT_SUMMARY>",
        (
            f"Tóm tắt mạch hội thoại các lượt trước (Lượt 1..{older_turns[-1].turn_index}, "
            f"khóa sổ PIT <= {ledger.request_as_of_date.isoformat()})."
        ),
        "Lưu ý kỷ luật: dùng để giữ trọn ngữ cảnh, ràng buộc cá nhân và ý định xuyên suốt phiên; "
        "mọi số liệu tài chính mới ở lượt hiện tại vẫn phải lấy từ kết quả kiểm chứng mới.",
    ]
    if ledger.all_session_tickers:
        header_lines.append(f"Các mã đã trao đổi trong phiên: {', '.join(ledger.all_session_tickers)}")
    header_lines.append(rolling_summary)
    header_lines.append("</CONVERSATION_CHECKPOINT_SUMMARY>")

    out_msgs: list[Any] = [
        AgentMessage(role="user", content="\n".join(header_lines)),
        AgentMessage(
            role="assistant",
            content="Đã ghi nhận đầy đủ tóm tắt lịch sử phiên trò chuyện và các ràng buộc trước đó.",
        ),
    ]
    for t in kept_recent:
        out_msgs.append(AgentMessage(role="user", content=t.user_content))
        out_msgs.append(AgentMessage(role="assistant", content=t.assistant_content))
    return out_msgs, rolling_summary, refreshed


_PRIOR_TURN_BADGE_RE = re.compile(
    r"\[(?:C\d+(?:\s+UNVERIFIED_CLAIM)?|CALC-\d+|TBL:[^\]]+|SCR:[^\]]+|KG:[^\]]+|DISC-\d+|MEM:[^\]]+|PORT:[^\]]+|SESS:[^\]]+)\]"
)
_PRIOR_TURN_METRIC_FIG_RE = re.compile(
    r"[+-]?\d+(?:[.,]\d+)*\s*(?:%|bps|điểm\s+cơ\s+bản|x|lần|tỷ(?:\s*đồng)?|triệu(?:\s*đồng|\s*USD)?|nghìn\s*tỷ|USD)(?![a-zA-ZÀ-ỹ])",
    re.IGNORECASE,
)


def _summarize_assistant_turn_without_badges(turn: SessionTurnRecord, *, max_chars: int = 420) -> str:
    """Distill a prior assistant reply into badge-free, number-free qualitative context for Stage 6.

    Prevents Stage 6 from lazily echoing prior Markdown tables, copying stale `[C#]`/`[CALC-*]` IDs,
    OR repeating un-badged numbers from earlier turns that are not in the current turn's VerdictCards.
    (Stage 1 Brief Planner still receives 100% verbatim `build_conversation_prefix_messages`.)
    """
    if turn.clarifying_question:
        return f"Hệ thống đã hỏi làm rõ: {turn.clarifying_question}"

    kept_lines: list[str] = []
    for raw_ln in (turn.assistant_content or "").splitlines():
        ln = raw_ln.strip()
        if not ln:
            continue
        if ln.startswith("|") or ln.startswith("> ⚠️") or ln.startswith("---"):
            continue
        cleaned = _PRIOR_TURN_BADGE_RE.sub("", ln)
        cleaned = _PRIOR_TURN_METRIC_FIG_RE.sub("[số liệu lượt trước]", cleaned)
        cleaned = re.sub(r"^#{1,4}\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            kept_lines.append(cleaned)
        if sum(len(x) for x in kept_lines) >= max_chars:
            break

    head_meta: list[str] = []
    if turn.primary_ticker:
        head_meta.append(f"Mã chính: {turn.primary_ticker}")
    elif turn.tickers:
        head_meta.append(f"Mã: {', '.join(turn.tickers[:4])}")
    if turn.screened_tickers:
        head_meta.append(f"Ứng viên lọc: {', '.join(turn.screened_tickers[:6])}")
    if turn.user_concern:
        head_meta.append(f"Trọng tâm đã giải quyết: {turn.user_concern}")

    body = " | ".join(kept_lines)[:max_chars].rstrip()
    prefix = f"[Tóm tắt kết luận Lượt {turn.turn_index} ({'; '.join(head_meta)})]: " if head_meta else (
        f"[Tóm tắt kết luận Lượt {turn.turn_index}]: "
    )
    return f"{prefix}{body}" if body else prefix.rstrip(": ")


def build_synthesis_history_messages(
    ledger: FinancialZeroMemLedger,
    *,
    max_recent_turns: int = 6,
) -> list[Any]:
    """Build badge-scrubbed, table-free conversational history specifically for Stage 6 Synthesis.

    - Stage 1 (`create_research_brief`) receives `build_conversation_prefix_messages` (full verbatim).
    - Stage 6 (`synthesize_from_verdict_cards`) receives verbatim `user` queries paired with clean,
      badge-free executive summaries of prior `assistant` replies so it writes a fresh answer from the
      current turn's `<VERIFIED_CLAIM_CARDS>` without echoing old tables or leaking stale `[C11]` IDs.
    """
    from sag_agent.types import AgentMessage

    complete_turns = [t for t in ledger.valid_turns if t.user_content and t.assistant_content]
    if not complete_turns:
        return []

    out: list[Any] = []
    older = complete_turns[:-max_recent_turns] if len(complete_turns) > max_recent_turns else []
    recent = complete_turns[-max_recent_turns:]
    if older:
        older_lines = [
            "<EARLIER_SESSION_TURNS_SUMMARY>",
            *(
                f"- Lượt {t.turn_index}: Hỏi '{_compact_assistant_excerpt(t.user_content, max_chars=180)}' -> "
                f"{_summarize_assistant_turn_without_badges(t, max_chars=220)}"
                for t in older[-12:]
            ),
            "</EARLIER_SESSION_TURNS_SUMMARY>",
        ]
        out.append(AgentMessage(role="user", content="\n".join(older_lines)))
        out.append(AgentMessage(role="assistant", content="Đã nắm tóm tắt các lượt trao đổi đầu phiên."))

    for t in recent:
        out.append(AgentMessage(role="user", content=t.user_content))
        out.append(AgentMessage(role="assistant", content=_summarize_assistant_turn_without_badges(t)))
    return out
