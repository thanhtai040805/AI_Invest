# IMPLEMENTATION_PLAN.md — IOS v5.1
## Kế Hoạch Triển Khai Kỹ Thuật

> **Mục đích:** AI Coding Agent đọc file này và bắt đầu code ngay theo từng Task, theo thứ tự. Không cần suy luận thêm về nghiệp vụ đầu tư. Mọi logic đã được định nghĩa trong AGENTS.md, DATA_SCHEMA.md, SYSTEM_SPEC.md.

> **Nguyên tắc coding:**
> 1. Không thay đổi logic đầu tư trong IOS
> 2. Không tạo rule mới không có trong IOS
> 3. Nếu thiếu thông tin kỹ thuật → tạo TODO comment, không tự suy luận
> 4. Sau mỗi Task: sinh code + test + technical doc

---

## PHASE 1 — NỀN TẢNG AN TOÀN
*Mục tiêu: Hệ thống có thể đứng vững và không bao giờ gây hại trước khi làm bất kỳ thứ gì khác.*

---

### EPIC 1.1 — DATA FOUNDATION

#### TASK-101: Data Quality Check Engine

| Field | Value |
|:---|:---|
| **Objective** | Xây dựng module kiểm tra chất lượng dữ liệu, chạy trước mỗi pipeline |
| **Inputs** | Raw OHLCV data, BCTC data, Universe list |
| **Outputs** | `DataQualityReport` (pass/fail per check, overall status) |
| **Dependencies** | None (task đầu tiên) |
| **Acceptance Criteria** | (1) Mỗi check có pass/fail rõ ràng. (2) Nếu bất kỳ critical check fail → overall = FAIL. (3) Log lý do fail chi tiết. (4) Không raise exception khi data thiếu — trả về FAIL với reason |
| **Definition of Done** | Code + unit test cho tất cả 8 checks trong DRY_RUN_CHECKLIST Phase 0 + technical doc mô tả cách thêm check mới |

**Checks phải implement:**
```
CHECK-01: OHLCV completeness — tất cả ticker có đủ data hôm nay không
CHECK-02: Price limit validation — |close/prev_close - 1| <= 7%
CHECK-03: Volume non-negative — không có âm
CHECK-04: Volume separation — volume_continuous + volume_atc + volume_ato ≈ total
CHECK-05: Financial freshness — announcement_date không quá 90 ngày
CHECK-06: Corporate action applied — tất cả CorporateAction có applied = true
CHECK-07: Announcement date exists — < 5% null
CHECK-08: Point-in-time integrity — không có data future-dated
```

---

#### TASK-102: Corporate Action Adjustment Engine

| Field | Value |
|:---|:---|
| **Objective** | Điều chỉnh backward price/EPS khi có corporate action mới |
| **Inputs** | `CorporateAction` records chưa applied, `MarketDataDaily` historical |
| **Outputs** | Updated `MarketDataDaily.close_adj` (và các trường adj khác), `CorporateAction.applied = true` |
| **Dependencies** | TASK-101 |
| **Acceptance Criteria** | (1) Giá lịch sử được adjust backward từ ex_date. (2) Sau khi apply, `applied = true` được set. (3) Không adjust nếu đã `applied = true`. (4) Test case: ticker có 2:1 split — giá ngày trước ex_date phải bị halve |
| **Definition of Done** | Code + test case với ít nhất 3 loại corporate action (SPLIT, DIVIDEND_STOCK, DIVIDEND_CASH) |

---

#### TASK-103: Data Ingestion — OHLCV

| Field | Value |
|:---|:---|
| **Objective** | Module lấy OHLCV từ nguồn primary, fallback sang secondary khi primary fail |
| **Inputs** | Danh sách tickers, date range |
| **Outputs** | `MarketDataDaily` records, bao gồm volume_continuous và volume_atc riêng biệt |
| **Dependencies** | TASK-101, TASK-102 |
| **Acceptance Criteria** | (1) Phân tách volume_continuous và volume_atc. (2) Ghi rõ data_source trong mỗi record. (3) Khi primary fail → tự động dùng secondary, log switching event. (4) Tính ADTV20_continuous đúng (loại ATC) |
| **Definition of Done** | Code + integration test với mock data source + doc về cách thêm data source mới |

---

#### TASK-104: Data Ingestion — Financial Statements

