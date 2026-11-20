from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openpyxl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.core.config import Settings
from sag_api.core.errors import ValidationError
from sag_api.db.base import Base
from sag_api.db.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    IngestionRun,
    Job,
)
from sag_api.enums import DocumentStatus
from sag_api.jobs import tasks
from sag_api.parsing.canonical import extract_canonical_blocks
from sag_api.parsing.local_worker import (
    _decode_rtf_bytes,
    _extract_pptx_streaming,
    _extract_xlsx_streaming,
    _LocalParseValidationError,
    _sanitize_docx_to_temp_file,
    _verify_image_dimensions_safe,
)
from sag_api.parsing.mineru import MinerUClient, _markdown_from_zip_path
from sag_api.parsing.mineru_official import OfficialMinerUClient
from sag_api.parsing.remote_object import (
    stream_remote_object_to_snapshot,
    validate_remote_object_url,
)
from sag_api.parsing.service import (
    ooxml_has_visual_content,
    prepare_document,
    should_route_to_mineru,
)
from sag_api.services import search_index_service, settings_service
from sag_api.services.document_service import handle_document_object_ingest


def _make_settings(**overrides) -> Settings:
    defaults = {
        "_env_file": None,
        "document_parser": "auto",
        "mineru_provider": "official",
        "mineru_base_url": "https://mineru.net",
        "mineru_official_api_token": "official-mineru-v4-test-token",
        "mineru_official_model": "vlm",
        "mineru_poll_interval": 0.05,
        "mineru_poll_timeout": 5.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _create_ooxml_zip(path: str, entries: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return path


def _build_mineru_result_zip_bytes() -> bytes:
    buf = io.BytesIO()
    content_list = [
        {"type": "text", "text": "MinerU Official V4", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Parsed page two content.", "page_idx": 1},
    ]
    full_md = "# MinerU Official V4\n\nParsed page two content.\n"
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("output/full.md", full_md.encode("utf-8"))
        zf.writestr("output/doc_content_list.json", json.dumps(content_list).encode("utf-8"))
    return buf.getvalue()


def test_smart_hybrid_mineru_routing_option_a_official_only(tmp_path) -> None:
    cfg = _make_settings()
    cfg_no_token = _make_settings(mineru_official_api_token=None)
    cfg_forced_mineru = _make_settings(document_parser="mineru")
    cfg_forced_local = _make_settings(document_parser="markitdown")

    client = MinerUClient(cfg)
    assert isinstance(client, OfficialMinerUClient)
    assert client.signature == "mineru-official-vlm-auto"

    # Legacy 302 DB overrides are cleansed so official token is never overwritten
    cleaned = settings_service._normalize_overrides(
        {
            "mineru_provider": "302",
            "mineru_base_url": "https://api.302ai.cn",
            "mineru_api_key": "legacy-302-key",
            "mineru_version": "2.5",
        }
    )
    assert cleaned["mineru_provider"] == "official"
    assert cleaned["mineru_base_url"] == "https://mineru.net"
    assert "mineru_api_key" not in cleaned
    assert "mineru_version" not in cleaned

    # 1. Direct MinerU formats (.pdf, .doc, .ppt, images)
    for ext in (".pdf", ".doc", ".ppt", ".png", ".jpg", ".webp"):
        sample = str(tmp_path / f"sample{ext}")
        assert should_route_to_mineru(sample, cfg) is True
        assert should_route_to_mineru(sample, cfg_no_token) is False
        assert should_route_to_mineru(sample, cfg_forced_local) is False

    # 2. Pure-text DOCX / PPTX stay on fast local lane under auto
    pure_docx = _create_ooxml_zip(
        str(tmp_path / "pure.docx"),
        {"word/document.xml": b"<w:document><w:body>Pure text</w:body></w:document>"},
    )
    assert ooxml_has_visual_content(pure_docx) is False
    assert should_route_to_mineru(pure_docx, cfg) is False
    assert should_route_to_mineru(pure_docx, cfg_forced_mineru) is True

    # 3. DOCX with embedded screenshot or chart routes to MinerU Official under auto
    visual_docx = _create_ooxml_zip(
        str(tmp_path / "visual.docx"),
        {
            "word/document.xml": b"<w:document/>",
            "word/media/diagram1.png": b"\x89PNG\r\n\x1a\nfake",
        },
    )
    assert ooxml_has_visual_content(visual_docx) is True
    assert should_route_to_mineru(visual_docx, cfg) is True

    # 4. PPTX with SmartArt/chart routes to MinerU Official; pure-text PPTX stays local
    pure_pptx = _create_ooxml_zip(
        str(tmp_path / "pure.pptx"),
        {"ppt/slides/slide1.xml": b"<p:sld/>"},
    )
    chart_pptx = _create_ooxml_zip(
        str(tmp_path / "chart.pptx"),
        {"ppt/slides/slide1.xml": b"<p:sld/>", "ppt/charts/chart1.xml": b"<c:chartSpace/>"},
    )
    assert should_route_to_mineru(pure_pptx, cfg) is False
    assert should_route_to_mineru(chart_pptx, cfg) is True

    # 5. Excel & plain text never route to MinerU
    assert should_route_to_mineru(str(tmp_path / "sheet.xlsx"), cfg_forced_mineru) is False
    assert should_route_to_mineru(str(tmp_path / "readme.md"), cfg_forced_mineru) is False


@pytest.mark.asyncio
async def test_official_mineru_v4_direct_r2_url_task_and_batch_upload_fallback(
    tmp_path, monkeypatch
) -> None:
    cfg = _make_settings(mineru_official_api_token="secret-official-token-51chars")
    sample_pdf = tmp_path / "scanned.pdf"
    sample_pdf.write_bytes(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
    zip_bytes = _build_mineru_result_zip_bytes()
    r2_presigned_url = (
        "https://acct123.r2.cloudflarestorage.com/datn/scanned.pdf"
        "?X-Amz-Signature=supersecret"
    )

    monkeypatch.setattr(
        "sag_api.parsing.mineru._assert_public_host",
        AsyncMock(return_value=None),
    )

    # Case A: Zero-upload direct URL task succeeds via POST /api/v4/extract/task
    seen_requests: list[tuple[str, str, str | None]] = []
    poll_counter = {"count": 0}

    def _direct_url_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        seen_requests.append((request.method, str(request.url), auth))
        if request.method == "POST" and str(request.url) == "https://mineru.net/api/v4/extract/task":
            assert auth == "Bearer secret-official-token-51chars"
            body = json.loads(request.content.decode("utf-8"))
            assert body["url"] == r2_presigned_url
            assert body["model_version"] == "vlm"
            return httpx.Response(
                200,
                json={"code": 0, "msg": "ok", "data": {"task_id": "task-v4-direct-1"}},
            )
        if request.method == "GET" and str(request.url) == "https://mineru.net/api/v4/extract/task/task-v4-direct-1":
            assert auth == "Bearer secret-official-token-51chars"
            poll_counter["count"] += 1
            if poll_counter["count"] == 1:
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"task_id": "task-v4-direct-1", "state": "running"}},
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-v4-direct-1",
                        "state": "done",
                        "full_zip_url": "https://cdn-mineru.openxlab.org.cn/results/res1.zip",
                    },
                },
            )
        if request.method == "GET" and str(request.url) == "https://cdn-mineru.openxlab.org.cn/results/res1.zip":
            # Must NEVER leak Authorization header to CDN result download URL
            assert auth is None
            return httpx.Response(
                200,
                headers={"content-type": "application/zip"},
                content=zip_bytes,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        "sag_api.parsing.mineru_official.httpx.AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(_direct_url_handler), **kw),
    )
    monkeypatch.setattr(
        "sag_api.parsing.mineru.httpx.AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(_direct_url_handler), **kw),
    )

    emitted_states: list[dict] = []

    async def _record_state(st: dict) -> None:
        # Secret R2 presigned URL must NEVER appear in persisted state
        assert r2_presigned_url not in json.dumps(st)
        emitted_states.append(dict(st))

    client = MinerUClient(cfg)
    md = await client.parse(
        str(sample_pdf),
        on_state=_record_state,
        remote_source_url=r2_presigned_url,
    )
    assert "<!-- PAGE 1 -->" in md
    assert "<!-- PAGE 2 -->" in md
    assert "MinerU Official V4" in md
    assert any(st.get("upload_source") == "remote_presigned" for st in emitted_states)
    assert emitted_states[-1]["status"] == "done"

    # Case B: Direct URL task rejected by upstream -> automatic fallback to file-urls/batch + PUT streaming
    uploaded_bytes = bytearray()

    def _fallback_batch_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if request.method == "POST" and str(request.url) == "https://mineru.net/api/v4/extract/task":
            return httpx.Response(
                200,
                json={"code": -60001, "msg": "url query signature not supported"},
            )
        if request.method == "POST" and str(request.url) == "https://mineru.net/api/v4/file-urls/batch":
            assert auth == "Bearer secret-official-token-51chars"
            body = json.loads(request.content.decode("utf-8"))
            assert body["model_version"] == "vlm"
            assert body["files"] == [{"name": "scanned.pdf"}]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "batch_id": "batch-v4-99",
                        "file_urls": ["https://oss-mineru.openxlab.org.cn/upload/scanned.pdf?sig=1"],
                    },
                },
            )
        if request.method == "PUT" and str(request.url).startswith("https://oss-mineru.openxlab.org.cn/upload/"):
            assert auth is None
            uploaded_bytes.extend(request.content)
            return httpx.Response(200)
        if request.method == "GET" and str(request.url) == "https://mineru.net/api/v4/extract-results/batch/batch-v4-99":
            assert auth == "Bearer secret-official-token-51chars"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {
                                "file_name": "scanned.pdf",
                                "state": "done",
                                "full_zip_url": "https://cdn-mineru.openxlab.org.cn/results/batch99.zip",
                            }
                        ]
                    },
                },
            )
        if request.method == "GET" and str(request.url) == "https://cdn-mineru.openxlab.org.cn/results/batch99.zip":
            assert auth is None
            return httpx.Response(200, content=zip_bytes)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    monkeypatch.setattr(
        "sag_api.parsing.mineru_official.httpx.AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(_fallback_batch_handler), **kw),
    )
    monkeypatch.setattr(
        "sag_api.parsing.mineru.httpx.AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(_fallback_batch_handler), **kw),
    )

    batch_states: list[dict] = []

    async def _record_batch_state(st: dict) -> None:
        assert r2_presigned_url not in json.dumps(st)
        batch_states.append(dict(st))

    md_fallback = await MinerUClient(cfg).parse(
        str(sample_pdf),
        on_state=_record_batch_state,
        remote_source_url=r2_presigned_url,
    )
    assert bytes(uploaded_bytes) == sample_pdf.read_bytes()
    assert "<!-- PAGE 1 -->" in md_fallback
    assert "<!-- PAGE 2 -->" in md_fallback
    assert any(st.get("batch_id") == "batch-v4-99" for st in batch_states)


