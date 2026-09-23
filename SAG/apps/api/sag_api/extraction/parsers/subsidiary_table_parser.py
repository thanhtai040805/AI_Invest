from __future__ import annotations

import re
from typing import Any

from sag_api.extraction.frames.base import EvidenceReference
from sag_api.extraction.frames.gil_frames import CorporateOwnershipFrame
from sag_api.extraction.parsers.accounting_taxonomy import (
    fold_text,
    parse_vn_percentage,
    parse_vn_number,
)

NON_SUBSIDIARY_NAMES = {
    "tong cong",
    "cong",
    "total",
    "dau tu vao cong ty con",
    "dau tu vao cac cong ty con",
    "dau tu vao cong ty lien ket",
    "dau tu gop von vao don vi khac",
    "cong ty con",
    "cong ty lien ket",
    "du phong giam gia",
    "du phong khoan dau tu dai han",
    "khac",
    "gia goc",
    "gia tri hop ly",
    "du phong",
    "nganh kinh doanh",
    "hoat dong chinh",
    "ten cong ty",
    "cong ty",
}


def is_valid_company_name(name: str) -> bool:
    """Validate that an extracted entity is a bona fide corporate entity, not an individual or accounting line."""
    folded = fold_text(name)
    if not name or len(name) < 3:
        return False
    if folded.startswith(("ong ", "ba ", "mr ", "ms ")):
        return False
    if folded in NON_SUBSIDIARY_NAMES:
        return False
    if any(item in folded for item in [
        "tai san", "no phai tra", "von chu so huu", "tien mat", "tien gui",
        "hang ton kho", "chi phi", "doanh thu", "tong cong", "du phong",
        "gia tri", "khau hao", "loi nhuan", "lai vay",
    ]):
        return False
    # Must contain alphabetic characters (not purely numeric/symbolic)
    return bool(re.search(r"[a-zA-ZÀ-ỹ]", name))


