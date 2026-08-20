# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""ONNX Runtime PP-DocLayoutV3 detector (RT-DETR export, blue-guardrails/pp-doclayout-v3-onnx).

A drop-in replacement for the transformers ``LayoutDetector``: same
``detect_pages(pages) -> dict[page_index, list[Region]]`` interface and the same
25-class label space, so the existing label/task mapping and bbox normalization
carry over unchanged.

The PaddleX RT-DETR ONNX takes three inputs (``image`` ``[N,3,800,800]``,
``im_shape`` ``[N,2]``, ``scale_factor`` ``[N,2]``) and returns detection boxes
already rescaled to original-page coordinates. The final column of the box output
is PP-DocLayoutV3's predicted reading-order rank, so post-processing must sort by
that model prediction before assigning formatter indices. Run it with the
TensorRT execution provider for speed; CUDA is the fallback.
"""

import ctypes
import glob
import logging
import os
import site
from typing import Any

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from PIL import Image

from cheap_ocr.layout.labels import ID2LABEL, default_page_regions, normalize_bbox, task_for_label
from cheap_ocr.models import Page, Region

logger = logging.getLogger(__name__)

HF_REPO = "blue-guardrails/pp-doclayout-v3-onnx"
ONNX_FILE = "onnx/inference.onnx"
INPUT_SIZE = 800
# Calibrated default for this RT-DETR export; the transformers backend uses 0.3.
SCORE_THRESHOLD = 0.5


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
    union = float(box1_area + box2_area - inter_area)
    return inter_area / union if union > 0 else 0.0


def _nms(boxes: np.ndarray[Any, Any], iou_same: float = 0.6, iou_diff: float = 0.95) -> list[int]:
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


def _containment_masks(
    boxes: np.ndarray[Any, Any], preserve_indices: set[int]
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
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


def _label_id(label: str) -> int | None:
    return next((class_id for class_id, name in ID2LABEL.items() if name == label), None)


def stripped_onnx_path() -> str:
    """Return a layout ONNX with the unused masks output removed (cached).

    The model has a heavy instance-mask head ([N,300,200,200]) we never use.
    Removing it shrinks the graph TensorRT must build and run, which makes engine
    builds dramatically faster and inference lighter. Falls back to the full model
    if extraction fails.
    """
    base = hf_hub_download(HF_REPO, ONNX_FILE)
    stripped = f"{base}.no_masks.onnx"
    if os.path.exists(stripped):
        return stripped
    try:
        import onnx

        model = onnx.load(base)
        input_names = [item.name for item in model.graph.input]
        keep = [item.name for item in model.graph.output if len(item.type.tensor_type.shape.dim) <= 2]
        if len(keep) >= 2:
            onnx.utils.extract_model(base, stripped, input_names, keep)
            logger.info("stripped_layout_onnx outputs=%s", keep)
            return stripped
    except Exception as exc:
        logger.warning("onnx_strip_masks_failed using_full_model error=%s: %s", type(exc).__name__, exc)
    return base


def tensorrt_providers(cache_path: str, *, opt_batch: int = 8, max_batch: int = 8, opt_level: int = 1) -> list[Any]:
    """Build an ONNX Runtime provider list that prefers TensorRT (FP16, cached), then CUDA, then CPU."""

    def shapes(batch: int) -> str:
        return f"image:{batch}x3x{INPUT_SIZE}x{INPUT_SIZE},im_shape:{batch}x2,scale_factor:{batch}x2"

    trt = (
        "TensorrtExecutionProvider",
        {
            "trt_fp16_enable": True,
            "trt_engine_cache_enable": True,
            "trt_engine_cache_path": cache_path,
            "trt_timing_cache_enable": True,
            "trt_builder_optimization_level": opt_level,
            "trt_profile_min_shapes": shapes(1),
            "trt_profile_opt_shapes": shapes(opt_batch),
            "trt_profile_max_shapes": shapes(max_batch),
        },
    )
    return [trt, "CUDAExecutionProvider", "CPUExecutionProvider"]


def _site_packages_dirs() -> list[str]:
    """Return candidate site-packages dirs that may hold the CUDA / TensorRT wheels."""
    # The directory holding onnxruntime also holds the nvidia/* and tensorrt_libs
    # wheel payloads, so derive it from there; supplement with the interpreter's
    # reported site dirs in case of a split layout.
    candidates: list[str] = []
    ort_file = getattr(ort, "__file__", None)
    if ort_file:
        candidates.append(os.path.dirname(os.path.dirname(os.path.abspath(ort_file))))
    try:
        candidates.extend(site.getsitepackages())
    except AttributeError:  # pragma: no cover - some minimal venvs lack getsitepackages.
        pass
    seen: set[str] = set()
    unique: list[str] = []
    for directory in candidates:
        if directory and directory not in seen:
            seen.add(directory)
            unique.append(directory)
    return unique


def _preload_native_libs() -> None:
    """Preload the CUDA + TensorRT shared libs from the pip wheels into the process.

    onnxruntime's TensorRT provider has ``DT_NEEDED`` dependencies on libnvinfer
    plus the CUDA math libs (libcublas, libcudart, ...) that ship inside the
    ``nvidia/*/lib`` and ``tensorrt_libs`` wheel directories. ONNX Runtime's own
    ``preload_dlls()`` covers CUDA + cuDNN but not TensorRT, so we ``dlopen`` the
    rest with ``RTLD_GLOBAL`` before creating the session. Doing this in-process is
    what lets the TensorRT backend work off a plain ``pip install`` with no
    ``LD_LIBRARY_PATH`` / ``ldconfig`` wiring on the host or in the image.

    Best effort: a lib that fails to load just falls through to the loader's normal
    search and, ultimately, to the CUDA / transformers fallback.
    """
    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls()
        except Exception as exc:  # pragma: no cover - best effort lib discovery.
            logger.warning("ort_preload_dlls_failed error=%s: %s", type(exc).__name__, exc)
    # Gather the CUDA libs and the TensorRT libs (the latter depend on the former).
    # Load with repeated passes so intra-wheel load-order dependencies resolve
    # regardless of glob order; RTLD_GLOBAL puts each into the link map so later
    # libs — and ORT's TensorRT provider — reuse them by soname.
    libs: list[str] = []
    for directory in _site_packages_dirs():
        for pattern in ("nvidia/*/lib/*.so*", "tensorrt_libs/*.so*"):
            libs.extend(sorted(glob.glob(os.path.join(directory, pattern))))
    pending = list(dict.fromkeys(libs))  # de-dup, preserve order
    loaded = 0
    while pending:
        unresolved: list[str] = []
        for lib in pending:
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                loaded += 1
            except OSError:  # a dep may not be loaded yet (resolved on a later pass).
                unresolved.append(lib)
        if len(unresolved) == len(pending):  # no progress this pass; give up.
            break
        pending = unresolved
    logger.debug("preloaded_native_libs loaded=%d unresolved=%d", loaded, len(pending))


class OnnxLayoutDetector:
    """PP-DocLayoutV3 RT-DETR detector backed by ONNX Runtime."""

    def __init__(
        self,
        *,
        providers: list[Any] | None = None,
        batch_size: int = 8,
        score_threshold: float | None = None,
        channel_order: str = "rgb",
        normalize_scale: float = 1.0 / 255.0,
        onnx_path: str | None = None,
    ) -> None:
        """Create the ONNX Runtime session for the layout model."""
        self.batch_size = max(1, int(batch_size))
        self.score_threshold = SCORE_THRESHOLD if score_threshold is None else float(score_threshold)
        self.channel_order = channel_order
        self.normalize_scale = float(normalize_scale)
        path = onnx_path or stripped_onnx_path()
        resolved_providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        _preload_native_libs()
        self.session = ort.InferenceSession(path, providers=resolved_providers)
        self.active_providers = self.session.get_providers()
        self.input_names = [item.name for item in self.session.get_inputs()]
        # Request only the detection + count outputs; the masks output
        # ([N,300,200,200] int32) is unused and copying it would be wasteful.
        self.output_names = [item.name for item in self.session.get_outputs() if len(item.shape) <= 2]

    def _preprocess(self, page: Page) -> np.ndarray[Any, Any]:
        image = page.image if page.image.mode == "RGB" else page.image.convert("RGB")
        resized = image.resize((INPUT_SIZE, INPUT_SIZE), Image.Resampling.BILINEAR)
        array = np.asarray(resized, dtype=np.float32)
        if self.channel_order == "bgr":
            array = array[:, :, ::-1]
        array = array * self.normalize_scale
        return np.ascontiguousarray(np.transpose(array, (2, 0, 1)))

    def detect_pages(self, pages: list[Page]) -> dict[int, list[Region]]:
        """Detect layout regions for rendered PDF pages."""
        page_regions: dict[int, list[Region]] = {}
        for start in range(0, len(pages), self.batch_size):
            chunk = pages[start : start + self.batch_size]
            # PaddleDetection rescales boxes to original coords via
            # origin_shape = im_shape / scale_factor. With im_shape=[800,800] and
            # scale_factor=[800/h, 800/w] this recovers the true [h, w].
            feeds = {
                "image": np.stack([self._preprocess(page) for page in chunk]).astype(np.float32),
                "im_shape": np.array([[INPUT_SIZE, INPUT_SIZE]] * len(chunk), dtype=np.float32),
                "scale_factor": np.array(
                    [[INPUT_SIZE / page.height, INPUT_SIZE / page.width] for page in chunk], dtype=np.float32
                ),
            }
            outputs = self.session.run(self.output_names, {k: v for k, v in feeds.items() if k in self.input_names})
            boxes, counts = self._select_boxes_counts(outputs)
            offset = 0
            for index, page in enumerate(chunk):
                count = int(counts[index]) if counts is not None else boxes.shape[0] // len(chunk)
                page_regions[page.page_index] = self._postprocess(boxes[offset : offset + count], page)
                offset += count
        return page_regions

    @staticmethod
    def _select_boxes_counts(
        outputs: list[np.ndarray[Any, Any]],
    ) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any] | None]:
        boxes: np.ndarray[Any, Any] | None = None
        counts: np.ndarray[Any, Any] | None = None
        for output in outputs:
            if output.ndim == 2 and 6 <= output.shape[-1] <= 8:
                boxes = output
            elif output.ndim == 1:
                counts = output
        if boxes is None:
            raise RuntimeError(
                f"Could not find a [num_boxes, 6-8] box output among shapes {[o.shape for o in outputs]}"
            )
        return boxes, counts

    def _postprocess(self, boxes: np.ndarray[Any, Any], page: Page) -> list[Region]:
        boxes_array = self._postprocess_box_rows(boxes, page)
        if boxes_array is None:
            return default_page_regions(page.page_index)

        regions: list[Region] = []
        for row in boxes_array:
            class_id = int(row[0])
            label = ID2LABEL.get(class_id, f"class_{class_id}")
            task_type = task_for_label(label)
            if task_type is None:
                continue
            bbox = normalize_bbox(row[2:6], page.width, page.height)
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            regions.append(
                Region(
                    page_index=page.page_index,
                    region_index=len(regions),
                    label=label,
                    task_type=task_type,
                    bbox_2d=bbox,
                    score=float(row[1]),
                )
            )
        if regions:
            return regions
        return default_page_regions(page.page_index)

    def _postprocess_box_rows(self, boxes: np.ndarray[Any, Any], page: Page) -> np.ndarray[Any, Any] | None:
        boxes_array = self._candidate_boxes(boxes, page)
        if boxes_array is None:
            return None

        boxes_array = boxes_array[_nms(boxes_array[:, :6], iou_same=0.6, iou_diff=0.98)]
        boxes_array = self._filter_huge_page_images(boxes_array, page)
        if len(boxes_array) == 0:
            return None

        preserve_indices = {
            class_id for label in ("image", "seal", "chart") if (class_id := _label_id(label)) is not None
        }
        _, contained_by_other = _containment_masks(boxes_array[:, :6], preserve_indices)
        boxes_array = boxes_array[contained_by_other == 0]
        if len(boxes_array) == 0:
            return None

        return boxes_array[np.lexsort((boxes_array[:, 7], boxes_array[:, 6]))]

    def _candidate_boxes(self, boxes: np.ndarray[Any, Any], page: Page) -> np.ndarray[Any, Any] | None:
        candidates: list[list[float]] = []
        for row_index, row in enumerate(boxes):
            score = float(row[1])
            if score < self.score_threshold:
                continue
            class_id = int(row[0])
            x1, y1, x2, y2 = (float(value) for value in row[2:6])
            x1 = max(0, min(x1, page.width))
            y1 = max(0, min(y1, page.height))
            x2 = max(0, min(x2, page.width))
            y2 = max(0, min(y2, page.height))
            if x1 >= x2 or y1 >= y2:
                continue

            # The ONNX export emits rows in score/top-k order. Column 6 is the
            # model's predicted logical reading-order rank; use it to match the
            # transformers backend instead of inventing a geometric ordering. If
            # an older export ever lacks this column, preserve the raw row order.
            order = float(row_index)
            if row.shape[0] >= 7:
                model_order = float(row[6])
                if np.isfinite(model_order) and model_order >= 0:
                    order = model_order

            candidates.append([float(class_id), score, x1, y1, x2, y2, order, float(row_index)])
        if not candidates:
            return None
        return np.array(candidates, dtype=np.float32)

    @staticmethod
    def _filter_huge_page_images(boxes_array: np.ndarray[Any, Any], page: Page) -> np.ndarray[Any, Any]:
        image_label_id = _label_id("image")
        if image_label_id is None or len(boxes_array) <= 1:
            return boxes_array
        max_image_area_ratio = 0.82 if page.width > page.height else 0.93
        image_area = page.width * page.height
        filtered_boxes = []
        for box in boxes_array:
            label_id, _, x1, y1, x2, y2 = box[:6]
            if label_id != image_label_id:
                filtered_boxes.append(box)
                continue
            box_area = (min(page.width, x2) - max(0, x1)) * (min(page.height, y2) - max(0, y1))
            if box_area <= max_image_area_ratio * image_area:
                filtered_boxes.append(box)
        if filtered_boxes:
            return np.array(filtered_boxes, dtype=np.float32)
        return boxes_array
