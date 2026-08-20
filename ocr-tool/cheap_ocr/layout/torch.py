# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Single-process PP-DocLayoutV3 layout detection vendored from GLM-OCR."""

import logging
from typing import Any

import cv2
import numpy as np
import torch
from transformers import PPDocLayoutV3ForObjectDetection, PPDocLayoutV3ImageProcessor

from cheap_ocr.layout.labels import ID2LABEL, LAYOUT_MODEL, default_page_regions, normalize_bbox, task_for_label
from cheap_ocr.models import Page, Region

logger = logging.getLogger(__name__)

# Calibrated default for the PyTorch detector; the ONNX RT-DETR export uses 0.5.
SCORE_THRESHOLD = 0.3


def _iou(box1: Any, box2: Any) -> float:
    x1, y1, x2, y2 = box1
    x1_p, y1_p, x2_p, y2_p = box2
    x1_i = max(x1, x1_p)
    y1_i = max(y1, y1_p)
    x2_i = min(x2, x2_p)
    y2_i = min(y2, y2_p)
    inter_area = max(0, x2_i - x1_i + 1) * max(0, y2_i - y1_i + 1)
    box1_area = (x2 - x1 + 1) * (y2 - y1 + 1)
    box2_area = (x2_p - x1_p + 1) * (y2_p - y1_p + 1)
    return inter_area / float(box1_area + box2_area - inter_area)


def _nms(boxes: Any, iou_same: float = 0.6, iou_diff: float = 0.95) -> list[int]:
    scores = boxes[:, 1]
    indices = np.argsort(scores)[::-1]
    selected: list[int] = []
    while len(indices) > 0:
        current = int(indices[0])
        current_box = boxes[current]
        current_class = current_box[0]
        current_coords = current_box[2:]
        selected.append(current)
        indices = indices[1:]
        filtered = []
        for index in indices:
            box = boxes[index]
            threshold = iou_same if current_class == box[0] else iou_diff
            if _iou(current_coords, box[2:]) < threshold:
                filtered.append(index)
        indices = np.array(filtered)
    return selected


def _is_contained(box1: Any, box2: Any) -> bool:
    _, _, x1, y1, x2, y2 = box1
    _, _, x1_p, y1_p, x2_p, y2_p = box2
    box1_area = (x2 - x1) * (y2 - y1)
    xi1 = max(x1, x1_p)
    yi1 = max(y1, y1_p)
    xi2 = min(x2, x2_p)
    yi2 = min(y2, y2_p)
    intersect_area = max(0, xi2 - xi1) * max(0, yi2 - yi1)
    return bool(box1_area > 0 and intersect_area / box1_area >= 0.8)


def _containment_masks(boxes: Any, preserve_indices: set[int]) -> tuple[Any, Any]:
    contains_other = np.zeros(len(boxes), dtype=int)
    contained_by_other = np.zeros(len(boxes), dtype=int)
    for i in range(len(boxes)):
        for j in range(len(boxes)):
            if i == j or boxes[i][0] in preserve_indices:
                continue
            if _is_contained(boxes[i], boxes[j]):
                contained_by_other[i] = 1
                contains_other[j] = 1
    return contains_other, contained_by_other