| Field | Value |
|:---|:---|
| **Objective** | Module lấy BCTC quarterly, enforce point-in-time via announcement_date |
| **Inputs** | Ticker list, date range |
| **Outputs** | `FinancialStatement` records với đầy đủ fields từ DATA_SCHEMA |
| **Dependencies** | TASK-101 |
| **Acceptance Criteria** | (1) `announcement_date` phải luôn có và <= ngày signal. (2) `has_data_flag = false` nếu thiếu bất kỳ field quan trọng nào. (3) Derived metrics (ROIC, accrual_ratio, FCF) tính tự động. (4) ROIC dùng 20% tax trừ khi có `tax_commitment_years > 5` |
| **Definition of Done** | Code + test cases bao gồm trường hợp thiếu announcement_date và thiếu BCTC năm t-1 |

---

### EPIC 1.2 — FAILSAFE & HARD LAWS

#### TASK-111: Hard Law Enforcement Engine

| Field | Value |
|:---|:---|
| **Objective** | Module kiểm tra tất cả Hard Laws trước khi bất kỳ lệnh nào được thực thi |
| **Inputs** | Proposed order, current portfolio state, current NAV |
| **Outputs** | `HardLawCheck` (PASS/FAIL, violated_law, reason) |
| **Dependencies** | DATA_SCHEMA Position, Order entities |
| **Acceptance Criteria** | (1) Check Điều 1: position loss ≤ 2% NAV. (2) Check Điều 2: thoát ≤ 5 phiên (dùng ADTV20). (3) Check Điều 4: single stock ≤ 15% NAV, sector ≤ 35% NAV. (4) FAIL phải có `violated_law` cụ thể. (5) Không có cơ chế override |
| **Definition of Done** | Code + test case cho từng Hard Law + test case cho combined violation |

---

#### TASK-112: Failsafe & Heartbeat System

| Field | Value |
|:---|:---|
| **Objective** | Heartbeat monitoring và Failsafe kích hoạt khi mất kết nối |
| **Inputs** | Broker API endpoint, heartbeat interval config |
| **Outputs** | `FailsafeStatus` (ACTIVE/INACTIVE), events log |
| **Dependencies** | TASK-111 |
| **Acceptance Criteria** | (1) Heartbeat check mỗi 30 giây. (2) Failsafe ACTIVE sau 3 missed heartbeats liên tiếp. (3) Failsafe ACTIVE khi latency > 1500ms kéo dài > 5 giây. (4) Khi ACTIVE: hủy pending orders, không nhận order mới, gửi alert. (5) Execution Engine check FailsafeStatus trước mỗi lệnh |
| **Definition of Done** | Code + test với mock broker (simulate disconnect, high latency) + alert integration |

---

#### TASK-113: Stop-Loss Engine

| Field | Value |
|:---|:---|
| **Objective** | Real-time giám sát P&L và kích hoạt stop-loss khi vi phạm Hard Law Điều 1 |
| **Inputs** | Open positions, real-time prices, current NAV |
| **Outputs** | `StopLossOrder` — gửi thẳng đến Execution Engine (bypass Portfolio Agent) |
| **Dependencies** | TASK-111, TASK-112 |
| **Acceptance Criteria** | (1) Check mỗi price update. (2) Trigger khi `unrealized_pnl_pct_nav <= -2%`. (3) Stop-loss order phải có urgency = EMERGENCY. (4) Không cần Portfolio Agent approval. (5) Log trigger event với timestamp và NAV tại thời điểm trigger |
| **Definition of Done** | Code + test case: simulate price drop gây 2.1% NAV loss → stop-loss phải fire trong < 30 giây |

---

## PHASE 2 — CORE SIGNAL ENGINE
*Mục tiêu: Hệ thống có thể tính toán signals đáng tin cậy cho toàn Universe.*

---

### EPIC 2.1 — UNIVERSE & FILTERING

#### TASK-201: Universe Manager

