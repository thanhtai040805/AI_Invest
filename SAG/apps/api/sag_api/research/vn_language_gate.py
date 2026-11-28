"""Vietnamese-Only Language Gate, Per-Page/2-Column Bilingual Detector & Safe .zip/.rar Unpacker (0 LLM).

Enforces AIInvest's strict Vietnamese-filing policy (`vn-clean-v3`):
1. Rejects 100%-English documents (`EXCLUDED_FULL_ENGLISH_DOCUMENT`) at metadata, archive-member,
   digital PDF, and scanned-canary stages (fixing legacy `lo < 3` full-English pass-through).
2. Classifies every page independently (`VI_PAGE`, `EN_PAGE`, `BILINGUAL_2COL_PAGE`,
   `BILINGUAL_MIXED_PAGE`, `NUMERIC_NEUTRAL`, `EMPTY_PAGE`) so English translation pages are
   dropped whether they appear as a trailing block, middle block, or alternating even/odd pages.
3. Detects side-by-side 2-column bilingual pages (`Left = VI | Right = EN` or `Left = EN | Right = VI`)
   via PyMuPDF word coordinates and returns the exact Vietnamese crop bbox while leaving A3
   2-page Vietnamese spreads (`Left = VI | Right = VI`) untouched.
4. Safely unpacks both `.zip` and `.rar` archives (stream-bounded via stdlib `zipfile` / `bsdtar`
   with Zip-Slip & Archive-Bomb guards), rejecting English member PDFs and merging multi-file
   Vietnamese AGM proposal packages (`Nghị quyết` + `Tờ trình`) without mixing `SEPARATE` into `CONSOLIDATED`.
"""

from __future__ import annotations

import functools
import io
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

import fitz

PageLanguageClass = Literal[
    "VI_PAGE",
    "EN_PAGE",
    "BILINGUAL_2COL_PAGE",
    "BILINGUAL_MIXED_PAGE",
    "NUMERIC_NEUTRAL",
    "EMPTY_PAGE",
]
DocLanguageVerdict = Literal[
    "VIETNAMESE",
    "BILINGUAL_VI_EN",
    "FULL_ENGLISH",
    "UNDETERMINED_SCAN",
]

VN_DIACRITIC_CHARS = frozenset(
    "ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
    "ĂÂĐÊÔƠƯÀÁẢÃẠẰẮẲẴẶẦẤẨẪẬÈÉẺẼẸỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌỒỐỔỖỘỜỚỞỠỢÙÚỦŨỤỪỨỬỮỰỲÝỶỸỴ"
)
_VIET_CHAR_RE = re.compile(r"[ĂÂÊÔƠƯĐăâêôơưđÀ-ỹà-ỹ]")
_EN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{1,}\b")

VN_FILING_KEYWORDS: tuple[str, ...] = (
    "thuyết minh",
    "bảng cân đối",
    "báo cáo tài chính",
    "kết quả hoạt động",
    "lưu chuyển tiền tệ",
    "vốn chủ sở hữu",
    "tài sản ngắn hạn",
    "tài sản dài hạn",
    "nợ phải trả",
    "doanh thu thuần",
    "lợi nhuận sau thuế",
    "tổng cộng tài sản",
    "tổng cộng nguồn vốn",
    "số cuối năm",
    "số đầu năm",
    "số cuối kỳ",
    "số đầu kỳ",
    "đơn vị tính",
    "tỷ đồng",
    "triệu đồng",
    "cổ đông",
    "hội đồng quản trị",
    "ban kiểm soát",
    "ban tổng giám đốc",
    "nghị quyết",
    "tờ trình",
    "đại hội đồng",
    "kiểm toán độc lập",
)

VN_TABLE_HEADER_CUES: tuple[str, ...] = (
    "chỉ tiêu",
    "mã số",
    "thuyết minh",
    "số cuối",
    "số đầu",
    "tổng cộng",
    "cộng",
    "đơn vị tính",
    "triệu đồng",
    "tỷ đồng",
    "chi tieu",
    "ma so",
    "tong cong",
)

VN_UNACCENTED_TEXT_CUES: tuple[str, ...] = (
    "bao cao",
    "doanh thu",
    "loi nhuan",
    "tai chinh",
    "co dong",
    "thuyet minh",
    "kiem toan",
    "nghi quyet",
    "to trinh",
    "ke hoach",
    "tong cong",
    "von chu",
    "hop nhat",
    "tai san",
)

EN_FIN_AND_GRAMMAR_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "from",
        "with",
        "that",
        "this",
        "these",
        "those",
        "which",
        "under",
        "during",
        "between",
        "within",
        "including",
        "accordance",
        "pursuant",
        "hereby",
        "thereof",
        "consolidated",
        "separate",
        "financial",
        "statement",
        "statements",
        "position",
        "balance",
        "sheet",
        "income",
        "operations",
        "operating",
        "cash",
        "flows",
        "flow",
        "changes",
        "equity",
        "owners",
        "assets",
        "current",
        "non-current",
        "liabilities",
        "receivables",
        "inventories",
        "payables",
        "borrowings",
        "provisions",
        "depreciation",
        "amortisation",
        "amortization",
        "revenue",
        "revenues",
        "sales",
        "goods",
        "services",
        "rendering",
        "cost",
        "gross",
        "profit",
        "loss",
        "expenses",
        "selling",
        "administrative",
        "corporate",
        "tax",
        "deferred",
        "earnings",
        "share",
        "shares",
        "shareholders",
        "shareholder",
        "general",
        "meeting",
        "annual",
        "report",
        "resolution",
        "board",
        "directors",
        "management",
        "supervisory",
        "committee",
        "audit",
        "auditors",
        "auditor",
        "independent",
        "notes",
        "closing",
        "opening",
        "ended",
        "december",
        "march",
        "june",
        "september",
        "million",
        "billion",
        "thousand",
        "total",
        "company",
        "corporation",
        "joint",
        "stock",
        "enterprise",
        "business",
        "results",
        "explanation",
        "disclosure",
        "information",
        "commission",
        "securities",
        "exchange",
    }
)

EN_TABLE_HEADER_CUES: tuple[str, ...] = (
    "closing balance",
    "opening balance",
    "as at 31",
    "as at 30",
    "for the year ended",
    "for the period ended",
    "code",
    "items",
    "notes",
    "current year",
    "prior year",
    "previous year",
)

