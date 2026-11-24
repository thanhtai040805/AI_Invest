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
COMMON_EN_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "company",
        "corporation",
        "report",
        "annual",
        "resolution",
        "general",
        "meeting",
        "shareholders",
        "shareholder",
        "board",
        "directors",
        "financial",
        "statement",
        "statements",
        "article",
        "approved",
        "million",
        "billion",
        "total",
        "revenue",
        "profit",
        "vietnam",
        "audit",
        "capital",
        "charter",
    }
)
_EN_WORD_TOKEN_RE = re.compile(r"\b[a-z]{3,}\b")
_RESIDUAL_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_VN_VOWELS_UPPER = (
    "AÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬ"
    "EÈÉẺẼẸÊỀẾỂỄỆ"
    "IÌÍỈĨỊ"
    "OÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢ"
    "UÙÚỦŨỤƯỪỨỬỮỰ"
    "YỲÝỶỸỴ"
)
_VN_VOWELS_LOWER = (
    "aàáảãạăằắẳẵặâầấẩẫậ"
    "eèéẻẽẹêềếểễệ"
    "iìíỉĩị"
    "oòóỏõọôồốổỗộơờớởỡợ"
    "uùúủũụưừứửữự"
    "yỳýỷỹỵ"
)
_VN_VOWELS_ALL = _VN_VOWELS_LOWER + _VN_VOWELS_UPPER
_CTRL_D_GLYPH = r"[\x01-\x08\x0b-\x1f\x7f-\x9f]"
_CTG_CORRUPT_D_RE = re.compile(
    rf"(?:(?<=^)|(?<=[\s(\[\-–/]))[‚È¢½€](?=[{_VN_VOWELS_ALL}])"
)
_CTG_SYN_UPPER_D_RE = re.compile(
    rf"(?<![A-Za-z0-9]){_CTRL_D_GLYPH}(?=[{_VN_VOWELS_UPPER}])"
)
_CTG_SYN_LOWER_D_RE = re.compile(
    rf"(?<![A-Za-z0-9]){_CTRL_D_GLYPH}(?=[{_VN_VOWELS_LOWER}])"
)
_TAB_DONG_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s(\[\-–/]))[\t\xa0\xad](?=(?:ồng|ược|ộ|ịnh|ầu|ến|ể|ối|ộng|ơn|ảm|ầy|ạo|ức|ộc|ổi|ặc|ạt|ứng|ều|ịa|ủ|ã|à|ang)\b)"
)

_WATERMARK_OR_SIG_LINE_RE = re.compile(
    r"(?:\boid\.\d|\bdn:\s*c=|\bcn=|\bserialnumber=|digitally\s+signed|signed\s+by|"
    r"ký\s+bởi:|ngày\s+ký:|tổ\s+chức\s+xác\s+thực:|văn\s+bản\s+này\s+được\s+xác\s+thực|"
    r"số\s+định\s+danh:|date:\s*20\d{2}\.\d{2}\.\d{2})",
    re.IGNORECASE,
)
_SHORT_DOC_EXEMPT_TYPES = frozenset(
    {
        "NQ_DHDCD",
        "AGM_RESOLUTION",
        "GIAI_TRINH",
        "GIẢI_TRÌNH",
        "EXPLANATION",
        "LATEST_QUARTER",
    }
)
_FINANCIAL_SHIELD_INSPECT_RE = re.compile(
    r"(?:bảng\s+cân\s+đối\s+kế\s+toán|báo\s+cáo\s+tình\s+hình\s+tài\s+chính|"
    r"báo\s+cáo\s+kết\s+quả\s+hoạt\s+động|báo\s+cáo\s+lưu\s+chuyển\s+tiền\s+tệ|"
    r"\bmẫu\s+số\s+b\s*0[1-9]\b|\bb\s*0[1-9][a-z]?\s*-\s*(?:dn|ctck)\b|\bb\s*0[1-5]\s*/\s*tctd\b|"
    r"thuyết\s+minh\s+báo\s+cáo\s+tài\s+chính|báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập)",
    re.IGNORECASE,
)
_ZIP_PART_RE = re.compile(r"^(?P<stem>.+?)[_\-. ]+(?:p|part)(?P<part>\d{1,2})\.pdf$", re.IGNORECASE)

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
    """Repair PDF glyph substitution quirks in HOSE filings (e.g. ctrl-bytes/'È'/'‚'/'¢'/'½' -> 'đ'/'Đ')."""
    s = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n")
    s = re.sub(r"\(cid:\d+\)", "đ", s)
    s = _CTG_SYN_UPPER_D_RE.sub("Đ", s)
    s = _CTG_SYN_LOWER_D_RE.sub("đ", s)
    s = _TAB_DONG_RE.sub("đ", s)
    s = _RESIDUAL_CTRL_RE.sub("", s)
    s = re.sub(r"(?i)triệu\s+[è‚€¢½]ồng", "Triệu đồng", s)
    s = re.sub(r"(?i)tỷ\s+[è‚€¢½]ồng", "Tỷ đồng", s)
    s = _CTG_CORRUPT_D_RE.sub("đ", s)
    return s


