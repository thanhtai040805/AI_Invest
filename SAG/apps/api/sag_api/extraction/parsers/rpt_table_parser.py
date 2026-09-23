from __future__ import annotations

import re
from typing import Any

from sag_api.enums import RelationType
from sag_api.extraction.anchors.discourse_resolver import (
    _MARKER_IN_CELL_RE,
    DiscourseResolver,
    canonicalize_marker,
)
from sag_api.extraction.frames.base import EvidenceReference
from sag_api.extraction.frames.gil_frames import RelatedPartyFlowFrame
from sag_api.extraction.parsers.accounting_taxonomy import (
    classify_temporal_column,
    detect_unit_scale,
    fold_text,
    parse_vn_number,
)


def _classify_transaction_flow(text: str, is_balance_table: bool = False) -> tuple[str, str]:
    """Classify Vietnamese transaction description into canonical relation_type and flow_kind aligned with VAS 26 & GIL denominators."""
    folded = fold_text(text)

    # 1. Loans / Lending (Cho vay)
    if "cho vay" in folded or "thu hoi tien cho vay" in folded or "thu hoi cho vay" in folded:
        if "thu hoi" in folded:
            return RelationType.LENDS_TO.value, "loan_repayment"
        return RelationType.LENDS_TO.value, "loan_balance" if is_balance_table else "loan"

    # 2. Interest (Lãi vay / Lãi tiền gửi / Thu lãi / Chi phí lãi)
    if "lai vay" in folded or "lai tien gui" in folded or "thu lai" in folded or "chi phi lai" in folded:
        if "phai tra" in folded:
            return RelationType.TRANSACTS_WITH.value, "interest_payable"
        if "chi phi" in folded or "tra lai" in folded:
            return RelationType.TRANSACTS_WITH.value, "interest_expense"
        return RelationType.TRANSACTS_WITH.value, "service_revenue"

    # 3. Borrowings / Debt (Đi vay / Vay và nợ)
    if (
        "di vay" in folded
        or "vay ngan han" in folded
        or "vay dai han" in folded
        or "khoan vay" in folded
        or "tien vay" in folded
        or "tra no goc" in folded
        or "tra no vay" in folded
        or "vay" in folded
    ):
        if "tra no" in folded:
            return RelationType.TRANSACTS_WITH.value, "loan_repayment"
        return RelationType.TRANSACTS_WITH.value, "borrowing_balance" if is_balance_table else "borrowing"

    # 4. Guarantees (Bảo lãnh)
    if "bao lanh" in folded:
        return RelationType.GUARANTEES_FOR.value, "guarantee"

    # 5. Capital Contributions / Investment (Đầu tư, góp vốn)
    if "dau tu" in folded or "gop von" in folded or "nhan gop von" in folded or "tang von" in folded:
        return RelationType.INVESTS_IN.value, "capital_contribution"
    if "von gop" in folded:
        return RelationType.INVESTS_IN.value, "capital_contribution"

    # Share value held by a related party is ownership evidence, not an issuer
    # receivable. Keep it observable but outside balance exposure ratios.
    if "von co phan" in folded or "co phan cua ben lien quan" in folded:
        return RelationType.TRANSACTS_WITH.value, "ownership_value"

    # 6. Dividends & Profit distribution (Cổ tức / Lợi nhuận)
    if "co tuc" in folded or "loi nhuan" in folded or "chia loi nhuan" in folded:
        return RelationType.TRANSACTS_WITH.value, "dividend"

    if "tien gui" in folded:
        return RelationType.TRANSACTS_WITH.value, "cash_deposit_balance"

    # 7. Receivables (Phải thu)
    if "phai thu" in folded:
        return RelationType.TRANSACTS_WITH.value, "receivable_balance" if is_balance_table else "receivable"

    # 8. Payables (Phải trả)
    if "phai tra" in folded:
        return RelationType.TRANSACTS_WITH.value, "payable_balance" if is_balance_table else "payable"

    # 9. Purchases & Expenses (Mua hàng hóa, chi phí dịch vụ)
    if any(k in folded for k in ("mua hang", "mua dich vu", "chi phi", "mua tai san", "hang mua")):
        return RelationType.TRANSACTS_WITH.value, "purchase"

    # 10. Revenues / Sales (Doanh thu, bán hàng, cung cấp dịch vụ)
    if any(k in folded for k in ("doanh thu", "ban hang", "cung cap", "tien thue")):
        return RelationType.TRANSACTS_WITH.value, "service_revenue"

    # Deposits / Investment Advances (Đặt cọc / Tạm ứng hợp tác đầu tư / Chuyển nhượng dự án)
    if any(k in folded for k in ("dat coc", "tam ung", "hop tac dau tu", "hop tac kinh doanh", "bcc", "chuyen nhuong du an", "mua co phan")):
        return RelationType.TRANSACTS_WITH.value, "deposit_or_investment_advance"

    # 11. Leases (Thuê hoạt động / Thuê văn phòng)
    if "thue van phong" in folded or "thue nha" in folded or "thue dat" in folded or "thue hoat dong" in folded:
        return RelationType.TRANSACTS_WITH.value, "operating_lease"

    if is_balance_table:
        return RelationType.TRANSACTS_WITH.value, "receivable_balance"
    return RelationType.TRANSACTS_WITH.value, "service_revenue"


