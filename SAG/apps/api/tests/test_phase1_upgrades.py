"""Tests for Phase 1 pre-Stage-3 upgrades:
1. DB/Port isolation (`aiinvest_sag`, ports 55432 / 16333 / 18000) & template validation.
2. Qdrant shared multi-tenant collection (`is_tenant=true`, `on_disk=true`, `int8`, tenant-scoped delete/reconcile).
3. 3-Layer Document Localization (PDF Bookmarks, LLM TOC Router, Printed-to-PDF Offset & A3 Spread Calibration,
   Stateful Spans, Heading-Aware Boilerplate Shield).
4. Tier-Aware Batch Ingest Pipeline (`pilot_8 | a5_36 | a4_81 | a3_buffer_139 | custom`) & `/ingest-batch` API.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fitz
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core import config as config_mod
from sag_api.core import db as db_mod
from sag_api.core.config import Settings
from sag_api.db.base import Base
from sag_api.main import create_app
from sag_api.research.batch_ingest import (
    PILOT_8_TICKERS,
    run_research_batch_ingest,
)
from sag_api.research.input_builder import (
    TocSectionSpan,
    build_research_sub_documents,
    calibrate_printed_to_pdf_mapping,
    classify_page_boilerplate,
    classify_text_to_slot,
    detect_repeating_running_lines,
    normalize_financial_markdown_headings,
    parse_llm_toc_json,
    strip_running_lines,
)
from sag_api.research.input_selector import (
    FinancialAnomalyContext,
    ResearchCandidateDocument,
)
from sag_api.research.ocr_adapter import map_mineru_markdown_to_original_pages
from sag_api.research.table_engine import (
    InMemoryTableRepository,
    extract_tables_from_document,
)
from sag_api.services.search_index_service import (
    delete_document_points_from_qdrant,
    ensure_qdrant_collection_and_indexes,
    reconcile_orphan_search_units,
    resolve_search_unit_collection_name,
)


def _make_pdf_with_footers(
    pages: list[tuple[str, str | None, str | None]],
    *,
    width: float = 595.0,
    height: float = 842.0,
    bookmarks: list[list[Any]] | None = None,
) -> bytes:
    """Create a realistic digital multi-page PDF with optional left/right footers and bookmarks."""
    filler = (
        "\nDoanh nghiệp niêm yết trên sàn HOSE công bố báo cáo tài chính hợp nhất đã kiểm toán "
        "và báo cáo thường niên đầy đủ số liệu hoạt động kinh doanh theo đúng quy định hiện hành."
    )
    doc = fitz.open()
    for body_text, left_footer, right_footer in pages:
        page = doc.new_page(width=width, height=height)
        if body_text:
            full_text = body_text if len(body_text) >= 140 else f"{body_text}{filler}"
            page.insert_htmlbox(fitz.Rect(48, 48, width - 48, height - 90), f"<pre>{full_text}</pre>")
        if left_footer is not None:
            page.insert_text((width * 0.22, height - 30), left_footer, fontsize=10)
        if right_footer is not None:
            page.insert_text((width * 0.78, height - 30), right_footer, fontsize=10)
    if bookmarks:
        doc.set_toc(bookmarks)
    raw = doc.tobytes()
    doc.close()
    return raw


# ── 1. DB / Port Isolation & Template Checks ─────────────────────────────────


def test_env_templates_isolate_aiinvest_sag_db_and_ports() -> None:
    api_root = Path(__file__).resolve().parents[1]
    sag_root = Path(__file__).resolve().parents[3]
    repo_root = Path(__file__).resolve().parents[4]

    api_tpl = (api_root / ".env.example").read_text(encoding="utf-8")
    sag_tpl = (sag_root / ".env.example").read_text(encoding="utf-8")
    root_tpl = (repo_root / ".env.example").read_text(encoding="utf-8")

    assert "aiinvest_sag" in api_tpl
    assert "localhost:55432/aiinvest_sag" in api_tpl
    assert "localhost:16333" in api_tpl
    assert "SAG_SAG_QDRANT_SHARED_COLLECTION=aiinvest_search_units" in api_tpl

    assert "POSTGRES_DB=aiinvest_sag" in sag_tpl
    assert "QDRANT_PORT=16333" in sag_tpl
    assert "API_PORT=18000" in sag_tpl
    assert "SAG_SAG_QDRANT_SHARED_COLLECTION=aiinvest_search_units" in sag_tpl

    assert "SAG_DB_NAME=aiinvest_sag" in root_tpl
    assert "SAG_QDRANT_PORT=16333" in root_tpl
    assert "SAG_SAG_QDRANT_SHARED_COLLECTION=aiinvest_search_units" in root_tpl

    loaded = Settings(_env_file=api_root / ".env.example")
    assert loaded.sag_pg_database == "aiinvest_sag"
    assert loaded.sag_pg_port == 55432
    assert loaded.sag_qdrant_url == "http://localhost:16333"
    assert loaded.sag_qdrant_shared_collection == "aiinvest_search_units"
    assert loaded.sag_qdrant_on_disk is True
    assert loaded.sag_qdrant_scalar_quantization is True


# ── 2. Qdrant Shared Multi-Tenant Collection (`is_tenant=true`, `on_disk=true`) ─


async def test_qdrant_shared_collection_provisions_is_tenant_on_disk_and_scopes_deletes(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config_mod.settings, "sag_qdrant_shared_collection", "aiinvest_search_units")
    monkeypatch.setattr(config_mod.settings, "sag_qdrant_on_disk", True)
    monkeypatch.setattr(config_mod.settings, "sag_qdrant_scalar_quantization", True)

    assert resolve_search_unit_collection_name("PVT") == "aiinvest_search_units"
    assert resolve_search_unit_collection_name("FPT") == "aiinvest_search_units"

    seen_requests: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        seen_requests.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path == "/collections/aiinvest_search_units":
            return httpx.Response(404, json={"status": "error"})
        if request.method == "POST" and request.url.path.endswith("/points/scroll"):
            return httpx.Response(200, json={"result": {"points": [], "next_page_offset": None}})
        return httpx.Response(200, json={"status": "ok", "result": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://qdrant.test") as client:
        await ensure_qdrant_collection_and_indexes(client, "aiinvest_search_units", vector_dim=1024)

        create_reqs = [b for m, p, b in seen_requests if m == "PUT" and p == "/collections/aiinvest_search_units"]
        assert len(create_reqs) == 1
        create_body = create_reqs[0]
        assert create_body["vectors"]["content_vector"]["on_disk"] is True
        assert create_body["on_disk_payload"] is True
        assert create_body["hnsw_config"] == {"m": 0, "payload_m": 16, "on_disk": True}
        assert create_body["quantization_config"]["scalar"]["type"] == "int8"

        idx_reqs = {
            b["field_name"]: b["field_schema"]
            for m, p, b in seen_requests
            if m == "PUT" and p == "/collections/aiinvest_search_units/index"
        }
        assert idx_reqs["project_id"] == {"type": "keyword", "is_tenant": True}
        assert idx_reqs["valid_from_ts"] == "integer"

        # Verify tenant-scoped delete & orphan reconcile in shared collection
        await delete_document_points_from_qdrant(client, project_id="PVT", document_version_ids=["ver-1"])
        del_bodies = [b for m, p, b in seen_requests if m == "POST" and p.endswith("/points/delete")]
        assert {"key": "project_id", "match": {"value": "PVT"}} in del_bodies[-1]["filter"]["must"]

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await reconcile_orphan_search_units(session, client, project_id="PVT")
        await engine.dispose()

        scroll_bodies = [b for m, p, b in seen_requests if m == "POST" and p.endswith("/points/scroll")]
        assert scroll_bodies[-1]["filter"]["must"] == [{"key": "project_id", "match": {"value": "PVT"}}]


def test_resolve_collection_defaults_to_per_project_when_shared_unset(monkeypatch) -> None:
    monkeypatch.setattr(config_mod.settings, "sag_qdrant_shared_collection", None)
    assert resolve_search_unit_collection_name("CTG") == "search_units_CTG"


# ── 3. 3-Layer Document Localization (Bookmarks, LLM TOC, Offset & Shield) ───


def test_printed_to_pdf_offset_calibration_single_page_and_spread_a3() -> None:
    # Single-page PDF with 3 unnumbered cover pages -> pdf_page = printed + 3
    single_pages = [
        ("Cover page", None, None),
        ("Inside cover", None, None),
        ("Table of contents", None, None),
    ] + [(f"Body text content for printed page {i}", None, str(i)) for i in range(1, 13)]
    raw_single = _make_pdf_with_footers(single_pages, width=595.0, height=842.0)
    with fitz.open(stream=raw_single, filetype="pdf") as doc:
        cal_single = calibrate_printed_to_pdf_mapping(doc)
    assert cal_single.mode == "SINGLE_PAGE"
    assert cal_single.offset == pytest.approx(3.0, abs=0.5)
    assert cal_single.printed_to_pdf(10, max_pages=15) == 13

    # 2-page A3 spread PDF (like CTG/PVT): PDF page k holds printed pages (2k-1, 2k)
    spread_pages = [
        (f"Left column {2 * k - 1} | Right column {2 * k}", str(2 * k - 1), str(2 * k))
        for k in range(1, 13)
    ]
    raw_spread = _make_pdf_with_footers(spread_pages, width=1191.0, height=842.0)
    with fitz.open(stream=raw_spread, filetype="pdf") as doc:
        cal_spread = calibrate_printed_to_pdf_mapping(doc)
    assert cal_spread.mode == "SPREAD_A3"
    # Printed pages 15 and 16 live on PDF page 8
    assert cal_spread.printed_to_pdf(16, max_pages=12) == 8


def test_heading_aware_boilerplate_shield_and_sticky_slot_continuation() -> None:
    # Body mention of ESG inside an MD&A page must NOT drop the page
    mda_with_esg_mention = (
        "ĐÁNH GIÁ KẾT QUẢ KINH DOANH NĂM 2025\n"
        "Doanh thu mảng công nghệ tăng trưởng 22% so với cùng kỳ.\n"
        "Bên cạnh đó, doanh nghiệp cũng tuân thủ các tiêu chuẩn ESG trong vận hành.\n"
    )
    assert classify_page_boilerplate(mda_with_esg_mention, doc_type="BCTN") is None

    # Heading is PR letter, but page contains dense audited numbers -> shielded!
    pr_with_table = (
        "THƯ CHỦ TỊCH HỘI ĐỒNG QUẢN TRỊ\n"
        "Bảng cân đối kế toán tóm tắt: 16.012.813 15.200.400 12.111.000 9.876.543 8.765.432 7.654.321\n"
    )
    assert classify_page_boilerplate(pr_with_table, doc_type="BCTN") is None

    # CAPEX heading mentioning "công ty con" in body stays in BCTN_CORE
    capex_page = (
        "KẾ HOẠCH KINH DOANH VÀ NGÂN SÁCH CAPEX NĂM 2026\n"
        "Tổng ngân sách đầu tư mở rộng đội tàu đạt 3.500 tỷ đồng.\n"
        "Một phần vốn sẽ phân bổ qua các công ty con vận tải biển.\n"
    )
    assert classify_text_to_slot(capex_page, doc_type="BCTN") == "BCTN_CORE"

    # Headingless continuation page inside BCTC_AUDITED stays in BCTC_AUDITED (0%-drop)
    continuation_page = "Mảng thương mại xăng dầu đạt 5.156 tỷ đồng với biên lợi nhuận gộp 0.43%."
    assert (
        classify_text_to_slot(continuation_page, doc_type="BCTN", previous_slot="BCTC_AUDITED")
        == "BCTC_AUDITED"
    )


async def test_3layer_localization_llm_toc_router_with_offset_and_headingless_continuation() -> None:
    class _MockLlmTocRouter:
        async def route_toc(
            self,
            *,
            ticker: str,
            doc_type: str,
            toc_or_outline_text: str,
            total_pdf_pages: int,
        ) -> list[TocSectionSpan]:
            assert ticker == "PVT"
            return parse_llm_toc_json(
                json.dumps(
                    {
                        "sections": [
                            {"slot": "BCTN_PR", "title": "Thư Chủ tịch", "printed_start": 1, "printed_end": 2},
                            {
                                "slot": "BCTN_CORE",
                                "title": "Hành trình vươn biển lớn",
                                "printed_start": 3,
                                "printed_end": 4,
                            },
                            {
                                "slot": "BCTC_AUDITED",
                                "title": "Báo cáo tài chính kiểm toán & Thuyết minh",
                                "printed_start": 5,
                                "printed_end": 8,
                            },
                        ]
                    }
                )
            )

    # 3 cover/TOC pages + 13 numbered pages (printed 1..13 -> PDF 4..16, offset=+3) -> total 16p (>= 15p)
    toc_page_text = (
        "MỤC LỤC\n"
        "Thư Chủ tịch ....... 1\n"
        "Hành trình vươn biển lớn ....... 3\n"
        "Thuyết minh 28 ....... 5\n"
        "Kiểm toán ....... 8"
    )
    pages: list[tuple[str, str | None, str | None]] = [
        ("BÁO CÁO THƯỜNG NIÊN PVT 2025 BÌA TRƯỚC", None, None),
        (toc_page_text, None, None),
        ("THÔNG TIN CHUNG DOANH NGHIỆP NIÊM YẾT TRÊN SÀN HOSE", None, None),
        # Printed 1-2 (PDF 4-5): BCTN_PR letter separated from core
        ("Thư Chủ tịch gửi quý cổ đông thân mến cảm ơn đồng hành suốt năm qua.", None, "1"),
        ("Thông điệp Ban lãnh đạo về tầm nhìn phát triển bền vững dài hạn.", None, "2"),
        # Printed 3-4 (PDF 6-7): Creative Vietnamese heading -> BCTN_CORE via 1-call router
        ("HÀNH TRÌNH VƯƠN BIỂN LỚN\nĐội tàu quốc tế mở rộng sang tuyến châu Âu và châu Mỹ.", None, "3"),
        ("Tiếp theo phần mở rộng thị trường vận tải lỏng quốc tế năm 2025.", None, "4"),
        # Printed 5-8 (PDF 8-11): BCTC_AUDITED preserving ALL notes + continuation pages (0%-drop)
        ("THUYẾT MINH 28 - CHI TIẾT DOANH THU THUẦN\nDoanh thu vận tải 10.856 tỷ.", None, "5"),
        ("Bảng nối tiếp (không lặp tiêu đề): Kinh doanh thương mại 5.156 tỷ, biên gộp 0.43%.", None, "6"),
        ("Bảng nối tiếp trang 3: Dịch vụ kho nổi FSO biên gộp 37.76%.", None, "7"),
        ("BÁO CÁO KIỂM TOÁN ĐỘC LẬP DELOITTE\nÝ kiến chấp nhận toàn phần.", None, "8"),
    ] + [(f"Phụ lục thông tin tham khảo bổ sung trang {i} của báo cáo.", None, str(i)) for i in range(9, 14)]

    pdf_bytes = _make_pdf_with_footers(pages)
    res = await build_research_sub_documents(
        ticker="PVT",
        pdf_bytes=pdf_bytes,
        doc_type="BCTN",
        published_date=date(2026, 4, 13),
        toc_router=_MockLlmTocRouter(),
    )

    assert res.localization_method == "LLM_TOC_ROUTER"
    assert res.page_calibration.mode == "SINGLE_PAGE"
    assert res.page_calibration.offset == pytest.approx(3.0, abs=0.5)

    slots = {b.sub_doc_slot: b for b in res.sub_documents}
    assert "BCTN_CORE" in slots
    assert 6 in slots["BCTN_CORE"].page_numbers
    assert 7 in slots["BCTN_CORE"].page_numbers
    assert "BCTC_AUDITED" in slots
    assert [8, 9, 10, 11] == slots["BCTC_AUDITED"].page_numbers
    assert res.coverage_audit.bctc_notes_coverage_pct == 100.0
    assert "BCTN_PR" in slots
    assert [4, 5] == slots["BCTN_PR"].page_numbers
    assert 4 in res.excluded_pages and 5 in res.excluded_pages
    assert "BCTC_AUDITED" not in res.missing_core_slots


async def test_3layer_localization_pdf_bookmarks_layer1a() -> None:
    pages = [
        (f"Nội dung trang {i} của báo cáo thường niên năm tài chính 2025.", None, str(i))
        for i in range(1, 17)
    ]
    bookmarks = [
        [1, "Thư Chủ tịch", 1],
        [1, "Đánh giá kết quả kinh doanh theo mảng", 3],
        [1, "Báo cáo ESG và Phát triển bền vững", 7],
        [1, "Báo cáo tài chính hợp nhất đã kiểm toán", 10],
    ]
    pdf_bytes = _make_pdf_with_footers(pages, bookmarks=bookmarks)
    res = await build_research_sub_documents(
        ticker="FPT",
        pdf_bytes=pdf_bytes,
        doc_type="BCTN",
        published_date=date(2026, 3, 30),
    )
    assert res.localization_method == "PDF_BOOKMARKS"
    slots = {b.sub_doc_slot: b for b in res.sub_documents}
    assert slots["BCTN_PR"].page_numbers == [1, 2]
    assert slots["BCTN_CORE"].page_numbers == [3, 4, 5, 6]
    assert all(p in res.excluded_pages for p in (7, 8, 9))
    assert slots["BCTC_AUDITED"].page_numbers == list(range(10, 17))
    assert res.coverage_audit.bctc_notes_coverage_pct == 100.0


# ── 4. Tier-Aware Batch Ingest Pipeline & `/ingest-batch` Endpoint ───────────


async def test_tier_aware_batch_ingest_a3_buffer_139_and_dry_run(tmp_path) -> None:
    pdf_bytes = _make_pdf_with_footers(
        [
            ("ĐÁNH GIÁ KẾT QUẢ KINH DOANH\nDoanh thu mảng lõi tăng 20%.", None, str(i))
            if i < 12
            else ("BÁO CÁO KIỂM TOÁN ĐỘC LẬP\nÝ kiến chấp nhận toàn phần.", None, str(i))
            for i in range(1, 17)
        ]
    )
    pvt_pdf = tmp_path / "pvt_bctn_2025.pdf"
    pvt_pdf.write_bytes(pdf_bytes)

    class _FakeCoreReader:
        async def resolve_tickers_for_tier(self, tier, *, as_of_date, adtv_threshold_vnd=5e9):
            assert tier == "a3_buffer_139"
            return ["PVT", "VDS"]

        async def fetch_candidates_for_ticker(self, ticker, *, as_of_date, years_back=3):
            if ticker == "PVT":
                return (
                    [
                        ResearchCandidateDocument(
                            doc_id="kd:1001",
                            ticker="PVT",
                            doc_type="annual_report",
                            title="Báo cáo thường niên năm 2025",
                            urls=[str(pvt_pdf)],
                            published_date=date(2026, 4, 13),
                        )
                    ],
                    FinancialAnomalyContext(),
                )
            return ([], FinancialAnomalyContext())

    uploaded: list[str] = []
    kg_called: list[list[str]] = []

    async def _uploader(bundle):
        uploaded.append(f"{bundle.project_id}:{bundle.sub_doc_slot}")
        return {"ok": True}

    async def _kg_refresher(tickers, cutoff):
        kg_called.append(list(tickers))
        return {"refreshed": len(tickers)}

    # Dry run does not upload or refresh KG
    dry = await run_research_batch_ingest(
        tier="a3_buffer_139",
        as_of_date=date(2026, 11, 20),
        dry_run=True,
        source_paths={"PVT": str(pvt_pdf)},
        core_reader=_FakeCoreReader(),
        subdoc_uploader=_uploader,
        kg_refresher=_kg_refresher,
    )
    assert dry.resolved_tickers == ["PVT", "VDS"]
    assert dry.total_sub_docs_uploaded == 0
    assert uploaded == []
    assert kg_called == []
    pvt_rep = next(r for r in dry.reports if r.ticker == "PVT")
    assert pvt_rep.status == "DRY_RUN_READY"
    assert "BCTN_CORE" in pvt_rep.sub_doc_slots

    # Live mode uploads sub-docs and refreshes 3-layer KG
    live = await run_research_batch_ingest(
        tier="a3_buffer_139",
        as_of_date=date(2026, 11, 20),
        dry_run=False,
        refresh_kg=True,
        source_paths={"PVT": str(pvt_pdf)},
        core_reader=_FakeCoreReader(),
        subdoc_uploader=_uploader,
        kg_refresher=_kg_refresher,
    )
    assert live.total_sub_docs_uploaded >= 1
    assert any(u.startswith("PVT:BCTN_CORE") for u in uploaded)
    assert kg_called == [["PVT"]]
    assert live.kg_refresh == {"refreshed": 1}


async def test_ingest_batch_api_endpoint_custom_and_fail_closed(tmp_path, monkeypatch) -> None:
    pdf_bytes = _make_pdf_with_footers(
        [("ĐÁNH GIÁ KẾT QUẢ KINH DOANH\nBiên gộp 21%.", None, str(i)) for i in range(1, 17)]
    )
    fpt_pdf = tmp_path / "fpt_bctn_2025.pdf"
    fpt_pdf.write_bytes(pdf_bytes)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            yield session

    bff_token = "aiinvest-sag-bff-upgrade-test-token-32bytes"
    monkeypatch.setattr(config_mod.settings, "auth_mode", "continuum")
    monkeypatch.setattr(config_mod.settings, "bff_service_token", bff_token)
    monkeypatch.delenv("SAG_CORE_DATABASE_URL", raising=False)

    app = create_app()
    app.dependency_overrides[db_mod.get_session] = _override_session
    headers = {
        "X-SAG-BFF-Service-Token": bff_token,
        "X-Continuum-User-Id": "65f1a2b3c4d5e6f7a8b9c0d1",
        "X-Continuum-Organization-Id": "aiinvest",
        "X-Continuum-Project-Id": "FPT",
        "X-Continuum-Security-Partition": "FPT",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://sag.test"
        ) as client:
            # Custom tier works cleanly in dry_run with local override
            resp = await client.post(
                "/api/v1/research-chat/ingest-batch",
                headers=headers,
                json={
                    "tier": "custom",
                    "tickers": ["FPT"],
                    "dry_run": True,
                    "source_paths": {"FPT": str(fpt_pdf)},
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["tier"] == "custom"
            assert body["resolved_tickers"] == ["FPT"]
            assert body["reports"][0]["status"] == "DRY_RUN_READY"

            # Fail closed with 503 when Core DB tier requested without SAG_CORE_DATABASE_URL
            fail_resp = await client.post(
                "/api/v1/research-chat/ingest-batch",
                headers=headers,
                json={"tier": "a3_buffer_139", "dry_run": True},
            )
            assert fail_resp.status_code == 503

            assert len(PILOT_8_TICKERS) == 8
    finally:
        await engine.dispose()


# ── 5. 0%-Drop BCTC Notes, Running Header Strip, MinerU Mapping & Table Wiring ─


async def test_bctc_zero_drop_running_headers_headings_and_high_ocr_cap() -> None:
    # 1. Running header stripper catches repeating B09-DN/HN headers automatically while keeping unique note headings
    raw_pages = {
        p: (
            "THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT (Tiếp theo)\n"
            "MẪU SỐ B 09-DN/HN\n"
            f"Trang {p}\n"
            + (
                "DỰ PHÒNG PHẢI THU NGẮN HẠN KHÓ ĐÒI\n"
                "GIAO DỊCH VỚI CÁC BÊN LIÊN QUAN\n"
                "BỘ PHẬN THEO LĨNH VỰC KINH DOANH\n"
                if p == 3
                else f"THUYẾT MINH SỐ {p + 10}. KHOẢN MỤC CHI TIẾT KỲ {p}\n"
            )
            + f"Doanh thu bán hàng trang {p}: 16.012.813.858.007 VND\n"
            + "Chi tiết đối chiếu số liệu theo quy định kế toán hiện hành.\n"
            + "Bảng phân tích biến động tăng giảm trong kỳ kế toán.\n"
        )
        for p in range(1, 9)
    }
    running = detect_repeating_running_lines(raw_pages)
    cleaned, removed = strip_running_lines(raw_pages[3], running)
    assert removed >= 2
    assert "MẪU SỐ B 09-DN/HN" not in cleaned
    norm_md, headings = normalize_financial_markdown_headings(cleaned, is_bctc=True)
    assert "## DỰ PHÒNG PHẢI THU NGẮN HẠN KHÓ ĐÒI" in norm_md
    assert "## GIAO DỊCH VỚI CÁC BÊN LIÊN QUAN" in norm_md
    assert "## BỘ PHẬN THEO LĨNH VỰC KINH DOANH" in norm_md
    assert len(headings) >= 3

    # 2. Scanned 40-page BCTC MUST NOT be truncated at 25 pages (0%-drop OCR cap = 120)
    doc = fitz.open()
    for _ in range(40):
        doc.new_page(width=595.0, height=842.0)
    scan_bctc = doc.tobytes()
    doc.close()

    seen_ocr_pages: list[int] = []

    class _FullBctcOcr:
        async def ocr_pdf_pages(self, pdf_bytes: bytes, page_numbers: list[int], *, language: str = "vi"):
            seen_ocr_pages.extend(page_numbers)
            return {
                p: (
                    "BÁO CÁO TÀI CHÍNH HỢP NHẤT\n"
                    f"Thuyết minh {p}. Giao dịch với các bên liên quan và hàng tồn kho trang {p}."
                )
                for p in page_numbers
            }

    built = await build_research_sub_documents(
        ticker="FPT",
        pdf_bytes=scan_bctc,
        doc_type="BCTC",
        published_date=date(2026, 3, 30),
        ocr_adapter=_FullBctcOcr(),
    )
    assert len(seen_ocr_pages) == 40
    assert built.coverage_audit.bctc_total_pages == 40
    assert built.coverage_audit.bctc_kept_pages == 40
    assert built.coverage_audit.bctc_notes_coverage_pct == 100.0
    assert len(built.sub_documents) == 1
    assert built.sub_documents[0].sub_doc_slot == "BCTC_AUDITED"
    assert len(built.sub_documents[0].page_numbers) == 40


async def test_mineru_page_mapping_and_html_table_wiring_in_batch_ingest(tmp_path) -> None:
    mapped = map_mineru_markdown_to_original_pages(
        "<!-- PAGE 1 -->\n## 25. Doanh thu\n<table><tr><th>Chỉ tiêu</th><th>2025</th></tr>"
        "<tr><td>Vận tải</td><td>10.000</td></tr><tr><td>Thương mại</td><td>5.000</td></tr>"
        "<tr><td>Tổng cộng</td><td>15.000</td></tr></table>\n"
        "<!-- PAGE 2 -->\n## 26. Giá vốn\nChi tiết giá vốn hàng bán.",
        [41, 42],
    )
    assert set(mapped.keys()) == {41, 42}
    assert "10.000" in mapped[41]
    assert "Giá vốn" in mapped[42]

    # Test end-to-end HTML table extraction & persistence through run_research_batch_ingest
    doc = fitz.open()
    for _ in range(16):
        doc.new_page(width=595.0, height=842.0)
    scan_pdf = doc.tobytes()
    doc.close()
    scan_path = tmp_path / "fpt_bctc_scan.pdf"
    scan_path.write_bytes(scan_pdf)

    html_table = (
        "## 25. Chi tiết doanh thu thuần\nĐơn vị: tỷ đồng\n"
        "<table>"
        "<tr><th>Chỉ tiêu</th><th>2025</th><th>2024</th></tr>"
        "<tr><td>Công nghệ</td><td>40.000</td><td>35.000</td></tr>"
        "<tr><td>Viễn thông</td><td>20.000</td><td>18.000</td></tr>"
        "<tr><td>Tổng cộng</td><td>60.000</td><td>53.000</td></tr>"
        "</table>"
    )

    class _HtmlOcr:
        async def ocr_pdf_pages(self, pdf_bytes: bytes, page_numbers: list[int], *, language: str = "vi"):
            return {
                p: (
                    html_table
                    if p == 10
                    else f"Thuyết minh báo cáo tài chính hợp nhất B09-DN trang {p} chi tiết số liệu."
                )
                for p in page_numbers
            }

    class _Reader:
        async def resolve_tickers_for_tier(self, tier, *, as_of_date, adtv_threshold_vnd=5e9):
            return ["FPT"]

        async def fetch_candidates_for_ticker(self, ticker, *, as_of_date, years_back=3):
            return (
                [
                    ResearchCandidateDocument(
                        doc_id="bctc:fpt2025",
                        ticker="FPT",
                        doc_type="BCTC",
                        title="BCTC hợp nhất kiểm toán năm 2025",
                        urls=[str(scan_path)],
                        published_date=date(2026, 3, 30),
                    )
                ],
                FinancialAnomalyContext(),
            )

    repo = InMemoryTableRepository()
    summary = await run_research_batch_ingest(
        tier="custom",
        custom_tickers=["FPT"],
        as_of_date=date(2026, 11, 20),
        dry_run=False,
        extract_tables=True,
        source_paths={"FPT": str(scan_path)},
        core_reader=_Reader(),
        ocr_adapter=_HtmlOcr(),
        table_repo=repo,
    )
    rep = summary.reports[0]
    assert rep.status == "INGESTED"
    assert rep.bctc_notes_coverage_pct == 100.0
    assert rep.tables_extracted >= 1
    assert rep.tables_verified >= 1
    stored = await repo.list_hydrated_tables(
        "FPT", as_of_ts=datetime(2026, 11, 20, tzinfo=UTC)
    )
    assert len(stored) >= 1
    assert stored[0]["verification_status"] == "VERIFIED"
    assert stored[0]["unit_raw"] == "tỷ đồng"
    assert callable(extract_tables_from_document)