| Field | Value |
|:---|:---|
| **Objective** | Duy trì Universe list với phân loại Group A/B/C/Sandbox/Excluded |
| **Inputs** | HOSE listing data, ADTV20, market cap, trading status, audit opinion |
| **Outputs** | `Security.universe_group` updated, `exclusion_log` |
| **Dependencies** | TASK-103, TASK-104 |
| **Acceptance Criteria** | (1) Hard filter: loại ngay khi trading_status != NORMAL. (2) ADTV20 tính từ `volume_continuous` (KHÔNG dùng total volume). (3) Group A phải bao gồm tất cả VN30 members. (4) Sandbox criteria: đúng 4 điều kiện trong DATA_REQUIREMENTS. (5) `exclusion_log` có lý do cụ thể |
| **Definition of Done** | Code + weekly schedule + test case với ticker đang bị cảnh báo |

---

#### TASK-202: Beneish M-Score Engine

| Field | Value |
|:---|:---|
| **Objective** | Tính Beneish M-Score và gate ticker không đạt |
| **Inputs** | `FinancialStatement` 2 năm gần nhất |
| **Outputs** | `Security.beneish_score`, `Security.beneish_status` (PASS/FAIL/PENDING) |
| **Dependencies** | TASK-104 |
| **Acceptance Criteria** | (1) Công thức 8 biến đúng theo TASK spec trong Blueprint. (2) Ngưỡng loại: M-Score > -1.78. (3) Nếu thiếu BCTC năm t-1 → status = PENDING (không PASS, không FAIL). (4) Gate: FAIL → không vào discovery_list. (5) Chạy lại sau mỗi mùa BCTC |
| **Definition of Done** | Code + test với công thức tính tay verify 3 ticker + PENDING test case |

---

#### TASK-203: Graph Intelligence Layer (GIL)

| Field | Value |
|:---|:---|
| **Objective** | Xây dựng ownership graph và phát hiện cấu trúc rủi ro Catastrophic |
| **Inputs** | `OwnershipRecord`, `InsiderTransaction`, RPT disclosures |
| **Outputs** | `Security.gil_flag`, OCR score, cycles detected count |
| **Dependencies** | TASK-101 (data), Graph database setup |
| **Acceptance Criteria** | (1) Node types: Company, Person, LegalEntity. (2) Edge types: OWNS, TRANSACTION, GUARANTEES, TRANSFER. (3) Cycle detection chạy đúng (test với graph có cycle nhân tạo). (4) OCR = sum ownership pct của entities liên kết về cùng 1 controller. (5) CATASTROPHIC flag khi: cycle value > 15% revenue HOẶC > 15% current assets |
| **Definition of Done** | Code + test graph với cycle và non-cycle + doc về cách update graph khi có disclosure mới |

---

### EPIC 2.2 — FACTOR ENGINE

#### TASK-211: Factor Engine — Value Group (F1)

| Field | Value |
|:---|:---|
| **Objective** | Tính 4 value factors, chuẩn hóa thành percentile rank 0–100 trong Universe |
| **Inputs** | `FinancialStatement`, `MarketDataDaily` |
| **Outputs** | `FactorScore.f1_value` |
| **Dependencies** | TASK-201, TASK-202 |
| **Acceptance Criteria** | (1) Chỉ dùng data sau `announcement_date`. (2) Percentile rank: low P/E → high score (invert). (3) Normalize trong Universe ngày đó (universe_size phải được ghi). (4) Null khi thiếu data, không interpolate |
| **Definition of Done** | Code + test: verify rank thay đổi khi Universe thay đổi |

---

#### TASK-212: Factor Engine — Quality Group (F2)

| Field | Value |
|:---|:---|
| **Objective** | Tính ROIC, GPM Stability, Accrual Ratio, Piotroski F-Score |
| **Inputs** | `FinancialStatement` 8 quý |
| **Outputs** | `FactorScore.f2_quality` + từng sub-score |
| **Dependencies** | TASK-104, TASK-211 |
| **Acceptance Criteria** | (1) ROIC: dùng effective_tax_rate chỉ khi có cam kết > 5 năm. (2) GPM Stability: so sánh YoY quarterly (Q1-2024 vs Q1-2023), KHÔNG so QoQ. (3) Accrual Ratio: cross-validate với GIL RPT receivables (flag nếu RPT cao). (4) Piotroski: 9 điểm, 3 nhóm, ghi riêng `profitability_subscore` |
| **Definition of Done** | Code + test case cho GPM seasonal company (để verify YoY, không QoQ) |

---

#### TASK-213: Factor Engine — Momentum Group (F3)

