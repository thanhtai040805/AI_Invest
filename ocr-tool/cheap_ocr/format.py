# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""GLM-OCR-compatible result formatting.

This file mirrors the formatting logic from
``glmocr/postprocess/result_formatter.py``. Its numerics and regexes must stay
byte-identical to upstream — output compatibility is a feature, and the golden
tests enforce it. ``format_document`` is deliberately synchronous: formatting is
pure string post-processing, and running the full-document logic in one piece
keeps behavior aligned with upstream page joining, image-region handling,
indexing, and post-processing order.
"""

import re
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from cheap_ocr.models import RecognizedRegion

try:  # Optional dependency; GLM-OCR uses it when present and falls back otherwise.
    from wordfreq import zipf_frequency
except Exception:  # pragma: no cover
    zipf_frequency = None


LABEL_VISUALIZATION_MAPPING: dict[str, list[str]] = {
    "image": ["chart", "image"],
    "table": ["table"],
    "formula": ["display_formula", "inline_formula"],
    "text": [
        "abstract",
        "algorithm",
        "content",
        "doc_title",
        "figure_title",
        "paragraph_title",
        "reference_content",
        "text",
        "vertical_text",
        "vision_footnote",
        "seal",
        "formula_number",
    ],
}


# ---------------------------------------------------------------------------
# Copied GLM-OCR result_postprocess_utils helpers
# ---------------------------------------------------------------------------


def find_consecutive_repeat(s: str, min_unit_len: int = 10, min_repeats: int = 10) -> str | None:
    """Return text truncated at a repeated span when one is detected."""
    n = len(s)
    if n < min_unit_len * min_repeats:
        return None

    max_unit_len = n // min_repeats
    if max_unit_len < min_unit_len:
        return None

    pattern = re.compile(
        r"(.{" + str(min_unit_len) + "," + str(max_unit_len) + r"}?)\1{" + str(min_repeats - 1) + ",}",
        re.DOTALL,
    )
    match = pattern.search(s)
    if match:
        return s[: match.start()] + match.group(1)
    return None


def clean_repeated_content(
    content: str,
    min_len: int = 10,
    min_repeats: int = 10,
    line_threshold: int = 10,
) -> str:
    """Remove pathological repeated OCR content."""
    stripped_content = content.strip()
    if not stripped_content:
        return content

    if len(stripped_content) > min_len * min_repeats:
        result = find_consecutive_repeat(stripped_content, min_unit_len=min_len, min_repeats=min_repeats)
        if result is not None:
            return result

    lines = [line.strip() for line in content.split("\n") if line.strip()]
    total_lines = len(lines)
    if total_lines >= line_threshold and lines:
        common, count = Counter(lines).most_common(1)[0]
        if count >= line_threshold and (count / total_lines) >= 0.8:
            for i, line in enumerate(lines):
                if line == common:
                    consecutive = sum(1 for j in range(i, min(i + 3, len(lines))) if lines[j] == common)
                    if consecutive >= 3:
                        original_lines = content.split("\n")
                        non_empty_count = 0
                        for idx, orig_line in enumerate(original_lines):
                            if orig_line.strip():
                                non_empty_count += 1
                                if non_empty_count == i + 1:
                                    return "\n".join(original_lines[: idx + 1])
                        break
    return content


def clean_formula_number(number_content: str) -> str:
    """Strip wrapping parentheses from a formula number."""
    number_clean = number_content.strip()
    if number_clean.startswith("(") and number_clean.endswith(")"):
        number_clean = number_clean[1:-1]
    elif number_clean.startswith("（") and number_clean.endswith("）"):
        number_clean = number_clean[1:-1]
    return number_clean


def normalize_inline_formula(content: str) -> str:
    """Normalize inline formula spacing around dollar delimiters."""
    inline_formula_re = re.compile(r"(?<!\$)\$\s*((?:[^$\\]|\\.)+?)\s*\$(?!\$)")
    if "$" not in content:
        return content

    parts: list[str] = []
    last_end = 0

    for m in inline_formula_re.finditer(content):
        formula = m.group(1).strip()
        if not formula:
            continue

        start, end = m.start(), m.end()
        before = content[last_end:start]

        if before and before[-1].isalnum():
            before += " "

        parts.append(before)
        parts.append(f"${formula}$")

        if end < len(content) and content[end].isalnum():
            parts.append(" ")

        last_end = end

    if not parts:
        return content

    parts.append(content[last_end:])
    return "".join(parts)


# ---------------------------------------------------------------------------
# GLM-OCR-compatible formatting (module functions; mirrors ResultFormatter.process)
# ---------------------------------------------------------------------------


def _process(grouped_results: list[list[dict[str, Any]]]) -> tuple[list[list[dict[str, Any]]], str]:
    """Process grouped results with GLM-OCR's original layout-mode logic."""
    json_final_results: list[list[dict[str, Any]]] = []

    for results in grouped_results:
        sorted_results = sorted(results, key=lambda x: x.get("index", 0))
        json_page_results = []
        valid_idx = 0

        for item in sorted_results:
            result = deepcopy(item)
            result["native_label"] = result.get("label", "text")

            result["label"] = _map_label(result["label"])

            result["content"] = _format_content(
                result["content"],
                result["label"],
                result["native_label"],
            )

            is_image_region = result["label"] == "image" or result.get("task_type") == "skip"
            if not is_image_region:
                content = result.get("content")
                if content is None or (isinstance(content, str) and content.strip() == ""):
                    continue

            result["index"] = valid_idx
            result["_is_image"] = is_image_region
            result.pop("task_type", None)
            result.pop("score", None)
            valid_idx += 1

            json_page_results.append(result)

        json_page_results = _merge_formula_numbers(json_page_results)
        json_page_results = _merge_text_blocks(json_page_results)
        json_page_results = _format_bullet_points(json_page_results)

        json_final_results.append(json_page_results)

    markdown_final_results = []
    for page_idx, json_page_results in enumerate(json_final_results):
        markdown_page_results = []
        for result in json_page_results:
            content = result["content"]
            if result.pop("_is_image", False):
                # Image regions stay in the layout JSON (with null content) but
                # produce no markdown; cheap-ocr does not extract crop files.
                continue
            elif content:
                markdown_page_results.append(content)
        page_number = page_idx + 1
        page_parts = [f"<!-- page:start {page_number} -->"]
        markdown_body = "\n\n".join(markdown_page_results)
        if markdown_body:
            page_parts.append(markdown_body)
        page_parts.append(f"<!-- page:end {page_number} -->")
        markdown_final_results.append("\n\n".join(page_parts))

    markdown_str = "\n\n".join(markdown_final_results)
    return json_final_results, markdown_str


