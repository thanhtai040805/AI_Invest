import re
from html.parser import HTMLParser
from typing import List, Tuple, Optional


class SimpleHTMLTableParser(HTMLParser):
    """Parser thẻ HTML <table> sử dụng thư viện chuẩn html.parser của Python (Zero external dependencies)."""

    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self.current_row: List[str] = []
        self.current_cell: List[str] = []
        self.in_cell: bool = False
        self.colspan: int = 1

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        tag_lower = tag.lower()
        if tag_lower in ("td", "th"):
            self.in_cell = True
            self.current_cell = []
            attr_dict = {k.lower(): (v or "") for k, v in attrs}
            try:
                self.colspan = int(attr_dict.get("colspan", "1"))
            except ValueError:
                self.colspan = 1
        elif tag_lower == "tr":
            self.current_row = []

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower in ("td", "th"):
            self.in_cell = False
            cell_text = " ".join("".join(self.current_cell).split()).replace("|", "\\|")
            for _ in range(max(1, self.colspan)):
                self.current_row.append(cell_text)
            self.colspan = 1
        elif tag_lower == "tr":
            if any(c.strip() for c in self.current_row):
                self.rows.append(self.current_row)

    def handle_data(self, data: str):
        if self.in_cell:
            self.current_cell.append(data)


class TableProcessor:
    """Xử lý và chuyển đổi các bảng HTML trong kết quả OCR thành bảng Markdown chuẩn (Clean Markdown Table) cho SAG."""

    @classmethod
    def parse_table_grid(cls, table_html: str) -> List[List[str]]:
        """Bóc tách ma trận hàng/cột từ HTML table."""
        parser = SimpleHTMLTableParser()
        try:
            parser.feed(table_html)
        except Exception:
            pass
        return parser.rows

    @classmethod
    def html_table_to_markdown(cls, table_html: str) -> str:
        """Chuyển đổi thẻ HTML <table>...</table> thành Clean Markdown Table (| Col 1 | Col 2 |).

        Sử dụng html.parser chuẩn của Python, 100% không bị phụ thuộc vào bs4 hay fallback xuất dòng dọc.
        """
        table_grid = cls.parse_table_grid(table_html)
        if not table_grid:
            return ""

        # Chuẩn hóa độ dài các hàng trong grid
        max_cols = max(len(r) for r in table_grid)
        for r in table_grid:
            while len(r) < max_cols:
                r.append("")

        # Xác định số lượng hàng làm Header (thường là 1 hoặc 2 hàng đầu tiên)
        header_rows_count = 1
        if len(table_grid) > 1:
            row2_str = " ".join(table_grid[1]).upper()
            if any(k in row2_str for k in ["GIA GOC", "GIA TRI HOP LY", "CUOI KY", "DAU NAM", "DU PHONG", "CHAGIA", "GIA MUA"]):
                header_rows_count = 2

        # Gộp tiêu đề cột
        final_headers: List[str] = []
        for col_idx in range(max_cols):
            parts = []
            for h_idx in range(header_rows_count):
                val = table_grid[h_idx][col_idx].strip()
                if val and val not in parts:
                    parts.append(val)
            header_title = " - ".join(parts) if parts else f"Cột {col_idx + 1}"
            final_headers.append(header_title)

        # Dữ liệu các hàng
        data_rows = table_grid[header_rows_count:]

        # Tạo chuỗi Markdown Table
        md_lines = []

        # Hàng Header
        header_line = "| " + " | ".join(final_headers) + " |"
        md_lines.append(header_line)

        # Hàng Separator
        separator_line = "| " + " | ".join(["---"] * max_cols) + " |"
        md_lines.append(separator_line)

        # Các hàng Dữ liệu
        for row in data_rows:
            if not any(c.strip() for c in row):
                continue
            row_line = "| " + " | ".join([c.strip() or "-" for c in row]) + " |"
            md_lines.append(row_line)

        return "\n".join(md_lines)

    @classmethod
    def process_table_block(cls, table_html: str) -> str:
        """Xử lý mọi khối <table>...</table>: Chuyển toàn bộ thành Clean Markdown Table chuẩn."""
        md_table = cls.html_table_to_markdown(table_html)
        return f"\n\n{md_table}\n\n"