| Field | Value |
|:---|:---|
| **Objective** | Tính Price Momentum (12M-1M), SUE hoặc SUE_proxy, Relative Strength |
| **Inputs** | `MarketDataDaily`, `FinancialStatement`, analyst consensus (nếu có) |
| **Outputs** | `FactorScore.f3_momentum` |
| **Dependencies** | TASK-103, TASK-104 |
| **Acceptance Criteria** | (1) Price Momentum: dùng OHLCV đã adjusted. (2) SUE: nếu consensus không có hoặc < 3 analysts → dùng SUE_proxy = (EPS_Q / EPS_Q-4) - 1. (3) Flag khi dùng proxy. (4) Window 12M-1M: loại 1 tháng gần nhất |
| **Definition of Done** | Code + test: verify SUE_proxy fallback khi thiếu consensus |

---

#### TASK-214: Factor Engine — Sentiment & Altdata Groups (F4, F6)

| Field | Value |
|:---|:---|
| **Objective** | Tính Foreign Flow Momentum, Insider Signal, Google Trends SVI |
| **Inputs** | Foreign flow data, Insider transactions, Google Trends API |
| **Outputs** | `FactorScore.f4_sentiment`, `FactorScore.f6_altdata` |
| **Dependencies** | TASK-103, DATA_REQUIREMENTS Groups 5, 7 |
| **Acceptance Criteria** | (1) F4.1: loại ngày `is_etf_rebalance_day = true`. (2) F4.3: chỉ dùng BUY_MARKET, SELL_MARKET, BUY_AGREEMENT, SELL_AGREEMENT — loại TRANSFER_INTERNAL, ESOP. (3) Signal date = `disclosure_date` (không phải `transaction_date`). (4) F6.1: `svi_signal = svi_zscore × polarity_score`, không dùng khi `polarity_score = null` |
| **Definition of Done** | Code + test case: ETF rebalance day → F4.1 không thay đổi |

---

#### TASK-215: CSS Scoring Engine

| Field | Value |
|:---|:---|
| **Objective** | Tổng hợp 6 nhóm factor thành CSS và Conviction Level theo regime |
| **Inputs** | `FactorScore` (F1–F6), `current_regime` |
| **Outputs** | `FactorScore.css`, `FactorScore.conviction` |
| **Dependencies** | TASK-211 đến TASK-214, TASK-301 (HMM) |
| **Acceptance Criteria** | (1) Regime weights đúng theo bảng REGIME_WEIGHTS trong Blueprint. (2) Bear Trending: CSS × 0.5. (3) Conviction: A+ ≥ 85, A ≥ 75, B ≥ 60, C ≥ 45, D < 45. (4) < 100 trades: equal weighting. ≥ 100 trades: IC-weighted với Ledoit-Wolf |
| **Definition of Done** | Code + test: Bear Trending + CSS=80 → output ≤ 40 |

---

### EPIC 2.3 — MOAT AI ENGINE

#### TASK-221: Moat AI — Document Ingestion

| Field | Value |
|:---|:---|
| **Objective** | Thu thập và parse tài liệu phi cấu trúc (annual reports, IR docs) |
| **Inputs** | Ticker list, document URLs hoặc PDF files |
| **Outputs** | Cleaned text per document, stored với ticker và doc_date |
| **Dependencies** | TASK-201 |
| **Acceptance Criteria** | (1) Support PDF và HTML. (2) Dedup: không process lại doc đã có. (3) Incremental: chỉ fetch doc mới. (4) Store raw text + metadata (ticker, doc_type, doc_date) |
| **Definition of Done** | Code + test với sample PDF |

---

#### TASK-222: Moat AI — LLM Scoring

| Field | Value |
|:---|:---|
| **Objective** | Gọi LLM để phân tích tài liệu và sinh Moat Score |
| **Inputs** | Cleaned document text từ TASK-221 |
| **Outputs** | `moat_score` (0–100), `moat_breakdown` (4 dimensions), `hallucination_risk` |
| **Dependencies** | TASK-221 |
| **Acceptance Criteria** | (1) Prompt template đúng như trong Blueprint (strict JSON output). (2) Parse JSON response, handle malformed output gracefully. (3) `hallucination_risk = HIGH` khi model không có evidence trích dẫn. (4) Khi `hallucination_risk = HIGH`: giảm weight Moat xuống 50% trong CSS. (5) Không dùng Moat Score khi không có document |
| **Definition of Done** | Code + test với mock LLM response (valid JSON, malformed JSON, missing evidence) |

