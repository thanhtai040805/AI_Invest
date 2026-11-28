"""TDD Suite for V3 Epistemic Foundation, Bipartite Upgrade Graph & Value-Gradient PPR."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_api.db.base import Base
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    Source,
    SourceProjectMapping,
)
from sag_api.enums import SourceStatus
from sag_api.research.epistemic_foundation import (
    build_document_discourse_contexts,
    compute_epistemic_profile,
    propagate_epistemic_value_ppr,
    select_company_story_spine,
)
from sag_api.services.knowledge_service import (
    build_financial_knowledge_config,
    graph_payload,
    persist_graph,
    rebuild_knowledge_version,
    verify_graph,
)


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def test_l1_discourse_state_machine_handles_ocr_typos_bilingual_questions_and_noise() -> None:
    """Verify:
    1) OCR-glitched reply headers (`Trà lòi:`, `Ông Truong Gia Bình cho biết:`) inherit `Toshikazu Nambu`
       and `Nhật Bản` from Question 1 under one shared `discourse_id`.
    2) Bilingual English Question (`Question 11: ... Masterise ...`) passes `Masterise` onto the
       Vietnamese `Trả lời:` block while marking the duplicate English block itself as `Tier 0` noise.
    3) Ballot tallying (`99,9245%`), hypothetical ESOP (`ông Nguyễn Văn A`), and PR awards score `Tier 0`.
    """
    blocks = [
        SimpleNamespace(
            id="b1",
            ordinal=1,
            block_type="paragraph",
            section_path="AGM > IV. THẢO LUẬN",
            normalized_text=(
                "Câu hỏi số 1: Tôi đánh giá rất cao việc đề cử ông Toshikazu Nambu vào HĐQT FPT. "
                "Ban lãnh đạo kỳ vọng gì vào việc ký kết những hợp đồng lớn tại Nhật Bản?"
            ),
        ),
        SimpleNamespace(
            id="b2",
            ordinal=2,
            block_type="paragraph",
            section_path="AGM > Trà lòi:",
            normalized_text=(
                "Ông Phạm Minh Tuân cho biết: Khách Nhật hợp tác đầu tiên phải đủ tin cậy. "
                "FPT có 1 thành viên HĐQT là đương kim PCT của Tập đoàn lớn nhất của Nhật là sự bảo chứng "
                "giúp ký hợp đồng Chuyển đổi AI trọn gói."
            ),
        ),
        SimpleNamespace(
            id="b3",
            ordinal=3,
            block_type="paragraph",
            section_path="AGM > Trà lòi:",
            normalized_text=(
                "Question 11: In the context of credit growth limits, while demand for capital remains strong "
                "for large-scale projects developed by Masterise, how does the Bank manage concentration risk "
                "and arrange funding for shareholders and corporate clients?"
            ),
        ),
        SimpleNamespace(
            id="b4",
            ordinal=4,
            block_type="paragraph",
            section_path="AGM > Trả lời:",
            normalized_text=(
                "Thứ nhất, nguồn vốn và tỷ lệ đòn bẩy tài chính gần như thấp nhất thị trường, "
                "Techcombank đẩy mạnh vai trò thu xếp vốn trái phiếu và thẩm định chặt chẽ."
            ),
        ),
        SimpleNamespace(
            id="b5",
            ordinal=5,
            block_type="paragraph",
            section_path="AGM > Kiểm phiếu",
            normalized_text=(
                "Tổng số tờ phiếu tán thành: 122 đại diện cho 121.872.717 phiếu biểu quyết, "
                "chiếm 99,9245% tính trên tổng số phiếu biểu quyết của cổ đông dự họp."
            ),
        ),
        SimpleNamespace(
            id="b6",
            ordinal=6,
            block_type="paragraph",
            section_path="AGM > PHỤ LỤC ESOP > Ví dụ:",
            normalized_text="Số lượng cổ phiếu được phân phối của ông Nguyễn Văn A trên là 110.131 cổ phiếu.",
        ),
    ]

    ctxs = build_document_discourse_contexts(blocks, version_id="ver-agm", sub_doc_slot="AGM_TARGETS")

    # b1 and b2 share discourse_id and b2 inherits "Toshikazu Nambu"
    assert ctxs["b1"].discourse_id == ctxs["b2"].discourse_id
    assert ctxs["b2"].discourse_role == "EXECUTIVE_ANSWER"
    assert any("Toshikazu Nambu" in e for e in ctxs["b2"].inherited_entities)
    assert "F1_GROWTH_BACKLOG_MARKET" in ctxs["b2"].inherited_facets

    p_b2 = compute_epistemic_profile(
        blocks[1].normalized_text,
        blocks[1].section_path,
        sub_doc_slot="AGM_TARGETS",
        discourse_ctx=ctxs["b2"],
    )
    assert p_b2["tier"] == 3 and p_b2["insight_score"] >= 0.88

    # b3 (English question) is Tier 0 noise, YET b4 (Vietnamese reply) inherits "Masterise"!
    assert ctxs["b3"].discourse_id == ctxs["b4"].discourse_id
    assert any("Masterise" in e for e in ctxs["b4"].inherited_entities)
    p_b3 = compute_epistemic_profile(
        blocks[2].normalized_text, blocks[2].section_path, sub_doc_slot="AGM_TARGETS", discourse_ctx=ctxs["b3"]
    )
    p_b4 = compute_epistemic_profile(
        blocks[3].normalized_text, blocks[3].section_path, sub_doc_slot="AGM_TARGETS", discourse_ctx=ctxs["b4"]
    )
    assert p_b3["tier"] == 0 and p_b3["insight_score"] == 0.0
    assert p_b4["tier"] == 3 and p_b4["insight_score"] >= 0.88

    # Ballot tallying & hypothetical ESOP Nguyễn Văn A -> strictly Tier 0!
    p_b5 = compute_epistemic_profile(blocks[4].normalized_text, blocks[4].section_path, sub_doc_slot="AGM_TARGETS")
    p_b6 = compute_epistemic_profile(blocks[5].normalized_text, blocks[5].section_path, sub_doc_slot="AGM_TARGETS")
    assert p_b5["tier"] == 0 and p_b5["insight_score"] == 0.0
    assert p_b6["tier"] == 0 and p_b6["insight_score"] == 0.0
    p_acb_ballot = compute_epistemic_profile(
        "Tổng số phiếu không tham gia biểu quyết: 123.807.778 phiếu, chiếm 3,82% trên tổng số phiếu biểu quyết.",
        "ACB AGM > BIÊN BẢN KIỂM PHIẾU",
        sub_doc_slot="AGM_TARGETS",
    )
    assert p_acb_ballot["tier"] == 0 and p_acb_ballot["insight_score"] == 0.0

    # Generic Note 2/3 VAS accounting-policy textbook text -> Tier 1 (NOT Tier 2/3 Story Spine!),
    # while real Note 1 corporate ownership transfer (SCIC -> Bộ Công an, CV 1906) stays Tier 3!
    p_note2_vas = compute_epistemic_profile(
        (
            "Những giao dịch làm thay đổi tỷ lệ sở hữu của Tập đoàn trong công ty con mà không dẫn đến "
            "mất quyền kiểm soát được kế toán tương tự như các giao dịch vốn chủ sở hữu."
        ),
        "Thuyết minh BCTC hợp nhất > 2. Cơ sở lập báo cáo tài chính > Cơ sở hợp nhất",
        sub_doc_slot="BCTC_AUDITED",
        structural_signals=("CONSOLIDATION_SCOPE",),
    )
    assert p_note2_vas["tier"] == 1 and p_note2_vas["insight_score"] <= 0.30

    p_note1_real = compute_epistemic_profile(
        (
            "Ngày 16/07/2025, SCIC đã chuyển giao quyền đại diện chủ sở hữu phần vốn nhà nước (50,16%) "
            "tại Công ty Cổ phần Viễn thông FPT về Bộ Công an theo Công văn số 1906/TTg-ĐMDN."
        ),
        "Thuyết minh BCTC quý > 1. Thông tin khái quát > Cấu trúc Tập đoàn",
        sub_doc_slot="QUARTER_NOTES",
        structural_signals=("CONSOLIDATION_SCOPE",),
    )
    assert p_note1_real["tier"] == 3 and p_note1_real["insight_score"] >= 0.80


@pytest.mark.asyncio
async def test_l2_bipartite_upgrade_graph_checksum_integrity_and_zero_hint_value_ppr(tmp_path) -> None:
    """End-to-end verification of the core scientific breakthrough:
    1) `rebuild_knowledge_version` + `persist_graph` + `verify_graph` passes 100% SHA-256 manifest checksums.
    2) From a pure `QUARTER_NOTES` segment revenue node (`CNTT nước ngoài đạt 28.538 tỷ, +11,6%`),
       `graph_payload` builds Bipartite Epistemic-Upgrade edges directly to `Toshikazu Nambu` (`AGM_TARGETS`)
       and `xMainframe COBOL` (`BCTN_CORE`), while suppressing duplicate accounting definitions and ballot noise!
    3) `propagate_epistemic_value_ppr` autonomously discovers `Toshikazu Nambu` and `xMainframe` WITHOUT
       the seed node ever mentioning `"Nambu"`, `"Nhật Bản"`, or `"COBOL"`.
    """
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'epistemic_v3.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    import hashlib

    def _blk(vid: str, ord_no: int, sec: str, txt: str) -> CanonicalBlock:
        return CanonicalBlock(
            id=f"{vid}-b{ord_no}",
            document_version_id=vid,
            ordinal=ord_no,
            block_type="paragraph",
            normalized_text=txt,
            content_hash=hashlib.sha256(txt.encode()).hexdigest(),
            page_from=ord_no,
            page_to=ord_no,
            section_path=sec,
        )

    async with sf() as s:
        s.add(Source(id="src-v3", name="HOSE", sag_source_config_id="cfg-v3", status=SourceStatus.ACTIVE))
        s.add(
            SourceProjectMapping(
                id="spm-v3",
                source_id="src-v3",
                organization_id="t1",
                project_id="FPT",
                state="CONFIRMED",
                mapping_version=1,
                confirmed_at=_utc(2026, 1, 1),
                confirmed_by="auditor",
                approval_ref="ref-v3",
            )
        )
        s.add_all(
            [
                Document(
                    id="doc-q3",
                    source_id="src-v3",
                    project_id="FPT",
                    tenant_id="t1",
                    filename="q3.pdf",
                    storage_path="/q3.pdf",
                    is_active=True,
                ),
                Document(
                    id="doc-agm",
                    source_id="src-v3",
                    project_id="FPT",
                    tenant_id="t1",
                    filename="agm.pdf",
                    storage_path="/agm.pdf",
                    is_active=True,
                ),
                Document(
                    id="doc-bctn",
                    source_id="src-v3",
                    project_id="FPT",
                    tenant_id="t1",
                    filename="bctn.pdf",
                    storage_path="/bctn.pdf",
                    is_active=True,
                ),
            ]
        )
        s.add_all(
            [
                DocumentVersion(
                    id="ver-q3",
                    document_id="doc-q3",
                    version_no=1,
                    file_hash="fh1",
                    status="READY",
                    search_status="SEARCH_READY",
                    knowledge_status="PENDING",
                    source_published_at=_utc(2026, 10, 24),
                    observed_at=_utc(2026, 10, 24),
                    valid_from=_utc(2026, 10, 24),
                    valid_to=_utc(9999, 12, 31),
                    metadata_json={
                        "security_partition_id": "all",
                        "sub_doc_slot": "QUARTER_NOTES",
                        "fiscal_year": 2026,
                        "fiscal_quarter": 3,
                    },
                ),
                DocumentVersion(
                    id="ver-agm",
                    document_id="doc-agm",
                    version_no=1,
                    file_hash="fh2",
                    status="READY",
                    search_status="SEARCH_READY",
                    knowledge_status="PENDING",
                    source_published_at=_utc(2026, 4, 21),
                    observed_at=_utc(2026, 4, 21),
                    valid_from=_utc(2026, 4, 21),
                    valid_to=_utc(9999, 12, 31),
                    metadata_json={
                        "security_partition_id": "all",
                        "sub_doc_slot": "AGM_TARGETS",
                        "fiscal_year": 2026,
                        "fiscal_quarter": 0,
                    },
                ),
                DocumentVersion(
                    id="ver-bctn",
                    document_id="doc-bctn",
                    version_no=1,
                    file_hash="fh3",
                    status="READY",
                    search_status="SEARCH_READY",
                    knowledge_status="PENDING",
                    source_published_at=_utc(2026, 3, 28),
                    observed_at=_utc(2026, 3, 28),
                    valid_from=_utc(2026, 3, 28),
                    valid_to=_utc(9999, 12, 31),
                    metadata_json={
                        "security_partition_id": "all",
                        "sub_doc_slot": "BCTN_CORE",
                        "fiscal_year": 2025,
                        "fiscal_quarter": 0,
                    },
                ),
            ]
        )
        await s.flush()

        blocks_all = [
            # Q3: Accounting symptom (NO mention of Nambu, Japan, or COBOL!) + Echo clone definition
            _blk(
                "ver-q3",
                1,
                "QUARTER_NOTES > 4. BÁO CÁO BỘ PHẬN",
                "Doanh thu mảng Sản phẩm và dịch vụ CNTT nước ngoài 9 tháng 2026 đạt 28.538 tỷ đồng, tăng 11,59%.",
            ),
            _blk(
                "ver-q3",
                2,
                "QUARTER_NOTES > 4. BÁO CÁO BỘ PHẬN",
                "Sản phẩm và Dịch vụ CNTT nước ngoài: bao gồm hoạt động sản xuất phần mềm và cung cấp dịch vụ CNTT.",
            ),
            _blk(
                "ver-q3",
                3,
                "QUARTER_NOTES > 4. BÁO CÁO BỘ PHẬN",
                "Doanh thu mảng CNTT trong nước đạt 6.976,7 tỷ đồng, biên lợi nhuận bộ phận cải thiện mạnh +333 bps.",
            ),
            # AGM: Ballot noise + Nambu Q&A + Public-sector labor pricing Q&A
            _blk(
                "ver-agm",
                1,
                "AGM_TARGETS > KIỂM PHIẾU",
                "Tổng số tờ phiếu tán thành: 729 đại diện cho 1.020.302.272 cổ phần, chiếm 99,99% số phiếu biểu quyết.",
            ),
            _blk(
                "ver-agm",
                2,
                "AGM_TARGETS > IV. THẢO LUẬN",
                "Câu hỏi số 1: Đề cử ông Toshikazu Nambu vào HĐQT FPT mang lại kỳ vọng gì cho các "
                "hợp đồng lớn tại Nhật Bản?",
            ),
            _blk(
                "ver-agm",
                3,
                "AGM_TARGETS > Trà lòi:",
                "Ông Phạm Minh Tuân cho biết: Thành viên HĐQT là đương kim PCT Tập đoàn lớn nhất của Nhật "
                "là sự bảo chứng giúp FPT ký các hợp đồng lớn Chuyển đổi AI trọn gói; sáng 16/4 đón "
                "30 doanh nghiệp Nhật Bản.",
            ),
            _blk(
                "ver-agm",
                4,
                "AGM_TARGETS > IV. THẢO LUẬN",
                "Câu hỏi số 6: Đánh giá biên lợi nhuận các dự án chuyển đổi số khu vực công theo Nghị quyết 57?",
            ),
            _blk(
                "ver-agm",
                5,
                "AGM_TARGETS > Trà lòi:",
                "Ông Nguyễn Thế Phương cho biết: Từ năm ngoái Chính phủ đã thay đổi cách tính đơn giá nhân "
                "công thay vì tính theo lương cơ bản, nhờ đó biên lợi nhuận các dự án trong nước sắp tới "
                "tốt hơn rất nhiều.",
            ),
            # BCTN: xMainframe COBOL moat + 1.5B USD bookings
            _blk(
                "ver-bctn",
                1,
                "BCTN_CORE > THÔNG ĐIỆP TỔNG GIÁM ĐỐC",
                "Doanh thu ký mới khối Công nghệ vượt mốc 1,5 tỷ USD (40.636 tỷ đồng, tăng 23,2%), "
                "số hợp đồng lớn trên 10 triệu USD tăng gấp đôi so với năm trước.",
            ),
            _blk(
                "ver-bctn",
                2,
                "BCTN_CORE > CÔNG NGHỆ CHIẾN LƯỢC",
                "Giải pháp AI xMainframe hỗ trợ phân tích và chuyển đổi hàng tỷ dòng mã COBOL di sản "
                "sang mô hình dịch vụ quản trị trọn gói Managed Services giúp nâng biên lợi nhuận.",
            ),
        ]
        s.add_all(blocks_all)
        await s.commit()

    cfg = build_financial_knowledge_config()
    # Provide realistic gentle embeddings (moderate similarity across business units, high on clones)
    dense_map = {
        "ver-q3-b1": [0.85, 0.25, 0.10, 0.15],
        "ver-q3-b2": [0.84, 0.24, 0.11, 0.14],
        "ver-q3-b3": [0.80, 0.10, 0.45, 0.15],
        "ver-agm-b1": [0.10, 0.10, 0.10, 0.90],
        "ver-agm-b2": [0.55, 0.65, 0.15, 0.20],
        "ver-agm-b3": [0.58, 0.68, 0.18, 0.15],
        "ver-agm-b4": [0.52, 0.15, 0.68, 0.15],
        "ver-agm-b5": [0.56, 0.18, 0.72, 0.12],
        "ver-bctn-b1": [0.68, 0.55, 0.20, 0.15],
        "ver-bctn-b2": [0.60, 0.48, 0.58, 0.12],
    }

    async with sf() as s:
        u_q3 = await rebuild_knowledge_version(s, "ver-q3", cfg, dense_by_block_id=dense_map)
        u_agm = await rebuild_knowledge_version(s, "ver-agm", cfg, dense_by_block_id=dense_map)
        u_bctn = await rebuild_knowledge_version(s, "ver-bctn", cfg, dense_by_block_id=dense_map)
        all_units = [*u_q3, *u_agm, *u_bctn]
        build = await persist_graph(s, all_units, cfg)
        await s.commit()
        verified_build = await verify_graph(s, build.id)
        assert verified_build.id == build.id

    payload = graph_payload(all_units, cfg)
    units_by_id = {u.id: u for u in all_units}
    adj: dict[str, list[tuple[str, float]]] = {}
    for e in payload["edges"]:
        adj.setdefault(e["source"], []).append((e["target"], float(e["weight"])))
        adj.setdefault(e["target"], []).append((e["source"], float(e["weight"])))

    # Ballot unit MUST have 0 edges in the graph!
    ballot_uid = next(u.id for u in all_units if "729 đại diện" in u.text)
    assert ballot_uid not in adj

    # Seed ONLY on Q3 accounting segment nodes (`CNTT nước ngoài` + `CNTT trong nước`) — ZERO Nambu/COBOL hints!
    seed_it_global = next(u.id for u in all_units if "28.538 tỷ" in u.text)
    seed_it_dom = next(u.id for u in all_units if "6.976,7 tỷ" in u.text)

    ranked = propagate_epistemic_value_ppr(
        seed_unit_ids=[seed_it_global, seed_it_dom],
        units_by_id=units_by_id,
        adjacency=adj,
        max_hops=3,
        top_n=4,
    )
    discovered_texts = "\n".join(units_by_id[uid].text for _, _, uid in ranked)

    # Autonomous discovery of ALL THREE hidden causal stories!
    assert "30 doanh nghiệp Nhật Bản" in discovered_texts or "Toshikazu Nambu" in discovered_texts
    assert "xMainframe" in discovered_texts or "1,5 tỷ USD" in discovered_texts
    assert "đơn giá nhân công" in discovered_texts

    # And `select_company_story_spine` extracts clean orthogonal Tier-3 peaks with zero ballot/boilerplate noise
    spine = select_company_story_spine(all_units, max_peaks=5)
    spine_texts = "\n".join(u.text for u in spine)
    assert "729 đại diện" not in spine_texts
    assert "bao gồm hoạt động sản xuất phần mềm" not in spine_texts
    assert "30 doanh nghiệp Nhật Bản" in spine_texts
    assert "đơn giá nhân công" in spine_texts

    await eng.dispose()


def test_l1_quote_verifier_table_card_percent_header_without_vnd_laundering() -> None:
    """Verify Stage 7 recognizes percentage columns/labels (`Tăng/giảm (%): 110,5`, `| Biên (%) | 21,6 |`)
    while still rejecting bare VND figures (`110,5 tỷ`) laundered as `110,5%`.
    """
    from sag_api.research.quote_verifier import verify_numeric_claims_against_evidence

    sec_pct_tbl = {
        "ticker": "ACB",
        "heading": "Giải trình KQKD Q3/2026",
        "content": (
            "| Chỉ tiêu | Q3/2026 | Q3/2025 | Tăng/giảm (%) |\n"
            "|---|---|---|---|\n"
            "| Trả lãi phát hành giấy tờ có giá | 2.410 | 1.145 | 110,5 |\n"
            "Biên LNTT điều chỉnh (%): 21,6"
        ),
    }
    ok_res = verify_numeric_claims_against_evidence(
        "Trả lãi giấy tờ có giá của ACB tăng +110,5% và biên LNTT điều chỉnh đạt 21,6% [C1].",
        sections_by_cid={"C1": sec_pct_tbl},
    )
    assert ok_res.unsupported_claims == []

    sec_vnd_only = {
        "ticker": "ACB",
        "heading": "Thuyết minh doanh thu",
        "content": "Doanh thu hoạt động khác đạt 110,5 tỷ đồng trong quý.",
    }
    bad_res = verify_numeric_claims_against_evidence(
        "Tăng trưởng lợi nhuận của ACB đạt +110,5% [C1].",
        sections_by_cid={"C1": sec_vnd_only},
    )
    assert len(bad_res.unsupported_claims) == 1

