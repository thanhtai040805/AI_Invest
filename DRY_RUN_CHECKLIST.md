# DRY_RUN_CHECKLIST.md — IOS v5.1

> **Mục đích:** Danh sách kiểm tra tuần tự trước khi chuyển từ paper trading sang live trading. Mỗi checkpoint phải PASS trước khi đi tiếp. Không có ngoại lệ.

> **Cách dùng:** Chạy theo thứ tự từ trên xuống. Ghi kết quả vào cột Status. Chỉ deploy khi toàn bộ cột Status = ✅ PASS.

---

## PHASE 0 — DATA INTEGRITY (Chạy trước tất cả)

Không có data sạch thì không có gì hết. Phase này là nền tảng.

| # | Checkpoint | Cách kiểm tra | Pass Condition | Status |
|:---|:---|:---|:---|:---|
| 0.1 | OHLCV data đủ cho toàn Universe | `len(missing_tickers) == 0` mỗi ngày trong 252 phiên gần nhất | 0 missing phiên | ☐ |
| 0.2 | Corporate action adjustment đúng | Lấy 5 ticker có split/dividend gần nhất, verify close_adj = close_unadj × adjustment_factor | 5/5 match | ☐ |
| 0.3 | Volume ATC được tách riêng | `volume_continuous + volume_atc + volume_ato ≈ volume_total` cho tất cả ticker | Sai số < 0.1% | ☐ |
| 0.4 | BCTC point-in-time đúng | Lấy 10 ticker ngẫu nhiên, verify signal chỉ xuất hiện sau `announcement_date` | 10/10 correct | ☐ |
| 0.5 | Không có look-ahead bias trong factor calculation | Chạy factor engine với date=T, verify không có data từ T+1 trở đi | Zero violations | ☐ |
| 0.6 | Data BCTC accuracy | Spot check 20 ticker: so sánh revenue, net_income, cfo với BCTC gốc SSC | Error rate < 2% | ☐ |
| 0.7 | Announcement date database đầy đủ | `count(null announcement_date) / count(all)` trong 4 quý gần nhất | < 5% null | ☐ |
| 0.8 | Daily data quality check script chạy được | Chạy `daily_data_quality_check()`, không exception | Clean run | ☐ |

---

## PHASE 1 — MODULE UNIT TESTS

Mỗi module chạy đúng theo spec.

### 1A — Universe Manager (M01)

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 1.1 | Hard filter loại đúng | Đưa vào 5 ticker đang bị cảnh báo → tất cả bị loại | 5/5 loại | ☐ |
| 1.2 | ADTV20 tính đúng (loại ATC) | Compare ADTV20 tính từ `volume_continuous` vs tính từ `volume_total` → phải khác nhau | Khác ≥ 5% | ☐ |
| 1.3 | Phân loại Group A/B/C đúng | Verify thủ công 10 ticker đã biết thuộc VN30 → đều là Group A | 10/10 đúng | ☐ |
| 1.4 | Sandbox criteria đúng | Đưa vào ticker tăng trưởng 30% 3 quý, nợ thấp → vào Sandbox | Pass | ☐ |

### 1B — Beneish M-Score (M03)

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 1.5 | 8 biến tính đúng công thức | Tính tay M-Score cho 3 ticker, compare với output module | Sai số < 0.01 | ☐ |
| 1.6 | Gate hoạt động đúng | Ticker có M-Score = -1.5 (> -1.78) → bị loại | Bị loại | ☐ |
| 1.7 | Không có exception khi thiếu data | Đưa vào ticker thiếu BCTC năm t-1 → graceful error, không crash | No crash | ☐ |

### 1C — Factor Engine (M06)

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 1.8 | ROIC tính đúng với tax_rate = 20% | Verify thủ công 5 ticker | 5/5 match | ☐ |
| 1.9 | ROIC dùng actual tax khi có cam kết > 5 năm | Đưa vào ticker có văn bản ưu đãi thuế → dùng thuế thực tế | Pass | ☐ |
| 1.10 | GPM Stability dùng YoY quarterly | GPM Q1-2024 so với Q1-2023 (KHÔNG so với Q4-2023) | Pass | ☐ |
| 1.11 | SUE_proxy kích hoạt khi thiếu consensus | Đưa vào Group B ticker không có analyst → dùng formula proxy | Pass | ☐ |
| 1.12 | Insider signal dùng disclosure_date | Signal date = disclosure_date, KHÔNG phải transaction_date | Pass | ☐ |
| 1.13 | F4.1 loại ngày ETF rebalance | Ngày rebalance ETF → foreign_flow không được dùng trong F4.1 | Pass | ☐ |
| 1.14 | Percentile normalization trong Universe | Tất cả factor scores nằm trong [0, 100] | Pass | ☐ |

