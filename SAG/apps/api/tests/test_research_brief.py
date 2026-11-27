"""Rule 08 L1 + L2 tests for `sag_api.research.brief` (Components 1 & 2: Brief + Deterministic Validator)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

from sag_agent.types import CancellationToken, ModelChunk, ModelRequest, Usage
from sag_api.research.brief import (
    create_and_validate_brief,
    validate_research_brief,
)

AS_OF = date(2026, 11, 26)
PROFILES = (
    {"ticker": "FRT", "company_name": "FPT Retail", "sector": "Bán lẻ", "industry": "Bán lẻ"},
    {"ticker": "VHM", "company_name": "Vinhomes", "sector": "Bất động sản", "industry": "Bất động sản"},
    {"ticker": "ACB", "company_name": "Ngân hàng Á Châu", "sector": "Tài chính", "industry": "Ngân hàng"},
    {"ticker": "MBB", "company_name": "MBBank", "sector": "Tài chính", "industry": "Ngân hàng"},
)


def test_validate_brief_canonicalizes_synonyms_clamps_pit_and_acl() -> None:
    raw = {
        "user_concern": "Người hỏi lo FRT vay ngắn hạn ôm tồn kho",
        "answer_shape": "verdict_with_reasons",
        "entities": {"tickers": ["FRT", "SECRET"], "anchor_ticker": None, "candidate_search": None},
        "premises_to_check": [],
        "claims": [
            {
                "id": "K1",
                "statement": "Nợ vay ngắn hạn của FRT ở mức rủi ro so với vốn chủ và dòng tiền",
                "evidence": ["fact:st_debt", "fact:equity", "fact:cfo", "kpi:de", "risk"],
                "period": "Q4/2026",  # Future relative to 2026-11-26 -> must clamp to Q3/2026!
            },
            {
                "id": "K2",
                "statement": "Mục đích vay và lãi suất vay ngắn hạn của FRT",
                "evidence": ["doc:QUARTER|vay ngắn hạn lãi suất"],
                "period": "FY2026",  # Unfinished FY -> must clamp to FY2025!
            },
        ],
        "ambiguities": [],
        "budget_tier": "standard",
    }
    out = validate_research_brief(
        raw,
        as_of_date=AS_OF,
        allowed_project_ids=frozenset({"FRT", "ACB"}),
        catalog_profiles=PROFILES,
    )
    assert out.ok is True
    assert out.brief is not None
    assert out.brief.entities.tickers == ["FRT"]
    assert out.denied_tickers == ["SECRET"]
    assert out.brief.claims[0].evidence == [
        "fact:st_borrowings",
        "fact:equity",
        "fact:cfo",
        "kpi:debt_to_equity",
        "risk",
    ]
    assert out.brief.claims[0].period == "Q3/2026"
    assert out.brief.claims[1].evidence == ["doc:QUARTER_NOTES|vay ngắn hạn lãi suất"]
    assert out.brief.claims[1].period == "FY2025"
    assert len(out.pit_adjustments) == 2


def test_validate_brief_accepts_parameterized_screen_and_kg_specs() -> None:
    raw = {
        "user_concern": "Tìm cổ phiếu trên sàn đang rẻ so với tiền mặt ròng",
        "answer_shape": "candidate_ranking",
        "entities": {
            "tickers": [],
            "anchor_ticker": None,
            "candidate_search": {
                "sectors": [],
                "criteria": [{"metric": "net_cash_to_market_cap", "op": ">=", "value": 0.25, "ref": "literal"}],
                "sort_by": "net_cash_to_market_cap",
                "sort_dir": "desc",
                "limit": 5,
            },
        },
        "claims": [
            {
                "id": "K1",
                "statement": "Lọc các mã có tiền mặt ròng / vốn hóa >= 25%",
                "evidence": ["screen:net_cash_to_market_cap>=0.25, sort_by:net_cash_to_market_cap desc", "kg:lãi suất"],
            }
        ],
    }
    out = validate_research_brief(raw, as_of_date=AS_OF, catalog_profiles=PROFILES)
    assert out.ok is True
    assert out.brief is not None
    assert out.brief.claims[0].evidence == ["screen", "kg:lãi suất"]


def test_validate_brief_rejects_anchor_without_candidate_search_and_repairs_on_retry() -> None:
    bad_anchor = {
        "user_concern": "Tìm ngân hàng an toàn như ACB mà tăng trưởng tốt hơn",
        "answer_shape": "candidate_ranking",
        "entities": {"tickers": ["ACB"], "anchor_ticker": "ACB", "candidate_search": None},
        "claims": [{"id": "K1", "statement": "ACB an toàn", "evidence": ["fact:npl"]}],
    }
    out = validate_research_brief(bad_anchor, as_of_date=AS_OF, catalog_profiles=PROFILES)
    assert out.ok is False
    assert any("anchor_ticker=ACB" in err for err in out.errors)


def test_validate_brief_never_asks_clarification_when_ticker_present_and_asks_when_purely_ambiguous() -> None:
    # 1) "VHM quý này ổn không?" -> has concrete ticker VHM; even if model sets needs_clarification=True,
    #    deterministic validator forces needs_clarification=False!
    vhm_raw = {
        "user_concern": "Kiểm tra sức khỏe tài chính VHM quý gần nhất",
        "answer_shape": "health_check",
        "entities": {"tickers": ["VHM"]},
        "claims": [{"id": "K1", "statement": "LNST và dòng tiền VHM", "evidence": ["fact:npat", "risk"]}],
        "ambiguities": ["ổn về ngắn hạn hay dài hạn"],
        "needs_clarification": True,
        "clarifying_question": "Bạn muốn hỏi ngắn hạn hay dài hạn?",
    }
    out_vhm = validate_research_brief(vhm_raw, as_of_date=AS_OF, catalog_profiles=PROFILES)
    assert out_vhm.ok is True
    assert out_vhm.brief is not None
    assert out_vhm.brief.needs_clarification is False
    assert out_vhm.brief.clarifying_question is None

    # 2) "Cổ phiếu nào tốt?" -> 0 tickers, 0 candidate criteria, ambiguities populated -> needs_clarification=True
    vague_raw = {
        "user_concern": "Người hỏi tìm cổ phiếu tốt nhưng chưa nêu tiêu chí",
        "answer_shape": "clarification_needed",
        "entities": {"tickers": [], "anchor_ticker": None, "candidate_search": None},
        "claims": [],
        "ambiguities": ["tốt theo tăng trưởng lợi nhuận, định giá rẻ hay cổ tức tiền mặt"],
        "needs_clarification": True,
    }
    out_vague = validate_research_brief(vague_raw, as_of_date=AS_OF, catalog_profiles=PROFILES)
    assert out_vague.ok is True
    assert out_vague.brief is not None
    assert out_vague.brief.needs_clarification is True
    assert out_vague.brief.clarifying_question is not None


async def test_create_and_validate_brief_retries_once_on_invalid_json_and_tracks_usage() -> None:
    class _TwoStepBriefModel:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            self.calls += 1
            if self.calls == 1:
                bad_json = (
                    '{"user_concern": "Tìm ngân hàng như ACB", '
                    '"entities": {"anchor_ticker": "ACB"}, "claims": []}'
                )
                yield ModelChunk(
                    text_delta=bad_json,
                    usage=Usage(input_tokens=800, output_tokens=60, cached_tokens=400),
                )
                return
            yield ModelChunk(
                text_delta="""```json
                {
                  "user_concern": "Tìm ngân hàng an toàn như ACB nhưng tăng trưởng LNST cao hơn",
                  "answer_shape": "candidate_ranking",
                  "entities": {
                    "tickers": ["ACB"],
                    "anchor_ticker": "ACB",
                    "candidate_search": {
                      "sectors": ["Ngân hàng"],
                      "template": "BANK",
                      "criteria": [
                        {"metric": "npl", "op": "<=", "ref": "anchor", "tolerance": 0.004},
                        {"metric": "profit_growth", "op": ">", "ref": "anchor"}
                      ],
                      "sort_by": "profit_growth",
                      "sort_dir": "desc",
                      "limit": 4
                    }
                  },
                  "premises_to_check": [],
                  "claims": [
                    {
                      "id": "K1",
                      "statement": "Đo chuẩn an toàn và tăng trưởng của ACB",
                      "evidence": ["fact:npl", "kpi:npat_growth_yoy"]
                    },
                    {
                      "id": "K2",
                      "statement": "Đối chiếu các ngân hàng ứng viên vượt tăng trưởng ACB",
                      "evidence": ["peers", "risk"]
                    }
                  ]
                }
                ```""",
                usage=Usage(input_tokens=950, output_tokens=180, cached_tokens=700),
            )

    model = _TwoStepBriefModel()
    brief, outcome, usage = await create_and_validate_brief(
        query="Tìm ngân hàng an toàn như ACB mà tăng trưởng tốt hơn",
        model=model,
        as_of_date=AS_OF,
        catalog_profiles=PROFILES,
    )
    assert model.calls == 2
    assert outcome.ok is True
    assert brief.entities.anchor_ticker == "ACB"
    assert brief.entities.candidate_search is not None
    assert brief.entities.candidate_search.criteria[0].metric == "vr_npl_ratio"
    assert brief.entities.candidate_search.criteria[1].metric == "npat_growth_yoy"
    assert usage.requests == 2
    assert usage.input_tokens == 1750
    assert usage.output_tokens == 240
    assert usage.cached_tokens == 1100


def test_validate_brief_caps_doc_specs_per_claim_and_declares_dropped_specs() -> None:
    raw = {
        "user_concern": "VHM quý này ổn không",
        "answer_shape": "health_check",
        "entities": {"tickers": ["VHM"]},
        "claims": [
            {
                "id": "K4",
                "statement": "Khoản mục bất thường trong thuyết minh",
                "evidence": [
                    "doc:QUARTER_NOTES|giao dịch bên liên quan",
                    "doc:QUARTER_NOTES|vay ngắn hạn lãi suất",
                    "doc:QUARTER_NOTES|cam kết bảo lãnh",
                    "fact:made_up_metric",
                ],
            }
        ],
    }
    out = validate_research_brief(raw, as_of_date=AS_OF, catalog_profiles=PROFILES)
    assert out.ok is True
    assert out.brief is not None
    assert [e for e in out.brief.claims[0].evidence if e.startswith("doc:")] == [
        "doc:QUARTER_NOTES|giao dịch bên liên quan",
        "doc:QUARTER_NOTES|vay ngắn hạn lãi suất",
    ]
    # The 3rd doc spec and the unknown metric are NEVER dropped silently
    assert any("cam kết bảo lãnh" in d for d in out.dropped_specs)
    assert any("made_up_metric" in d for d in out.dropped_specs)


async def test_brief_fallback_without_tickers_asks_instead_of_inventing_criteria_and_traces_raw_output() -> None:
    class _AlwaysInvalidBriefModel:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            self.calls += 1
            yield ModelChunk(
                text_delta='{"user_concern": "rẻ so với tiền mặt", "claims": [{"id": "K1", "statement": "x", '
                '"evidence": ["screen_by:net_cash>=0.25"]}]}',
                usage=Usage(input_tokens=900, output_tokens=50, cached_tokens=800),
            )

    events: list[tuple[str, dict]] = []
    brief, outcome, usage = await create_and_validate_brief(
        query="Cổ phiếu nào trên sàn đang rẻ so với tiền mặt?",
        model=_AlwaysInvalidBriefModel(),
        as_of_date=AS_OF,
        catalog_profiles=PROFILES,
        trace=lambda stage, payload: events.append((stage, payload)),
    )
    assert usage.requests == 2
    assert outcome.fallback_used is True
    assert outcome.fallback_mode == "clarification"
    # Must NOT silently substitute a different question (old behavior: ROE >= 12% screen)
    assert brief.needs_clarification is True
    assert brief.entities.candidate_search is None
    assert brief.claims == []
    stages = [s for s, _ in events]
    assert stages == ["brief.request", "brief.attempt", "brief.attempt", "brief.final"]
    first_attempt = events[1][1]
    assert "screen_by:net_cash>=0.25" in first_attempt["raw_output"]
    assert first_attempt["validator_ok"] is False
    assert first_attempt["raw_parsed"]["user_concern"] == "rẻ so với tiền mặt"
    assert events[3][1]["source"] == "fallback:clarification"


def test_validate_brief_pure_candidate_search_auto_adds_screen_claim_and_declares_invalid_template() -> None:
    # Mirrors real C4 Attempt 1 from DeepSeek-V4-Flash: valid candidate_search, empty claims, invalid template
    raw = {
        "user_concern": "Tìm cổ phiếu có giá trị tiền mặt lớn so với vốn hóa thị trường",
        "answer_shape": "candidate_ranking",
        "entities": {
            "tickers": [],
            "anchor_ticker": None,
            "candidate_search": {
                "sectors": [],
                "template": "CASH_RICH",
                "criteria": [
                    {"metric": "net_cash_to_market_cap", "op": ">=", "value": 0.25, "ref": "literal", "tolerance": 0.0}
                ],
                "sort_by": "net_cash_to_market_cap",
                "sort_dir": "desc",
                "limit": 10,
            },
        },
        "premises_to_check": [],
        "claims": [],
        "ambiguities": [],
    }
    out = validate_research_brief(raw, as_of_date=AS_OF, catalog_profiles=PROFILES)
    assert out.ok is True
    assert out.brief is not None
    assert len(out.brief.claims) == 1
    assert out.brief.claims[0].id == "K1"
    assert out.brief.claims[0].evidence == ["screen"]
    assert out.brief.entities.candidate_search is not None
    assert out.brief.entities.candidate_search.template is None
    assert any("CASH_RICH" in d for d in out.dropped_specs)


def test_validate_brief_preserves_up_to_8_evidence_specs_per_claim() -> None:
    raw = {
        "user_concern": "Đánh giá sức khỏe tài chính VHM Q3/2026",
        "answer_shape": "health_check",
        "entities": {"tickers": ["VHM"]},
        "claims": [
            {
                "id": "K2",
                "statement": "Chất lượng lợi nhuận và rủi ro bảng cân đối",
                "evidence": [
                    "fact:cfo",
                    "fact:inventory",
                    "fact:st_receivables",
                    "kpi:cfo_to_npat",
                    "kpi:inventory_growth_yoy",
                    "kpi:receivables_growth_yoy",
                    "kpi:st_debt_to_liquid_assets",
                    "risk",
                    "fact:equity",  # 9th -> dropped and declared
                ],
                "period": "Q3/2026",
            }
        ],
    }
    out = validate_research_brief(raw, as_of_date=AS_OF, catalog_profiles=PROFILES)
    assert out.ok is True
    assert out.brief is not None
    assert len(out.brief.claims[0].evidence) == 8
    assert "kpi:receivables_growth_yoy" in out.brief.claims[0].evidence
    assert "kpi:st_debt_to_liquid_assets" in out.brief.claims[0].evidence
    assert "risk" in out.brief.claims[0].evidence
    assert any("fact:equity" in d for d in out.dropped_specs)


async def test_antigravity_verbatim_prefix_compaction_and_dropped_by_claim_tracking() -> None:
    import json as _json

    from sag_api.research.session_memory import (
        build_conversation_prefix_messages,
        build_financial_zeromem_ledger,
    )

    history = [
        {
            "role": "user",
            "content": "Phân tích chi tiết mã FPT giúp mình, chú ý câu chuyện hợp nhất công ty con.",
            "turn_as_of_date": "2026-11-26",
            "primary_ticker": "FPT",
        },
        {
            "role": "assistant",
            "content": (
                "FPT Q3/2026 có doanh thu hợp nhất giảm 20,05% do ngừng hợp nhất FTEL, "
                "nhưng tương đương tăng 12,1%."
            ),
            "turn_as_of_date": "2026-11-26",
            "primary_ticker": "FPT",
            "expanded_tickers": ["FPT"],
            "user_concern": "Phân tích chi tiết FPT và biến động hợp nhất FTEL",
            "answer_shape": "health_check",
        },
        {
            "role": "user",
            "content": (
                "Có nên đầu tư vào FPT không? Mình thích doanh nghiệp trả cổ tức tiền mặt "
                "đều như đồng hồ Thụy Sĩ."
            ),
            "turn_as_of_date": "2026-11-26",
        },
        {
            "role": "assistant",
            "content": (
                "Để đánh giá phù hợp nhất cho FPT, bạn cho mình biết: "
                "(1) khẩu vị biến động, (2) kỳ vọng sinh lời, (3) thời gian nắm giữ?"
            ),
            "turn_as_of_date": "2026-11-26",
            "primary_ticker": "FPT",
            "needs_clarification": True,
            "clarifying_question": "Bạn cho mình biết khẩu vị biến động, kỳ vọng sinh lời và thời gian nắm giữ?",
        },
        {
            "role": "user",
            "content": "Tương lai Q4/2027 ra sao?",
            "turn_as_of_date": "2027-12-31",
        },
        {
            "role": "assistant",
            "content": "Số liệu tương lai Q4/2027 tuyệt mật 999999 tỷ đồng.",
            "turn_as_of_date": "2027-12-31",
            "primary_ticker": "FPT",
        },
    ]
    ledger = build_financial_zeromem_ledger(history, request_as_of_date=AS_OF)
    assert ledger.quarantined_future_turns == 1

    # 1) Under 16k budget -> 100% verbatim intact pairs, 0 future PIT leakage, unusual Swiss-watch nuance preserved!
    prefix_msgs, summary_txt, refreshed = build_conversation_prefix_messages(
        ledger,
        verbatim_token_budget=16_000,
    )
    assert summary_txt is None and refreshed is False
    assert [m.role for m in prefix_msgs] == ["user", "assistant", "user", "assistant"]
    joined_prefix = "\n".join(m.content for m in prefix_msgs)
    assert "đồng hồ Thụy Sĩ" in joined_prefix
    assert "999999" not in joined_prefix

    # 2) Over tight budget -> compacts older turns into narrative checkpoint summary while keeping recent turn verbatim
    compact_msgs, rolling_sum, did_compact = build_conversation_prefix_messages(
        ledger,
        verbatim_token_budget=90,
        keep_recent_token_budget=60,
    )
    assert did_compact is True and rolling_sum is not None
    assert "CONVERSATION_CHECKPOINT_SUMMARY" in compact_msgs[0].content
    assert "FTEL" in rolling_sum
    # Raw lossless transcript recall still finds Turn 1 verbatim!
    recalled = ledger.recall(query_terms=["FTEL"])
    assert any(r["turn_index"] == 1 and "12,1%" in r["assistant_excerpt"] for r in recalled["recalled_turns"])

    # 3) create_and_validate_brief places [system, *history_messages, current_user] as immutable prefix
    captured_requests: list[ModelRequest] = []

    class _RecordingBriefModel:
        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del cancellation
            captured_requests.append(request)
            yield ModelChunk(
                text_delta=_json.dumps(
                    {
                        "user_concern": "Đánh giá FPT theo khẩu vị biến động thấp, kỳ vọng 10-15%/năm, tích sản >5 năm",
                        "answer_shape": "verdict_with_reasons",
                        "entities": {"tickers": ["FRT"], "anchor_ticker": None, "candidate_search": None},
                        "premises_to_check": [],
                        "claims": [
                            {
                                "id": "K1",
                                "statement": "Độ biến động beta và rủi ro tài chính",
                                "evidence": ["fact:vr_beta", "risk"],
                                "period": "Q3/2026",
                            }
                        ],
                        "budget_tier": "standard",
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(input_tokens=400, output_tokens=80, cached_tokens=320),
            )

    brief, val_out, usage = await create_and_validate_brief(
        query="Biến động thấp, kỳ vọng 10-15% 1 năm, tích sản trên 5 năm",
        model=_RecordingBriefModel(),
        as_of_date=AS_OF,
        catalog_profiles=PROFILES,
        history_messages=prefix_msgs,
    )
    assert len(captured_requests) == 1
    sent_msgs = captured_requests[0].messages
    assert sent_msgs[0].role == "system"
    assert [m.role for m in sent_msgs[1:-1]] == ["user", "assistant", "user", "assistant"]
    assert sent_msgs[-1].role == "user"
    assert "Biến động thấp" in sent_msgs[-1].content
    assert usage.cached_tokens == 320
    assert brief.claims[0].evidence == ["risk"]
    assert val_out.dropped_by_claim_id.get("K1") and any("vr_beta" in d for d in val_out.dropped_by_claim_id["K1"])