_BILINGUAL_META_CUE_RE = re.compile(
    r"(?:song[\s_\-]*ngữ|song[\s_\-]*ngu|bilingual|việt[\s_\-]*anh|viet[\s_\-]*anh|"
    r"\bvi[\s_\-]+en\b|\bvie[\s_\-]+eng\b|\bvn[\s_\-]+en\b|tiếng\s+việt|tieng\s+viet)",
    re.IGNORECASE,
)
_VN_UNACCENTED_META_CUE_RE = re.compile(
    r"(?:\bbctc\b|\bbctn\b|bao[\s_\-]*cao|nghi[\s_\-]*quyet|to[\s_\-]*trinh|"
    r"tai[\s_\-]*lieu|dhdcd|dai[\s_\-]*hoi|giai[\s_\-]*trinh|hop[\s_\-]*nhat|"
    r"cong[\s_\-]*ty[\s_\-]*me|rieng[\s_\-]*le|kiem[\s_\-]*toan|soat[\s_\-]*xet|"
    r"ban[\s_\-]*cao[\s_\-]*bach|co[\s_\-]*tuc|bien[\s_\-]*ban|thuong[\s_\-]*nien|"
    r"thuyet[\s_\-]*minh)",
    re.IGNORECASE,
)
_ARCHIVE_EN_FILING_PHRASE_RE = re.compile(
    r"(?:\bannual\s+reports?\b|\bfinancial\s+statements?\b|"
    r"\b(?:consolidated|separate|interim|audited|quarterly)\s+financial\b|"
    r"\bsustainability\s+reports?\b|\bcorporate\s+governance\s+reports?\b|"
    r"\b(?:agm|egm)\b|\bgeneral\s+meeting(?:\s+of\s+shareholders)?\b|"
    r"\b(?:resolution|minutes)\s+(?:of|and)\b|"
    r"\b(?:resolution|minutes)\b(?=.*\b(?:shareholders?|meeting|20\d{2})\b)|"
    r"\binformation\s+disclosure\b|\bexplanation\s+(?:of|for|on)\b|"
    r"\benglish\s+version\b)",
    re.IGNORECASE,
)
_EXPLICIT_EN_FILE_SUFFIX_RE = re.compile(
    r"(?:^|[_\-\s./])(?:en|eng|english)(?:[_\-\s]*(?:ver|version|final|signed|\d{1,4}))?\.pdf$",
    re.IGNORECASE,
)
_EXPLICIT_VN_FILE_SUFFIX_RE = re.compile(
    r"(?:^|[_\-\s./])(?:vn|vi|vie|viet|tiengviet)(?:[_\-\s]*(?:ver|version|final|signed|\d{1,4}))?\.pdf$",
    re.IGNORECASE,
)
_STANDALONE_EN_TITLE_RE = re.compile(
    r"^(?:[\[\(A-Z0-9_\-\s:/.]{0,24})?"
    r"(?:consolidated\s+financial\s+statements?|separate\s+financial\s+statements?|"
    r"interim\s+financial\s+statements?|audited\s+financial\s+statements?|"
    r"quarterly\s+financial\s+statements?|financial\s+statements?\b|"
    r"annual\s+report\b|sustainability\s+report\b|"
    r"resolution\s+of\s+(?:the\s+)?(?:annual\s+|extraordinary\s+)?general\s+meeting|"
    r"documents?\s+for\s+(?:the\s+)?(?:annual\s+)?general\s+meeting|"
    r"notice\s+of\s+(?:the\s+)?(?:annual\s+)?general\s+meeting|"
    r"explanation\s+(?:of|for|on)\s+(?:business|financial|profit)\b|"
    r"information\s+disclosure\b|corporate\s+governance\s+report\b)",
    re.IGNORECASE,
)

_PROCEDURAL_ARCHIVE_MEMBER_RE = re.compile(
    r"(?:kiem[\s_\-]*phieu|kiểm\s+phiếu|the[\s_\-]*le[\s_\-]*bieu[\s_\-]*quyet|"
    r"thể\s+lệ\s+biểu\s+quyết|phieu[\s_\-]*bieu[\s_\-]*quyet|phiếu\s+biểu\s+quyết|"
    r"giay[\s_\-]*uy[\s_\-]*quyen|giấy\s+ủy\s+quyền|chuong[\s_\-]*trinh[\s_\-]*dai[\s_\-]*hoi|"
    r"chương\s+trình\s+đại\s+hội|thu[\s_\-]*moi[\s_\-]*hop|thư\s+mời\s+họp|"
    r"quy[\s_\-]*che[\s_\-]*to[\s_\-]*chuc|quy\s+chế\s+tổ\s+chức)",
    re.IGNORECASE,
)
_ARCHIVE_PART_RE = re.compile(
    r"^(?P<stem>.+?)[_\-. ]+(?:p|part|phan)(?P<part>\d{1,2})\.pdf$",
    re.IGNORECASE,
)
_RAR_MAGIC_V4 = b"Rar!\x1a\x07\x00"
_RAR_MAGIC_V5 = b"Rar!\x1a\x07\x01\x00"

DEFAULT_MAX_ARCHIVE_BYTES = 350 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_ENTRIES = 64

ArchiveMemberLangClass = Literal["BILINGUAL", "EXPLICIT_VI", "EXPLICIT_EN", "NEUTRAL"]


class ArchiveExtractionError(ValueError):
    """Raised when an archive (.zip/.rar) is invalid, unsafe, empty, or 100% English."""


@dataclass(frozen=True, slots=True)
class PageLanguageInfo:
    page_no: int
    lang_class: PageLanguageClass
    alpha_chars: int
    vn_diacritics: int
    vn_ratio: float
    vn_kw_hits: int
    en_kw_hits: int
    vietnamese_crop_x: tuple[float, float] | None = None


@dataclass(slots=True)
class DocumentLanguageAssessment:
    verdict: DocLanguageVerdict
    vi_pages: int = 0
    en_pages: int = 0
    bilingual_2col_pages: int = 0
    bilingual_mixed_pages: int = 0
    numeric_neutral_pages: int = 0
    empty_or_scan_pages: int = 0
    english_pages_to_drop: list[int] = field(default_factory=list)
    two_col_crops: dict[int, tuple[float, float]] = field(default_factory=dict)
    page_details: list[PageLanguageInfo] = field(default_factory=list)


def is_rar_bytes(data: bytes) -> bool:
    """True when `data` begins with RAR v4 or RAR v5 magic header bytes."""
    return data.startswith(_RAR_MAGIC_V4) or data.startswith(_RAR_MAGIC_V5)


