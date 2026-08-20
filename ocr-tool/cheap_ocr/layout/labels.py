"""Shared PP-DocLayoutV3 label space and post-processing helpers.

Both the PyTorch detector (:mod:`cheap_ocr.layout.torch`) and the ONNX/TensorRT
detector (:mod:`cheap_ocr.layout.onnx`) target PP-DocLayoutV3 and so share the
same 25-class label space, label→task mapping, bbox normalization, and
empty-page fallback. They live here so neither detector imports the other; in
particular the lightweight ONNX backend avoids pulling in torch/transformers.
"""

from typing import Any

from cheap_ocr.models import Region, Task

LAYOUT_MODEL = "PaddlePaddle/PP-DocLayoutV3_safetensors"
"""Fixed layout model. Post-processing and task mapping are purpose-built for it."""

ID2LABEL: dict[int, str] = {
    0: "abstract",
    1: "algorithm",
    2: "aside_text",
    3: "chart",
    4: "content",
    5: "display_formula",
    6: "doc_title",
    7: "figure_title",
    8: "footer",
    9: "footer_image",
    10: "footnote",
    11: "formula_number",
    12: "header",
    13: "header_image",
    14: "image",
    15: "inline_formula",
    16: "number",
    17: "paragraph_title",
    18: "reference",
    19: "reference_content",
    20: "seal",
    21: "table",
    22: "text",
    23: "vertical_text",
    24: "vision_footnote",
}

LABEL_TASK_MAPPING: dict[str, set[str]] = {
    "text": {
        "abstract",
        "algorithm",
        "content",
        "doc_title",
        "figure_title",
        "formula_number",
        "paragraph_title",
        "reference_content",
        "seal",
        "text",
        "vertical_text",
        "vision_footnote",
    },
    "formula": {"display_formula", "inline_formula", "formula"},
    "table": {"table"},
    "skip": {"chart", "image"},
    "abandon": {
        "aside_text",
        "footer",
        "footer_image",
        "footnote",
        "header",
        "header_image",
        "number",
        "reference",
    },
}


def task_for_label(label: str) -> Task | None:
    """Map a layout label to an OCR task, or None for abandoned regions."""
    for task_type, labels in LABEL_TASK_MAPPING.items():
        if label in labels:
            return None if task_type == "abandon" else Task(task_type)
    return Task.TEXT


def normalize_bbox(box: Any, width: int, height: int) -> tuple[int, int, int, int]:
    """Scale an ``(x1, y1, x2, y2)`` pixel box into the 0–1000 normalized space."""
    x1, y1, x2, y2 = (float(value) for value in box)
    return (
        max(0, min(1000, round(x1 / width * 1000))),
        max(0, min(1000, round(y1 / height * 1000))),
        max(0, min(1000, round(x2 / width * 1000))),
        max(0, min(1000, round(y2 / height * 1000))),
    )


def default_page_regions(page_index: int) -> list[Region]:
    """Return the single full-page text region used when detection finds nothing."""
    return [
        Region(
            page_index=page_index,
            region_index=0,
            label="text",
            task_type=Task.TEXT,
            bbox_2d=(0, 0, 1000, 1000),
            score=1.0,
        )
    ]
