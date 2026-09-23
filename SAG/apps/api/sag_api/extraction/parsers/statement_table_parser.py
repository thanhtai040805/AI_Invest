from __future__ import annotations

import re
from typing import Any

from sag_api.enums import FactType
from sag_api.extraction.parsers.accounting_taxonomy import (
    classify_statement_line,
    classify_temporal_column,
    detect_unit_scale_context,
    fold_text,
    parse_vn_number,
)

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_SEPARATOR_RE = re.compile(r"^\|?\s*[-:]+[-| :]*$")
_CODE_RE = re.compile(r"^\d{1,3}$")
_NUMBER_TOKEN_RE = re.compile(r"(?<!\d)-?\d[\d.,]{2,}")


def _looks_like_ocr_glued_numbers(raw: str) -> bool:
    """Reject one cell that contains multiple dot-separated numbers."""
    return bool(re.search(r"\.\d{3}\d{1,3}\.", raw))


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.endswith("|") and stripped.count("|") >= 2


def _is_separator(line: str) -> bool:
    return bool(_SEPARATOR_RE.match(line.strip()))


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    cells = stripped.split("|")
    # OCR sometimes drops only the opening pipe. Keep the first cell in that
    # case; dropping it shifts label/code/value columns and loses denominators.
    if stripped.startswith("|"):
        cells = cells[1:]
    if stripped.endswith("|"):
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _has_value_near_anchor(lines: list[str], anchor: str) -> bool:
    """Detect a numeric total without mistaking the printed formula for data."""
    folded_anchor = fold_text(anchor)
    found_anchor = False
    for index, line in enumerate(lines):
        if folded_anchor not in fold_text(line):
            continue
        found_anchor = True
        for candidate in lines[index : index + 4]:
            candidate = re.sub(r"\([^)]*=[^)]*\)", "", candidate)
            for token in _NUMBER_TOKEN_RE.findall(candidate):
                if token in {"270", "280"}:
                    continue
                if parse_vn_number(token) is not None:
                    return True
    return not found_anchor


def _is_equity_changes_ending_balance_row(row_label: str) -> bool:
    """Identify the ending balance row of an equity changes statement across all VN issuers.

    Standardized according to Circular 200/2014/TT-BTC & VAS:
    - Explicit ending keywords: 'cuối kỳ', 'cuối năm', 'số cuối kỳ', 'số cuối năm', 'số dư cuối kỳ', 'số dư cuối năm'
    - Explicit balance sheet date markers: '(số dư)? tại ngày dd/mm/yyyy' or 'tại ngày dd tháng mm'
      Excludes beginning-of-period rows (ngày 1 tháng 1 / 01/01).
    """
    rf = fold_text(row_label)
    if any(k in rf for k in ("cuoi ky", "cuoi nam", "so cuoi ky", "so cuoi nam", "so du cuoi")):
        return True
    if re.search(r"(so du\s+)?tai ngay\s*(3[01]|[12]?\d)\s*([/.]|thang)\s*(1[0-2]|0?[1-9])", rf):
        if re.search(r"tai ngay\s*0?1\s*([/.]|thang)\s*0?1\b", rf):
            return False
        return True
    return False


FINANCIAL_SECTION_MARKERS: dict[str, tuple[str, ...]] = {
    "balance_sheet": (
        "bang can doi", "bao cao tinh hinh tai chinh", "balance sheet",
        "statement of financial position",
    ),
    "income_statement": (
        "ket qua hoat dong", "bao cao ket qua", "income statement",
        "statement of income", "statement of comprehensive income",
        "comprehensive income", "statement of profit", "profit and loss",
    ),
    "cash_flow": (
        "luu chuyen tien", "cash flow", "statement of cash flows",
    ),
    "notes": (
        "thuyet minh", "notes to financial statements", "notes to the financial statements",
        "notes to interim financial statements", "notes to the interim financial statements",
        "notes to separate financial statements", "notes to the separate financial statements",
        "financial statement notes",
    ),
}