def is_standalone_english_metadata(
    title: str,
    url: str = "",
    filename: str = "",
) -> tuple[bool, str]:
    """Layer-1 zero-download check: reject ONLY unambiguous standalone English filings.

    Never rejects bilingual filings (`Song ngữ`, `VI-EN`) or Core DB synthetic titles
    such as `BCTC CONSOLIDATED FPT FY2025` unless the filename explicitly marks `_EN.pdf`.
    """
    norm_title = unicodedata.normalize("NFC", (title or "").strip())
    url_base = (url or "").split("?", 1)[0].rsplit("/", 1)[-1]
    fname = unicodedata.normalize("NFC", (filename or url_base or "").strip())
    combined = f"{norm_title} {fname}"

    if _BILINGUAL_META_CUE_RE.search(combined):
        return False, ""
    if _EXPLICIT_VN_FILE_SUFFIX_RE.search(fname):
        return False, ""

    # Explicit `_EN.pdf` / `_ENG.pdf` / `_English.pdf` filename suffix without bilingual/VN suffix
    if fname and _EXPLICIT_EN_FILE_SUFFIX_RE.search(fname) and not _VIET_CHAR_RE.search(fname):
        return True, f"EXCLUDED_ENGLISH_FILENAME:{fname}"

    # Pure English title with zero Vietnamese diacritics and zero VN filing acronyms (`BCTC`, `BCTN`, ...)
    if (
        norm_title
        and not _VIET_CHAR_RE.search(norm_title)
        and not _VN_UNACCENTED_META_CUE_RE.search(norm_title)
        and _STANDALONE_EN_TITLE_RE.search(norm_title)
    ):
        return True, f"EXCLUDED_ENGLISH_TITLE:{norm_title[:80]}"

    return False, ""


def classify_text_language(text: str) -> PageLanguageInfo:
    """Classify page text into `VI_PAGE`, `EN_PAGE`, `BILINGUAL_MIXED_PAGE`, `NUMERIC_NEUTRAL`, or `EMPTY_PAGE`."""
    nfc = unicodedata.normalize("NFC", text or "").strip()
    if len(nfc) < 12:
        return PageLanguageInfo(0, "EMPTY_PAGE", 0, 0, 0.0, 0, 0)

    alpha_chars = [c for c in nfc if c.isalpha()]
    n_alpha = len(alpha_chars)
    vn_diacritics = sum(1 for c in alpha_chars if c in VN_DIACRITIC_CHARS)
    vn_ratio = round(vn_diacritics / max(1, n_alpha), 4)
    low = nfc.lower()
    vn_kw_hits = sum(1 for kw in VN_FILING_KEYWORDS if kw in low) + sum(
        1 for kw in VN_UNACCENTED_TEXT_CUES if kw in low
    )
    en_tokens = [tok.lower() for tok in _EN_WORD_RE.findall(nfc)]
    en_kw_hits = sum(1 for tok in en_tokens if tok in EN_FIN_AND_GRAMMAR_WORDS)

    if n_alpha < 35:
        if vn_diacritics >= 2 or vn_kw_hits >= 1 or any(h in low for h in VN_TABLE_HEADER_CUES):
            return PageLanguageInfo(0, "VI_PAGE", n_alpha, vn_diacritics, vn_ratio, vn_kw_hits, en_kw_hits)
        if vn_diacritics == 0 and (en_kw_hits >= 2 or any(h in low for h in EN_TABLE_HEADER_CUES)):
            return PageLanguageInfo(0, "EN_PAGE", n_alpha, vn_diacritics, vn_ratio, vn_kw_hits, en_kw_hits)
        has_digits = sum(1 for c in nfc if c.isdigit()) >= 6
        return PageLanguageInfo(
            0,
            "NUMERIC_NEUTRAL" if has_digits else "EMPTY_PAGE",
            n_alpha,
            vn_diacritics,
            vn_ratio,
            vn_kw_hits,
            en_kw_hits,
        )

    # Pure English page (allowing <= 0.35% stray OCR accent artifact when VN keywords == 0 and EN hits >= 6)
    if vn_kw_hits == 0 and (
        (vn_diacritics == 0 and (en_kw_hits >= 3 or (en_kw_hits >= 2 and len(en_tokens) >= 8)))
        or (vn_ratio < 0.0035 and en_kw_hits >= 6 and len(en_tokens) >= 20)
    ):
        return PageLanguageInfo(0, "EN_PAGE", n_alpha, vn_diacritics, vn_ratio, vn_kw_hits, en_kw_hits)

    if vn_ratio >= 0.015 or vn_diacritics >= 4 or vn_kw_hits >= 1:
        # Check if interleaved English mirror lines also exist on this Vietnamese page
        en_only_lines = 0
        for raw_ln in nfc.splitlines():
            s = raw_ln.strip()
            if len(s) < 12 or _VIET_CHAR_RE.search(s):
                continue
            ln_toks = [t.lower() for t in _EN_WORD_RE.findall(s)]
            if len(ln_toks) >= 3 and sum(1 for t in ln_toks if t in EN_FIN_AND_GRAMMAR_WORDS) >= 2:
                en_only_lines += 1
        cls: PageLanguageClass = "BILINGUAL_MIXED_PAGE" if en_only_lines >= 2 else "VI_PAGE"
        return PageLanguageInfo(0, cls, n_alpha, vn_diacritics, vn_ratio, vn_kw_hits, en_kw_hits)

    return PageLanguageInfo(0, "NUMERIC_NEUTRAL", n_alpha, vn_diacritics, vn_ratio, vn_kw_hits, en_kw_hits)


def detect_bilingual_two_column_crop(
    page: fitz.Page,
    *,
    left_sidebar_x: float = 0.0,
) -> tuple[float, float] | None:
    """Detect side-by-side 2-column bilingual layout (`Left=VI | Right=EN` or `Left=EN | Right=VI`).

    Returns `(keep_x0, keep_x1)` in page points for the Vietnamese column, or `None` when the page
    is single-column or a 2-page Vietnamese A3 spread (`Left=VI | Right=VI`).
    """
    rect = page.rect
    w, h = float(rect.width), float(rect.height)
    if w <= 120.0 or h <= 120.0:
        return None
    eff_left = left_sidebar_x if (w > h * 1.25 and 0.0 < left_sidebar_x < w * 0.30) else 0.0
    usable_w = w - eff_left
    if usable_w < 200.0:
        return None

    words = page.get_text("words")
    y_lo, y_hi = h * 0.10, h * 0.90
    body_words = [
        wd
        for wd in words
        if float(wd[0]) >= eff_left - 2.0 and y_lo <= float(wd[1]) <= y_hi and str(wd[4]).strip()
    ]
    if len(body_words) < 30:
        return None

    # Probe candidate vertical gutters across [38%..62%] of usable width
    best_split: float | None = None
    best_cross: int = 999999
    mid_target = eff_left + usable_w * 0.50
    for pct in (0.50, 0.48, 0.52, 0.45, 0.55, 0.42, 0.58, 0.38, 0.62):
        cand_x = eff_left + usable_w * pct
        crossing = sum(
            1 for wd in body_words if float(wd[0]) < cand_x - 3.0 and float(wd[2]) > cand_x + 3.0
        )
        is_closer_tie = (
            crossing == best_cross
            and best_split is not None
            and abs(cand_x - mid_target) < abs(best_split - mid_target)
        )
        if crossing < best_cross or is_closer_tie:
            best_cross = crossing
            best_split = cand_x

    if best_split is None or best_cross > max(2, int(len(body_words) * 0.025)):
        return None

    left_words = [wd for wd in body_words if float(wd[2]) <= best_split + 3.0]
    right_words = [wd for wd in body_words if float(wd[0]) >= best_split - 3.0]
    if len(left_words) < 12 or len(right_words) < 12:
        return None

    # Require substantial vertical overlap so top-VN / bottom-EN is not mistaken for 2 columns
    l_y0, l_y1 = min(float(wd[1]) for wd in left_words), max(float(wd[3]) for wd in left_words)
    r_y0, r_y1 = min(float(wd[1]) for wd in right_words), max(float(wd[3]) for wd in right_words)
    inter_h = max(0.0, min(l_y1, r_y1) - max(l_y0, r_y0))
    min_span = max(1.0, min(l_y1 - l_y0, r_y1 - r_y0))
    if inter_h / min_span < 0.45:
        return None

    left_txt = " ".join(str(wd[4]) for wd in left_words)
    right_txt = " ".join(str(wd[4]) for wd in right_words)
    l_info = classify_text_language(left_txt)
    r_info = classify_text_language(right_txt)

    l_is_vi = l_info.lang_class in ("VI_PAGE", "BILINGUAL_MIXED_PAGE") and l_info.vn_diacritics >= 4
    r_is_vi = r_info.lang_class in ("VI_PAGE", "BILINGUAL_MIXED_PAGE") and r_info.vn_diacritics >= 4
    l_is_en = l_info.lang_class == "EN_PAGE"
    r_is_en = r_info.lang_class == "EN_PAGE"

    if l_is_vi and r_is_en:
        return (round(eff_left, 1), round(best_split, 1))
    if r_is_vi and l_is_en:
        return (round(best_split, 1), round(w, 1))
    return None