class SubsidiaryTableParser:
    """Deterministic parser for corporate ownership structures, subsidiaries, and associates."""

    def __init__(self, issuer_ticker: str = "") -> None:
        self.issuer_ticker = issuer_ticker.upper().strip()

    def parse_subsidiary_tables(self, markdown_text: str) -> list[CorporateOwnershipFrame]:
        """Extract all corporate ownership frames from markdown text."""
        lines = markdown_text.splitlines()
        frames: list[CorporateOwnershipFrame] = []

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

        # Parse each table against subsidiary patterns
        for tbl in tables:
            parsed = self._parse_candidate_table(tbl, lines)
            frames.extend(parsed)

        # Deduplicate frames by child_company
        seen: dict[str, CorporateOwnershipFrame] = {}
        for f in frames:
            key = fold_text(f.child_company)
            if key not in seen or (f.ownership_pct > seen[key].ownership_pct):
                seen[key] = f

        return list(seen.values())

    def _parse_candidate_table(
        self, table_lines: list[tuple[int, str]], all_lines: list[str]
    ) -> list[CorporateOwnershipFrame]:
        if len(table_lines) < 2:
            return []

        # Check surrounding context (previous 15 lines)
        start_line = table_lines[0][0]
        context_start = max(0, start_line - 15)
        context_text = " ".join(all_lines[context_start:start_line])
        context_folded = fold_text(context_text)

        header_cells = [c.strip() for c in table_lines[0][1].strip("|").split("|")]
        header_text = " ".join(header_cells)
        header_folded = fold_text(header_text)

        # Check if table or context relates to subsidiaries
        is_sub_context = any(
            kw in context_folded
            for kw in [
                "phu luc 1",
                "danh sach cong ty con",
                "dau tu vao cac cong ty con",
                "dau tu vao cong ty con",
                "dau tu vao cong ty lien ket",
                "dau tu tai chinh dai han",
                "von gop lien doanh",
            ]
        )

        has_sub_row = any(
            "dau tu vao cong ty con" in fold_text(line_str) or "cong ty con" in fold_text(line_str)
            for _, line_str in table_lines
        )

        if not (is_sub_context or has_sub_row):
            return []

        frames: list[CorporateOwnershipFrame] = []
        data_rows = table_lines[1:]
        if data_rows and all(set(c.strip()).issubset({"-", ":", " "}) for c in data_rows[0][1].strip("|").split("|")):
            data_rows = data_rows[1:]

        # Identify column positions
        name_col = -1
        rel_col = -1
        pct_col = -1
        voting_col = -1

        for idx, h in enumerate(header_cells):
            hf = fold_text(h)
            if any(k in hf for k in ["ten cong ty", "cong ty", "don vi"]):
                if name_col == -1:
                    name_col = idx
            elif any(k in hf for k in ["moi quan he", "quan he"]):
                rel_col = idx
            elif any(k in hf for k in ["ty le bieu quyet"]):
                voting_col = idx
            elif any(k in hf for k in ["ty le loi ich", "ty le von gop", "% so huu", "ty le so huu", "ty le (%)", "ty le"]):
                if pct_col == -1:
                    pct_col = idx

        if name_col == -1:
            name_col = 1 if len(header_cells) > 2 and re.match(r"^[0-9ivxa-z\*\.\(\)\-\s]+$", header_cells[0], re.I) else 0

        active_mode = "DEFAULT"
        if "dau tu vao cong ty lien ket" in context_folded or "von gop lien doanh" in context_folded:
            active_mode = "ASSOCIATE"
        elif is_sub_context:
            active_mode = "SUBSIDIARY"

        for line_num, line_str in data_rows:
            cells = [c.strip() for c in line_str.strip("|").split("|")]
            if not cells or len(cells) < 2:
                continue

            first_folded = fold_text(cells[0])
            all_row_folded = fold_text(line_str)

            # Check mode switches in investment tables
            if "dau tu vao cong ty con" in first_folded:
                active_mode = "SUBSIDIARY"
                continue
            if "dau tu vao cong ty lien ket" in first_folded or "dau tu vao cong ty lien doanh" in first_folded:
                active_mode = "ASSOCIATE"
                continue
            if "dau tu gop von vao don vi khac" in first_folded:
                active_mode = "OTHER"
                continue

            if active_mode == "OTHER":
                continue

            # Check if index marker is in cell 0
            start_idx = 0
            if len(cells) > 2 and re.match(r"^[0-9ivxa-z\*\.\(\)\-\s]+$", cells[0], re.I):
                start_idx = 1

            col_to_use = name_col if name_col >= start_idx and name_col < len(cells) else start_idx
            raw_name = cells[col_to_use]
            cleaned_name = re.sub(r"^\s*([0-9ivxa-z]+[\)\.]|\-)\s*", "", raw_name, flags=re.I).strip()

            if not is_valid_company_name(cleaned_name):
                continue

            # Check relationship column if present
            status = active_mode
            if rel_col != -1 and rel_col < len(cells):
                rel_val = fold_text(cells[rel_col])
                if "cong ty con" in rel_val:
                    status = "SUBSIDIARY"
                elif "lien ket" in rel_val or "lien doanh" in rel_val:
                    status = "ASSOCIATE"
                else:
                    continue  # Skip key management, other parties

            # Extract percentages
            pct_val = 0.0
            if pct_col != -1 and pct_col < len(cells):
                parsed_pct = parse_vn_percentage(cells[pct_col])
                if parsed_pct is not None:
                    pct_val = parsed_pct

            voting_val = pct_val
            if voting_col != -1 and voting_col < len(cells):
                parsed_voting = parse_vn_percentage(cells[voting_col])
                if parsed_voting is not None:
                    voting_val = parsed_voting

            if pct_val == 0.0:
                for c in cells[start_idx + 1:]:
                    if "%" in c:
                        parsed_pct = parse_vn_percentage(c)
                        if parsed_pct is not None and 0.0 < parsed_pct <= 100.0:
                            pct_val = parsed_pct
                            voting_val = parsed_pct
                            break

            # A table role is not evidence of an exact ownership percentage.
            # Keep the percentage unknown unless a percent cell was present.
            consolidation_status = "SUBSIDIARY" if status == "SUBSIDIARY" else "ASSOCIATE"

            frame = CorporateOwnershipFrame(
                parent_company=self.issuer_ticker or "ISSUER",
                child_company=cleaned_name,
                ownership_pct=round(pct_val, 2),
                voting_power_pct=round(voting_val, 2),
                consolidation_status=consolidation_status,
                evidence=EvidenceReference(
                    line_start=line_num,
                    line_end=line_num,
                    quote=line_str,
                ),
            )
            frames.append(frame)

        return frames
