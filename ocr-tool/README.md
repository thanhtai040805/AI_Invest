# OCR Tool — PDF OCR giá rẻ (Modal-backed)

Tool OCR báo cáo tài chính (BCTC) Việt Nam + engine OCR đa dụng, chạy trên Modal.
Gồm 2 package và 1 file config:

```
ocr-tool/
├── cheap_ocr/              # Engine OCR đa dụng (layout PP-DocLayoutV3 + Qwen2.5-VL-7B qua vLLM)
│   ├── modal/              #   Modal workers (GpuWorker, StorageWorker) + entrypoint run
│   └── cli.py              #   CLI: cheap-ocr (local / modal)
├── financial_pipeline/     # Orchestration BCTC (classify/prune trang → gom batch → GPU → filter)
│   ├── pipeline.py         #   FinancialOcrPipeline — API chính cho dự án tổng
│   └── modal/              #   classify_one + BatchSupervisor (deploy qua deploy.py)
├── financial_profile.yaml  # Runtime config (danh sách statement giữ lại, threshold...)
├── requirements.txt        # Deps cho dự án tiêu thụ
└── README.md
```

## 1. Cài đặt (dự án tiêu thụ)

```bash
pip install -r requirements.txt
# modal login  # cùng workspace/account với nơi đã deploy tool
```

- Không cần GPU stack ở local — mọi thứ nặng đã nướng trong image trên Modal.
- `import cheap_ocr` / `import financial_pipeline` là light (GPU objects import lazy).

## 2. Deploy lên Modal (chỉ làm 1 lần / khi đổi config)

```bash
# Windows: $env:PYTHONUTF8="1"; $env:CHEAP_OCR_GPU="L40S"
PYTHONUTF8=1 CHEAP_OCR_GPU=L40S modal deploy -m cheap_ocr.modal.run
PYTHONUTF8=1 CHEAP_OCR_GPU=L40S modal deploy -m financial_pipeline.modal.deploy
```

> Phải deploy qua **`run.py`** (cheap-ocr) và **`deploy.py`** (financial-pipeline) —
> các module này mới đăng ký đủ `GpuWorker`/`StorageWorker`/`BatchSupervisor`.
> Deploy `app.py` sẽ không đăng ký worker.

## 3. Cách dùng

### 3a. Lập trình (recommended cho dự án tổng)

```python
from financial_pipeline import FinancialOcrPipeline

pipe = FinancialOcrPipeline(profile_path="financial_profile.yaml")

# Nhiều URL cafef → markdown + metrics
results = pipe.process_batch_urls_cloud(
    ["https://.../FTS...pdf", "https://.../HPG...pdf"],
    enable_filtering=True,
)
for item in results:
    m = item["metrics"]
    print(item["filename"], f"{m.retained_pages}/{m.total_pdf_pages} trang",
          f"~{m.estimated_tokens:,} tokens", f"gpu {m.time_modal_ocr_sec}s")
    open(item["filename"].rsplit(".",1)[0] + ".md", "w", encoding="utf-8").write(item["markdown"])

# Hoặc 1 file PDF (bytes) local:
markdown, metrics, class_result = pipe.process_pdf_bytes(pdf_bytes, filename="a.pdf")
```

### 3b. CLI (chạy nguyên batch từ folder/cloud)

```bash
cheap-ocr modal --source ./input --target ./output
cheap-ocr modal --source s3://bucket/in --target s3://bucket/out --detach
# Giữ nguyên config tối ưu mặc định; flag nào set thì flag đó thắng.
```

### 3c. Low-level: gọi thẳng GpuWorker (ít khi cần)

```python
import modal
from cheap_ocr.config import OcrConfig
from cheap_ocr.models import DocumentInput

worker = modal.Cls.from_name("cheap-ocr", "GpuWorker")
resp = worker.process_batch.remote(
    [DocumentInput(uri=..., relative_path="a.pdf", input_id="...", metadata={}, data=pdf_bytes)],
    OcrConfig(), source, target, "batch-1", True, False,
)
```

## 4. Tối ưu chi phí (đã đo & áp dụng)

Nguyên tắc billing Modal: **GPU/CPU tính theo `max(request, actual)` × thời gian container sống**,
L40S ~$1.95/h (list), CPU ~$0.047/core-h. Tiền thật nằm ở **GPU-giây × giờ** và **thời gian idle**, không phải số lần gọi.