def assess_financial_document_completeness(markdown: str, doc_role: str) -> dict[str, Any]:
    """Report whether a financial source contains the sections needed by GIL.

    This intentionally inspects the OCR Markdown, before parsing.  Therefore a
    missing section is evidence of an incomplete source/OCR artifact, not proof
    that a parser failed to classify a row.
    """
    if str(doc_role or "").upper() not in {"ANNUAL_BACKBONE", "LATEST_QUARTER"}:
        return {"required": [], "found": [], "missing": [], "status": "NOT_APPLICABLE"}
    folded = fold_text(markdown)
    found = [
        section for section, markers in FINANCIAL_SECTION_MARKERS.items()
        if any(marker in folded for marker in markers)
    ]
    required = list(FINANCIAL_SECTION_MARKERS)
    missing = [section for section in required if section not in found]
    data_issues: list[str] = []
    if "balance_sheet" in found and any(
        anchor in folded for anchor in ("tong cong tai san", "total assets", "tong tai san")
    ) and not any(
        _has_value_near_anchor(markdown.splitlines(), anchor)
        for anchor in ("tong cong tai san", "total assets", "tong tai san")
        if anchor in folded
    ):
        data_issues.append("balance_sheet.total_assets_value")
    return {
        "required": required,
        "found": found,
        "missing": missing,
        "data_issues": data_issues,
        "status": "COMPLETE" if not missing and not data_issues else "INCOMPLETE",
    }


