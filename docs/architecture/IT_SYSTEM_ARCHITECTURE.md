# AIInvest: Tài Liệu Kiến Trúc Hệ Thống Công Nghệ Thông Tin
> **Phiên bản**: 2.0.0 (Enterprise IT Architecture Specification)  
> **Áp dụng cho**: Monorepo AIInvest (Frontend, Backend, AI Engine, SAG Forensic Engine, Storage Tier)  
> **Tài liệu toán học & thuật toán**: [ALGORITHMS_AND_FINANCIAL_MODELS.md](file:///d:/AIInvest/docs/architecture/ALGORITHMS_AND_FINANCIAL_MODELS.md)  
> **Sơ đồ đính kèm**: [docs/diagrams/paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html)

---

## 1. Tổng Quan Kiến Trúc Doanh Nghiệp (Enterprise Architecture Overview)

AIInvest là hệ thống phân tích tài chính định lượng và điều tra gian lận báo cáo tài chính (Financial Forensics) tự động hóa chuyên sâu cho thị trường chứng khoán Việt Nam (HOSE/HNX). 

Hệ thống được thiết kế theo mô hình **Event-Driven Microservices kết hợp Clean Architecture**, phân tách triệt để giữa tầng giao tiếp người dùng, tầng nghiệp vụ điều phối, tầng mô hình định lượng (Quant Intelligence) và tầng dữ liệu chứng cứ pháp lý (Forensics Ground Truth).

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                      TẦNG 1: TRÌNH DIỄN (PRESENTATION TIER)                    │
│   Next.js 15+ App Router · React Server Components · TradingView · WebSockets   │
└───────────────────────────────────────┬────────────────────────────────────────┘
                                        │ HTTPS (Port 443) / WSS
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                   TẦNG 2: API GATEWAY & QUẢN TRỊ NGHIỆP VỤ                     │
│    Express Gateway · Prisma ORM · JWT Auth · Redis Cache & Pub/Sub (:3001)      │
└───────────────────┬───────────────────────────────────────┬────────────────────┘
                    │ REST / gRPC                           │ Event Bus
                    ▼                                       ▼
┌───────────────────────────────────────┐   ┌────────────────────────────────────┐
│   TẦNG 3: QUANT ORG CORE ENGINE       │   │  TẦNG 4: ĐIỀU TRA GIAN LẬN (SAG)   │
│ 12 Quant Agents · Clean Architecture  │   │ SAG v2 Research Core (internal)    │
│ Dual-Book Risk (HOSE T+2.5) (:8000)   │RPC│ MinerU Financial OCR Parser        │
└───────────────────┬───────────────────┘   └─────────────────┬──────────────────┘
                    │                                         │
                    │ SQL Pool (Port 5432)                    │ S3 API (Port 9000)
                    ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                     TẦNG 5: LƯU TRỮ DOANH NGHIỆP (PERSISTENCE)                 │
│ PostgreSQL 16 (Core) · PostgreSQL/pgvector (SAG) · MinIO/S3 (BCTC PDFs & Files) │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phân Rã Kỹ Thuật 5 Tầng (Technical Tier Breakdown)

### Tầng 1: Presentation Tier (`front-end/`)
- **Công nghệ lõi**: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS.
- **Đóng gói Production**: Docker Multi-stage standalone output (`node:22-alpine`), giảm kích thước image xuống ~150MB, chạy dưới quyền user bảo mật không đặc quyền `nextjs`.
- **Tính năng trọng yếu**:
  - **Streaming UI**: Nhận dữ liệu suy luận từng bước (Step-by-step Agent Reasoning) của 12 Agents thông qua Server-Sent Events (SSE) và WebSockets.
  - **Interactive Charting**: Tích hợp TradingView Advanced Charts hiển thị nến OHLCV, chỉ báo kỹ thuật thời gian thực.
  - **3D Evidence Graph**: Trực quan hóa đồ thị liên kết chứng cứ từ báo cáo tài chính bằng đồ thị mạng lưới.
- **Port**: `3000` (Container nội bộ) / `80` & `443` qua Nginx Reverse Proxy.

### Tầng 2: API Gateway & Service Layer (`back-end/`)
- **Công nghệ lõi**: Express 4, TypeScript, Prisma ORM, Redis.
- **Trách nhiệm kỹ thuật**:
  - **API Gateway & Routing**: Tiếp nhận toàn bộ request từ người dùng, thực hiện rate limiting, CORS, và SSL Termination.
  - **Identity & Access Management (IAM)**: Xác thực JWT Bearer, phân quyền Role-Based Access Control (RBAC).
  - **State & Portfolio Management**: Quản lý tài khoản, danh mục đầu tư người dùng, theo dõi số dư tiền và cổ phiếu.
  - **Redis Caching & Session**: Lưu trữ session, blacklist token và làm cầu nối pub/sub cho các sự kiện thị trường.
- **Port**: `3001`.

### Tầng 3: Core Computational & Quant Engine (`ai-engine/`)
- **Công nghệ lõi**: Python 3.11+, FastAPI, Pydantic v2, Celery/Async Workers.
- **Tổ chức Clean Architecture**:
  - **Domain Core (`app/domain/`)**: Thực thể kinh doanh thuần túy, quy tắc giao dịch T+2.5, biên độ giá trần/sàn ±7% của sàn HOSE. Không phụ thuộc bất kỳ ORM hay framework nào.
  - **Application Layer (`app/application/`)**: Điều phối use case, pipeline phân tích BCTC, kịch bản tranh luận giữa 12 agents định lượng.
  - **Infrastructure Layer (`app/infrastructure/`)**: Triển khai repository cụ thể (SQLAlchemy), Redis driver. SAG connector/pipeline code is quarantined for research and is not imported by the runtime Agent graph.
  - **Presentation Layer (`app/presentation/`)**: Router FastAPI đóng gói RESTful endpoints, DTO schema validation.
- **12 Autonomous Quant Agents**:
  1. *Macro Agent*: Phân tích lãi suất, lạm phát, tỷ giá USD/VND.
  2. Forensic/SAG research is outside the production Agent graph and has no runtime decision output.
  3. *Moat Agent*: Đánh giá hào kinh tế (kinh tế quy mô, lợi thế vô hình).
  4. *Valuation Agent*: Định giá DCF, P/E, P/B chiết khấu dòng tiền.
  5. *Cash Flow Quality Agent*: Chất lượng dòng tiền hoạt động kinh doanh (CFO/Net Income).
  6. *Sentiment Agent*: Phân tích tin tức báo chí, diễn đàn tài chính.
  7. *Technical Agent*: Tín hiệu động lượng, phân kỳ RSI, MACD.
  8. *Risk Parity Agent*: Phân bổ tỷ trọng danh mục rủi ro cân bằng.
  9. *Liquidity Agent*: Kiểm tra khối lượng khớp lệnh thị trường HOSE.
  10. *Peer Relative Agent*: So sánh tương quan cùng ngành.
  11. *Catalyst Agent*: Sự kiện trọng yếu (M&A, tăng vốn, cổ tức).
  12. *Execution Arbiter Agent*: Tổng hợp tranh luận và đưa ra quyết định đặt lệnh.
- **Port**: `8000`.

### Tầng 4: Financial Forensics & OCR Engine (`SAG/` & `MinerU`)
- **Công nghệ lõi**: MinerU PDF Parser, SAG v2 Python Evidence Engine.
- **Nguyên lý Zero-Hallucination**:
  - **MinerU**: Trích xuất bảng biểu phức tạp và văn bản báo cáo tài chính dạng PDF quét thành Markdown với neo dòng chính xác (line-anchored).
  - **SAG v2 (Statement Analysis & Governance)**: 
    - **quote_hash**: Băm SHA-256 từng dòng thông tin tài chính để lưu dấu vết chứng cứ không thể giả mạo.
    - **GIL (Governance / Integrity / Liability Risk)**: Thuật toán nhận diện dấu hiệu gian lận báo cáo tài chính (doanh thu ảo, phải thu đột biến, tồn kho ảo).
    - **MOAT Score**: Tính toán điểm số lợi thế cạnh tranh bền vững của doanh nghiệp.
- **Port**: `8000` inside the `research` Compose profile only; no public SAG port.

### Tầng 5: Enterprise Persistence & Storage Tier
- **PostgreSQL 16 (Core)**: Lưu tài khoản, tiền mặt, vị thế, lệnh, dữ liệu thị trường và kết quả định lượng.
- **Portfolio account identity**: `portfolio_account.account_id` stores up to 64 characters so it can use the same UUID identifier as `users.id`.
- **PostgreSQL 16 + pgvector (SAG)**: CSDL tách biệt cho tài liệu, chứng cứ, đồ thị và vector pháp y. SAG chỉ đọc dữ liệu thị trường Core qua kết nối riêng.
- **MinIO / AWS S3**: Lưu trữ các file PDF gốc BCTC, tài liệu công bố thông tin và báo cáo xuất ra.
- **Redis 7**: Cache dữ liệu bảng giá, ticker OHLCV và kênh truyền Pub/Sub giữa các microservices.
- **Ports**: PostgreSQL (`5432`), Redis (`6379`), MinIO (`9000`/`9001`).

---

## 3. Vùng An Toàn & Phân Tách Mạng (Security & Trust Boundaries)

```
[ INTERNET / EXTERNAL CLIENTS ]
             │
             ▼ (HTTPS / TLS 1.3 - Port 443)
┌─────────────────────────────────────────────────────────┐
│ ZONE 1: PUBLIC DMZ (Reverse Proxy / NGINX / Cloudflare) │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼ (Internal VPC Subnet 10.0.1.0/24)
┌─────────────────────────────────────────────────────────┐
│ ZONE 2: APPLICATION SERVICE MESH                        │
│   • front-end (:3000)                                   │
│   • back-end Express API Gateway (:3001)                │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼ (Private Subnet 10.0.2.0/24 - mTLS)
┌─────────────────────────────────────────────────────────┐
│ ZONE 3: COMPUTATIONAL INTEL CORE                        │
│   • ai-engine Quant Org (:8000)                         │
│   • SAG v2 Forensics Core (:8001)                       │
│   • MinerU OCR Workers                                  │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼ (Isolated DB Subnet 10.0.3.0/24 - No Public IP)
┌─────────────────────────────────────────────────────────┐
│ ZONE 4: ENTERPRISE DATA STORAGE                         │
│   • PostgreSQL 16 Core (:5432)                          │
│   • PostgreSQL/pgvector SAG (internal :5432)            │
│   • Redis Cache (:6379)                                 │
│   • MinIO S3 Object Storage (:9000)                     │
└─────────────────────────────────────────────────────────┘
```

### Chính sách an toàn thông tin:
1. **Zero External Access to Database**: CSDL PostgreSQL và Redis không gán Public IP, chỉ lắng nghe kết nối từ mạng nội bộ Docker/VPC.
2. **Strict Identity Propagation**: Mọi request qua API Gateway đều được xác thực JWT, giải mã `userId`, và truyền tải dưới dạng header tin cậy nội bộ (`x-user-id`).
3. **Evidence Immutability**: Các bản băm `quote_hash` sau khi ghi vào PostgreSQL được bảo vệ bằng quyền ghi Append-Only đối với các bảng bằng chứng.
4. **Service Authentication**: Route quản trị của Backend dùng `INTERNAL_SERVICE_TOKEN`; route quản trị AI Engine dùng `AI_ENGINE_ADMIN_TOKEN`; Backend truyền token này qua `X-Admin-Token`. SAG production yêu cầu `SAG_SERVICE_TOKEN` và `SAG_SECRET_KEY` mạnh, không chấp nhận giá trị mặc định.
5. **Atomic Portfolio Ledger**: Tiền, vị thế và lệnh được cập nhật trong transaction có khóa tài khoản. DB áp đặt một vị thế cho mỗi `(user_id, symbol)`, reaction chỉ được trỏ tới đúng một post hoặc comment, và `paper_trades` luôn có `account_id`.
6. **Reverse Proxy DMZ Isolation**: Toàn bộ client truy cập qua Nginx Reverse Proxy (cổng 80/443). Nginx thực hiện định tuyến `/` -> frontend, `/api/` -> backend, `/socket.io/` -> WebSocket, bật Gzip nén, và giấu toàn bộ port microservices và database phía sau mạng nội bộ `aiinvest_default`.
7. **Automated Database Lifecycle & Healthcheck Gating**: Job `migrate` chạy `prisma migrate deploy` trước AI Engine và Backend. Backend chỉ phục vụ request sau khi AI Engine healthy. SAG và migration Alembic chỉ chạy trong profile `research`. Docker Compose dùng `condition: service_healthy` và log rotation (`max-size: 50m`, `max-file: 5`).

---

## 4. Đặc Tả Pipeline Dữ Liệu BCTC & Ra Quyết Định

Quy trình khép kín từ khi phát hiện BCTC mới đến khi sinh lệnh mua/bán:

1. **Ingestion**: Người dùng upload hoặc crawler định kỳ kéo BCTC định dạng PDF từ cổng thông tin HOSE/UBCK về MinIO.
2. **OCR Parsing**: MinerU đọc PDF, chuyển đổi toàn bộ bảng cân đối kế toán, kết quả kinh doanh, lưu chuyển tiền tệ và thuyết minh thành Markdown có định vị số dòng.
3. **Research-only Evidence Extraction**: `bctc_to_sag_pipeline.py` and `sag_connector.py` remain available only for isolated SAG research; production ETL does not dispatch them.
4. **Research-only Fraud & Moat Scoring**: GIL/MOAT outputs are not current inputs to production Agents while SAG is frozen.
5. **Independent Multi-Agent Consensus**: The production orchestrator runs on Core financial, market, audit, Beneish, liquidity and thesis evidence without a SAG payload or universal forensic gate.
6. **Dual-Book Portfolio Allocation**: Nếu vượt qua vòng thẩm định pháp y, Risk Parity Agent phân bổ tỷ trọng dựa trên quy tắc HOSE (T+2.5, biên độ trần/sàn ±7%).
7. **Order Dispatch**: Khi chưa có broker gateway, mọi cấu hình `LIVE` bị từ chối. Shadow BUY lưu thành `PENDING_SHADOW` trong bảng `orders`, dùng giá duyệt làm giới hạn trong ngày. Một worker tuần tự quét mọi mã chờ trong phiên liên tục: nghỉ 1 giây sau lượt quét khi có lệnh chờ, 5 giây khi rảnh. Khi đủ thanh khoản ở mức giá cho phép, chính order đó được cập nhật cùng cash, position và execution trong một transaction PostgreSQL. Lệnh chưa khớp hết hạn cuối phiên. Không có lệnh nào gửi lên DNSE.

---

## 5. Thư Viện Sơ Đồ Trực Quan Tương Tác (Interactive Diagrams Hub)

Toàn bộ các sơ đồ tương tác tự chứa (standalone SVG/HTML) được lưu trữ tại thư mục [docs/diagrams/](file:///d:/AIInvest/docs/diagrams/):

### Sơ Đồ Thuật Toán & Biến Đổi Biểu Diễn Dữ Liệu Khoa Học (NeurIPS/SIGMOD Standard)
- **[paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html)**: Sơ đồ phương pháp luận báo cáo khoa học Paper-Grade: Bóc tách hình thái dữ liệu 2 pha (Offline Ingestion AST, băm `quote_hash` SHA-256, chiếu siêu đồ thị $M:N$, không gian vector 1024 chiều vs Online Parallel Fork-Join Retrieval, bộ tích lũy Queue Accumulator $32 \to 24 \to 8$, điều khiển tương tác Step Motion có phím tắt, Dynamic Flow Opacity và bảng khảo sát chi tiết Inspector Drawer khi nhấp vào từng node).

Chi tiết hướng dẫn mở, tương tác và xem sơ đồ được mô tả tại [docs/diagrams/README.md](file:///d:/AIInvest/docs/diagrams/README.md).
### SAG boundary hiện hành (2026-09-22)

SAG là service nghiên cứu độc lập, không phải Agent quyết định thứ 13 và không nằm trong runtime dependency graph của `ai-engine`. Compose chỉ khởi động SAG bằng profile tường minh `research`; ETL không dispatch BCTC vào SAG; `DailyInvestmentPipeline` không gọi connector/adapter và các Decision Agent không nhận `ForensicAssessment`, GIL flag hay dữ liệu SAG cũ. Mã connector, pipeline và contract cũ được giữ trong vùng nghiên cứu để không mất lịch sử và phục vụ nghiên cứu riêng.

Khi cần đưa SAG/MOAT trở lại, chỉ tạo một contract tích hợp mới sau khi nghiên cứu hoàn tất, kèm caller/contract, migration, rollback, observability và fail-closed tests. Không nối trực tiếp SAG vào từng Agent và không dùng lại legacy `gil_flag` như dữ liệu hiện hành; `SAG_HOLD` chỉ là sentinel tương thích cho cột legacy `NOT NULL`, không phải kết quả phân tích.