def _apply_layout_postprocess(
    raw_results: list[dict[str, Any]],
    id2label: dict[int, str],
    image_sizes: list[tuple[int, int]],
    *,
    layout_nms: bool = True,
    layout_merge_bboxes_mode: str = "large",
) -> list[list[dict[str, Any]]]:
    all_labels = list(id2label.values())
    paddle_format_results: list[list[dict[str, Any]]] = []

    for image_index, result in enumerate(raw_results):
        scores = result["scores"].detach().cpu().numpy()
        labels = result["labels"].detach().cpu().numpy()
        boxes = result["boxes"].detach().cpu().numpy()
        order_seq = result.get("order_seq")
        orders = order_seq.detach().cpu().numpy() if order_seq is not None else np.arange(len(scores))
        image_width, image_height = image_sizes[image_index]

        boxes_with_order = [
            [int(labels[i]), float(scores[i]), *boxes[i].tolist(), int(orders[i])] for i in range(len(scores))
        ]
        if not boxes_with_order:
            paddle_format_results.append([])
            continue

        boxes_array = np.array(boxes_with_order)
        if layout_nms:
            boxes_array = boxes_array[_nms(boxes_array[:, :6], iou_same=0.6, iou_diff=0.98)]

        if len(boxes_array) > 1:
            image_label_id = all_labels.index("image") if "image" in all_labels else None
            max_image_area_ratio = 0.82 if image_width > image_height else 0.93
            image_area = image_width * image_height
            filtered_boxes = []
            for box in boxes_array:
                label_id, _, x1, y1, x2, y2 = box[:6]
                if label_id != image_label_id:
                    filtered_boxes.append(box)
                    continue
                box_area = (min(image_width, x2) - max(0, x1)) * (min(image_height, y2) - max(0, y1))
                if box_area <= max_image_area_ratio * image_area:
                    filtered_boxes.append(box)
            if filtered_boxes:
                boxes_array = np.array(filtered_boxes)

        if layout_merge_bboxes_mode == "large" and len(boxes_array) > 1:
            preserve_indices = {all_labels.index(label) for label in ("image", "seal", "chart") if label in all_labels}
            _, contained_by_other = _containment_masks(boxes_array[:, :6], preserve_indices)
            boxes_array = boxes_array[contained_by_other == 0]

        if len(boxes_array) == 0:
            paddle_format_results.append([])
            continue

        boxes_array = boxes_array[np.argsort(boxes_array[:, 6])]
        image_results: list[dict[str, Any]] = []
        for box_data in boxes_array:
            class_id = int(box_data[0])
            score = float(box_data[1])
            x1, y1, x2, y2 = box_data[2:6]
            order = int(box_data[6]) if box_data[6] > 0 else None
            label = id2label.get(class_id, f"class_{class_id}")
            x1 = max(0, min(float(x1), image_width))
            y1 = max(0, min(float(y1), image_height))
            x2 = max(0, min(float(x2), image_width))
            y2 = max(0, min(float(y2), image_height))
            if x1 >= x2 or y1 >= y2:
                continue

            image_results.append(
                {
                    "cls_id": class_id,
                    "label": label,
                    "score": score,
                    "coordinate": [int(x1), int(y1), int(x2), int(y2)],
                    "order": order,
                }
            )
        paddle_format_results.append(image_results)
    return paddle_format_results


