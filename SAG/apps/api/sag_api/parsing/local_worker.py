"""Isolated subprocess worker & memory-bounded local parsers for binary documents.

Prevents C-extension memory fragmentation, GIL starvation, and OOM spikes on the
FastAPI event loop when parsing PDF, DOCX, PPTX, XLSX, legacy Office, or images locally.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import concurrent.futures.process
import multiprocessing
import os
import posixpath
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import zipfile
from typing import Any
from xml.etree import ElementTree

from sag_api.core.config import Settings
from sag_api.core.errors import (
    ServiceUnavailableError,
    UpstreamError,
    ValidationError,
)

_IMAGE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
)
_PACKAGE_RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_RELATIONSHIP_TAG = f"{{{_PACKAGE_RELATIONSHIP_NAMESPACE}}}Relationship"
_WORD_RELATIONSHIPS_PREFIX = "word/_rels/"
_WORDPROCESSING_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DRAWING_TAG = f"{{{_WORDPROCESSING_NAMESPACE}}}drawing"
_PICT_TAG = f"{{{_WORDPROCESSING_NAMESPACE}}}pict"
_OFFICE_DOCUMENT_RELATIONSHIP_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_RELATIONSHIP_EMBED_ATTRIBUTE = f"{{{_OFFICE_DOCUMENT_RELATIONSHIP_NAMESPACE}}}embed"
_RELATIONSHIP_ID_ATTRIBUTE = f"{{{_OFFICE_DOCUMENT_RELATIONSHIP_NAMESPACE}}}id"

_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PRESENTATIONML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_SLIDE_NAME_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAX_IMAGE_PIXELS = 36_000_000  # 36 MP decompression-bomb guard

_POOL_LOCK = threading.Lock()
_EXECUTOR: concurrent.futures.ProcessPoolExecutor | None = None
_EXECUTOR_CONFIG: tuple[int, int] | None = None


class _LocalParseValidationError(ValueError):
    """Picklable domain validation failure raised inside subprocess worker."""


class _LocalParseUpstreamError(RuntimeError):
    """Picklable missing-dependency failure raised inside subprocess worker."""


def _get_or_create_executor(
    max_workers: int,
    max_tasks_per_child: int,
) -> concurrent.futures.ProcessPoolExecutor:
    global _EXECUTOR, _EXECUTOR_CONFIG
    desired = (max(1, int(max_workers)), max(1, int(max_tasks_per_child)))
    with _POOL_LOCK:
        if _EXECUTOR is None or _EXECUTOR_CONFIG != desired:
            if _EXECUTOR is not None:
                try:
                    _EXECUTOR.shutdown(wait=False, cancel_futures=True)
                except Exception:  # noqa: BLE001
                    pass
            ctx = multiprocessing.get_context("spawn")
            _EXECUTOR = concurrent.futures.ProcessPoolExecutor(
                max_workers=desired[0],
                mp_context=ctx,
                max_tasks_per_child=desired[1],
            )
            _EXECUTOR_CONFIG = desired
        return _EXECUTOR


def _reset_executor() -> None:
    global _EXECUTOR, _EXECUTOR_CONFIG
    with _POOL_LOCK:
        if _EXECUTOR is not None:
            try:
                _EXECUTOR.shutdown(wait=False, cancel_futures=True)
            except Exception:  # noqa: BLE001
                pass
        _EXECUTOR = None
        _EXECUTOR_CONFIG = None


atexit.register(_reset_executor)


async def convert_local_isolated(path: str, settings: Settings) -> str:
    """Convert a non-plain-text document to Markdown inside an isolated subprocess."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix in _IMAGE_EXTENSIONS:
        # Fast decompression-bomb & clear OCR guidance without spawning a worker unnecessarily
        await asyncio.to_thread(_verify_image_dimensions_safe, path, _MAX_IMAGE_PIXELS)
        raise ValidationError(
            "Tệp hình ảnh cần cấu hình MinerU OCR (SAG_MINERU_OFFICIAL_API_TOKEN) để trích xuất văn bản."
        )

    executor = _get_or_create_executor(
        settings.local_parse_workers,
        settings.local_parse_max_tasks_per_child,
    )
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(
        executor,
        _parse_binary_file_worker,
        path,
        int(settings.excel_max_rows_per_sheet),
        int(settings.excel_max_cols_per_sheet),
    )
    try:
        markdown = await asyncio.wait_for(
            future,
            timeout=float(settings.local_parse_timeout_seconds),
        )
    except TimeoutError as exc:
        _reset_executor()
        raise ServiceUnavailableError(
            f"Phân tích tệp cục bộ quá thời gian cho phép ({settings.local_parse_timeout_seconds:.0f}s)"
        ) from exc
    except concurrent.futures.process.BrokenProcessPool as exc:
        _reset_executor()
        raise ServiceUnavailableError(
            "Tiến trình phân tích tệp cục bộ bị gián đoạn do vượt ngưỡng tài nguyên"
        ) from exc
    except _LocalParseUpstreamError as exc:
        raise UpstreamError(str(exc)) from exc
    except _LocalParseValidationError as exc:
        raise ValidationError(str(exc)) from exc
    except (ImportError, ModuleNotFoundError) as exc:
        raise UpstreamError("Chưa cài bộ chuyển đổi cục bộ nên không thể phân tích tệp này") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(f"Phân tích tệp cục bộ thất bại: {exc}") from exc

    cleaned = markdown.strip()
    if not _is_meaningful_markdown(cleaned):
        raise ValidationError("Không trích xuất được văn bản hợp lệ từ tệp tài liệu")
    return cleaned + "\n"