def compute_vn_readability_score(text: str) -> tuple[float, dict[str, Any]]:
    """Compute per-page Vietnamese/bilingual text readability score (catches BAD_FONT digital fakes)."""
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
    en_tokens = _EN_WORD_TOKEN_RE.findall(low)
    en_word_hits = sum(1 for tok in en_tokens if tok in COMMON_EN_WORDS)
    en_ratio = en_word_hits / max(1, len(en_tokens))
    is_clean_english = (
        bad_ctrl == 0
        and cid_hits == 0
        and len(en_tokens) >= 8
        and (en_word_hits >= 4 or (en_word_hits >= 2 and en_ratio >= 0.08))
    )
    corruption_rate = (bad_ctrl + cid_hits * 4) / max(1, total_chars)
    score = 1.0 - min(1.0, corruption_rate * 8.0)
    if len(alpha_chars) >= 80:
        if vn_ratio < 0.03 and word_hits < 2:
            latin_ext_weird = sum(
                1 for c in nfc if 0xC0 <= ord(c) <= 0xFF and c.lower() not in VN_DIACRITICS
            )
            if latin_ext_weird / max(1, len(alpha_chars)) > 0.05:
                score = min(score, 0.35)
            elif word_hits == 0 and not is_clean_english:
                score = min(score, 0.65)
        elif vn_ratio >= 0.06 and word_hits >= 2:
            score = max(score, 0.85 - min(0.4, corruption_rate * 8.0))
    return round(max(0.0, min(1.0, score)), 3), {
        "len": total_chars,
        "vn_ratio": round(vn_ratio, 3),
        "word_hits": word_hits,
        "en_word_hits": en_word_hits,
        "is_clean_english": is_clean_english,
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


def _read_zip_member_bounded(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    remaining_bytes: int,
) -> bytes:
    buf = io.BytesIO()
    with zf.open(info, "r") as member_stream:
        read_total = 0
        while True:
            chunk = member_stream.read(65536)
            if not chunk:
                break
            read_total += len(chunk)
            if read_total > remaining_bytes:
                raise ZipExtractionError(
                    f"Actual uncompressed bytes of {info.filename} exceed limit {remaining_bytes}"
                )
            buf.write(chunk)
    return buf.getvalue()


def safe_extract_pdf_from_zip(
    data: bytes,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_ZIP_BYTES,
    max_entries: int = DEFAULT_MAX_ZIP_ENTRIES,
) -> tuple[bytes, str, list[str]]:
    """Safely extract the primary research PDF (or merged `_p1 + _p2` parts) from a ZIP archive."""
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

            # Detect sequential multi-part filings (e.g. `*_p1.pdf`, `*_p2.pdf`) sharing the chosen stem
            part_match = _ZIP_PART_RE.match(chosen_info.filename.replace("\\", "/").rsplit("/", 1)[-1])
            sibling_parts: list[tuple[int, zipfile.ZipInfo]] = []
            if part_match:
                target_stem = part_match.group("stem").lower()
                for info, _sz in pdf_candidates:
                    base_name = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                    m_sib = _ZIP_PART_RE.match(base_name)
                    if m_sib and m_sib.group("stem").lower() == target_stem:
                        sibling_parts.append((int(m_sib.group("part")), info))
                sibling_parts.sort(key=lambda item: (item[0], item[1].filename))

            if len(sibling_parts) >= 2:
                remaining = max_uncompressed_bytes
                part_blobs: list[bytes] = []
                part_names: list[str] = []
                for _pidx, pinfo in sibling_parts:
                    blob = _read_zip_member_bounded(zf, pinfo, remaining_bytes=remaining)
                    remaining -= len(blob)
                    part_blobs.append(blob)
                    part_names.append(pinfo.filename)
                merged_doc = fitz.open()
                try:
                    for blob in part_blobs:
                        with fitz.open(stream=blob, filetype="pdf") as part_doc:
                            if len(part_doc) > 0:
                                merged_doc.insert_pdf(part_doc)
                    if len(merged_doc) > 0:
                        merged_bytes = merged_doc.tobytes()
                        if len(merged_bytes) <= max_uncompressed_bytes:
                            return merged_bytes, "+".join(part_names), zip_entries
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    merged_doc.close()
                # Fall back to the first part if merge exceeds size or fails
                return part_blobs[0], part_names[0], zip_entries

            # Single chosen PDF: stream-read with hard byte cap to defeat spoofed ZipInfo.file_size headers
            raw_pdf = _read_zip_member_bounded(zf, chosen_info, remaining_bytes=max_uncompressed_bytes)
            return raw_pdf, chosen_info.filename, zip_entries
    except zipfile.BadZipFile as exc:
        raise ZipExtractionError(f"Corrupt ZIP archive: {exc}") from exc


def unwrap_pdf_payload(
    data: bytes,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_ZIP_BYTES,
    max_entries: int = DEFAULT_MAX_ZIP_ENTRIES,
) -> tuple[bytes, bool, str | None, list[str]]:
    """Unwrap a raw `.pdf` or `.zip`-disguised-as-`.pdf` payload into valid PDF bytes.

    Returns `(pdf_bytes, is_zip, chosen_entry, zip_entries)`.
    Raises `ZipExtractionError` on malicious/empty ZIPs.
    """
    if zipfile.is_zipfile(io.BytesIO(data)):
        pdf_bytes, chosen_entry, zip_entries = safe_extract_pdf_from_zip(
            data,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_entries=max_entries,
        )
        return pdf_bytes, True, chosen_entry, zip_entries
    return data, False, None, []


def detect_repeating_left_sidebar_x(pdf_source: bytes | pathlib.Path | str) -> float:
    """Zero-hardcode multi-page repeating-band sidebar detector using PyMuPDF.

    Returns x-cutoff right after persistent navigation sidebar on widescreen single-pages (e.g. FPT),
    and 0.0 on standard pages, 2-page A3 spreads with central gutter, or widescreen layouts where
    the left margin carries non-repeating body text (e.g. HPG).
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
        word_y_rels: dict[tuple[str, int], list[float]] = {}
        page_words_cache: list[tuple[float, float, list[Any]]] = []

        for idx in sample_indices:
            pg = doc[idx]
            w_p, h_p = float(pg.rect.width), float(pg.rect.height)
            words = pg.get_text("words")
            page_words_cache.append((w_p, h_p, words))
            mid = w_p / 2.0
            crossing = sum(1 for w in words if w[0] < mid - 4 and w[2] > mid + 4)
            if crossing <= 2 and len(words) > 20:
                gutter_hits += 1
            seen_on_pg: set[tuple[str, int]] = set()
            y_lo, y_hi = h_p * 0.12, h_p * 0.88
            for x0, y0, x1, _y1, txt, *_ in words:
                if x1 < w_p * 0.25 and y_lo < y0 < y_hi:
                    key = (str(txt).strip().lower(), round(float(x0) / 10.0))
                    if key not in seen_on_pg:
                        seen_on_pg.add(key)
                        word_page_counts[key] += 1
                        word_max_x1[key] = max(word_max_x1.get(key, 0.0), float(x1))
                        word_y_rels.setdefault(key, []).append(float(y0) / max(1.0, h_p))

        if gutter_hits >= len(sample_indices) * 0.6:
            return 0.0

        min_rep = max(3, int(len(sample_indices) * 0.25))
        strong_min_rep = max(3, int(len(sample_indices) * 0.45))
        rep_keys = {k for k, cnt in word_page_counts.items() if cnt >= min_rep}
        strong_keys = {k for k, cnt in word_page_counts.items() if cnt >= strong_min_rep}
        if len(rep_keys) < 3 or len(strong_keys) < 3:
            return 0.0

        # Require strongly repeating sidebar items to span >= 3 distinct vertical bands (>= 0.04*h apart)
        strong_y_means = sorted(sum(word_y_rels[k]) / len(word_y_rels[k]) for k in strong_keys)
        distinct_bands = [strong_y_means[0]]
        for y_val in strong_y_means[1:]:
            if y_val - distinct_bands[-1] >= 0.04:
                distinct_bands.append(y_val)
        if len(distinct_bands) < 3:
            return 0.0

        candidate_x = round(max(word_max_x1[k] for k in rep_keys) + 8.0, 1)

        # Body-text guard: reject if left strip carries substantial non-repeating body words
        total_left_words = 0
        nonrep_left_words = 0
        for _w_p, h_p, words in page_words_cache:
            y_lo, y_hi = h_p * 0.12, h_p * 0.88
            for x0, y0, _x1, _y1, txt, *_ in words:
                if float(x0) < candidate_x and y_lo < float(y0) < y_hi:
                    total_left_words += 1
                    key = (str(txt).strip().lower(), round(float(x0) / 10.0))
                    if key not in rep_keys:
                        nonrep_left_words += 1
        if total_left_words > 0 and (nonrep_left_words / total_left_words) >= 0.30:
            return 0.0

        return candidate_x
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


def _compute_page_image_coverage(page: fitz.Page) -> float:
    """Estimate fraction of page area covered by embedded raster images (scan detector signal)."""
    rect = page.rect
    page_area = float(rect.width) * float(rect.height)
    if page_area <= 0.0:
        return 0.0
    try:
        images = page.get_images(full=True)
    except Exception:  # noqa: BLE001
        return 0.0
    if not images:
        return 0.0
    covered = 0.0
    seen_xrefs: set[int] = set()
    for img in images:
        xref = int(img[0])
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            rects = page.get_image_rects(xref)
        except Exception:  # noqa: BLE001
            continue
        for ir in rects:
            inter = ir & rect
            if not inter.is_empty:
                covered += float(inter.width) * float(inter.height)
    return min(1.0, covered / page_area)


def _strip_watermark_and_repeating_lines(
    raw_text: str,
    repeating_watermark_keys: set[str],
) -> str:
    """Strip e-office watermarks, digital signature stamps, and >50%-page repeating boilerplate."""
    kept: list[str] = []
    for raw_ln in (raw_text or "").splitlines():
        s = raw_ln.strip()
        if not s:
            continue
        if _WATERMARK_OR_SIG_LINE_RE.search(s):
            continue
        norm_k = re.sub(r"\b\d{1,4}\b", "#", re.sub(r"\s+", " ", s).lower())
        if norm_k in repeating_watermark_keys:
            continue
        if re.fullmatch(r"[\d\s./\-:]+", s) and len(s) <= 12:
            continue
        kept.append(s)
    return "\n".join(kept).strip()


def inspect_pdf_bytes(
    data: bytes,
    source_url: str = "",
    *,
    doc_type: str | None = None,
) -> InspectionResult:
    """Full InputInspector: unpacks .zip safely, detects watermarks/scans, BAD_FONT, NOTICE_ONLY, spread."""
    try:
        data, is_zip, chosen_entry, zip_entries = unwrap_pdf_payload(data)
    except ZipExtractionError as exc:
        err_code = "ZIP_NO_PDF" if "no PDF" in str(exc) else f"ZIP_ERROR:{exc}"
        return InspectionResult(
            is_zip=True,
            zip_entries=[],
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
        if n_pages == 0:
            return InspectionResult(
                is_zip=is_zip,
                zip_entries=zip_entries,
                chosen_entry=chosen_entry,
                routed_branch="D",
                doc_class="ERROR",
                source_url=source_url,
                error="EMPTY_PDF(0 pages)",
            )

        # Pre-pass: gather raw text and detect lines repeating on > 50% of pages (e.g. e-office watermarks)
        raw_page_texts: list[str] = []
        page_img_cov: list[float] = []
        page_is_spread: list[bool] = []
        line_doc_freq: Counter[str] = Counter()

        for page in doc:
            is_spread = float(page.rect.width) > float(page.rect.height) * 1.25
            page_is_spread.append(is_spread)
            t = (page.get_text("text") or "").strip()
            raw_page_texts.append(t)
            page_img_cov.append(_compute_page_image_coverage(page))
            if n_pages >= 5 and t:
                seen_keys: set[str] = set()
                for raw_ln in t.splitlines():
                    s = raw_ln.strip()
                    if 4 <= len(s) <= 180:
                        k = re.sub(r"\b\d{1,4}\b", "#", re.sub(r"\s+", " ", s).lower())
                        if k not in seen_keys:
                            seen_keys.add(k)
                            line_doc_freq[k] += 1

        # Only strip lines repeating on > 50% of pages when short/watermark-like (avoid synthetic test prose)
        repeating_watermark_keys: set[str] = set()
        if n_pages >= 5:
            wm_thresh = max(4, int(n_pages * 0.50) + 1)
            for k, cnt in line_doc_freq.items():
                if cnt >= wm_thresh and (
                    _WATERMARK_OR_SIG_LINE_RE.search(k)
                    or "http://" in k
                    or "https://" in k
                    or "eoffice" in k
                    or "smartca" in k
                ):
                    repeating_watermark_keys.add(k)

        dig_clean = 0
        bad_font_pages = 0
        scan_pages = 0
        spread_pages = sum(1 for sp in page_is_spread if sp)
        scores: list[float] = []
        all_text_low: list[str] = []
        embedded_urls: list[str] = []
        page_triage: list[PageTriageInfo] = []
        bad_font_page_numbers: list[int] = []
        scan_page_numbers: list[int] = []
        digital_clean_page_numbers: list[int] = []

        for page_idx, raw_t in enumerate(raw_page_texts):
            page_no = page_idx + 1
            is_spread = page_is_spread[page_idx]
            img_cov = page_img_cov[page_idx]
            eff_t = _strip_watermark_and_repeating_lines(raw_t, repeating_watermark_keys)
            eff_chars = len(eff_t)

            if eff_chars < 80 or (img_cov >= 0.55 and eff_chars < 250):
                scan_pages += 1
                scan_page_numbers.append(page_no)
                page_triage.append(
                    PageTriageInfo(
                        page_no=page_no,
                        triage_class="SCAN",
                        char_count=eff_chars,
                        vn_readability_score=0.0,
                        is_spread=is_spread,
                    )
                )
            else:
                sc, _ = compute_vn_readability_score(eff_t)
                scores.append(sc)
                if eff_chars > 200 and sc < BAD_FONT_THRESHOLD:
                    bad_font_pages += 1
                    bad_font_page_numbers.append(page_no)
                    page_triage.append(
                        PageTriageInfo(
                            page_no=page_no,
                            triage_class="BAD_FONT",
                            char_count=eff_chars,
                            vn_readability_score=sc,
                            is_spread=is_spread,
                        )
                    )
                else:
                    dig_clean += 1
                    digital_clean_page_numbers.append(page_no)
                    all_text_low.append(eff_t.lower())
                    page_triage.append(
                        PageTriageInfo(
                            page_no=page_no,
                            triage_class="DIGITAL_CLEAN",
                            char_count=eff_chars,
                            vn_readability_score=sc,
                            is_spread=is_spread,
                        )
                    )
            if n_pages <= NOTICE_ONLY_MAX_PAGES:
                embedded_urls.extend(re.findall(r"https?://[^\s<>\"'\)\]]+", raw_t))
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
    has_fin_shield = bool(_FINANCIAL_SHIELD_INSPECT_RE.search(joined))
    norm_doc_type = (doc_type or "").strip().upper()
    is_short_doc_exempt = norm_doc_type in _SHORT_DOC_EXEMPT_TYPES

    if (
        n_pages <= NOTICE_ONLY_MAX_PAGES
        and not is_short_doc_exempt
        and not has_fin_shield
    ):
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


def inspect_pdf_bytes_detailed(
    data: bytes,
    source_url: str = "",
    *,
    doc_type: str | None = None,
) -> dict[str, Any]:
    """Dictionary-returning convenience wrapper compatible with Stage 1 prototype."""
    return inspect_pdf_bytes(data, source_url=source_url, doc_type=doc_type).to_dict()
