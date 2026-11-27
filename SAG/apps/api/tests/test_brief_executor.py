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


async def test_locate_candidates_excludes_financials_from_net_cash_and_labels_tolerance_and_periods() -> None:
    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    class _InsurerAndCorpReader(_BankAndCorpCoreReader):
        async def fetch_issuer_profiles(self, tickers=None):
            base = list(await super().fetch_issuer_profiles(tickers=None))
            base.append(
                {
                    "ticker": "BMI",
                    "company_name": "Bảo Minh",
                    "sector": "Tài chính",
                    "industry": "Bảo hiểm",
                    "market_cap": 3_000e9,
                }
            )
            if tickers:
                want = {t.upper() for t in tickers}
                return [p for p in base if p["ticker"] in want]
            return base

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            base = dict(await super().fetch_pit_market_caps(as_of_date, tickers=tickers))
            base["BMI"] = 3_000e9
            return base

        async def fetch_pit_financial_statements(
            self,
            as_of_date,
            *,
            tickers=None,
            lookback_years=6,
            prefer_annual=False,
            statement_types=("IS", "BS", "CF", "RATIOS"),
        ):
            rows = list(
                await super().fetch_pit_financial_statements(
                    as_of_date,
                    tickers=tickers,
                    lookback_years=lookback_years,
                    prefer_annual=prefer_annual,
                    statement_types=statement_types,
                )
            )
            # Insurer BMI holds huge short-term deposits backing technical reserves -> must be excluded!
            rows.append(
                {
                    "symbol": "BMI",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "BS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {
                        "bs_total_assets": 8_600e9,
                        "bs_equity": 2_900e9,
                        "i_tiền_và_các_khoản_tương_đương_tiền": 500e9,
                        "bs_short_term_investments": 5_000e9,
                    },
                }
            )
            return rows

    reader = _InsurerAndCorpReader()
    profiles = tuple(await reader.fetch_issuer_profiles())

    brief_cash = validate_research_brief(
        {
            "user_concern": "Tìm cổ phiếu rẻ so với tiền mặt",
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
            "claims": [],
        },
        as_of_date=AS_OF,
        catalog_profiles=profiles,
    ).brief
    assert brief_cash is not None

    ctx = ResearchToolContext(
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
    loc = await locate_candidates(brief_cash, ctx)
    assert loc.analysis_tickers == ["CASHCO"]
    assert all(r["ticker"] != "BMI" for r in loc.candidate_rows)
    assert any("[Q3/2026" in line for line in loc.summary_lines)

    # Also verify anchor tolerance label is shown transparently when tolerance > 0
    brief_acb = validate_research_brief(
        {
            "user_concern": "Ngân hàng an toàn như ACB",
            "answer_shape": "candidate_ranking",
            "entities": {
                "tickers": ["ACB"],
                "anchor_ticker": "ACB",
                "candidate_search": {
                    "sectors": ["Ngân hàng"],
                    "template": "BANK",
                    "criteria": [{"metric": "vr_npl_ratio", "op": "<=", "ref": "anchor", "tolerance": 0.005}],
                    "sort_by": "npat_growth_yoy",
                    "sort_dir": "desc",
                    "limit": 3,
                },
            },
            "claims": [{"id": "K1", "statement": "Xếp hạng", "evidence": ["screen"]}],
        },
        as_of_date=AS_OF,
        catalog_profiles=profiles,
    ).brief
    assert brief_acb is not None
    loc_acb = await locate_candidates(brief_acb, ctx)
    assert any("± dung sai 0.50%" in line for line in loc_acb.summary_lines)


async def test_quant_filters_unreliable_quarterly_cfo_and_surfaces_st_investments_liquidity() -> None:
    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    class _RetailTrapReader:
        async def fetch_issuer_profiles(self, tickers=None):
            del tickers
            return [
                {
                    "ticker": "FRT",
                    "company_name": "FPT Retail",
                    "sector": "Bán lẻ",
                    "industry": "Bán lẻ",
                    "market_cap": 20_000e9,
                }
            ]

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            del as_of_date, tickers
            return {"FRT": 20_000e9}

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
            return [
                {
                    "symbol": "FRT",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "BS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {
                        "bs_total_assets": 30_500e9,
                        "bs_equity": 6_500e9,
                        "i_tiền_và_các_khoản_tương_đương_tiền": 510e9,
                        "bs_short_term_investments": 11_274e9,
                        "bs_short_term_borrowings": 14_228e9,
                    },
                },
                # Sparse Q1 + Q3 CF (missing Q2) -> load_fact_set flags quarterly CF as unreliable!
                {
                    "symbol": "FRT",
                    "period_end": date(2026, 3, 31),
                    "statement_type": "CF",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 4, 25),
                    "data": {"cf_net_cash_flows_from_operating_activities": 37_220e9},
                },
                {
                    "symbol": "FRT",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "CF",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"cf_net_cash_flows_from_operating_activities": 92_494e9},
                },
                {
                    "symbol": "FRT",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "CF",
                    "frequency": "yearly",
                    "source": "alphastock",
                    "published_date": date(2026, 3, 25),
                    "data": {"cf_net_cash_flows_from_operating_activities": 2_747e9},
                },
            ]

    reader = _RetailTrapReader()
    profiles = tuple(await reader.fetch_issuer_profiles())
    brief = validate_research_brief(
        {
            "user_concern": "Kiểm tra thanh khoản và dòng tiền FRT",
            "answer_shape": "health_check",
            "entities": {"tickers": ["FRT"]},
            "claims": [
                {
                    "id": "K1",
                    "statement": "Thanh khoản vay ngắn hạn và dòng tiền HĐKD của FRT",
                    "evidence": ["fact:st_borrowings", "fact:cash_and_equivalents", "fact:cfo"],
                    "period": "Q3/2026",
                }
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
        available_tickers=frozenset({"FRT"}),
        catalog_profiles=profiles,
        core_reader=reader,
    )
    class _UnusedModel:
        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            raise AssertionError("Quant-only claim must not invoke LLM Mini-Reader")
            yield  # pragma: no cover

    loc = await locate_candidates(brief, ctx)
    exec_res = await execute_brief_claims(brief=brief, located=loc, ctx=ctx, model=_UnusedModel())
    text = "\n".join(exec_res.claim_cards[0].summary_lines)
    # Unreliable quarterly CF (92,494 tỷ) must be filtered out; FY2025 must remain with explicit warning
    assert "92,494" not in text
    assert "FY2025=" in text
    assert "chỉ dùng số năm FY" in text
    # Hidden term deposits (11,274 tỷ st_investments) MUST be surfaced deterministically alongside cash
    assert "đầu tư tài chính ngắn hạn" in text
    assert "11,274" in text
    assert any(
        t.get("formula") == "(cash_and_equivalents + st_investments) / st_borrowings"
        for t in ctx.evidence.calculator_traces
    )


async def test_premise_direction_falls_back_to_proxy_kpi_and_marks_unprovable_premise_no_data() -> None:
    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    class _SinglePointNimReader:
        def __init__(self, include_multi_quarter_proxy: bool) -> None:
            self.include_multi_quarter_proxy = include_multi_quarter_proxy

        async def fetch_issuer_profiles(self, tickers=None):
            del tickers
            return [
                {
                    "ticker": "ACB",
                    "company_name": "ACB",
                    "sector": "Tài chính",
                    "industry": "Ngân hàng",
                    "market_cap": 115_000e9,
                }
            ]

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            del as_of_date, tickers
            return {"ACB": 115_000e9}

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
            # Vendor vr_nim has ONLY ONE quarter (exactly like real Core PG!)
            rows: list[dict[str, Any]] = [
                {
                    "symbol": "ACB",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "RATIOS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"rt_bank_nim": 0.0292},
                },
                {
                    "symbol": "ACB",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "IS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"is_net_interest_income": 8_195e9},
                },
                {
                    "symbol": "ACB",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "BS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"bs_total_assets": 1_030_000e9},
                },
            ]
            if self.include_multi_quarter_proxy:
                rows.extend(
                    [
                        {
                            "symbol": "ACB",
                            "period_end": date(2026, 6, 30),
                            "statement_type": "IS",
                            "frequency": "quarterly",
                            "source": "alphastock",
                            "published_date": date(2026, 7, 25),
                            "data": {"is_net_interest_income": 7_700e9},
                        },
                        {
                            "symbol": "ACB",
                            "period_end": date(2026, 6, 30),
                            "statement_type": "BS",
                            "frequency": "quarterly",
                            "source": "alphastock",
                            "published_date": date(2026, 7, 25),
                            "data": {"bs_total_assets": 1_000_000e9},
                        },
                        {
                            "symbol": "ACB",
                            "period_end": date(2026, 3, 31),
                            "statement_type": "BS",
                            "frequency": "quarterly",
                            "source": "alphastock",
                            "published_date": date(2026, 4, 25),
                            "data": {"bs_total_assets": 980_000e9},
                        },
                    ]
                )
            return rows

    brief_raw = {
        "user_concern": "Tại sao NIM ACB sập trong Q3?",
        "answer_shape": "premise_audit_and_explanation",
        "entities": {"tickers": ["ACB"]},
        "premises_to_check": [
            {
                "id": "P1",
                "statement": "NIM của ACB trong Q3/2026 giảm mạnh so với kỳ trước",
                "evidence": ["fact:vr_nim"],
                "period": "Q3/2026",
            }
        ],
        "claims": [],
    }

    # Case A: 1-point vr_nim falls back to computed nii_to_avg_assets (rising Q2->Q3) -> REFUTED + grounded trace!
    reader_a = _SinglePointNimReader(include_multi_quarter_proxy=True)
    profiles_a = tuple(await reader_a.fetch_issuer_profiles())
    brief_a = validate_research_brief(brief_raw, as_of_date=AS_OF, catalog_profiles=profiles_a).brief
    assert brief_a is not None
    ctx_a = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"ACB"}),
        catalog_profiles=profiles_a,
        core_reader=reader_a,
    )
    class _UnusedModel:
        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            raise AssertionError("Quant-only premise must not invoke LLM Mini-Reader")
            yield  # pragma: no cover

    res_a = await execute_brief_claims(
        brief=brief_a, located=await locate_candidates(brief_a, ctx_a), ctx=ctx_a, model=_UnusedModel()
    )
    assert res_a.premise_cards[0].status == "REFUTED"
    assert "BÁC BỎ TIỀN ĐỀ" in res_a.premise_cards[0].summary_lines[0]
    assert any(t.get("source") == "PREMISE_DIRECTION_CHECK" for t in ctx_a.evidence.calculator_traces)

    # Case B: only 1 quarter total -> cannot compare -> MUST be NO_DATA (never falsely CONFIRMED!)
    reader_b = _SinglePointNimReader(include_multi_quarter_proxy=False)
    profiles_b = tuple(await reader_b.fetch_issuer_profiles())
    brief_b = validate_research_brief(brief_raw, as_of_date=AS_OF, catalog_profiles=profiles_b).brief
    assert brief_b is not None
    ctx_b = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"ACB"}),
        catalog_profiles=profiles_b,
        core_reader=reader_b,
    )
    res_b = await execute_brief_claims(
        brief=brief_b, located=await locate_candidates(brief_b, ctx_b), ctx=ctx_b, model=_UnusedModel()
    )
    assert res_b.premise_cards[0].status == "NO_DATA"
    assert "CHƯA KIỂM CHỨNG ĐƯỢC TIỀN ĐỀ" in res_b.premise_cards[0].summary_lines[0]


