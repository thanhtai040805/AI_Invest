from __future__ import annotations

import re
from typing import Any

from sag_api.extraction.frames.base import EvidenceReference
from sag_api.extraction.frames.gil_frames import GovernanceInsiderFrame
from sag_api.extraction.parsers.accounting_taxonomy import (
    fold_text,
    parse_vn_percentage,
    parse_vn_number,
)


def _clean_person_name(raw: str) -> str:
    """Clean person name from titles, English translations, and serial numbers."""
    cleaned = re.sub(r"^\s*([0-9ivxa-z]+[\)\.]|\-)\s*", "", raw, flags=re.I).strip()
    # If name has slash separating Vietnamese and English (e.g. Phạm Nhật Vượng/Pham Nhat Vuong)
    if "/" in cleaned:
        cleaned = cleaned.split("/")[0].strip()
    # OCR frequently glues the Vietnamese name to its English translation:
    # "Nguyen Van A Mr. Nguyen Van A".
    cleaned = re.split(r"(?:mr|ms|mrs|miss)\.?\s*", cleaned, maxsplit=1, flags=re.I)[0].strip()
    return cleaned


def _classify_role(role_text: str) -> str:
    """Classify corporate governance role."""
    folded = fold_text(role_text)
    if "chu tich hdqt" in folded or "chu tich hoi dong" in folded or "chairman" in folded:
        return "CHAIRMAN"
    if "pho chu tich" in folded or "vice chairman" in folded:
        return "VICE_CHAIRMAN"
    if "doc lap" in folded or "independent" in folded:
        return "INDEPENDENT_BOD_MEMBER"
    if "truong ban kiem soat" in folded or "head of bos" in folded or "truong bks" in folded:
        return "CHIEF_SUPERVISOR"
    if "kiem soat" in folded or "bks" in folded or "supervisor" in folded:
        return "SUPERVISOR"
    if "tong giam doc" in folded or "ceo" in folded:
        return "CEO"
    if "pho tong giam doc" in folded:
        return "DEPUTY_CEO"
    if "thanh vien hdqt" in folded or "tv hdqt" in folded or "uy vien hdqt" in folded or "member of bod" in folded:
        return "BOD_MEMBER"
    return "INSIDER"


def _looks_like_person_name(value: str) -> bool:
    folded = fold_text(value)
    if any(token in folded for token in ("cong ty", "joint stock", "jsc", "llc", "inc", "ltd", "group", "subsidiary")):
        return False
    if folded in {"tong giam doc", "pho tong giam doc", "ke toan truong", "nguoi thuc hien giao dich", "nguoi duoc uy quyen cbtt", "khong co", "none", "n a"}:
        return False
    return bool(re.search(r"[a-zA-ZÀ-ỹ]", value))


