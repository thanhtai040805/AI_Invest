"""Rule 08 L1 + L2 tests for `brief_executor.py` (Candidate Locator, Premise Refutation & Parallel Claims)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from sag_agent.types import CancellationToken, ModelChunk, ModelRequest, Usage
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.research.brief import validate_research_brief
from sag_api.research.brief_executor import execute_brief_claims, locate_candidates
from sag_api.research.chat_orchestrator import RetrievalResult, RetrievedResearchSection
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext

AS_OF = date(2026, 11, 26)


def _principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-brief-exec",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-exec",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"*"}),
        conversation_scope="all",
    )


class _BankAndCorpCoreReader:
    async def fetch_issuer_profiles(self, tickers=None):
        all_p = [
            {
                "ticker": "ACB",
                "company_name": "Ngân hàng Á Châu",
                "sector": "Tài chính",
                "industry": "Ngân hàng",
                "market_cap": 115_000e9,
            },
            {
                "ticker": "MBB",
                "company_name": "MBBank",
                "sector": "Tài chính",
                "industry": "Ngân hàng",
                "market_cap": 135_000e9,
            },
            {
                "ticker": "WEAKB",
                "company_name": "Weak Bank",
                "sector": "Tài chính",
                "industry": "Ngân hàng",
                "market_cap": 30_000e9,
            },
            {
                "ticker": "CASHCO",
                "company_name": "Cash Rich Corp",
                "sector": "Công nghiệp",
                "industry": "Sản xuất",
                "market_cap": 10_000e9,
            },
        ]
        if tickers:
            want = {t.upper() for t in tickers}
            return [p for p in all_p if p["ticker"] in want]
        return all_p

    async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
        del as_of_date, tickers
        return {"ACB": 115_000e9, "MBB": 135_000e9, "WEAKB": 30_000e9, "CASHCO": 10_000e9}

    async def fetch_pit_financial_statements(
        self,
        as_of_date,
        *,
        tickers=None,
        lookback_years=6,
        prefer_annual=False,
        statement_types=("IS", "BS", "CF", "RATIOS"),
    ):
        del as_of_date, tickers, lookback_years, prefer_annual, statement_types
        rows: list[dict[str, Any]] = []
        # ACB: NPL 1.48%, LLR 82%, NIM Q2=3.60% -> Q3=3.58% (flat, NOT collapsed!), NPAT YoY +8%
        for _q, pe, pub, npat_cur, npat_prev, nim in (
            (1, date(2026, 3, 31), date(2026, 4, 25), 4_100e9, 3_850e9, 0.0362),
            (2, date(2026, 6, 30), date(2026, 7, 25), 4_200e9, 3_900e9, 0.0360),
            (3, date(2026, 9, 30), date(2026, 10, 25), 4_320e9, 4_000e9, 0.0358),
        ):
            pe_prev = date(2025, pe.month, pe.day)
            rows.extend(
                [
                    {
                        "symbol": "ACB",
                        "period_end": pe_prev,
                        "statement_type": "IS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": date(2025, pe.month + 1, 20),
                        "data": {"is_net_profit_after_tax": npat_prev, "is_total_operating_income": 8_000e9},
                    },
                    {
                        "symbol": "ACB",
                        "period_end": pe,
                        "statement_type": "IS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": pub,
                        "data": {
                            "is_net_profit_after_tax": npat_cur,
                            "is_total_operating_income": 8_600e9,
                            "is_net_interest_income": 7_000e9,
                        },
                    },
                    {
                        "symbol": "ACB",
                        "period_end": pe,
                        "statement_type": "BS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": pub,
                        "data": {
                            "bs_total_assets": 780_000e9,
                            "bs_equity": 82_000e9,
                            "bs_loans_to_customers_gross": 550_000e9,
                            "bs_customer_deposits": 520_000e9,
                        },
                    },
                    {
                        "symbol": "ACB",
                        "period_end": pe,
                        "statement_type": "RATIOS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": pub,
                        "data": {"rt_bank_npl": 0.0148, "rt_bank_npl_coverage": -0.82, "rt_bank_nim": nim},
                    },
                ]
            )
        rows.append(
            {
                "symbol": "ACB",
                "period_end": date(2025, 12, 31),
                "statement_type": "IS",
                "frequency": "quarterly",
                "source": "alphastock",
                "published_date": date(2026, 1, 25),
                "data": {"is_net_profit_after_tax": 4_050e9, "is_total_operating_income": 8_200e9},
            }
        )
        rows.append(
            {
                "symbol": "ACB",
                "period_end": date(2024, 12, 31),
                "statement_type": "IS",
                "frequency": "quarterly",
                "source": "alphastock",
                "published_date": date(2025, 1, 25),
                "data": {"is_net_profit_after_tax": 3_750e9, "is_total_operating_income": 7_600e9},
            }
        )

        # MBB: NPL 1.45% (<= ACB), LLR 95% (>= ACB), Equity/Assets 11.2% (>= ACB), NPAT YoY +25% (> ACB!)
        for pe, pub, npat_cur, npat_prev in (
            (date(2026, 3, 31), date(2026, 4, 25), 5_500e9, 4_400e9),
            (date(2026, 6, 30), date(2026, 7, 25), 5_800e9, 4_600e9),
            (date(2026, 9, 30), date(2026, 10, 25), 6_250e9, 5_000e9),
        ):
            pe_prev = date(2025, pe.month, pe.day)
            rows.extend(
                [
                    {
                        "symbol": "MBB",
                        "period_end": pe_prev,
                        "statement_type": "IS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": date(2025, pe.month + 1, 20),
                        "data": {"is_net_profit_after_tax": npat_prev, "is_total_operating_income": 11_000e9},
                    },
                    {
                        "symbol": "MBB",
                        "period_end": pe,
                        "statement_type": "IS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": pub,
                        "data": {"is_net_profit_after_tax": npat_cur, "is_total_operating_income": 13_500e9},
                    },
                    {
                        "symbol": "MBB",
                        "period_end": pe,
                        "statement_type": "BS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": pub,
                        "data": {
                            "bs_total_assets": 1_000_000e9,
                            "bs_equity": 112_000e9,
                            "bs_loans_to_customers_gross": 700_000e9,
                            "bs_customer_deposits": 680_000e9,
                        },
                    },
                    {
                        "symbol": "MBB",
                        "period_end": pe,
                        "statement_type": "RATIOS",
                        "frequency": "quarterly",
                        "source": "alphastock",
                        "published_date": pub,
                        "data": {"rt_bank_npl": 0.0145, "rt_bank_npl_coverage": -0.95, "rt_bank_nim": 0.041},
                    },
                ]
            )
        rows.append(
            {
                "symbol": "MBB",
                "period_end": date(2025, 12, 31),
                "statement_type": "IS",
                "frequency": "quarterly",
                "source": "alphastock",
                "published_date": date(2026, 1, 25),
                "data": {"is_net_profit_after_tax": 5_600e9, "is_total_operating_income": 12_000e9},
            }
        )
        rows.append(
            {
                "symbol": "MBB",
                "period_end": date(2024, 12, 31),
                "statement_type": "IS",
                "frequency": "quarterly",
                "source": "alphastock",
                "published_date": date(2025, 1, 25),
                "data": {"is_net_profit_after_tax": 4_500e9, "is_total_operating_income": 10_000e9},
            }
        )

        # WEAKB: high NPL 4.2% -> must NOT beat MBB on safety
        rows.extend(
            [
                {
                    "symbol": "WEAKB",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "IS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"is_net_profit_after_tax": 800e9, "is_total_operating_income": 2_000e9},
                },
                {
                    "symbol": "WEAKB",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "BS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"bs_total_assets": 200_000e9, "bs_equity": 12_000e9},
                },
                {
                    "symbol": "WEAKB",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "RATIOS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"rt_bank_npl": 0.042, "rt_bank_npl_coverage": -0.45},
                },
            ]
        )

        # CASHCO: cash+st_inv = 6,000 tỷ, st_borrowings = 1,000 tỷ -> net_cash = 5,000 tỷ / 10,000 tỷ mcap = 50%!
        rows.extend(
            [
                {
                    "symbol": "CASHCO",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "BS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {
                        "bs_total_assets": 12_000e9,
                        "bs_equity": 9_000e9,
                        "i_tiền_và_các_khoản_tương_đương_tiền": 2_500e9,
                        "bs_short_term_investments": 3_500e9,
                        "bs_short_term_borrowings": 1_000e9,
                    },
                },
                {
                    "symbol": "CASHCO",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "IS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"is_net_revenue": 3_000e9, "is_net_profit_after_tax": 450e9},
                },
            ]
        )
        return rows


async def test_locate_candidates_resolves_anchor_relative_and_net_cash_screens() -> None:
    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    reader = _BankAndCorpCoreReader()
    profiles = tuple(await reader.fetch_issuer_profiles())

    # 1) "An toàn như ACB mà tăng trưởng tốt hơn" -> must discover MBB and reject WEAKB!
    brief_acb = validate_research_brief(
        {
            "user_concern": "Tìm ngân hàng an toàn như ACB mà tăng trưởng tốt hơn",
            "answer_shape": "candidate_ranking",
            "entities": {
                "tickers": ["ACB"],
                "anchor_ticker": "ACB",
                "candidate_search": {
                    "sectors": ["Ngân hàng"],
                    "template": "BANK",
                    "criteria": [
                        {"metric": "vr_npl_ratio", "op": "<=", "ref": "anchor", "tolerance": 0.002},
                        {"metric": "vr_npl_coverage", "op": ">=", "ref": "anchor"},
                        {"metric": "npat_growth_yoy", "op": ">", "ref": "anchor"},
                    ],
                    "sort_by": "npat_growth_yoy",
                    "sort_dir": "desc",
                    "limit": 3,
                },
            },
            "claims": [{"id": "K1", "statement": "So sánh ứng viên với ACB", "evidence": ["screen", "peers"]}],
        },
        as_of_date=AS_OF,
        catalog_profiles=profiles,
    ).brief
    assert brief_acb is not None

    ctx1 = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"ACB", "MBB"}),
        catalog_profiles=profiles,
        core_reader=reader,
    )
    loc1 = await locate_candidates(brief_acb, ctx1)
    assert loc1.anchor_ticker == "ACB"
    assert loc1.analysis_tickers == ["ACB", "MBB"]
    assert all(r["ticker"] != "WEAKB" for r in loc1.candidate_rows)
    assert loc1.candidate_rows[0]["ticker"] == "MBB"
    assert loc1.candidate_rows[0]["all_passed"] is True

    # 2) "Cổ phiếu nào trên sàn đang rẻ so với tiền mặt?" -> must discover CASHCO (net_cash/mcap = 50% >= 25%)!
    brief_cash = validate_research_brief(
        {
            "user_concern": "Tìm cổ phiếu có tiền mặt ròng cao so với vốn hóa",
            "answer_shape": "candidate_ranking",
            "entities": {
                "tickers": [],
                "anchor_ticker": None,
                "candidate_search": {
                    "sectors": [],
                    "criteria": [{"metric": "net_cash_to_market_cap", "op": ">=", "value": 0.25, "ref": "literal"}],
                    "sort_by": "net_cash_to_market_cap",
                    "sort_dir": "desc",
                    "limit": 4,
                },
            },
            "claims": [{"id": "K1", "statement": "Xếp hạng net cash / vốn hóa", "evidence": ["screen"]}],
        },
        as_of_date=AS_OF,
        catalog_profiles=profiles,
    ).brief
    assert brief_cash is not None

    ctx2 = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"ACB", "MBB"}),
        catalog_profiles=profiles,
        core_reader=reader,
    )
    loc2 = await locate_candidates(brief_cash, ctx2)
    assert loc2.analysis_tickers == ["CASHCO"]
    assert loc2.candidate_rows[0]["metrics"]["net_cash_to_market_cap"] == 0.5


async def test_execute_brief_claims_refutes_false_premise_and_extracts_doc_verdict() -> None:
    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    class _StubRetriever:
        async def retrieve(self, **_kwargs: Any) -> RetrievalResult:
            return RetrievalResult(
                sections=[
                    RetrievedResearchSection(
                        ticker="ACB",
                        doc_id="d-acb-q3",
                        document_version_id="v-acb-q3",
                        sub_doc_slot="QUARTER_NOTES",
                        page_from=12,
                        page_to=12,
                        published_date=date(2026, 10, 25),
                        heading="Thuyết minh thu nhập lãi thuần ACB Q3/2026",
                        content="Biên lãi ròng NIM của ACB duy trì ổn định quanh 3,58% nhờ CASA đạt 22,4%.",
                        fiscal_year=2026,
                        fiscal_quarter=3,
                    )
                ]
            )

    class _MiniReaderModel:
        def __init__(self) -> None:
            self.calls = 0

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            self.calls += 1
            mini_json = (
                '{"status": "CONFIRMED", '
                '"findings": ["NIM ACB ổn định quanh 3,58% nhờ CASA đạt 22,4% [C1]."], '
                '"anomaly_followup_query": null}'
            )
            yield ModelChunk(
                text_delta=mini_json,
                usage=Usage(input_tokens=600, output_tokens=70, cached_tokens=500),
            )

    reader = _BankAndCorpCoreReader()
    profiles = tuple(await reader.fetch_issuer_profiles())
    brief = validate_research_brief(
        {
            "user_concern": "Kiểm tra vì sao NIM ACB sập trong Q3/2026",
            "answer_shape": "premise_audit_and_explanation",
            "entities": {"tickers": ["ACB"], "anchor_ticker": None, "candidate_search": None},
            "premises_to_check": [
                {
                    "id": "P1",
                    "statement": "NIM của ACB trong Q3/2026 sập mạnh so với kỳ trước",
                    "evidence": ["fact:vr_nim"],
                    "period": "Q3/2026",
                }
            ],
            "claims": [
                {
                    "id": "K1",
                    "statement": "Nguyên nhân khiến NIM sập do chi phí vốn tăng vọt",
                    "evidence": ["fact:nii", "doc:QUARTER_NOTES|chi phí vốn NIM"],
                    "period": "Q3/2026",
                    "depends_on": ["P1"],
                },
                {
                    "id": "K2",
                    "statement": "Thuyết minh diễn biến NIM và CASA thực tế của ACB trong Q3/2026",
                    "evidence": ["doc:QUARTER_NOTES|NIM CASA"],
                    "period": "Q3/2026",
                    "depends_on": [],
                },
            ],
        },
        as_of_date=AS_OF,
        catalog_profiles=profiles,
    ).brief
    assert brief is not None

    ctx = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"ACB"}),
        catalog_profiles=profiles,
        core_reader=reader,
        retriever_override=_StubRetriever(),
    )
    loc = await locate_candidates(brief, ctx)
    mini_model = _MiniReaderModel()
    exec_res = await execute_brief_claims(brief=brief, located=loc, ctx=ctx, model=mini_model)

    # P1 MUST be deterministically REFUTED (3.60% -> 3.58% is -0.6%, NOT a collapse!)
    assert exec_res.premise_cards[0].status == "REFUTED"
    assert "BÁC BỎ TIỀN ĐỀ" in exec_res.premise_cards[0].summary_lines[0]
    # K1 (depends_on=["P1"]) MUST carry the refuted-premise warning AND skip speculative doc search!
    assert "P1 đã bị bác bỏ" in exec_res.claim_cards[0].summary_lines[0]
    # K2 (independent doc claim) ran Mini-Reader once and extracted [C1]!
    assert mini_model.calls == 1
    assert exec_res.claim_cards[1].status == "CONFIRMED"
    assert "[C1]" in exec_res.claim_cards[1].badges
