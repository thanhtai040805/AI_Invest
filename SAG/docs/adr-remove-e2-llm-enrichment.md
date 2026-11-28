# ADR: Loại Bỏ Pha E2 LLM Enrichment Khỏi Pipeline Knowledge & Ingest Của SAG

- **Trạng thái:** Accepted & Implemented (2026-10-29)
- **Phạm vi ảnh hưởng:** `sag_api/jobs/knowledge.py`, `sag_api/services/knowledge_service.py`, `sag_api/jobs/tasks.py`, `sag_api/core/config.py`, `Dataset/sag_benchmark_runtime.py`, `Dataset/run_eight_file_smoke.py`
- **Các tầng được giữ nguyên 100%:** Phase 1–2C (`CanonicalBlock` + `SearchUnit` + Qdrant Hybrid Index + Shared Embedding Preload Cache) và Phase 5A (`KnowledgeJob(kind="FOUNDATION")` + `E0/E1` Deterministic Extraction + `DenseGraph` `KnowledgeUnitEdge` + `Leiden Routing Tree` + `HippoRAG 2` Multi-Hop Expansion).

---

## 1. Bối cảnh & Câu hỏi kiến trúc

Trong thiết kế ban đầu của Phase 5, sau khi hoàn tất `FOUNDATION` (`E0/E1` regex/heuristic + `DenseGraph` vector `1024` chiều cho từng `KnowledgeUnit`), `KnowledgeWorker` chọn lọc các `KnowledgeUnit` có cờ `e2_reason` (`causal_constraint`, `low_confidence_important`, `semantic_relation`) để gọi LLM (`Qwen/Qwen3-8B`) trích xuất thêm 4 nhóm dữ liệu JSON có neo vị trí ký tự (`entities`, `aliases`, `claims`, `relations`) vào bảng `knowledge_evidence` với `tier = "E2"`.

Để đánh giá khách quan liệu **E2 LLM Enrichment** có thực sự mang lại giá trị gia tăng xứng đáng với chi phí token và độ trễ ingest hay không, chúng tôi đã tích hợp hệ thống **đo token chính xác từ provider (`sag_api/core/token_usage.py`)** và thực hiện phép thử đối chứng A/B nghiêm ngặt trên toàn bộ bộ chuẩn **RetrievalCI Bench v0 (81 tài liệu doanh nghiệp, 3,585 `KnowledgeUnit`, 50 câu hỏi gồm Single-Hop, Multi-Hop, Contradiction và Unanswerable)**, mỗi lượt đều clean sạch PostgreSQL Local và Qdrant Cloud.

---

## 2. Bằng chứng thực nghiệm đối chứng (Controlled A/B Benchmark)

- **Cấu hình Model cố định ở cả 2 lượt chạy:**
  - Embedding: `Qwen/Qwen3-Embedding-0.6B` (`1024` dims, `$0.01 / 1M input tokens` trên SiliconFlow)
  - LLM: `Qwen/Qwen3-8B` (`enable_thinking=False`, `$0.06 / 1M input tokens`, `$0.06 / 1M output tokens` trên SiliconFlow)
- **Lượt A — Không E2 (`RUN_ID = 20261029T031043Z-bb191f`):** Bật đầy đủ `Phase 2C Hybrid + Phase 5A FOUNDATION (E0/E1 + DenseGraph + Leiden Tree)`, `E2 = 0`.
- **Lượt B — Bật Full E2 (`RUN_ID = 20261029T103921Z-183d1c`):** Bật đầy đủ `Phase 2C Hybrid + Phase 5A FOUNDATION + Phase 5B E2 Enrichment` (`258/258` E2 jobs `SUCCEEDED`, `0` lỗi, sinh `1,768` dòng `E2` evidence).

### Bảng so sánh chất lượng & chi phí trực tiếp

