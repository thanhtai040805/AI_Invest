"""Region crop preparation for vLLM OCR requests (crop, resize, encode).

Prepared OCR regions carry the final base64 data URL for their vLLM request.
All crop/resize/JPEG/base64 work runs in a worker thread pool owned by the
engine.

The resize geometry constants are facts about GLM-OCR's vision encoder (Qwen-style
patching), not settings; ``smart_resize`` must stay numerically identical to the
upstream preprocessing.
"""

import asyncio
import base64
import io
import math
from concurrent.futures import Executor
from typing import Final

from PIL import Image

from cheap_ocr.config import OcrConfig
from cheap_ocr.models import Page, PreparedRegion, RecognizedRegion, Region, Task

# GLM-OCR model constants: crop pixel budget and patch geometry.
MIN_PIXELS: Final = 112 * 112
MAX_PIXELS: Final = 14 * 14 * 4 * 1280
T_PATCH_SIZE: Final = 2
PATCH_EXPAND_FACTOR: Final = 1


def smart_resize(
    t: int,
    h: int,
    w: int,
    t_factor: int = 1,
    h_factor: int = 28,
    w_factor: int = 28,
    min_pixels: int = MIN_PIXELS,
    max_pixels: int = MAX_PIXELS,
) -> tuple[int, int]:
    """Copied from GLM-OCR/Qwen-style image preprocessing."""
    assert t >= t_factor, "Temporal dimension must be greater than the factor."

    h_bar = round(h / h_factor) * h_factor
    w_bar = round(w / w_factor) * w_factor
    t_bar = round(t / t_factor) * t_factor

    if t_bar * h_bar * w_bar > max_pixels:
        beta = math.sqrt((t * h * w) / max_pixels)
        h_bar = max(h_factor, math.floor(h / beta / h_factor) * h_factor)
        w_bar = max(w_factor, math.floor(w / beta / w_factor) * w_factor)
    elif t_bar * h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (t * h * w))
        h_bar = math.ceil(h * beta / h_factor) * h_factor
        w_bar = math.ceil(w * beta / w_factor) * w_factor

    return int(h_bar), int(w_bar)


def crop_region(page: Page, region: Region) -> Image.Image:
    """Crop a normalized region bbox from a rendered page image."""
    img = page.image
    w, h = img.size
    x1n, y1n, x2n, y2n = region.bbox_2d
    x1 = max(0, min(w - 1, int(x1n * w / 1000)))
    y1 = max(0, min(h - 1, int(y1n * h / 1000)))
    x2 = max(x1 + 1, min(w, int(x2n * w / 1000)))
    y2 = max(y1 + 1, min(h, int(y2n * h / 1000)))
    return img.crop((x1, y1, x2, y2))


def encode_image_bytes(image: Image.Image, jpeg_quality: int) -> bytes:
    """Resize per GLM-OCR geometry and JPEG-encode an image for one vLLM request."""
    if image.mode != "RGB":
        image = image.convert("RGB")

    w, h = image.size
    h_bar, w_bar = smart_resize(
        t=T_PATCH_SIZE,
        h=h,
        w=w,
        t_factor=T_PATCH_SIZE,
        h_factor=14 * 2 * PATCH_EXPAND_FACTOR,
        w_factor=14 * 2 * PATCH_EXPAND_FACTOR,
    )
    if (w_bar, h_bar) != (w, h):
        image = image.resize((w_bar, h_bar), Image.Resampling.BICUBIC)

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=jpeg_quality, optimize=False)
    return buf.getvalue()


def prepare_region(page: Page, region: Region, jpeg_quality: int) -> PreparedRegion:
    """Crop one OCR-able region and prepare its vLLM image data URL."""
    cropped = crop_region(page, region)
    image_bytes = encode_image_bytes(cropped, jpeg_quality)
    image_url = f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('ascii')}"
    return PreparedRegion(region=region, image_url=image_url, image_size_bytes=len(image_bytes))


async def prepare_regions(
    pages: list[Page],
    page_regions: dict[int, list[Region]],
    config: OcrConfig,
    executor: Executor,
) -> tuple[list[PreparedRegion], list[RecognizedRegion]]:
    """Crop/resize/encode OCR regions on ``executor``; emit skip regions immediately."""
    loop = asyncio.get_running_loop()
    page_by_idx = {page.page_index: page for page in pages}
    ocr_work: list[tuple[Page, Region]] = []
    skipped: list[RecognizedRegion] = []

    for page_idx in sorted(page_regions):
        for region in page_regions[page_idx]:
            if region.task_type == Task.SKIP:
                skipped.append(RecognizedRegion(region=region, content=None))
            else:
                ocr_work.append((page_by_idx[page_idx], region))

    if not ocr_work:
        return [], skipped

    tasks = [
        loop.run_in_executor(executor, prepare_region, page, region, config.jpeg_quality) for page, region in ocr_work
    ]
    prepared = await asyncio.gather(*tasks)
    return list(prepared), skipped