async def test_locate_candidates_scans_all_411_tickers_without_120_cutoff_and_blocks_vr_mcap_leak() -> None:
    """P1.4 Red->Green: 411 HOSE stocks with `stocks.market_cap == 0` must all be evaluated (no 120 alphabetical
    cap hiding S..Z stocks like ST8/VVS), `vnstock_vci` 'Common shares' charter capital must resolve (DVV),
    wide batches (>30 tickers) must request `lookback_years=2`, and historical `as_of_date` must NOT leak
    backfilled `rt_value_market_cap` when `fetch_pit_market_caps` has no price for a delisted/unpriced ticker.
    """

    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    # Build 411 3-letter corporate HOSE profiles: 407 filler tickers + DVV + ST8 + VVS + ZLK
    filler_tks = (
        [f"A{idx:02d}" for idx in range(100)]
        + [f"B{idx:02d}" for idx in range(100)]
        + [f"C{idx:02d}" for idx in range(100)]
        + [f"E{idx:02d}" for idx in range(100)]
        + [f"F{idx:02d}" for idx in range(7)]
    )
    special_tks = ["DVV", "ST8", "VVS", "ZLK"]
    all_tks = sorted(filler_tks + special_tks)
    assert len(all_tks) == 411

    class _Full411CoreReader:
        def __init__(self) -> None:
            self.seen_lookback_years: list[int] = []

        async def fetch_issuer_profiles(self, tickers=None):
            want = {t.upper() for t in tickers} if tickers else None
            out = []
            for tk in all_tks:
                if want and tk not in want:
                    continue
                out.append(
                    {
                        "ticker": tk,
                        "company_name": f"CTCP {tk}",
                        "sector": "Công nghiệp",
                        "industry": "Sản xuất",
                        "exchange": "HOSE",
                        # Mirrors live Core PG where 406/411 rows have market_cap = 0!
                        "market_cap": 58_000e9 if tk == "A00" else 0,
                    }
                )
            return out

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            del as_of_date
            want = {t.upper() for t in tickers} if tickers else set(all_tks)
            mc: dict[str, float] = {}
            for tk in want:
                if tk == "ZLK":
                    # Simulate missing historical price -> must NOT fall back to poisoned vr_market_cap!
                    continue
                mc[tk] = 100e9 if tk in {"ST8", "VVS", "DVV"} else 1_000e9
            return mc

        async def fetch_pit_financial_statements(
            self,
            as_of_date,
            *,
            tickers=None,
            lookback_years=6,
            prefer_annual=False,
            statement_types=("IS", "BS", "CF", "RATIOS"),
        ):
            del as_of_date, prefer_annual, statement_types
            self.seen_lookback_years.append(int(lookback_years))
            want = {t.upper() for t in tickers} if tickers else set(all_tks)
            rows: list[dict[str, Any]] = []
            for tk in want:
                if tk == "ST8":
                    cash_v = 310e9  # net_cash / mcap = 310% (#1 on HOSE, letter 'S' > 120th ticker!)
                elif tk == "VVS":
                    cash_v = 210e9  # net_cash / mcap = 210% (#2 on HOSE, letter 'V' > 120th ticker!)
                elif tk == "DVV":
                    cash_v = 150e9  # net_cash / mcap = 150%, uses vnstock_vci 'Common shares'
                elif tk == "ZLK":
                    cash_v = 500e9
                else:
                    cash_v = 50e9  # 5% net_cash / mcap
                bs_data: dict[str, Any] = {
                    "i_tiền_và_các_khoản_tương_đương_tiền": cash_v,
                    "bs_total_assets": max(cash_v * 1.5, 500e9),
                    "bs_equity": max(cash_v * 1.2, 400e9),
                }
                if tk == "DVV":
                    bs_data["Common shares"] = 100e9
                else:
                    bs_data["bs_charter_capital"] = 100e9
                rows.append(
                    {
                        "symbol": tk,
                        "period_end": date(2026, 3, 31),
                        "statement_type": "BS",
                        "frequency": "quarterly",
                        "source": "vnstock_vci" if tk == "DVV" else "alphastock",
                        "published_date": date(2026, 4, 25),
                        "data": bs_data,
                    }
                )
                if tk == "ZLK":
                    # Backfilled fetch-date market cap on historical ratios row -> Gate G4 leak if used!
                    rows.append(
                        {
                            "symbol": "ZLK",
                            "period_end": date(2026, 3, 31),
                            "statement_type": "RATIOS",
                            "frequency": "quarterly",
                            "source": "alphastock",
                            "published_date": date(2026, 4, 25),
                            "data": {"rt_value_market_cap": 50e9},
                        }
                    )
            return rows

    hist_as_of = date(2026, 6, 30)
    reader = _Full411CoreReader()
    profiles = tuple(await reader.fetch_issuer_profiles())
    brief_raw = {
        "user_concern": "Cổ phiếu nào trên sàn đang rẻ so với tiền mặt?",
        "answer_shape": "screened_candidates",
        "entities": {
            "tickers": [],
            "candidate_search": {
                "sectors": [],
                "criteria": [{"metric": "net_cash_to_market_cap", "op": ">=", "ref": "literal", "value": 0.25}],
                "sort_by": "net_cash_to_market_cap",
                "sort_dir": "desc",
                "limit": 5,
            },
        },
        "premises_to_check": [],
        "claims": [],
    }
    brief = validate_research_brief(brief_raw, as_of_date=hist_as_of, catalog_profiles=profiles).brief
    assert brief is not None

    # Verify DVV charter_capital resolves via vnstock_vci 'Common shares'
    from sag_api.research.facts.store import load_fact_set

    dvv_fs = await load_fact_set(reader, tickers=["DVV"], as_of_date=hist_as_of, lookback_years=2)
    dvv_cc = dvv_fs.get("DVV", "charter_capital", "Q", 2026, 1)
    assert dvv_cc.fact is not None and dvv_cc.fact.value == 100e9

    ctx = ResearchToolContext(
        as_of_date=hist_as_of,
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=hist_as_of),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"A00", "A01"}),
        catalog_profiles=profiles,
        core_reader=reader,
    )
    located = await locate_candidates(brief, ctx)
    picked = [r["ticker"] for r in located.candidate_rows]
    # Must scan all 411/411 tickers, surface ST8 (#1), VVS (#2), DVV (#3), and NEVER include ZLK!
    assert "411/411" in located.summary_lines[0]
    assert picked[:3] == ["ST8", "VVS", "DVV"]
    assert "ZLK" not in picked
    assert 2 in reader.seen_lookback_years