def _parse_rpt_amount(raw: str) -> float | None:
    # OCR can glue several table cells into one numeric token. Summing that
    # token creates a value that is not evidenced by any source number.
    if re.search(r"\.\d{3}\d{1,3}\.", raw):
        return None
    return parse_vn_number(raw)


NON_ENTITY_TERMS = {
    "tien gui ngan hang",
    "tien mat",
    "cac khoan tuong duong tien",
    "tien va cac khoan tuong duong tien",
    "tuong duong tien",
    "tien gui co ky han",
    "anh huong ngan han",
    "trong nuoc",
    "ngoai nuoc",
    "xuat khau",
    "noi dia",
    "net profit",
    "profit after tax",
    "corporate income tax",
    "income tax",
    "operating income",
    "gross profit",
    "cash and cash equivalents",
    "doanh thu",
    "chi phi",
    "tong cong",
    "cong",
    "total",
    "cac doi tuong khac",
    "cac cong ty khac",
    "cac ben lien quan khac",
    "ben lien quan khac",
    "ben lien quan",
    "cac khoan dau tu khac",
    "cac khoan phai thu khac",
    "cac khoan phai tra khac",
    "doanh thu thuan",
    "loi nhuan duoc chia",
    "tien thu tu di vay",
    "tien tra no goc vay",
    "phai thu ngan han khac",
    "phai tra ngan han khac",
    "tai san ngan han",
    "tai san dai han",
    "von chu so huu",
    "tong cong tai san",
    "tong cong nguon von",
    "tien gui tai ngan hang",
    "tien gui tai nhnnvn",
    "tai ngan hang",
    "cua ngan hang",
    "thu nhap tu hoat dong dich vu",
    "lai thuan tu hoat dong kinh doanh ngoai hoi",
}

ACCOUNTING_LABEL_TERMS = (
    "doanh thu", "loi nhuan", "tien thu", "tien tra", "phai thu", "phai tra",
    "tai san", "nguon von", "chi phi", "von chu", "hang ton kho", "khau hao",
    "thu nhap", "lai thuan", "thu tu", "chi tu", "ngoai hoi", "tien gui",
    "tien vay", "cho thue", "tai ngan hang", "cua ngan hang",
    "cong no", "bo phan", "khong phan bo", "gia von", "bao cao",
    "tuong duong tien", "anh huong", "trong nuoc", "ngoai nuoc", "xuat khau",
    "net profit", "profit after", "income tax", "gross profit", "cash and cash",
)


def _valid_counterparty(value: str, *, explicit_party_column: bool) -> bool:
    folded = fold_text(value)
    if not folded or folded in NON_ENTITY_TERMS:
        return False
    if any(term in folded for term in ACCOUNTING_LABEL_TERMS):
        return False
    if re.fullmatch(r"[\d\s./()%:-]+", value):
        return False
    if explicit_party_column:
        return len(value) >= 3
    return any(marker in folded for marker in ENTITY_MARKERS)

CATEGORY_KEYWORDS = {
    "doanh thu cung cap dich vu",
    "cung cap hang hoa",
    "ban hang hoa",
    "mua hang",
    "mua hang hoa va dich vu",
    "cho vay",
    "thu hoi tien cho vay",
    "thu hoi cho vay",
    "di vay",
    "vay",
    "tra no goc vay",
    "chi phi lai vay",
    "thu lai tien cho vay",
    "lai tien gui",
    "co tuc",
    "doanh thu hoat dong tai chinh",
    "chi phi tai chinh",
    "phai thu khac",
    "phai tra khac",
    "cac khoan cho vay",
}