def test_mineru_zip_streaming_injects_monotonic_page_markers(tmp_path) -> None:
    full_md = (
        "# System Architecture\n\n"
        "Gateway authenticates incoming JWT assertions.\n\n"
        "## Storage Layer\n\n"
        "| Service | Engine |\n"
        "| --- | --- |\n"
        "| Vector | Qdrant |\n\n"
        "## Worker Queue\n\n"
        "BullMQ coordinates asynchronous R2 object ingestion."
    )
    content_list = [
        {"type": "text", "text": "System Architecture", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Gateway authenticates incoming JWT assertions.", "page_idx": 0},
        {"type": "text", "text": "Storage Layer", "text_level": 2, "page_idx": 1},
        {"type": "table", "table_body": "<table><tr><td>Vector</td><td>Qdrant</td></tr></table>", "page_idx": 1},
        {"type": "text", "text": "Worker Queue", "text_level": 2, "page_idx": 2},
        {"type": "text", "text": "BullMQ coordinates asynchronous R2 object ingestion.", "page_idx": 2},
    ]
    zip_path = str(tmp_path / "mineru_result.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("output/full.md", full_md.encode("utf-8"))
        zf.writestr("output/doc_content_list.json", json.dumps(content_list).encode("utf-8"))
        zf.writestr("output/images/crop_01.jpg", b"\xff\xd8\xff\xe0" + b"x" * 4096)

    extracted_md = _markdown_from_zip_path(zip_path, 20 * 1024 * 1024)
    assert "<!-- PAGE 1 -->" in extracted_md
    assert "<!-- PAGE 2 -->" in extracted_md
    assert "<!-- PAGE 3 -->" in extracted_md

    blocks = extract_canonical_blocks(extracted_md, version_id="ver-1")
    pages_by_text = {b.normalized_text: b.page_from for b in blocks}
    assert pages_by_text["System Architecture"] == 1
    assert pages_by_text["Storage Layer"] == 2
    assert pages_by_text["Worker Queue"] == 3
    assert any(b.block_type == "table" and b.page_from == 2 for b in blocks)


def test_local_streaming_parsers_xlsx_pptx_docx_rtf_and_bomb_guard(tmp_path) -> None:
    # 1. Streaming XLSX with bounded rows
    xlsx_path = str(tmp_path / "metrics.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Latency"
    ws.append(["Endpoint", "P95_ms"])
    for idx in range(1, 10):
        ws.append([f"/api/v{idx}", idx * 12])
    wb.save(xlsx_path)

    xlsx_md = _extract_xlsx_streaming(xlsx_path, max_rows=5, max_cols=8)
    assert "<!-- PAGE 1 -->" in xlsx_md
    assert "## Sheet: Latency" in xlsx_md
    assert "| Endpoint | P95_ms |" in xlsx_md
    assert "truncated at 5" in xlsx_md

    # 2. Streaming PPTX slide XML + speaker notes (skips ppt/media/*)
    slide1_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree>
        <p:sp>
          <p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
          <p:txBody><a:p><a:r><a:t>Zero-Buffer R2 Bridge</a:t></a:r></a:p></p:txBody>
        </p:sp>
        <p:sp>
          <p:nvSpPr><p:nvPr/></p:nvSpPr>
          <p:txBody>
            <a:p><a:r><a:t>Presigned PUT upload</a:t></a:r></a:p>
            <a:p><a:r><a:t>64KB streaming worker</a:t></a:r></a:p>
          </p:txBody>
        </p:sp>
      </p:spTree></p:cSld>
    </p:sld>"""
    notes1_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
             xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody>
        <a:p><a:r><a:t>Emphasize zero RAM overhead on BFF.</a:t></a:r></a:p>
      </p:txBody></p:sp></p:spTree></p:cSld>
    </p:notes>"""
    pptx_path = _create_ooxml_zip(
        str(tmp_path / "deck.pptx"),
        {
            "ppt/slides/slide1.xml": slide1_xml,
            "ppt/notesSlides/notesSlide1.xml": notes1_xml,
            "ppt/media/heavy_video.mp4": b"\x00" * 1024,
        },
    )
    pptx_md = _extract_pptx_streaming(pptx_path)
    assert "<!-- PAGE 1 -->" in pptx_md
    assert "Zero-Buffer R2 Bridge" in pptx_md
    assert "Emphasize zero RAM overhead on BFF." in pptx_md

    # 3. DOCX disk-to-disk media stripping sanitizer
    rels_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/big.png"/>
    </Relationships>"""
    doc_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>
        <w:p><w:r><w:t>Clean contract text</w:t></w:r><w:drawing><w:blip r:embed="rId1"/></w:drawing></w:p>
      </w:body>
    </w:document>"""
    docx_path = _create_ooxml_zip(
        str(tmp_path / "heavy.docx"),
        {
            "word/document.xml": doc_xml,
            "word/_rels/document.xml.rels": rels_xml,
            "word/media/big.png": b"\x89PNG\r\n\x1a\n" + b"0" * 4096,
        },
    )
    sanitized_docx = _sanitize_docx_to_temp_file(docx_path)
    try:
        assert sanitized_docx != docx_path
        with zipfile.ZipFile(sanitized_docx, "r") as zf:
            assert "word/media/big.png" not in zf.namelist()
            assert b"rId1" not in zf.read("word/document.xml")
    finally:
        if os.path.exists(sanitized_docx) and sanitized_docx != docx_path:
            os.remove(sanitized_docx)

    # 4. RTF skips {\pict ...} binary hex dumps
    rtf_bytes = br"{\rtf1\ansi{\fonttbl{\f0 Arial;}}Hello Architecture\par{\pict\pngblip 001122334455}Second line}"
    rtf_text = _decode_rtf_bytes(rtf_bytes)
    assert "Hello Architecture" in rtf_text
    assert "Second line" in rtf_text
    assert "00112233" not in rtf_text

    # 5. Image decompression-bomb header guard
    bomb_png = str(tmp_path / "bomb.png")
    ihdr_data = struct.pack(">IIBBBBB", 12000, 12000, 8, 2, 0, 0, 0)
    with open(bomb_png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr_data)
    with pytest.raises(_LocalParseValidationError, match="12000x12000"):
        _verify_image_dimensions_safe(bomb_png, 36_000_000)


@pytest.mark.asyncio
async def test_r2_object_ingest_e2e_streaming_verification_and_disk_gc(
    tmp_path, monkeypatch
) -> None:
    cfg = _make_settings(
        mineru_official_api_token=None,
        remote_snapshot_gc_enabled=True,
    )
    cfg.activate_data_dir(tmp_path / "engine_data")
    monkeypatch.setattr(tasks, "settings", cfg)
    monkeypatch.setattr("sag_api.services.document_service.settings", cfg)

    # Reject untrusted hosts immediately
    with pytest.raises(ValidationError) as exc_info:
        validate_remote_object_url("https://attacker.example.com/malicious.pdf", cfg)
    assert exc_info.value.code == "UNTRUSTED_OBJECT_HOST"

    # Build a valid XLSX payload in memory to serve from simulated R2 presigned URL
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "R2_Spec"
    ws.append(["Module", "Owner"])
    ws.append(["IngestionWorker", "PlatformTeam"])
    wb.save(buf)
    xlsx_bytes = buf.getvalue()
    xlsx_sha256 = hashlib.sha256(xlsx_bytes).hexdigest()

    r2_url = (
        "https://acct123.r2.cloudflarestorage.com/datn-bucket/projects/p1/spec.xlsx"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=secret123"
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)

    # Register zero-buffer object ingest
    async with factory() as session:
        resp = await handle_document_object_ingest(
            session,
            tenant_id="tenant-1",
            project_id="proj-1",
            owner_id="user-1",
            security_partition_id="part-proj-1",
            client_token="idem-r2-xlsx-1",
            source_url=r2_url,
            original_filename="spec.xlsx",
            expected_sha256=xlsx_sha256,
            expected_size_bytes=len(xlsx_bytes),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            logical_source_id="continuum:project_document:doc-99",
        )
        assert resp.is_duplicate is False
        assert resp.file_hash == xlsx_sha256

        # Idempotent retry returns duplicate immediately
        dup = await handle_document_object_ingest(
            session,
            tenant_id="tenant-1",
            project_id="proj-1",
            owner_id="user-1",
            security_partition_id="part-proj-1",
            client_token="idem-r2-xlsx-1",
            source_url=r2_url,
            original_filename="spec.xlsx",
            expected_sha256=xlsx_sha256,
            expected_size_bytes=len(xlsx_bytes),
            logical_source_id="continuum:project_document:doc-99",
        )
        assert dup.is_duplicate is True
        assert dup.run_id == resp.run_id

    # Mock DNS public check & httpx streaming from R2
    monkeypatch.setattr(
        "sag_api.parsing.remote_object._assert_public_host",
        AsyncMock(return_value=None),
    )

    def _mock_transport(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == r2_url
        return httpx.Response(
            200,
            headers={"content-length": str(len(xlsx_bytes))},
            content=xlsx_bytes,
        )

    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "sag_api.parsing.remote_object.httpx.AsyncClient",
        lambda **kw: orig_async_client(transport=httpx.MockTransport(_mock_transport), **kw),
    )

    async def _fake_index(session, *, document_version, **kwargs):
        document_version.search_status = "SEARCH_READY"
        document_version.search_ready_at = datetime.now(UTC)
        return [SimpleNamespace(id="unit-1")]

    monkeypatch.setattr(search_index_service, "run_search_indexing_stage", _fake_index)
    manager = SimpleNamespace(
        get_sag_embedding=AsyncMock(return_value=None),
        process_document=AsyncMock(),
    )

    async with factory() as session:
        job = await session.scalar(select(Job).where(Job.document_id == resp.document_id))
        doc = await session.get(Document, resp.document_id)
        assert job is not None and doc is not None
        raw_snapshot_path = doc.storage_path
        # Zero bytes existed on disk before worker ran!
        assert not os.path.exists(raw_snapshot_path)

        await tasks._process_document_unlocked(session, job, engine_manager=manager)

        await session.refresh(doc)
        await session.refresh(job)
        run = await session.get(IngestionRun, resp.run_id)
        ver = await session.get(DocumentVersion, resp.version_id)
        blocks = (
            await session.scalars(
                select(CanonicalBlock).where(CanonicalBlock.document_version_id == resp.version_id)
            )
        ).all()

        assert doc.status == DocumentStatus.READY
        assert doc.progress == 100
        assert run is not None and run.status == "SUCCEEDED" and run.current_stage == "COMPLETE"
        assert ver is not None and ver.search_status == "SEARCH_READY"
        assert any("IngestionWorker" in b.normalized_text for b in blocks)
        # Presigned URL scrubbed from job.payload after completion
        assert "remote_source_url" not in (job.payload or {})
        # Raw heavy binary was GC'd after COMPLETE, while .parsed.markitdown.md sidecar remains!
        assert not os.path.exists(raw_snapshot_path)
        parsed_cache_path = f"{raw_snapshot_path}.parsed.markitdown.md"
        assert os.path.isfile(parsed_cache_path)

    # Reprocessing / prepare_document recovers directly from sidecar cache without raw binary!
    recovered = await prepare_document(raw_snapshot_path, cfg)
    assert recovered.cached is True
    assert recovered.path == parsed_cache_path

    # Verify SHA-256 mismatch & extension spoofing fail-closed during streaming
    bad_target = str(tmp_path / "spoofed.pdf")
    with pytest.raises(ValidationError, match="PDF"):
        await stream_remote_object_to_snapshot(
            r2_url,
            target_path=bad_target,
            original_filename="spoofed.pdf",
            expected_sha256=xlsx_sha256,
            expected_size_bytes=len(xlsx_bytes),
            settings=cfg,
        )
    assert not os.path.exists(bad_target)

    await engine.dispose()