### 1D — Scoring Engine (M07)

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 1.15 | Equal weighting khi < 100 trades | Khởi động với 0 trades → confirm equal weight | Pass | ☐ |
| 1.16 | Bear Trending giảm CSS 50% | Đưa ticker CSS = 80 vào Bear Trending → output ≤ 40 (×0.5) | Pass | ☐ |
| 1.17 | Conviction mapping đúng | CSS = 76 → "A", CSS = 86 → "A+", CSS = 44 → "D" | Pass | ☐ |

### 1E — HMM Regime (M09)

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 1.18 | Hysteresis hoạt động | Regime mới vượt 15% nhưng chỉ 2 phiên → KHÔNG chuyển | No change | ☐ |
| 1.19 | Hysteresis chuyển đúng | Regime mới vượt 15%, duy trì 3 phiên → chuyển | Switches | ☐ |
| 1.20 | Regime label historical hợp lý | Label tháng 3/2020 và tháng 11/2022 phải là Bear Trending | ≥ 80% days | ☐ |

### 1F — Risk Engine (M13)

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 1.21 | Hard Stop không thể override | Gọi `override_stop_loss()` → function không tồn tại / raise exception | Exception | ☐ |
| 1.22 | ES tính từ historical 500 phiên | Verify window = 500 phiên, quantile = 97.5% | Pass | ☐ |
| 1.23 | Drawdown protocol kích hoạt đúng | Simulate drawdown 12% → action = YELLOW, reduce exposure 20% | Pass | ☐ |
| 1.24 | Max concentration limit enforce | Thử set position = 16% NAV → bị reject | Rejected | ☐ |
| 1.25 | Sector limit enforce | Thử set sector = 36% NAV → bị reject | Rejected | ☐ |

### 1G — Failsafe (M17)

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 1.26 | Failsafe kích hoạt sau 3 missed heartbeats | Simulate 3 consecutive timeout → Failsafe ACTIVE | Active | ☐ |
| 1.27 | Failsafe kích hoạt khi latency > 1500ms × 5 giây | Simulate high latency → Failsafe ACTIVE | Active | ☐ |
| 1.28 | Failsafe hủy pending orders | Sau khi Failsafe active → `pending_orders == []` | Empty | ☐ |
| 1.29 | Failsafe ghi log | Log file có entry với timestamp và reason | Pass | ☐ |
| 1.30 | Failsafe gửi alert | Alert được gửi (email/Telegram/Slack) | Received | ☐ |

---

## PHASE 2 — INTEGRATION TESTS

Các module phối hợp đúng với nhau.

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 2.1 | Pipeline chạy end-to-end không lỗi | Chạy full pipeline với Universe = 10 ticker, 1 ngày | No exception | ☐ |
| 2.2 | GIL CATASTROPHIC block investment | Ticker có `gil_flag = CATASTROPHIC` → không xuất hiện trong portfolio candidates | Blocked | ☐ |
| 2.3 | Beneish FAIL block investment | Ticker bị M-Score loại → không xuất hiện trong factor engine | Blocked | ☐ |
| 2.4 | D/E conviction block investment | Ticker có conviction D → không xuất hiện trong execution orders | Blocked | ☐ |
| 2.5 | CDC giảm Kelly đúng | Kích hoạt CDC → position size giảm từ 1/4K xuống 1/8K | Halved | ☐ |
| 2.6 | P_fail > 10% giảm Kelly | Simulate P_fail = 12% → Kelly scaling giảm | Reduced | ☐ |
| 2.7 | Priority order enforce | Drawdown RED đang active + tín hiệu mua A+ → KHÔNG mua (capital preservation wins) | No buy | ☐ |
| 2.8 | Data contract format đúng | Output của mỗi module match JSON schema đã định nghĩa trong Blueprint | 100% match | ☐ |

---

## PHASE 3 — DRY RUN (Paper Trading)

