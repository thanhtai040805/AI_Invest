# SYSTEM_SPEC.md — IOS v5.1
## Đặc Tả Hệ Thống Tổng Thể

> **Mục đích:** Mô tả hệ thống là GÌ, làm GÌ, cần ĐÁP ỨNG yêu cầu gì. Không chọn framework, không chọn ngôn ngữ, không thiết kế code. Đây là cầu nối giữa AGENTS.md (nghiệp vụ) và IMPLEMENTATION_PLAN.md (coding tasks).

---

## 1. SYSTEM OBJECTIVES

**Mục tiêu chính:**
Xây dựng một Autonomous Investment System hoạt động trên thị trường HOSE, thực thi toàn bộ quy trình từ phát hiện cơ hội đến thực thi lệnh và học hỏi, với sự can thiệp thủ công ở mức tối thiểu.

**3 tiêu chí thành công:**
1. **Tồn tại vốn:** Không bao giờ để tổn thất vị thế đơn lẻ vượt 2% NAV. Không deploy lệnh khi hệ thống có lỗi không xác định.
2. **Alpha sinh ra:** Danh mục outperform VN-Index sau 1 năm vận hành đủ điều kiện.
3. **Cải thiện liên tục:** IC của hệ thống không giảm theo thời gian sau khi kiểm soát regime.

**Hệ thống KHÔNG được làm:**
- Thay đổi logic đầu tư trong IOS mà không có sự phê duyệt của Governance + CIO
- Tạo rule mới không có trong IOS
- Hoạt động khi data pipeline chưa pass quality check

---

## 2. CORE COMPONENTS

Hệ thống gồm 5 thành phần lớn, tương ứng với 4 tầng trong IOS v5.1:

### Component A — Data Ingestion & Quality Layer
- Thu thập dữ liệu từ tất cả nguồn trong DATA_REQUIREMENTS.md
- Validate, clean, và corporate action adjust
- Enforce point-in-time integrity
- Lưu trữ theo schema trong DATA_SCHEMA.md
- Expose data qua internal interface cho các components khác

### Component B — Signal Generation Layer
- Chạy Beneish M-Score filter
- Tính toán 6 nhóm factor scores (F1–F6)
- Chạy Moat AI Engine (NLP trên tài liệu phi cấu trúc)
- Tổng hợp CSS theo regime
- Gán Conviction Level

### Component C — Decision Engine Layer
- Phân loại HMM Regime (với Hysteresis)
- Tính GARCH Cash Target
- Chạy Counter Thesis logic
- Tính Quarter Kelly position size
- Tối ưu hóa danh mục (12–18 vị thế, pairwise correlation constraint)
- Quản lý Drawdown Protocol
- Điều khiển VN30F Hedge

### Component D — Execution & Control Layer
- Thực thi lệnh theo EAE (NORMAL/STRESS/CRISIS)
- Heartbeat monitoring
- Failsafe Engine
- Real-time position monitoring và stop-loss
- Capital Degradation Control (CDC)

### Component E — Intelligence & Governance Layer
- MRAL diagnostics (IC tracking, slippage tracking)
- Learning Loop (walk-forward, factor IC)
- Audit trail
- Version control cho rules và weights
- Agent coordination và escalation

---

## 3. DATA FLOW

### Luồng chính (hàng ngày):

```
06:00 — Morning Pipeline Start
  ├─ Data Quality Check (Component A)
  │     Nếu FAIL → halt toàn bộ pipeline, alert
  │
  ├─ Universe Update (Component B)
  │     Beneish scan (nếu mùa BCTC)
  │     GIL update
  │
  ├─ Factor Calculation (Component B)
  │     F1–F6 cho toàn Universe eligible
  │     Moat AI (incremental, chỉ ticker có doc mới)
  │     CSS + Conviction
  │
  ├─ Regime Classification (Component C)
  │     HMM với Hysteresis
  │     GARCH Cash Target update
  │
  ├─ Decision Engine (Component C)
  │     Thesis generation cho Conviction ≥ B
  │     Counter Thesis evaluation
  │     Risk check
  │     Portfolio optimization
  │     Order generation
  │
08:30 — Orders ready cho ngày
  │
09:00–14:30 — Trading Hours
  ├─ Market Surveillance: real-time monitoring
  ├─ Execution Engine: thực thi orders theo EAE
  ├─ Position Monitor: real-time P&L, stop-loss
  ├─ Risk Monitor: real-time ES, concentration check
  │
15:00–16:00 — End of Day
  ├─ NAV calculation
  ├─ Performance metrics update
  ├─ Slippage records → Learning Agent
  ├─ IC calculation update
  ├─ Risk Assessment snapshot
  │
  └─ Prepare for next day
```

