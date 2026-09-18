---
name: scientific-diagram-prompt-crafter
description: Masterclass meta-skill for autonomous codebase root research and paper-grade scientific diagram prompt engineering. Codifies the exact 6-stage cognitive engine to trace raw source code down to its mathematical and data-structure foundations (bypassing framework plumbing), extract numerical ground truth, apply fresh light pastel aesthetics (strictly NO black item backgrounds), dynamic flow opacity, item-click inspector drawers, and synthesize the ultimate prompt to generate paper-grade scientific diagrams (NeurIPS/ICLR/SIGMOD standard).
license: MIT
metadata:
  version: "4.0.0"
---

# Scientific Diagram Prompt Crafter & Root Research Meta-Skill

> **Bản chất của Skill**: Đây là một **Meta-Skill Độc Lập & Phổ Quát (Universal Cognitive Engine)** hướng dẫn AI Agent cách tái hiện chính xác tư duy của một **Principal Architect / Quant Researcher** khi điều tra mã nguồn từ gốc (**Root Research**): Bóc tách ADN biến đổi dữ liệu, khai quật công thức toán học và hằng số biên, từ đó tự động sinh ra **Prompt tối thượng** để kiến tạo sơ đồ khoa học đạt chuẩn xuất bản tại các hội nghị học thuật hàng đầu thế giới (NeurIPS, ICLR, SIGMOD).

---

## 1. Bản Đồ 6 Tầng Tư Duy Bóc Tách Từ Gốc (The 6-Stage Cognitive Trajectory)

Để vẽ được một sơ đồ kỹ thuật đỉnh cao, Agent **không bao giờ nhìn lướt qua code hay dựa vào suy đoán**. Agent phải kích hoạt chuỗi 6 tầng nhận thức tuần tự:

