import os
import fitz  # PyMuPDF
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from PIL import Image

try:
    import pytesseract
    HAS_PYTESSERACT = True
    win_tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(win_tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = win_tesseract_path
except ImportError:
    HAS_PYTESSERACT = False

from .config import FinancialProfileConfig, load_profile


@dataclass
class PageMeta:
    page_number: int  # 1-indexed
    page_type: str    # 'balance_sheet', 'income_statement', 'cash_flow', 'audit_report', 'footnote', etc.
    matched_signature: str
    decision: str     # 'KEEP' or 'SKIP'
    snippet: str      # Snippet of extracted header text


@dataclass
class PageClassificationResult:
    total_pages: int
    retained_pages_count: int
    skipped_pages_count: int
    pages_meta: List[PageMeta]
    retained_page_indices: List[int]  # 0-indexed
    pruned_pdf_bytes: bytes


class PageClassifier:
    """Mô hình phân loại trang BCTC tối ưu chạy trên CPU.

    Chiến lược 3-tier theo loại PDF:
    1. FULLY DIGITAL PDF  : Text layer đủ → classify ngay qua text, không cần OCR (< 1s)
    2. HYBRID PDF         : Có text layer ở một số trang → OCR top-band (35%) MỌI trang scan
                            để tìm trang tiêu đề báo cáo ("Mẫu số B0X - CTCK/DN", "BẢN THUYẾT MINH",
                            "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG", ...) nằm giữa file; trang dữ liệu scan thừa
                            hưởng context của section. OCR top-band nhanh (~0.3-0.5s/trang) nên chi
                            phí CPU chấp nhận được so với GPU tiết kiệm được.
    3. FULLY SCANNED PDF  : Toàn scan, không có text layer → BỎ QUA Tesseract hoàn toàn,
                            giữ nguyên toàn bộ PDF gốc (ROI âm: scan 80 trang = 100s classify
                            nhưng chỉ tiết kiệm 30s GPU → lãng phí!)
    """

    # Trang scan tối đa được phép OCR trong 1 hybrid doc (an toàn tránh timeout; 30min/container)
    MAX_OCR_PAGES = 300

    # Ngưỡng trang có text để phát hiện "fully digital"
    TEXT_CHAR_THRESHOLD = 40

    # Sample size để phát hiện loại PDF
    SAMPLE_SIZE = 5

    # --- English-section detection ------------------------------------------
    # BCTC thường có 1 bản dịch tiếng Anh ở cuối (trùng nội dung với phần tiếng
    # Việt) — OCR bản này là lãng phí GPU. Nhận diện bằng tỉ lệ ký tự có dấu
    # tiếng Việt trên số chữ cái ASCII: trang tiếng Việt có tỉ lệ cao (>= ~0.06),
    # trang tiếng Anh xấp xỉ 0 (Tesseract đọc sạch chữ Latin, không có dấu).
    VIET_DIACRITICS = frozenset("ăâđêôơưĂÂĐÊÔƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ")
    # Dưới ngưỡng này coi là tiếng Anh; từ 0.04 trở lên chắc chắn tiếng Việt.
    ENGLISH_DIACRITIC_RATIO_THRESHOLD = 0.02
    # Cần đủ chữ cái mới phán đoán (trang toàn bảng số không đáng tin).
    ENGLISH_MIN_ALPHA_CHARS = 30
    # Nếu phần "tiếng Anh" bắt đầu ngay từ đầu trang (< 3) thì nghi ngờ nhận diện
    # sai (cả tài liệu tiếng Anh, hiếm với BCTC Việt) → giữ nguyên an toàn.
    ENGLISH_MIN_START_PAGE = 3

    # Title mạnh dùng làm gate cho OCR của trang scan: chỉ tin signature khi top-band
    # OCR chứa một trong các cụm này (hoặc mã "Mẫu số B0X") để tránh noise của bảng số
    # làm lật context (vd. đang ở footnote mà một dòng bảng chứa "KẾT QUẢ KINH DOANH").
    STRONG_TITLES = frozenset([
        "BAO CAO TINH HINH TAI CHINH HOP NHAT",
        "BAO CAO TINH HINH TAI CHINH RIENG",
        "BAO CAO TINH HINH TAI CHINH",
        "TINH HINH TAI CHINH HOP NHAT",
        "TINH HINH TAI CHINH RIENG",
        "TINH HINH TAI CHINH",
        "BANG CAN DOI KE TOAN HOP NHAT",
        "BANG CAN DOI KE TOAN RIENG",
        "BANG CAN DOI KE TOAN",
        "BAO CAO KET QUA HOAT DONG KINH DOANH HOP NHAT",
        "BAO CAO KET QUA HOAT DONG KINH DOANH RIENG",
        "BAO CAO KET QUA HOAT DONG KINH DOANH",
        "BAO CAO KET QUA HOAT DONG HOP NHAT",
        "BAO CAO KET QUA HOAT DONG RIENG",
        "BAO CAO KET QUA HOAT DONG",
        "BAO CAO LUU CHUYEN TIEN TE HOP NHAT",
        "BAO CAO LUU CHUYEN TIEN TE RIENG",
        "BAO CAO LUU CHUYEN TIEN TE",
        "LUU CHUYEN TIEN TE HOP NHAT",
        "LUU CHUYEN TIEN TE RIENG",
        "LUU CHUYEN TIEN TE",
        "THUYET MINH BAO CAO TAI CHINH",
        "THUYET MINH BCTC",
        "BAO CAO CUA BAN GIAM DOC",
        "BAO CAO HOI DONG QUAN TRI",
        "BAO CAO KIEM TOAN DOC LAP",
        "BAO CAO KIEM TOAN",
        "Y KIEN CUA KIEM TOAN VIEN",
        "STATEMENT OF FINANCIAL POSITION",
        "INCOME STATEMENT",
        "CASH FLOW STATEMENT",
        "NOTES TO THE FINANCIAL STATEMENTS",
        "INDEPENDENT AUDITOR S REPORT",
        "JOINT STOCK COMPANY",
        "THE SOCIALIST REPUBLIC OF VIETNAM",
    ])
    # Mã mẫu biểu: "MẪU SỐ B01", "Mẫu số B 01", "Mẫu số B03b - CTCK", ...
    FORM_CODE_RE = re.compile(r"MAU SO B\s*0\d")

    def __init__(self, config: Optional[FinancialProfileConfig] = None):
        self.config = config or load_profile()
        self.signatures = self.config.page_classifier.signatures
        self.skip_types = set(self.config.page_classifier.skip_page_types)
        self.keep_types = set(self.config.page_classifier.keep_page_types)

    def _detect_pdf_type(self, doc: fitz.Document) -> str:
        """Phát hiện loại PDF bằng cách sample nhanh 5 trang.

        Returns:
            'digital'  - Có text layer tốt → classify qua text
            'hybrid'   - Một số trang có text, một số không
            'scanned'  - Toàn scan, không có text layer nào
        """
        total = len(doc)
        # Lấy mẫu đều từ đầu, giữa, cuối tài liệu
        sample_indices = list(set([
            0,
            min(1, total - 1),
            total // 4,
            total // 2,
            min(total - 1, total * 3 // 4)
        ]))

        text_page_count = 0
        for idx in sample_indices:
            text = doc[idx].get_text("text") or ""
            if len(text.strip()) >= self.TEXT_CHAR_THRESHOLD:
                text_page_count += 1

        ratio = text_page_count / len(sample_indices)
        if ratio >= 0.6:
            return "digital"
        elif ratio >= 0.2:
            return "hybrid"
        else:
            return "scanned"

    def classify_and_prune(self, pdf_bytes: bytes) -> PageClassificationResult:
        """Phân loại từng trang trong PDF và trích xuất file PDF mới chỉ chứa các trang được GIỮ (KEEP).

        Tự động chọn chiến lược:
        - Digital PDF  : Classify đầy đủ qua text layer (< 1s)
        - Hybrid PDF   : Classify + Tesseract giới hạn 8 trang đầu (< 15s)
        - Scanned PDF  : Bỏ qua classify, giữ nguyên toàn bộ (< 0.5s, tránh tốn 100s+ OCR vô ích)
        """
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)

        # === BƯỚC 0: Phát hiện loại PDF ===
        pdf_type = self._detect_pdf_type(doc)

        # === BƯỚC 0.5: Phát hiện phần đuôi tiếng Anh (bản dịch trùng nội dung) ===
        eng_from = self._find_english_section(doc)
        if eng_from < total_pages:
            print(
                f"  [CLASSIFIER] Phát hiện bản dịch tiếng Anh từ trang {eng_from + 1} → "
                f"bỏ {total_pages - eng_from} trang trùng ({100.0 * (total_pages - eng_from) / total_pages:.0f}% GPU)."
            )

        # === Scanned PDF: bỏ qua classify, giữ toàn bộ (trừ đuôi tiếng Anh) ===
        if pdf_type == "scanned":
            if eng_from < total_pages:
                pages_meta = [
                    PageMeta(
                        page_number=i + 1,
                        page_type="unknown",
                        matched_signature="",
                        decision="KEEP",
                        snippet="[scanned-no-text-layer]"
                    )
                    for i in range(eng_from)
                ]
                pages_meta += [
                    PageMeta(
                        page_number=i + 1,
                        page_type="english_translation",
                        matched_signature="[english-section]",
                        decision="SKIP",
                        snippet="[english-duplicate]"
                    )
                    for i in range(eng_from, total_pages)
                ]
                retained_indices = list(range(eng_from))
                new_doc = fitz.open()
                for idx in retained_indices:
                    new_doc.insert_pdf(doc, from_page=idx, to_page=idx)
                pruned_bytes = new_doc.tobytes()
                new_doc.close()
                doc.close()
                return PageClassificationResult(
                    total_pages=total_pages,
                    retained_pages_count=len(retained_indices),
                    skipped_pages_count=total_pages - len(retained_indices),
                    pages_meta=pages_meta,
                    retained_page_indices=retained_indices,
                    pruned_pdf_bytes=pruned_bytes,
                )
            print(f"  [CLASSIFIER] Phát hiện Fully Scanned PDF ({total_pages} trang) → Bỏ qua Tesseract, giữ toàn bộ gửi Modal GPU.")
            pages_meta = [
                PageMeta(
                    page_number=i + 1,
                    page_type="unknown",
                    matched_signature="",
                    decision="KEEP",
                    snippet="[scanned-no-text-layer]"
                )
                for i in range(total_pages)
            ]
            doc.close()
            return PageClassificationResult(
                total_pages=total_pages,
                retained_pages_count=total_pages,
                skipped_pages_count=0,
                pages_meta=pages_meta,
                retained_page_indices=list(range(total_pages)),
                pruned_pdf_bytes=pdf_bytes  # Trả lại nguyên bản, không tốn thêm thời gian
            )

        # === Digital / Hybrid PDF: Classify từng trang ===
        if pdf_type == "digital":
            print(f"  [CLASSIFIER] Digital PDF ({total_pages} trang) → Classify qua text layer (nhanh).")
        else:
            print(f"  [CLASSIFIER] Hybrid PDF ({total_pages} trang) → OCR top-band mọi trang scan.")

        pages_meta: List[PageMeta] = []
        retained_indices: List[int] = []
        current_context = "unknown"
        ocr_pages_done = 0

        for page_idx in range(total_pages):
            page = doc[page_idx]

            # Đuôi tiếng Anh: bỏ hẳn, không cần phân loại cấu trúc
            if page_idx >= eng_from:
                snippet = (page.get_text("text") or "").strip()[:120].replace("\n", " ") or "[english-duplicate]"
                pages_meta.append(PageMeta(
                    page_number=page_idx + 1,
                    page_type="english_translation",
                    matched_signature="[english-section]",
                    decision="SKIP",
                    snippet=snippet,
                ))
                continue

            text = page.get_text("text") or ""
            is_scanned = len(text.strip()) < self.TEXT_CHAR_THRESHOLD

            # Hybrid: OCR top-band của MỌI trang không có text layer (không giới hạn 8 trang
            # đầu) để bắt được trang tiêu đề báo cáo nằm giữa file.
            if is_scanned:
                if pdf_type == "hybrid" and ocr_pages_done < self.MAX_OCR_PAGES:
                    ocr_text = self._extract_header_ocr(page)
                    ocr_pages_done += 1
                    combined_text = (text + " " + ocr_text).strip()
                else:
                    combined_text = text
            else:
                combined_text = text

            norm_text = self._normalize_text(combined_text)
            snippet = combined_text.strip()[:120].replace("\n", " ")

            detected_type = "unknown"
            matched_sig = ""

            # TOC Guard: trang Mục lục chứa tên của tất cả báo cáo → SKIP và không đổi current_context
            if "MUC LUC" in norm_text or "TABLE OF CONTENTS" in norm_text:
                detected_type = "cover_page"
                matched_sig = "[table-of-contents]"
            elif not (is_scanned and pdf_type == "hybrid") or self._has_strong_title(norm_text):
                # Khớp chữ ký nhận diện loại trang
                for page_type, sig_list in self.signatures.items():
                    for sig in sig_list:
                        sig_norm = self._normalize_text(sig)
                        if sig_norm and sig_norm in norm_text:
                            detected_type = page_type
                            matched_sig = sig
                            break
                    if detected_type != "unknown":
                        break

            # Guard: trong phần thuyết minh (footnote), tên các báo cáo chỉ là tiêu đề mục
            # A/B/C (vd. "A. Thuyết minh về Báo cáo tình hình tài chính", "B. Thuyết minh về
            # Báo cáo kết quả hoạt động") → KHÔNG cho chúng lật context (thuyết minh luôn đứng
            # sau các báo cáo trong BCTC, nên nếu đã vào footnote thì các báo cáo đã qua).
            if current_context == "footnote" and detected_type in (
                "balance_sheet", "income_statement", "cash_flow", "cover_page"
            ):
                detected_type = "unknown"
                matched_sig = ""

            # Context continuation: trang không xác định → thừa hưởng section hiện tại.
            # Áp dụng cho CẢ skip-type (balance_sheet/income/cash/english) lẫn keep-type
            # (footnote/audit/directors) để trang dữ liệu scan theo đúng section của nó.
            if detected_type != "unknown":
                current_context = detected_type
            else:
                detected_type = current_context

            # Ra quyết định KEEP / SKIP
            if detected_type == "unknown":
                decision = "SKIP"
            elif detected_type in self.skip_types:
                decision = "SKIP"
            else:
                decision = "KEEP"

            pages_meta.append(PageMeta(
                page_number=page_idx + 1,
                page_type=detected_type,
                matched_signature=matched_sig,
                decision=decision,
                snippet=snippet
            ))

            if decision == "KEEP":
                retained_indices.append(page_idx)

        # Bảo vệ dữ liệu: nếu không classify được trang nào → giữ tất cả
        if not retained_indices:
            print("[!] Cảnh báo: Không thể xác định cấu trúc trang. Giữ lại toàn bộ trang để đảm bảo an toàn.")
            retained_indices = list(range(total_pages))
            for meta in pages_meta:
                meta.decision = "KEEP"

        # Tạo PDF mới chỉ chứa trang KEEP
        new_doc = fitz.open()
        for idx in retained_indices:
            new_doc.insert_pdf(doc, from_page=idx, to_page=idx)

        pruned_bytes = new_doc.tobytes()
        new_doc.close()
        doc.close()

        return PageClassificationResult(
            total_pages=total_pages,
            retained_pages_count=len(retained_indices),
            skipped_pages_count=total_pages - len(retained_indices),
            pages_meta=pages_meta,
            retained_page_indices=retained_indices,
            pruned_pdf_bytes=pruned_bytes
        )

    def _extract_header_ocr(self, page: fitz.Page) -> str:
        """Cắt top 35% trang và chạy tesseract OCR siêu nhanh trên CPU."""
        if not HAS_PYTESSERACT:
            return page.get_text("text") or ""

        try:
            rect = page.rect
            header_clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.35)
            pix = page.get_pixmap(dpi=130, clip=header_clip)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            try:
                text = pytesseract.image_to_string(img, lang="vie+eng")
            except Exception:
                text = pytesseract.image_to_string(img)
            return text or ""
        except Exception:
            return page.get_text("text") or ""

    def _page_ocr_text(self, doc: fitz.Document, page_idx: int) -> str:
        """Lấy toàn văn một trang: text layer nếu có, nếu không thì OCR full-page 100 DPI."""
        text = doc[page_idx].get_text("text") or ""
        if len(text.strip()) >= self.TEXT_CHAR_THRESHOLD:
            return text
        try:
            pix = doc[page_idx].get_pixmap(dpi=100)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            try:
                return pytesseract.image_to_string(img, lang="vie+eng") or ""
            except Exception:
                return pytesseract.image_to_string(img) or ""
        except Exception:
            return text

    def _is_english_page(self, doc: fitz.Document, page_idx: int) -> bool:
        """True nếu trang là tiếng Anh (tỉ lệ dấu tiếng Việt gần 0, đủ chữ cái)."""
        text = self._page_ocr_text(doc, page_idx)
        letters = sum(1 for c in text if c.isascii() and c.isalpha())
        if letters < self.ENGLISH_MIN_ALPHA_CHARS:
            return False
        diacritics = sum(1 for c in text if c in self.VIET_DIACRITICS)
        return (diacritics / letters) < self.ENGLISH_DIACRITIC_RATIO_THRESHOLD

    def _find_english_section(self, doc: fitz.Document) -> int:
        """Tìm chỉ số (0-based) của trang tiếng Anh đầu tiên trong phần đuôi tiếng Anh.

        Phần tiếng Anh là một khối liên tục ở cuối tài liệu (bản dịch BCTC).
        Dùng binary search trên ngôn ngữ từng trang (text layer nếu có, OCR nếu scan)
        nên chỉ cần ~log2(N) lần OCR thay vì OCR toàn bộ. Trả về ``total_pages``
        nếu không có phần tiếng Anh.
        """
        total = len(doc)
        if total == 0:
            return 0
        # Gate nhanh: trang cuối không phải tiếng Anh → không có bản dịch.
        if not self._is_english_page(doc, total - 1):
            return total
        lo, hi = 0, total - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self._is_english_page(doc, mid):
                hi = mid
            else:
                lo = mid + 1
        # An toàn: tài liệu gần như toàn tiếng Anh là bất thường → không skip.
        if lo < self.ENGLISH_MIN_START_PAGE:
            return total
        return lo

    def _has_strong_title(self, norm_text: str) -> bool:
        """True nếu văn bản (đã normalize) chứa title mạnh hoặc mã mẫu biểu 'MẪU SỐ B0X'."""
        if not norm_text:
            return False
        if self.FORM_CODE_RE.search(norm_text):
            return True
        return any(t in norm_text for t in self.STRONG_TITLES)

    def _normalize_text(self, text: str) -> str:
        """Chuẩn hóa viết hoa, xóa dấu tiếng Việt và thay thế ký tự đặc biệt bằng khoảng trắng."""
        if not text:
            return ""
        nfkd_form = unicodedata.normalize('NFD', text.upper())
        clean_text = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
        clean_text = re.sub(r"[^\w\s]", " ", clean_text)
        return " ".join(clean_text.split())
