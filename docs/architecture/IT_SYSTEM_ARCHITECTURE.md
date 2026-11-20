# AIInvest: Tài Liệu Kiến Trúc Hệ Thống Công Nghệ Thông Tin
> **Phiên bản**: 2.0.0 (Enterprise IT Architecture Specification)  
> **Áp dụng cho**: Monorepo AIInvest (Frontend, Backend, AI Engine, SAG Stock Research Engine, Storage Tier)  
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
│   TẦNG 3: QUANT ORG CORE ENGINE       │   │  TẦNG 4: STOCK RESEARCH RAG (SAG)  │
│ 12 Quant Agents · Clean Architecture  │   │ Knowledge Routing RAG (v1, isolated)│
│ Dual-Book Risk (HOSE T+2.5) (:8000)   │   │ Smart Hybrid MinerU + Leiden Tree  │
└───────────────────┬───────────────────┘   └─────────────────┬──────────────────┘
                    │                                         │
                    │ SQL Pool (Port 5432)                    │ PG + Qdrant
                    ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                     TẦNG 5: LƯU TRỮ DOANH NGHIỆP (PERSISTENCE)                 │
│  PostgreSQL 16 (Core) · PostgreSQL 16 + Qdrant (SAG) · Cloudflare R2 / Redis   │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phân Rã Kỹ Thuật 5 Tầng (Technical Tier Breakdown)

### Tầng 1: Presentation Tier (`front-end/`)
- **Công nghệ lõi**: Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS.
- **Đóng gói Production**: Docker Multi-stage standalone output (`node:22-alpine`), giảm kích thước image xuống ~150MB, chạy dưới quyền user bảo mật không đặc quyền `nextjs`.
- **Tính năng trọng yếu**:
  - **Streaming UI**: Nhận dữ liệu suy luận từng bước (Step-by-step Agent Reasoning) của 12 Agents thông qua Server-Sent Events (SSE) và WebSockets.
  - **Interactive Charting (`@/lib/tv-chart`)**: Hệ thống biểu đồ hợp nhất `<TradingViewChart />` trên toàn bộ mặt phẳng làm việc (`/trade`, `StockDetailModal`), thay thế triệt để `klinecharts`, hỗ trợ độ chính xác 2 chữ số thập phân cho chỉ số (`VNINDEX`, `VN30`, `VN100`), chuỗi lịch sử tối đa `5000` phiên (`14,5+` năm), cô lập dữ liệu theo từng mã cổ phiếu khi chuyển đổi ticker, chế độ `compact` không che khuất nến và đồng bộ Fullscreen native (`fullscreenchange` / `Esc`).
  - **Collapsible Workspace Rail (`Shell.tsx`)**: Mặc định thu gọn (`64px`) chỉ hiển thị icon `lucide-react` riêng biệt cho 6 phân hệ (`/agent`, `/portfolio`, `/discovery`, `/trade`, `/sectors`, `/research`) kèm floating tooltip khi hover/focus; khi mở rộng (`224px`) chỉ hiển thị nhãn văn bản. Trang mặc định sau đăng nhập/fallback chuyển sang `/agent` (`AI War Room`); các route `/dashboard`, `/markets`, `/watchlist`, `/backtest`, `/settings`, `/help`, `/ml-fund` đã được gỡ bỏ và `/community` được ẩn khỏi điều hướng UI.
  - **Global In-Place Research Modal (`StockDetailModal`)**: Tích hợp toàn cục tại `Shell.tsx` kết hợp bộ chặn điều hướng `/stock/[symbol]` trong `router.tsx`, mở hồ sơ cổ phiếu ngay tại chỗ mà không làm gián đoạn trạng thái nghiên cứu của người dùng.
  - **3D Evidence Graph**: Trực quan hóa đồ thị liên kết chứng cứ từ báo cáo tài chính bằng đồ thị mạng lưới.
- **Port**: `3000` (Container nội bộ) / `80` & `443` qua Nginx Reverse Proxy.

