"""HOSE PDF InputInspector & 4-Branch Hybrid Router (0 LLM).

Productionizes Stage 1 verified logic:
- Safe `.zip` extraction with Zip-Slip path traversal and Zip-Bomb size guards
- Vietnamese font readability scoring (`BAD_FONT` detection at `< 0.72`)
- `NOTICE_ONLY` (`< 15` pages) gate + embedded real-report URL extraction
- Automatic repeating left-sidebar detector (e.g. FPT widescreen) & A3 2-page spread splitter
- 4-Branch PDF Router (A: Digital+Notes, B: Digital/Hybrid no notes, C: Scan TOC-First, D: Notice/BadFont)
"""

from __future__ import annotations

import io
import pathlib
import posixpath
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

import fitz

BranchCode = Literal["A", "B", "C", "D"]
DocClass = Literal["NOTICE_ONLY", "BAD_FONT", "DIGITAL", "HYBRID", "SCAN", "ERROR"]
PageTriageClass = Literal["DIGITAL_CLEAN", "BAD_FONT", "SCAN"]

VN_DIACRITICS = frozenset("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")
COMMON_VN_WORDS = (
    "doanh thu",
    "lợi nhuận",
    "tài sản",
    "công ty",
    "kế hoạch",
    "báo cáo",
    "cổ đông",
    "thuyết minh",
    "kiểm toán",
    "năm",
)

_VN_VOWELS_ALL = (
    "aàáảãạăằắẳẵặâầấẩẫậ"
    "eèéẻẽẹêềếểễệ"
    "iìíỉĩị"
    "oòóỏõọôồốổỗộơờớởỡợ"
    "uùúủũụưừứửữự"
    "yỳýỷỹỵ"
    "AÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬ"
    "EÈÉẺẼẸÊỀẾỂỄỆ"
    "IÌÍỈĨỊ"
    "OÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ"
    "UÙÚỦŨỤƯỪỨỬỮỰ"
    "YỲÝỶỸỴ"
)
_CTG_CORRUPT_D_RE = re.compile(rf"(?:(?<=^)|(?<=[\s(\[\-–/]))[‚È¢½€](?=[{_VN_VOWELS_ALL}])")

DEFAULT_MAX_ZIP_BYTES = 150 * 1024 * 1024  # 150 MB uncompressed cap
DEFAULT_MAX_ZIP_ENTRIES = 64
BAD_FONT_THRESHOLD = 0.72
NOTICE_ONLY_MAX_PAGES = 14


class ZipExtractionError(ValueError):
    """Raised when a ZIP archive fails security or structural validation."""


@dataclass(frozen=True, slots=True)
class VirtualPage:
    pdf_page: int
    side: Literal["FULL", "L", "R"]
    bbox: tuple[float, float, float, float]
    crop_left_x: float = 0.0


@dataclass(frozen=True, slots=True)
class PageTriageInfo:
    page_no: int
    triage_class: PageTriageClass
    char_count: int
    vn_readability_score: float
    is_spread: bool