```
┌────────────────────────────────────────────────────────────────────────┐
│  TẦNG 1: PHẢN XẠ HOÀI NGHI & BÁC BỎ TẦNG VỎ (SKEPTICAL NOISE GATE)     │
│  Từ chối vẽ các hộp rỗng: Controller, Service, DTO, ORM, HTTP Pipes   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  TẦNG 2: DÒNG CHẢY HẠT VẬT LÝ & BIẾN THIÊN HÌNH THÁI (MORPHOLOGY)       │
│  S_0 (Bytes) -> S_1 (AST Tree) -> S_2 (Hash/Chunks) -> S_3 (Tensor/G) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  TẦNG 3: CƠN THÈM KHÁT CHÂN LÝ SỐ HỌC & BIÊN CỨNG (GROUND TRUTH)       │
│  Grep công thức toán (Cosine, Softmax), ngưỡng cắt động, veto guards   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  TẦNG 4: PHÂN LẬP KHÔNG - THỜI GIAN (SPATIOTEMPORAL DECOUPLING)        │
│  Pha 1: Offline Ingestion/Indexing vs Pha 2: Online Hot-Path Inference │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  TẦNG 5: BIỂU TƯỢNG HÌNH HỌC, PASTEL TƯƠI SÁNG & TƯƠNG TÁC SÂU         │
│  3D Axes, Graph Mesh, CẤM NỀN ĐEN, Dynamic Opacity, Inspector Drawer   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│  TẦNG 6: ĐÓNG GÓI MASTER PROMPT ĐẶC TẢ HOÀN CHỈNH                     │
│  Chuyển hóa toàn bộ phát hiện thành một bản thiết kế bất biến 6 mục    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Chi Tiết Phương Pháp Luận & Hành Động Cụ Thể

### Tầng 1: Phản Xạ Hoài Nghi & Bác Bỏ Tầng Vỏ (The Skeptical Reflex)
- **Tư duy cốt lõi**: Mọi framework (FastAPI, NestJS, Spring, Django) chỉ là "ống dẫn nước" (plumbing), không phải "dòng nước". Nếu một kỹ sư hay nhà khoa học nhìn vào sơ đồ chỉ thấy các hộp ghi "Controller", "Service", "Database", sơ đồ đó **hoàn toàn vô giá trị**.
- **Hành động của Agent**:
  - Dùng `grep_search` hoặc `list_dir` nhắm thẳng vào các thư mục nghiệp vụ sâu: `domain/`, `core/`, `algorithms/`, `pipelines/`, `kernel/`, `math/`.
  - **Loại bỏ ngay lập tức**: Controller endpoints, DTO mappers, serializer, middleware, Docker/Nginx configs.

### Tầng 2: Dòng Chảy Hạt Vật Lý & Biến Thiên Hình Thái (Particle Morphology)
- **Tư duy cốt lõi**: Dữ liệu không phải là biến số trừu tượng. Dữ liệu là một thực thể vật lý bị biến dạng liên tục qua từng trạm xử lý:
  $$\text{Input: } S_0 \xrightarrow{\text{Operator}_1} S_1 \xrightarrow{\text{Operator}_2} S_2 \dots \xrightarrow{\text{Operator}_n} S_{\text{final}}$$
- **Hành động của Agent**: Trả lời chính xác 5 câu hỏi:
  1. Dữ liệu bước vào hệ thống ở trạng thái vật lý nào? (Ví dụ: `raw_pdf: bytes`, `json_stream`, `audio_pcm`).
  2. Trạm đầu tiên biến đổi nó thành cấu trúc gì? (Ví dụ: `Markdown AST Tree`, `Token Stream`).
  3. Cơ chế định danh và bảo toàn tính xác thực là gì? (Ví dụ: `SHA-256(line)` chống bịa đặt).
  4. Tại điểm phân nhánh (Fork), dữ liệu rẽ thành các không gian nào? (Ví dụ: Không gian vector $1024$-d vs Đồ thị siêu cạnh Bipartite $G=(V, E)$).
  5. Dữ liệu hội tụ (Join) và đóng gói thành context như thế nào?

### Tầng 3: Cơn Thèm Khát Chân Lý Số Học & Biên Cứng (Numerical Ground Truth)
- **Tư duy cốt lõi**: Sự khác biệt giữa một sơ đồ "vẽ chơi" và một sơ đồ "chuẩn hội nghị khoa học" nằm ở **công thức toán học và các hằng số định lượng**. Không bao giờ nói chung chung "thuật toán lọc", "tính điểm tương đồng".
- **Hành động của Agent**:
  - Chạy `grep_search` tìm các từ khóa: `threshold`, `cutoff`, `limit`, `weight`, `ratio`, `max_`, `min_`, `formula`, `veto`, `alpha`, `lambda`.
  - Bóc tách công thức: Ví dụ Cosine Similarity $S = \frac{\mathbf{u}\cdot\mathbf{v}}{\|\mathbf{u}\|\|\mathbf{v}\|}$, Kelly Criterion $f^* = \frac{bp - q}{b}$, Tarjan SCC.
  - Khai quật ngưỡng cắt: Ví dụ `S_raw >= max(0.35, 0.68 * S_top)`, giới hạn sàn HOSE `13.51%`, trần rủi ro `Beneish M-Score <= -1.78`.
  - Khai quật bộ đếm nén: Bộ đếm hàng đợi nén từ $32 \to 24 \to 8$ ứng viên (Dynamic Queue Accumulator).

### Tầng 4: Phân Lập Không - Thời Gian (Spatiotemporal Decoupling)
- **Tư duy cốt lõi**: Hệ thống thực tế luôn tách biệt những công việc nặng nhọc tốn thời gian ra khỏi đường truyền thời gian thực.
- **Hành động của Agent**: Tách bố cục thành 2 nửa:
  - **Nửa trên - Pha 1 (Offline / Indexing / Batch Pipeline)**: Ingestion, OCR, AST parsing, Vector Embedding, Hypergraph building, lưu trữ kép (Dual Store).
  - **Nửa dưới - Pha 2 (Online / Query Inference / Hot-Path)**: Nhận user query, phân nhánh song song Fork-Join (Vector Search + Lexical Grep + Forensic Guard), hội tụ hàng đợi nén và kích hoạt downstream execution.

---

## 3. Quy Chuẩn Thị Giác & Trực Quan Khoa Học (Paper-Grade Visual Standard)

### A. Bảng Màu "Tươi Nhưng Nhạt" (Fresh Pastel Palette)
> [!IMPORTANT]
> **TỐI KỴ TUYỆT ĐỐI: CẤM DÙNG NỀN ĐEN HAY MÀU U ÁM CHO ITEM/NODE!**
> Cấm hoàn toàn `fill="#000000"`, `fill="#0f172a"` hoặc các khối hộp xám đen xì. Nền item đen làm sơ đồ trông như "bảng mạch điện tử rẻ tiền" hoặc màn hình hacker u tối, hoàn toàn xa lạ với các ấn phẩm khoa học của Nature, NeurIPS hay ICLR.

1. **Gam màu nền Node (Pastel Fill)**: Phải sáng, tươi tắn, thanh thoát và dịu mắt:
   - **Vector / Không gian nhúng**: Xanh da trời nhạt (`#e0f2fe` hoặc `#bae6fd`).
   - **Đồ thị / Quan hệ cấu trúc**: Tím oải hương nhạt (`#f3e8ff` hoặc `#e9d5ff`).
   - **Tiêu điểm / Bằng chứng xác minh**: Xanh bạc hà nhạt (`#dcfce7` hoặc `#bbf7d0`).
   - **Rủi ro / Phản biện / Veto Gate**: Cam đào nhạt (`#ffedd5` hoặc `#fed7aa`).
   - **Dữ liệu trung tính / Lưu trữ**: Trắng tinh khiết (`#ffffff`) hoặc xám ngà rất nhẹ (`#f8fafc`).
