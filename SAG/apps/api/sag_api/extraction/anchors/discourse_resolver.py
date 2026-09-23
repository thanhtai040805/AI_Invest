from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Regex to detect footnotes markers like (i), (ii), (iii), (1), (2), (*), (**)
_MARKER_IN_CELL_RE = re.compile(
    r"\(\s*([ivxlcdm]+|\*+|[0-9]+|[a-z])\s*\)", re.IGNORECASE
)
_MARKER_PARAGRAPH_START_RE = re.compile(
    r"^\s*(?:\(([ivxlcdm]+|\*+|[0-9]+|[a-z])\)|\b([ivxlcdm]+|\*+|[0-9]+|[a-z])\))\s+(.+)",
    re.IGNORECASE,
)


def canonicalize_marker(raw: str) -> str:
    """Normalize footnote markers to lowercase stripped format (e.g., '  ( ii ) ' -> 'ii')."""
    cleaned = raw.strip()
    match = re.search(r"([ivxlcdm]+|\*+|[0-9]+|[a-z])", cleaned, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return cleaned.lower()


@dataclass
class FootnoteAnchor:
    marker: str
    line_no: int
    text: str


@dataclass
class CompositeAnalyticalUnit:
    """A dual analytical unit consisting of a table row anchor and its corresponding footnote explanation."""
    marker: str
    section_heading: str
    table_line_no: int
    table_headers: dict[int, str]
    table_cells: list[str]
    footnote_start_line: int
    footnote_end_line: int
    footnote_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DiscourseResolver:
    """Resolves anaphoric discourse links between table cells and footnote paragraphs."""

    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.lines = markdown.splitlines()

    def find_footnotes_in_range(self, start_line: int, end_line: int) -> dict[str, FootnoteAnchor]:
        """Scan text lines within a section range to find footnotes starting with (i), (*), etc."""
        footnotes: dict[str, FootnoteAnchor] = {}
        for line_idx in range(max(0, start_line - 1), min(len(self.lines), end_line)):
            line = self.lines[line_idx].strip()
            if not line or line.startswith("|") or line.startswith("#"):
                continue
            match = _MARKER_PARAGRAPH_START_RE.match(line)
            if match:
                marker = canonicalize_marker(match.group(1) or match.group(2))
                content = match.group(3).strip()
                # If paragraph continues across following non-empty, non-table lines, join them
                curr = line_idx + 1
                full_text = [content]
                while curr < min(len(self.lines), end_line):
                    next_line = self.lines[curr].strip()
                    if not next_line or next_line.startswith("|") or next_line.startswith("#") or _MARKER_PARAGRAPH_START_RE.match(next_line):
                        break
                    full_text.append(next_line)
                    curr += 1
                footnotes[marker] = FootnoteAnchor(
                    marker=marker,
                    line_no=line_idx + 1,
                    text=" ".join(full_text),
                )
        return footnotes

    def resolve_composite_units(
        self,
        table_rows: list[tuple[int, list[str], dict[int, str]]],
        section_heading: str,
        section_start: int,
        section_end: int,
    ) -> list[CompositeAnalyticalUnit]:
        """Pair table rows having markers (i), (ii), etc. with their corresponding footnotes."""
        footnotes = self.find_footnotes_in_range(section_start, section_end)
        if not footnotes:
            return []

        composite_units: list[CompositeAnalyticalUnit] = []
        for line_no, cells, headers in table_rows:
            row_text = " ".join(cells)
            found_markers = [canonicalize_marker(m) for m in _MARKER_IN_CELL_RE.findall(row_text)]
            for marker in found_markers:
                if marker in footnotes:
                    fn = footnotes[marker]
                    composite_units.append(
                        CompositeAnalyticalUnit(
                            marker=marker,
                            section_heading=section_heading,
                            table_line_no=line_no,
                            table_headers=headers,
                            table_cells=cells,
                            footnote_start_line=fn.line_no,
                            footnote_end_line=fn.line_no,
                            footnote_text=fn.text,
                            metadata={"matched_marker": marker},
                        )
                    )
        return composite_units