def _parse_binary_file_worker(
    path: str,
    excel_max_rows: int = 5000,
    excel_max_cols: int = 64,
) -> str:
    """Top-level picklable entrypoint executed inside the spawned worker process."""
    suffix = os.path.splitext(path)[1].lower()

    if suffix in _IMAGE_EXTENSIONS:
        _verify_image_dimensions_safe(path, _MAX_IMAGE_PIXELS)
        raise _LocalParseValidationError(
            "Tệp hình ảnh cần cấu hình MinerU OCR (SAG_MINERU_OFFICIAL_API_TOKEN) để trích xuất văn bản."
        )

    if suffix == ".pdf":
        return _extract_pdf_streaming(path)

    if suffix == ".docx":
        return _extract_docx_streaming(path)

    if suffix == ".pptx":
        return _extract_pptx_streaming(path)

    if suffix == ".xlsx":
        return _extract_xlsx_streaming(path, max_rows=excel_max_rows, max_cols=excel_max_cols)

    if suffix == ".rtf":
        return _extract_rtf_file(path)

    if suffix in {".doc", ".ppt"}:
        return _extract_legacy_office(path, suffix)

    return _convert_via_markitdown(path)


def _is_meaningful_markdown(markdown: str) -> bool:
    normalized = markdown.strip().casefold()
    return bool(normalized) and normalized not in {
        "none",
        "null",
        "undefined",
        "nan",
        "{}",
        "[]",
    }


# ---------------------------------------------------------------------------
# 1. PDF Page-by-Page Streaming Extractor (< 20 MB RAM, preserves <!-- PAGE N -->)
# ---------------------------------------------------------------------------