---

## PHASE 3 — DECISION ENGINE
*Mục tiêu: Hệ thống có thể ra quyết định phân bổ vốn tự động.*

---

### EPIC 3.1 — REGIME & RISK

#### TASK-301: HMM Regime Classifier

| Field | Value |
|:---|:---|
| **Objective** | Phân loại 4 regime với Hysteresis |
| **Inputs** | VN-Index OHLCV, advance/decline count, volume data |
| **Outputs** | `current_regime` (Bull_Trending/Bull_Choppy/Bear_Trending/Bear_Bounce) |
| **Dependencies** | TASK-103 |
| **Acceptance Criteria** | (1) 3 observable variables: VN-Index vs MA50, AD ratio 20D, Volume trend. (2) Hysteresis: chỉ chuyển regime khi posterior vượt trội ≥ 15% VÀ duy trì ≥ 3 phiên. (3) Validate: tháng 3/2020 phải label Bear_Trending ≥ 80% phiên. (4) Retrain quarterly, không auto-retrain hàng ngày |
| **Definition of Done** | Code + Hysteresis unit test (2 phiên không switch, 3 phiên switch) + historical validation report |

---

#### TASK-302: GARCH Cash Engine

| Field | Value |
|:---|:---|
| **Objective** | Tính VIX_VN_analog và Cash Target |
| **Inputs** | VN-Index log returns 252 ngày |
| **Outputs** | `vix_vn_analog` (0–100), `cash_target_pct` |
| **Dependencies** | TASK-103, TASK-301 |
| **Acceptance Criteria** | (1) GARCH(1,1) trên log returns. (2) Annualize: daily_vol × sqrt(252). (3) Normalize về 0–100 dùng rolling 252 phiên. (4) Cash formula: BASE[regime] + 0.003 × vix_vn, capped at 80%. (5) BASE: Bull_Trending=5%, Bull_Choppy=15%, Bear_Trending=40%, Bear_Bounce=25% |
| **Definition of Done** | Code + test: verify cap at 80% + verify base theo regime |

---

#### TASK-303: Risk Engine — ES & Drawdown

| Field | Value |
|:---|:---|
| **Objective** | Tính ES 97.5% và quản lý Drawdown Protocol |
| **Inputs** | Portfolio daily returns (500 phiên), current NAV, peak NAV |
| **Outputs** | `portfolio_es_975`, `drawdown_protocol` tier, `drawdown_action` |
| **Dependencies** | TASK-111 |
| **Acceptance Criteria** | (1) Historical Simulation, window 500 phiên (dùng fewer nếu chưa đủ, flag ESTIMATE). (2) ES = mean của 2.5% worst returns (dương). (3) Drawdown tiers: 5% ALERT, 10% YELLOW (-20% exposure), 15% ORANGE (-40%), 20% RED (-100% mua mới). (4) Alert ngay khi ES > 4% NAV |
| **Definition of Done** | Code + test: verify ES calculation với synthetic data + all 4 drawdown tiers |

---

### EPIC 3.2 — PORTFOLIO DECISIONS

#### TASK-311: Counter Thesis Engine

| Field | Value |
|:---|:---|
| **Objective** | Tự động evaluate rủi ro và block/approve thesis |
| **Inputs** | `InvestmentThesis`, GIL output, Beneish score, `current_regime` |
| **Outputs** | `cts_score` (0–100), `verdict` (PROCEED/CONDITIONAL/BLOCK), `block_reasons` |
| **Dependencies** | TASK-203, TASK-202, TASK-301 |
| **Acceptance Criteria** | (1) `gil_flag = CATASTROPHIC` → verdict = BLOCK, `cts_score = 0`, no exception. (2) Bear_Trending → tự động tăng risk weights. (3) Verdict = CONDITIONAL phải có điều kiện cụ thể, không chung chung. (4) Không thể PROCEED khi chưa pass Beneish và GIL |
| **Definition of Done** | Code + test: CATASTROPHIC input → BLOCK + test: Bear_Trending tăng CTS vs Bull_Trending |

---

#### TASK-312: Kelly Position Sizer

