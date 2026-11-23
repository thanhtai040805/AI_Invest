"""Comprehensive regression & edge-case tests for SAG Canonical Pipeline Remediation (Phases 0–5).

Covers:
- Phase 0: Optional ticker, GENERAL/MOAT mode, N8b Auth-Before-Action, Q1=A org_id default.
- Phase 1: N2 ZIP multi-part merge + fail-closed BAD_FONT, N3 short AGM AGM_TARGETS, F9 CTG \\x16 repair,
  F4 scanned watermark + audited_fs_missing_warning, F5 tightened sidebar detector, F7/F10 BCTC span & heading hygiene.
- Phase 2: F12 MinerU HTML table safe_break_line, N10 YAML frontmatter skip, F15 document_title L0,
  F13 section_path <= 512 clamp, F6 HTML & digital block_type='table' bypassing C99.
- Phase 3: N4a-b column classification (code/note_ref/period_amount/component_amount),
  N5 multi-table lattice + HTML stitching, F3 0 table_id collisions, Q3=A grouped table_card
  CanonicalBlock + SearchUnit + Qdrant payload table_id + KnowledgeUnit skip + FK-safe re-run.
- Phase 4: F1 verbatim single-block quote_hash (>= 99% PASS), N9 [C99] HALLUCINATED_CITATION_ID
  100% rejection, N4c Calculator/Discrepancy skipping Mã số/Thuyết minh/Giá gốc-Dự phòng/LNST
  chưa phân phối/Note sub-tables, 4a-4e Pre-Retrieval & Multi-Hop (soft BCTN_PR prior, contextual
  query rewrite, dynamic thematic Universe routing + NO_DOC_COVERAGE, Vietnamese multi-hop
  decomposition + dense/sparse subqueries + 1-round bridge Hop-2, adaptive top_k & diversity
  balance, relevance floor / evidence sufficiency gate).
- Phase 5: F11a financial knowledge config, N7a-b research_tree_build_config & temporal_from_ts/
  temporal_to_ts extraction, F11b table-safe MinHash & dedup guard, F16 PIT restatement
  set_document_points_valid_to_in_qdrant & 0% leakage.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import fitz
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, Source
from sag_api.parsing.canonical import extract_canonical_blocks
from sag_api.parsing.mineru import _inject_page_markers_from_content_list
from sag_api.research.chat_orchestrator import (
    ResearchChatOrchestrator,
    RetrievalResult,
    RetrievedResearchSection,
    _parse_citations_from_answer,
    _run_deterministic_calculator,
    _run_discrepancy_check,
    apply_slot_ranking_prior,
    balance_sections_by_ticker_and_subquery,
    collect_stream_result,
    compute_adaptive_top_k,
    evaluate_evidence_sufficiency,
    extract_missing_bridge_queries,
    extract_query_tickers,
    rewrite_contextual_query,
)
from sag_api.research.discrepancy_detector import extract_audited_baselines_from_tables
from sag_api.research.input_inspector import (
    inspect_pdf_bytes,
    normalize_ctg_font_quirks,
    unwrap_pdf_payload,
)
from sag_api.research.quote_verifier import (
    CanonicalBlockRecord,
    verify_citations,
)
from sag_api.research.table_engine import (
    ExtractedTableRecord,
    build_grouped_table_search_cards,
    classify_table_columns,
    extract_tables_from_document,
)
from sag_api.sag.search_unit_store import build_search_filter
from sag_api.schemas.research_chat import HydratedTableItem, ResearchChatStreamRequest
from sag_api.services.dedup_and_temporal_service import _dedup_candidates, tokenize_for_minhash
from sag_api.services.query_analysis import decompose_query_clauses, extract_query_features
from sag_api.services.search_index_service import (
    build_search_units_from_blocks,
    index_table_search_cards_for_version,
    set_document_points_valid_to_in_qdrant,
)
from sag_api.services.tree_query_snapshot_service import _extract_manifest_node_features


def _principal(allowed: frozenset[str] = frozenset({"*"}), tenant: str = "aiinvest-default") -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="tester",
        organization_id=tenant,
        tenant_id=tenant,
        allowed_project_ids=allowed,
        allowed_partition_ids=allowed,
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-1",
        issued_at=1_700_000_000,
        expires_at=1_900_000_000,
        conversation_scope="all",
    )


def _make_pdf_bytes(pages: list[str]) -> bytes:
    doc = fitz.open()
    for txt in pages:
        pg = doc.new_page()
        pg.insert_text((50, 50), txt)
    raw = doc.tobytes()
    doc.close()
    return raw


# ── Phase 1 & 2 Tests ────────────────────────────────────────────────────────


def test_zip_multipart_merge_and_short_agm_and_ctg_x16() -> None:
    p1 = _make_pdf_bytes(["NGHI QUYET DAI HOI DONG CO DONG\nDieu 1: Doanh thu 10.000 ty dong."])
    p2 = _make_pdf_bytes(["Dieu 2: Loi nhuan sau thue 1.500 ty dong."])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("agm_2025_p2.pdf", p2)
        zf.writestr("agm_2025_p1.pdf", p1)
    merged, was_zip, selected_name, _ = unwrap_pdf_payload(buf.getvalue())
    assert was_zip is True
    assert "agm_2025_p1.pdf" in selected_name
    doc = fitz.open(stream=merged, filetype="pdf")
    assert len(doc) == 2
    assert "Dieu 1" in doc[0].get_text("text")
    assert "Dieu 2" in doc[1].get_text("text")
    doc.close()

    # Short 5-page AGM (>= 100 chars/page) must NOT be misclassified as NOTICE_ONLY
    short_agm = _make_pdf_bytes(
        [
            (
                f"NGHI QUYET DAI HOI DONG CO DONG THUONG NIEN NAM 2025\n"
                f"Dieu {i + 1}: Thong qua ke hoach doanh thu {5000 + i * 100} ty dong va "
                f"loi nhuan sau thue 1.250 ty dong, chia co tuc bang tien mat 15%."
            )
            for i in range(5)
        ]
    )
    insp = inspect_pdf_bytes(short_agm, doc_type="NQ_DHDCD")
    assert insp.doc_class == "DIGITAL"

    # F9: CTG/MWG ctrl-char & soft-hyphen/tab/nbsp 'đ' repair with zero stray \x16 remaining
    repaired = normalize_ctg_font_quirks(
        "Tổng Giám \x16ốc và \x16ẠI HỘI \x16ỒNG CỔ \x16ÔNG\x16 | "
        "Hội \x05ồng, \x85ược, \x10ã, \x7fiều hành, nghìn tỷ \xadồng, tốc \tộ, \xa0ầu năm"
    )
    assert "\x16" not in repaired
    assert "Tổng Giám đốc" in repaired
    assert "ĐẠI HỘI ĐỒNG CỔ ĐÔNG" in repaired
    assert "Hội đồng, được, đã, điều hành, nghìn tỷ đồng, tốc độ, đầu năm" in repaired


def test_mineru_safe_break_and_canonical_yaml_html_digital_and_clamp() -> None:
    # F12: MinerU page marker must NEVER split inside an HTML <table>
    md_html = (
        "Intro paragraph on page 1\n"
        "<table>\n<tr><td>Row A alpha</td></tr>\n<tr><td>Row B beta</td></tr>\n</table>\n"
        "After paragraph on page 2"
    )
    marked = _inject_page_markers_from_content_list(
        md_html,
        [
            {"page_idx": 0, "text": "Intro paragraph on page 1"},
            {"page_idx": 1, "text": "Row B beta"},
        ],
    )
    tbl_start = marked.index("<table>")
    tbl_end = marked.index("</table>") + len("</table>")
    assert "<!-- PAGE" not in marked[tbl_start:tbl_end]

    # N10 + F15 + F13 + F6: YAML skipped, document_title L0, section_path <= 512, HTML & digital table
    normal_md = (
        "---\ntitle: Meta\nstatus: ACTIVE\n---\n\n"
        "# PHẦN I\n## KQKD\n\n"
        "<table>\n<tr><th>Chi tieu</th><th>2025</th><th>2024</th></tr>\n"
        "<tr><td>Doanh thu</td><td>10.000</td><td>8.000</td></tr>\n</table>\n\n"
        "Doanh thu ban hang 10.000.000 8.500.000\n"
        "Gia von hang ban 6.000.000 5.100.000\n"
        "Loi nhuan gop 4.000.000 3.400.000\n"
    )
    blks = extract_canonical_blocks(normal_md, version_id="v1", document_title="ROOT_DOC")
    assert not any("status: ACTIVE" in b.normalized_text for b in blks)
    assert all(b.section_path.startswith("Meta") for b in blks)
    tbl_blocks = [b for b in blks if b.block_type == "table"]
    assert len(tbl_blocks) >= 2

    no_fm_blks = extract_canonical_blocks("# PHẦN I\n## KQKD\n\nBody text.", version_id="v1", document_title="ROOT_DOC")
    assert all(b.section_path.startswith("ROOT_DOC") for b in no_fm_blks)

    long_h = "A" * 280
    long_blks = extract_canonical_blocks(
        f"# {long_h}\n## {long_h}\n\nParagraph text.",
        version_id="v1",
        document_title="ROOT_DOC",
    )
    assert all(len(b.section_path) <= 512 for b in long_blks)


# ── Phase 3 Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_phase3_column_classification_zero_collisions_and_q3a_table_cards() -> None:
    headers = ["Chỉ tiêu", "Mã số", "Thuyết minh", "31/12/2025", "31/12/2024"]
    rows = [
        {"label": "Doanh thu thuần", "values": [10.0, 5.1, 60_000.0, 50_000.0]},
        {"label": "Lợi nhuận sau thuế", "values": [60.0, 5.2, 12_000.0, 10_000.0]},
        {"label": "Lợi nhuận sau thuế chưa phân phối", "values": [421.0, None, 25_000.0, 20_000.0]},
    ]
    roles, amt_indices = classify_table_columns(headers, rows)
    assert roles == ["label", "code", "note_ref", "period_amount", "period_amount"]
    assert amt_indices == [2, 3]

    # Component columns (Giá gốc vs Dự phòng) must NOT be classified as period_amount
    comp_roles, _ = classify_table_columns(
        ["Chỉ tiêu", "Giá gốc", "Dự phòng"],
        [{"label": "Phải thu ngắn hạn", "values": [500.0, -50.0]}],
    )
    assert comp_roles == ["label", "component_amount", "component_amount"]

    # F3: Two identical-title tables on the same page with different table_idx / virtual_side MUST NOT collide
    raw_extracted = {
        "headers": ["Chỉ tiêu", "2025", "2024"],
        "rows": [
            [
                {"raw_text": "Doanh thu", "numeric_value": None},
                {"raw_text": "100", "numeric_value": 100.0},
                {"raw_text": "80", "numeric_value": 80.0},
            ],
            [
                {"raw_text": "Tổng cộng", "numeric_value": None},
                {"raw_text": "100", "numeric_value": 100.0},
                {"raw_text": "80", "numeric_value": 80.0},
            ],
        ],
        "currency": "VND",
        "unit_raw": "tỷ đồng",
        "unit_multiplier": 1e9,
    }
    now_dt = datetime(2026, 4, 15, tzinfo=UTC)
    r_l = ExtractedTableRecord.from_extraction(
        project_id="FPT", document_version_id="v1", sub_doc_slot="BCTC_AUDITED",
        fiscal_year=2025, table_title="KQKD", page_from=10, page_to=10,
        extracted=raw_extracted, valid_from=now_dt, virtual_side="L", table_idx=0,
    )
    r_r = ExtractedTableRecord.from_extraction(
        project_id="FPT", document_version_id="v1", sub_doc_slot="BCTC_AUDITED",
        fiscal_year=2025, table_title="KQKD", page_from=10, page_to=10,
        extracted=raw_extracted, valid_from=now_dt, virtual_side="R", table_idx=1,
    )
    assert r_l.table_id != r_r.table_id

    # HTML cross-page stitching in extract_tables_from_document
    html_md = (
        "<!-- PAGE 5 -->\n# BẢNG CÂN ĐỐI KẾ TOÁN\nĐơn vị tính: tỷ đồng\n"
        "<table><tr><th>Chỉ tiêu</th><th>31/12/2025</th><th>31/12/2024</th></tr>"
        "<tr><td>Tài sản ngắn hạn</td><td>1.000</td><td>900</td></tr>"
        "<tr><td>Tiền mặt</td><td>200</td><td>150</td></tr></table>\n"
        "<!-- PAGE 6 -->\n(Tiếp theo)\n"
        "<table><tr><th>Chỉ tiêu</th><th>31/12/2025</th><th>31/12/2024</th></tr>"
        "<tr><td>Tài sản dài hạn</td><td>500</td><td>400</td></tr>"
        "<tr><td>Tổng cộng tài sản</td><td>1.500</td><td>1.300</td></tr></table>"
    )
    stitched_recs = extract_tables_from_document(
        pdf_bytes=None, project_id="PVT", document_version_id="v1",
        sub_doc_slot="BCTC_AUDITED", fiscal_year=2025, page_numbers=[5, 6],
        markdown_content=html_md, valid_from=now_dt,
    )
    assert len(stitched_recs) == 1
    assert stitched_recs[0].page_from == 5 and stitched_recs[0].page_to == 6
    assert "BẢNG CÂN ĐỐI KẾ TOÁN" in stitched_recs[0].section_path

    # Statement-boundary guard: B02 (KQKD) MUST NOT over-stitch with B03 (LCTT) despite identical headers
    from sag_api.research.table_engine import stitch_cross_page_tables

    def _row(lbl: str, v1: float, v2: float) -> list[dict[str, Any]]:
        return [
            {"raw_text": lbl, "numeric_value": None},
            {"raw_text": str(v1), "numeric_value": v1},
            {"raw_text": str(v2), "numeric_value": v2},
        ]

    t_is = {
        "headers": ["Chỉ tiêu", "Năm nay", "Năm trước"],
        "rows": [
            _row("1 Doanh thu bán hàng", 100.0, 80.0),
            _row("2 Doanh thu thuần", 100.0, 80.0),
            _row("3 Giá vốn hàng bán", 60.0, 50.0),
            _row("4 Lợi nhuận gộp", 40.0, 30.0),
        ],
        "virtual_seq": 10,
        "sub_doc_slot": "BCTC_AUDITED",
        "page_from": 147,
        "page_to": 147,
        "section_path": "BCTC_AUDITED > BÁO CÁO KẾT QUẢ HOẠT ĐỘNG",
    }
    t_cf = {
        "headers": ["Chỉ tiêu", "Năm nay", "Năm trước"],
        "rows": [
            _row("Lưu chuyển tiền từ hoạt động kinh doanh", 30.0, 25.0),
            _row("Tiền và tương đương tiền cuối năm", 50.0, 40.0),
        ],
        "virtual_seq": 11,
        "sub_doc_slot": "BCTC_AUDITED",
        "page_from": 148,
        "page_to": 148,
        "section_path": "BCTC_AUDITED > BÁO CÁO LƯU CHUYỂN TIỀN TỆ HỢP NHẤT",
    }
    assert len(stitch_cross_page_tables([t_is, t_cf])) == 2

    # Q3=A: Grouped table cards persisted as CanonicalBlock(block_type="table_card") & atomic SearchUnits
    cards = build_grouped_table_search_cards(r_l.to_dict(), rows_per_card=6)
    assert len(cards) == 1
    assert cards[0]["table_id"] == r_l.table_id

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as session:
        src = Source(id="src-1", name="FPT", sag_source_config_id="cfg-1")
        doc = Document(
            id="doc-1",
            source_id="src-1",
            project_id="FPT",
            tenant_id="aiinvest-default",
            filename="fpt.md",
            storage_path="/tmp/fpt.md",
        )
        ver = DocumentVersion(id="ver-1", document_id="doc-1", version_no=1, file_hash="h1", valid_from=now_dt)
        session.add_all([src, doc, ver])
        await session.flush()

        n_cards = await index_table_search_cards_for_version(
            session, document_version_id="ver-1", project_id="FPT", table_records=[r_l],
        )
        assert n_cards == 1
        blks = (
            await session.scalars(select(CanonicalBlock).where(CanonicalBlock.document_version_id == "ver-1"))
        ).all()
        assert len(blks) == 1
        assert blks[0].block_type == "table_card"
        assert blks[0].source_anchor == f"table_card:{r_l.table_id}:0"

        units = build_search_units_from_blocks(blks, document_version_id="ver-1", security_partition_id="FPT")
        assert len(units) == 1
        assert units[0].block_from_id == blks[0].id
        # Persist SearchUnit + a prose block, then re-run index_table_search_cards_for_version:
        # MUST delete dependent SearchUnits before table_card blocks (0 FK crash) & enqueue REPROCESS!
        session.add(units[0])
        session.add(
            CanonicalBlock(
                id="prose-b0",
                document_version_id="ver-1",
                ordinal=0,
                block_type="paragraph",
                page_from=1,
                page_to=1,
                section_path="BCTC_AUDITED",
                normalized_text="Thuyết minh báo cáo tài chính hợp nhất.",
                content_hash="h-prose",
            )
        )
        await session.flush()

        n_cards_rerun = await index_table_search_cards_for_version(
            session, document_version_id="ver-1", project_id="FPT", table_records=[r_l],
        )
        assert n_cards_rerun == 1
        from sag_api.db.models import Job
        from sag_api.enums import JobType

        reproc_jobs = (
            await session.scalars(
                select(Job).where(Job.document_id == "doc-1", Job.type == JobType.REPROCESS_DOCUMENT)
            )
        ).all()
        assert len(reproc_jobs) == 1
    await engine.dispose()


# ── Phase 4 Tests (G1, G2, Pre-Retrieval & Multi-Hop) ────────────────────────


def test_phase4_quote_verifier_c99_and_calculator_discrepancy_hygiene() -> None:
    # F1 + N9: Multi-block SearchUnit verbatim single-block quote passes 100%, while [C99] is 100% rejected!
    b1 = CanonicalBlockRecord(
        document_version_id="v1", page_from=10, page_to=10,
        normalized_text="I. KẾT QUẢ KINH DOANH", sub_doc_slot="BCTN_CORE",
        published_date=date(2026, 4, 10), block_id="b1", ordinal=1,
    )
    b2 = CanonicalBlockRecord(
        document_version_id="v1", page_from=11, page_to=11,
        normalized_text="Doanh thu thuần năm 2025 đạt 62.849 tỷ đồng, tăng trưởng 19,4% so với năm trước.",
        sub_doc_slot="BCTN_CORE", published_date=date(2026, 4, 10), block_id="b2", ordinal=2,
    )
    sec = RetrievedResearchSection(
        ticker="FPT", doc_id="d1", document_version_id="v1", sub_doc_slot="BCTN_CORE",
        page_from=10, page_to=11, published_date=date(2026, 4, 10),
        content=f"{b1.normalized_text}\n\n{b2.normalized_text}",
        block_from_id="b1", block_to_id="b2",
    )
    blocks_map = {"v1": [b1, b2]}
    ans = "Doanh thu FPT đạt 62.849 tỷ đồng [C1] và bịa thêm nguồn [C99]."
    cands = _parse_citations_from_answer(ans, [sec], blocks_by_version=blocks_map)
    assert len(cands) == 1
    assert cands[0]["page"] == 11  # Exact page of b2, NOT b1!

    ver = verify_citations(cands, blocks_map, date(2026, 11, 20), answer_text=ans, valid_citation_ids={"C1"})
    assert len(ver.verified_citations) == 1
    assert any(r["citation_id"] == "C99" and r["verified"] is False for r in ver.rejected_citations)
    assert "[C99 UNVERIFIED_CLAIM]" in ver.sanitized_answer
    assert any(bs["code"] == "HALLUCINATED_CITATION_ID" and bs["scope"] == "C99" for bs in ver.blindspots)

    # N4c: Calculator MUST skip Mã số / Thuyết minh AND NEVER calculate YoY on Giá gốc vs Dự phòng
    vas_table = HydratedTableItem(
        table_id="t-vas", title="Báo cáo KQKD", sub_doc_slot="BCTC_AUDITED",
        page_from=5, page_to=5, currency="VND", unit_raw="tỷ đồng", unit_multiplier=1e9,
        verification_status="VERIFIED",
        headers=[["Chỉ tiêu", "Mã số", "Thuyết minh", "Năm 2025", "Năm 2024"]],
        rows=[
            {"label": "Doanh thu thuần", "values": [10.0, 5.1, 60000.0, 50000.0]},
            {"label": "Lợi nhuận sau thuế", "values": [60.0, 5.2, 12000.0, 10000.0]},
        ],
    )
    prov_table = HydratedTableItem(
        table_id="t-prov", title="Phải thu khách hàng", sub_doc_slot="BCTC_AUDITED",
        page_from=20, page_to=20, currency="VND", unit_raw="tỷ đồng", unit_multiplier=1e9,
        verification_status="VERIFIED",
        headers=[["Chỉ tiêu", "Giá gốc", "Dự phòng"]],
        rows=[{"label": "Phải thu ngắn hạn", "values": [5000.0, -250.0]}],
    )
    traces = _run_deterministic_calculator(
        query="Lợi nhuận sau thuế tăng bao nhiêu?",
        hydrated_tables=[vas_table, prov_table],
        graph_hops=[],
    )
    assert len(traces) == 1
    assert traces[0].formatted == "+20.00%"  # 12000 vs 10000 (skips Mã số/TM/Giá gốc-Dự phòng)

    # N4c + F8b: Discrepancy skips LNST chưa phân phối, Balance Sheet flow rows, and sub-note tables
    note_rev_table = {
        "table_id": "t-note",
        "title": "Thuyết minh báo cáo tài chính hợp nhất > 29. Chi phí tài chính",
        "section_path": "BCTC_AUDITED > THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT > 29. Chi phí tài chính",
        "sub_doc_slot": "BCTC_AUDITED",
        "page_from": 210,
        "currency": "VND",
        "unit_raw": "tỷ đồng",
        "unit_multiplier": 1e9,
        "verification_status": "VERIFIED",
        "headers": [["Chỉ tiêu", "2025", "2024"]],
        "rows": [{"label": "Doanh thu bán hàng hóa", "values": [952.79, 575.30]}],
    }
    bs_and_is = [
        note_rev_table,
        {
            "table_id": "t-bs", "title": "Bảng cân đối kế toán", "sub_doc_slot": "BCTC_AUDITED",
            "page_from": 3, "currency": "VND", "unit_raw": "tỷ đồng", "unit_multiplier": 1e9,
            "verification_status": "VERIFIED",
            "headers": [["Chỉ tiêu", "Mã số", "Thuyết minh", "31/12/2025", "31/12/2024"]],
            "rows": [
                {"label": "Lợi nhuận sau thuế chưa phân phối", "values": [421.0, 21.0, 25000.0, 20000.0]},
                {"label": "Lợi nhuận sau thuế đã thực hiện", "values": [421.0, None, 1542.0, 1100.0]},
            ],
        },
        vas_table.model_dump(),
    ]
    baselines = extract_audited_baselines_from_tables(bs_and_is)
    assert baselines["npat"].value == 12000.0
    assert baselines["npat"].citation == "BCTC_AUDITED·P5"
    assert baselines["net_revenue"].value == 60000.0  # NEVER Note P210 952.79!

    # Note sub-table alone MUST NOT populate consolidated net_revenue
    assert "net_revenue" not in extract_audited_baselines_from_tables([note_rev_table])

    # F8c: _run_discrepancy_check MUST NOT feed BCTC_AUDITED into narrative_text (0 self-comparison alerts)
    bctc_sec = RetrievedResearchSection(
        ticker="FPT", doc_id="d1", document_version_id="v1", sub_doc_slot="BCTC_AUDITED",
        page_from=5, page_to=5, published_date=date(2026, 4, 10),
        content="Lợi nhuận sau thuế đạt 9.000 tỷ đồng.",
    )
    assert _run_discrepancy_check(sections=[bctc_sec], hydrated_tables=[vas_table]) == []

    # Real PR mismatch vs audited table DOES alert with full Natural 4-Class slot names
    pr_sec = RetrievedResearchSection(
        ticker="FPT", doc_id="d2", document_version_id="v2", sub_doc_slot="BCTN_PR",
        page_from=2, page_to=2, published_date=date(2026, 4, 10),
        content="Lợi nhuận sau thuế năm 2025 đạt 15.000 tỷ đồng.",
    )
    disc = _run_discrepancy_check(sections=[pr_sec], hydrated_tables=[vas_table])
    assert len(disc) == 1
    assert disc[0].citations == ["BCTN_PR·P2", "BCTC_AUDITED·P5"]


@pytest.mark.asyncio
async def test_phase4_pre_retrieval_multihop_and_freeform_chat() -> None:
    # 4b: Contextual query rewrite & dynamic ticker extraction (0% hardcode)
    rewritten = rewrite_contextual_query(
        "Còn biên lợi nhuận gộp của công ty này thì sao?",
        [{"role": "user", "content": "Phân tích doanh thu năm 2025 của FPT"}],
        active_tickers=["FPT"],
    )
    assert "FPT" in rewritten and "doanh thu năm 2025" in rewritten

    matched, unknown = extract_query_tickers(
        "So sánh ROE, LPG, HRC, PPA, EVN, BKS của @FPT và VCB về doanh thu VND",
        available_tickers={"FPT", "MWG", "PVT"},
    )
    assert matched == ["FPT"]
    assert unknown == ["VCB"]  # Domain acronyms filtered out; uningested VCB caught!

    # 4c: Vietnamese compound multi-hop decomposition
    q_mh = "Phân tích cơ cấu nợ vay của PVT và tác động đến chi phí lãi vay, đồng thời so sánh với kế hoạch ĐHĐCĐ"
    clauses = decompose_query_clauses(q_mh)
    assert len(clauses) >= 2
    assert extract_query_features(q_mh).multi_hop is True

    # 4a: BCTN_PR is NOT hard-excluded in GENERAL mode
    s_pr = RetrievedResearchSection(
        ticker="FPT", doc_id="d1", document_version_id="v1", sub_doc_slot="BCTN_PR",
        page_from=2, page_to=2, published_date=date(2026, 4, 1), content="Thư Chủ tịch HĐQT", score=0.9,
    )
    s_core = RetrievedResearchSection(
        ticker="FPT", doc_id="d2", document_version_id="v2", sub_doc_slot="BCTN_CORE",
        page_from=10, page_to=10, published_date=date(2026, 4, 1), content="Kết quả kinh doanh", score=0.5,
    )
    ranked_gen = apply_slot_ranking_prior([s_core, s_pr], query="Thông điệp Chủ tịch FPT", mode="GENERAL")
    assert ranked_gen[0].sub_doc_slot == "BCTN_PR"
    assert len(ranked_gen) == 2

    # 4d: Adaptive top_k & balanced per-ticker allocation
    assert (
        compute_adaptive_top_k(
            resolved_query="Doanh thu FPT 2025?",
            tickers=["FPT"],
            sub_queries=["Doanh thu FPT 2025?"],
        )
        == 10
    )
    assert compute_adaptive_top_k(resolved_query=q_mh, tickers=["FPT", "MWG"], sub_queries=list(clauses)) >= 18

    fpt_secs = [
        RetrievedResearchSection(
            ticker="FPT",
            doc_id=f"df{i}",
            document_version_id="vf",
            sub_doc_slot="BCTN_CORE",
            page_from=i + 1,
            page_to=i + 1,
            published_date=date(2026, 4, 1),
            content=f"FPT content {i}",
            score=0.9 - i * 0.01,
        )
        for i in range(6)
    ]
    mwg_secs = [
        RetrievedResearchSection(
            ticker="MWG",
            doc_id=f"dm{i}",
            document_version_id="vm",
            sub_doc_slot="BCTN_CORE",
            page_from=i + 1,
            page_to=i + 1,
            published_date=date(2026, 4, 1),
            content=f"MWG content {i}",
            score=0.7 - i * 0.01,
        )
        for i in range(3)
    ]
    balanced = balance_sections_by_ticker_and_subquery(
        [*fpt_secs, *mwg_secs],
        tickers=["FPT", "MWG"],
        sub_queries=[],
        limit=4,
    )
    assert sum(1 for s in balanced if s.ticker == "FPT") == 2
    assert sum(1 for s in balanced if s.ticker == "MWG") == 2

    # 4c: Conditional bridge Hop-2 detects missing Thuyết minh số 18 AND bridge corporate entities
    hop1_ref = RetrievedResearchSection(
        ticker="FPT", doc_id="d1", document_version_id="v1", sub_doc_slot="BCTC_AUDITED",
        page_from=5, page_to=5, published_date=date(2026, 4, 1),
        heading="BẢNG CÂN ĐỐI KẾ TOÁN",
        content=(
            "Chi tiết vay và nợ thuê tài chính tại Công ty TNHH Phần mềm FPT "
            "được trình bày tại Thuyết minh số 18."
        ),
    )
    bridges = extract_missing_bridge_queries([hop1_ref], query="Cấu trúc nợ vay FPT")
    assert any("Thuyết minh số 18" in b for b in bridges)
    assert any("Công ty TNHH Phần mềm FPT" in b for b in bridges)

    # 4e: Relevance floor + per-ticker & per-subquery evidence sufficiency gate
    low_sec = RetrievedResearchSection(
        ticker="FPT", doc_id="d1", document_version_id="v1", sub_doc_slot="BCTN_CORE",
        page_from=1, page_to=1, published_date=date(2026, 4, 1),
        content="Hoạt động từ thiện trồng cây xanh tại địa phương.", score=0.03,
    )
    bs_suff, abstain = evaluate_evidence_sufficiency(
        resolved_query="Tỷ lệ nợ xấu ngân hàng quý 3",
        explicit_tickers=["FPT", "MWG"],
        sections=[low_sec],
        hydrated_tables=[],
        graph_hops=[],
        sub_queries=["Hoạt động trồng cây xanh", "Kế hoạch cổ tức tiền mặt 2026"],
    )
    assert abstain is True
    assert any(b.code == "INSUFFICIENT_EVIDENCE" and b.scope == "MWG" for b in bs_suff)
    assert any(b.code == "INSUFFICIENT_EVIDENCE" and b.scope == "subquery:2" for b in bs_suff)
    assert any(b.code == "LOW_RELEVANCE_EVIDENCE" for b in bs_suff)

    # Free-form chat stream with ticker=None + 1-round conditional Hop-2
    calls: list[str] = []

    class _FakeMultiHopRetriever:
        async def retrieve(self, *, query: str, tickers, as_of_date: date, top_k: int) -> RetrievalResult:
            calls.append(query)
            if "Thuyết minh số 18" in query:
                b_note = CanonicalBlockRecord(
                    document_version_id="v1", page_from=30, page_to=30,
                    normalized_text="Thuyết minh 18: Vay ngắn hạn 12.000 tỷ đồng tại Vietcombank.",
                    sub_doc_slot="BCTC_AUDITED", published_date=date(2026, 4, 1), block_id="bn", ordinal=10,
                )
                return RetrievalResult(
                    sections=[
                        RetrievedResearchSection(
                            ticker="FPT", doc_id="d1", document_version_id="v1", sub_doc_slot="BCTC_AUDITED",
                            page_from=30, page_to=30, published_date=date(2026, 4, 1),
                            heading="18. Vay và nợ thuê tài chính", content=b_note.normalized_text, score=0.92,
                            block_from_id="bn", block_to_id="bn",
                        )
                    ],
                    canonical_blocks={"v1": [b_note]},
                )
            b_bs = CanonicalBlockRecord(
                document_version_id="v1", page_from=5, page_to=5,
                normalized_text="Vay ngắn hạn xem chi tiết tại Thuyết minh số 18.",
                sub_doc_slot="BCTC_AUDITED", published_date=date(2026, 4, 1), block_id="bb", ordinal=1,
            )
            return RetrievalResult(
                sections=[
                    RetrievedResearchSection(
                        ticker="FPT", doc_id="d1", document_version_id="v1", sub_doc_slot="BCTC_AUDITED",
                        page_from=5, page_to=5, published_date=date(2026, 4, 1),
                        heading="BẢNG CÂN ĐỐI KẾ TOÁN", content=b_bs.normalized_text, score=0.88,
                        block_from_id="bb", block_to_id="bb",
                    )
                ],
                canonical_blocks={"v1": [b_bs]},
            )

    orch = ResearchChatOrchestrator(retriever=_FakeMultiHopRetriever())
    req = ResearchChatStreamRequest(
        query="Chi tiết nợ vay của @FPT năm 2025",
        ticker=None,
        mode="GENERAL",
        as_of_date=date(2026, 11, 20),
        enable_kg=False,
    )
    answer, meta, _ = await collect_stream_result(orch.stream_chat(req, principal=_principal()))
    assert meta["chat_mode"] == "GENERAL"
    assert meta["primary_ticker"] == "FPT"
    assert len(calls) == 2  # Hop-1 + conditional bridge Hop-2 for Thuyết minh số 18!
    assert meta["telemetry"]["hipporag_hops"] >= 2
    assert "Thuyết minh 18" in answer or len(meta["citations"]) >= 1


# ── Phase 5 Tests (Dedup Table-Safe, Leiden PIT Timestamps, F16 Restatement) ─


@pytest.mark.asyncio
async def test_phase5_table_safe_dedup_leiden_pit_ts_and_f16_restatement() -> None:
    # F11b: Vietnamese dot-thousands stay intact in MinHash tokens & differing tables are NEVER merged
    toks = tokenize_for_minhash("Doanh thu 16.012.813 triệu đồng")
    assert "16012813" in toks

    b_2025 = SimpleNamespace(
        id="b1", document_version_id="v2", block_type="table",
        section_path="BCTC_AUDITED > KQKD", source_anchor="t1",
        normalized_text="Chỉ tiêu | 2025 | 2024\nDoanh thu thuần | 60.000 | 50.000\nLợi nhuận | 12.000 | 10.000",
        content_hash="h_2025", dedup_signature_json={"table_id": "t1", "sub_doc_slot": "BCTC_AUDITED"},
    )
    b_2024 = SimpleNamespace(
        id="b0", document_version_id="v1", block_type="table",
        section_path="BCTC_AUDITED > KQKD", source_anchor="t1",
        normalized_text="Chỉ tiêu | 2025 | 2024\nDoanh thu thuần | 60.500 | 50.000\nLợi nhuận | 12.000 | 10.000",
        content_hash="h_2024", dedup_signature_json={"table_id": "t1", "sub_doc_slot": "BCTC_AUDITED"},
    )
    same_sig = [1] * 128
    _, near_dups = _dedup_candidates([b_2025], [b_2024], {"b1": same_sig, "b0": same_sig}, None, "v2")
    assert near_dups == []  # Different numeric value (60.000 vs 60.500) -> NEVER merged!

    # N7b: _extract_manifest_node_features extracts exact epoch timestamps (ts_from, ts_to)
    feats = _extract_manifest_node_features(
        {
            "routing_profiles": [
                {
                    "partition_id": "FPT",
                    "node_id": "n1",
                    "dense_medoid": [1.0, 0.0],
                    "temporal_from": "2025-03-15T00:00:00+00:00",
                    "temporal_to": "2026-03-20T00:00:00+00:00",
                }
            ]
        }
    )
    medoid, y_range, ts_range = feats[("FPT", "n1")]
    assert medoid == (1.0, 0.0)
    assert y_range == (2025, 2026)
    assert ts_range == (
        int(datetime(2025, 3, 15, tzinfo=UTC).timestamp()),
        int(datetime(2026, 3, 20, tzinfo=UTC).timestamp()),
    )

    # F16: build_search_filter enforces valid_from_ts <= as_of_ts AND (valid_to_ts empty OR > as_of_ts)
    flt = build_search_filter(
        project_id="FPT", tenant_id="aiinvest-default", partition_id="FPT",
        document_version_ids=["v1", "v2"], as_of_ts=1_750_000_000,
    )
    assert {"key": "valid_from_ts", "range": {"lte": 1_750_000_000}} in flt["must"]
    assert {
        "should": [
            {"is_empty": {"key": "valid_to_ts"}},
            {"key": "valid_to_ts", "range": {"gt": 1_750_000_000}},
        ]
    } in flt["must"]

    # F16: set_document_points_valid_to_in_qdrant updates payload without deleting historical PIT points
    seen_calls: list[tuple[str, dict[str, Any]]] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        seen_calls.append((req.url.path, _json.loads(req.content)))
        return httpx.Response(200, json={"result": {"status": "completed"}})

    async with httpx.AsyncClient(base_url="https://qdrant.invalid", transport=httpx.MockTransport(_handler)) as qclient:
        updated = await set_document_points_valid_to_in_qdrant(
            qclient, project_id="FPT", document_version_ids=["ver-old"],
            valid_to_ts=1_775_000_000, valid_to_iso="2026-04-01T00:00:00+00:00",
        )
        assert updated == 1
    assert len(seen_calls) == 1
    assert seen_calls[0][0].endswith("/points/payload")
    assert seen_calls[0][1]["payload"]["valid_to_ts"] == 1_775_000_000


@pytest.mark.asyncio
async def test_thematic_universe_routing_and_table_tenant_isolation() -> None:
    """4b + Security: Dynamic thematic Universe routing picks PVT over alphabetical truncation;
    SQLAlchemyTableStore enforces tenant_id isolation under wildcard '*' principal scope."""
    from sag_api.db.models.stock_research import KgNode, SagExtractedTable
    from sag_api.research.chat_orchestrator import SQLAlchemyTableStore

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    now_dt = datetime(2026, 4, 15, tzinfo=UTC)
    pilot_8 = ["CTG", "DCM", "FPT", "HPG", "MWG", "NT2", "PVT", "VCI"]

    async with sf() as session:
        src = Source(id="src-u", name="HOSE", sag_source_config_id="cfg-u")
        session.add(src)
        for tk in pilot_8:
            session.add(
                Document(
                    id=f"doc-{tk}",
                    source_id="src-u",
                    project_id=tk,
                    tenant_id="aiinvest-default",
                    filename=f"{tk}.md",
                    storage_path=f"/tmp/{tk}.md",
                    is_active=True,
                )
            )
            session.add(
                DocumentVersion(
                    id=f"ver-{tk}",
                    document_id=f"doc-{tk}",
                    version_no=1,
                    file_hash=f"h-{tk}",
                    valid_from=now_dt,
                )
            )
        # Add another tenant's doc & table to verify cross-tenant isolation
        session.add(
            Document(
                id="doc-other-org",
                source_id="src-u",
                project_id="PVT",
                tenant_id="other-org",
                filename="secret.md",
                storage_path="/tmp/secret.md",
                is_active=True,
            )
        )
        session.add(
            DocumentVersion(
                id="ver-other-org",
                document_id="doc-other-org",
                version_no=1,
                file_hash="h-other",
                valid_from=now_dt,
            )
        )
        session.add(
            SagExtractedTable(
                table_id="tbl-other-org",
                project_id="PVT",
                document_version_id="ver-other-org",
                sub_doc_slot="BCTC_AUDITED",
                fiscal_year=2025,
                fiscal_quarter=0,
                table_title="Secret Org Table",
                section_path="BCTC_AUDITED",
                page_from=1,
                page_to=1,
                currency="VND",
                unit_raw="tỷ đồng",
                unit_multiplier=1e9,
                headers_json=["Chỉ tiêu", "2025"],
                rows_json=[
                    [
                        {"raw_text": "Doanh thu", "numeric_value": None},
                        {"raw_text": "10", "numeric_value": 10.0},
                    ]
                ],
                verification_status="VERIFIED",
                table_hash="th-other",
                valid_from=now_dt,
            )
        )
        # Seed PIT KgNode for PVT (shipping/tanker) & VCI (securities) — alphabetically 7th & 8th!
        session.add(
            KgNode(
                node_id="company:PVT",
                node_type="COMPANY",
                label="Tổng Công ty Cổ phần Vận tải Dầu khí đội tàu chở dầu LPG",
                ticker="PVT",
                effective_from=date(2025, 1, 1),
                valid_to=date(9999, 12, 31),
            )
        )
        session.add(
            KgNode(
                node_id="company:VCI",
                node_type="COMPANY",
                label="Công ty Cổ phần Chứng khoán Vietcap cho vay margin môi giới",
                ticker="VCI",
                effective_from=date(2025, 1, 1),
                valid_to=date(9999, 12, 31),
            )
        )
        await session.flush()

        orch = ResearchChatOrchestrator(session=session)
        p_default = _principal(tenant="aiinvest-default")
        avail, aliases = await orch._discover_available_tickers(
            principal=p_default,
            as_of_date=date(2026, 6, 1),
        )
        assert avail == set(pilot_8)
        assert any(v == "PVT" for v in aliases.values())  # KgNode import works (no silent ImportError!)

        routed = await orch._route_universe_tickers_thematically(
            query="Doanh nghiệp nào vận tải dầu khí có đội tàu chở dầu và LPG?",
            available_tickers=avail,
            principal=p_default,
            as_of_date=date(2026, 6, 1),
            limit=6,
        )
        assert len(routed) == 6
        assert routed[0] == "PVT"  # Ranked #1 via KG thematic match instead of dropped alphabetically!

        tbl_store = SQLAlchemyTableStore(session)
        assert await tbl_store.get_table_by_id("tbl-other-org", tenant_id="aiinvest-default") is None
        assert await tbl_store.get_table_by_id("tbl-other-org", tenant_id="other-org") is not None
    await engine.dispose()