| Chỉ số đo lường | **Không E2 (`FOUNDATION + DenseGraph`)**<br>`20261029T031043Z-bb191f` | **Bật Full E2 (`+258 LLM Jobs`)**<br>`20261029T103921Z-183d1c` | Chênh lệch khi thêm E2 |
|---|---:|---:|---:|
| **RetrievalCI Overall Score (`0.7*R + 0.3*P`)** | **93.64** | 93.42 | **-0.22 điểm (Kém đi)** |
| **Source Recall (45 câu có đáp án)** | **99.17%** | **99.17%** | **+0.00% (Không tăng)** |
| — Single-Hop / Multi-Hop / Contradiction Recall | `100.0%` / `97.5%` / `100.0%` | `100.0%` / `97.5%` / `100.0%` | **+0.00% ở mọi phân nhóm** |
| **Source Precision** | **80.73%** | 80.00% | **-0.73% (Nhiều nhiễu hơn)** |
| — Multi-Hop Source Precision | **75.78%** | 73.33% | **-2.45% (Kém đi)** |
| **Abstention Correctness (5 câu bẫy không đáp án)** | **100.0% (`5/5`)** | 80.0% (`4/5`) | **-20.0% (Sai thêm câu `qst_0481`)** |
| **Answer Citation Precision / Recall** | **98.15%** / **83.52%** | 97.10% / 78.89% | **-1.05% / -4.63%** |
| **Số lượt gọi LLM lúc Ingest (81 files)** | **`0` calls** | `258` calls | **+258 HTTP LLM round-trips** |
| **Token tiêu thụ lúc Ingest (81 files)** | **`375,463` tokens** *(100% embedding)* | `526,898` tokens *(+`151,435` LLM tok)* | **+40.3% tổng token ingest** |
| **Chi phí tiền lúc Ingest (81 files, SiliconFlow)** | **`$0.003755`** | `$0.012841` *(riêng E2: `$0.009086`)* | **Đắt gấp 3.42 lần (+242% chi phí ingest)** |
| **Tổng chi phí 1 bài Benchmark (Ingest + 50 Q&A)** | **`$0.014224`** | `$0.023310` | **+63.9% tổng chi phí toàn bài** |

---

## 3. Bốn bằng chứng định lượng giải thích vì sao E2 không tăng chất lượng mà còn làm giảm độ chính xác

Truy vấn hậu kiểm trực tiếp trên PostgreSQL của project `43c17f3765f64e3f8f6e4c3f` (`e2_quality_and_token_audit.json` & `bench_v0_sag_diagnostics.json`) chỉ ra 4 nguyên nhân gốc rễ:

### Bằng chứng 1 — `FOUNDATION (E0/E1 + DenseGraph)` đã chạm trần toán học tuyệt đối (`Oracle Recall Ceiling`) dưới `top_k = 5`, còn E2 tìm thêm được `0` tài liệu đúng
- Trên 45 câu hỏi có đáp án, bản **Không E2** đã đạt `Recall = 1.000` ở **`44 / 45` câu** (bao gồm 14/15 câu Multi-Hop bắc cầu phức tạp qua Confluence, Jira, GitHub PR, Slack).
- Câu duy nhất `< 1.0` là `qst_0354` (`Recall = 0.625`, `Precision = 1.000`): câu này có **8 tài liệu Ground Truth** trong khi cấu hình UI/API giới hạn `requested_top_k = 5` (`5/5` slot trả về đều đúng → `5/8 = 0.625` là mức trần toán học tối đa cho `top_k = 5`).
- Khi đối chiếu từng câu trên toàn bộ 50 câu giữa `No-E2` và `Full-E2`:
  - `42 / 50` câu trả về tập tài liệu giống hệt nhau 100%.
  - `8 / 50` câu (`qst_0341`, `qst_0344`, `qst_0348`, `qst_0349`, `qst_0350`, `qst_0351`, `qst_0481`, `qst_0482`) có thay đổi 1 tài liệu ở cuối danh sách, nhưng **số tài liệu Ground Truth tìm thêm được ở cả 8 câu là `0 / 8` (`100%` tài liệu mà E2 kéo thêm đều là false-positive lân cận)**. Đặc biệt ở câu bẫy `qst_0481`, tài liệu lân cận do E2 kéo thêm làm câu trả lời xuất hiện ghi chú phụ thay vì từ chối thuần túy, làm `Abstention` tụt từ `100%` xuống `80%`.