2. **Đường viền (Stroke)**: Sắc sảo, mảnh (1.2px - 1.5px), dùng tone màu đậm tương ứng (`#0284c7`, `#7c3aed`, `#16a34a`, `#ea580c`, `#cbd5e1`).
3. **Typography**: Chữ màu Slate/Navy đậm (`#0f172a`, `#1e293b`), tương phản tuyệt đối trên nền sáng, dễ đọc và sắc nét.
4. **Canvas Background**: Trắng sáng kỹ thuật (`#fafafa` hoặc `#f8fafc`) có thể điểm họa tiết chấm lưới kỹ thuật mờ siêu nhẹ (dot grid `rgba(0,0,0,0.03)`).

### B. Biểu Tượng Kỹ Thuật (Data Glyphs) Thay Vì Hộp Chữ Nhật Chết
- **Không gian Vector**: Vẽ hệ trục tọa độ 3D $(x, y, z)$ có 3 mũi tên vector màu sắc khác nhau ($V_1, V_2, V_3$).
- **Đồ thị Tri thức**: Vẽ cụm chấm tròn liên kết lưới đan chéo (Bipartite Graph).
- **Dải phân đoạn (Chunks)**: Vẽ một dải các ô thẻ liên tiếp nối đuôi nhau `[ Chunk 1 | Chunk 2 | Chunk 3 ]`.
- **Toán tử trên đường nối**: Gắn trực tiếp thẻ hình viên thuốc (Pill Badge) trên thân mũi tên: `[1:1]`, `[1:N AST]`, `[M:N Hyperedge]`, `[Cosine >= 0.4]`, `[Fork]`, `[Concat/Join]`.

### C. Động Lực Học Luồng (Dynamic Flow Opacity & Movement)
1. **Dynamic Flow Opacity**:
   - Khi chạy ở chế độ Step: Mũi tên và item của các bước **chưa chạy tới** phải tự động mờ xuống (`opacity: 0.14 - 0.20`).
   - Chỉ có bước **hiện tại (is-current)** mới bừng sáng `opacity: 1.0` kèm hiệu ứng dòng chảy photon (`.flow-active`).
2. **Hạt Photon (Flow Tokens)**: Hạt ánh sáng chuyển động dọc theo vector nối mô phỏng gói tin dữ liệu đang truyền.
3. **Bộ đếm dồn ứ (Queue Accumulator)**: Hiển thị hiệu ứng nén dữ liệu thời gian thực ($32 \to 24 \to 8$).
4. **Bộ điều khiển Step**: Có đầy đủ nút Play, Pause, Next, Prev, Replay kèm hỗ trợ phím tắt Space và Mũi tên $\leftarrow \rightarrow$.