#### Research: luận điểm mới nhất và lịch sử

- `GET /api/v1/workspace/research` trả luận điểm mới nhất theo từng ticker từ `investment_theses`, kèm phán quyết Agent 05 từ `counter_thesis_verdicts`. Thời điểm `generated_at` ưu tiên nhật ký tạo luận điểm để tránh lỗi múi giờ của `created_at` cũ, fallback về `created_at` nếu thiếu nhật ký. Trạng thái duyệt ở đây không phải xác nhận của CIO.
- `GET /api/v1/workspace/research/thesis-history?page=1&ticker=HPG&date=2026-09-27` đọc `log_investment_thesis`, phân trang 20 bản lưu, lọc ticker và `analysis_date` của phiên phân tích/replay. `created_at` giữ thời điểm thực sự ghi log (UTC, hiển thị Việt Nam). Bản lưu cũ thiếu `analysis_date` được nhóm theo ngày ghi log và gắn nhãn thiếu ngày phân tích; không suy đoán ngày replay. Trạng thái lịch sử là trạng thái tại lúc lập; không gắn phán quyết mới nhất vào bản lưu cũ.
- Hai bộ chọn ngày Research và Agent dùng chung lịch `dd/mm/yyyy`, tuần bắt đầu thứ Hai, múi giờ Việt Nam. `dates` của API là ngày có bản ghi (Research áp dụng ticker nhưng không áp dụng ngày đang chọn; ưu tiên ngày phân tích), không phải lịch nghỉ sàn hoặc lịch thanh toán. Riêng Research chỉ cho chọn ngày có bản lưu phù hợp ticker; bấm tab Lịch sử xóa bộ lọc và tải lại toàn bộ bản lưu, hiển thị các ngày gần nhất để truy cập trực tiếp.
- `GET /api/v1/workspace/agent?date=YYYY-MM-DD` lọc nhật ký, luận điểm, phán quyết và quyết định theo ngày Việt Nam. `account` và `positionHealth` trong endpoint nhật ký giữ snapshot giám sát cũ để tương thích; `positionHealth` chỉ chứa mã còn nắm giữ của `MULTI_AGENT_ACCOUNT_ID`. Không dùng trung bình phần trăm trong log Agent 09 làm lợi nhuận tài khoản.
- Trang Agents trình bày báo cáo theo quyết định giao dịch, luận điểm sau thẩm định, mục tiêu theo thời hạn và rủi ro cần quan sát (doanh nghiệp/vĩ mô/kỹ thuật). Các phép tính, đánh giá phản biện chi tiết và dữ liệu gốc nằm trong phần nhật ký kỹ thuật thu gọn. API Agent trả thêm thesis_snapshot từ log mới nhất cùng thesis_id để đọc đầy đủ lý do chọn mã, thời điểm đầu tư và điều kiện quan sát; không sửa nội dung đã lưu.
- Khối giao dịch gộp kết quả hiển thị Agent 06–07–08. Số lượng/tỷ lệ được duyệt lấy từ log rủi ro có es_97_5_inputs.proposed_order.decision_id khớp đúng quyết định phân bổ; thiếu kết quả thì chờ duyệt, không lấy số lượng đề xuất thay thế. Tỷ lệ này là giá trị lệnh trên NAV, không phải tỷ trọng nắm giữ cuối cùng. Log thực thi được hiển thị riêng theo thời điểm/mã lệnh, có nhãn mô phỏng; trạng thái chờ không được tính là khớp. Nguồn hiện chưa có định danh bền để nối luận điểm, phân bổ và thực thi thành một lượt duy nhất.
- Các nhóm lướt sóng/trung hạn/dài hạn chỉ phục vụ trình bày thời gian đã lưu; mỗi mục tiêu giữ nguyên thời hạn và giá của luận điểm. Nhóm chưa có phân tích hiển thị thiếu dữ liệu. UI không sinh mục tiêu kỳ hạn mới hoặc đổi chính sách giao dịch. Xem [nguyên tắc đang có trong code](AGENT_INVESTMENT_PRINCIPLES.md) để đọc chọn mã, cắt lỗ và quản trị danh mục.
- Kết quả tài khoản được thu gọn để ưu tiên quyết định đầu tư. Khung workspace dùng bộ chọn điều hướng trên màn hình nhỏ thay cho thanh bên cố định; các đường dẫn và chức năng tìm kiếm được giữ nguyên.
- `GET /api/v1/workspace/agent/portfolio` trả định giá hiện tại của quỹ Multi-Agent bằng cùng service với `GET /api/v1/portfolio/snapshot` (endpoint Portfolio yêu cầu JWT và dùng `req.userId`). Giao diện ghi rõ tài khoản. Snapshot gồm `summary`, `positions`, `performance`, `risks`, `orders`; tiền mặt, vị thế, hóa đơn và lịch sử NAV đọc trong transaction Repeatable Read. Mỗi mã chỉ gọi quote một lần. Agent làm mới tài khoản riêng, không tải lại toàn bộ nhật ký mỗi phút.
- Lãi đã chốt tính theo giá vốn bình quân di động từ `orders` + `order_executions`, phân bổ phí mua khi bán từng phần, trừ phí bán/thuế. Lãi chưa chốt = giá trị vị thế - giá vốn gồm phí mua; không trừ phí bán dự kiến. Tổng lãi/lỗ là tổng hai phần; tỷ suất tài khoản chia cho vốn ban đầu suy ra từ tiền mặt và toàn bộ hóa đơn. Hệ thống chưa có sổ nạp/rút/cổ tức; không coi công thức này là TWR/MWR có dòng tiền ngoài. Thiếu hóa đơn hoặc không khớp lượng/giá vốn thì trả null cho lãi/lỗ sau phí, không dùng `paper_trades` thay thế.
- Quote DNSE có đơn vị VND, cần nguồn `dnse-ws`, timestamp nhận không quá 30 giây và không stale. Fallback dùng giá đóng cửa có ngày, không thay bằng giá vốn. Giao diện dùng Socket.IO subscriptions chung, gom tick một giây/lần, đọc lại trạng thái tài khoản mỗi phút khi tab đang mở. Không ghi DB theo từng tick. NAV/risk dùng lịch sử EOD riêng; daily P&L cần snapshot phiên trước (thiếu baseline trả null). Sharpe/Alpha/Beta cần tối thiểu 20 lợi suất ngày; benchmark VNINDEX đồng bộ ngày, lãi suất phi rủi ro 0%; không annualize khoảng trống nhiều phiên thành lợi suất một ngày. Drawdown tính trên các snapshot hiện có, không suy diễn đáy trong khoảng thiếu dữ liệu.
- Bảng giá dùng key Redis bền `stock:{symbol}:quote` làm snapshot mới nhất: tick DNSE ghi đè theo mã, ETL EOD làm mới từ `market_data_daily.close_unadj` (fallback `close_adj`). Snapshot thị trường và socket đọc Redis trước; quote API dùng PostgreSQL khi key thiếu hoặc Redis không truy cập được. PostgreSQL vẫn là nguồn khôi phục; không ghi từng tick vào DB. Order book chỉ có snapshot Redis và bị đánh dấu/ẩn khi quá cũ, vì DB không lưu lịch sử bid/ask.
- Trần/sàn DNSE nằm trong key bền `stock:{symbol}:sec_def`, được khôi phục khi stream hub khởi động lại và ghép vào snapshot API/EOD. Giá trị rỗng của quote không ghi đè bộ trần/sàn hợp lệ; `priceBandAsOf` giữ thời điểm nguồn của bộ giá này. Ghép trần/sàn không đổi tham chiếu hoặc biến động của giá EOD; chỉ bản tin đúng ngày hiện tại cập nhật tham chiếu trong metadata realtime. Redis lưu bộ giá mới nhất, không phải lịch sử trần/sàn theo từng phiên.
- Tỷ trọng ngành trên Dashboard và Sectors dùng cùng phép tính: vốn hóa khi xác nhận có đủ cho mọi mã trong các ngành, nếu thiếu dữ liệu hoặc độ phủ dùng số mã nhất quán. Heatmap đếm toàn bộ mã trong universe kể cả mã thiếu giá/tham chiếu; biến động chỉ tính từ các mã có giá hợp lệ và trả null khi chưa có. `marketCapCount`/`market_cap_count` mô tả độ phủ vốn hóa; UI ghi rõ cơ sở tỷ trọng và hiển thị tất cả ngành. API giữ vốn hóa/độ phủ của heatmap live cùng nguồn, chỉ ghép lịch sử cho sparkline và khối ngoại. Đồng bộ danh mục DNSE và snapshot không xóa vốn hóa hiện có khi nguồn thiếu hoặc trả giá trị không hợp lệ; `/instruments` không cung cấp vốn hóa trong payload đã đối chiếu. SSI là cổ phiếu hợp lệ, không bị loại bởi bộ lọc tiền tố ETF.
- **Ngừng vận hành kênh ML Tự hành (`/ml-fund`) — một thất bại không thể từ chối**: Trang `/ml-fund`, biểu đồ `NavLineChart.tsx`, endpoint `GET /api/v1/workspace/ml-fund`, dịch vụ `standalone_ml_channel.py` và các script/worker độc lập tương ứng đã được gỡ bỏ hoàn toàn khỏi hệ thống runtime. Kênh ML độc lập thiếu nền tảng phân tích cơ bản nhân quả và hội đồng phản biện đối kháng không chứng minh được hiệu quả thực chiến nên được ghi nhận là **một thất bại không thể từ chối**; kiến trúc 12 Multi-Agent là kênh đầu tư tự hành duy nhất được duy trì (schema lịch sử `standalone_ml_predictions` được giữ thụ động trong Prisma để bảo toàn tính toàn vẹn checksum migration).
- Agent 04 tạo revision riêng khi không truyền `seq_num`, tránh ghi đè giữa các lần chạy. Sequence được truyền tường minh vẫn giữ định dạng cũ. Thời gian lưu luận điểm/phán quyết dùng UTC có múi giờ; khi cập nhật cùng ID, thời điểm tạo luận điểm được giữ nguyên.
- Phán quyết cập nhật đồng thời các thành phần CTS, điểm cuối, cổng kiểm tra, rủi ro và giới hạn thực thi. Giao diện cảnh báo nếu các thành phần CTS cũ không khớp điểm cuối; dữ liệu cũ không được tự sửa hoặc suy diễn lại.

