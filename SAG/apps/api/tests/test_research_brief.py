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