### D. Tương Tác Sâu: Item-Click Inspector Drawer (Xóa Sổ Thẻ Rác Dưới Đáy)
> [!IMPORTANT]
> **TRIỆT TIÊU TOÀN BỘ CÁC THẺ GHI CHÚ TĨNH DÀI DÒNG DƯỚI ĐÁY SƠ ĐỒ!**
> Việc để lại một đống thẻ văn bản tĩnh dưới chân sơ đồ gây ô nhiễm thị giác và làm người xem mất tập trung.

- Mọi Item trên SVG đều có `cursor: pointer` và hiệu ứng hover tinh tế.
- Khi người dùng **Click vào bất kỳ Item nào**, một **Inspector Drawer** trượt mượt mà từ cạnh phải màn hình hiển thị:
  1. **Tiêu đề & Phân loại**: Tên node kèm Subsystem Badge.
  2. **Thuật toán & Công thức toán học**: Khối hiển thị code/math chuẩn mực.
  3. **Ngưỡng tham số biên trong Code**: Các con số hằng số khai quật được từ repo.
  4. **Hình thái Dữ liệu (I/O Morphology)**: Cấu trúc truyền nhận vào/ra.
  5. **Mã nguồn thực tế (Codebase Reference)**: Link clickable tới file mã nguồn gốc.
- Hỗ trợ phím `ESC` và click ra ngoài backdrop để đóng nhanh Drawer.

---

## 4. Khuôn Mẫu Master Prompt Tối Thượng (Master Prompt Synthesis Engine)

Sau khi hoàn tất quá trình Root Research, Agent điền toàn bộ kết quả vào bản đặc tả 6 phần sau để xuất xưởng Prompt vẽ sơ đồ:

````markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/ICLR/SIGMOD standard) dưới dạng một file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, mô tả chính xác giải thuật và dòng chảy dữ liệu được bóc tách từ mã nguồn gốc:

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: [Tên chính xác của giải thuật hoặc module lõi]
- **Mục tiêu kỹ thuật**: [Bài toán toán học / kỹ thuật cốt lõi cần giải quyết]
- **Đường dẫn mã nguồn gốc đã khảo sát**: [Liệt kê các file core/domain đã research]

### 2. BỐ CỤC PHÂN RÃ HAI PHA (SPATIOTEMPORAL DUAL-PHASE LAYOUT)
- **Nửa trên: PHA 1 - OFFLINE / BATCH INGESTION & KNOWLEDGE INDEXING**
  * Đầu vào thô: [Kiểu dữ liệu và hình thái ban đầu: ví dụ Byte Stream, Raw JSON, Files]
  * Trạm bóc tách cấu trúc: [Toán tử và giải thuật bóc tách]
  * Phân đoạn & Dải Chunks: [Hình thái dải ô liên tiếp kèm Content Hash SHA-256]
  * Trạm Vector hóa & Đồ thị hóa: [Toán tử Dense Embedding + Trích xuất Entity-Relation]
  * Kho lưu trữ kép (Dual Persistence): [Hệ thống lưu trữ SQL quan hệ + Không gian Vector / Graph]
- **Nửa dưới: PHA 2 - ONLINE / REAL-TIME INFERENCE & HOT-PATH EXECUTION**
  * Trigger truy vấn: [Dạng thức câu hỏi / tín hiệu đầu vào từ người dùng]
  * Phân nhánh song song (Parallel Fork-Join):
    - Nhánh 1 (Dense Vector Retrieval): [Toán tử không gian 3D, công thức Cosine Similarity]
    - Nhánh 2 (Graph / Lexical Navigation): [Toán tử duyệt đồ thị / tìm kiếm quan hệ]
    - Nhánh 3 (Forensic / Guard / Hard Law): [Bộ lọc phủ quyết, kiểm tra quy tắc an toàn]
  * Điểm hội tụ & Nén hàng đợi (Queue Accumulator): [Toán tử nén từ M ứng viên xuống N ứng viên]
  * Đóng gói ngữ cảnh đầu ra: [Định dạng context cuối cùng đưa vào downstream engine]

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- [Liệt kê công thức toán học chính xác khai quật được, dùng ký hiệu chuẩn LaTeX / Math]
- [Liệt kê các con số biên cụ thể: Ngưỡng lọc (thresholds), Top-K, Kích thước batch, Tỷ lệ nén]
- [Liệt kê điều kiện rẽ nhánh và phủ quyết Veto chính xác]