| Field | Value |
|:---|:---|
| **Objective** | Tính position size theo Quarter Kelly với tất cả constraints |
| **Inputs** | conviction, `current_regime`, NAV, ADTV20, win_rate và payoff từ Learning Agent |
| **Outputs** | `position_size_vnd` (đã áp dụng tất cả limits) |
| **Dependencies** | TASK-303, TASK-111 |
| **Acceptance Criteria** | (1) Quarter Kelly = (full_kelly / 4) × NAV. (2) Conviction limits: A+=15%, A=12%, B=8%, C=5%. (3) Liquidity limit = ADTV20 × 5 × 0.20. (4) Hard Stop limit = (2% NAV) / stop_loss_pct. (5) Output = min(all constraints). (6) CDC active → 1/8 Kelly thay vì 1/4 |
| **Definition of Done** | Code + test: verify output = min(constraints) không phải một constraint cụ thể |

---

#### TASK-313: Portfolio Optimizer

| Field | Value |
|:---|:---|
| **Objective** | Chọn 12–18 vị thế tối ưu từ candidates |
| **Inputs** | Candidates list (PROCEED verdict), current portfolio, correlation matrix |
| **Outputs** | `portfolio_decision` — list BUY/SELL/HOLD với sizes |
| **Dependencies** | TASK-311, TASK-312, TASK-302 |
| **Acceptance Criteria** | (1) Max 18, min 12 vị thế. (2) Pairwise correlation < 0.5 (greedy selection, sorted by CSS). (3) Sector constraint: mỗi sector ≤ 35% NAV. (4) Rebalance chỉ khi drift > ±5% VÀ duy trì 3 phiên HOẶC drift > 1.5× vol_20d. (5) Decision log đầy đủ rationale cho mỗi quyết định |
| **Definition of Done** | Code + test: verify correlation constraint (thêm ticker correlation cao → bị reject) |

---

## PHASE 4 — EXECUTION LAYER

---

### EPIC 4.1 — ORDER EXECUTION

#### TASK-401: Execution Adaptation Engine (EAE)

| Field | Value |
|:---|:---|
| **Objective** | Thực thi lệnh theo mode phù hợp với điều kiện thị trường |
| **Inputs** | `order_instruction`, order book, volume ratio, spread ratio, session_context |
| **Outputs** | `execution_report` (fill_price, fill_volume, slippage, mode_used) |
| **Dependencies** | TASK-112, TASK-113, TASK-103 |
| **Acceptance Criteria** | (1) Mode selection: CRISIS khi bear_prob > 80% hoặc breadth < 10%; STRESS khi volume_ratio < 50% hoặc spread > 2×; NORMAL otherwise. (2) Không thực thi khi Failsafe ACTIVE. (3) Không vượt 20% ADTV20/phiên. (4) Dùng VWAP khi volume_atc > 30% total. (5) Ghi actual slippage vs expected |
| **Definition of Done** | Code + test mỗi mode + test: Failsafe ACTIVE → reject order |

---

#### TASK-402: VN30F Hedge Controller

| Field | Value |
|:---|:---|
| **Objective** | Tự động short VN30F khi cần phòng vệ |
| **Inputs** | `market_breadth`, HMM bear probability, portfolio exposure |
| **Outputs** | VN30F short orders, `hedge_ratio` |
| **Dependencies** | TASK-301, TASK-303, TASK-401 |
| **Acceptance Criteria** | (1) Trigger: breadth < 15% HOẶC bear_prob > 80%. (2) Hedge ratio dynamic: 0.20 đến 0.80 theo severity. (3) Max hedge 80% portfolio. (4) Track basis risk: alert khi basis > 2% bất thường. (5) Unwind khi cả 2 conditions không còn thỏa mãn |
| **Definition of Done** | Code + test trigger + test unwind conditions |

---

## PHASE 5 — INTELLIGENCE LAYER

---

### EPIC 5.1 — LEARNING & MONITORING

#### TASK-501: Learning Agent — IC Tracking