### Luồng Intraday (event-driven):
- Anomaly detected → Market Surveillance → Alert đến tất cả agents
- Stop-loss triggered → Monitoring Agent → Execution Agent (bypass Portfolio)
- Drawdown crosses tier → Risk Agent → Portfolio Agent → adjust exposure
- VN30F Hedge trigger → Risk Agent → Execution Agent → short VN30F
- Failsafe triggered → Halt execution, notify Governance + CIO

---

## 4. DECISION FLOW

### Thứ tự ưu tiên khi conflict (không đảo ngược):

```
Priority 1: Hard Laws (Constitution) → Bất khả xâm phạm
Priority 2: Failsafe & Heartbeat → An toàn hệ thống
Priority 3: Drawdown Protocol → Bảo toàn vốn tích cực
Priority 4: Risk Limits (ES, Concentration) → Giới hạn rủi ro
Priority 5: CDC → Kiểm soát model degradation
Priority 6: Portfolio Optimization → Tối ưu phân bổ
Priority 7: Signal Generation → Tìm cơ hội
```

**Rule:** Khi bất kỳ Priority N kích hoạt action, mọi output của Priority > N bị override.

Ví dụ: Khi Drawdown = RED (Priority 3), lệnh mua từ Portfolio Optimization (Priority 6) bị block hoàn toàn.

---

## 5. SCHEDULING REQUIREMENTS

| Task | Frequency | Time | Deadline |
|:---|:---|:---|:---|
| Data Quality Check | Daily | 05:45 | 06:00 |
| Universe Review | Weekly (Monday) | 06:00 | 07:00 |
| Beneish Scan | Quarterly (post-BCTC) | 06:00 | 08:00 |
| GIL Update | Event-driven (new disclosure) | Within 2h | — |
| Factor Calculation | Daily | 06:00 | 07:30 |
| Moat AI | Incremental daily | 06:00 | 08:00 |
| HMM Regime | Daily (end-of-day input) | 06:00 | 08:30 |
| GARCH Cash Target | Daily | 06:00 | 08:30 |
| Order Generation | Daily | 08:00 | 08:30 |
| Market Surveillance | Real-time 09:00–15:00 | — | < 60s latency |
| Stop-loss Check | Real-time 09:00–15:00 | — | < 5min trigger |
| NAV Calculation | Daily | 15:00 | 15:30 |
| IC Update | Daily | 15:30 | 16:00 |
| HMM Retrain | Quarterly | Off-hours | — |
| Walk-Forward Review | Quarterly | Off-hours | — |
| Audit Report | Weekly | Sunday | Monday 09:00 |

---

## 6. MONITORING REQUIREMENTS

### 6.1 System Health Monitoring
- Heartbeat check mỗi 30 giây trong trading hours
- Data feed latency < 5 giây (alert nếu vượt)
- Data quality score phải > 95% mỗi ngày trước khi pipeline chạy
- Pipeline completion time: mỗi bước phải complete trước deadline

### 6.2 Investment Performance Monitoring
- NAV update cuối mỗi phiên
- Drawdown từ peak: tracking real-time trong trading hours
- ES 97.5%: tính daily, alert nếu > 4% NAV
- IC rolling 20 và 60 phiên: cập nhật daily
- Slippage thực tế vs expected: track mỗi lệnh

### 6.3 Model Health Monitoring
- IC decay detection: so sánh rolling IC với baseline
- Regime classification accuracy: đo lại mỗi quý
- Beneish False Positive rate: đo lại mỗi quý (khi có đủ data)
- Moat AI hallucination rate: đo khi có sample để validate

### 6.4 Compliance Monitoring
- Hard Law violations: zero tolerance, alert ngay lập tức
- Concentration limits: check trước mỗi lệnh
- Decision log completeness: 100% decisions phải có rationale
- Audit trail integrity: hash-based verification

---

## 7. AUDIT REQUIREMENTS