class StatementTableParser:
    """Deterministic parser for Balance Sheet, Income Statement, and Cash Flows tables."""

    def __init__(self, markdown: str, ticker: str = "") -> None:
        self.markdown = markdown
        self.lines = markdown.splitlines()
        self.ticker = ticker.upper().strip()
        self._document_scale = 1.0
        folded = fold_text(markdown[:12000])
        bank_signals = (
            "ngan hang thuong mai",
            "cho vay khach hang",
            "thu nhap lai thuan",
            "tien gui cua khach hang",
            "to chuc tin dung",
        )
        self.is_bank = sum(signal in folded for signal in bank_signals) >= 2

    def parse_statement_facts(self, accounting_scope: str = "CONSOLIDATED") -> list[dict[str, Any]]:
        """Extract all standard accounting line items (equity, assets, receivables, payables, cash, revenue)."""
        facts: list[dict[str, Any]] = []
        in_table = False
        current_table: list[tuple[int, list[str]]] = []
        self._document_scale = 1.0

        for line_no, line in enumerate(self.lines, start=1):
            if _is_table_row(line):
                if not in_table:
                    in_table = True
                    current_table = []
                current_table.append((line_no, _split_row(line)))
            else:
                if in_table:
                    # Process completed table block
                    table_facts = self._process_table_block(current_table, self._document_scale, accounting_scope)
                    facts.extend(table_facts)
                    equity_facts = self._process_equity_changes_table(
                        current_table, self._document_scale, accounting_scope
                    )
                    facts.extend(equity_facts)
                    in_table = False
                    current_table = []

        if in_table and current_table:
            facts.extend(self._process_table_block(current_table, self._document_scale, accounting_scope))
            facts.extend(self._process_equity_changes_table(current_table, self._document_scale, accounting_scope))

        # OCR often inserts blank lines between continuation fragments of one
        # statement table.  Recover coded rows from those fragments without
        # opening the gate for arbitrary numeric note tables.
        seen_lines = {fact["evidence"]["line_start"] for fact in facts}
        for line_no, line in enumerate(self.lines, 1):
            if line_no in seen_lines or not _is_table_row(line):
                continue
            cells = _split_row(line)
            code_idx = next((idx for idx, cell in enumerate(cells[:2]) if _CODE_RE.match(cell)), None)
            if code_idx is None or len(cells) <= code_idx + 1:
                continue
            row_label = cells[0] if code_idx == 1 else cells[1]
            acct_code = cells[code_idx]
            if any(term in fold_text(" ".join(self.lines[max(0, line_no - 8) : line_no])) for term in ("luu chuyen tien", "cash flow")):
                continue
            classification = classify_statement_line(row_label, acct_code, is_bank=self.is_bank)
            if classification is None:
                continue
            value_raw = next(
                (
                    cell
                    for cell in cells[code_idx + 1 :]
                    if not _looks_like_ocr_glued_numbers(cell) and parse_vn_number(cell) is not None
                ),
                None,
            )
            if value_raw is None:
                continue
            parsed = parse_vn_number(value_raw)
            if parsed is None:
                continue
            nearby_scale, scale_found = detect_unit_scale_context(
                "\n".join(self.lines[max(0, line_no - 8) : line_no + 1])
            )
            value_numeric = parsed * (1.0 if abs(parsed) > 500_000_000_000 else (nearby_scale if scale_found else self._document_scale))
            fact_type, semantic_key = classification
            facts.append({
                "fact_type": fact_type.value,
                "label": f"{row_label} (fallback)",
                "semantic_key": semantic_key,
                "value_numeric": value_numeric,
                "value_text": value_raw,
                "unit": "VND",
                "currency": "VND",
                "raw_label": row_label[:256],
                "evidence": {"line_start": line_no, "line_end": line_no, "quote": line.strip()},
                "metadata_json": {
                    "source": "DETERMINISTIC_STATEMENT_ROW_FALLBACK",
                    "statement_scope": accounting_scope,
                    "accounting_code": acct_code,
                },
            })

        return facts

    def _process_table_block(
        self,
        table_rows: list[tuple[int, list[str]]],
        default_scale: float,
        scope: str,
    ) -> list[dict[str, Any]]:
        if len(table_rows) < 3:
            return []

        # 1. Detect header row
        header_row_idx = 0
        header_after_separator = False
        for idx, (_, cells) in enumerate(table_rows):
            line_str = "| " + " | ".join(cells) + " |"
            if _is_separator(line_str):
                before = max(0, idx - 1)
                after = idx + 1
                after_headers = table_rows[after][1] if after < len(table_rows) else []
                if after_headers and any(classify_temporal_column(cell) is not None for cell in after_headers):
                    header_row_idx = after
                    header_after_separator = True
                else:
                    header_row_idx = before
                break

        header_cells = table_rows[header_row_idx][1] if table_rows else []
        headers: dict[int, str] = {i: c for i, c in enumerate(header_cells)}
        data_like_header = bool(
            header_cells
            and _CODE_RE.match(header_cells[0].strip())
            and sum(parse_vn_number(cell) is not None for cell in header_cells[2:]) >= 1
        )

        context = fold_text(" ".join(self.lines[max(0, table_rows[0][0] - 26): table_rows[0][0] - 1]))
        recent_context = fold_text(" ".join(self.lines[max(0, table_rows[0][0] - 9): table_rows[0][0] - 1]))
        headings = [
            line for line in self.lines[: table_rows[0][0] - 1]
            if line.strip().startswith("#")
        ]
        heading_context = fold_text(headings[-1] if headings else "")
        header_folded = fold_text(" ".join(header_cells))
        table_folded = fold_text(" ".join(" ".join(cells) for _, cells in table_rows[:5]))
        note_label = self._note_context_label(heading_context, recent_context, table_folded)
        if not self._is_statement_table(header_folded, context, recent_context, table_folded, headers, table_rows) and not note_label:
            return []

        # Detect table-local scale if present (from preceding lines or first rows)
        start_line = table_rows[0][0]
        prec_text = "\n".join(self.lines[max(0, start_line - 8) : start_line])
        table_text = prec_text + "\n| " + " | ".join(header_cells) + " |"
        local_scale, local_scale_found = detect_unit_scale_context(table_text)
        scale = local_scale if local_scale_found else default_scale
        if note_label == "bao lanh" and self.is_bank:
            # Vietnamese bank off-balance tables inherit the statement's
            # million-VND unit even when OCR drops the unit row.
            nearby_unit = fold_text(" ".join(self.lines[max(0, start_line - 3) : start_line + 2]))
            scale = 1_000_000.0 if "ty vnd" not in nearby_unit else 1_000_000_000.0

        primary_statement_table = (
            any("ma so" in fold_text(header) or "code" in fold_text(header) for header in headers.values())
            or any(term in header_folded for term in ("tai san", "nguon von"))
            or "bang can doi" in context
            or (
                "bao cao tinh hinh tai chinh" in context
                and any(classify_temporal_column(header) is not None for header in headers.values())
            )
        )

        # Find columns: label, code, value
        code_col_idx = -1
        label_col_idx = 0
        for idx, h in headers.items():
            hf = fold_text(h)
            if "ma so" in hf or "code" in hf:
                code_col_idx = idx
            elif any(k in hf for k in ["chi tieu", "tai san", "nguon von", "noi dung", "khoan muc", "item"]):
                label_col_idx = idx

        if code_col_idx == 0 and label_col_idx == 0 and len(headers) > 1:
            label_col_idx = 1

        body_start = header_row_idx + (1 if header_after_separator else 2)
        body_rows = table_rows if data_like_header else table_rows[body_start:]
        facts: list[dict[str, Any]] = []
        note_fallback_used = False

        for line_no, cells in body_rows:
            if not cells:
                continue
            line_str = "| " + " | ".join(cells) + " |"
            if _is_separator(line_str):
                continue

            # Detect row label and acct_code
            row_label = ""
            acct_code = None

            if code_col_idx >= 0 and code_col_idx < len(cells):
                cell_code = cells[code_col_idx].strip()
                if _CODE_RE.match(cell_code):
                    acct_code = cell_code

            if label_col_idx < len(cells):
                row_label = cells[label_col_idx].strip()

            # If cells[0] was detected as numeric code and label was not set or looks like a code
            if not row_label or _CODE_RE.match(row_label):
                if len(cells) > 1 and not _CODE_RE.match(cells[1].strip()):
                    acct_code = cells[0].strip() if _CODE_RE.match(cells[0].strip()) else acct_code
                    row_label = cells[1].strip()

            row_uses_note_label = bool(
                note_label and (not row_label or len(row_label) < 2 or parse_vn_number(row_label) is not None)
            )
            if row_uses_note_label:
                if note_fallback_used:
                    continue
                note_fallback_used = True
                row_label = note_label
            elif not row_label or len(row_label) < 2:
                if not note_label:
                    continue
                row_label = note_label

            classification = classify_statement_line(row_label, acct_code, is_bank=self.is_bank)
            if classification is None:
                continue

            fact_type, semantic_key = classification

            # Find latest balance/period value cell
            # In Vietnamese statements: Usually last column or second-to-last is Cuối năm / Cuối kỳ (Current)
            value_numeric = None
            value_text = ""
            statement_col_name = "cuoiky"

            # Search numeric cells from current reporting period first
            current_cols = [
                idx
                for idx, header in headers.items()
                if classify_temporal_column(header) == "CURRENT_PERIOD"
                or any(term in fold_text(header) for term in ("cuoi ky", "cuoi nam", "ky nay", "nam nay", "current"))
            ]
            other_temporal_cols = [
                idx
                for idx, header in headers.items()
                if idx not in current_cols
                and (
                    classify_temporal_column(header) is not None
                    or any(term in fold_text(header) for term in ("nam truoc", "so dau", "ky truoc", "previous"))
                )
            ]
            if data_like_header and len(cells) >= 3:
                candidate_cols = [len(cells) - 2]
            elif current_cols:
                candidate_cols = current_cols
            else:
                candidate_cols = other_temporal_cols or list(range(len(cells) - 1, 0, -1))

            for col_idx in candidate_cols:
                if col_idx >= len(cells) or col_idx == code_col_idx:
                    continue
                col_header = headers.get(col_idx, "")
                cell_raw = cells[col_idx].strip()
                if _looks_like_ocr_glued_numbers(cell_raw):
                    # parse_vn_number intentionally recovers glued OCR totals
                    # for generic text, but a statement cell must never sum
                    # two columns into one accounting fact.
                    value_numeric = None
                    value_text = ""
                    break
                parsed = parse_vn_number(cell_raw)
                if parsed is not None:
                    eff_scale = 1.0 if abs(parsed) > 500_000_000_000 else scale
                    value_numeric = parsed * eff_scale
                    value_text = cell_raw
                    statement_col_name = col_header or "cuoiky"
                    if "cuoiky" not in fold_text(statement_col_name) and "cuoi" not in fold_text(statement_col_name):
                        statement_col_name = f"{statement_col_name}_cuoiky"
                    break
                elif candidate_cols == current_cols and cell_raw in ("-", "—", "nil", "0", "0,0", "0.0"):
                    # Current period explicitly zeroed / nil -> do not leak from previous period
                    value_numeric = 0.0
                    value_text = cell_raw
                    statement_col_name = col_header or "cuoiky"
                    if "cuoiky" not in fold_text(statement_col_name) and "cuoi" not in fold_text(statement_col_name):
                        statement_col_name = f"{statement_col_name}_cuoiky"
                    break

            if value_numeric is not None:
                facts.append({
                    "fact_type": fact_type.value,
                    "label": f"{row_label} ({statement_col_name})",
                    "semantic_key": semantic_key,
                    "value_numeric": value_numeric,
                    "value_text": value_text,
                    "unit": "VND",
                    "currency": "VND",
                    "raw_label": row_label[:256],
                    "evidence": {
                        "line_start": line_no,
                        "line_end": line_no,
                        "quote": self.lines[line_no - 1].strip(),
                    },
                "metadata_json": {
                        "source": "DETERMINISTIC_STATEMENT_TABLE",
                        "statement_column": statement_col_name,
                        "statement_scope": scope,
                        "accounting_code": acct_code,
                    },
                })

        if local_scale_found and facts and primary_statement_table:
            self._document_scale = local_scale
        return facts

    @staticmethod
    def _note_context_label(heading_context: str, recent_context: str, table_folded: str) -> str | None:
        """Map note-table section context to a metric when the row has no label column."""
        text = f"{heading_context} {recent_context}"
        rules = (
            (("doanh thu hoat dong tai chinh",), "doanh thu hoat dong tai chinh"),
            (("gia von hang ban", "gia von"), "gia von hang ban"),
            (("doanh thu ban hang", "doanh thu thuan", "doanh thu"), "doanh thu thuan"),
        )
        for terms, label in rules:
            if any(term in text for term in terms):
                return label
        if any(term in table_folded for term in ("tien mat", "tien gui", "tuong duong tien")) and "tien va cac khoan tuong duong tien" in text:
            return "tien va cac khoan tuong duong tien"
        if any(term in table_folded for term in ("bao lanh vay", "bao lanh khac")):
            return "bao lanh"
        return None

    @staticmethod
    def _is_statement_table(
        header_folded: str,
        context: str,
        recent_context: str,
        table_folded: str,
        headers: dict[int, str],
        table_rows: list[tuple[int, list[str]]],
    ) -> bool:
        """Gate classification to financial-statement tables, not every numeric table."""
        if any(term in f"{recent_context} {table_folded}" for term in ("luu chuyen tien", "chuyen tien", "cash flow")):
            return False
        has_label = any(term in header_folded for term in ("chi tieu", "item", "tai san", "nguon von"))
        has_code = any(term in fold_text(header) for header in headers.values() for term in ("ma so", "code"))
        has_period = any(
            classify_temporal_column(header) is not None
            or any(term in fold_text(header) for term in ("nam nay", "nam truoc", "so cuoi", "so dau", "ky nay", "ky truoc"))
            for header in headers.values()
        ) or any(term in context for term in ("tai ngay", "ky ke toan")) or bool(re.search(r"ngay\s+\d{1,2}\s+thang\s+\d{1,2}", context))
        has_period = has_period or any(
            bool(re.search(r"\b\d{1,2}\s*[/.]\s*\d{1,2}\s*[/.]\s*20\d{2}\b", fold_text(header)))
            for header in headers.values()
        )
        has_statement_context = any(
            term in context
            for term in (
                "bang can doi", "bao cao tinh hinh tai chinh", "ket qua hoat dong",
                "bao cao tai chinh", "thuyet minh", "thong tin theo bo phan", "bo phan",
                "balance sheet", "statement of financial position", "income statement",
                "statement of profit", "cash flow", "statement of cash flows",
            )
        )
        has_period = has_period or ("vnd" in header_folded and has_statement_context)
        note_table_context = any(
            term in context
            for term in ("bo phan", "doanh thu", "gia von", "phai thu", "tien va cac khoan tuong duong tien")
        )
        classifiable_row = any(
            classify_statement_line(cell, is_bank=False) is not None
            for _, cells in table_rows
            for cell in cells[:2]
        )
        return has_period and (
            has_label and (has_code or has_statement_context or "thuyet minh" in header_folded)
            or classifiable_row and (note_table_context or has_statement_context or has_code)
        )

    def _process_equity_changes_table(
        self,
        table_rows: list[tuple[int, list[str]]],
        default_scale: float,
        scope: str,
    ) -> list[dict[str, Any]]:
        """Extract total equity and equity components from Statement of Changes in Equity table."""
        if len(table_rows) < 3:
            return []

        header_row_idx = 0
        for idx, (_, cells) in enumerate(table_rows):
            line_str = "| " + " | ".join(cells) + " |"
            if _is_separator(line_str):
                header_row_idx = max(0, idx - 1)
                break

        h_row = table_rows[header_row_idx][1]
        hf = [fold_text(c) for c in h_row]
        joined_hf = " ".join(hf)

        # Exclude governance / insider share trading tables
        if any(bad in joined_hf for bad in ["ho ten", "chuc vu", "cccd", "cmnd", "giao dich", "dia chi"]):
            return []

        equity_col_matches = sum(
            1
            for c in hf
            if any(
                k in c
                for k in [
                    "von co phan",
                    "von gop",
                    "von dieu le",
                    "von dau tu",
                    "thang du",
                    "loi nhuan",
                    "quy dau tu",
                    "quy khac",
                    "co phieu quy",
                    "co phieu mua lai",
                ]
            )
        )
        if equity_col_matches < 2:
            return []

        # Table-level scale
        start_line = table_rows[0][0]
        prec_text = "\n".join(self.lines[max(0, start_line - 8) : start_line])
        table_head_text = prec_text + "\n| " + " | ".join(h_row) + " |"
        scale, scale_found = detect_unit_scale_context(table_head_text)
        if not scale_found:
            scale = default_scale

        tot_col_idx = -1
        for idx, c in enumerate(hf):
            if any(k in c for k in ["tong cong", "tong so", "total"]):
                tot_col_idx = idx
        if tot_col_idx == -1:
            tot_col_idx = len(h_row) - 1

        # Find latest ending balance row
        last_row = None
        for lno, row in table_rows[header_row_idx + 1 :]:
            if not row or _is_separator("| " + " | ".join(row) + " |"):
                continue
            if _is_equity_changes_ending_balance_row(row[0]):
                last_row = (lno, row)

        if not last_row:
            return []

        lno, r = last_row
        val_str = r[tot_col_idx] if tot_col_idx < len(r) else r[-1]
        val = parse_vn_number(val_str)
        val_last = parse_vn_number(r[-1])
        if val_last is not None and (
            val is None
            or any(k in fold_text(h_row[0]) for k in ("von", "thang du", "co phieu", "loi nhuan", "quy"))
            or (val is not None and abs(val_last) > abs(val))
        ):
            val_str = r[-1]
            val = val_last
        if val is None:
            return []

        final_scale = 1.0 if abs(val) > 500_000_000_000 else scale
        final_val = val * final_scale

        facts: list[dict[str, Any]] = []
        row_label = r[0].strip()
        facts.append({
            "fact_type": FactType.EQUITY.value,
            "label": f"Tổng cộng vốn chủ sở hữu ({row_label})",
            "semantic_key": "total_equity",
            "value_numeric": final_val,
            "value_text": val_str,
            "unit": "VND",
            "currency": "VND",
            "raw_label": f"Vốn chủ sở hữu ({row_label})",
            "evidence": {
                "line_start": lno,
                "line_end": lno,
                "quote": self.lines[lno - 1].strip(),
            },
            "metadata_json": {
                "source": "DETERMINISTIC_STATEMENT_TABLE",
                "statement_column": "tong_cong_cuoiky",
                "statement_scope": scope,
                "table_type": "EQUITY_CHANGES",
            },
        })
        if scale_found:
            self._document_scale = scale
        return facts