### 4. BỘ KÝ HIỆU HÌNH HỌC (DATA GLYPHS SPECIFICATION)
- Glyph Hệ trục tọa độ 3D có 3 vector mang màu sắc khác nhau (Ve1, Ve2, Ve3) cho không gian nhúng.
- Glyph Đồ thị thực thể với các chấm tròn kết nối lưới đan chéo cho cấu trúc quan hệ.
- Glyph Dải ô nối tiếp cho mảng Chunks.
- Nhãn toán tử bo góc (Pill Badges) gắn trực tiếp trên thân các mũi tên: [1:1], [Many:Many Hyperedge], [Cosine >= X], [Fork], [Concat/Join].

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE RULES)
- **TUYỆT ĐỐI KHÔNG DÙNG NỀN ĐEN CHO ITEM/NODE**. Cấm mọi màu nền đen `#000000` hoặc `#0f172a`.
- Nền Canvas tổng thể: Trắng sáng kỹ thuật (#fafafa hoặc #f8fafc) kèm chấm lưới siêu nhẹ.
- Nền Item phân định chức năng bằng các tone pastel tươi sáng:
  * Vector/Embedding: Xanh da trời nhạt (#e0f2fe), viền xanh biển sắc nét (#0284c7).
  * Graph/Quan hệ: Tím oải hương nhạt (#f3e8ff), viền tím thạch anh (#7c3aed).
  * Tiêu điểm/Verified: Xanh bạc hà nhạt (#dcfce7), viền xanh lá tươi (#16a34a).
  * Rủi ro/Veto: Cam đào nhạt (#ffedd5), viền cam cháy (#ea580c).
  * Dữ liệu trung tính: Trắng tinh khiết (#ffffff), viền slate (#cbd5e1).
- Chữ: Slate/Navy đậm (#0f172a), nét rõ, tương phản cao.

### 6. ĐỘNG LỰC HỌC LUỒNG & TƯƠNG TÁC BẢNG ĐIỀU TRA (INTERACTIVE SPECIFICATIONS)
- **Dynamic Flow Opacity**: Mũi tên và item của các bước chưa kích hoạt phải mờ xuống (`opacity: 0.14 - 0.20`). Khi bước đó active, đường nối sáng bừng 100% kèm chuyển động dòng chảy photon (`.flow-active`).
- **Thanh điều khiển Step**: Có đầy đủ các nút Play, Pause, Next, Prev, Replay và hỗ trợ phím Space, phím Mũi tên.
- **Hạt Photon (Flow Token)** & **Bộ đếm dồn ứ (Queue Accumulator)** nhảy số trực quan.
- **Item-Click Inspector Drawer**: KHÔNG hiển thị các thẻ tĩnh dài dòng dưới đáy sơ đồ. Khi click vào bất kỳ Item nào trên SVG, mở một bảng Inspector Drawer trượt từ bên phải màn hình hiển thị: Tiêu đề, Công thức toán học ngầm, Ngưỡng tham số biên, Cấu trúc I/O và Link clickable tới file mã nguồn gốc. Hỗ trợ phím ESC để đóng.
````

---

## 5. Quy Trình Vận Hành Độc Lập Của Agent

1. **Nhận nhiệm vụ**: Người dùng yêu cầu vẽ bất kỳ hệ thống nào.
2. **Kích hoạt 6 Tầng Nhận Thức**: Tự động dùng `grep_search` và `view_file` để điều tra từ gốc, bóc tách dòng hạt vật lý, săn lùng công thức toán và ngưỡng cắt biên.
3. **Tổng hợp Master Prompt**: Điền toàn bộ phát hiện kỹ thuật vào mẫu đặc tả tại Mục 4.
4. **Thực thi bản vẽ HTML/SVG**: Dùng Master Prompt để kiến tạo sơ đồ khoa học hoàn hảo với Fresh Pastel Palette, Dynamic Opacity và Item-Click Inspector Drawer.