ENTITY_MARKERS = {
    "cong ty",
    "tnhh",
    "cp",
    "jsc",
    "corp",
    "group",
    "ong ",
    "ba ",
    "chi nhanh",
    "ngan hang",
    "quy ",
    "vien ",
    "truong ",
}


def is_category_header_row(cell_text: str) -> bool:
    """Check if a table cell represents a transaction category rather than an entity."""
    folded = fold_text(cell_text)
    if not folded:
        return False
    # If it contains explicit entity markers, it's not purely a category
    if any(marker in folded for marker in ENTITY_MARKERS):
        return False
    # If it matches or starts with category keywords
    if any(folded == cat or folded.startswith(cat) for cat in CATEGORY_KEYWORDS):
        return True
    accounting_category_prefixes = (
        "doanh thu", "ban hang", "cung cap", "gia tri hang mua", "mua hang",
        "hang mua", "phai thu", "phai tra", "cho vay", "di vay", "tien gui", "co tuc",
        "loi nhuan", "lai tien gui", "chi phi", "so du"
    )
    return any(folded.startswith(pfx) for pfx in accounting_category_prefixes)


def is_rpt_section_heading(line: str) -> bool:
    """Detect if a markdown heading marks the start of a Note discussing related parties."""
    trimmed = line.strip()
    if not (trimmed.startswith("#") or (len(trimmed) < 100 and any(trimmed.startswith(f"{k}.") for k in range(15, 45)))):
        return False
    # Skip policy definitions e.g. "2.26 Các bên liên quan", "3.27 Các bên liên quan"
    if re.match(r"^#{1,3}\s*(2|3)\.\d+\s+", trimmed) or re.match(r"^#{1,3}\s*\([a-z]\)\s+", trimmed):
        return False
    folded = fold_text(trimmed)
    if "ben lien quan" in folded and any(w in folded for w in ["nghiep vu", "giao dich", "so du", "thuyet minh", "danh sach", "chi tiet"]):
        return True
    return False


def _rpt_note_number(line: str) -> str | None:
    """Return the note number used to keep 39a/39b subsections together."""
    match = re.match(r"^#{1,6}\s*(?:note\s*)?(\d+)", line.strip(), flags=re.I)
    return match.group(1) if match else None


def _is_rpt_continuation_heading(line: str, section_title: str) -> bool:
    """Keep the current related-party note, not every later report table."""
    if is_rpt_section_heading(line):
        return True
    current_number = _rpt_note_number(section_title)
    next_number = _rpt_note_number(line)
    if current_number and next_number:
        return current_number == next_number
    folded = fold_text(line)
    return any(term in folded for term in ("ben lien quan", "related party", "giao dich", "so du"))