def resolve_english_pages_to_drop(
    page_classes: dict[int, PageLanguageClass],
    page_texts: dict[int, str] | None = None,
) -> tuple[DocLanguageVerdict, list[int]]:
    """Determine overall document language verdict and exact 1-based English pages to drop.

    Handles:
    - 100% Full-English documents (`verdict == "FULL_ENGLISH"`, all pages dropped)
    - Tail half-English translations (e.g. p.1..100 VI + p.101..200 EN)
    - Alternating page-by-page translations (p.1 VI, p.2 EN, p.3 VI, p.4 EN, ...)
    - Middle English sections followed by a Vietnamese appendix
    - Continuation `NUMERIC_NEUTRAL` pages belonging to an `EN_PAGE` financial statement block
    """
    vi_pages = [
        p
        for p, c in sorted(page_classes.items())
        if c in ("VI_PAGE", "BILINGUAL_2COL_PAGE", "BILINGUAL_MIXED_PAGE")
    ]
    en_pages = [p for p, c in sorted(page_classes.items()) if c == "EN_PAGE"]

    if not vi_pages and en_pages:
        return "FULL_ENGLISH", sorted(en_pages)
    if not vi_pages and not en_pages:
        return "UNDETERMINED_SCAN", []
    if not en_pages:
        has_bilingual = any(
            c in ("BILINGUAL_2COL_PAGE", "BILINGUAL_MIXED_PAGE") for c in page_classes.values()
        )
        return ("BILINGUAL_VI_EN" if has_bilingual else "VIETNAMESE"), []

    dropped: set[int] = set(en_pages)
    sticky_lang: Literal["VI", "EN"] = "VI"
    for pno in sorted(page_classes):
        cls = page_classes[pno]
        if cls in ("VI_PAGE", "BILINGUAL_2COL_PAGE", "BILINGUAL_MIXED_PAGE"):
            sticky_lang = "VI"
        elif cls == "EN_PAGE":
            sticky_lang = "EN"
            dropped.add(pno)
        elif cls == "NUMERIC_NEUTRAL" and sticky_lang == "EN":
            raw_t = ((page_texts or {}).get(pno) or "").lower()
            has_vn_hdr = bool(_VIET_CHAR_RE.search(raw_t)) or any(k in raw_t for k in VN_TABLE_HEADER_CUES)
            if not has_vn_hdr:
                dropped.add(pno)
            else:
                sticky_lang = "VI"

    return "BILINGUAL_VI_EN", sorted(dropped)