### Bằng chứng 2 — `51.2%` sản lượng E2 (`905 / 1,768` dòng) là "Dead Tokens" không bao giờ được luồng Graph hay Search đọc tới
- Trong `1,768` dòng E2 đã sinh (`76,283` LLM input tokens + `75,152` LLM output tokens): có **`475` Claims (`26.9%`)** và **`430` Relations (`24.3%`)** = **`905` dòng (`51.2%`)**.
- Cả bộ dựng đồ thị Leiden (`knowledge_service.py::graph_payload`) lẫn bộ truy hồi HippoRAG 2 (`search_unit_retrieval_service.py`) **đều không đọc `claim` hay `relation`**, mà tổng hợp câu trả lời trực tiếp từ văn bản gốc đã xác thực của `SearchUnit` / `CanonicalBlock`.

### Bằng chứng 3 — Lãng phí lượt gọi rỗng (`7.4%`), trùng lặp với `E0/E1` (`19.2%`) và rơi rụng chuỗi `alias` (`67.8%`)
- **`19 / 258` E2 jobs (`7.36%`)** gọi LLM trên các block tiêu đề dẫn nhập cụt (`"Operational problems we must handle:"`) hoặc dòng kẻ bảng Markdown (`"| --- | --- |"`) và trả về **`0` evidence (`0/100` điểm)**.
- Trong `863` dòng `entity` (`559`) + `alias` (`304`) còn lại: **`166 / 863` dòng (`19.2%`)** sinh ra nhãn `label` đã được chính bộ trích xuất tất định `E0/E1` bắt miễn phí trên cùng unit đó (`KMS`, `IAM`, `HSM`, `ADR-007`, `SRE`, `Timeout`, `Throttling`...).
- Trong `304` dòng `alias`, bước gộp `features["entities"]` chỉ lấy `label` và bỏ qua chuỗi `alias` ở **`206 / 304` dòng (`67.8%`)**.

### Bằng chứng 4 — Các cầu nối xuyên tài liệu "mới" do E2 sinh thêm chủ yếu là danh từ chung làm loãng đồ thị HippoRAG 2
- Trong toàn bộ `538` nhãn thực thể mới (chưa có ở `E0/E1` trên unit đó) do `Qwen3-8B` sinh ra, chỉ có **`49` nhãn** xuất hiện ở `>= 2` tài liệu để tạo cầu nối mới.
- Kiểm tra trực tiếp 49 nhãn này trong PostgreSQL cho thấy các thực thể định danh đặc hiệu (`ADR-007`, `PR-19422`, `SUP-18510`, `PROXIMA-ENT-014`...) đã được `E0/E1` + `_extract_artifact_codes` vét sạch; phần E2 sinh thêm chủ yếu là **danh từ chung** (`customer` nối 16 file, `idempotency key` 6 file, `latency` 5 file, `configuration`, `endpoint`, `feature`, `date`, `input`, `evidence`...). Chính các nhãn chung này làm tăng mật độ cạnh nhiễu giữa các tài liệu khác sự cố, kéo Precision giảm và kích hoạt thêm hàng trăm đợt xếp lịch rebuild cây Leiden không cần thiết.

---

## 4. Quyết định kiến trúc

1. **Loại bỏ hoàn toàn pha E2 LLM Enrichment (`KnowledgeJob.kind == "E2"`, `llm_enrich`, `apply_e2`, `knowledge_e2_*`)** khỏi `KnowledgeWorker`, `rebuild_knowledge_version`, `rebuild_universe_tree`, và toàn bộ runner Benchmark / E2E.
2. **Giữ nguyên 100% Phase 5A (`FOUNDATION`) ở lúc Ingest:**
   - Mọi tài liệu sau khi đạt `SEARCH_READY` ở Phase 2C vẫn chạy `KnowledgeJob(kind="FOUNDATION")` để sinh `KnowledgeUnit`, trích xuất tất định `E0/E1`, gắn vector `DenseGraph` (**tốn `0` token mới nhờ tái sử dụng 100% shared embedding cache từ Phase 2C**), xây `KnowledgeUnitEdge` và xuất bản cây định tuyến Leiden (`TreeManifest`) phục vụ HippoRAG 2 Multi-Hop, Knowledge Universe UI (`neural_tier="E0_E1"`) và Knowledge Quality Audit.
3. **Giữ tương thích ngược schema DB & API:**
   - Bảng `knowledge_evidence`, cột `knowledge_jobs.usage_json`, và trường `neural_tier` / `e2_reason` trên schema API `UniverseKnowledgeUnitOut` vẫn giữ nguyên định dạng để không ảnh hưởng tới dữ liệu lịch sử hay giao diện Next.js (`apps/web`).