def _extract_pdf_streaming(path: str) -> str:
    pages_out: list[str] = []
    page_count = 0

    # Priority 1: PyMuPDF (fitz) — C-speed page-by-page extraction with table awareness
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError:
        fitz = None

    if fitz is not None:
        with fitz.open(path) as doc:
            page_count = len(doc)
            for idx in range(page_count):
                page = doc.load_page(idx)
                page_no = idx + 1
                parts: list[str] = []
                text = (page.get_text("text") or "").strip()
                if text:
                    parts.append(text)
                if parts:
                    pages_out.append(f"<!-- PAGE {page_no} -->\n\n" + "\n\n".join(parts))
        combined = "\n\n".join(pages_out).strip()
        if _is_meaningful_markdown(combined):
            return combined
        if page_count > 0:
            raise _LocalParseValidationError(
                "Tệp PDF không có lớp văn bản (scanned PDF). Vui lòng cấu hình SAG_MINERU_OFFICIAL_API_TOKEN để bật MinerU OCR."
            )

    # Priority 2: pypdf page-by-page
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None  # type: ignore[assignment]

    if PdfReader is not None:
        reader = PdfReader(path)
        page_count = len(reader.pages)
        for page_no, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages_out.append(f"<!-- PAGE {page_no} -->\n\n{text}")
        combined = "\n\n".join(pages_out).strip()
        if _is_meaningful_markdown(combined):
            return combined
        if page_count > 0:
            raise _LocalParseValidationError(
                "Tệp PDF không có lớp văn bản (scanned PDF). Vui lòng cấu hình SAG_MINERU_OFFICIAL_API_TOKEN để bật MinerU OCR."
            )

    # Priority 3: MarkItDown fallback
    md = _convert_via_markitdown(path)
    if not _is_meaningful_markdown(md):
        raise _LocalParseValidationError(
            "Tệp PDF không có lớp văn bản (scanned PDF). Vui lòng cấu hình SAG_MINERU_OFFICIAL_API_TOKEN để bật MinerU OCR."
        )
    return md


# ---------------------------------------------------------------------------
# 2. DOCX Disk-to-Disk Streaming Sanitizer (omits word/media/* binary payloads)
# ---------------------------------------------------------------------------


def _extract_docx_streaming(path: str) -> str:
    temp_docx = _sanitize_docx_to_temp_file(path)
    try:
        return _convert_via_markitdown(temp_docx)
    finally:
        if temp_docx != path:
            try:
                os.remove(temp_docx)
            except OSError:
                pass