def assess_pdf_language(
    pdf_bytes: bytes,
    *,
    left_sidebar_x: float = 0.0,
) -> DocumentLanguageAssessment:
    """Assess every page of a PDF (0 OCR cost) for full-English rejection, per-page EN drop & 2-col crop."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_classes: dict[int, PageLanguageClass] = {}
        page_texts: dict[int, str] = {}
        two_col_crops: dict[int, tuple[float, float]] = {}
        details: list[PageLanguageInfo] = []

        for idx, page in enumerate(doc):
            pno = idx + 1
            raw_t = (page.get_text("text") or "").strip()
            page_texts[pno] = raw_t
            crop = detect_bilingual_two_column_crop(page, left_sidebar_x=left_sidebar_x)
            if crop is not None:
                two_col_crops[pno] = crop
                info = classify_text_language(raw_t)
                p_info = PageLanguageInfo(
                    page_no=pno,
                    lang_class="BILINGUAL_2COL_PAGE",
                    alpha_chars=info.alpha_chars,
                    vn_diacritics=info.vn_diacritics,
                    vn_ratio=info.vn_ratio,
                    vn_kw_hits=info.vn_kw_hits,
                    en_kw_hits=info.en_kw_hits,
                    vietnamese_crop_x=crop,
                )
            else:
                info = classify_text_language(raw_t)
                p_info = PageLanguageInfo(
                    page_no=pno,
                    lang_class=info.lang_class,
                    alpha_chars=info.alpha_chars,
                    vn_diacritics=info.vn_diacritics,
                    vn_ratio=info.vn_ratio,
                    vn_kw_hits=info.vn_kw_hits,
                    en_kw_hits=info.en_kw_hits,
                    vietnamese_crop_x=None,
                )
            page_classes[pno] = p_info.lang_class
            details.append(p_info)

        verdict, en_to_drop = resolve_english_pages_to_drop(page_classes, page_texts)
        return DocumentLanguageAssessment(
            verdict=verdict,
            vi_pages=sum(1 for d in details if d.lang_class == "VI_PAGE"),
            en_pages=sum(1 for d in details if d.lang_class == "EN_PAGE"),
            bilingual_2col_pages=sum(1 for d in details if d.lang_class == "BILINGUAL_2COL_PAGE"),
            bilingual_mixed_pages=sum(1 for d in details if d.lang_class == "BILINGUAL_MIXED_PAGE"),
            numeric_neutral_pages=sum(1 for d in details if d.lang_class == "NUMERIC_NEUTRAL"),
            empty_or_scan_pages=sum(1 for d in details if d.lang_class == "EMPTY_PAGE"),
            english_pages_to_drop=en_to_drop,
            two_col_crops=two_col_crops,
            page_details=details,
        )
    finally:
        doc.close()


def _validate_archive_member_name(filename: str) -> None:
    """Reject Zip-Slip / RAR-Slip path traversal attempts."""
    if not filename or "\x00" in filename:
        raise ArchiveExtractionError("Invalid empty or null-byte archive member name")
    normalized = filename.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ArchiveExtractionError(f"Unsafe absolute archive member path: {filename}")
    parts = [p for p in normalized.split("/") if p]
    if any(part == ".." for part in parts):
        raise ArchiveExtractionError(f"Archive path traversal detected: {filename}")
    cleaned = posixpath.normpath(normalized)
    if cleaned.startswith("../") or cleaned == "..":
        raise ArchiveExtractionError(f"Normalized archive path traversal detected: {filename}")


@functools.lru_cache(maxsize=1)
def _resolve_7z_binary() -> str | None:
    """Locate full `7z` / `7zz` binary (supporting RAR5 + UTF-8; excludes `7zr` which is .7z-only)."""
    candidates: list[str] = []
    for name in ("7z", "7zz"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    local_app = os.environ.get("LOCALAPPDATA", "")
    for path in (
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
        os.path.join(local_app, "Programs", "7-Zip", "7z.exe") if local_app else "",
    ):
        if path and os.path.isfile(path) and path not in candidates:
            candidates.append(path)
    for binary in candidates:
        try:
            proc = subprocess.run(
                [binary, "i"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            info = f"{proc.stdout} {proc.stderr}".lower()
            if "rar5" in info or "rar" in info:
                return binary
        except Exception:  # noqa: BLE001
            continue
    return None


@functools.lru_cache(maxsize=1)
def _resolve_unrar_binary() -> str | None:
    """Locate `unrar` / `UnRAR` binary capable of reading RAR4/RAR5 archives with UTF-8 filenames."""
    candidates: list[str] = []
    for name in ("unrar", "UnRAR"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    for path in (
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
    ):
        if os.path.isfile(path) and path not in candidates:
            candidates.append(path)
    for binary in candidates:
        try:
            proc = subprocess.run(
                [binary],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ver = f"{proc.stdout} {proc.stderr}".lower()
            if "unrar" in ver or "rarlab" in ver:
                return binary
        except Exception:  # noqa: BLE001
            continue
    return None


@functools.lru_cache(maxsize=1)
def _resolve_bsdtar_binary() -> str | None:
    """Locate `bsdtar` (or Windows/libarchive `tar`) capable of reading `.rar` archives safely."""
    candidates: list[str] = []
    for name in ("bsdtar", "tar"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    win_tar = r"C:\Windows\System32\tar.exe"
    if os.path.isfile(win_tar) and win_tar not in candidates:
        candidates.append(win_tar)

    for binary in candidates:
        try:
            proc = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            ver = f"{proc.stdout} {proc.stderr}".lower()
            if "bsdtar" in ver or "libarchive" in ver:
                return binary
        except Exception:  # noqa: BLE001
            continue
    return None


def _stream_subprocess_bounded(
    cmd: list[str],
    *,
    member_name: str,
    remaining_bytes: int,
    timeout_seconds: int = 60,
    backend_label: str = "RAR",
) -> bytes:
    """Run a streaming extraction subprocess with strict uncompressed-byte and wall-clock caps."""
    ext_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    buf = io.BytesIO()
    read_n = 0
    try:
        assert ext_proc.stdout is not None
        while True:
            chunk = ext_proc.stdout.read(65536)
            if not chunk:
                break
            read_n += len(chunk)
            if read_n > remaining_bytes:
                ext_proc.kill()
                raise ArchiveExtractionError(
                    f"Uncompressed RAR member {member_name} exceeds remaining budget {remaining_bytes}"
                )
            buf.write(chunk)
        ret = ext_proc.wait(timeout=timeout_seconds)
        if ret != 0:
            stderr_txt = (
                ext_proc.stderr.read().decode("utf-8", errors="replace").strip()
                if ext_proc.stderr
                else ""
            )
            raise ArchiveExtractionError(
                f"{backend_label} extraction failed for {member_name}: {stderr_txt or ret}"
            )
    finally:
        if ext_proc.poll() is None:
            ext_proc.kill()
    return buf.getvalue()


def _read_rar_pdf_members_via_7z(
    seven_z: str,
    rar_path: str,
    *,
    max_uncompressed_bytes: int,
    max_entries: int,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    list_proc = subprocess.run(
        [seven_z, "l", "-slt", "-sccUTF-8", "-ba", rar_path],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if list_proc.returncode != 0:
        err_msg = list_proc.stderr.decode("utf-8", errors="replace").strip()
        out_msg = list_proc.stdout.decode("utf-8", errors="replace").strip()
        raise ArchiveExtractionError(f"Corrupt RAR archive: {err_msg or out_msg or list_proc.returncode}")

    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_ln in list_proc.stdout.decode("utf-8", errors="replace").splitlines():
        ln = raw_ln.strip()
        if not ln:
            if current.get("Path"):
                entries.append(current)
            current = {}
            continue
        if " = " in ln:
            k, v = ln.split(" = ", 1)
            current[k.strip()] = v.strip()
    if current.get("Path"):
        entries.append(current)

    if not entries:
        raise ArchiveExtractionError("Corrupt RAR archive: contains no readable entries")
    if len(entries) > max_entries:
        raise ArchiveExtractionError(
            f"RAR entry count {len(entries)} exceeds limit {max_entries}"
        )

    pdf_entries: list[tuple[str, int]] = []
    total_declared = 0
    for ent in entries:
        name = ent["Path"]
        _validate_archive_member_name(name)
        is_folder = ent.get("Folder") == "+" or name.endswith("/") or name.endswith("\\")
        if is_folder or name.startswith("__MACOSX"):
            continue
        try:
            decl_sz = int(ent.get("Size") or 0)
        except ValueError:
            decl_sz = 0
        if decl_sz < 0 or decl_sz > max_uncompressed_bytes:
            raise ArchiveExtractionError(
                f"RAR member {name} size {decl_sz} exceeds limit {max_uncompressed_bytes}"
            )
        total_declared += decl_sz
        if total_declared > max_uncompressed_bytes:
            raise ArchiveExtractionError(
                f"Total RAR uncompressed size exceeds limit {max_uncompressed_bytes}"
            )
        if name.lower().endswith(".pdf"):
            pdf_entries.append((name, decl_sz))

    if not pdf_entries:
        raise ArchiveExtractionError("RAR archive contains no PDF files")

    remaining = max_uncompressed_bytes
    extracted: list[tuple[str, bytes]] = []
    entries_desc: list[str] = []
    for name, _decl_sz in pdf_entries:
        # Use explicit UTF-8 listfile so special characters (`@`, `*`, Vietnamese diacritics) never break argv
        listfile = f"{rar_path}.member.lst"
        with open(listfile, "w", encoding="utf-8", newline="\n") as lfh:
            lfh.write(name + "\n")
        try:
            blob = _stream_subprocess_bounded(
                [seven_z, "x", "-so", "-y", "-sccUTF-8", "-scsUTF-8", rar_path, f"@{listfile}"],
                member_name=name,
                remaining_bytes=remaining,
                timeout_seconds=60,
                backend_label="7z RAR",
            )
        finally:
            try:
                os.remove(listfile)
            except OSError:
                pass
        remaining -= len(blob)
        extracted.append((name, blob))
        entries_desc.append(f"{name} ({len(blob) // 1024}KB)")

    return extracted, entries_desc


def _read_rar_pdf_members_via_unrar(
    unrar: str,
    rar_path: str,
    *,
    max_uncompressed_bytes: int,
    max_entries: int,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    list_proc = subprocess.run(
        [unrar, "lb", "-scu", rar_path],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if list_proc.returncode != 0:
        err_msg = list_proc.stderr.decode("utf-8", errors="replace").strip()
        raise ArchiveExtractionError(f"Corrupt RAR archive: {err_msg or list_proc.returncode}")

    raw_names = [
        ln.strip()
        for ln in list_proc.stdout.decode("utf-8", errors="replace").splitlines()
        if ln.strip()
    ]
    if not raw_names:
        raise ArchiveExtractionError("Corrupt RAR archive: contains no readable entries")
    if len(raw_names) > max_entries:
        raise ArchiveExtractionError(
            f"RAR entry count {len(raw_names)} exceeds limit {max_entries}"
        )

    pdf_names: list[str] = []
    for name in raw_names:
        _validate_archive_member_name(name)
        if name.endswith("/") or name.endswith("\\") or name.startswith("__MACOSX"):
            continue
        if name.lower().endswith(".pdf"):
            pdf_names.append(name)

    if not pdf_names:
        raise ArchiveExtractionError("RAR archive contains no PDF files")

    remaining = max_uncompressed_bytes
    extracted: list[tuple[str, bytes]] = []
    entries_desc: list[str] = []
    for name in pdf_names:
        blob = _stream_subprocess_bounded(
            [unrar, "p", "-inul", rar_path, name],
            member_name=name,
            remaining_bytes=remaining,
            timeout_seconds=60,
            backend_label="unrar RAR",
        )
        remaining -= len(blob)
        extracted.append((name, blob))
        entries_desc.append(f"{name} ({len(blob) // 1024}KB)")

    return extracted, entries_desc


def _read_rar_pdf_members_bounded(
    data: bytes,
    *,
    max_uncompressed_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES,
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Stream-extract `.pdf` members from a `.rar` archive into memory without writing extracted files to disk.

    Multi-backend chain (RAR5 + UTF-8 Vietnamese safe): `7z` -> `unrar` -> `bsdtar`.
    """
    if len(data) > max_uncompressed_bytes:
        raise ArchiveExtractionError(f"RAR payload size {len(data)} exceeds limit {max_uncompressed_bytes}")

    seven_z = _resolve_7z_binary()
    unrar = _resolve_unrar_binary()
    bsdtar = _resolve_bsdtar_binary()
    if seven_z is None and unrar is None and bsdtar is None:
        raise ArchiveExtractionError(
            "RAR archive detected but no unpacker (`7z`, `unrar`, or `bsdtar`) is available on host."
        )

    with tempfile.TemporaryDirectory(prefix="sag_rar_inspect_") as tmp_dir:
        rar_path = os.path.join(tmp_dir, "payload.rar")
        with open(rar_path, "wb") as fh:
            fh.write(data)

        if seven_z is not None:
            return _read_rar_pdf_members_via_7z(
                seven_z,
                rar_path,
                max_uncompressed_bytes=max_uncompressed_bytes,
                max_entries=max_entries,
            )

        if unrar is not None:
            return _read_rar_pdf_members_via_unrar(
                unrar,
                rar_path,
                max_uncompressed_bytes=max_uncompressed_bytes,
                max_entries=max_entries,
            )

        assert bsdtar is not None
        list_proc = subprocess.run(
            [bsdtar, "-tf", rar_path],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if list_proc.returncode != 0:
            err_msg = list_proc.stderr.decode("utf-8", errors="replace").strip()
            if "empty or unreadable filename" in err_msg.lower():
                raise ArchiveExtractionError(
                    "RAR_UNPACKER_ENCODING_UNSUPPORTED: host bsdtar/tar.exe cannot decode "
                    "non-ASCII RAR5 filenames; install 7-Zip (`7z`) or `unrar`."
                )
            raise ArchiveExtractionError(f"Corrupt RAR archive: {err_msg or list_proc.returncode}")

        raw_names = [
            ln.strip()
            for ln in list_proc.stdout.decode("utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
        if not raw_names:
            raise ArchiveExtractionError("Corrupt RAR archive: contains no readable entries")
        if len(raw_names) > max_entries:
            raise ArchiveExtractionError(
                f"RAR entry count {len(raw_names)} exceeds limit {max_entries}"
            )

        pdf_names: list[str] = []
        for name in raw_names:
            _validate_archive_member_name(name)
            if name.endswith("/") or name.startswith("__MACOSX"):
                continue
            if name.lower().endswith(".pdf"):
                pdf_names.append(name)

        if not pdf_names:
            raise ArchiveExtractionError("RAR archive contains no PDF files")

        remaining = max_uncompressed_bytes
        extracted: list[tuple[str, bytes]] = []
        entries_desc: list[str] = []
        for name in pdf_names:
            blob = _stream_subprocess_bounded(
                [bsdtar, "-xOf", rar_path, name],
                member_name=name,
                remaining_bytes=remaining,
                timeout_seconds=60,
                backend_label="bsdtar RAR",
            )
            remaining -= len(blob)
            extracted.append((name, blob))
            entries_desc.append(f"{name} ({len(blob) // 1024}KB)")

        return extracted, entries_desc


def classify_archive_member_filename(filename: str) -> ArchiveMemberLangClass:
    """Classify an archive member filename into `BILINGUAL`, `EXPLICIT_VI`, `EXPLICIT_EN`, or `NEUTRAL`.

    Works regardless of date/ticker prefixes (e.g. `20260420 - HDC - 2025 Annual Report.pdf`,
    `m88_...___fts___resolution_and_minutes_agm_2026.pdf`).
    """
    base = unicodedata.normalize("NFC", (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip())
    if not base:
        return "NEUTRAL"
    stem = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    stem_spaced = re.sub(r"[_\-./]+", " ", stem).strip()

    if _BILINGUAL_META_CUE_RE.search(base) or _BILINGUAL_META_CUE_RE.search(stem_spaced):
        return "BILINGUAL"

    has_vn = bool(
        _VIET_CHAR_RE.search(base)
        or _EXPLICIT_VN_FILE_SUFFIX_RE.search(base)
        or _VN_UNACCENTED_META_CUE_RE.search(stem_spaced)
    )
    has_en = bool(
        _EXPLICIT_EN_FILE_SUFFIX_RE.search(base)
        or _ARCHIVE_EN_FILING_PHRASE_RE.search(stem_spaced)
    )
    if has_vn and has_en:
        return "BILINGUAL"
    if has_vn:
        return "EXPLICIT_VI"
    if has_en:
        return "EXPLICIT_EN"
    return "NEUTRAL"


_DIGITAL_SIG_BLOCK_START_RE = re.compile(
    r"(?:digitally\s+signed\s+by|signed\s+by|ký\s+bởi\s*:|người\s+ký\s*:|"
    r"\bdn:\s*c=|\bcn=|\boid\.\d|\bserialnumber=|tổ\s+chức\s+xác\s+thực\s*:|"
    r"văn\s+bản\s+này\s+được\s+xác\s+thực)",
    re.IGNORECASE,
)


def _strip_signature_stamp_for_lang_probe(text: str) -> str:
    """Strip Vietnamese CA X.509 digital-signature overlays before quick archive language probing.

    HOSE issuers routinely sign scanned English PDFs with a Vietnamese CA certificate
    (`Digitally signed by CÔNG TY CỔ PHẦN ... DN: C=VN, S=HÀ NỘI ...`), which injects a short
    Vietnamese text overlay on page 1 of an otherwise 100% scanned English document.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if not _DIGITAL_SIG_BLOCK_START_RE.search(raw):
        return raw
    # When the entire page text is a short digital-signature stamp overlay (< 700 chars), ignore it
    if len(raw) < 700 and re.search(
        r"(?:\bdn:\s*c=|\boid\.\d|digitally\s+signed\s+by|ký\s+bởi\s*:)", raw, re.IGNORECASE
    ):
        return ""
    kept: list[str] = []
    skip_wrapped_sig = False
    for raw_ln in raw.splitlines():
        s = raw_ln.strip()
        if not s:
            skip_wrapped_sig = False
            continue
        if _DIGITAL_SIG_BLOCK_START_RE.search(s) or re.match(
            r"^(?:reason|location|date|ngày\s+ký|mst)\s*:", s, re.IGNORECASE
        ):
            skip_wrapped_sig = True
            continue
        if skip_wrapped_sig and len(s) <= 60:
            # Skip short wrapped lines of a CA subject name / MST inside the signature box
            continue
        skip_wrapped_sig = False
        kept.append(s)
    return "\n".join(kept).strip()


def _classify_archive_pdf_member(name: str, pdf_bytes: bytes) -> tuple[ArchiveMemberLangClass, bool]:
    """Classify an archive PDF member using text-layer probe first, then filename cues.

    Returns `(lang_class, is_hard_english)` where `is_hard_english` is True when the PDF is
    unambiguously English on its own (digital EN text layer or explicit `_EN.pdf` suffix),
    whereas scanned PDFs with English filing phrases (`EXPLICIT_EN`, `is_hard_english=False`)
    are dropped whenever a Vietnamese/Bilingual sibling exists in the same archive.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    fname_cls = classify_archive_member_filename(base)
    has_en_suffix = bool(
        _EXPLICIT_EN_FILE_SUFFIX_RE.search(base)
        and not _VIET_CHAR_RE.search(base)
        and fname_cls != "BILINGUAL"
    )
    if not pdf_bytes.startswith(b"%PDF"):
        return fname_cls, (fname_cls == "EXPLICIT_EN" and has_en_suffix)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return fname_cls, (fname_cls == "EXPLICIT_EN" and has_en_suffix)
    try:
        total = len(doc)
        if total == 0:
            return fname_cls, (fname_cls == "EXPLICIT_EN" and has_en_suffix)
        sample_idxs = sorted({0, min(1, total - 1), total // 2, total - 1})
        vi_hits = 0
        en_hits = 0
        for idx in sample_idxs:
            raw_txt = (doc[idx].get_text("text") or "").strip()
            txt = _strip_signature_stamp_for_lang_probe(raw_txt)
            info = classify_text_language(txt)
            if info.lang_class in ("VI_PAGE", "BILINGUAL_MIXED_PAGE"):
                vi_hits += 1
            elif info.lang_class == "EN_PAGE":
                en_hits += 1
        if vi_hits > 0:
            return ("BILINGUAL" if en_hits > 0 else "EXPLICIT_VI"), False
        if en_hits > 0:
            return "EXPLICIT_EN", True
        return fname_cls, (fname_cls == "EXPLICIT_EN" and has_en_suffix)
    finally:
        doc.close()


def _quick_pdf_member_is_full_english(name: str, pdf_bytes: bytes) -> bool:
    """Fast check (`<= 4` sample pages, 0 OCR) to detect standalone English PDFs inside a multi-PDF archive."""
    lang_cls, is_hard_en = _classify_archive_pdf_member(name, pdf_bytes)
    return is_hard_en or lang_cls == "EXPLICIT_EN"


def _merge_pdf_blobs(
    items: list[tuple[str, bytes]],
    *,
    max_uncompressed_bytes: int,
) -> tuple[bytes, str] | None:
    merged_doc = fitz.open()
    names: list[str] = []
    try:
        for name, blob in items:
            with fitz.open(stream=blob, filetype="pdf") as part_doc:
                if len(part_doc) > 0:
                    merged_doc.insert_pdf(part_doc)
                    names.append(name)
        if len(merged_doc) > 0:
            merged_bytes = merged_doc.tobytes()
            if len(merged_bytes) <= max_uncompressed_bytes:
                return merged_bytes, "+".join(names)
    except Exception:  # noqa: BLE001
        return None
    finally:
        merged_doc.close()
    return None


def rank_archive_pdf_candidates(
    members: list[tuple[str, bytes]],
    entries_desc: list[str],
    *,
    doc_type: str | None = None,
    max_uncompressed_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
) -> list[tuple[bytes, str, list[str]]]:
    """Filter English & procedural PDFs inside an archive and return ranked candidate payloads.

    Enforces:
    1. Hard-English drop (digital EN text layer or `_EN.pdf` suffix) always rejected.
    2. Sibling-aware EN-phrase drop: scanned PDFs with English filing phrases (`2025 Annual Report.pdf`,
       `resolution_and_minutes_agm_2026.pdf`) are dropped whenever a Vietnamese/Bilingual sibling exists.
    3. Language-before-Size precedence: `EXPLICIT_VI`/`BILINGUAL` always outranks `NEUTRAL` regardless of size.
    4. Candidate fallback list: unchosen non-merged alternatives are appended after the primary choice so
       downstream OCR canary can fall back to the next member before ever writing a full-archive tombstone.
    """
    if not members:
        raise ArchiveExtractionError("Archive contains no PDF files")

    classified: list[tuple[str, bytes, ArchiveMemberLangClass, bool]] = []
    for name, blob in members:
        lang_cls, is_hard_en = _classify_archive_pdf_member(name, blob)
        classified.append((name, blob, lang_cls, is_hard_en))

    has_vi_sibling = any(cls in ("EXPLICIT_VI", "BILINGUAL") for _, _, cls, _ in classified)

    vn_or_unknown: list[tuple[str, bytes, ArchiveMemberLangClass]] = []
    en_rejected: list[str] = []
    for name, blob, lang_cls, is_hard_en in classified:
        drop_as_en = is_hard_en or (lang_cls == "EXPLICIT_EN" and (has_vi_sibling or len(classified) > 1))
        if drop_as_en:
            en_rejected.append(name)
        else:
            vn_or_unknown.append((name, blob, lang_cls))

    if not vn_or_unknown and en_rejected:
        raise ArchiveExtractionError(
            f"EXCLUDED_FULL_ENGLISH_DOCUMENT: all PDF members in archive are English ({', '.join(en_rejected[:4])})"
        )

    # Filter procedural ballot/proxy/agenda files if substantive PDFs also exist
    substantive = [
        (n, b, cls)
        for n, b, cls in vn_or_unknown
        if not _PROCEDURAL_ARCHIVE_MEMBER_RE.search(n.lower())
    ]
    pool = substantive if substantive else vn_or_unknown
    pool_has_vi = any(cls in ("EXPLICIT_VI", "BILINGUAL") for _, _, cls in pool)

    norm_dt = (doc_type or "").strip().upper()
    is_agm_pkg = norm_dt in ("NQ_DHDCD", "TAI_LIEU_DHDCD", "AGM_RESOLUTION", "AGM_MATERIALS") or any(
        any(
            k in n.lower()
            for k in ("to_trinh", "tờ trình", "to trinh", "nghi_quyet", "nghị quyết", "nghi quyet", "dhdcd", "đhđcđ")
        )
        for n, _, _ in pool
    )
    has_both_scopes = any(
        any(k in n.lower() for k in ("hop_nhat", "hợp nhất", "hop nhat", "hn")) for n, _, _ in pool
    ) and any(
        any(k in n.lower() for k in ("rieng", "riêng", "cong_ty_me", "công ty mẹ", "ctm"))
        for n, _, _ in pool
    )

    # Multi-proposal Vietnamese AGM package: merge Resolution + Board Reports + Proposals in deterministic order
    if is_agm_pkg and not has_both_scopes and len(pool) >= 2:
        agm_merge_pool = (
            [(n, b) for n, b, cls in pool if cls in ("EXPLICIT_VI", "BILINGUAL")]
            if pool_has_vi
            else [(n, b) for n, b, _ in pool]
        )
        if len(agm_merge_pool) >= 2:
            def _agm_sort_key(item: tuple[str, bytes]) -> tuple[int, str]:
                fl = item[0].lower()
                if "nghi_quyet" in fl or "nghị quyết" in fl or "nghi quyet" in fl:
                    return (0, fl)
                if "bao_cao" in fl or "báo cáo" in fl or "bao cao" in fl:
                    return (1, fl)
                if "to_trinh" in fl or "tờ trình" in fl or "to trinh" in fl:
                    return (2, fl)
                return (3, fl)

            ordered_agm = sorted(agm_merge_pool, key=_agm_sort_key)
            merged = _merge_pdf_blobs(ordered_agm, max_uncompressed_bytes=max_uncompressed_bytes)
            if merged is not None:
                return [(merged[0], merged[1], entries_desc)]
        elif len(agm_merge_pool) == 1:
            primary_name, primary_blob = agm_merge_pool[0]
            fallbacks = [
                (b, n, entries_desc) for n, b, _ in pool if n != primary_name
            ]
            return [(primary_blob, primary_name, entries_desc), *fallbacks]

    # Score primary filing: Language-before-Size, then Consolidated/Primary over Separate/Charter
    scored: list[tuple[int, int, int, str, bytes]] = []
    for name, blob, lang_cls in pool:
        fl = name.lower()
        fl_spaced = re.sub(r"[_\-./]+", " ", fl)
        lang_penalty = 0 if lang_cls in ("EXPLICIT_VI", "BILINGUAL") else 1
        is_charter = "dieu le" in fl_spaced or "điều lệ" in fl
        is_separate = any(k in fl_spaced for k in ("rieng", "riêng", "cong ty me", "công ty mẹ"))
        is_consolidated = any(k in fl_spaced for k in ("hop nhat", "hợp nhất"))
        is_priority = any(
            k in fl_spaced
            for k in (
                "nghi quyet",
                "nghị quyết",
                "bctn",
                "bao cao thuong nien",
                "baocaothuongnien",
                "báo cáo thường niên",
                "thuong nien",
                "thường niên",
                "bctc",
                "bao cao tai chinh",
                "báo cáo tài chính",
                "hop nhat",
                "hợp nhất",
                "giai trinh",
                "giải trình",
            )
        )
        if is_charter:
            doc_penalty = 3
        elif is_separate and has_both_scopes:
            doc_penalty = 2
        elif is_consolidated or is_priority:
            doc_penalty = 0
        else:
            doc_penalty = 1
        scored.append((lang_penalty, doc_penalty, -len(blob), name, blob))

    scored.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    chosen_name = scored[0][3]

    # Sequential multi-part (`*_p1.pdf`, `*_p2.pdf`) stem merge
    base_chosen = chosen_name.replace("\\", "/").rsplit("/", 1)[-1]
    part_match = _ARCHIVE_PART_RE.match(base_chosen)
    if part_match:
        target_stem = part_match.group("stem").lower()
        sibling_parts: list[tuple[int, str, bytes]] = []
        for name, blob, _cls in pool:
            bname = name.replace("\\", "/").rsplit("/", 1)[-1]
            m_sib = _ARCHIVE_PART_RE.match(bname)
            if m_sib and m_sib.group("stem").lower() == target_stem:
                sibling_parts.append((int(m_sib.group("part")), name, blob))
        sibling_parts.sort(key=lambda item: (item[0], item[1]))
        if len(sibling_parts) >= 2:
            merged = _merge_pdf_blobs(
                [(n, b) for _, n, b in sibling_parts],
                max_uncompressed_bytes=max_uncompressed_bytes,
            )
            if merged is not None:
                merged_part_names = {n for _, n, _ in sibling_parts}
                rest = [
                    (blob, name, entries_desc)
                    for _, _, _, name, blob in scored
                    if name not in merged_part_names
                ]
                return [(merged[0], merged[1], entries_desc), *rest]

    return [(blob, name, entries_desc) for _, _, _, name, blob in scored]


def select_and_merge_archive_pdfs(
    members: list[tuple[str, bytes]],
    entries_desc: list[str],
    *,
    doc_type: str | None = None,
    max_uncompressed_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
) -> tuple[bytes, str, list[str]]:
    """Filter out full-English & procedural ballot PDFs inside an archive, returning the top candidate."""
    ranked = rank_archive_pdf_candidates(
        members,
        entries_desc,
        doc_type=doc_type,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    return ranked[0]