| Field | Value |
|:---|:---|
| **Objective** | Track Information Coefficient của từng factor, phát hiện decay |
| **Inputs** | `FactorScore` lúc entry, realized returns sau khi close position |
| **Outputs** | `ic_report` (IC rolling 20, 60 phiên, per regime), `decay_diagnosis` |
| **Dependencies** | TASK-215, Position close events |
| **Acceptance Criteria** | (1) IC = Spearman correlation(factor_scores, realized_returns) per regime. (2) Decay trigger: IC_rolling_20 < IC_baseline × 0.50. (3) Decay diagnosis: 4 loại (DATA_ERROR/REGIME_MISMATCH/CROWDING/STRUCTURAL_DECAY). (4) Chỉ STRUCTURAL_DECAY mới trigger CDC. (5) Baseline thiết lập sau 100 trades |
| **Definition of Done** | Code + test: simulate IC drop 60% → STRUCTURAL_DECAY diagnosis |

---

#### TASK-502: MRAL Diagnostic Engine

| Field | Value |
|:---|:---|
| **Objective** | Theo dõi model reality alignment: predicted vs realized |
| **Inputs** | Predicted IC, realized IC, expected slippage, actual slippage, regime predictions |
| **Outputs** | MRAL dashboard, `cdc_signal` khi cần |
| **Dependencies** | TASK-501, TASK-401 |
| **Acceptance Criteria** | (1) Track daily: realized IC vs predicted IC. (2) Track daily: actual slippage vs baseline. (3) CDC trigger: IC decay > 50% HOẶC slippage > 2× baseline (5 lệnh liên tiếp). (4) CDC effect: Kelly × 0.5 (từ 1/4 xuống 1/8) |
| **Definition of Done** | Code + test CDC trigger từ cả hai paths |

---

#### TASK-503: Audit Trail Engine

| Field | Value |
|:---|:---|
| **Objective** | Ghi bất biến mọi quyết định quan trọng |
| **Inputs** | Events từ tất cả agents |
| **Outputs** | Immutable audit log với hash-chaining |
| **Dependencies** | DATA_SCHEMA (tất cả entities) |
| **Acceptance Criteria** | (1) Hash-chaining: mỗi record chứa hash của record trước. (2) Không có DELETE operation trên audit table cho bất kỳ user. (3) Tất cả 10 loại events trong SYSTEM_SPEC Audit Requirements đều được capture. (4) Timestamp precision: milliseconds |
| **Definition of Done** | Code + test: verify hash chain integrity + test no-delete enforcement |

---

## BẢNG DEPENDENCY MAP

```
Phase 1 (Foundation):
  TASK-101 (Data Quality)
  TASK-102 (CA Adjustment) → cần 101
  TASK-103 (OHLCV Ingest) → cần 101, 102
  TASK-104 (Financial Ingest) → cần 101
  TASK-111 (Hard Laws) → cần schema
  TASK-112 (Failsafe) → cần 111
  TASK-113 (Stop-loss) → cần 111, 112

Phase 2 (Signal):
  TASK-201 (Universe) → cần 103, 104
  TASK-202 (Beneish) → cần 104
  TASK-203 (GIL) → cần 101 + graph DB
  TASK-211–214 (Factors) → cần 201, 202, 103, 104
  TASK-215 (CSS) → cần 211–214 + 301
  TASK-221–222 (Moat AI) → cần 201

Phase 3 (Decision):
  TASK-301 (HMM) → cần 103
  TASK-302 (GARCH) → cần 103, 301
  TASK-303 (ES/Drawdown) → cần 111
  TASK-311 (Counter Thesis) → cần 203, 202, 301
  TASK-312 (Kelly) → cần 303, 111
  TASK-313 (Optimizer) → cần 311, 312, 302

Phase 4 (Execution):
  TASK-401 (EAE) → cần 112, 113, 103
  TASK-402 (VN30F Hedge) → cần 301, 303, 401

Phase 5 (Intelligence):
  TASK-501 (IC Tracking) → cần 215 + position close events
  TASK-502 (MRAL) → cần 501, 401
  TASK-503 (Audit) → cần tất cả schema
```

---

## ĐỊNH NGHĨA DONE (GLOBAL)

Mỗi Task chỉ được đánh dấu DONE khi có đủ:

```
✅ Code implementation
✅ Unit tests bao gồm happy path + edge cases
✅ Test cho failure scenarios (bad data, timeout, etc.)
✅ Technical documentation (function signatures, configs)
✅ Integration test với mock data (không cần live data)
✅ TODO comments cho bất kỳ thứ gì cần external dependency chưa xác định
```