class LayoutDetector:
    """Single PP-DocLayoutV3 detector instance for one GPU process."""

    def __init__(self, *, batch_size: int = 4, threshold: float | None = None, device: str = "cuda"):
        """Load the model and processor."""
        torch.set_num_threads(4)
        self.batch_size = max(1, int(batch_size))
        self.threshold = SCORE_THRESHOLD if threshold is None else float(threshold)
        self.device = device if device.startswith("cuda") and torch.cuda.is_available() else "cpu"
        if device.startswith("cuda") and not self.device.startswith("cuda"):
            raise RuntimeError(
                f"Transformers layout backend requested CUDA device {device!r}, but torch.cuda.is_available() is false"
            )
        self.image_processor = PPDocLayoutV3ImageProcessor.from_pretrained(LAYOUT_MODEL)
        self.model = PPDocLayoutV3ForObjectDetection.from_pretrained(LAYOUT_MODEL)
        self.model.eval().to(self.device)
        model_device = str(next(self.model.parameters()).device)
        if self.device.startswith("cuda") and not model_device.startswith("cuda"):
            raise RuntimeError(
                f"Transformers layout backend expected model on CUDA device {self.device!r}, got {model_device!r}"
            )
        self.active_device = model_device
        logger.info(
            "transformers_layout_detector_ready requested_device=%s active_device=%s",
            device,
            model_device,
        )
        self.id2label = self._id2label(getattr(self.model.config, "id2label", None))
        self._patch_empty_polygon_crash()

    @staticmethod
    def _id2label(value: Any) -> dict[int, str]:
        if isinstance(value, dict):
            return {int(key): str(label) for key, label in value.items()}
        return ID2LABEL

    def _patch_empty_polygon_crash(self) -> None:
        def safe_extract(boxes: Any, masks: Any, scale_ratio: Any) -> list[Any]:
            scale_w, scale_h = scale_ratio[0] / 4, scale_ratio[1] / 4
            mask_h, mask_w = masks.shape[1:]
            polygon_points = []
            for index in range(len(boxes)):
                x_min, y_min, x_max, y_max = boxes[index].astype(np.int32)
                box_w, box_h = x_max - x_min, y_max - y_min
                rect = np.array([[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]], dtype=np.float32)
                if box_w <= 0 or box_h <= 0:
                    polygon_points.append(rect)
                    continue
                x_start = int(round((x_min * scale_w).item()))
                x_end = int(round((x_max * scale_w).item()))
                y_start = int(round((y_min * scale_h).item()))
                y_end = int(round((y_max * scale_h).item()))
                x_start, x_end = np.clip([x_start, x_end], 0, mask_w)
                y_start, y_end = np.clip([y_start, y_end], 0, mask_h)
                cropped_mask = masks[index, y_start:y_end, x_start:x_end]
                if cropped_mask.size == 0:
                    polygon_points.append(rect)
                    continue
                resized = cv2.resize(cropped_mask.astype(np.uint8), (box_w, box_h), interpolation=cv2.INTER_NEAREST)
                polygon = self.image_processor._mask2polygon(resized)
                if polygon is None or len(polygon) < 4:
                    polygon_points.append(rect)
                else:
                    polygon_points.append(polygon + np.array([x_min, y_min]))
            return polygon_points

        self.image_processor._extract_polygon_points_by_masks = safe_extract

    def detect_pages(self, pages: list[Page]) -> dict[int, list[Region]]:
        """Detect layout regions for rendered PDF pages."""
        page_regions: dict[int, list[Region]] = {}
        for start in range(0, len(pages), self.batch_size):
            chunk_pages = pages[start : start + self.batch_size]
            chunk_images = [
                page.image.convert("RGB") if page.image.mode != "RGB" else page.image for page in chunk_pages
            ]
            inputs = self.image_processor(images=chunk_images, return_tensors="pt")
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with torch.inference_mode():
                outputs = self.model(**inputs)
            target_sizes = torch.tensor([image.size[::-1] for image in chunk_images], device=self.device)
            raw_results = self.image_processor.post_process_object_detection(
                outputs, threshold=self.threshold, target_sizes=target_sizes
            )
            layout_results = _apply_layout_postprocess(
                raw_results,
                self.id2label,
                [image.size for image in chunk_images],
            )
            for page, layout_items in zip(chunk_pages, layout_results, strict=True):
                page_regions[page.page_index] = self._regions_from_layout_items(page, layout_items)

            if self.device.startswith("cuda"):
                del inputs, outputs, target_sizes, raw_results
                torch.cuda.empty_cache()
        return page_regions

    def _regions_from_layout_items(self, page: Page, layout_items: list[dict[str, Any]]) -> list[Region]:
        regions: list[Region] = []
        for item in layout_items:
            label = str(item["label"])
            task_type = task_for_label(label)
            if task_type is None:
                continue
            bbox = normalize_bbox(item["coordinate"], page.width, page.height)
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            regions.append(
                Region(
                    page_index=page.page_index,
                    region_index=len(regions),
                    label=label,
                    task_type=task_type,
                    bbox_2d=bbox,
                    score=float(item["score"]),
                )
            )
        if regions:
            return regions
        return default_page_regions(page.page_index)