@dataclass(slots=True)
class InspectionResult:
    pages: int = 0
    mb: float = 0.0
    is_zip: bool = False
    zip_entries: list[str] = field(default_factory=list)
    chosen_entry: str | None = None
    spread_pages: int = 0
    digital_clean_pages: int = 0
    bad_font_pages: int = 0
    scan_pages: int = 0
    median_vn_readability: float = 0.0
    doc_class: DocClass = "ERROR"
    has_embedded_notes: bool = False
    has_segment_or_bank_notes: bool = False
    has_plan: bool = False
    embedded_urls: list[str] = field(default_factory=list)
    routed_branch: BranchCode = "D"
    est_ocr_pages: int = 15
    auto_sidebar_x: float = 0.0
    source_url: str = ""
    error: str | None = None
    page_triage: list[PageTriageInfo] = field(default_factory=list)
    bad_font_page_numbers: list[int] = field(default_factory=list)
    scan_page_numbers: list[int] = field(default_factory=list)
    digital_clean_page_numbers: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "mb": self.mb,
            "is_zip": self.is_zip,
            "zip_entries": list(self.zip_entries),
            "chosen_entry": self.chosen_entry,
            "spread_pages": self.spread_pages,
            "digital_clean_pages": self.digital_clean_pages,
            "bad_font_pages": self.bad_font_pages,
            "scan_pages": self.scan_pages,
            "median_vn_readability": self.median_vn_readability,
            "doc_class": self.doc_class,
            "has_embedded_notes": self.has_embedded_notes,
            "has_segment_or_bank_notes": self.has_segment_or_bank_notes,
            "has_plan": self.has_plan,
            "embedded_urls": list(self.embedded_urls),
            "routed_branch": self.routed_branch,
            "est_ocr_pages": self.est_ocr_pages,
            "auto_sidebar_x": self.auto_sidebar_x,
            "source_url": self.source_url,
            "error": self.error,
            "bad_font_page_numbers": list(self.bad_font_page_numbers),
            "scan_page_numbers": list(self.scan_page_numbers),
            "digital_clean_page_numbers": list(self.digital_clean_page_numbers),
        }


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def normalize_ctg_font_quirks(text: str) -> str:
    """Repair known PDF glyph substitution quirks observed in HOSE filings (e.g. 'Èồng'/'‚ủ'/'¢ược'/'½oái' -> 'đ')."""
    s = unicodedata.normalize("NFC", text or "")
    s = re.sub(r"\(cid:\d+\)", "đ", s)
    s = re.sub(r"(?i)triệu\s+[è‚€¢½]ồng", "Triệu đồng", s)
    s = re.sub(r"(?i)tỷ\s+[è‚€¢½]ồng", "Tỷ đồng", s)
    s = _CTG_CORRUPT_D_RE.sub("đ", s)
    return s


def compute_vn_readability_score(text: str) -> tuple[float, dict[str, Any]]:
    """Compute per-page Vietnamese text readability score (catches BAD_FONT digital fakes)."""
    raw = text or ""
    if not raw.strip():
        return 0.0, {"len": 0, "reason": "EMPTY"}
    nfc = unicodedata.normalize("NFC", raw)
    total_chars = len(nfc)
    bad_ctrl = sum(
        1
        for c in nfc
        if c == "\ufffd" or (ord(c) < 32 and c not in "\n\r\t") or (0x80 <= ord(c) <= 0x9F)
    )
    cid_hits = len(re.findall(r"\(cid:\d+\)", nfc))
    alpha_chars = [c for c in nfc if c.isalpha()]
    vn_chars = sum(1 for c in alpha_chars if c.lower() in VN_DIACRITICS)
    vn_ratio = vn_chars / max(1, len(alpha_chars))
    low = nfc.lower()
    word_hits = sum(1 for w in COMMON_VN_WORDS if w in low)
    corruption_rate = (bad_ctrl + cid_hits * 4) / max(1, total_chars)
    score = 1.0 - min(1.0, corruption_rate * 8.0)
    if len(alpha_chars) >= 80:
        if vn_ratio < 0.03 and word_hits < 2:
            latin_ext_weird = sum(
                1 for c in nfc if 0xC0 <= ord(c) <= 0xFF and c.lower() not in VN_DIACRITICS
            )
            if latin_ext_weird / max(1, len(alpha_chars)) > 0.05:
                score = min(score, 0.35)
            elif word_hits == 0:
                score = min(score, 0.65)
        elif vn_ratio >= 0.06 and word_hits >= 2:
            score = max(score, 0.85 - min(0.4, corruption_rate * 8.0))
    return round(max(0.0, min(1.0, score)), 3), {
        "len": total_chars,
        "vn_ratio": round(vn_ratio, 3),
        "word_hits": word_hits,
        "cid_hits": cid_hits,
        "bad_ctrl": bad_ctrl,
    }


def _validate_zip_member_name(filename: str) -> None:
    """Reject Zip-Slip path traversal attempts."""
    if not filename or "\x00" in filename:
        raise ZipExtractionError("Invalid empty or null-byte ZIP member name")
    normalized = filename.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ZipExtractionError(f"Unsafe absolute ZIP member path: {filename}")
    parts = [p for p in normalized.split("/") if p]
    if any(part == ".." for part in parts):
        raise ZipExtractionError(f"Zip-Slip path traversal detected: {filename}")
    cleaned = posixpath.normpath(normalized)
    if cleaned.startswith("../") or cleaned == "..":
        raise ZipExtractionError(f"Zip-Slip normalized traversal detected: {filename}")