| Lever | Giá trị đã set | Tiết kiệm | Ghi chú |
|---|---|---|---|
| Supervisor **preemptible** (bỏ `nonpreemptible=True`) | mặc định | ~4x CPU-side/file | nonpreemptible tính 3x giá |
| **CPU request 16→8 core** cho GPU container | `CHEAP_OCR_CPU_CORES=8.0` | ~giảm nửa CPU bill | Đo thực tế: OCR chỉ dùng ~1–4 core, peak 9.8 (1 lần); 8 core không làm OCR chậm (GPU times giữ nguyên) |
| **scaledown_window 15→5 phút** | `CHEAP_OCR_SCALEDOWN_WINDOW_SECONDS=300` | ~$0.35–0.40/run | Mỗi run không còn trả ~10 phút idle thừa |
| **Gom càng nhiều file/run** | — | boot ~4.5 phút chia đều | 6 file: wall 484s, L40S ~$0.50; gọi lẻ từng file = trả lại boot mỗi lần |

**Cấm kỵ (hiểu ngược dễ tốn tiền):**
- ❌ **Chia batch nhỏ để "né cold start"** — container chỉ scale down sau `scaledown_window` kể từ *request cuối*; chia nhỏ + cách xa nhau = thêm N lần boot. Gom lại mới rẻ.
- ❌ Để supervisor `nonpreemptible=True` — giá ×3.
- ❌ Set CPU request quá cao chỉ vì sợ nghẽn — Modal tính theo request, dư cũng trả.

**Chi phí cố định lưu ý:**
- Volume `huggingface-cache` ~18 GiB (model weights) tính GB-tháng ~$2.7/tháng — đổi lấy cold boot nhanh, nên giữ.
- Cold boot ~4.5 phút (load model) — chi phí cố định mỗi session, không tránh được khi container lạnh.

**Cấu hình đã khóa (đừng đổi nếu chưa có lý do):**
- `api_server_count=1`, spec decoding **tắt**, prefix caching **tắt** — do bug vLLM (crash EngineCore). Chỉ mở lại sau khi upgrade vLLM đã fix.

## 5. Chiến lược gom batch (số file MIN/MAX)

Pipeline (`financial_pipeline`) tự xếp file **lớn trước**, gom thành batch
(`max_docs=16 / max_bytes=512MB / max_pages=1200`) và gửi toàn bộ batch cùng lúc
→ **mọi file trong 1 run chạy chung 1 container session** (không scale-down giữa
các batch vì request đến liên tiếp).

**Cấu trúc chi phí:**
- Cố định mỗi session: boot ~4.5 phút + idle 5 phút ≈ **$0.35–0.40** (không tránh được).
- Biên mỗi file: chỉ GPU-time (~$0.05–0.07/file cỡ FTS; file vài trang ~$0.04–0.05).

**MIN (tối thiểu):** tránh chạy 1–2 file nhỏ — sẽ trả ~$0.37 cố định cho ~$0.08 việc làm.
Break-even khoảng **≥ 4–6 file/run** (hoặc ≥ 2–3 file cỡ FTS). Ngoại lệ duy nhất: file gấp
→ cứ chạy, đó là giá của sự gấp.

**MAX (tối đa):** không có giới hạn gây hại — thêm file chỉ cộng GPU-time biên; boot+idle
đã trả rồi. Quy tắc vàng: **gom hết những gì đang chờ vào 1 run** (khoảng ~1 phút/file cỡ FTS
→ 50 file ≈ 1.5h GPU, vẫn < timeout 6h).

**File lệch nhau (3 trang vs 96 trang):** chạy cùng 1 run là tối ưu. File nhỏ được nhét vào
batch chứa file lớn → **chạy đè trong lúc GPU xử lý batch lớn** (vLLM continuous batching),
nên chi phí biên của file nhỏ ≈ đúng số trang của nó. Tách riêng = 2 lần boot = đắt gấp ~1.5x.
Chỉ tách run khi: cần kết quả sớm cho 1 file gấp, hoặc có 2 nhóm ưu tiên khác hẳn.

## 6. Output

- Markdown thuần (`*.md`) — giữ cấu trúc headings, bảng giữ nguyên.
- `*_stats.json` — metrics: số trang giữ/loại, tokens, thời gian classify/OCR/filter.
- Filtering giữ ~69% trang (31% trang bìa/công bố/điều lệ... bị loại); PDF scan giữ nguyên toàn bộ để không mất số liệu.
