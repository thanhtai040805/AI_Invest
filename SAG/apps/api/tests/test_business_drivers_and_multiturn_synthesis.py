"""TDD L1-L3 tests for deterministic BusinessDriverEngine, multi-slot qualitative sweep,
and badge-safe multi-turn Stage 6 synthesis history (preventing Turn-2 echo & stale [C11] leaks).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sag_agent.types import CancellationToken, ModelChunk, ModelProvider, ModelRequest, Usage
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.research.analytics.business_drivers import (
    compute_multi_quarter_trajectory,
    extract_balance_sheet_note_breakdowns,
    extract_segment_drivers,
)
from sag_api.research.brief import BriefEntities, ClaimSpec, ResearchBrief
from sag_api.research.brief_executor import LocatedTargets, execute_brief_claims
from sag_api.research.chat_orchestrator import RetrievalResult, RetrievedResearchSection
from sag_api.research.facts import build_fact_set, resolve_template
from sag_api.research.quote_verifier import verify_numeric_claims_against_evidence
from sag_api.research.session_memory import (
    build_conversation_prefix_messages,
    build_financial_zeromem_ledger,
    build_synthesis_history_messages,
)
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext


def _fpt_q3_2026_segment_tables() -> list[dict[str, Any]]:
    """Exact real FPT 2026Q3 p.29 (9M2026) and p.30 (9M2025) segment tables from sag_extracted_tables."""
    return [
        {
            "table_id": "tbl-fpt-seg-2026q3-p29",
            "project_id": "FPT",
            "sub_doc_slot": "QUARTER_NOTES",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
            "page_from": 29,
            "title": "4. BÁO CÁO BỘ PHÂN (TIẾP THEO)",
            "section_path": "QUARTER_NOTES > THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT > 4. BÁO CÁO BỘ PHÂN",
            "currency": "VND",
            "unit_raw": "unspecified",
            "unit_multiplier": 1.0,
            "verification_status": "TABLE_UNVERIFIED",
            "headers": [
                "Chỉ tiêu",
                "Công nghệ thông tin :: Sản phẩm và dịch vụ CNTT nước ngoài",
                "Công nghệ thông tin :: Sản phẩm và dịch vụ CNTT trong nước",
                "Đầu tư, giáo dục và khác",
                "Loại trừ hợp nhất",
                "Tổng cộng",
            ],
            "rows": [
                [
                    {"raw_text": "Doanh thu theo bộ phận", "numeric_value": None},
                    {"raw_text": "28.537.966", "numeric_value": 28537966.0},
                    {"raw_text": "6.976.698", "numeric_value": 6976698.0},
                    {"raw_text": "5.108.695", "numeric_value": 5108695.0},
                    {"raw_text": "(600.699)", "numeric_value": -600699.0},
                    {"raw_text": "40.022.660", "numeric_value": 40022660.0},
                ],
                [
                    {"raw_text": "Chi phí phân bổ trực tiếp cho bộ phận", "numeric_value": None},
                    {"raw_text": "(24.186.116)", "numeric_value": -24186116.0},
                    {"raw_text": "(6.535.121)", "numeric_value": -6535121.0},
                    {"raw_text": "(3.983.503)", "numeric_value": -3983503.0},
                    {"raw_text": "581.915", "numeric_value": 581915.0},
                    {"raw_text": "(34.122.825)", "numeric_value": -34122825.0},
                ],
                [
                    {"raw_text": "Phần lãi trong công ty liên kết", "numeric_value": None},
                    {"raw_text": "19.001", "numeric_value": 19001.0},
                    {"raw_text": "2.047", "numeric_value": 2047.0},
                    {"raw_text": "2.199.255", "numeric_value": 2199255.0},
                    {"raw_text": "-", "numeric_value": None},
                    {"raw_text": "2.220.303", "numeric_value": 2220303.0},
                ],
                [
                    {"raw_text": "Kết quả hoạt động kinh doanh theo bộ phận (ii)", "numeric_value": None},
                    {"raw_text": "4.370.850", "numeric_value": 4370850.0},
                    {"raw_text": "443.624", "numeric_value": 443624.0},
                    {"raw_text": "3.324.447", "numeric_value": 3324447.0},
                    {"raw_text": "(18.784)", "numeric_value": -18784.0},
                    {"raw_text": "8.120.137", "numeric_value": 8120137.0},
                ],
            ],
        },
        {
            "table_id": "tbl-fpt-seg-2026q3-p30",
            "project_id": "FPT",
            "sub_doc_slot": "QUARTER_NOTES",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
            "page_from": 30,
            "title": "4. BÁO CÁO BỘ PHẬN (TIẾP THEO)",
            "section_path": "QUARTER_NOTES > THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT > 4. BÁO CÁO BỘ PHẬN",
            "currency": "VND",
            "unit_raw": "unspecified",
            "unit_multiplier": 1.0,
            "verification_status": "TABLE_UNVERIFIED",
            "headers": [
                "Chỉ tiêu",
                "Công nghệ thông tin :: Sản phẩm và dịch vụ CNTT nước ngoài",
                "Công nghệ thông tin :: Sản phẩm và dịch vụ CNTT trong nước",
                "Công nghệ thông tin :: Viễn thông",
                "Công nghệ thông tin :: Nội dung số",
                "Đầu tư, giáo dục và khác",
                "Loại trừ hợp nhất",
                "Tổng cộng",
            ],
            "rows": [
                [
                    {"raw_text": "Doanh thu theo bộ phận", "numeric_value": None},
                    {"raw_text": "25.573.503", "numeric_value": 25573503.0},
                    {"raw_text": "5.375.991", "numeric_value": 5375991.0},
                    {"raw_text": "13.738.174", "numeric_value": 13738174.0},
                    {"raw_text": "548.743", "numeric_value": 548743.0},
                    {"raw_text": "5.416.174", "numeric_value": 5416174.0},
                    {"raw_text": "(765.209)", "numeric_value": -765209.0},
                    {"raw_text": "49.887.375", "numeric_value": 49887375.0},
                ],
                [
                    {"raw_text": "Phần lãi trong công ty liên kết", "numeric_value": None},
                    {"raw_text": "9.295", "numeric_value": 9295.0},
                    {"raw_text": "798", "numeric_value": 798.0},
                    {"raw_text": "-", "numeric_value": None},
                    {"raw_text": "-", "numeric_value": None},
                    {"raw_text": "364.186", "numeric_value": 364186.0},
                    {"raw_text": "-", "numeric_value": None},
                    {"raw_text": "374.279", "numeric_value": 374279.0},
                ],
                [
                    {"raw_text": "Kết quả hoạt động kinh doanh theo bộ phận (ii)", "numeric_value": None},
                    {"raw_text": "3.540.247", "numeric_value": 3540247.0},
                    {"raw_text": "162.930", "numeric_value": 162930.0},
                    {"raw_text": "2.801.040", "numeric_value": 2801040.0},
                    {"raw_text": "105.668", "numeric_value": 105668.0},
                    {"raw_text": "1.648.840", "numeric_value": 1648840.0},
                    {"raw_text": "(17.248)", "numeric_value": -17248.0},
                    {"raw_text": "8.241.477", "numeric_value": 8241477.0},
                ],
            ],
        },
    ]


def _fpt_q3_2026_note6_tables() -> list[dict[str, Any]]:
    """Exact real FPT 2026Q3 p.32-33 Note 6 (ST investments, FX term deposits, Associate roll-forward)."""
    return [
        {
            "table_id": "tbl-fpt-note6-p32",
            "project_id": "FPT",
            "sub_doc_slot": "QUARTER_NOTES",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
            "page_from": 32,
            "title": "6. CÁC KHOẢN ĐẦU TƯ TÀI CHÍNH",
            "section_path": "QUARTER_NOTES > THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT > 6. CÁC KHOẢN ĐẦU TƯ TÀI CHÍNH",
            "currency": "VND",
            "unit_raw": "VND",
            "unit_multiplier": 1.0,
            "verification_status": "TABLE_UNVERIFIED",
            "headers": [
                "col_0",
                "30/09/2026 :: Giá gốcVND",
                "30/09/2026 :: Giá trị ghi sổVND",
                "31/12/2025 :: Giá gốcVND",
                "31/12/2025 :: Giá trị ghi sổVND",
            ],
            "rows": [
                [
                    {"raw_text": "Ngắn hạn", "numeric_value": None},
                    {"raw_text": "21.485.745.662.108", "numeric_value": 21485745662108.0},
                    {"raw_text": "21.449.651.162.976", "numeric_value": 21449651162976.0},
                    {"raw_text": "30.060.419.659.879", "numeric_value": 30060419659879.0},
                    {"raw_text": "30.024.547.484.655", "numeric_value": 30024547484655.0},
                ],
                [
                    {"raw_text": "- Tiền gửi có kỳ hạn (i)", "numeric_value": None},
                    {"raw_text": "20.878.556.627.776", "numeric_value": 20878556627776.0},
                    {"raw_text": "20.878.556.627.776", "numeric_value": 20878556627776.0},
                    {"raw_text": "29.630.986.737.440", "numeric_value": 29630986737440.0},
                    {"raw_text": "29.630.986.737.440", "numeric_value": 29630986737440.0},
                ],
                [
                    {"raw_text": "- Các khoản cho vay", "numeric_value": None},
                    {"raw_text": "61.051.010.424", "numeric_value": 61051010424.0},
                    {"raw_text": "58.231.563.068", "numeric_value": 58231563068.0},
                    {"raw_text": "9.195.910.819", "numeric_value": 9195910819.0},
                    {"raw_text": "6.598.787.371", "numeric_value": 6598787371.0},
                ],
                [
                    {"raw_text": "- Lãi tiền gửi, cho vay", "numeric_value": None},
                    {"raw_text": "512.862.972.132", "numeric_value": 512862972132.0},
                    {"raw_text": "512.862.972.132", "numeric_value": 512862972132.0},
                    {"raw_text": "386.961.959.844", "numeric_value": 386961959844.0},
                    {"raw_text": "386.961.959.844", "numeric_value": 386961959844.0},
                ],
            ],
        },
        {
            "table_id": "tbl-fpt-note6-fx-p32",
            "project_id": "FPT",
            "sub_doc_slot": "QUARTER_NOTES",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
            "page_from": 32,
            "title": "Chi tiết các khoản tiền gửi có kỳ hạn theo nguyên tệ như sau:",
            "section_path": "QUARTER_NOTES > THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT > 6. CÁC KHOẢN ĐẦU TƯ TÀI CHÍNH",
            "currency": "VND",
            "unit_raw": "VND",
            "unit_multiplier": 1.0,
            "verification_status": "PARTIAL",
            "headers": [
                "col_0",
                "30/09/2026 :: Nguyên tệ",
                "30/09/2026 :: Tương đương VND",
                "31/12/2025 :: Nguyên tệ",
                "31/12/2025 :: Tương đương VND",
            ],
            "rows": [
                [
                    {"raw_text": "VND", "numeric_value": None},
                    {"raw_text": "19.719.006.627.776", "numeric_value": 19719006627776.0},
                    {"raw_text": "19.719.006.627.776", "numeric_value": 19719006627776.0},
                    {"raw_text": "28.465.140.287.440", "numeric_value": 28465140287440.0},
                    {"raw_text": "28.465.140.287.440", "numeric_value": 28465140287440.0},
                ],
                [
                    {"raw_text": "JPY", "numeric_value": None},
                    {"raw_text": "7.000.000.000,00", "numeric_value": 7000000000.0},
                    {"raw_text": "1.159.550.000.000", "numeric_value": 1159550000000.0},
                    {"raw_text": "6.000.000.000,00", "numeric_value": 6000000000.0},
                    {"raw_text": "1.007.580.000.000", "numeric_value": 1007580000000.0},
                ],
                [
                    {"raw_text": "", "numeric_value": None},
                    {"raw_text": "", "numeric_value": None},
                    {"raw_text": "20.878.556.627.776", "numeric_value": 20878556627776.0},
                    {"raw_text": "", "numeric_value": None},
                    {"raw_text": "29.630.986.737.440", "numeric_value": 29630986737440.0},
                ],
            ],
        },
        {
            "table_id": "tbl-fpt-note6b-p33",
            "project_id": "FPT",
            "sub_doc_slot": "QUARTER_NOTES",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
            "page_from": 33,
            "title": "b. Đầu tư vào công ty liên doanh, liên kết",
            "section_path": (
                "QUARTER_NOTES > THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT > "
                "b. Đầu tư vào công ty liên doanh, liên kết"
            ),
            "currency": "VND",
            "unit_raw": "VND",
            "unit_multiplier": 1.0,
            "verification_status": "VERIFIED",
            "headers": [
                "col_0",
                "Từ ngày 01/01/2026 đến ngày 30/09/2026 :: VND",
                "Từ ngày 01/01/2025 đến ngày 31/12/2025 :: VND",
            ],
            "rows": [
                [
                    {"raw_text": "Số dư đầu kỳ", "numeric_value": None},
                    {"raw_text": "3.581.128.517.425", "numeric_value": 3581128517425.0},
                    {"raw_text": "2.281.222.436.752", "numeric_value": 2281222436752.0},
                ],
                [
                    {"raw_text": "Tăng do thay đổi phương pháp hợp nhất tại FTEL", "numeric_value": None},
                    {"raw_text": "5.157.899.799.701", "numeric_value": 5157899799701.0},
                    {"raw_text": "-", "numeric_value": None},
                ],
                [
                    {
                        "raw_text": (
                            "Phần lợi nhuận của Tập đoàn trong kết quả kinh doanh "
                            "của công ty liên doanh, liên kết"
                        ),
                        "numeric_value": None,
                    },
                    {"raw_text": "2.220.302.808.436", "numeric_value": 2220302808436.0},
                    {"raw_text": "658.024.835.314", "numeric_value": 658024835314.0},
                ],
                [
                    {"raw_text": "Số dư cuối kỳ", "numeric_value": None},
                    {"raw_text": "10.753.092.657.035", "numeric_value": 10753092657035.0},
                    {"raw_text": "3.581.128.517.425", "numeric_value": 3581128517425.0},
                ],
            ],
        },
    ]


def test_l1_segment_driver_engine_verifies_horizontal_math_and_computes_mix_margins_and_yoy() -> None:
    seg_tables = _fpt_q3_2026_segment_tables()
    drivers = extract_segment_drivers(seg_tables, "FPT")
    assert drivers, "Expected horizontal-verified segment drivers from FPT 2026Q3 p.29-30"

    joined = "\n".join(d["summary_line"] for d in drivers)
    # Overseas IT: 28,538 bn (+11.59% YoY), op profit 4,370.9 bn (+23.46% YoY), op margin 15.32%
    assert "CNTT nước ngoài" in joined
    assert "15.32%" in joined or "15,32%" in joined
    assert "23.46%" in joined or "+23.5%" in joined or "23,46%" in joined
    # Domestic IT: op profit 443.6 bn vs 162.9 bn (+172.28% YoY)
    assert "CNTT trong nước" in joined
    assert "172." in joined or "172," in joined
    # Deconsolidated Telecom column detected & associate profit jump surfaced
    assert "Viễn thông" in joined

    # Fail-closed: corrupting horizontal total by > 0.5% MUST reject the corrupted row/table
    corrupted = [dict(seg_tables[0])]
    bad_row = [dict(c) for c in seg_tables[0]["rows"][0]]
    bad_row[-1] = {"raw_text": "99.999.999", "numeric_value": 99999999.0}
    corrupted[0]["rows"] = [bad_row]
    assert extract_segment_drivers(corrupted, "FPT") == []


def test_l1_balance_sheet_note_breakdown_and_stage7_compatibility() -> None:
    note_tables = _fpt_q3_2026_note6_tables()
    breakdowns = extract_balance_sheet_note_breakdowns(None, note_tables, "FPT")
    assert len(breakdowns) >= 2
    txt = "\n".join(b["summary_line"] for b in breakdowns)
    assert "97.34%" in txt or "97,34%" in txt
    assert "JPY" in txt
    assert "FTEL" in txt

    # Verify Stage 7 accepts prose citing registered trace numbers with 0 unsupported claims
    traces = [{**b["calc_trace"], "calc_id": f"CALC-{idx}"} for idx, b in enumerate(breakdowns, 1)]
    sample_prose = (
        "Đầu tư tài chính ngắn hạn 21.449,7 tỷ đồng thực chất gồm 97,34% là tiền gửi có kỳ hạn "
        "(20.878,6 tỷ đồng), trong đó có 1.159,6 tỷ đồng tiền gửi bằng JPY [CALC-1] [CALC-2]."
    )
    audit = verify_numeric_claims_against_evidence(
        sample_prose,
        sections_by_cid={},
        calculator_traces=traces,
        hydrated_tables=note_tables,
    )
    assert audit.unsupported_claims == [], f"Unexpected Stage 7 flags: {audit.unsupported_claims}"


def test_l1_multi_quarter_trajectory_computes_margin_trend_and_operating_leverage() -> None:
    sample_path = Path(__file__).parent / "fixtures" / "core_financial_statements_sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    templates = {sym: resolve_template(ind) for sym, ind in sample["industries"].items()}
    fs = build_fact_set(sample["rows"], templates_by_ticker=templates, as_of_date=date(2026, 11, 27))

    corp_traj = compute_multi_quarter_trajectory(fs, "VHM")
    assert corp_traj is not None
    assert "Biên gộp" in corp_traj["summary_line"]
    assert corp_traj["calc_trace"]["ticker"] == "VHM"

    bank_traj = compute_multi_quarter_trajectory(fs, "ACB")
    assert bank_traj is not None
    assert "CIR" in bank_traj["summary_line"]
    assert bank_traj["calc_trace"]["ticker"] == "ACB"


class _StubTableStore:
    async def get_tables(self, **kwargs: Any) -> list[dict[str, Any]]:
        slot = kwargs.get("sub_doc_slot")
        all_t = [*_fpt_q3_2026_segment_tables(), *_fpt_q3_2026_note6_tables()]
        if slot:
            return [t for t in all_t if t["sub_doc_slot"] == slot]
        return all_t


class _StubRetriever:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def retrieve(self, *, query: str, tickers: list[str], as_of_date: date, top_k: int = 8) -> Any:
        self.calls.append({"query": query, "tickers": list(tickers), "top_k": top_k})
        return RetrievalResult(
            hipporag_hops=2,
            canonical_blocks={},
            sections=[
                RetrievedResearchSection(
                    ticker="FPT",
                    doc_id="doc-q3",
                    document_version_id="dv-q3",
                    sub_doc_slot="QUARTER_NOTES",
                    page_from=9,
                    page_to=9,
                    published_date=date(2026, 10, 29),
                    content="Khối Công nghệ đạt doanh thu 12.377 tỷ đồng (+14,4%) và LNTT 1.739 tỷ đồng (+16%).",
                    heading="GIẢI TRÌNH KQKD Q3/2026",
                    fiscal_year=2026,
                    fiscal_quarter=3,
                ),
                RetrievedResearchSection(
                    ticker="FPT",
                    doc_id="doc-bctn",
                    document_version_id="dv-bctn",
                    sub_doc_slot="BCTN_CORE",
                    page_from=64,
                    page_to=64,
                    published_date=date(2026, 4, 9),
                    content="Doanh thu Chuyển đổi số đạt 16.751 tỷ đồng (+16,8%), chiếm 47,3% CNTT nước ngoài.",
                    heading="ĐỘNG LỰC CHUYỂN ĐỔI SỐ",
                    fiscal_year=2025,
                    fiscal_quarter=0,
                ),
                RetrievedResearchSection(
                    ticker="FPT",
                    doc_id="doc-agm",
                    document_version_id="dv-agm",
                    sub_doc_slot="AGM_TARGETS",
                    page_from=30,
                    page_to=30,
                    published_date=date(2026, 4, 21),
                    content="FPT vận hành 2 Nhà máy AI tại Nhật Bản và Việt Nam, đón làn sóng Nghị quyết 57.",
                    heading="THẢO LUẬN ĐHĐCĐ 2026",
                    fiscal_year=2026,
                    fiscal_quarter=0,
                ),
            ],
        )


class _StubMiniReaderModel(ModelProvider):
    async def stream_turn(self, request: ModelRequest, cancellation: CancellationToken):
        txt = (
            '{"status": "CONFIRMED", "findings": ['
            '"Khối Công nghệ đạt doanh thu 12.377 tỷ đồng (+14,4%) [C1].", '
            '"Doanh thu Chuyển đổi số đạt 16.751 tỷ đồng (+16,8%), chiếm 47,3% CNTT nước ngoài [C2].", '
            '"FPT vận hành 2 Nhà máy AI tại Nhật Bản và Việt Nam [C3]."'
            '], "anomaly_followup_query": null}'
        )
        yield ModelChunk(text_delta=txt, usage=Usage(requests=1, input_tokens=200, output_tokens=60))


@pytest.mark.asyncio
async def test_l2_brief_executor_auto_enriches_business_drivers_and_multislot_sweep() -> None:
    @asynccontextmanager
    async def _noop_sf():
        yield None  # type: ignore[misc]

    retr = _StubRetriever()
    ctx = ResearchToolContext(
        as_of_date=date(2026, 11, 27),
        principal=VerifiedPrincipal(
            subject="u-1",
            organization_id="aiinvest-default",
            allowed_project_ids=frozenset({"*"}),
            issuer="continuum-be-bff",
            key_id="service-token",
            token_id="tok-1",
            issued_at=1700000000,
            expires_at=1700000060,
            tenant_id="aiinvest-default",
            allowed_partition_ids=frozenset({"*"}),
            conversation_scope="all",
        ),
        chat_mode="GENERAL",
        session_factory=_noop_sf,
        session_ledger=build_financial_zeromem_ledger([], request_as_of_date=date(2026, 11, 27)),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"FPT"}),
        retriever_override=retr,
        table_store_override=_StubTableStore(),
    )
    brief = ResearchBrief(
        user_concern="Phân tích chi tiết FPT quý gần nhất",
        answer_shape="health_check",
        entities=BriefEntities(tickers=["FPT"]),
        claims=[
            ClaimSpec(
                id="K1",
                statement="Đánh giá tăng trưởng doanh thu và động lực kinh doanh của FPT",
                evidence=["fact:revenue_net", "doc:QUARTER_NOTES|kết quả kinh doanh"],
                period="Q3/2026",
            )
        ],
    )
    res = await execute_brief_claims(
        brief=brief,
        located=LocatedTargets(analysis_tickers=["FPT"]),
        ctx=ctx,
        model=_StubMiniReaderModel(),
    )
    rendered = "\n\n".join(c.render_for_synthesis() for c in res.claim_cards)
    # 1) Segment & Note 6 deterministic drivers automatically enriched!
    assert "CNTT nước ngoài" in rendered
    assert "97,34%" in rendered or "97.34%" in rendered
    # 2) Multi-slot sweep pulled BCTN_CORE (16.751 tỷ Chuyển đổi số) & AGM_TARGETS (2 Nhà máy AI)!
    assert "16.751" in rendered
    assert "Nhà máy AI" in rendered


def test_l2_synthesis_history_strips_prior_badges_and_tables_preventing_c11_echo() -> None:
    raw_history = [
        {"role": "user", "content": "Phân tích chi tiết mã FPT trong quý gần nhất"},
        {
            "role": "assistant",
            "content": (
                "## Kết luận trực diện: FPT tích cực\n\n"
                "| Chỉ tiêu | Giá trị |\n|---|---|\n| ROE | 25,13% [CALC-12] |\n\n"
                "- Mảng Dịch vụ Chuyển đổi số đạt 16.751 tỷ đồng, tăng 16,8% [C11].\n"
                "- Vốn hóa 99.377,8 tỷ đồng [CALC-11]."
            ),
            "primary_ticker": "FPT",
            "user_concern": "Phân tích chi tiết FPT Q3/2026",
        },
    ]
    ledger = build_financial_zeromem_ledger(raw_history, request_as_of_date=date(2026, 11, 27))
    # Stage 1 planner still gets full verbatim conversation prefix
    planner_msgs, _, _ = build_conversation_prefix_messages(ledger)
    assert any("[C11]" in m.content for m in planner_msgs if m.role == "assistant")

    # Stage 6 synthesizer gets badge-scrubbed, table-free conversational context so it NEVER echoes [C11] or tables!
    synth_msgs = build_synthesis_history_messages(ledger)
    assert len(synth_msgs) == 2
    assistant_ctx = synth_msgs[1].content
    assert "[C11]" not in assistant_ctx
    assert "[CALC-12]" not in assistant_ctx
    assert "16.751" not in assistant_ctx
    assert "16,8%" not in assistant_ctx
    assert "|---|---|" not in assistant_ctx
    assert "FPT" in assistant_ctx


def test_l1_stage7_verifies_segment_and_trajectory_bps_zero_false_alarms_and_catches_fake_bps() -> None:
    seg_tables = _fpt_q3_2026_segment_tables()
    drivers = extract_segment_drivers(seg_tables, "FPT")
    traces = [{**d["calc_trace"], "calc_id": f"CALC-{idx}"} for idx, d in enumerate(drivers, 1)]

    # Positive: exact +147 bps, +333 bps, +3,463 bps from FPT segment report must pass Stage 7 cleanly
    valid_prose = (
        "| CNTT nước ngoài | 28,538.0 | 70.25% [CALC-1] | +11.59% | 15.32% | +147 bps [CALC-1] |\n"
        "| CNTT trong nước | 6,976.7 | 17.17% [CALC-2] | +29.78% | 6.36% | +333 bps [CALC-2] |\n"
        "| Đầu tư, giáo dục & khác | 5,108.7 | 12.58% [CALC-3] | -5.68% | 65.07% | +3,463 bps [CALC-3] |"
    )
    ok_audit = verify_numeric_claims_against_evidence(
        valid_prose,
        sections_by_cid={},
        calculator_traces=traces,
    )
    assert ok_audit.unsupported_claims == [], f"Unexpected Stage 7 bps flags: {ok_audit.unsupported_claims}"

    # Adversarial: invented +999 bps MUST still fail closed!
    bad_audit = verify_numeric_claims_against_evidence(
        "Mảng CNTT nước ngoài mở rộng biên +999 bps [CALC-1].",
        sections_by_cid={},
        calculator_traces=traces,
    )
    assert len(bad_audit.unsupported_claims) == 1
    assert "999" in bad_audit.unsupported_claims[0]["claim_text"]


def test_l2_followup_suitability_brief_unsupported_cagr_or_beta_recovers_partial() -> None:
    from sag_api.research.brief import validate_research_brief

    raw = {
        "user_concern": "Đánh giá FPT theo khẩu vị biến động thấp, lợi nhuận 10-15%/năm, tích sản >5 năm",
        "answer_shape": "verdict_with_reasons",
        "entities": {"tickers": ["FPT"], "anchor_ticker": None, "candidate_search": None},
        "premises_to_check": [
            {
                "id": "P2",
                "statement": "FPT duy trì tăng trưởng lợi nhuận ổn định ở mức 10-15%/năm",
                "evidence": ["kpi:npat_cagr_5y"],
                "period": "FY2025",
            }
        ],
        "claims": [
            {
                "id": "K1",
                "statement": "Chất lượng dòng tiền và đòn bẩy an toàn cho tích sản dài hạn",
                "evidence": ["kpi:roe", "kpi:debt_to_equity", "kpi:net_income_growth", "mem"],
                "period": "Q3/2026",
            },
            {
                "id": "K2",
                "statement": "Độ biến động giá cổ phiếu thấp theo khẩu vị nhà đầu tư",
                "evidence": ["fact:beta"],
                "period": "Q3/2026",
            },
        ],
    }
    out = validate_research_brief(
        raw,
        as_of_date=date(2026, 11, 28),
        catalog_profiles=[{"ticker": "FPT", "company_name": "FPT Corp", "industry": "Công nghệ"}],
    )
    assert out.ok is True, f"Expected graceful PARTIAL survival on concrete ticker, got errors={out.errors}"
    assert out.brief is not None
    assert out.brief.needs_clarification is False
    assert out.brief.entities.tickers == ["FPT"]
    # `kpi:net_income_growth` canonicalized cleanly to `kpi:npat_growth_yoy`
    assert "kpi:npat_growth_yoy" in out.brief.claims[0].evidence
    # `P2` and `K2` keep dropped_by_claim_id so VerdictCards stay honestly PARTIAL while testing nearest BCTC/risk!
    assert out.dropped_by_claim_id.get("P2")
    assert out.dropped_by_claim_id.get("K2")
    assert "kpi:npat_growth_yoy" in out.brief.premises_to_check[0].evidence
    assert out.brief.claims[1].evidence == ["risk"]


def test_l1_stage7_allows_stitched_adjacent_page_sibling_same_doc_and_blocks_cross_doc_or_fake() -> None:
    sections_by_cid = {
        "C6": {
            "ticker": "FPT",
            "doc_id": "doc-q3-notes",
            "page_from": 9,
            "page_to": 9,
            "heading": "Bảng so sánh tương đương",
            "content": "Doanh thu điều chỉnh tăng +12,1%, LNST +13,2%, lũy kế 9T đạt 40.023 tỷ đồng (+12,4%).",
        },
        "C7": {
            "ticker": "FPT",
            "doc_id": "doc-q3-notes",
            "page_from": 8,
            "page_to": 8,
            "heading": "Giải trình biến động KQKD",
            "content": "Từ 01/01/2026 Tập đoàn thay đổi phương pháp hợp nhất đối với FTEL như bảng sau:",
        },
        "C9": {
            "ticker": "FPT",
            "doc_id": "doc-other",
            "page_from": 8,
            "page_to": 8,
            "heading": "Tài liệu khác",
            "content": "Giá trị hợp đồng khác 88.888 tỷ đồng.",
        },
    }
    # Same (ticker, doc_id) on page 8 & page 9 -> stitched continuation table supported cleanly!
    ok_res = verify_numeric_claims_against_evidence(
        "Trên cơ sở tương đương sau tách FTEL, doanh thu Q3 tăng +12,1% và 9T đạt 40.023 tỷ đồng [C7].",
        sections_by_cid=sections_by_cid,
    )
    assert ok_res.unsupported_claims == []

    # Different doc_id (`doc-other` vs `doc-q3-notes`) MUST still fail closed!
    cross_doc = verify_numeric_claims_against_evidence(
        "Giá trị hợp đồng đạt 88.888 tỷ đồng [C7].",
        sections_by_cid=sections_by_cid,
    )
    assert len(cross_doc.unsupported_claims) == 1
