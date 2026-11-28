# AIInvest: Tài Liệu Kiến Trúc Hệ Thống Công Nghệ Thông Tin
> **Phiên bản**: 2.1.0 (Enterprise IT Architecture Specification — Chặng 2 Phase 1)  
> **Áp dụng cho**: Monorepo AIInvest (Frontend, Backend, AI Engine, SAG v2 Stock Research Engine, Storage Tier)  
> **Tài liệu toán học & thuật toán**: [ALGORITHMS_AND_FINANCIAL_MODELS.md](file:///d:/AIInvest/docs/architecture/ALGORITHMS_AND_FINANCIAL_MODELS.md)  
> **Sơ đồ đính kèm**: [docs/diagrams/paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html)

---

## 1. Tổng Quan Kiến Trúc Doanh Nghiệp (Enterprise Architecture Overview)

AIInvest là hệ thống phân tích tài chính định lượng và nghiên cứu cổ phiếu (Stock Research / MOAT) tự động hóa chuyên sâu cho thị trường chứng khoán Việt Nam (HOSE/HNX). 

Hệ thống được thiết kế theo mô hình **Event-Driven Microservices kết hợp Clean Architecture**, phân tách triệt để giữa tầng giao tiếp người dùng, tầng nghiệp vụ điều phối, tầng mô hình định lượng (Quant Intelligence) và tầng nghiên cứu chứng cứ báo cáo (SAG v2 Stock Research Engine). Theo **Rule 06 (Narrowed)**, Pha 1 đã mở khóa **Chat AI Phân Tích Doanh Nghiệp (`/research-chat`)** kết nối qua Express 4 SSE Gateway tới SAG v2 (`research` profile), trong khi **GIL** vẫn bị loại bỏ vĩnh viễn và **tích hợp 12 Decision Agents (`ai-engine`) tiếp tục đóng (`SAG_CLOSED`)** tới khi vượt Cổng Kiểm Nghiệm G1–G5.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                      TẦNG 1: TRÌNH DIỄN (PRESENTATION TIER)                    │
│   Next.js 16 App Router · TradingView · WebSockets · /research-chat (Phase 1)  │
└───────────────────────────────────────┬────────────────────────────────────────┘
                                        │ HTTPS (Port 443) / WSS / SSE
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                   TẦNG 2: API GATEWAY & QUẢN TRỊ NGHIỆP VỤ                     │
│  Express 4 · Prisma ORM · JWT Auth · LIVE OMS (:3001)                          │
│  ├─ aiEngineService (timeout 800ms) ──▶ ai-engine                              │
│  └─ sagResearchChatService (SSE 60s, BFF Headers) ──▶ sag-api (Phase 1)        │
└───────────────────┬───────────────────────────────────────┬────────────────────┘
                    │ REST (Port 8000)                      │ SSE / REST (:8000, profile: research)
                    │ [Rule 06: ai-engine <-> SAG = NONE]   │
                    ▼                                       ▼
┌───────────────────────────────────────┐   ┌────────────────────────────────────┐
│   TẦNG 3: QUANT ORG CORE ENGINE       │   │ TẦNG 4: SAG v2 RESEARCH ENGINE     │
│ 12 Quant Agents · Clean Architecture  │   │ Knowledge Routing RAG + 3-Layer KG │
│ Dual-Book Risk (HOSE T+2.5) (:8000)   │   │ MinerU (vi) + Table Engine + Calc  │
│ (SAG_CLOSED — 0 calls to sag-api)     │   │ DeepInfra (DeepSeek-V4 + Qwen3)    │
└───────────────────┬───────────────────┘   └─────────────────┬──────────────────┘
                    │                                         │
                    │ SQL Pool (Port 5432)                    │ SAG PG + Qdrant + Core DB (RO)
                    ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                     TẦNG 5: LƯU TRỮ DOANH NGHIỆP (PERSISTENCE)                 │
│  PostgreSQL 16 (Core + sag_chat_eval_feedback) · Redis 7 · Cloudflare R2       │
│  PostgreSQL 16 SAG (0013 tables & KG) · Qdrant v1.14.1 (1024d + VN-Dot BM25)   │
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
  - **Collapsible Workspace Rail (`Shell.tsx`)**: Mặc định thu gọn (`64px`) chỉ hiển thị icon `lucide-react` riêng biệt cho các phân hệ (`/agent`, `/portfolio`, `/discovery`, `/trade`, `/sectors`, `/research`, và `/research-chat` — `"Chat Phân tích AI"` cho Pha 1) kèm floating tooltip khi hover/focus; khi mở rộng (`224px`) chỉ hiển thị nhãn văn bản. Trang mặc định sau đăng nhập/fallback chuyển sang `/agent` (`AI War Room`); các route `/dashboard`, `/markets`, `/watchlist`, `/backtest`, `/settings`, `/help`, `/ml-fund` đã được gỡ bỏ và `/community` được ẩn khỏi điều hướng UI.
  - **Enterprise Analysis AI Chat Workspace (`/research-chat`, Pha 1 Verification UI)**: Xây dựng tại `front-end/src/app/(workspace)/research-chat/page.tsx` + `src/components/research-chat/*` + `src/lib/api/researchChat.ts` (không đụng `/agent` hay `/research`):
    - *Thanh điều khiển trên cùng (`TopControlBar.tsx`)*: Chọn mã cổ phiếu chính hoặc so sánh đa mã (tối đa `6` mã), công tắc **Đồ thị 3 Tầng (Vĩ mô & True-Peer)**, bộ chọn **Point-in-Time `as_of_date`** (kèm nút kiểm thử `2025-06-30`), quản lý phiên chat và pill tiến độ Exit Gate G1–G5.
    - *Cột trái (`ChatColumn.tsx`)*: 6 gợi ý câu hỏi MOAT mẫu, tiến trình SSE 7 pha theo thời gian thực (`routing -> retrieval -> graph_hop -> table_hydration -> calculator -> synthesis -> verification`), bộ phân tích Markdown AST an toàn XSS (`markdownParser.ts` + `MarkdownCitationView.tsx`, không dùng `dangerouslySetInnerHTML`) kèm badge tương tác (`[S07·P168 ✓]`, `[Bảng #1]`, `[Calc #1]`, `[KG: CONFIRMED_BOTH]`), hộp cảnh báo mâu thuẫn PR-vs-Kiểm toán (`DiscrepancyAlertsBox`) và hộp điểm mù khai báo (`DeclaredBlindspotsBox`).
    - *Cột phải (`EvidenceInspector.tsx`, 5 tab)*: (1) Trích dẫn gốc (`quote_hash`, số trang PDF, `sub_doc_slot`, ngày PIT); (2) Bảng 2D đã hydrate + `CalculatorTrace` từ Python; (3) Đồ thị 3 Tầng (`MACRO`, `VALUE_CHAIN`, `TRUE_PEER` kèm $\beta$, $p$-value, $q_{\text{FDR}}$, `tercile_stable`, `verification_tier`); (4) Routing & Token Trace (Leiden nodes, HippoRAG hops, tokens, chi phí, latency); (5) Đánh giá kiểm nghiệm (`PASS / NEEDS_FIX / FAIL` ghi vào `sag_chat_eval_feedback` + biểu đồ G1–G5).
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
  - **Dedicated 60s SSE Streaming Gateway cho Research Chat (`back-end/src/services/sagResearchChat.service.ts` + `src/modules/ai/ai.routes.ts`)**:
    - Tách biệt hoàn toàn khỏi `aiEngineService` (`timeout: 800ms` dành cho dashboard quant); sử dụng `fetch` + `AbortController` với **`SAG_RESEARCH_TIMEOUT_MS = 60_000`** để proxy SSE từ `sag-api` (`SAG_API_URL`) với `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `res.flushHeaders()`.
    - Truyền bộ header xác thực Continuum BFF: `X-SAG-BFF-Service-Token`, `X-Continuum-User-Id`, `X-Continuum-Organization-Id`, `X-Continuum-Project-Id` (`ticker` hoặc `"all"`), `X-Continuum-Security-Partition` (`ticker` hoặc `"all"`), và `X-Continuum-Allowed-Project-Ids` (danh sách tối đa `6` mã khi so sánh đa mã). Fail-closed (`503`) nếu thiếu `SAG_BFF_SERVICE_TOKEN`.
    - Các route được bảo vệ bởi `authMiddleware` dưới `/api/v1/ai/research-chat/*`: `POST /sessions`, `GET /sessions`, `GET /sessions/:id`, `POST /stream` (SSE), `POST /feedback`, `GET /eval-summary` (tính KPI Exit Gate G1–G5 & kiểm toán rò rỉ PIT), `GET /kg/:ticker`, `GET /tables/:tableId`. Middleware `aiLimiter` (`rateLimiter.ts`) miễn giới hạn cho các `GET /research-chat/*` chỉ đọc để không bóp nghẹt UI Inspector.
    - Lưu lịch sử hội thoại trên bảng hiện hữu `chat_sessions` & `chat_messages` (`type: 'research_chat_v2'`, `metadata` JSONB) và kết quả chấm điểm trên `sag_chat_eval_feedback` (không persist assistant message khi stream lỗi/bị cắt).
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

### Tầng 4: SAG v2 Stock Research & Enterprise Chat Engine (`SAG/`)
- **Công nghệ lõi**: FastAPI + `zleap-sag==0.12.0` + PostgreSQL 16 (`sag-postgres`, migration `0001..0013`) + Qdrant v1.14.1 (`sag-qdrant`) + Read-only Core DB Bridge (`SAG_CORE_DATABASE_URL`) + PyMuPDF / `pdfplumber` / MinerU Official (`language="vi"`).
- **Nhà cung cấp AI (DeepInfra)**: `https://api.deepinfra.com/v1/openai` cho cả LLM (`deepseek-ai/DeepSeek-V4-Flash-0731`, ghi nhận pricing/policy trong `litellm_policy.py` & `token_usage.py`) và Embedding (`Qwen/Qwen3-Embedding-0.6B`, `1024d`).
- **Phạm vi duy nhất (Rule 06 Narrowed)**: Nghiên cứu cổ phiếu chuyên sâu (**Stock Research / MOAT**) và **Chat AI Phân Tích Doanh Nghiệp (Pha 1)** thông qua `/api/v1/research-chat/*`. Toàn bộ phân hệ **GIL (Graph Intelligence Layer)** và `/api/v2/financial` cũ vẫn bị loại bỏ `100%`; `ai-engine` không gọi SAG.
- **Kiến trúc Knowledge Routing RAG v2 (`0` LLM calls lúc Ingest chuẩn; tùy chọn 1 call 3-Way BCTN Triage)**:
  - **HOSE Input Layer (`sag_api/research/input_{selector,inspector,builder}.py`, `ocr_adapter.py`, `batch_ingest.py`)**: Tuyển chọn 3 Cốt lõi + 2 Bổ trợ + 4 Loại bỏ, khử trùng BCTN/BCTC, giải nén `.zip` an toàn Zip-Slip, khóa PIT `effective_published_at`, fallback 404, chặn `NOTICE_ONLY` (`< 15p`) & `BAD_FONT` (`vn_readability_score < 0.72`), định tuyến 4 nhánh A/B/C/D, nối trực tiếp **`MinerUOcrAdapter`** (`OfficialMinerUClient`, `language="vi"`, trần OCR `120` trang cho BCTC scan để không cắt cụt Thuyết minh), **4 Lớp Tài Liệu Tự Nhiên + SAG Native Tree** (`BCTN_CORE`, `BCTN_PR`, `BCTC_AUDITED`, `AGM_TARGETS`/`QUARTER_NOTES`): tự gỡ header/footer lặp lại, tự hiệu chuẩn `Printed-to-PDF Offset` & Spread A3, tự định vị BCTC nhúng trong BCTN (`detect_embedded_bctc_start_page`), lọc 3 trạng thái `KEEP / PR / DROP` trên BCTN có khiên bảo vệ bảng tài chính, giữ nguyên **0%-drop (100% phủ trang)** toàn bộ BCTC & Thuyết minh B09/TT49/TT334 kèm chuẩn hóa Markdown `#/##` cho SAG tự xây cây tri thức gốc + `DocumentCoverageAudit`, và Batch Ingest động theo tier Core DB (`pilot_8 | a5_36 | a4_81 | a3_buffer_139 | custom`).
  - **Dual-Representation Financial Table Engine (`sag_api/research/table_engine.py`, nối trực tiếp vào `batch_ingest.py`)**: Bóc `pdfplumber` lattice/stream (tách virtual page A3) + giải `rowspan/colspan` HTML MinerU + khâu bảng vắt trang `(Tiếp theo)` + kế thừa đơn vị/tiền tệ + chuẩn hóa số Việt + kiểm định số học phẳng & đối chiếu phân cấp CĐKT (`<= 0.5%`, `TABLE_UNVERIFIED` fail-closed). Lưu ma trận 2D vào `sag_extracted_tables` và thẻ tìm kiếm `Row-Linearized Search Cards` vào Qdrant shared multi-tenant collection **`aiinvest_search_units`** (`project_id` gắn `is_tenant: true`, `on_disk: true`, `int8` scalar quantization, `1024d` + VN-Dot BM25 + `PAYLOAD_INDEX_FIELDS` PIT).
  - **Knowledge Foundation & Zero-Hardcode 3-Layer KG (`P1..P5`, `A1..A7`, `kg_*.py`)**: Bi-temporal `valid_from = source_published_at`, VN Accounting Bridge (`TM-{num}`, `B0x-DN/HN`, `TT334`), DenseGraph hiệu chỉnh + Leiden Tree (`target=6, max=20, res=0.08`, Blue-Green `SLOT_A/B`), kết hợp 3 động cơ đồ thị không hardcode (`kg_entity_linker.py`, `kg_macro_coupling.py`, `kg_true_peer.py`) lưu tại `kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`.
  - **Research Chat API & Verification (`api/v1/research_chat.py`, `chat_orchestrator.py`, `calculator.py`, `discrepancy_detector.py`, `quote_verifier.py`)**: Xác thực `require_research_bff_principal` (Continuum BFF headers); phục vụ `POST /api/v1/research-chat/stream` (SSE 7 pha: `routing`, `retrieval`, `graph_hop`, `table_hydration`, `calculator`, `synthesis`, `verification`, `done`), `GET /kg/{ticker}`, `GET /tables/{table_id}`, `POST /ingest-pilot`, `POST /ingest-batch`. Chạy đối chất PR-vs-Kiểm toán `0 LLM`, Python Calculator sandbox (`CalculatorTrace`, cấm mental math), `A7` half-life (`365/180/90d`) + 3-layer abstention -> `declared_blindspots[]`, và kiểm chứng `quote_hash` SHA-256 hậu tổng hợp.
- **Port**: `8000` bên trong `research` Compose profile (`sag-api`, standalone host bind `127.0.0.1:18000`), không mở port công khai trên Nginx DMZ.

### Tầng 5: Enterprise Persistence & Storage Tier
- **PostgreSQL 16 (Core — `aiinvest`)**: Lưu tài khoản, tiền mặt, vị thế, lệnh, dữ liệu thị trường, kết quả định lượng 12 Agents, lịch sử chat (`chat_sessions`, `chat_messages` với `metadata` JSONB) và bảng chấm điểm kiểm nghiệm Pha 1 (`sag_chat_eval_feedback` qua Prisma migration `20261120140000_sag_chat_eval_feedback`). `sag-api` chỉ đọc các bảng tài chính/vĩ mô qua `SAG_CORE_DATABASE_URL`.
- **Portfolio account identity**: `portfolio_account.account_id` stores up to 64 characters so it can use the same UUID identifier as `users.id`.
- **Alpha research storage**: `alpha_signals` is retired because the current application has no readers or writers.
- **Trading decisions**: Orders are produced through the Agent workflow and risk approval; the detached daily BUY/HOLD/SELL feed and its UI/API are retired.
- **PostgreSQL 16 + Qdrant (SAG — profile `research`)**: CSDL tách biệt hoàn toàn (`sag-postgres` DB **`aiinvest_sag`** trên `127.0.0.1:55432` + `sag-qdrant` `mem_limit: 1g` trên `127.0.0.1:16333`, volumes `aiinvest_sag_*`) lưu `CanonicalBlock`, `DocumentVersion`, `KnowledgeUnit`, `Knowledge Routing Tree`, cộng thêm 4 bảng Pha 1 từ migration `0013_stock_research_tables_and_kg.sql` (`sag_extracted_tables`, `kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`) và shared multi-tenant collection `aiinvest_search_units` (`project_id` `is_tenant: true`, `on_disk: true`, `int8` quantization) kèm các chỉ mục payload PIT trên Qdrant (`valid_from_ts`, `fiscal_year`, `fiscal_quarter`, `doc_type`, `sub_doc_slot`, `sector_group`, `table_id`).
- **Cloudflare R2 / Object Storage**: Lưu trữ các file PDF gốc BCTC/BCTN, tài liệu công bố thông tin và báo cáo xuất ra.
- **Redis 7**: Cache dữ liệu bảng giá, ticker OHLCV và kênh truyền Pub/Sub giữa các microservices.
- **Ports**: PostgreSQL Core (`5432`), SAG PostgreSQL `aiinvest_sag` (`127.0.0.1:55432` research-only), Qdrant (`127.0.0.1:16333` research-only), SAG API standalone (`127.0.0.1:18000`), Redis (`6379`).

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
4. **Service Authentication**: Route quản trị của Backend dùng `INTERNAL_SERVICE_TOKEN`; route quản trị AI Engine dùng `AI_ENGINE_ADMIN_TOKEN`; Backend truyền token này qua `X-Admin-Token`. Giữa Express Gateway và SAG (`research-chat`), `sagResearchChat.service.ts` xác thực bằng `X-SAG-BFF-Service-Token` (`SAG_BFF_SERVICE_TOKEN`) kèm các header Continuum (`X-Continuum-User-Id`, `X-Continuum-Organization-Id`, `X-Continuum-Project-Id`, `X-Continuum-Security-Partition`, `X-Continuum-Allowed-Project-Ids`). SAG production từ chối khởi động khi `SAG_SECRET_KEY` hoặc token rỗng/mặc định; `ai-engine` không được cấp `SAG_API_URL` hay `SAG_BFF_SERVICE_TOKEN`.
5. **Atomic Portfolio Ledger**: Tiền, vị thế và lệnh được cập nhật trong transaction có khóa tài khoản. DB áp đặt một vị thế cho mỗi `(user_id, symbol)`, reaction chỉ được trỏ tới đúng một post hoặc comment, và `paper_trades` luôn có `account_id`.
6. **Reverse Proxy DMZ Isolation**: Toàn bộ client truy cập qua Nginx Reverse Proxy (cổng 80/443). Nginx thực hiện định tuyến `/` -> frontend, `/api/` -> backend (bao gồm SSE `/api/v1/ai/research-chat/stream` khi profile `research` chạy), `/socket.io/` -> WebSocket, bật Gzip nén, và giấu toàn bộ port microservices và database phía sau mạng nội bộ `aiinvest_default`.
7. **Automated Database Lifecycle & Healthcheck Gating**: Job `migrate` chạy `prisma migrate deploy` trước AI Engine và Backend. Backend chỉ phục vụ request sau khi AI Engine healthy (và không `depends_on: sag-api` để core stack khởi động độc lập). SAG (`sag-postgres`, `sag-qdrant`, `sag-api` với SQL migrations `0001..0013` và `init_db()`) chỉ chạy trong profile `research`. Docker Compose dùng `condition: service_healthy` và log rotation (`max-size: 50m`, `max-file: 5`).

---

## 4. Đặc Tả Pipeline Dữ Liệu BCTC & Ra Quyết Định

Quy trình song song nhưng phân tách ranh giới nghiêm ngặt giữa **Nghiên cứu Stock Research / Chat Pha 1 (SAG v2)** và **Thực thi 12 Agents (`ai-engine`)**:

1. **HOSE Input Selection & Hybrid Extraction (`SAG` profile `research`)**: Tài liệu công bố nghiên cứu cổ phiếu (3 Cốt lõi + 2 Bổ trợ + 4 Loại bỏ) đi qua `InputInspector` (`NOTICE_ONLY < 15p`, `BAD_FONT`, 4 nhánh A/B/C/D), tách gọn thành **4 Lớp Tài Liệu Tự Nhiên** (`BCTN_CORE`, `BCTN_PR`, `BCTC_AUDITED` 0%-drop chuẩn hóa `#/##` cho SAG Native Tree, `AGM_TARGETS`/`QUARTER_NOTES`) gắn neo `<!-- PAGE N -->` và `valid_from = effective_published_at`.
2. **Table Engine, Knowledge Routing & 3-Layer KG (`0` LLM lúc Ingest)**: Dual-Representation Table Engine lưu ma trận 2D vào `sag_extracted_tables` + `Row-Linearized Search Cards` vào Qdrant (`1024d` + VN-Dot BM25); `KnowledgeWorker` dựng `DenseGraph` + cây Leiden (`SLOT_A/B`); 3 động cơ đồ thị không hardcode dựng `kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`.
3. **Phase 1 Enterprise Analysis AI Chat (SSE 60s)**: Người dùng tại `/research-chat` gửi truy vấn qua Express `/api/v1/ai/research-chat/stream` -> SAG 7-stage pipeline (`routing -> retrieval -> graph_hop -> table_hydration -> calculator -> synthesis -> verification -> done`) -> trả về câu trả lời có `quote_hash` verified, `CalculatorTrace`, `DiscrepancyAlert`, `declared_blindspots[]`, và lưu đánh giá Exit Gate G1–G5 vào `sag_chat_eval_feedback`.
4. **Rule 06 Runtime Separation (Phase 2 Chưa Mở)**: `DailyInvestmentPipeline` và 12 Decision Agents (`ai-engine`) hoàn toàn không gọi SAG (`SAG_CLOSED`); các module GIL cũ đã bị xóa vĩnh viễn. Chỉ khi vượt đủ 5 tiêu chí G1–G5 trên `>= 40` phiên kiểm thử, Pha 2 mới nối Read-Only snapshot `stock_competitive_dossiers` cho Agent 04/05 và `/research`.
5. **Independent Multi-Agent Consensus (`ai-engine`)**: Orchestrator sản xuất vận hành độc lập trên Core financial, market, audit, Beneish, liquidity và thesis evidence mà không phụ thuộc payload từ SAG.
6. **Dual-Book Portfolio Allocation**: Nếu vượt qua vòng thẩm định pháp y, Risk Parity Agent phân bổ tỷ trọng dựa trên quy tắc HOSE (T+2.5, biên độ trần/sàn ±7%).
7. **Order Dispatch**: Khi chưa có broker gateway, mọi cấu hình `LIVE` bị từ chối. Shadow BUY lưu thành `PENDING_SHADOW` trong bảng `orders`, dùng giá duyệt làm giới hạn trong ngày. Một worker tuần tự quét mọi mã chờ trong phiên liên tục: nghỉ 1 giây sau lượt quét khi có lệnh chờ, 5 giây khi rảnh. Khi đủ thanh khoản ở mức giá cho phép, chính order đó được cập nhật cùng cash, position và execution trong một transaction PostgreSQL. Lệnh chưa khớp hết hạn cuối phiên. Không có lệnh nào gửi lên DNSE.

---

## 5. Thư Viện Sơ Đồ Trực Quan Tương Tác (Interactive Diagrams Hub)

Toàn bộ các sơ đồ tương tác tự chứa (standalone SVG/HTML) được lưu trữ tại thư mục [docs/diagrams/](file:///d:/AIInvest/docs/diagrams/):

### Sơ Đồ Thuật Toán & Biến Đổi Biểu Diễn Dữ Liệu Khoa Học (NeurIPS/SIGMOD Standard)
- **[paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html)**: Sơ đồ phương pháp luận báo cáo khoa học Paper-Grade: Bóc tách hình thái dữ liệu 2 pha (Offline Ingestion AST, băm `quote_hash` SHA-256, chiếu siêu đồ thị $M:N$, không gian vector 1024 chiều vs Online Parallel Fork-Join Retrieval, bộ tích lũy Queue Accumulator $32 \to 24 \to 8$, điều khiển tương tác Step Motion có phím tắt, Dynamic Flow Opacity và bảng khảo sát chi tiết Inspector Drawer khi nhấp vào từng node).

Chi tiết hướng dẫn mở, tương tác và xem sơ đồ được mô tả tại [docs/diagrams/README.md](file:///d:/AIInvest/docs/diagrams/README.md).
### SAG boundary hiện hành (2026-11-20 — Chặng 2 Phase 1, Rule 06 Narrowed)

SAG đã loại bỏ vĩnh viễn toàn bộ phần **GIL (Graph Intelligence Layer)** và pipeline BCTC v2 cũ (`sag_connector.py`, `sag_evidence_agent.py`, `forensic_assessment.py`, `bctc_to_sag_pipeline.py`, `document_selector.py`, `gil_service.py`, `api/v2/financial.py`, `gil-decision-tree-gating.html`) và vận hành kiến trúc **SAG v2 Knowledge Routing RAG + Dual-Rep Table Engine + 3-Layer KG** (`zleap-sag==0.12.0`, PostgreSQL 16 + Qdrant, 0 LLM calls lúc ingest).

Theo bản thu hẹp của `.agents/rules/06-sag-analysis-hold.md`:
- **Đã mở khóa (Phase 1):** `front-end` (`/research-chat`) <-> `back-end` (`sagResearchChat.service.ts`, `/api/v1/ai/research-chat/*`, `60s` SSE) <-> `sag-api` (`/api/v1/research-chat/*`, profile `research`, đọc Core DB qua `SAG_CORE_DATABASE_URL`), kèm `sag_chat_eval_feedback` để đo Exit Gate G1–G5.
- **Vẫn đóng (`SAG_CLOSED`):** `ai-engine` (`DailyInvestmentPipeline`, Agent 04, Agent 05, và toàn bộ 12 Decision Agents) tuyệt đối không có đường kết nối hay phụ thuộc tới `sag-api` (`ai-engine/tests/test_sag_runtime_separation.py` 6/6 pass). Pha 2 (nối Read-Only `stock_competitive_dossiers` cho Agent 04/05 và `/research`) chỉ mở sau khi chủ dự án duyệt vượt Exit Gate G1–G5. Các cột legacy `gil_flag` trong Core DB chỉ giữ thụ động cho tương thích schema `NOT NULL`.

- Bộ lọc ngày Agent và dấu dữ liệu trên lịch dùng `analysis_date` của nhật ký luận điểm (replay), hoặc ngày Việt Nam của nhật ký khớp chính xác `thesis_id`. `generated_at` lấy thời gian nhật ký để tránh timestamp lệch của state legacy; chỉ fallback timestamp state khi không có nhật ký. Frontend dùng cùng trường ngày API, không lọc lại bằng `created_at` lệch. Không sửa dữ liệu legacy hay suy đoán ngày replay thiếu metadata.

- ML snapshots are immutable per account/session/ticker. All ranked signals include feature_date, decision (BUY/SKIP), reason, model SHA-256 and linked order_id. Signals and pending orders commit together under a users row lock; sizing includes fees and reserves existing pending orders. API joins order status separately; a prediction is never a filled position.
- ML live decisions use completed daily bars strictly before the decision session, with recent socket quotes for limit sizing and previous close fallback for limits only. Shadow execution still requires fresh executable bid/ask depth. Historical mode requires a model cutoff strictly before the session, injected historical prices and PENDING_REPLAY orders excluded from the live worker.
- `experiments/replay_ml_fund.py` replays 1–52 historical sessions on the explicitly named account without resetting data. It uses timestamped DNSE depth, day-only limits, StopLossEngine and the repository settlement/cash checks; fail-closed on missing depth. Model cutoff and artifact hash are recorded in the report and prediction snapshots. The preflight rejects an account with existing orders/predictions/positions.
- `scripts/train_hybrid_stacking.py --cutoff YYYY-MM-DD --export-path PATH` truncates universe selection and price data before generating forward labels. Incomplete forward labels are removed; the artifact records its observable-data cutoff. Financial-ratio features use known published dates, not quarter-end dates. Weekly/monthly training scripts exist, but a scheduler invoking them was not found in this audit; do not assume automatic retraining is operational.

- The position monitoring daemon also calls ML-only protection. It reads only the dedicated ML holdings, requires fresh executable bids, applies the shared StopLossEngine, and queues SELL orders without directly filling them. The Shadow worker executes the recorded side (BUY/SELL) through the same portfolio transaction and settlement guards.