def _sanitize_docx_to_temp_file(path: str) -> str:
    """Stream DOCX entries to a temporary file on disk, omitting word/media/* binaries
    and cleaning dangling image relationships so MarkItDown uses ~90% less RAM.
    """
    try:
        with zipfile.ZipFile(path, "r") as source:
            entries = source.infolist()
            kept_entries = [
                entry
                for entry in entries
                if not entry.filename.lower().startswith("word/media/")
            ]
            kept_names = frozenset(entry.filename for entry in kept_entries)
            replacements: dict[str, bytes] = {}

            for entry in kept_entries:
                if not (
                    entry.filename.startswith(_WORD_RELATIONSHIPS_PREFIX)
                    and entry.filename.endswith(".rels")
                ):
                    continue
                relationships = source.read(entry)
                sanitized, removed_ids = _without_dangling_image_relationships(
                    relationships=relationships,
                    relationships_entry=entry.filename,
                    names=kept_names,
                )
                if sanitized != relationships:
                    replacements[entry.filename] = sanitized
                if not removed_ids:
                    continue
                source_part = entry.filename.replace("/_rels/", "/", 1)[: -len(".rels")]
                if source_part not in kept_names:
                    continue
                source_xml = replacements.get(source_part) or source.read(source_part)
                sanitized_source = _without_image_relationship_references(
                    content=source_xml,
                    relationship_ids=removed_ids,
                )
                if sanitized_source != source_xml:
                    replacements[source_part] = sanitized_source

            if len(kept_entries) == len(entries) and not replacements:
                return path

            fd, temp_path = tempfile.mkstemp(prefix=".sanitized-", suffix=".docx")
            os.close(fd)
            try:
                with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as dest:
                    dest.comment = source.comment
                    for entry in kept_entries:
                        if entry.filename in replacements:
                            dest.writestr(entry.filename, replacements[entry.filename])
                        elif entry.is_dir():
                            dest.writestr(entry, b"")
                        else:
                            with source.open(entry, "r") as src_stream, dest.open(
                                entry.filename, "w"
                            ) as dst_stream:
                                shutil.copyfileobj(src_stream, dst_stream, length=64 * 1024)
                return temp_path
            except Exception:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                raise
    except (ElementTree.ParseError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return path


def _without_dangling_image_relationships(
    *,
    relationships: bytes,
    relationships_entry: str,
    names: frozenset[str],
) -> tuple[bytes, frozenset[str]]:
    root = ElementTree.fromstring(relationships)
    removed_ids: set[str] = set()
    source_part = relationships_entry.replace("/_rels/", "/", 1)[: -len(".rels")]
    base_dir = posixpath.dirname(source_part)
    for relationship in list(root.findall(_RELATIONSHIP_TAG)):
        if relationship.get("Type") != _IMAGE_RELATIONSHIP_TYPE:
            continue
        if relationship.get("TargetMode") == "External":
            continue
        target = relationship.get("Target")
        if target is None:
            continue
        target_path = posixpath.normpath(posixpath.join(base_dir, target))
        if target_path in names:
            continue
        rel_id = relationship.get("Id")
        if rel_id is None:
            continue
        root.remove(relationship)
        removed_ids.add(rel_id)
    if not removed_ids:
        return relationships, frozenset()
    return (
        ElementTree.tostring(root, encoding="utf-8", xml_declaration=True),
        frozenset(removed_ids),
    )


def _without_image_relationship_references(
    *,
    content: bytes,
    relationship_ids: frozenset[str],
) -> bytes:
    root = ElementTree.fromstring(content)
    parents = {child: parent for parent in root.iter() for child in parent}
    containers: set[ElementTree.Element[str]] = set()
    for element in root.iter():
        rel_id = element.get(_RELATIONSHIP_EMBED_ATTRIBUTE) or element.get(
            _RELATIONSHIP_ID_ATTRIBUTE
        )
        if rel_id not in relationship_ids:
            continue
        curr: ElementTree.Element[str] | None = element
        while curr is not None:
            if curr.tag in {_DRAWING_TAG, _PICT_TAG}:
                containers.add(curr)
                break
            curr = parents.get(curr)
    if not containers:
        return content
    for container in containers:
        parent = parents.get(container)
        if parent is not None:
            parent.remove(container)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------
# 3. PPTX Streaming Slide XML Extractor (<!-- PAGE N --> per slide, skips media)
# ---------------------------------------------------------------------------


def _extract_pptx_streaming(path: str) -> str:
    slides_markdown: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
            slide_entries: list[tuple[int, str]] = []
            for name in names:
                match = _SLIDE_NAME_RE.match(name)
                if match:
                    slide_entries.append((int(match.group(1)), name))
            slide_entries.sort(key=lambda item: item[0])

            for slide_no, slide_entry in slide_entries:
                slide_xml = archive.read(slide_entry)
                slide_parts = _parse_pptx_slide_xml(slide_xml, slide_no)

                notes_entry = f"ppt/notesSlides/notesSlide{slide_no}.xml"
                if notes_entry in names:
                    notes_text = _parse_pptx_notes_xml(archive.read(notes_entry))
                    if notes_text:
                        slide_parts.append(f"**Speaker Notes:**\n{notes_text}")

                if slide_parts:
                    slides_markdown.append(
                        f"<!-- PAGE {slide_no} -->\n\n" + "\n\n".join(slide_parts)
                    )
    except (zipfile.BadZipFile, ElementTree.ParseError):
        slides_markdown = []

    combined = "\n\n".join(slides_markdown).strip()
    if _is_meaningful_markdown(combined):
        return combined
    return _convert_via_markitdown(path)


def _parse_pptx_slide_xml(xml_bytes: bytes, slide_no: int) -> list[str]:
    root = ElementTree.fromstring(xml_bytes)
    title_text: str | None = None
    body_blocks: list[str] = []

    p_sp = f"{{{_PRESENTATIONML_NS}}}sp"
    p_nvSpPr = f"{{{_PRESENTATIONML_NS}}}nvSpPr"
    p_nvPr = f"{{{_PRESENTATIONML_NS}}}nvPr"
    p_ph = f"{{{_PRESENTATIONML_NS}}}ph"
    p_graphicFrame = f"{{{_PRESENTATIONML_NS}}}graphicFrame"
    a_p = f"{{{_DRAWINGML_NS}}}p"
    a_t = f"{{{_DRAWINGML_NS}}}t"
    a_tbl = f"{{{_DRAWINGML_NS}}}tbl"
    a_tr = f"{{{_DRAWINGML_NS}}}tr"
    a_tc = f"{{{_DRAWINGML_NS}}}tc"

    for elem in root.iter():
        if elem.tag == p_sp:
            is_title = False
            nv = elem.find(p_nvSpPr)
            if nv is not None:
                nv_pr = nv.find(p_nvPr)
                if nv_pr is not None:
                    ph = nv_pr.find(p_ph)
                    if ph is not None and ph.get("type") in {"title", "ctrTitle"}:
                        is_title = True

            paragraphs: list[str] = []
            for para in elem.iter(a_p):
                runs = [t.text for t in para.iter(a_t) if t.text]
                line = "".join(runs).strip()
                if line:
                    paragraphs.append(line)

            if not paragraphs:
                continue
            if is_title and title_text is None:
                title_text = " ".join(paragraphs)
            elif len(paragraphs) > 1:
                body_blocks.append("\n".join(f"- {p}" for p in paragraphs))
            else:
                body_blocks.append(paragraphs[0])

        elif elem.tag == p_graphicFrame:
            for tbl in elem.iter(a_tbl):
                rows_data: list[list[str]] = []
                for tr in tbl.findall(a_tr):
                    row_cells: list[str] = []
                    for tc in tr.findall(a_tc):
                        cell_text = " ".join(
                            "".join(t.text for t in p.iter(a_t) if t.text).strip()
                            for p in tc.iter(a_p)
                        ).strip()
                        row_cells.append(_escape_md_cell(cell_text))
                    if any(row_cells):
                        rows_data.append(row_cells)
                if rows_data:
                    body_blocks.append(_format_markdown_table(rows_data))

    parts: list[str] = []
    if title_text:
        parts.append(f"## Slide {slide_no}: {title_text}")
    elif body_blocks:
        parts.append(f"## Slide {slide_no}")
    parts.extend(body_blocks)
    return parts


def _parse_pptx_notes_xml(xml_bytes: bytes) -> str:
    root = ElementTree.fromstring(xml_bytes)
    a_p = f"{{{_DRAWINGML_NS}}}p"
    a_t = f"{{{_DRAWINGML_NS}}}t"
    lines: list[str] = []
    for para in root.iter(a_p):
        text = "".join(t.text for t in para.iter(a_t) if t.text).strip()
        # Ignore standalone slide-number placeholders
        if text and not text.isdigit():
            lines.append(text)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# 4. XLSX Streaming Read-Only Extractor (bounded rows/cols, < 15 MB RAM)
# ---------------------------------------------------------------------------


def _extract_xlsx_streaming(
    path: str,
    *,
    max_rows: int = 5000,
    max_cols: int = 64,
) -> str:
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError:
        return _convert_via_markitdown(path)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets_out: list[str] = []
    try:
        for sheet_idx, sheet_name in enumerate(wb.sheetnames, start=1):
            ws = wb[sheet_name]
            collected_rows: list[list[str]] = []
            max_used_col = 0
            truncated_rows = False

            for raw_row in ws.iter_rows(values_only=True):
                if raw_row is None:
                    continue
                sliced = list(raw_row[:max_cols])
                cells = [_format_excel_cell(val) for val in sliced]
                # Trim trailing empty cells on this row
                last_non_empty = 0
                for col_i, cell_val in enumerate(cells):
                    if cell_val:
                        last_non_empty = col_i + 1
                if last_non_empty == 0:
                    continue
                if len(collected_rows) >= max_rows:
                    truncated_rows = True
                    break
                max_used_col = max(max_used_col, last_non_empty)
                collected_rows.append(cells[:last_non_empty])

            if not collected_rows or max_used_col == 0:
                continue

            normalized_rows = [
                row + [""] * (max_used_col - len(row)) for row in collected_rows
            ]
            table_md = _format_markdown_table(normalized_rows)
            sheet_parts = [
                f"<!-- PAGE {sheet_idx} -->",
                f"## Sheet: {sheet_name}",
                table_md,
            ]
            if truncated_rows:
                sheet_parts.append(
                    f"*(Sheet truncated at {max_rows} non-empty rows)*"
                )
            sheets_out.append("\n\n".join(sheet_parts))
    finally:
        wb.close()

    combined = "\n\n".join(sheets_out).strip()
    if not _is_meaningful_markdown(combined):
        raise _LocalParseValidationError("Tệp bảng tính Excel không có dữ liệu hợp lệ")
    return combined


def _format_excel_cell(value: Any) -> str:
    if value is None:
        return ""
    return _escape_md_cell(str(value).strip())


def _escape_md_cell(text: str) -> str:
    if not text:
        return ""
    cleaned = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return cleaned.replace("|", "\\|").strip()


def _format_markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    col_count = max(len(r) for r in rows)
    padded = [r + [""] * (col_count - len(r)) for r in rows]
    header = "| " + " | ".join(padded[0]) + " |"
    separator = "| " + " | ".join("---" for _ in range(col_count)) + " |"
    body = ["| " + " | ".join(r) + " |" for r in padded[1:]]
    return "\n".join([header, separator, *body])


# ---------------------------------------------------------------------------
# 5. Legacy Office (.doc / .ppt / .rtf) & Image Decompression-Bomb Guard
# ---------------------------------------------------------------------------


def _extract_legacy_office(path: str, suffix: str) -> str:
    with open(path, "rb") as f:
        prefix = f.read(64)
    if suffix == ".doc" and prefix.lstrip().startswith(b"{\\rtf"):
        return _extract_rtf_file(path)

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        target_ext = "docx" if suffix == ".doc" else "pptx"
        with tempfile.TemporaryDirectory(prefix=".sag-soffice-") as tmp_dir:
            profile_uri = "file:///" + os.path.join(tmp_dir, "profile").replace("\\", "/")
            cmd = [
                soffice,
                "--headless",
                "--nologo",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                target_ext,
                "--outdir",
                tmp_dir,
                path,
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=45,
                check=False,
            )
            converted_candidates = [
                os.path.join(tmp_dir, name)
                for name in os.listdir(tmp_dir)
                if name.lower().endswith(f".{target_ext}")
            ]
            if proc.returncode == 0 and converted_candidates:
                converted_path = converted_candidates[0]
                return (
                    _extract_docx_streaming(converted_path)
                    if target_ext == "docx"
                    else _extract_pptx_streaming(converted_path)
                )

    try:
        md = _convert_via_markitdown(path)
        if _is_meaningful_markdown(md):
            return md
    except Exception:  # noqa: BLE001
        pass

    modern_ext = ".docx" if suffix == ".doc" else ".pptx"
    raise _LocalParseValidationError(
        f"Định dạng Office 97-2003 ({suffix}) cần cấu hình MinerU (SAG_MINERU_OFFICIAL_API_TOKEN) "
        f"hoặc lưu lại dưới định dạng {modern_ext}."
    )


def _extract_rtf_file(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read(32 * 1024 * 1024)
    text = _decode_rtf_bytes(raw)
    if not _is_meaningful_markdown(text):
        raise _LocalParseValidationError("Tệp RTF không có văn bản hợp lệ")
    return text


_SKIP_RTF_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "object",
    "themedata",
    "colorschememapping",
    "datastore",
    "latentstyles",
}


def _decode_rtf_bytes(data: bytes) -> str:
    """Lightweight streaming RTF text extractor that skips {\\pict ...} binary dumps."""
    src = data.decode("latin1", errors="ignore")
    out: list[str] = []
    stack: list[bool] = []
    skip_group = False
    i = 0
    n = len(src)

    while i < n:
        ch = src[i]
        if ch == "{":
            stack.append(skip_group)
            # Check if group starts with \* or a skipped destination control word
            m = re.match(r"^\{\\(?:(\*)|([a-zA-Z]+))", src[i : i + 32])
            if m and (m.group(1) == "*" or (m.group(2) or "").lower() in _SKIP_RTF_DESTINATIONS):
                skip_group = True
            i += 1
        elif ch == "}":
            if stack:
                skip_group = stack.pop()
            i += 1
        elif ch == "\\":
            if i + 1 < n and src[i + 1] in "\\{}":
                if not skip_group:
                    out.append(src[i + 1])
                i += 2
                continue
            if i + 3 < n and src[i + 1] == "'":
                hex_code = src[i + 2 : i + 4]
                if not skip_group:
                    try:
                        out.append(bytes.fromhex(hex_code).decode("cp1252", errors="replace"))
                    except ValueError:
                        pass
                i += 4
                continue
            m = re.match(r"^\\([a-zA-Z]+)(-?\d+)? ?", src[i : i + 32])
            if m:
                word = m.group(1).lower()
                arg = m.group(2)
                if word in _SKIP_RTF_DESTINATIONS:
                    skip_group = True
                elif not skip_group:
                    if word in {"par", "line", "row"}:
                        out.append("\n")
                    elif word in {"tab", "cell"}:
                        out.append("\t")
                    elif word == "u" and arg is not None:
                        codepoint = int(arg)
                        if codepoint < 0:
                            codepoint += 65536
                        if 0 <= codepoint <= 0x10FFFF:
                            out.append(chr(codepoint))
                        # Skip fallback ANSI replacement character '?'
                        end_idx = i + m.end()
                        if end_idx < n and src[end_idx] == "?":
                            i = end_idx + 1
                            continue
                i += m.end()
            else:
                i += 2
        elif ch in "\r\n":
            i += 1
        else:
            if not skip_group:
                out.append(ch)
            i += 1

    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def _verify_image_dimensions_safe(path: str, max_pixels: int) -> None:
    """Inspect image header dimensions without decoding pixel buffers into RAM."""
    with open(path, "rb") as f:
        header = f.read(64 * 1024)
    width = height = 0
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        width, height = struct.unpack(">II", header[16:24])
    elif header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        if len(header) >= 10:
            width, height = struct.unpack("<HH", header[6:10])
    elif header.startswith(b"\xff\xd8\xff"):
        idx = 2
        while idx + 9 < len(header):
            if header[idx] != 0xFF:
                idx += 1
                continue
            marker = header[idx + 1]
            if marker == 0xFF:
                idx += 1
                continue
            if marker in {0xD8, 0xD9} or (0xD0 <= marker <= 0xD7):
                idx += 2
                continue
            seg_len = struct.unpack(">H", header[idx + 2 : idx + 4])[0]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height, width = struct.unpack(">HH", header[idx + 5 : idx + 9])
                break
            idx += 2 + max(2, seg_len)

    if width > 0 and height > 0 and (width * height) > max_pixels:
        raise _LocalParseValidationError(
            f"Kích thước điểm ảnh quá lớn ({width}x{height} px), từ chối xử lý để bảo vệ bộ nhớ."
        )


def _convert_via_markitdown(path: str) -> str:
    try:
        from markitdown import MarkItDown
    except (ImportError, ModuleNotFoundError) as exc:
        raise _LocalParseUpstreamError("Chưa cài MarkItDown nên không thể phân tích tệp này") from exc

    result = MarkItDown().convert(path)
    markdown = getattr(result, "markdown", None)
    if markdown is None:
        markdown = getattr(result, "text_content", None)
    if not isinstance(markdown, str):
        raise TypeError("MarkItDown trả về định dạng kết quả không xác định")
    return markdown
