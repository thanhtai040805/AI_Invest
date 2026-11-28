"""Phase 1 TDD Suite: Deterministic Structural Signal Pointers, Boilerplate Edge Denoising,
Subsidiary/Associate Table Linker, and Cross-Slot 2-Hop Propagation (`0` LLM tokens, `0%` PIT leak).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, KnowledgeUnit, KnowledgeUnitEdge
from sag_api.research.kg_entity_linker import extract_corporate_structure_from_tables
from sag_api.services.knowledge_service import (
    KnowledgeConfig,
    build_financial_knowledge_config,
    classify_structural_signals,
    extract_e0_e1,
    graph_payload,
    is_accounting_boilerplate_unit,
)


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def test_l1_classify_structural_signals_and_extract_e0_e1() -> None:
    agm_blk = CanonicalBlock(
        id="b-agm-1",
        document_version_id="v-agm",
        ordinal=10,
        block_type="paragraph",
        normalized_text=(
            "Phần thảo luận tại Đại hội: Cổ đông hỏi về việc chuyển giao quyền kiểm soát FTEL "
            "ảnh hưởng thế nào đến cổ tức tiền mặt? Ông Nguyễn Thế Phương trả lời cổ tức từ FTEL "
            "chỉ khoảng 450 - 500 tỷ đồng/năm nên không ảnh hưởng chính sách cổ tức của FPT."
        ),
        content_hash="h1",
        page_from=29,
        page_to=29,
        section_path="AGM_TARGETS > BIÊN BẢN ĐHĐCĐ THƯỜNG NIÊN 2026 > PHẦN THẢO LUẬN",
        source_anchor="p29-b10",
    )
    _, feats = extract_e0_e1(agm_blk, build_financial_knowledge_config())
    assert "AGM_QA" in feats["structural_signals"]
    assert "CONSOLIDATION_SCOPE" in feats["structural_signals"]
    assert "e2_reason" not in feats

    scope_blk = CanonicalBlock(
        id="b-scope-1",
        document_version_id="v-aud",
        ordinal=20,
        block_type="paragraph",
        normalized_text=(
            "Ngày 18/03/2026, SCIC đã bàn giao 50,16% vốn tại CTCP Viễn thông FPT (FTEL) sang Bộ Công an "
            "theo Công văn số 1906, dẫn tới Tập đoàn chấm dứt hợp nhất FTEL và ghi nhận là công ty liên kết."
        ),
        content_hash="h2",
        page_from=176,
        page_to=176,
        section_path="BCTC_AUDITED > THUYẾT MINH > 1. CẤU TRÚC TẬP ĐOÀN",
        source_anchor="p176-b20",
    )
    sigs_scope = classify_structural_signals(
        scope_blk.normalized_text,
        scope_blk.section_path or "",
        sub_doc_slot="BCTC_AUDITED",
    )
    assert "CONSOLIDATION_SCOPE" in sigs_scope

    mdna_blk = CanonicalBlock(
        id="b-mdna-1",
        document_version_id="v-bctn",
        ordinal=30,
        block_type="paragraph",
        normalized_text=(
            "Khối Công nghệ ghi nhận doanh thu Chuyển đổi số đạt 16.751 tỷ đồng (+16,8% YoY), "
            "thị trường Nhật Bản tăng trưởng 28% nhờ nhu cầu hiện đại hóa hệ thống legacy COBOL bằng AI."
        ),
        content_hash="h3",
        page_from=42,
        page_to=42,
        section_path="BCTN_CORE > BÁO CÁO CỦA BAN TỔNG GIÁM ĐỐC > PHÂN TÍCH THEO KHỐI KINH DOANH",
        source_anchor="p42-b30",
    )
    sigs_mdna = classify_structural_signals(mdna_blk.normalized_text, mdna_blk.section_path or "")
    assert "SEGMENT_MDNA" in sigs_mdna


def test_l1_boilerplate_suppression_in_graph_payload() -> None:
    assert is_accounting_boilerplate_unit("Trong đó:", "THUYẾT MINH > 5. TIỀN VÀ TƯƠNG ĐƯƠNG TIỀN")
    assert is_accounting_boilerplate_unit(
        "Các thuyết minh đính kèm là một bộ phận hợp thành của báo cáo tài chính hợp nhất giữa niên độ này.",
        "BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT",
    )
    assert not is_accounting_boilerplate_unit(
        "Trong đó: tiền gửi có kỳ hạn tại Ngân hàng TMCP Á Châu (ACB) đạt 4.200 tỷ đồng với lãi suất 5,8%/năm.",
        "THUYẾT MINH > 5. ĐẦU TƯ TÀI CHÍNH NGẮN HẠN",
    )

    u_bp_1 = KnowledgeUnit(
        id="u-bp-1",
        tenant_id="t1",
        project_id="FPT",
        document_version_id="v1",
        security_partition_id="p1",
        source_id="s1",
        ordinal=1,
        text="Trong đó:",
        features_json={"entities": [], "citations": [], "dense": [1.0, 0.0, 0.0]},
        provenance_json={"document_id": "d1", "section_path": "TM 5", "sub_doc_slot": "QUARTER_NOTES"},
        valid_from=_utc(2026, 10, 20),
        valid_to=_utc(9999, 12, 31),
        checksum="c-bp-1",
    )
    u_bp_2 = KnowledgeUnit(
        id="u-bp-2",
        tenant_id="t1",
        project_id="FPT",
        document_version_id="v2",
        security_partition_id="p1",
        source_id="s1",
        ordinal=2,
        text="Trong đó:",
        features_json={"entities": [], "citations": [], "dense": [1.0, 0.0, 0.0]},
        provenance_json={"document_id": "d2", "section_path": "TM 8", "sub_doc_slot": "BCTC_AUDITED"},
        valid_from=_utc(2026, 3, 28),
        valid_to=_utc(9999, 12, 31),
        checksum="c-bp-2",
    )
    u_real_1 = KnowledgeUnit(
        id="u-real-1",
        tenant_id="t1",
        project_id="FPT",
        document_version_id="v1",
        security_partition_id="p1",
        source_id="s1",
        ordinal=3,
        text="Khoản đầu tư vào Công ty Cổ phần Viễn thông FPT (FTEL) được ghi nhận theo phương pháp vốn chủ sở hữu.",
        features_json={"entities": ["FTEL", "Viễn thông FPT"], "citations": [], "dense": [0.2, 0.9, 0.1]},
        provenance_json={"document_id": "d1", "section_path": "TM 6", "sub_doc_slot": "QUARTER_NOTES"},
        valid_from=_utc(2026, 10, 20),
        valid_to=_utc(9999, 12, 31),
        checksum="c-r-1",
    )
    u_real_2 = KnowledgeUnit(
        id="u-real-2",
        tenant_id="t1",
        project_id="FPT",
        document_version_id="v2",
        security_partition_id="p1",
        source_id="s1",
        ordinal=4,
        text="SCIC chuyển giao 50,16% vốn tại Công ty Cổ phần Viễn thông FPT (FTEL) sang Bộ Công an.",
        features_json={"entities": ["FTEL", "SCIC", "Bộ Công an"], "citations": [], "dense": [0.22, 0.88, 0.12]},
        provenance_json={"document_id": "d2", "section_path": "TM 1", "sub_doc_slot": "BCTC_AUDITED"},
        valid_from=_utc(2026, 3, 28),
        valid_to=_utc(9999, 12, 31),
        checksum="c-r-2",
    )

    payload = graph_payload([u_bp_1, u_bp_2, u_real_1, u_real_2], KnowledgeConfig(edge_min_score=0.45))
    edge_pairs = {(e["source"], e["target"]) for e in payload["edges"]}
    assert ("u-bp-1", "u-bp-2") not in edge_pairs
    assert ("u-real-1", "u-real-2") in edge_pairs


def test_l1_extract_corporate_structure_from_tables_pit_safe() -> None:
    stocks_df = pd.DataFrame(
        [
            {"symbol": "FPT", "name": "Công ty Cổ phần FPT", "sector": "TECHNOLOGY"},
            {"symbol": "FRT", "name": "Công ty Cổ phần Bán lẻ Kỹ thuật số FPT", "sector": "RETAIL_TRADE"},
            {"symbol": "MWG", "name": "Công ty Cổ phần Đầu tư Thế Giới Di Động", "sector": "RETAIL_TRADE"},
        ]
    )
    tables = [
        {
            "table_id": "tbl-fpt-sub-q3",
            "project_id": "FPT",
            "document_version_id": "ver-fpt-q3",
            "sub_doc_slot": "QUARTER_NOTES",
            "table_title": "A. CÔNG TY CON VÀ CÔNG TY LIÊN KẾT TRỌNG YẾU",
            "section_path": "QUARTER_NOTES > THUYẾT MINH > 1. CẤU TRÚC TẬP ĐOÀN",
            "page_from": 14,
            "headers_json": [
                "STT",
                "Công ty",
                "Hoạt động chính",
                "30/09/2026 :: Tỷ lệ sở hữu (%)",
                "30/09/2026 :: Tỷ lệ biểu quyết (%)",
            ],
            "rows_json": [
                ["1", "Công ty TNHH Phần mềm FPT (FPT Software)", "Xuất khẩu phần mềm", "100,00%", "100,00%"],
                ["2", "Công ty Cổ phần Viễn thông FPT (FTEL)", "Viễn thông & Data Center", "45,66%", "45,66%"],
                ["3", "Công ty Cổ phần Bán lẻ Kỹ thuật số FPT (FRT)", "Bán lẻ ICT & Dược phẩm", "46,54%", "46,54%"],
                [
                    {"raw_text": "4", "numeric_value": 4},
                    {"raw_text": "Công ty TNHH NAC", "numeric_value": None},
                    {"raw_text": "Dịch vụ số", "numeric_value": None},
                    {"raw_text": "100%", "numeric_value": 100.0},
                    {"raw_text": "100%", "numeric_value": 100.0},
                ],
            ],
            "valid_from": _utc(2026, 10, 22),
        },
        {
            "table_id": "tbl-mwg-future",
            "project_id": "MWG",
            "document_version_id": "ver-mwg-future",
            "sub_doc_slot": "BCTC_AUDITED",
            "table_title": "Danh sách công ty con",
            "section_path": "BCTC_AUDITED > THUYẾT MINH",
            "page_from": 12,
            "headers_json": ["Tên công ty", "Lĩnh vực", "Tỷ lệ lợi ích (%)"],
            "rows_json": [
                ["Công ty Cổ phần Thương mại Bách Hóa Xanh", "Bán lẻ thực phẩm", "94,95%"],
            ],
            "valid_from": _utc(2027, 2, 15),  # Future relative to as_of_date -> MUST BE DROPPED
        },
    ]

    nodes, edges = extract_corporate_structure_from_tables(
        tables,
        stocks_df=stocks_df,
        as_of_date=date(2026, 11, 28),
    )
    # Future MWG table must be strictly excluded by PIT gate
    assert all("Bách Hóa Xanh" not in n["label"] for n in nodes)
    assert all(e["src_node_id"] == "TICKER:FPT" for e in edges)

    rel_by_dst = {e["dst_node_id"]: e for e in edges}
    # Listed associate FRT links directly to TICKER:FRT
    assert "TICKER:FRT" in rel_by_dst
    assert rel_by_dst["TICKER:FRT"]["relation_type"] == "ASSOCIATE_OF"
    assert rel_by_dst["TICKER:FRT"]["verification_tier"] == "TABLE_VERIFIED"

    # Unlisted subsidiary FPT Software (>=50% -> OWNS_SUBSIDIARY) & unlisted associate FTEL (<50% -> ASSOCIATE_OF)
    fsoft_edges = [e for e in edges if "Phần mềm FPT" in e["evidence_json"].get("entity_name", "")]
    ftel_edges = [e for e in edges if "Viễn thông FPT" in e["evidence_json"].get("entity_name", "")]
    assert len(fsoft_edges) == 1 and fsoft_edges[0]["relation_type"] == "OWNS_SUBSIDIARY"
    assert len(ftel_edges) == 1 and ftel_edges[0]["relation_type"] == "ASSOCIATE_OF"
    # Unlisted 3-letter company ("Công ty TNHH NAC") MUST create a CORP: node, NEVER an unbacked TICKER:NAC FK!
    nac_edges = [e for e in edges if e["evidence_json"].get("entity_name") == "Công ty TNHH NAC"]
    assert len(nac_edges) == 1
    assert nac_edges[0]["dst_node_id"].startswith("CORP:FPT:")
    assert any(n["node_id"] == nac_edges[0]["dst_node_id"] for n in nodes)


@pytest.mark.asyncio
async def test_l2_hybrid_retriever_unlocks_cross_slot_2hop_chain(tmp_path) -> None:
    """Verify `SAGRetrieverAdapter.retrieve_disclosures` bridges `QUARTER_NOTES -> BCTC_AUDITED -> AGM_TARGETS`
    even when `sub_doc_slots=['QUARTER_NOTES']` is passed by Stage 1, while skipping boilerplate neighbors.
    """
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from sag_api.core.principal_assertion import VerifiedPrincipal
    from sag_api.db.base import Base
    from sag_api.db.models import SearchUnit, Source
    from sag_api.enums import SourceStatus
    from sag_api.research.chat_orchestrator import SAGRetrieverAdapter

    db_path = tmp_path / "hop2.sqlite"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = async_sessionmaker(eng, expire_on_commit=False)
    async with sf() as sess:
        src = Source(
            id="src-fpt",
            name="FPT Filings",
            sag_source_config_id="cfg-fpt",
            status=SourceStatus.ACTIVE,
        )
        sess.add(src)
        docs_and_vers = [
            ("doc-q3", "ver-q3", "QUARTER_NOTES", 2026, 3, _utc(2026, 10, 22)),
            ("doc-aud", "ver-aud", "BCTC_AUDITED", 2025, 0, _utc(2026, 3, 28)),
            ("doc-agm", "ver-agm", "AGM_TARGETS", 2026, 0, _utc(2026, 4, 15)),
        ]
        for did, vid, slot, fy, fq, pub in docs_and_vers:
            sess.add(
                Document(
                    id=did,
                    tenant_id="aiinvest-default",
                    project_id="FPT",
                    source_id="src-fpt",
                    filename=f"{did}.pdf",
                    storage_path=f"/{did}.pdf",
                    is_active=True,
                )
            )
            sess.add(
                DocumentVersion(
                    id=vid,
                    document_id=did,
                    version_no=1,
                    file_hash=f"fh-{vid}",
                    status="READY",
                    search_status="SEARCH_READY",
                    knowledge_status="DATA_READY",
                    source_published_at=pub,
                    observed_at=pub,
                    valid_from=pub,
                    valid_to=_utc(9999, 12, 31),
                    metadata_json={
                        "security_partition_id": "all",
                        "sub_doc_slot": slot,
                        "fiscal_year": fy,
                        "fiscal_quarter": fq,
                    },
                )
            )
        await sess.flush()
        blocks = [
            CanonicalBlock(
                id="blk-q3",
                document_version_id="ver-q3",
                ordinal=1,
                block_type="paragraph",
                normalized_text="Lãi từ công ty liên kết tăng mạnh nhờ ghi nhận CTCP Viễn thông FPT (FTEL).",
                content_hash="ch-q3",
                page_from=18,
                page_to=18,
                section_path="QUARTER_NOTES > TM 6. ĐẦU TƯ LIÊN KẾT",
                source_anchor="a-q3",
            ),
            CanonicalBlock(
                id="blk-bp",
                document_version_id="ver-q3",
                ordinal=2,
                block_type="paragraph",
                normalized_text="Trong đó:",
                content_hash="ch-bp",
                page_from=19,
                page_to=19,
                section_path="QUARTER_NOTES > TM 7",
                source_anchor="a-bp",
            ),
            CanonicalBlock(
                id="blk-aud",
                document_version_id="ver-aud",
                ordinal=1,
                block_type="paragraph",
                normalized_text="SCIC chuyển giao 50,16% vốn tại FTEL sang Bộ Công an theo Công văn 1906.",
                content_hash="ch-aud",
                page_from=176,
                page_to=176,
                section_path="BCTC_AUDITED > TM 1. CẤU TRÚC TẬP ĐOÀN",
                source_anchor="a-aud",
            ),
            CanonicalBlock(
                id="blk-agm",
                document_version_id="ver-agm",
                ordinal=1,
                block_type="paragraph",
                normalized_text=(
                    "Cổ đông hỏi về FTEL sau khi chuyển về Bộ Công an: Ông Nguyễn Thế Phương cho biết "
                    "cổ tức FTEL khoảng 450-500 tỷ/năm nên không ảnh hưởng cổ tức tiền mặt của FPT."
                ),
                content_hash="ch-agm",
                page_from=29,
                page_to=29,
                section_path="AGM_TARGETS > PHẦN THẢO LUẬN ĐHĐCĐ",
                source_anchor="a-agm",
            ),
        ]
        sess.add_all(blocks)
        await sess.flush()
        sess.add(
            SearchUnit(
                id="su-q3",
                document_version_id="ver-q3",
                block_from_id="blk-q3",
                block_to_id="blk-q3",
                security_partition_id="FPT",
                content_hash="ch-q3",
                token_count=85,
                page_from=18,
                page_to=18,
                section_path=blocks[0].section_path,
            )
        )
        kus = [
            KnowledgeUnit(
                id="ku-q3",
                document_version_id="ver-q3",
                tenant_id="aiinvest-default",
                project_id="FPT",
                source_id="src-fpt",
                security_partition_id="all",
                ordinal=1,
                text=blocks[0].normalized_text,
                content_hash="ch-q3",
                input_checksum="ic1",
                checksum="c1",
                is_current=True,
                provenance_json={
                    "document_id": "doc-q3",
                    "evidence_block_ids": ["blk-q3"],
                    "page_from": 18,
                    "page_to": 18,
                    "section_path": blocks[0].section_path,
                    "sub_doc_slot": "QUARTER_NOTES",
                },
                features_json={"entities": ["FTEL"], "structural_signals": ["CONSOLIDATION_SCOPE"]},
                valid_from=_utc(2026, 10, 22),
                valid_to=_utc(9999, 12, 31),
            ),
            KnowledgeUnit(
                id="ku-bp",
                document_version_id="ver-q3",
                tenant_id="aiinvest-default",
                project_id="FPT",
                source_id="src-fpt",
                security_partition_id="all",
                ordinal=2,
                text="Trong đó:",
                content_hash="ch-bp",
                input_checksum="ic-bp",
                checksum="c-bp",
                is_current=True,
                provenance_json={
                    "document_id": "doc-q3",
                    "evidence_block_ids": ["blk-bp"],
                    "page_from": 19,
                    "page_to": 19,
                    "section_path": blocks[1].section_path,
                    "sub_doc_slot": "QUARTER_NOTES",
                },
                features_json={"entities": []},
                valid_from=_utc(2026, 10, 22),
                valid_to=_utc(9999, 12, 31),
            ),
            KnowledgeUnit(
                id="ku-aud",
                document_version_id="ver-aud",
                tenant_id="aiinvest-default",
                project_id="FPT",
                source_id="src-fpt",
                security_partition_id="all",
                ordinal=1,
                text=blocks[2].normalized_text,
                content_hash="ch-aud",
                input_checksum="ic2",
                checksum="c2",
                is_current=True,
                provenance_json={
                    "document_id": "doc-aud",
                    "evidence_block_ids": ["blk-aud"],
                    "page_from": 176,
                    "page_to": 176,
                    "section_path": blocks[2].section_path,
                    "sub_doc_slot": "BCTC_AUDITED",
                },
                features_json={
                    "entities": ["FTEL", "SCIC", "Bộ Công an"],
                    "structural_signals": ["CONSOLIDATION_SCOPE"],
                },
                valid_from=_utc(2026, 3, 28),
                valid_to=_utc(9999, 12, 31),
            ),
            KnowledgeUnit(
                id="ku-agm",
                document_version_id="ver-agm",
                tenant_id="aiinvest-default",
                project_id="FPT",
                source_id="src-fpt",
                security_partition_id="all",
                ordinal=1,
                text=blocks[3].normalized_text,
                content_hash="ch-agm",
                input_checksum="ic3",
                checksum="c3",
                is_current=True,
                provenance_json={
                    "document_id": "doc-agm",
                    "evidence_block_ids": ["blk-agm"],
                    "page_from": 29,
                    "page_to": 29,
                    "section_path": blocks[3].section_path,
                    "sub_doc_slot": "AGM_TARGETS",
                },
                features_json={
                    "entities": ["FTEL", "Bộ Công an"],
                    "structural_signals": ["AGM_QA", "CONSOLIDATION_SCOPE"],
                },
                valid_from=_utc(2026, 4, 15),
                valid_to=_utc(9999, 12, 31),
            ),
        ]
        sess.add_all(kus)
        from sag_api.db.models import KnowledgeGraphBuild

        sess.add(
            KnowledgeGraphBuild(
                id="graph-test1",
                tenant_id="aiinvest-default",
                project_id="FPT",
                checksum="chk",
                manifest_json={},
                input_fingerprint="fp",
            )
        )
        sess.add_all(
            [
                KnowledgeUnitEdge(
                    build_id="graph-test1",
                    source_unit_id="ku-q3",
                    target_unit_id="ku-bp",
                    security_partition_id="all",
                    weight=0.99,
                    signals_json={},
                ),
                KnowledgeUnitEdge(
                    build_id="graph-test1",
                    source_unit_id="ku-q3",
                    target_unit_id="ku-aud",
                    security_partition_id="all",
                    weight=0.78,
                    signals_json={},
                ),
                KnowledgeUnitEdge(
                    build_id="graph-test1",
                    source_unit_id="ku-aud",
                    target_unit_id="ku-agm",
                    security_partition_id="all",
                    weight=0.76,
                    signals_json={},
                ),
            ]
        )
        await sess.commit()

    def _qdrant_handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content.decode("utf-8"))
        searches = body.get("searches") or []
        point = {
            "id": "pt-q3",
            "score": 0.88,
            "payload": {
                "search_unit_id": "su-q3",
                "project_id": "FPT",
                "tenant_id": "aiinvest-default",
                "security_partition_id": "all",
                "document_version_id": "ver-q3",
                "sub_doc_slot": "QUARTER_NOTES",
                "section_path": "QUARTER_NOTES > TM 6. ĐẦU TƯ LIÊN KẾT",
                "page_from": 18,
                "page_to": 18,
                "block_from_id": "blk-q3",
                "block_to_id": "blk-q3",
                "fiscal_year": 2026,
                "fiscal_quarter": 3,
                "content": "Lãi từ công ty liên kết tăng mạnh nhờ ghi nhận CTCP Viễn thông FPT (FTEL).",
            },
        }
        return httpx.Response(200, json={"result": [{"points": [point]} for _ in searches]})

    class _FakeEmbed:
        async def generate(self, _text: str) -> list[float]:
            return [0.2, 0.4, 0.6]

    class _FakeEM:
        def __init__(self, qc: httpx.AsyncClient) -> None:
            self._qc = qc

        async def get_search_unit_qdrant_client(self) -> httpx.AsyncClient:
            return self._qc

        async def get_sag_embedding(self, _cid, _src) -> _FakeEmbed:
            return _FakeEmbed()

    principal = VerifiedPrincipal(
        subject="u1",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-1",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"all", "*"}),
        conversation_scope="all",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(_qdrant_handler), base_url="http://qdrant.local") as qc:
        async with sf() as sess:
            retriever = SAGRetrieverAdapter(sess, _FakeEM(qc))
            res, meta = await retriever.retrieve_disclosures(
                dense_queries=["lãi công ty liên kết FTEL Q3/2026"],
                vas_terms=["công ty liên kết"],
                tickers=["FPT"],
                sub_doc_slots=["QUARTER_NOTES"],  # Restricted slot on seed query!
                fiscal_years=[],
                fiscal_quarters=[],
                as_of_date=date(2026, 11, 28),
                top_k=6,
                principal=principal,
            )

    assert meta["status"] == "OK"
    slots_returned = [s.sub_doc_slot for s in res.sections]
    assert "QUARTER_NOTES" in slots_returned
    assert "BCTC_AUDITED" in slots_returned
    assert "AGM_TARGETS" in slots_returned
    assert all(s.content.strip() != "Trong đó:" for s in res.sections)
    assert res.hipporag_hops == 2

    await eng.dispose()