class RptTableParser:
    """Deterministic parser for related-party notes across standard Vietnamese corporate and banking BCTC."""

    def __init__(self, issuer_ticker: str = "") -> None:
        self.issuer_ticker = issuer_ticker.upper().strip()

    def parse_rpt_tables(self, markdown_text: str) -> list[RelatedPartyFlowFrame]:
        """Extract all RPT flow frames from markdown text."""
        lines = markdown_text.splitlines()
        frames: list[RelatedPartyFlowFrame] = []
        resolver = DiscourseResolver(markdown_text)
        footnotes = resolver.find_footnotes_in_range(1, len(lines))

        doc_scale = detect_unit_scale(markdown_text[:20000])

        in_rpt_section = False
        current_section_title = ""
        current_category = ""
        current_table_lines: list[tuple[int, str]] = []

        def flush_table(tbl_lines: list[tuple[int, str]], section_ctx: str, cat_ctx: str) -> None:
            if not tbl_lines:
                return
            parsed_frames = self._parse_single_table(
                tbl_lines, section_ctx, cat_ctx, doc_scale, footnotes
            )
            frames.extend(parsed_frames)

        for line_num, line in enumerate(lines, 1):
            trimmed = line.strip()
            folded = fold_text(trimmed)

            if is_rpt_section_heading(line):
                in_rpt_section = True
                current_section_title = trimmed
                current_category = ""
            elif in_rpt_section and trimmed.startswith("#") and not _is_rpt_continuation_heading(line, current_section_title):
                flush_table(current_table_lines, current_section_title, current_category)
                current_table_lines = []
                in_rpt_section = False
                current_section_title = ""
                current_category = ""

            if in_rpt_section:
                if trimmed.startswith("|") and trimmed.endswith("|"):
                    current_table_lines.append((line_num, trimmed))
                else:
                    if current_table_lines:
                        flush_table(current_table_lines, current_section_title, current_category)
                        current_table_lines = []
                    if trimmed and not trimmed.startswith("|") and len(trimmed) < 120:
                        if is_category_header_row(trimmed):
                            current_category = trimmed

        if current_table_lines:
            flush_table(current_table_lines, current_section_title, current_category)

        # Aggregate related-party balances are commonly printed in the
        # balance sheet, outside the VAS-26 note heading.  They are still
        # valid GIL denominators, but the normal table parser must not treat
        # the accounting label as a counterparty.  Parse only rows with an
        # explicit related-party marker and keep the evidence line intact.
        existing_lines = {frame.evidence.line_start for frame in frames}
        for line_num, line in enumerate(lines, 1):
            if line_num in existing_lines or not line.strip().startswith("|"):
                continue
            folded = fold_text(line)
            if "ben lien quan" not in folded:
                continue
            if not any(term in folded for term in ("phai thu", "phai tra", "von co phan", "von gop")):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            label = cells[0]
            if not label or is_category_header_row(label) is False and not any(
                term in fold_text(label) for term in ("phai thu", "phai tra", "von co phan", "von gop")
            ):
                continue
            raw_amount = next((cell for cell in cells[1:] if _parse_rpt_amount(cell) is not None), None)
            if raw_amount is None:
                continue
            parsed_amount = _parse_rpt_amount(raw_amount)
            if parsed_amount is None:
                continue
            local_scale = detect_unit_scale("\n".join(lines[max(0, line_num - 8) : line_num + 1]))
            effective_scale = 1.0 if abs(parsed_amount) >= 10_000_000.0 else local_scale
            folded_label = fold_text(label)
            # A balance-sheet row such as "không phải là bên liên quan" is a
            # non-related subtotal. It must not enter the related-party
            # denominator as an aggregate exposure.
            if "khong phai" in folded_label and "lien quan" in folded_label:
                continue
            if "phai tra" in folded_label:
                flow_kind = "payable_balance"
            elif "phai thu" in folded_label:
                flow_kind = "receivable_balance"
            else:
                flow_kind = "ownership_value"
            frames.append(
                RelatedPartyFlowFrame(
                    subject=self.issuer_ticker or "ISSUER",
                    object="RELATED_PARTIES_AGGREGATE",
                    relationship="RELATED_PARTY_AGGREGATE",
                    relation_type=RelationType.TRANSACTS_WITH.value,
                    flow_kind=flow_kind,
                    amount_vnd=abs(parsed_amount) * effective_scale,
                    transaction_description=label,
                    evidence=EvidenceReference(line_start=line_num, line_end=line_num, quote=line.strip()),
                )
            )

        return frames

    def _parse_single_table(
        self,
        table_lines: list[tuple[int, str]],
        section_title: str,
        active_category: str,
        doc_scale: float,
        footnotes: dict[str, Any],
    ) -> list[RelatedPartyFlowFrame]:
        if len(table_lines) < 2:
            return []

        frames: list[RelatedPartyFlowFrame] = []
        header_cells = [c.strip() for c in table_lines[0][1].strip("|").split("|")]
        data_rows = table_lines[1:]
        if data_rows and all(set(c.strip()).issubset({"-", ":", " "}) for c in data_rows[0][1].strip("|").split("|")):
            data_rows = data_rows[1:]

        local_scale = doc_scale
        header_text = " ".join(header_cells)
        tbl_text = header_text + " " + section_title
        folded_tbl = fold_text(tbl_text)
        cleaned_tbl = re.sub(r"\b(cong ty|ty le|ty gia|trieu tap)\b", " ", folded_tbl)

        unit_match = re.search(r"\b(don vi tinh|dvt)\s*:\s*([^\n|]+)", cleaned_tbl)
        if unit_match:
            unit_val = unit_match.group(2)
            if "trieu" in unit_val:
                local_scale = 1_000_000.0
            elif re.search(r"\b(ty|billion)\b", unit_val):
                local_scale = 1_000_000_000.0
            elif "dong" in unit_val or "vnd" in unit_val:
                local_scale = 1.0
        elif re.search(r"\b(trieu dong|trieu vnd|million)\b", cleaned_tbl):
            local_scale = 1_000_000.0
        elif re.search(r"\b(ty dong|ty vnd|billion)\b", cleaned_tbl):
            local_scale = 1_000_000_000.0
        elif "dong" in cleaned_tbl or "vnd" in cleaned_tbl:
            local_scale = 1.0

        folded_header = fold_text(header_text)
        sample_rows_text = fold_text(" ".join(line_str for _, line_str in data_rows[:6]))

        # Classify table kind based strictly on table header and contents, NOT the entire note's section_title
        has_balance_header = any(
            k in folded_header
            for k in ["so cuoi", "cuoi ky", "cuoi nam", "so dau", "dau ky", "dau nam"]
        )
        has_flow_header = any(
            k in folded_header
            for k in ["ky nay", "ky truoc", "nam nay", "nam truoc", "phat sinh", "gia tri giao dich"]
        )

        if has_balance_header and not has_flow_header:
            is_balance_table = True
        elif has_flow_header and not has_balance_header:
            is_balance_table = False
        else:
            if any(k in sample_rows_text for k in ["doanh thu", "ban hang", "mua hang", "hang mua", "cung cap", "chi phi", "giao dich chu yeu"]):
                is_balance_table = False
            elif any(k in sample_rows_text for k in ["so du", "phai thu", "phai tra", "tien gui co ky han", "du no"]):
                is_balance_table = True
            else:
                is_balance_table = False

        party_col = -1
        rel_col = -1
        desc_col = -1
        rate_col = -1
        maturity_col = -1
        amount_cols: list[int] = []

        for idx, h in enumerate(header_cells):
            hf = fold_text(h)
            if any(k in hf for k in ["ben lien quan", "doi tuong", "cong ty", "ten"]):
                if party_col == -1:
                    party_col = idx
            elif any(k in hf for k in ["moi quan he", "quan he"]):
                rel_col = idx
            elif any(k in hf for k in ["noi dung", "nghiep vu", "giao dich", "dien giai"]):
                desc_col = idx
            elif any(k in hf for k in ["lai suat", "rate"]):
                rate_col = idx
            elif any(k in hf for k in ["ngay dao han", "ky han", "maturity"]):
                maturity_col = idx
            elif classify_temporal_column(h) is not None or bool(re.search(r"\b20\d{2}\b", hf)) or any(
                k in hf
                for k in [
                    "so cuoi nam",
                    "so dau nam",
                    "nam nay",
                    "nam truoc",
                    "so du",
                    "vnd",
                    "cuoi ky",
                    "dau ky",
                    "ky nay",
                    "ky truoc",
                    "gia tri",
                    "so tien",
                ]
            ):
                amount_cols.append(idx)

        # Prioritize Current Period amount columns over Previous Period columns
        current_cols = [ac for ac in amount_cols if classify_temporal_column(header_cells[ac]) == "CURRENT_PERIOD"]
        other_cols = [ac for ac in amount_cols if ac not in current_cols]
        ordered_amount_cols = current_cols + other_cols

        explicit_party_column = party_col != -1
        if party_col == -1:
            party_col = 0 if len(header_cells) > 1 else -1

        current_category = active_category
        frames: list[RelatedPartyFlowFrame] = []

        # Keep entity detail rows and suppress a parent total after "Trong đó"
        # so the same disclosure is not counted twice.
        suppressed_parent_indices: set[int] = set()

        for line_num, line_str in data_rows:
            cells = [c.strip() for c in line_str.strip("|").split("|")]
            if not cells or len(cells) < 2:
                continue

            # Check if cells[0] is just an index marker (e.g. i), ii), 1., -)
            start_idx = 0
            if len(cells) > 2 and re.match(r"^[0-9ivxa-z\*\.\(\)\-\s]+$", cells[0], re.I):
                start_idx = 1

            first_cell = cells[party_col] if party_col > start_idx and party_col < len(cells) else cells[start_idx]
            first_cell_clean = re.sub(r"^\s*([ivx0-9a-z]+[\)\.]|\-)\s*", "", first_cell, flags=re.I).strip()

            first_folded = fold_text(first_cell_clean)
            if first_folded == "trong do" or first_folded.startswith("trong do:"):
                if frames:
                    suppressed_parent_indices.add(len(frames) - 1)
                continue

            # Check if this cell is a transaction category header (Tree layout)
            if is_category_header_row(first_cell_clean):
                current_category = first_cell_clean
                continue

            # Check if this cell is a sub-breakdown header ("Trong đó:")
            # Strip "Trong đó: -", "Trong đó:", "- " from party name
            clean_counterparty = re.sub(r"^(trong do\s*[:\-]|\-)\s*", "", first_cell_clean, flags=re.I).strip()
            party_name = clean_counterparty
            if not _valid_counterparty(party_name, explicit_party_column=explicit_party_column):
                continue

            # Skip header repeats
            if fold_text(party_name) in {
                "ben lien quan", "doi tuong", "ten cong ty", "ten", "chuc danh",
                "stt", "noi dung", "nghiep vu", "giao dich", "moi quan he", "quan he", "don vi tinh"
            }:
                continue

            relationship_str = cells[rel_col] if rel_col != -1 and rel_col < len(cells) else ""

            trans_desc = ""
            if desc_col != -1 and desc_col < len(cells) and cells[desc_col]:
                trans_desc = cells[desc_col]
            elif not explicit_party_column:
                # Two-column related-party tables carry the transaction type
                # in a preceding category row; fall back to the row label.
                trans_desc = current_category or party_name
            elif current_category:
                trans_desc = current_category
            elif section_title:
                trans_desc = section_title

            relation_type, flow_kind = _classify_transaction_flow(trans_desc, is_balance_table=is_balance_table)

            # A ratio row is not a monetary flow, even if it appears while a
            # related-party note is still open.
            if any(cell.strip() == "%" for cell in cells) or (
                "/" in first_cell_clean and any("%" in cell for cell in cells)
            ):
                continue

            interest_rate = cells[rate_col] if rate_col != -1 and rate_col < len(cells) else None
            maturity_date = cells[maturity_col] if maturity_col != -1 and maturity_col < len(cells) else None

            amount_val: float | None = None
            if current_cols:
                # When explicit current period column(s) exist, strictly extract from them.
                # If the cell is "-" or "0" or empty, the balance/flow for current period is 0.0 VND!
                # NEVER fall back to previous period columns.
                for ac in current_cols:
                    if ac < len(cells):
                        cell_val = cells[ac].strip()
                        if cell_val in {"-", "0", "0.0", "", "nil", "null"}:
                            amount_val = 0.0
                            break
                        parsed_num = _parse_rpt_amount(cell_val)
                        if parsed_num is not None:
                            effective_scale = local_scale
                            if abs(parsed_num) >= 10_000_000.0 or bool(re.search(r"\d+[\.,]\d{3}[\.,]\d{3}", cell_val)):
                                effective_scale = 1.0
                            amount_val = abs(parsed_num) * effective_scale
                            break
            else:
                # Table has no explicit temporal headers: check other amount columns
                for ac in ordered_amount_cols:
                    if ac < len(cells):
                        cell_val = cells[ac].strip()
                        if cell_val in {"-", "0", "0.0", "", "nil", "null"}:
                            amount_val = 0.0
                            break
                        parsed_num = _parse_rpt_amount(cell_val)
                        if parsed_num is not None:
                            effective_scale = local_scale
                            if abs(parsed_num) >= 10_000_000.0 or bool(re.search(r"\d+[\.,]\d{3}[\.,]\d{3}", cell_val)):
                                effective_scale = 1.0
                            amount_val = abs(parsed_num) * effective_scale
                            break

            found_markers = [canonicalize_marker(m) for m in _MARKER_IN_CELL_RE.findall(line_str)]
            footnote_note = None
            if found_markers:
                fn_obj = footnotes.get(found_markers[0])
                if fn_obj:
                    footnote_note = fn_obj.text

            subject = self.issuer_ticker or "ISSUER"
            counterpart = party_name

            frame = RelatedPartyFlowFrame(
                subject=subject,
                object=counterpart,
                relationship=relationship_str or "RELATED_PARTY",
                relation_type=relation_type,
                flow_kind=flow_kind,
                amount_vnd=amount_val or 0.0,
                transaction_description=trans_desc,
                interest_rate=interest_rate,
                maturity_date=maturity_date,
                collateral_note=footnote_note,
                evidence=EvidenceReference(
                    line_start=line_num,
                    line_end=line_num,
                    quote=line_str,
                ),
            )

            # Hierarchical reconciliation for sub-items under "Trong đó:"
            frames.append(frame)

        final_frames = [
            frame
            for index, frame in enumerate(frames)
            if index not in suppressed_parent_indices
            and (frame.amount_vnd > 0 or not (frame.evidence and frame.evidence.quote and frame.evidence.quote.strip().startswith("-")))
        ]
        return final_frames