def _clean_content(content: str | None) -> str:
    if content is None:
        return ""

    content = re.sub(r"^(\\t)+", "", content).lstrip()
    content = re.sub(r"(\\t)+$", "", content).rstrip()

    content = re.sub(r"(\.)\1{2,}", r"\1\1\1", content)
    content = re.sub(r"(·)\1{2,}", r"\1\1\1", content)
    content = re.sub(r"(_)\1{2,}", r"\1\1\1", content)
    content = re.sub(r"(\\_)\1{2,}", r"\1\1\1", content)

    if len(content) >= 2048:
        content = clean_repeated_content(content)

    content = normalize_inline_formula(content)
    return content.strip()


def _format_content(content: Any, label: str, native_label: str) -> str | None:
    if content is None:
        return content

    if label == "table":
        if content.startswith("<table") and content.endswith("</table>"):
            content = content.strip()
        else:
            content = _clean_content(str(content))
    elif label == "formula":
        if content.startswith("$$") and content.endswith("$$"):
            content = content.strip()
        else:
            content = _clean_content(str(content))
    else:
        content = _clean_content(str(content))

    # Title formatting: this is what the minimal formatter was missing.
    if native_label == "doc_title":
        content = re.sub(r"^#+\s*", "", content)
        content = "# " + content
    elif native_label == "paragraph_title":
        if content.startswith("- ") or content.startswith("* "):
            content = content[2:].lstrip()
        content = re.sub(r"^#+\s*", "", content)
        content = "## " + content.lstrip()

    if label == "formula":
        if content.startswith("$$") or content.startswith("\\[") or content.startswith("\\("):
            content = content[2:].strip()
        if content.endswith("$$") or content.endswith("\\]") or content.endswith("\\)"):
            content = content[:-2].strip()
        content = "$$\n" + content + "\n$$"

    if label == "text":
        if content.startswith("```") and (not content.endswith("```")):
            content = content + "\n```"

        if content.startswith("·") or content.startswith("•") or content.startswith("* "):
            content = "- " + content[1:].lstrip()

        match = re.match(r"^(\(|\（)(\d+|[A-Za-z])(\)|\）)(.*)$", content)
        if match:
            _, symbol, _, rest = match.groups()
            content = f"({symbol}) {rest.lstrip()}"

        match = re.match(r"^(\d+|[A-Za-z])(\.|\)|\）)(.*)$", content)
        if match:
            symbol, sep, rest = match.groups()
            sep = ")" if sep == "）" else sep
            content = f"{symbol}{sep} {rest.lstrip()}"

        content = re.sub(r"(?<!\n)\n(?!\n)", "\n\n", content)

    return content