### Tầng 2: API Gateway & Service Layer (`back-end/`)
- **Công nghệ lõi**: Express 4, TypeScript, Prisma ORM, Redis.
- **Trách nhiệm kỹ thuật**:
  - **API Gateway & Routing**: Tiếp nhận toàn bộ request từ người dùng, thực hiện rate limiting, CORS, và SSL Termination.
  - **Identity & Access Management (IAM)**: Xác thực JWT Bearer, phân quyền Role-Based Access Control (RBAC).
  - **State, Portfolio & Million-OPS LIVE Matching Engine (`matchingEngine.ts` + `portfolio.service.ts`)**: Vận hành sổ lệnh giới hạn `ArenaOrderBook` cấp phát phẳng `TypedArray` không sinh rác bộ nhớ (`Zero-GC`, `131.072` slot, bảng bước giá `10 ₫` kết hợp bitset phân cấp 3 tầng `Math.clz32` tra cứu Best Bid/Ask `O(1)`, đạt thông lượng **> 4,5 – 18,1 triệu lệnh/giây** trên 1 nhân) cùng `LiveOrderGateway` đối chiếu độ sâu L1–L3 trực tiếp từ DNSE (`LO`, `MP`/`MTL`, `ATO`, `ATC`; `FILLED`, `PARTIALLY_FILLED`, `PENDING`, `CANCELLED`), phong tỏa `lockedCash` / `lockedShares` chuẩn chu kỳ thanh toán `T+2.5` VSDC, hỗ trợ sửa/hủy lệnh `O(1)` (`PATCH`/`DELETE /api/v1/portfolio/order/:id`) và đẩy cập nhật tức thời qua Socket.IO (`portfolio:order_updated`).
  - **Redis Caching & Session**: Lưu trữ session, blacklist token và làm cầu nối pub/sub cho các sự kiện thị trường.