def safe_extract_pdf_from_zip(
    data: bytes,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_ZIP_BYTES,
    max_entries: int = DEFAULT_MAX_ZIP_ENTRIES,
) -> tuple[bytes, str, list[str]]:
    """Safely extract the primary research PDF from a ZIP archive with Zip-Slip & size guards."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ZipExtractionError("Payload is not a valid ZIP archive")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ZipExtractionError(
                    f"ZIP entry count {len(infos)} exceeds limit {max_entries}"
                )

            total_declared_size = 0
            pdf_candidates: list[tuple[zipfile.ZipInfo, int]] = []
            for info in infos:
                _validate_zip_member_name(info.filename)
                if info.is_dir():
                    continue
                if info.file_size < 0 or info.file_size > max_uncompressed_bytes:
                    raise ZipExtractionError(
                        f"ZIP member {info.filename} size {info.file_size} exceeds limit {max_uncompressed_bytes}"
                    )
                total_declared_size += info.file_size
                if total_declared_size > max_uncompressed_bytes:
                    raise ZipExtractionError(
                        f"Total ZIP uncompressed size exceeds limit {max_uncompressed_bytes}"
                    )
                if info.filename.lower().endswith(".pdf") and not info.filename.startswith("__MACOSX"):
                    pdf_candidates.append((info, info.file_size))

            zip_entries = [f"{info.filename} ({sz // 1024}KB)" for info, sz in pdf_candidates]
            if not pdf_candidates:
                raise ZipExtractionError("ZIP archive contains no PDF files")

            scored: list[tuple[int, int, str, zipfile.ZipInfo]] = []
            for info, fsize in pdf_candidates:
                fl = info.filename.lower()
                is_ballot_or_charter = any(
                    k in fl
                    for k in (
                        "kiem_phieu",
                        "kiểm phiếu",
                        "dieu_le",
                        "điều lệ",
                        "bieu_quyet",
                        "biểu quyết",
                    )
                )
                is_priority_doc = any(
                    k in fl
                    for k in (
                        "nghi_quyet",
                        "nghị quyết",
                        "bctn",
                        "bctc",
                        "annual",
                        "hop_nhat",
                        "hợp nhất",
                    )
                )
                penalty = 2 if is_ballot_or_charter else (0 if is_priority_doc else 1)
                scored.append((penalty, -fsize, info.filename, info))
            scored.sort(key=lambda item: (item[0], item[1], item[2]))
            chosen_info = scored[0][3]

            # Stream-read with hard byte cap to defeat spoofed ZipInfo.file_size headers
            buf = io.BytesIO()
            with zf.open(chosen_info, "r") as member_stream:
                read_total = 0
                while True:
                    chunk = member_stream.read(65536)
                    if not chunk:
                        break
                    read_total += len(chunk)
                    if read_total > max_uncompressed_bytes:
                        raise ZipExtractionError(
                            f"Actual uncompressed bytes of {chosen_info.filename} exceed limit {max_uncompressed_bytes}"
                        )
                    buf.write(chunk)
            return buf.getvalue(), chosen_info.filename, zip_entries
    except zipfile.BadZipFile as exc:
        raise ZipExtractionError(f"Corrupt ZIP archive: {exc}") from exc


def detect_repeating_left_sidebar_x(pdf_source: bytes | pathlib.Path | str) -> float:
    """Zero-hardcode multi-page repeating-band sidebar detector using PyMuPDF.

    Returns x-cutoff right after persistent navigation sidebar on widescreen single-pages (e.g. FPT),
    and 0.0 on standard pages or 2-page A3 spreads with central gutter (e.g. PVT, CTG).
    """
    doc = (
        fitz.open(stream=pdf_source, filetype="pdf")
        if isinstance(pdf_source, (bytes, bytearray))
        else fitz.open(str(pdf_source))
    )
    try:
        n = len(doc)
        if n < 6:
            return 0.0
        sample_indices = sorted({int(i * (n - 1) / min(18, n - 1)) for i in range(min(19, n))})
        mid_pg = doc[sample_indices[len(sample_indices) // 2]]
        page_w, page_h = float(mid_pg.rect.width), float(mid_pg.rect.height)
        if page_w <= page_h * 1.25:
            return 0.0
        gutter_hits = 0
        word_page_counts: Counter[tuple[str, int]] = Counter()
        word_max_x1: dict[tuple[str, int], float] = {}
        for idx in sample_indices:
            pg = doc[idx]
            w_p, h_p = float(pg.rect.width), float(pg.rect.height)
            words = pg.get_text("words")
            mid = w_p / 2.0
            crossing = sum(1 for w in words if w[0] < mid - 4 and w[2] > mid + 4)
            if crossing <= 2 and len(words) > 20:
                gutter_hits += 1
            seen_on_pg: set[tuple[str, int]] = set()
            for x0, y0, x1, _y1, txt, *_ in words:
                if x1 < w_p * 0.25 and 20.0 < y0 < h_p - 35.0:
                    key = (str(txt).strip().lower(), round(float(x0) / 10.0))
                    if key not in seen_on_pg:
                        seen_on_pg.add(key)
                        word_page_counts[key] += 1
                        word_max_x1[key] = max(word_max_x1.get(key, 0.0), float(x1))
        if gutter_hits >= len(sample_indices) * 0.6:
            return 0.0
        min_rep = max(3, int(len(sample_indices) * 0.25))
        rep_x1s = [word_max_x1[k] for k, cnt in word_page_counts.items() if cnt >= min_rep]
        if len(rep_x1s) < 3:
            return 0.0
        return round(max(rep_x1s) + 8.0, 1)
    finally:
        doc.close()


def split_virtual_pages(page: Any, left_sidebar_x: float = 0.0) -> list[VirtualPage]:
    """Detect A3 2-page spread (width > 1.25 * height with central gutter) vs single page."""
    w, h = float(page.width), float(page.height)
    page_num = int(getattr(page, "page_number", 1))
    if w > h * 1.25 and left_sidebar_x == 0.0:
        words = page.extract_words()
        mid = w / 2.0
        crossing = [wd for wd in words if wd["x0"] < mid - 4 and wd["x1"] > mid + 4]
        if len(crossing) <= 2 and len(words) > 10:
            return [
                VirtualPage(page_num, "L", (0.0, 0.0, mid, h)),
                VirtualPage(page_num, "R", (mid, 0.0, w, h)),
            ]
    return [VirtualPage(page_num, "FULL", (left_sidebar_x, 0.0, w, h), left_sidebar_x)]


def inspect_pdf_bytes(data: bytes, source_url: str = "") -> InspectionResult:
    """Full InputInspector: unpacks .zip safely, checks NOTICE_ONLY (<15p), BAD_FONT, notes, spread layout."""
    is_zip = False
    zip_entries: list[str] = []
    chosen_entry: str | None = None

    if zipfile.is_zipfile(io.BytesIO(data)):
        is_zip = True
        try:
            data, chosen_entry, zip_entries = safe_extract_pdf_from_zip(data)
        except ZipExtractionError as exc:
            err_code = "ZIP_NO_PDF" if "no PDF" in str(exc) else f"ZIP_ERROR:{exc}"
            return InspectionResult(
                is_zip=True,
                zip_entries=zip_entries,
                routed_branch="D",
                doc_class="ERROR",
                source_url=source_url,
                error=err_code,
            )

    if not data.startswith(b"%PDF"):
        return InspectionResult(
            is_zip=is_zip,
            zip_entries=zip_entries,
            chosen_entry=chosen_entry,
            routed_branch="D",
            doc_class="ERROR",
            source_url=source_url,
            error=f"NOT_PDF({data[:16]!r})",
        )

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        n_pages = len(doc)
        dig_clean = 0
        bad_font_pages = 0
        scan_pages = 0
        spread_pages = 0
        scores: list[float] = []
        all_text_low: list[str] = []
        embedded_urls: list[str] = []
        page_triage: list[PageTriageInfo] = []
        bad_font_page_numbers: list[int] = []
        scan_page_numbers: list[int] = []
        digital_clean_page_numbers: list[int] = []

        for page_idx, page in enumerate(doc):
            page_no = page_idx + 1
            is_spread = float(page.rect.width) > float(page.rect.height) * 1.25
            if is_spread:
                spread_pages += 1
            t = (page.get_text("text") or "").strip()
            if len(t) < 80:
                scan_pages += 1
                scan_page_numbers.append(page_no)
                page_triage.append(
                    PageTriageInfo(
                        page_no=page_no,
                        triage_class="SCAN",
                        char_count=len(t),
                        vn_readability_score=0.0,
                        is_spread=is_spread,
                    )
                )
            else:
                sc, _ = compute_vn_readability_score(t)
                scores.append(sc)
                if len(t) > 200 and sc < BAD_FONT_THRESHOLD:
                    bad_font_pages += 1
                    bad_font_page_numbers.append(page_no)
                    page_triage.append(
                        PageTriageInfo(
                            page_no=page_no,
                            triage_class="BAD_FONT",
                            char_count=len(t),
                            vn_readability_score=sc,
                            is_spread=is_spread,
                        )
                    )
                else:
                    dig_clean += 1
                    digital_clean_page_numbers.append(page_no)
                    all_text_low.append(t.lower())
                    page_triage.append(
                        PageTriageInfo(
                            page_no=page_no,
                            triage_class="DIGITAL_CLEAN",
                            char_count=len(t),
                            vn_readability_score=sc,
                            is_spread=is_spread,
                        )
                    )
            if n_pages <= NOTICE_ONLY_MAX_PAGES:
                embedded_urls.extend(re.findall(r"https?://[^\s<>\"'\)\]]+", t))
    finally:
        doc.close()

    joined = "\n".join(all_text_low)
    has_embedded_notes = any(
        k in joined
        for k in (
            "thuyết minh báo cáo tài chính",
            "b 09",
            "b09-dn",
            "b05/tctd",
            "thuyết minh bctc",
        )
    )
    has_segment = any(
        k in joined
        for k in (
            "báo cáo bộ phận",
            "thông tin bộ phận",
            "doanh thu thuần theo bộ phận",
            "phân tích chất lượng nợ",
        )
    )
    has_plan = bool(
        re.search(
            r"(?:kế hoạch(?:\s+kinh\s+doanh)?(?:\s+năm)?\s+20\d{2}|mục tiêu(?:\s+năm)?\s+20\d{2})",
            joined,
        )
    )

    if n_pages <= NOTICE_ONLY_MAX_PAGES:
        doc_class: DocClass = "NOTICE_ONLY"
        branch: BranchCode = "D"
        est_ocr_pages = 14
    elif bad_font_pages >= max(3, int(n_pages * 0.30)) or (
        bad_font_pages > 0 and dig_clean == 0 and scan_pages < n_pages
    ):
        doc_class = "BAD_FONT"
        branch = "D"
        est_ocr_pages = 15
    elif dig_clean >= n_pages * 0.70:
        doc_class = "DIGITAL"
        if has_embedded_notes:
            branch = "A"
            est_ocr_pages = 0
        else:
            branch = "B"
            est_ocr_pages = 8
    elif dig_clean >= 10:
        doc_class = "HYBRID"
        branch = "A" if has_embedded_notes and scan_pages < n_pages * 0.35 else "B"
        est_ocr_pages = 8 if branch == "B" else 3
    else:
        doc_class = "SCAN"
        branch = "C"
        est_ocr_pages = 20

    auto_sidebar_x = (
        detect_repeating_left_sidebar_x(data)
        if doc_class in ("DIGITAL", "HYBRID")
        else 0.0
    )

    return InspectionResult(
        pages=n_pages,
        mb=round(len(data) / 1048576, 2),
        is_zip=is_zip,
        zip_entries=zip_entries,
        chosen_entry=chosen_entry,
        spread_pages=spread_pages,
        digital_clean_pages=dig_clean,
        bad_font_pages=bad_font_pages,
        scan_pages=scan_pages,
        median_vn_readability=round(sorted(scores)[len(scores) // 2], 3) if scores else 0.0,
        doc_class=doc_class,
        has_embedded_notes=has_embedded_notes,
        has_segment_or_bank_notes=has_segment,
        has_plan=has_plan,
        embedded_urls=sorted(set(embedded_urls))[:5],
        routed_branch=branch,
        est_ocr_pages=est_ocr_pages,
        auto_sidebar_x=auto_sidebar_x,
        source_url=source_url,
        page_triage=page_triage,
        bad_font_page_numbers=bad_font_page_numbers,
        scan_page_numbers=scan_page_numbers,
        digital_clean_page_numbers=digital_clean_page_numbers,
    )


def inspect_pdf_bytes_detailed(data: bytes, source_url: str = "") -> dict[str, Any]:
    """Dictionary-returning convenience wrapper compatible with Stage 1 prototype."""
    return inspect_pdf_bytes(data, source_url=source_url).to_dict()
