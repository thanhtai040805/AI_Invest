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

#### Research: luận điểm mới nhất và lịch sử

- `GET /api/v1/workspace/research` trả luận điểm mới nhất theo từng ticker từ `investment_theses`, kèm phán quyết Agent 05 từ `counter_thesis_verdicts`. Thời điểm `generated_at` ưu tiên nhật ký tạo luận điểm để tránh lỗi múi giờ của `created_at` cũ, fallback về `created_at` nếu thiếu nhật ký. Trạng thái duyệt ở đây không phải xác nhận của CIO.
- `GET /api/v1/workspace/research/thesis-history?page=1&ticker=HPG&date=2026-09-27` đọc `log_investment_thesis`, phân trang 20 bản lưu, lọc ticker và `analysis_date` của phiên phân tích/replay. `created_at` giữ thời điểm thực sự ghi log (UTC, hiển thị Việt Nam). Bản lưu cũ thiếu `analysis_date` được nhóm theo ngày ghi log và gắn nhãn thiếu ngày phân tích; không suy đoán ngày replay. Trạng thái lịch sử là trạng thái tại lúc lập; không gắn phán quyết mới nhất vào bản lưu cũ.
- Ba bộ chọn ngày Research, Agent và ML Fund dùng chung lịch `dd/mm/yyyy`, tuần bắt đầu thứ Hai, múi giờ Việt Nam. `dates` của API là ngày có bản ghi (Research áp dụng ticker nhưng không áp dụng ngày đang chọn; ưu tiên ngày phân tích), không phải lịch nghỉ sàn hoặc lịch thanh toán. Riêng Research chỉ cho chọn ngày có bản lưu phù hợp ticker; bấm tab Lịch sử xóa bộ lọc và tải lại toàn bộ bản lưu, hiển thị các ngày gần nhất để truy cập trực tiếp.
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
- Tỷ trọng ngành trên Dashboard và Sectors dùng cùng phép tính: vốn hóa khi có đủ cho mọi mã trong các ngành, nếu thiếu dùng số mã nhất quán. Heatmap đếm toàn bộ mã trong universe kể cả mã thiếu giá/tham chiếu; biến động chỉ tính từ các mã có giá hợp lệ và trả null khi chưa có. `marketCapCount`/`market_cap_count` mô tả độ phủ vốn hóa; UI ghi rõ cơ sở tỷ trọng và hiển thị tất cả ngành.
- `GET /api/v1/workspace/ml-fund?date=YYYY-MM-DD` trả dự báo của tài khoản ML riêng theo SQL DATE; bỏ `date` sẽ chọn ngày dự báo mới nhất. `dates` và `selectedDate` phục vụ lịch. `latestClose` và `account.total_nav` lấy NAV chốt mới nhất từ `portfolio_nav_history`, độc lập bộ lọc. Tiền mặt hiện tại từ `users`, vị thế từ `positions` và chứng từ khớp lệnh được đọc cùng transaction Repeatable Read. Vị thế được ước tính bằng giá đóng cửa gần nhất `market_data_daily.close_unadj` (nghìn VND → VND); tiền mặt hiện tại + các giá trị này được đặt tên `account.estimated_nav`, dùng cho tỷ trọng danh mục hiện tại, không ghi đè NAV lịch sử. Giá vốn/lãi chưa chốt dùng sổ bình quân gồm phí mua; thiếu thị giá hoặc lệch chứng từ giữ null. `trading.realizedPnl` lọc theo ngày bán khớp Việt Nam trong `from`/`to`, sau khi tính giá vốn từ toàn bộ lịch sử; `trading.sales` cung cấp lượng bán, giá vốn phân bổ và tiền bán sau phí/thuế. Độ chính xác tổng hợp từ `standalone_ml_predictions` đã đối soát theo ngày dự báo trong khoảng `from`/`to`, độc lập bộ chọn ngày dự báo. Lịch sử đối soát tải tối đa 200 bản ghi, thống kê không giới hạn. Giá nền ML và ngày feature được hiển thị riêng: kết quả ba phiên là biến động giá theo dữ liệu ML, không phải lợi nhuận giao dịch thực hiện. Màn hình không đổi chính sách thoát vị thế thành T+3: code monitoring xét điều kiện bảo vệ giá và time stop 5 ngày lịch, lệnh bán còn phải thực sự khớp.
- ML trả thêm `navDates`, `predictionSummary` theo kỳ, chuỗi `performance.equityCurve` và `performance.missingDates`. Ngày thiếu NAV được đối chiếu với các phiên có giá đóng cửa trong Core DB; không suy ra NAV từ dự báo. `dailyPnl` cần snapshot phiên giao dịch liền trước. Khi lọc `from`, `baselineDate` là phiên giao dịch trước ngày bắt đầu; không tự lùi thêm khi snapshot đó thiếu. Mốc cuối cũng phải có snapshot đúng ngày. Chênh lệch NAV kỳ có thể tính khi đủ hai mốc dù thiếu điểm giữa, với giả định không nạp/rút vốn ngoài giao dịch. UI ghi ngày hai mốc, mở lịch sử NAV sẵn, ngắt đường đồ thị qua ngày thiếu và chia bảng đối soát thành 20 dòng/trang; dự báo chưa đối soát có trạng thái riêng. Giới hạn tải 200 kết quả được ghi rõ, người dùng có thể thu hẹp kỳ để truy cập ngày cũ.
- Biểu đồ NAV trên ML Fund và Danh mục dùng chung `NavLineChart` với Recharts. Trục ngày dùng UTC calendar để giữ đúng SQL DATE, trục NAV ghi rõ đơn vị và tooltip đọc giá trị đầy đủ của từng snapshot. Đường ngắt tại các ngày trong `missingDates` của ML; không suy ra phiên thiếu từ cuối tuần/ngày nghỉ và không bổ sung điểm NAV hiện tại vào lịch sử. Một snapshot hiển thị một điểm, chưa khẳng định xu hướng.
- Agent 04 tạo revision riêng khi không truyền `seq_num`, tránh ghi đè giữa các lần chạy. Sequence được truyền tường minh vẫn giữ định dạng cũ. Thời gian lưu luận điểm/phán quyết dùng UTC có múi giờ; khi cập nhật cùng ID, thời điểm tạo luận điểm được giữ nguyên.
- Phán quyết cập nhật đồng thời các thành phần CTS, điểm cuối, cổng kiểm tra, rủi ro và giới hạn thực thi. Giao diện cảnh báo nếu các thành phần CTS cũ không khớp điểm cuối; dữ liệu cũ không được tự sửa hoặc suy diễn lại.

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
- **Alpha research storage**: `alpha_signals` is retired because the current application has no readers or writers.
- **Trading decisions**: Orders are produced through the Agent workflow and risk approval; the detached daily BUY/HOLD/SELL feed and its UI/API are retired.
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

