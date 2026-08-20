import re
import unicodedata
from typing import List, Tuple, Dict, Optional
from .config import FinancialProfileConfig, load_profile
from .table_processor import TableProcessor


class RegionClassifier:
    """Mô hình phân loại vùng/bảng dữ liệu trong phần Thuyết minh để lọc bỏ hoặc biến đổi các bảng cho SAG."""

    def __init__(self, config: Optional[FinancialProfileConfig] = None):
        self.config = config or load_profile()
        self.skip_keywords = [self._normalize_text(k) for k in self.config.region_classifier.skip_region_keywords if k]
        self.keep_keywords = [self._normalize_text(k) for k in self.config.region_classifier.keep_region_keywords if k]

    def _normalize_text(self, text: str) -> str:
        """Xóa dấu tiếng Việt, ký tự đặc biệt và chuyển chữ hoa."""
        if not text:
            return ""
        nfkd_form = unicodedata.normalize('NFD', text.upper())
        clean_text = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
        clean_text = re.sub(r"[^\w\s]", " ", clean_text)
        return " ".join(clean_text.split())

    def should_keep_region(self, title_or_text: str) -> Tuple[bool, str]:
        """Xác định một vùng text/bảng có nên được giữ lại hay không dựa trên tiêu đề hoặc nội dung."""
        norm_text = self._normalize_text(title_or_text)

        for kw in self.keep_keywords:
            if kw and kw in norm_text:
                return True, f"Matched keep keyword: '{kw}'"

        for kw in self.skip_keywords:
            if kw and kw in norm_text:
                return False, f"Matched skip keyword: '{kw}'"

        return True, "Default retain"

    def _convert_tables_to_markdown(self, markdown_text: str) -> Tuple[str, int]:
        """Bóc tách và biến đổi tất cả các khối HTML <table> (bao gồm cả khối ```html...```) thành Clean Markdown Table."""
        tables_processed = 0

        # Pattern 1: Xóa khối fenced code ```html <table>...</table> ```
        fenced_pattern = re.compile(r"```html\s*[\r\n]*(<table[\s\S]*?</table>)\s*[\r\n]*```", re.IGNORECASE)

        def _replace_fenced(m):
            nonlocal tables_processed
            tables_processed += 1
            table_html = m.group(1)
            return TableProcessor.process_table_block(table_html)

        cleaned_text = fenced_pattern.sub(_replace_fenced, markdown_text)

        # Pattern 2: Xóa các khối <table>...</table> đơn lẻ chưa có fence
        unfenced_pattern = re.compile(r"(<table[\s\S]*?</table>)", re.IGNORECASE)

        def _replace_unfenced(m):
            nonlocal tables_processed
            tables_processed += 1
            table_html = m.group(1)
            return TableProcessor.process_table_block(table_html)

        cleaned_text = unfenced_pattern.sub(_replace_unfenced, cleaned_text)
        return cleaned_text, tables_processed

    def filter_markdown_sections(self, markdown_text: str) -> Tuple[str, Dict[str, int]]:
        """Lọc bớt và biến đổi các khối bảng/tiêu đề trong Markdown đầu ra cho SAG."""
        stats = {
            "total_sections": 0,
            "retained_sections": 0,
            "skipped_sections": 0,
            "tables_processed": 0
        }

        # 1. Chuyển đổi toàn bộ các khối <table> HTML thành Clean Markdown Tables và bóc ```html
        converted_md, tables_count = self._convert_tables_to_markdown(markdown_text)
        stats["tables_processed"] = tables_count

        # 2. Xử lý Lọc Section theo Headings
        lines = converted_md.split("\n")
        filtered_lines = []
        skip_current_section = False
        heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$")

        for line in lines:
            match = heading_pattern.match(line.strip())
            if match:
                stats["total_sections"] += 1
                title = match.group(2)
                keep, reason = self.should_keep_region(title)

                if keep:
                    skip_current_section = False
                    stats["retained_sections"] += 1
                    filtered_lines.append(line)
                else:
                    skip_current_section = True
                    stats["skipped_sections"] += 1
                    filtered_lines.append(f"\n> *[Đã lọc bỏ mục '{title}' ({reason}) để tối ưu chi phí AI]*\n")
            else:
                if not skip_current_section:
                    filtered_lines.append(line)

        return "\n".join(filtered_lines), stats