class GovernanceTableParser:
    """Deterministic parser for Corporate Governance reports under Form TT96 / Circular 96/2020/TT-BTC."""

    def __init__(self, issuer_ticker: str = "") -> None:
        self.issuer_ticker = issuer_ticker.upper().strip()

    def parse_governance_tables(self, markdown_text: str) -> list[GovernanceInsiderFrame]:
        """Extract all governance insider frames from markdown text."""
        lines = markdown_text.splitlines()
        frames: list[GovernanceInsiderFrame] = []

        # Find all markdown tables in the document
        tables: list[list[tuple[int, str]]] = []
        current_table: list[tuple[int, str]] = []

        for line_num, line in enumerate(lines, 1):
            trimmed = line.strip()
            if trimmed.startswith("|") and trimmed.endswith("|"):
                current_table.append((line_num, trimmed))
            else:
                if current_table:
                    tables.append(current_table)
                    current_table = []

        if current_table:
            tables.append(current_table)

        # Parse each table
        for tbl in tables:
            parsed = self._parse_single_governance_table(tbl, lines)
            frames.extend(parsed)

        return frames

    def _parse_single_governance_table(
        self, table_lines: list[tuple[int, str]], all_lines: list[str]
    ) -> list[GovernanceInsiderFrame]:
        if len(table_lines) < 2:
            return []

        start_line = table_lines[0][0]
        context_start = max(0, start_line - 15)
        context_text = " ".join(all_lines[context_start:start_line])
        context_folded = fold_text(context_text)

        header_cells = [c.strip() for c in table_lines[0][1].strip("|").split("|")]
        header_folded = fold_text(" ".join(header_cells))
        has_role_or_share_column = any(
            any(k in fold_text(header) for k in ("chuc vu", "position", "so co phieu", "shares", "ty le", "percentage"))
            for header in header_cells
        )
        if (
            any("to chuc" in fold_text(header) and "giao dich" in fold_text(header) for header in header_cells)
            and not has_role_or_share_column
        ):
            return []

        has_person_columns = any(
            any(k in fold_text(header) for k in ["ho ten", "ho va ten", "name", "thanh vien", "chuc vu", "position", "so co phieu"])
            for header in header_cells
        )
        # A nearby governance heading is only useful when the table itself has
        # person-shaped columns.  This prevents resolution/attendance tables
        # from inheriting the previous board heading.
        is_bod_table = has_person_columns and any(k in header_folded or k in context_folded for k in ["thanh vien hdqt", "hoi dong quan tri", "board of directors"])
        is_bos_table = has_person_columns and any(k in header_folded or k in context_folded for k in ["thanh vien bks", "ban kiem soat", "board of supervisors"])
        is_insider_table = has_person_columns and any(k in header_folded or k in context_folded for k in ["nguoi noi bo", "nguoi co lien quan", "danh sach nguoi noi bo", "giao dich co phieu"])

        if not (is_bod_table or is_bos_table or is_insider_table):
            return []

        frames: list[GovernanceInsiderFrame] = []
        data_rows = table_lines[1:]
        if data_rows and all(set(c.strip()).issubset({"-", ":", " "}) for c in data_rows[0][1].strip("|").split("|")):
            data_rows = data_rows[1:]

        # Identify column positions
        name_col = -1
        role_col = -1
        rel_col = -1
        shares_col = -1
        pct_col = -1

        for idx, h in enumerate(header_cells):
            hf = fold_text(h)
            if any(k in hf for k in ["thanh vien", "ho ten", "ho va ten", "name", "nguoi thuc hien"]):
                if name_col == -1:
                    name_col = idx
            elif any(k in hf for k in ["chuc vu", "position"]):
                role_col = idx
            elif any(k in hf for k in ["moi quan he", "quan he voi nguoi noi bo", "relationship"]):
                rel_col = idx
            elif any(k in hf for k in ["so co phieu", "shares", "so cp"]):
                shares_col = idx
            elif any(k in hf for k in ["ty le so huu", "ownership", "ownership percentage"]):
                pct_col = idx

        if name_col == -1:
            name_col = 1 if len(header_cells) > 2 and re.match(r"^[0-9ivxa-z\*\.\(\)\-\s]+$", header_cells[0], re.I) else 0

        if role_col == -1 and shares_col == -1 and pct_col == -1:
            return []

        current_insider_anchor = ""

        for line_num, line_str in data_rows:
            cells = [c.strip() for c in line_str.strip("|").split("|")]
            if not cells or len(cells) < 2:
                continue

            start_idx = 0
            if len(cells) > 2 and re.match(r"^[0-9ivxa-z\*\.\(\)\-\s]+$", cells[0], re.I):
                start_idx = 1

            col_to_use = name_col if name_col >= start_idx and name_col < len(cells) else start_idx
            raw_name = cells[col_to_use]
            cleaned_name = _clean_person_name(raw_name)

            folded_name = fold_text(cleaned_name)
            if (
                not _looks_like_person_name(cleaned_name)
                or re.match(r"^(?:so|phien)\b", folded_name)
                or folded_name in {"tong", "cong", "tong cong", "total", "cong lai", "all"}
                or folded_name.startswith("tong cong")
                or folded_name.startswith("cong ")
                or any(
                    w in folded_name
                    for w in [
                        "thanh vien hdqt",
                        "thanh vien bks",
                        "nghi quyet",
                        "quyet dinh",
                        "dai hoi",
                        "bien ban",
                        "nam 20",
                        "bao cao",
                        "board of",
                        "members of",
                    ]
                )
            ):
                continue

            # Role & relationship
            raw_role = cells[role_col] if role_col != -1 and role_col < len(cells) else ""
            raw_rel = cells[rel_col] if rel_col != -1 and rel_col < len(cells) else ""
            person_title = fold_text(cleaned_name).startswith(("ong ", "ba ", "mr ", "ms ", "mrs "))
            relation_folded = fold_text(f"{raw_role} {raw_rel}")
            if not person_title and any(
                token in relation_folded for token in ("cong ty", "subsidiary", "related organization", "organization")
            ):
                continue

            # Check if this person is a primary insider (has corporate role)
            if raw_role and not raw_rel:
                current_insider_anchor = cleaned_name

            role_position = _classify_role(raw_role) if raw_role else ("SUPERVISOR" if is_bos_table else "INSIDER")

            # Ownership %
            pct_val = 0.0
            if pct_col != -1 and pct_col < len(cells):
                parsed_pct = parse_vn_percentage(cells[pct_col])
                if parsed_pct is not None:
                    pct_val = parsed_pct
            # Shares held
            shares_held = 0
            if shares_col != -1 and shares_col < len(cells):
                parsed_shares = parse_vn_number(cells[shares_col])
                if parsed_shares is not None:
                    shares_held = int(abs(parsed_shares))

            related_to = None
            relationship_kind = None
            if raw_rel:
                related_to = current_insider_anchor if current_insider_anchor and current_insider_anchor != cleaned_name else None
                relationship_kind = raw_rel.split("/")[0].strip()

            frame = GovernanceInsiderFrame(
                person_name=cleaned_name,
                issuer_ticker=self.issuer_ticker or "ISSUER",
                role_position=role_position,
                related_to_insider=related_to,
                relationship_kind=relationship_kind,
                shares_held=shares_held,
                ownership_pct=round(pct_val, 8),
                evidence=EvidenceReference(
                    line_start=line_num,
                    line_end=line_num,
                    quote=line_str,
                ),
            )
            frames.append(frame)

        return frames