- **Port**: `3001`.

### Tầng 3: Core Computational & Quant Engine (`ai-engine/`)
- **Công nghệ lõi**: Python 3.11+, FastAPI, Pydantic v2, Celery/Async Workers.
- **Tổ chức Clean Architecture**:
  - **Domain Core (`app/domain/`)**: Thực thể kinh doanh thuần túy, quy tắc giao dịch T+2.5, biên độ giá trần/sàn ±7% của sàn HOSE. Không phụ thuộc bất kỳ ORM hay framework nào.
  - **Application Layer (`app/application/`)**: Điều phối use case, pipeline phân tích BCTC, kịch bản tranh luận giữa 12 agents định lượng.
  - **Infrastructure Layer (`app/infrastructure/`)**: Triển khai repository cụ thể (SQLAlchemy), Redis driver. Legacy GIL/SAG v2 connectors and pipelines have been completely removed and are not imported by the runtime Agent graph.
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

### Tầng 4: Stock Research Knowledge Routing RAG Engine (`SAG/`)
- **Công nghệ lõi**: FastAPI + `zleap-sag==0.12.0` + PostgreSQL 16 (source of truth) + Qdrant (vector accelerator) + Smart Hybrid MinerU / isolated local parser.
- **Phạm vi duy nhất**: Nghiên cứu cổ phiếu chuyên sâu (điểm mạnh, điểm yếu cấu trúc và lợi thế cạnh tranh / **MOAT**). Toàn bộ phân hệ **GIL (Graph Intelligence Layer)** và API `/api/v2/financial` cũ đã được loại bỏ hoàn toàn.
- **Kiến trúc Knowledge Routing RAG (0 LLM calls lúc Ingest)**:
  - **Canonical Extraction & Dedup/Versioning**: Phân rã tài liệu thành `CanonicalBlock` có neo vị trí/trang, chống trùng 4 tầng (SHA-256 + MinHash) và quản lý `DocumentVersion` theo thời gian.
  - **Search Path (`SEARCH_READY`)**: Xây dựng `SearchUnit` và chỉ mục lai Dense + Sparse trên Qdrant với bộ nhớ đệm embedding dùng chung (`SAG_DOCUMENT_EVENT_EXTRACTION_ENABLED=false`).
  - **Knowledge Foundation (`KNOWLEDGE_READY`)**: Trích xuất tất định `E0/E1` (loại bỏ hoàn toàn pha E2 LLM enrichment theo ADR benchmark), dựng `DenseGraph` (`KnowledgeUnitEdge`), phân cụm **Constrained Hierarchical Leiden** tạo `Knowledge Routing Tree`, và xuất bản không gián đoạn qua giao thức **Blue-Green dual-slot (`A/B`)**.
  - **Adaptive Retrieval & Grounded Citations**: Định tuyến chùm trên cây tri thức kết hợp ngân sách global escape, truy hồi lai cục bộ + RRF + MMR + mở rộng đa bước HippoRAG 2 giới hạn, kèm theo dõi token chuẩn xác (`token_usage.py`).