- Bộ lọc ngày Agent và dấu dữ liệu trên lịch dùng `analysis_date` của nhật ký luận điểm (replay), hoặc ngày Việt Nam của nhật ký khớp chính xác `thesis_id`. `generated_at` lấy thời gian nhật ký để tránh timestamp lệch của state legacy; chỉ fallback timestamp state khi không có nhật ký. Frontend dùng cùng trường ngày API, không lọc lại bằng `created_at` lệch. Không sửa dữ liệu legacy hay suy đoán ngày replay thiếu metadata.

- ML snapshots are immutable per account/session/ticker. All ranked signals include feature_date, decision (BUY/SKIP), reason, model SHA-256 and linked order_id. Signals and pending orders commit together under a users row lock; sizing includes fees and reserves existing pending orders. API joins order status separately; a prediction is never a filled position.
- ML live decisions use completed daily bars strictly before the decision session, with recent socket quotes for limit sizing and previous close fallback for limits only. Shadow execution still requires fresh executable bid/ask depth. Historical mode requires a model cutoff strictly before the session, injected historical prices and PENDING_REPLAY orders excluded from the live worker.
- `experiments/replay_ml_fund.py` replays 1–52 historical sessions on the explicitly named account without resetting data. It uses timestamped DNSE depth, day-only limits, StopLossEngine and the repository settlement/cash checks; fail-closed on missing depth. Model cutoff and artifact hash are recorded in the report and prediction snapshots. The preflight rejects an account with existing orders/predictions/positions.
- `scripts/train_hybrid_stacking.py --cutoff YYYY-MM-DD --export-path PATH` truncates universe selection and price data before generating forward labels. Incomplete forward labels are removed; the artifact records its observable-data cutoff. Financial-ratio features use known published dates, not quarter-end dates. Weekly/monthly training scripts exist, but a scheduler invoking them was not found in this audit; do not assume automatic retraining is operational.

- The position monitoring daemon also calls ML-only protection. It reads only the dedicated ML holdings, requires fresh executable bids, applies the shared StopLossEngine, and queues SELL orders without directly filling them. The Shadow worker executes the recorded side (BUY/SELL) through the same portfolio transaction and settlement guards.