Chạy hệ thống với data thực nhưng KHÔNG đặt lệnh thật. Tối thiểu **20 phiên giao dịch liên tiếp**.

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 3.1 | Pipeline chạy đúng giờ mỗi ngày | 06:00 morning run, 15:00 end-of-day run, không fail | 20/20 ngày | ☐ |
| 3.2 | Universe update đúng | Weekly review chạy thứ Hai, kết quả hợp lý | 4/4 tuần | ☐ |
| 3.3 | Regime label cập nhật hàng ngày | `current_regime` thay đổi khi thị trường thay đổi | Pass | ☐ |
| 3.4 | CSS được tính cho tất cả Universe | Không có ticker eligible nào bị bỏ sót | 100% coverage | ☐ |
| 3.5 | Simulated slippage được tính | Mỗi simulated trade có estimated_slippage ghi lại | All trades | ☐ |
| 3.6 | Simulated NAV tracking đúng | NAV được update sau mỗi phiên, PnL = sum(position PnL) | Reconciles | ☐ |
| 3.7 | Drawdown tracking đúng | Peak NAV được track, drawdown = (peak - current) / peak | Correct | ☐ |
| 3.8 | Không có position vượt 15% NAV | Scan toàn bộ simulated portfolio | Zero violations | ☐ |
| 3.9 | Không có sector vượt 35% NAV | Scan theo sector | Zero violations | ☐ |
| 3.10 | Log đủ chi tiết để debug | Mỗi quyết định có: timestamp, ticker, reason, factor_scores, conviction | All present | ☐ |

---

## PHASE 4 — HISTORICAL REPLAY CHECK

Kiểm tra xem hệ thống có bị look-ahead bias không bằng cách replay lại một giai đoạn đã biết.

| # | Checkpoint | Pass Condition | Status |
|:---|:---|:---|:---|
| 4.1 | Chọn giai đoạn test: Q1/2020 (COVID crash) | — | Selected | ☐ |
| 4.2 | Tháng 3/2020 HMM label là Bear Trending | ≥ 80% phiên | Pass | ☐ |
| 4.3 | Hệ thống tăng cash trong tháng 3/2020 | Cash target > 30% | Pass | ☐ |
| 4.4 | Không có buy signal mạnh khi Bear Trending peak | Conviction A+/A count giảm ≥ 50% vs normal period | Pass | ☐ |
| 4.5 | Chọn giai đoạn test: Q4/2021 (bull run) | — | Selected | ☐ |
| 4.6 | Q4/2021 HMM label là Bull Trending | ≥ 70% phiên | Pass | ☐ |
| 4.7 | Simulated portfolio outperform VN-Index trong Q4/2021 | Return > VN-Index return | Pass | ☐ |
| 4.8 | Không có position có loss > 2% NAV mà chưa bị stop | Zero violations | ☐ |

---

## PHASE 5 — PRE-LIVE CHECKLIST

Các thứ cần có trước khi bật switch sang live.

| # | Checkpoint | Status |
|:---|:---|:---|
| 5.1 | Broker API kết nối thành công và đặt lệnh test thành công | ☐ |
| 5.2 | Heartbeat đang ping broker API mỗi 30 giây | ☐ |
| 5.3 | Alert system (email/Telegram) nhận được message test | ☐ |
| 5.4 | Failsafe đã được test thủ công ít nhất 1 lần (simulate disconnect) | ☐ |
| 5.5 | Starting NAV đã được xác nhận và ghi vào hệ thống | ☐ |
| 5.6 | Max daily loss limit được set (ngoài ES): nếu NAV giảm > X% trong 1 ngày → halt | ☐ |
| 5.7 | Người vận hành có thể can thiệp thủ công (manual override với audit trail) | ☐ |
| 5.8 | Backup data source đã được test | ☐ |
| 5.9 | Database backup tự động đang chạy | ☐ |
| 5.10 | Documentation đủ để người khác vận hành thay khi cần | ☐ |

---

## TỔNG KẾT

```
Phase 0 — Data Integrity:     8 checkpoints
Phase 1 — Unit Tests:        30 checkpoints
Phase 2 — Integration:        8 checkpoints
Phase 3 — Dry Run:           10 checkpoints
Phase 4 — Historical Replay:  8 checkpoints
Phase 5 — Pre-Live:          10 checkpoints
─────────────────────────────────────────────
TOTAL:                        74 checkpoints
```

**Điều kiện deploy live:** 74/74 ✅ PASS.

Nếu bất kỳ checkpoint nào FAIL → fix → rerun từ phase đó. Không skip.

> **Nhắc lại ba câu hỏi của IOS v5.1 trước khi bật live:**  
> *"Tại sao bây giờ, tại sao cổ phiếu này, và tôi có thể sai như thế nào?"*  
> Nếu hệ thống không trả lời được cả ba — Không bật.