- **Port**: `8000` inside the `research` Compose profile only; no public SAG port.

### Tầng 5: Enterprise Persistence & Storage Tier
- **PostgreSQL 16 (Core)**: Lưu tài khoản, tiền mặt, vị thế, lệnh, dữ liệu thị trường và kết quả định lượng.
- **Portfolio account identity**: `portfolio_account.account_id` stores up to 64 characters so it can use the same UUID identifier as `users.id`.
- **Alpha research storage**: `alpha_signals` is retired because the current application has no readers or writers.
- **Trading decisions**: Orders are produced through the Agent workflow and risk approval; the detached daily BUY/HOLD/SELL feed and its UI/API are retired.
- **PostgreSQL 16 + Qdrant (SAG)**: CSDL tách biệt (`sag-postgres` + `sag-qdrant` hoặc Qdrant Cloud) cho `CanonicalBlock`, `DocumentVersion`, `KnowledgeUnit`, `Knowledge Routing Tree` và chỉ mục vector lai trong profile `research`.
- **Cloudflare R2 / Object Storage**: Lưu trữ các file PDF gốc BCTC/BCTN, tài liệu công bố thông tin và báo cáo xuất ra.
- **Redis 7**: Cache dữ liệu bảng giá, ticker OHLCV và kênh truyền Pub/Sub giữa các microservices.
- **Ports**: PostgreSQL (`5432`), Qdrant (`6333` research-only), Redis (`6379`).

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
│   • SAG Knowledge Routing RAG (:8000, research profile) │
│   • Smart Hybrid MinerU / Local Parse Workers           │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼ (Isolated DB Subnet 10.0.3.0/24 - No Public IP)
┌─────────────────────────────────────────────────────────┐
│ ZONE 4: ENTERPRISE DATA STORAGE                         │
│   • PostgreSQL 16 Core (:5432)                          │
│   • PostgreSQL 16 SAG (research profile, internal :5432)│
│   • Qdrant Vector Engine (research profile, :6333)      │
│   • Redis Cache (:6379)                                 │
│   • Cloudflare R2 Object Storage                        │
└─────────────────────────────────────────────────────────┘
```

### Chính sách an toàn thông tin:
1. **Zero External Access to Database**: CSDL PostgreSQL và Redis không gán Public IP, chỉ lắng nghe kết nối từ mạng nội bộ Docker/VPC.
2. **Strict Identity Propagation**: Mọi request qua API Gateway đều được xác thực JWT, giải mã `userId`, và truyền tải dưới dạng header tin cậy nội bộ (`x-user-id`).
3. **Evidence Immutability**: Các bản băm `quote_hash` / `content_hash` của `CanonicalBlock` và trích dẫn nguồn sau khi ghi vào PostgreSQL được kiểm chứng toàn vẹn và gắn neo vị trí/trang.
4. **Service Authentication**: Route quản trị của Backend dùng `INTERNAL_SERVICE_TOKEN`; route quản trị AI Engine dùng `AI_ENGINE_ADMIN_TOKEN`; Backend truyền token này qua `X-Admin-Token`. SAG production yêu cầu `SAG_BFF_SERVICE_TOKEN` (>=32 ký tự) hoặc RS256 JWKS Principal Assertion và `SAG_SECRET_KEY` mạnh (>=32 byte), từ chối khởi động khi dùng khóa rỗng, ngắn hoặc mặc định.
5. **Atomic Portfolio Ledger**: Tiền, vị thế và lệnh được cập nhật trong transaction có khóa tài khoản. DB áp đặt một vị thế cho mỗi `(user_id, symbol)`, reaction chỉ được trỏ tới đúng một post hoặc comment, và `paper_trades` luôn có `account_id`.
6. **Reverse Proxy DMZ Isolation**: Toàn bộ client truy cập qua Nginx Reverse Proxy (cổng 80/443). Nginx thực hiện định tuyến `/` -> frontend, `/api/` -> backend, `/socket.io/` -> WebSocket, bật Gzip nén, và giấu toàn bộ port microservices và database phía sau mạng nội bộ `aiinvest_default`.
7. **Automated Database Lifecycle & Healthcheck Gating**: Job `migrate` chạy `prisma migrate deploy` trước AI Engine và Backend. Backend chỉ phục vụ request sau khi AI Engine healthy. SAG (`sag-postgres`, `sag-qdrant`, `sag-api` với SQL migrations `0001..0012` và `init_db()`) chỉ chạy trong profile `research`. Docker Compose dùng `condition: service_healthy` và log rotation (`max-size: 50m`, `max-file: 5`).

---

## 4. Đặc Tả Pipeline Dữ Liệu BCTC & Ra Quyết Định

Quy trình khép kín từ khi phát hiện BCTC mới đến khi sinh lệnh mua/bán:

1. **Ingestion**: Tài liệu công bố nghiên cứu cổ phiếu (BCTN, Thuyết minh BCTC hợp nhất kiểm toán, NQ ĐHĐCĐ khi chạm điều kiện) được chuẩn bị theo đặc tả MOAT và nạp vào SAG ở profile `research`.
2. **Canonical Extraction & Indexing**: Smart Hybrid MinerU / local worker chuẩn hóa thành `CanonicalBlock`, dedup SHA-256/MinHash, tạo `SearchUnit` trên Qdrant và `KnowledgeUnit` + `DenseGraph` + cây Leiden (`FOUNDATION`, 0 LLM calls).
3. **Isolated Research Service**: Legacy GIL/BCTC v2 connector and pipeline modules (`bctc_to_sag_pipeline.py`, `sag_connector.py`, `document_selector.py`, `forensic_assessment.py`) have been removed; production ETL does not dispatch to SAG.
4. **Research-only MOAT Grounding**: MOAT evidence outputs are not yet wired into production Decision Agents while Rule 06 remains active.
5. **Independent Multi-Agent Consensus**: The production orchestrator runs on Core financial, market, audit, Beneish, liquidity and thesis evidence without a SAG payload or any GIL gate.
6. **Dual-Book Portfolio Allocation**: Nếu vượt qua vòng thẩm định pháp y, Risk Parity Agent phân bổ tỷ trọng dựa trên quy tắc HOSE (T+2.5, biên độ trần/sàn ±7%).
7. **Order Dispatch**: Khi chưa có broker gateway, mọi cấu hình `LIVE` bị từ chối. Shadow BUY lưu thành `PENDING_SHADOW` trong bảng `orders`, dùng giá duyệt làm giới hạn trong ngày. Một worker tuần tự quét mọi mã chờ trong phiên liên tục: nghỉ 1 giây sau lượt quét khi có lệnh chờ, 5 giây khi rảnh. Khi đủ thanh khoản ở mức giá cho phép, chính order đó được cập nhật cùng cash, position và execution trong một transaction PostgreSQL. Lệnh chưa khớp hết hạn cuối phiên. Không có lệnh nào gửi lên DNSE.

---

## 5. Thư Viện Sơ Đồ Trực Quan Tương Tác (Interactive Diagrams Hub)

Toàn bộ các sơ đồ tương tác tự chứa (standalone SVG/HTML) được lưu trữ tại thư mục [docs/diagrams/](file:///d:/AIInvest/docs/diagrams/):

### Sơ Đồ Thuật Toán & Biến Đổi Biểu Diễn Dữ Liệu Khoa Học (NeurIPS/SIGMOD Standard)
- **[paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html)**: Sơ đồ phương pháp luận báo cáo khoa học Paper-Grade: Bóc tách hình thái dữ liệu 2 pha (Offline Ingestion AST, băm `quote_hash` SHA-256, chiếu siêu đồ thị $M:N$, không gian vector 1024 chiều vs Online Parallel Fork-Join Retrieval, bộ tích lũy Queue Accumulator $32 \to 24 \to 8$, điều khiển tương tác Step Motion có phím tắt, Dynamic Flow Opacity và bảng khảo sát chi tiết Inspector Drawer khi nhấp vào từng node).

Chi tiết hướng dẫn mở, tương tác và xem sơ đồ được mô tả tại [docs/diagrams/README.md](file:///d:/AIInvest/docs/diagrams/README.md).
### SAG boundary hiện hành (2026-11-20)

SAG đã được loại bỏ hoàn toàn toàn bộ phần **GIL (Graph Intelligence Layer)** và pipeline BCTC v2 cũ (`sag_connector.py`, `sag_evidence_agent.py`, `forensic_assessment.py`, `bctc_to_sag_pipeline.py`, `document_selector.py`, `gil_service.py`, `api/v2/financial.py`, `gil-decision-tree-gating.html`) và thay thế bằng kiến trúc **Knowledge Routing RAG** sạch (`zleap-sag==0.12.0`, PostgreSQL 16 + Qdrant, 0 LLM calls lúc ingest).

SAG vẫn là service nghiên cứu độc lập, không phải Agent quyết định thứ 13 và không nằm trong runtime dependency graph của `ai-engine`. Compose chỉ khởi động SAG (`sag-postgres`, `sag-qdrant`, `sag-api`) bằng profile tường minh `research`; `DailyInvestmentPipeline` và 12 Decision Agents không gọi SAG trong lúc Rule 06 còn hiệu lực.

Khi triển khai tích hợp **Stock Research / MOAT** (theo đặc tả đầu vào 3 cốt lõi + 2 bổ trợ + 4 loại bỏ), chỉ tạo một contract tích hợp mới riêng cho MOAT kèm caller/contract, migration, rollback, observability và fail-closed tests. Các cột legacy `gil_flag` trong Core DB chỉ giữ thụ động cho tương thích schema `NOT NULL` cho tới khi có migration gỡ bỏ riêng.

- Bộ lọc ngày Agent và dấu dữ liệu trên lịch dùng `analysis_date` của nhật ký luận điểm (replay), hoặc ngày Việt Nam của nhật ký khớp chính xác `thesis_id`. `generated_at` lấy thời gian nhật ký để tránh timestamp lệch của state legacy; chỉ fallback timestamp state khi không có nhật ký. Frontend dùng cùng trường ngày API, không lọc lại bằng `created_at` lệch. Không sửa dữ liệu legacy hay suy đoán ngày replay thiếu metadata.

- ML snapshots are immutable per account/session/ticker. All ranked signals include feature_date, decision (BUY/SKIP), reason, model SHA-256 and linked order_id. Signals and pending orders commit together under a users row lock; sizing includes fees and reserves existing pending orders. API joins order status separately; a prediction is never a filled position.
- ML live decisions use completed daily bars strictly before the decision session, with recent socket quotes for limit sizing and previous close fallback for limits only. Shadow execution still requires fresh executable bid/ask depth. Historical mode requires a model cutoff strictly before the session, injected historical prices and PENDING_REPLAY orders excluded from the live worker.
- `experiments/replay_ml_fund.py` replays 1–52 historical sessions on the explicitly named account without resetting data. It uses timestamped DNSE depth, day-only limits, StopLossEngine and the repository settlement/cash checks; fail-closed on missing depth. Model cutoff and artifact hash are recorded in the report and prediction snapshots. The preflight rejects an account with existing orders/predictions/positions.
- `scripts/train_hybrid_stacking.py --cutoff YYYY-MM-DD --export-path PATH` truncates universe selection and price data before generating forward labels. Incomplete forward labels are removed; the artifact records its observable-data cutoff. Financial-ratio features use known published dates, not quarter-end dates. Weekly/monthly training scripts exist, but a scheduler invoking them was not found in this audit; do not assume automatic retraining is operational.

- The position monitoring daemon also calls ML-only protection. It reads only the dedicated ML holdings, requires fresh executable bids, applies the shared StopLossEngine, and queues SELL orders without directly filling them. The Shadow worker executes the recorded side (BUY/SELL) through the same portfolio transaction and settlement guards.