async def test_asyncpg_fetch_pit_market_caps_computes_close_times_shares_and_zero_overwrite_fix() -> None:
    """P1.4 Red->Green: `AsyncPGCoreMetricsReader.fetch_pit_market_caps` must compute `close_unadj * 1000 * shares`
    with 365d lookback + `corporate_actions` post-trade_date PIT guard, and `screen_universe_metrics` must not
    overwrite `market_cap` with `0.0` when `stocks.market_cap == 0` near `date.today()`.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from sag_api.db import models as _all_models  # noqa: F401
    from sag_api.db.base import Base
    from sag_api.research.financial_metrics_store import AsyncPGCoreMetricsReader, screen_universe_metrics

    captured_sqls: list[tuple[str, tuple[Any, ...]]] = []

    class _FakeConn:
        async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            captured_sqls.append((sql, args))
            return [
                {"ticker": "CCI", "market_cap": 418_647_706_000.0},
                {"ticker": "DVV", "market_cap": 150_000_000_000.0},
            ]

    class _FakePool:
        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[_FakeConn]:
            yield _FakeConn()

        async def close(self) -> None:
            return None

    async def _pool_factory(*_args: Any, **_kwargs: Any) -> _FakePool:
        return _FakePool()

    reader = AsyncPGCoreMetricsReader("postgresql://u:p@127.0.0.1:5432/aiinvest", pool_factory=_pool_factory)
    mcaps = await reader.fetch_pit_market_caps(date(2026, 11, 26), tickers=["CCI", "DVV"])
    assert mcaps == {"CCI": 418_647_706_000.0, "DVV": 150_000_000_000.0}
    assert len(captured_sqls) == 1
    sql_text, sql_args = captured_sqls[0]
    assert "close_unadj" in sql_text and "1000" in sql_text
    assert "corporate_actions" in sql_text
    assert "Common shares" in sql_text
    # 365-day lookback so illiquid HOSE stocks (411/411) resolve cleanly
    assert (sql_args[0] - sql_args[1]).days >= 365

    # Also verify screen_universe_metrics never overwrites missing market_cap with 0.0 when stocks.market_cap == 0!
    class _ZeroStockMcapReader:
        async def fetch_issuer_profiles(self, tickers=None):
            del tickers
            return [
                {
                    "ticker": "ZRO",
                    "company_name": "Zero Stock Mcap Co",
                    "sector": "Công nghiệp",
                    "industry": "Sản xuất",
                    "exchange": "HOSE",
                    "market_cap": 0,
                }
            ]

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            del as_of_date, tickers
            return {}

        async def fetch_pit_financial_ratios(self, as_of_date, *, tickers=None, lookback_years=7):
            del as_of_date, tickers, lookback_years
            return [
                {
                    "symbol": "ZRO",
                    "ratio_date": date(2025, 12, 31),
                    "frequency": "yearly",
                    "published_date": date(2026, 3, 20),
                    "roe": 0.15,
                    "pe": 8.0,
                    "pb": 1.2,
                }
            ]

        async def fetch_pit_financial_statements(
            self, as_of_date, *, tickers=None, lookback_years=5, prefer_annual=True, statement_types=("BS", "IS", "CF")
        ):
            del as_of_date, tickers, lookback_years, prefer_annual, statement_types
            return []

    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with sf() as s:
        rows, _, _ = await screen_universe_metrics(
            s,
            tenant_id="aiinvest-default",
            as_of_date=date.today(),
            allowed_project_ids=frozenset({"*"}),
            core_reader=_ZeroStockMcapReader(),
            scope="core_universe",
            tickers=["ZRO"],
            prefer_annual=True,
        )
    await eng.dispose()
    assert rows[0]["ticker"] == "ZRO"
    assert rows[0].get("market_cap") is None


async def test_single_ticker_peers_honest_partial_status_divergence_autoprobe_and_reasoning_buffer() -> None:
    import json

    from sag_api.research.analytics import PeriodRef, compute_kpi
    from sag_api.research.brief_synthesizer import _ReasoningWordStreamBuffer
    from sag_api.research.facts import load_fact_set

    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    class _FptAndPeersReader:
        async def fetch_issuer_profiles(self, tickers=None):
            rows = [
                {
                    "ticker": "FPT",
                    "company_name": "FPT Corp",
                    "sector": "Công nghệ thông tin",
                    "industry": "Phần mềm & Dịch vụ CNTT",
                    "market_cap": 190_000e9,
                },
                {
                    "ticker": "CMG",
                    "company_name": "CMC Corp",
                    "sector": "Công nghệ thông tin",
                    "industry": "Phần mềm & Dịch vụ CNTT",
                    "market_cap": 9_500e9,
                },
                {
                    "ticker": "ELC",
                    "company_name": "ELCOM",
                    "sector": "Công nghệ thông tin",
                    "industry": "Phần mềm & Dịch vụ CNTT",
                    "market_cap": 2_400e9,
                },
            ]
            if tickers:
                want = {t.upper() for t in tickers}
                return [r for r in rows if r["ticker"] in want]
            return rows

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            del as_of_date, tickers
            return {"FPT": 190_000e9, "CMG": 9_500e9, "ELC": 2_400e9}

        async def fetch_pit_financial_statements(
            self,
            as_of_date,
            *,
            tickers=None,
            lookback_years=6,
            prefer_annual=False,
            statement_types=("IS", "BS", "CF", "RATIOS"),
        ):
            del as_of_date, lookback_years, prefer_annual, statement_types
            all_rows = [
                # FPT Q3/2025 -> Q3/2026 (-20.05% headline revenue drop due to FTEL deconsolidation!)
                {
                    "symbol": "FPT",
                    "period_end": date(2025, 9, 30),
                    "statement_type": "IS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2025, 10, 25),
                    "data": {"is_net_revenue": 17_204e9, "is_net_profit_after_tax": 2_895e9},
                },
                {
                    "symbol": "FPT",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "IS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"is_net_revenue": 13_754.2e9, "is_net_profit_after_tax": 2_723.1e9},
                },
                {
                    "symbol": "FPT",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "BS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"bs_total_assets": 72_000e9, "bs_equity": 36_000e9},
                },
                {
                    "symbol": "FPT",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "CF",
                    "frequency": "yearly",
                    "source": "alphastock",
                    "published_date": date(2026, 3, 25),
                    "data": {"cf_net_cash_flows_from_operating_activities": 10_136e9},
                },
                {
                    "symbol": "FPT",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "IS",
                    "frequency": "yearly",
                    "source": "alphastock",
                    "published_date": date(2026, 3, 25),
                    "data": {"is_net_revenue": 62_849e9, "is_net_profit_after_tax": 11_220e9},
                },
                # CMG peer
                {
                    "symbol": "CMG",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "IS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"is_net_revenue": 2_180e9, "is_net_profit_after_tax": 145e9},
                },
                {
                    "symbol": "CMG",
                    "period_end": date(2026, 9, 30),
                    "statement_type": "BS",
                    "frequency": "quarterly",
                    "source": "alphastock",
                    "published_date": date(2026, 10, 25),
                    "data": {"bs_total_assets": 8_200e9, "bs_equity": 3_400e9},
                },
            ]
            if tickers:
                want = {t.upper() for t in tickers}
                return [r for r in all_rows if r["symbol"] in want]
            return all_rows

    reader = _FptAndPeersReader()
    profiles = tuple(await reader.fetch_issuer_profiles())

    # 1) Vietnamese KPI detail check (zero raw English 'cash-flow KPI evaluated on')
    fs_check = await load_fact_set(reader, tickers=["FPT"], as_of_date=AS_OF)
    kv_cfo = compute_kpi(fs_check, "FPT", "cfo_to_npat", PeriodRef("Q", 2026, 3))
    assert "cash-flow KPI evaluated on" not in kv_cfo.detail
    assert "FY2025" in kv_cfo.detail

    # 2) _ReasoningWordStreamBuffer word-boundary & protocol JSON cutoff check
    rbuf = _ReasoningWordStreamBuffer()
    out_r = rbuf.push("Cần kiểm tra doc:cơ cấu doanh thu và rủi ro. ") + rbuf.push(
        '```json\n{"user_concern": "FPT", "claims": []}\n```'
    ) + rbuf.flush()
    assert "báo cáoơ" not in out_r
    assert "cơ cấu doanh thu" in out_r
    assert '"user_concern"' not in out_r and "{" not in out_r

    # 3) Brief with quant-only K1 (-20.05% revenue drop on indexed FPT -> triggers Auto-Probe!)
    #    and K2 with dropped `fact:vr_beta` + `risk` + single-ticker `peers`!
    val = validate_research_brief(
        {
            "user_concern": "Đánh giá tăng trưởng và so sánh ngành của FPT",
            "answer_shape": "verdict_with_reasons",
            "entities": {"tickers": ["FPT"]},
            "claims": [
                {
                    "id": "K1",
                    "statement": "Tăng trưởng doanh thu và lợi nhuận Q3/2026 của FPT",
                    "evidence": ["fact:revenue_net", "kpi:top_line_growth_yoy"],
                    "period": "Q3/2026",
                },
                {
                    "id": "K2",
                    "statement": "Độ biến động beta của FPT thấp hơn trung bình ngành",
                    "evidence": ["fact:vr_beta", "risk"],
                    "period": "Q3/2026",
                },
                {
                    "id": "K3",
                    "statement": "So sánh biên lợi nhuận ròng của FPT với doanh nghiệp cùng ngành",
                    "evidence": ["kpi:net_margin", "peers"],
                    "period": "Q3/2026",
                },
            ],
        },
        as_of_date=AS_OF,
        catalog_profiles=profiles,
    )
    assert val.brief is not None

    class _StubRetriever:
        async def retrieve(self, **kwargs):
            from sag_api.research.chat_orchestrator import RetrievalResult, RetrievedResearchSection

            del kwargs
            return RetrievalResult(
                sections=[
                    RetrievedResearchSection(
                        ticker="FPT",
                        doc_id="doc-fpt-q3",
                        document_version_id="ver-fpt-q3",
                        sub_doc_slot="QUARTER_NOTES",
                        page_from=8,
                        page_to=8,
                        published_date=date(2026, 10, 25),
                        content=(
                            "Giải trình KQKD Q3/2026: FTEL không còn là công ty con hợp nhất; "
                            "trên cơ sở tương đương doanh thu tăng 12,1% và LNTT tăng 13,2%."
                        ),
                        heading="GIẢI TRÌNH",
                    )
                ]
            )

    class _MiniModel:
        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            yield ModelChunk(
                text_delta=json.dumps(
                    {
                        "status": "CONFIRMED",
                        "findings": [
                            "Doanh thu báo cáo giảm do ngừng hợp nhất FTEL; so sánh tương đương vẫn tăng 12,1% [C1]."
                        ],
                        "anomaly_followup_query": None,
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(input_tokens=150, output_tokens=45),
            )

    ctx = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"FPT"}),
        catalog_profiles=profiles,
        core_reader=reader,
        retriever_override=_StubRetriever(),
    )
    loc = await locate_candidates(val.brief, ctx)
    exec_res = await execute_brief_claims(
        brief=val.brief,
        located=loc,
        ctx=ctx,
        model=_MiniModel(),
        dropped_by_claim_id=val.dropped_by_claim_id,
    )

    k1, k2, k3 = exec_res.claim_cards
    # K1 auto-probed QUARTER_NOTES due to -20.05% YoY revenue drop and attached verbatim [C1] excerpt!
    assert "[C1]" in k1.badges
    rendered_k1 = k1.render_for_synthesis()
    assert "FTEL không còn là công ty con hợp nhất" in rendered_k1
    # K2 had `fact:vr_beta` dropped and ONLY neutral risk line -> MUST NOT be marked CONFIRMED!
    assert k2.status in ("PARTIAL", "NO_DATA")
    # K3 single-ticker `peers` auto-resolved same-sector peer CMG!
    k3_txt = "\n".join(k3.summary_lines)
    assert "CMG=" in k3_txt