def _map_label(label: str) -> str:
    if label in LABEL_VISUALIZATION_MAPPING.get("image", []):
        return "image"
    if label in LABEL_VISUALIZATION_MAPPING.get("text", []):
        return "text"
    if label in LABEL_VISUALIZATION_MAPPING.get("table", []):
        return "table"
    if label in LABEL_VISUALIZATION_MAPPING.get("formula", []):
        return "formula"
    return label


def _is_likely_valid_merged_word(merged_word: str) -> bool:
    token = merged_word.strip().lower()
    if not token:
        return False

    if zipf_frequency is not None:
        try:
            return zipf_frequency(token, "en") >= 2.5
        except Exception:
            pass

    if not re.fullmatch(r"[a-z][a-z'\-]{2,30}", token):
        return False
    if "--" in token or "''" in token:
        return False
    return True


def _merge_text_blocks(json_page_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not json_page_results:
        return json_page_results

    merged_results: list[dict[str, Any]] = []
    skip_indices: set[int] = set()

    for i, block in enumerate(json_page_results):
        if i in skip_indices:
            continue

        if block.get("label") != "text":
            merged_results.append(block)
            continue

        content = block.get("content", "")
        if not isinstance(content, str):
            merged_results.append(block)
            continue

        content_stripped = content.rstrip()
        if not content_stripped:
            merged_results.append(block)
            continue

        if not content_stripped.endswith("-"):
            merged_results.append(block)
            continue

        merged = False
        for j in range(i + 1, len(json_page_results)):
            if json_page_results[j].get("label") == "text":
                next_content = json_page_results[j].get("content", "")
                if isinstance(next_content, str):
                    next_stripped = next_content.lstrip()
                    if next_stripped and next_stripped[0].islower():
                        words_before = content_stripped[:-1].split()
                        next_words = next_stripped.split()

                        if words_before and next_words:
                            word_fragment_before = words_before[-1]
                            word_fragment_after = next_words[0]
                            merged_word = word_fragment_before + word_fragment_after

                            if _is_likely_valid_merged_word(merged_word):
                                merged_content = content_stripped[:-1] + next_content.lstrip()
                                merged_block = deepcopy(block)
                                merged_block["content"] = merged_content
                                merged_results.append(merged_block)
                                skip_indices.add(j)
                                merged = True
                        break

        if not merged:
            merged_results.append(block)

    for idx, block in enumerate(merged_results):
        block["index"] = idx
    return merged_results


def _format_bullet_points(
    json_page_results: list[dict[str, Any]], left_align_threshold: float = 10.0
) -> list[dict[str, Any]]:
    if len(json_page_results) < 3:
        return json_page_results

    for i in range(1, len(json_page_results) - 1):
        current_block = json_page_results[i]
        prev_block = json_page_results[i - 1]
        next_block = json_page_results[i + 1]

        if current_block.get("native_label") != "text":
            continue
        if prev_block.get("native_label") != "text" or next_block.get("native_label") != "text":
            continue

        current_content = current_block.get("content", "")
        if current_content.startswith("- "):
            continue

        prev_content = prev_block.get("content", "")
        next_content = next_block.get("content", "")
        if not (prev_content.startswith("- ") and next_content.startswith("- ")):
            continue

        current_bbox = current_block.get("bbox_2d", [])
        prev_bbox = prev_block.get("bbox_2d", [])
        next_bbox = next_block.get("bbox_2d", [])
        if not (current_bbox and prev_bbox and next_bbox):
            continue

        current_left = current_bbox[0]
        prev_left = prev_bbox[0]
        next_left = next_bbox[0]

        if (
            abs(current_left - prev_left) <= left_align_threshold
            and abs(current_left - next_left) <= left_align_threshold
        ):
            current_block["content"] = "- " + current_content

    return json_page_results


def _merge_formula_numbers(json_page_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not json_page_results:
        return json_page_results

    merged_results: list[dict[str, Any]] = []
    skip_indices: set[int] = set()

    for i, block in enumerate(json_page_results):
        if i in skip_indices:
            continue

        native_label = block.get("native_label", "")

        if native_label == "formula_number":
            if i + 1 < len(json_page_results):
                next_block = json_page_results[i + 1]
                if next_block.get("label") == "formula":
                    number_content = block.get("content", "").strip()
                    number_clean = clean_formula_number(number_content)
                    formula_content = next_block.get("content", "")
                    merged_block = deepcopy(next_block)
                    if formula_content.endswith("\n$$"):
                        merged_block["content"] = formula_content[:-3] + f" \\tag{{{number_clean}}}\n$$"
                    merged_results.append(merged_block)
                    skip_indices.add(i + 1)
                    continue
            continue

        if block.get("label") == "formula":
            if i + 1 < len(json_page_results):
                next_block = json_page_results[i + 1]
                if next_block.get("native_label") == "formula_number":
                    number_content = next_block.get("content", "").strip()
                    number_clean = clean_formula_number(number_content)
                    formula_content = block.get("content", "")
                    merged_block = deepcopy(block)
                    if formula_content.endswith("\n$$"):
                        merged_block["content"] = formula_content[:-3] + f" \\tag{{{number_clean}}}\n$$"
                    merged_results.append(merged_block)
                    skip_indices.add(i + 1)
                    continue
            merged_results.append(block)
            continue

        merged_results.append(block)

    for idx, block in enumerate(merged_results):
        block["index"] = idx
    return merged_results


def _region_to_original_dict(recognized: RecognizedRegion) -> dict[str, Any]:
    region = recognized.region
    result: dict[str, Any] = {
        "index": region.region_index,
        "label": region.label,
        "content": recognized.content,
        "bbox_2d": list(region.bbox_2d),
        # ``task_type`` drives the image-region check in ``_process`` (popped
        # there); ``score`` is popped unread, matching upstream's input shape.
        "task_type": str(region.task_type),
        "score": region.score,
    }
    if recognized.status_code is not None:
        result["_ocr_status_code"] = recognized.status_code
    if recognized.latency_ms is not None:
        result["_ocr_latency_ms"] = recognized.latency_ms
    if recognized.usage is not None:
        result["_ocr_usage"] = asdict(recognized.usage)
    return result


def format_document(
    page_count: int,
    regions: list[RecognizedRegion],
) -> tuple[list[list[dict[str, Any]]], str]:
    """Format a document's regions with the exact GLM-OCR ``ResultFormatter`` logic."""
    by_page: dict[int, list[RecognizedRegion]] = defaultdict(list)
    for region in regions:
        by_page[region.region.page_index].append(region)

    grouped_results = [
        [_region_to_original_dict(region) for region in by_page.get(page_idx, [])] for page_idx in range(page_count)
    ]
    return _process(grouped_results)