**Mọi hành động sau phải có audit record bất biến:**
- Tạo/sửa/đóng InvestmentThesis
- Tạo/thực thi/hủy Order
- Thay đổi trạng thái Position
- Kích hoạt/tắt Failsafe
- Kích hoạt/tắt CDC
- Kích hoạt Drawdown Protocol
- Kích hoạt VN30F Hedge
- Mọi thay đổi đến factor weights, thresholds, IOS parameters
- Mọi exception approval từ CIO Agent

**Audit record phải chứa:** timestamp (millisecond precision), agent_id, action, rationale, trạng thái trước, trạng thái sau.

**Retention:** Tối thiểu 5 năm. Không được xóa.

**Immutability:** Audit records không thể bị sửa sau khi tạo. Nếu cần correction, tạo correction record mới trỏ về record cũ.

---

## 8. RECOVERY REQUIREMENTS

### Data Feed Failure:
- Detect: trong vòng 60 giây
- Fallback: chuyển sang backup source tự động
- Nếu backup cũng fail: halt execution pipeline, giữ nguyên vị thế hiện tại
- Recovery: sau khi feed restore, replay data gap và recalculate signals

### Component Failure:
- Detection: heartbeat timeout hoặc error threshold
- Isolation: component failed không ảnh hưởng execution của positions đang mở
- Stop-loss vẫn hoạt động ngay cả khi Signal Generation bị down
- Recovery: restart component, không cần restart toàn hệ thống

### Database Failure:
- Không thực thi lệnh mới khi database unavailable
- In-memory cache đủ để tiếp tục giám sát vị thế và stop-loss trong 30 phút
- Full backup daily, incremental backup mỗi giờ

### Market Event Extreme (circuit breaker, halt toàn sàn):
- Hệ thống tự detect qua Market Surveillance
- Pause execution, không cancel orders đang pending
- Notify CIO Agent để có chỉ thị rõ ràng
- Resume chỉ khi HOSE chính thức thông báo mở lại

---

## 9. SECURITY REQUIREMENTS

### Data Security:
- API credentials không được lưu trong code
- Broker API credentials được mã hóa, rotate định kỳ
- Data feed access được authenticated

### Execution Security:
- Mọi lệnh phải có source tracing (từ agent nào, tại sao)
- Không có cơ chế "admin override" bỏ qua Hard Laws
- Rate limiting cho broker API calls để tránh rủi ro over-ordering

### Audit Security:
- Audit logs được hash-chained (mỗi record hash của record trước)
- Không có quyền DELETE trên audit tables cho bất kỳ user nào

---

## 10. SCALABILITY REQUIREMENTS

### Universe Scalability:
- Hệ thống thiết kế cho Universe tối đa 300 tickers
- Factor calculation < 30 phút cho 300 tickers
- Moat AI incremental: chỉ chạy lại khi có document mới (không full-scan daily)

### Computational Priorities:
- Priority 1 (must complete trước 08:30): Beneish filter, Factor scores, HMM, Order generation
- Priority 2 (best effort): Moat AI, GIL update, Alt data
- Priority 3 (can delay): Walk-forward backtesting, quarterly review

### Latency Requirements:
- Stop-loss detection → order instruction: < 30 giây
- Heartbeat check: 30 giây interval
- Anomaly alert broadcast: < 60 giây từ event

### Graph Database (GIL):
- Query ownership depth ≤ 3 levels: < 5 giây
- Cycle detection cho subgraph 1 company: < 10 giây
- Full universe scan: hàng tuần, off-hours, không giới hạn thời gian

---

## TỔNG KẾT — KHẾ ƯỚC HỆ THỐNG

```
Hệ thống CAM KẾT:
✅ Không bao giờ thực thi lệnh khi data quality failed
✅ Không bao giờ để stop-loss bị override vì bất kỳ lý do gì
✅ Không bao giờ thay đổi IOS logic mà không có Governance + CIO approval
✅ Không bao giờ mất audit record
✅ Luôn có khả năng dừng an toàn trong < 5 phút

Hệ thống KHÔNG CAM KẾT:
❌ Alpha dương mọi năm (market có thể làm tất cả strategy thua)
❌ Zero slippage (thanh khoản HOSE có giới hạn)
❌ Perfect regime classification (HMM có uncertainty)
```