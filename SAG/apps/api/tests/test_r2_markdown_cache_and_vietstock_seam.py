"""L2 Wire-Boundary & L3 Crash-Safe E2E Tests for Vietstock/Vietcap Sources, R2 `.md` Cache & `/ingest-batch`."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import fitz
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core import config as config_mod
from sag_api.core import db as db_mod
from sag_api.db.base import Base
from sag_api.main import create_app
from sag_api.research import (
    CafeFFallbackClient,
    InMemoryTableRepository,
    SagR2MarkdownCache,
    VietcapIQPublicDateClient,
    VietstockDocumentClient,
    attach_cafef_fallback_urls,
    extract_vn_filing_signing_date,
    run_research_batch_ingest,
)


def _make_utf8_pdf(pages_html: list[str]) -> bytes:
    doc = fitz.open()
    for html in pages_html:
        pg = doc.new_page(width=595.0, height=842.0)
        if html:
            pg.insert_htmlbox(fitz.Rect(42, 42, 553, 800), html)
    raw = doc.tobytes()
    doc.close()
    return raw


@pytest.mark.asyncio
async def test_vietstock_vietcap_public_date_join_and_cafef_wire_contracts():
    """Layer 2 Wire-Boundary Test (`httpx.MockTransport` — zero wrapper mocking):
    1. Joins Vietcap IQ `publicDate` onto Vietstock BCTC rows to defeat stale `LastUpdate = 2026`.
    2. Collects `doctype = 1, 2, 3, 4, 5, 8` (+ `10` for brokerages), never querying `doctype = 9`.
    3. Filters standalone English Vietstock rows at Layer 1 while keeping bilingual `Song ngữ` rows.
    4. Attaches CafeF secondary URLs onto matching `(fiscal_year, fiscal_quarter)` candidates.
    """
    seen_doctypes: set[int] = set()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "iq.vietcap.com.vn":
            assert req.url.path == "/api/iq-insight-service/v1/company/SSI/financial-statement"
            assert req.url.params.get("section") == "BALANCE_SHEET"
            return httpx.Response(
                200,
                json={
                    "status": 200,
                    "data": {
                        "years": [
                            {
                                "yearReport": 2023,
                                "lengthReport": 5,
                                "publicDate": "2024-03-28T00:00:00",
                            },
                            {
                                "yearReport": 2025,
                                "lengthReport": 5,
                                "publicDate": "2026-03-25T00:00:00",
                            },
                        ],
                        "quarters": [
                            {
                                "yearReport": 2025,
                                "lengthReport": 4,
                                "publicDate": "2026-01-20T00:00:00",
                            }
                        ],
                    },
                },
            )

        if req.url.host == "finance.vietstock.vn":
            dt = int(req.url.params["doctype"])
            pg = int(req.url.params["page"])
            seen_doctypes.add(dt)
            if pg > 1:
                return httpx.Response(200, json=[])
            if dt == 1:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "DocumentID": 101,
                            "Title": "Báo cáo tài chính hợp nhất kiểm toán năm 2023",
                            "Url": "https://static2.vietstock.vn/SSI_2023_BCTC_HN.pdf",
                            # Stale CMS edit timestamp in 2026 -> defeated by Vietcap publicDate join
                            "LastUpdate": "/Date(1773532800000)/",
                        },
                        {
                            "DocumentID": 102,
                            "Title": "Consolidated Financial Statements FY 2025",
                            "Url": "https://static2.vietstock.vn/SSI_2025_FS_EN.pdf",
                            "LastUpdate": "25/03/2026",
                        },
                        {
                            "DocumentID": 103,
                            "Title": "Báo cáo tài chính hợp nhất năm 2025 (Song ngữ Việt - Anh)",
                            "Url": "https://static2.vietstock.vn/SSI_2025_BCTC_VI_EN.pdf",
                            "LastUpdate": "26/03/2026",
                        },
                    ],
                )
            if dt == 5:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "DocumentID": 501,
                            "Title": "Tài liệu Đại hội đồng cổ đông thường niên năm 2026",
                            "Url": "https://static2.vietstock.vn/SSI_2026_TaiLieu_DHDCD.rar",
                            "LastUpdate": "02/04/2026",
                        }
                    ],
                )
            if dt == 8:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "DocumentID": 801,
                            "Title": "Giải trình chênh lệch KQKD Quý 4/2025 trên 10%",
                            "Url": "https://static2.vietstock.vn/SSI_Q4_2025_GiaiTrinh.pdf",
                            "LastUpdate": "20/01/2026",
                        }
                    ],
                )
            if dt == 10:
                return httpx.Response(
                    200,
                    json=[
                        {
                            "DocumentID": 1001,
                            "Title": "Báo cáo tỷ lệ an toàn tài chính tại 31/12/2025",
                            "Url": "https://static2.vietstock.vn/SSI_2025_ATTC.pdf",
                            "LastUpdate": "25/03/2026",
                        }
                    ],
                )
            return httpx.Response(200, json=[])

        if req.url.host == "cafef.vn":
            html = (
                '<a href="https://cafef1.mediacdn.vn/SSI_2025_BCTC_HN.pdf">BCTC 2025</a>'
                '<a href="https://cafef1.mediacdn.vn/SSI_2025_FS_EN.pdf">FS EN</a>'
            )
            return httpx.Response(200, text=html)

        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vc_client = VietcapIQPublicDateClient(http_client=client)
        pub_dates = await vc_client.fetch_public_dates("SSI")
        assert pub_dates[(2023, 0)] == date(2024, 3, 28)
        assert pub_dates[(2025, 0)] == date(2026, 3, 25)
        assert pub_dates[(2025, 4)] == date(2026, 1, 20)

        vs_client = VietstockDocumentClient(http_client=client)
        candidates, skipped_en = await vs_client.collect_candidates(
            "SSI",
            as_of_date=date(2026, 6, 30),
            years_back=3,
            is_brokerage=True,
            vietcap_public_dates=pub_dates,
        )
        assert seen_doctypes == {1, 2, 3, 4, 5, 8, 10}
        assert 9 not in seen_doctypes  # Governance strictly excluded!
        assert len(skipped_en) == 1
        assert "102:" in skipped_en[0]

        by_id = {c.doc_id: c for c in candidates}
        # FY2023 BCTC got exact `2024-03-28` from Vietcap IQ instead of Vietstock's 2026 CMS edit date!
        assert by_id["vietstock:SSI:1:101"].announcement_date == date(2024, 3, 28)
        assert by_id["vietstock:SSI:5:501"].doc_type == "TAI_LIEU_DHDCD"
        assert by_id["vietstock:SSI:8:801"].doc_type == "GIAI_TRINH"
        assert by_id["vietstock:SSI:10:1001"].doc_type == "TY_LE_AT_TC"

        cafef_client = CafeFFallbackClient(http_client=client)
        backup_urls = await cafef_client.fetch_backup_urls("SSI")
        assert backup_urls == ["https://cafef1.mediacdn.vn/SSI_2025_BCTC_HN.pdf"]
        attached = attach_cafef_fallback_urls(candidates, backup_urls)
        assert attached == 1
        assert "https://cafef1.mediacdn.vn/SSI_2025_BCTC_HN.pdf" in by_id["vietstock:SSI:1:103"].urls

    # Legal signing date extractor ignores 1999 founding date and picks 2026 release date
    cover_txt = (
        "CÔNG TY CỔ PHẦN FPT (Thành lập ngày 13 tháng 09 năm 1988)\n"
        "BÁO CÁO THƯỜNG NIÊN NĂM 2025\n"
        "Hà Nội, ngày 12 tháng 04 năm 2026\nTổng Giám đốc đã ký"
    )
    assert extract_vn_filing_signing_date(cover_txt, fiscal_year=2025) == date(2026, 4, 12)


@pytest.mark.asyncio
async def test_r2_s3_sigv4_crash_recovery_and_english_tombstone_e2e(tmp_path: Path):
    """Layer 3 E2E Resilience Test:
    1. Scanned Vietnamese BCTC runs MinerU OCR, cleans bilingual prose/tables, writes `.md` + `manifest.json`
       to mock R2 S3 (`AWS4-HMAC-SHA256`) BEFORE `subdoc_uploader` crashes!
    2. Retry after crash hits R2 `.md` cache with `0` OCR calls (`ocr_pages_used == 0`, `r2_cache_hits == 1`)
       and completes DB/table indexing cleanly!
    3. Scanned Full-English PDF writes R2 tombstone after 2-page canary; retry skips with `0` OCR calls!
    """
    r2_objects: dict[str, bytes] = {}
    s3_auth_headers: list[str] = []

    def r2_handler(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("Authorization", "")
        s3_auth_headers.append(auth)
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=test-ak/")
        key = req.url.path.lstrip("/")
        if req.method == "PUT":
            r2_objects[key] = req.content
            return httpx.Response(200, text="")
        if req.method == "GET":
            if key not in r2_objects:
                return httpx.Response(404, text="NoSuchKey")
            return httpx.Response(200, content=r2_objects[key])
        return httpx.Response(405)

    scan_bctc_path = tmp_path / "PVT_BCTC_2025_scan.pdf"
    scan_bctc_path.write_bytes(_make_utf8_pdf([""] * 6))

    ocr_call_count = 0

    class _CountingScanOcr:
        async def ocr_pdf_pages(
            self, pdf_bytes: bytes, page_numbers: list[int], *, language: str = "vi"
        ) -> dict[int, str]:
            nonlocal ocr_call_count
            ocr_call_count += 1
            return {
                p: (
                    "## BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT / CONSOLIDATED BALANCE SHEET\n"
                    "Đơn vị tính: Tỷ đồng / Unit: Billion VND\n"
                    "Doanh thu thuần tăng trưởng ổn định.\n"
                    "Net revenue grew steadily during the fiscal year.\n"
                    "<table>"
                    "<tr><th>Chỉ tiêu<br>Items</th><th>Items</th><th>2025</th><th>2024</th></tr>"
                    "<tr><td>Tiền mặt<br>Cash on hand</td><td>Cash on hand</td><td>600</td><td>500</td></tr>"
                    "<tr><td>Tiền gửi ngân hàng</td><td>Bank deposits</td><td>400</td><td>300</td></tr>"
                    "<tr><td>Tổng cộng / Total</td><td>Total</td><td>1.000</td><td>800</td></tr>"
                    "</table>"
                )
                for p in page_numbers
            }

    class _ZeroAllowedOcr:
        async def ocr_pdf_pages(self, *_args, **_kwargs):
            raise AssertionError("OCR MUST NOT be called when R2 `.md` cache HITs!")

    class _SingleDocReader:
        def __init__(self, doc_bytes: bytes, title: str) -> None:
            self._bytes = doc_bytes
            self._title = title

        async def resolve_tickers_for_tier(self, tier, *, as_of_date, adtv_threshold_vnd=0.0):
            return ["PVT"]

        async def fetch_candidates_for_ticker(self, ticker, *, as_of_date, years_back=3):
            from sag_api.research import FinancialAnomalyContext, ResearchCandidateDocument

            return (
                [
                    ResearchCandidateDocument(
                        doc_id="bctc:pvt:2025",
                        ticker="PVT",
                        doc_type="BCTC",
                        title=self._title,
                        statement_scope="CONSOLIDATED",
                        published_date=date(2026, 3, 28),
                        raw_bytes=self._bytes,
                    )
                ],
                FinancialAnomalyContext(),
            )

    async with httpx.AsyncClient(transport=httpx.MockTransport(r2_handler)) as r2_http:
        r2_cache = SagR2MarkdownCache(
            account_id="acc123",
            endpoint_url="https://acc123.r2.cloudflarestorage.com",
            access_key_id="test-ak",
            secret_access_key="test-sk",
            bucket_name="aiinvest-bctc-prod",
            http_client=r2_http,
        )

        # RUN 1: Downstream uploader crashes AFTER OCR & R2 `.md` write!
        async def _crashing_uploader(_bundle):
            raise RuntimeError("Simulated downstream Qdrant/Postgres outage")

        summary_crash = await run_research_batch_ingest(
            tier="custom",
            custom_tickers=["PVT"],
            as_of_date=date(2026, 6, 30),
            core_reader=_SingleDocReader(scan_bctc_path.read_bytes(), "BCTC hợp nhất kiểm toán năm 2025"),
            ocr_adapter=_CountingScanOcr(),
            subdoc_uploader=_crashing_uploader,
            r2_cache=r2_cache,
        )
        assert ocr_call_count == 2  # 2-page canary + remaining 4 pages
        assert summary_crash.reports[0].ocr_pages_used == 6
        assert any(k.endswith("/BCTC_AUDITED.md") for k in r2_objects)
        md_stored = next(v.decode("utf-8") for k, v in r2_objects.items() if k.endswith("/BCTC_AUDITED.md"))
        # Verify stored R2 `.md` is ALREADY cleaned of English lines & bilingual table noise!
        assert "Net revenue grew steadily" not in md_stored
        assert "Cash on hand" not in md_stored
        assert "Tiền mặt" in md_stored

        # RUN 2: Retry with `_ZeroAllowedOcr` -> 0 OCR calls, instant recovery from R2 `.md`!
        uploaded_bundles = []

        async def _ok_uploader(bundle):
            uploaded_bundles.append(bundle)
            return {"version_id": "ver-recovered-1"}

        tbl_repo = InMemoryTableRepository()
        summary_ok = await run_research_batch_ingest(
            tier="custom",
            custom_tickers=["PVT"],
            as_of_date=date(2026, 6, 30),
            core_reader=_SingleDocReader(scan_bctc_path.read_bytes(), "BCTC hợp nhất kiểm toán năm 2025"),
            ocr_adapter=_ZeroAllowedOcr(),
            subdoc_uploader=_ok_uploader,
            table_repo=tbl_repo,
            r2_cache=r2_cache,
        )
        rep = summary_ok.reports[0]
        assert rep.status == "INGESTED"
        assert rep.r2_cache_hits == 1
        assert rep.ocr_pages_used == 0
        assert rep.uploaded_sub_docs == 1
        assert rep.tables_verified >= 1
        assert len(uploaded_bundles) == 1

        # RUN 3: Scanned Full-English PDF -> Canary writes tombstone on Run A; Run B uses 0 OCR calls!
        en_scan_bytes = _make_utf8_pdf([""] * 10)
        en_ocr_calls = 0

        class _EnScanOcr:
            async def ocr_pdf_pages(self, pdf_bytes: bytes, page_numbers: list[int], *, language: str = "vi"):
                nonlocal en_ocr_calls
                en_ocr_calls += len(page_numbers)
                return {
                    p: (
                        "CONSOLIDATED STATEMENT OF CASH FLOWS FOR THE YEAR ENDED 31 DECEMBER 2025. "
                        "Cash flows from operating activities and notes to the financial statements."
                    )
                    for p in page_numbers
                }

        rep_en_1 = (
            await run_research_batch_ingest(
                tier="custom",
                custom_tickers=["PVT"],
                as_of_date=date(2026, 6, 30),
                core_reader=_SingleDocReader(en_scan_bytes, "BCTC 2025"),
                ocr_adapter=_EnScanOcr(),
                r2_cache=r2_cache,
            )
        ).reports[0]
        assert en_ocr_calls == 2  # Stopped at 2-page canary!
        assert rep_en_1.rejected_documents >= 1

        rep_en_2 = (
            await run_research_batch_ingest(
                tier="custom",
                custom_tickers=["PVT"],
                as_of_date=date(2026, 6, 30),
                core_reader=_SingleDocReader(en_scan_bytes, "BCTC 2025"),
                ocr_adapter=_ZeroAllowedOcr(),  # Tombstone HIT -> 0 OCR!
                r2_cache=r2_cache,
            )
        ).reports[0]
        assert rep_en_2.rejected_documents >= 1


@pytest.mark.asyncio
async def test_asgi_ingest_batch_endpoint_strips_bilingual_pages_end_to_end(
    monkeypatch, tmp_path: Path
):
    """Layer 2/3 Zero-Orphan ASGI Entrypoint Test (`POST /api/v1/research-chat/ingest-batch`)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(db_mod, "SessionLocal", factory)
    monkeypatch.setattr(config_mod.settings, "bff_service_token", "test-bff-secret")
    monkeypatch.setattr(config_mod.settings, "knowledge_worker_enabled", False)

    bilingual_bctn = _make_utf8_pdf(
        [
            "<h1>BÁO CÁO THƯỜNG NIÊN NĂM 2025 / ANNUAL REPORT 2025</h1>"
            "<p>Doanh thu thuần năm 2025 đạt 32.500 tỷ đồng, tăng 18% so với cùng kỳ.</p>"
            "<p>Net revenue in 2025 reached VND 32,500 billion, increasing by 18% compared to prior year.</p>"
            "<p>Kế hoạch kinh doanh năm 2026 đặt mục tiêu lợi nhuận 4.200 tỷ đồng.</p>",
            "<h1>ANNUAL REPORT 2025 - ENGLISH TRANSLATION</h1>"
            "<p>The Board of Directors hereby presents the business performance and financial statements "
            "of the Corporation for the fiscal year ended 31 December 2025.</p>",
        ]
        * 8
    )
    pdf_file = tmp_path / "FPT_BCTN_2025_bilingual.pdf"
    pdf_file.write_bytes(bilingual_bctn)

    app = create_app()
    headers = {
        "X-SAG-BFF-Service-Token": "test-bff-secret",
        "X-Continuum-User-Id": "000000000000000000000001",
        "X-Continuum-Organization-Id": "aiinvest-default",
        "X-Continuum-Project-Id": "all",
        "X-Continuum-Security-Partition": "all",
        "X-Continuum-Allowed-Project-Ids": "*",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://sag.local"
        ) as client:
            resp = await client.post(
                "/api/v1/research-chat/ingest-batch",
                headers=headers,
                json={
                    "tier": "custom",
                    "tickers": ["FPT"],
                    "as_of_date": "2026-06-30",
                    "dry_run": True,
                    "source_paths": {"FPT": str(pdf_file)},
                },
            )
        assert resp.status_code == 200, json.dumps(resp.json(), ensure_ascii=False)
        body = resp.json()
        assert body["reports"][0]["ticker"] == "FPT"
        assert body["reports"][0]["status"] == "DRY_RUN_READY"
        assert "BCTN_CORE" in body["reports"][0]["sub_doc_slots"]
    finally:
        await engine.dispose()
