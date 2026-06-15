# AGENT_INTERACTION.md — IOS v5.1
## Luồng Tương Tác Giữa Các Agent

> **Nguyên tắc:** Agents không giao tiếp tùy tiện. Mỗi luồng thông tin có nguồn, đích, điều kiện kích hoạt, và điều kiện dừng rõ ràng. Không có vòng lặp vô hạn. Không có quyết định mồ côi không có người nhận.

---

## PHẦN 1: SƠ ĐỒ LUỒNG TỔNG QUAN

```
[Market Surveillance] ──────────────────────────────────────────────┐
        │                                                            │
        │ market_pulse, anomaly_alert                                │
        ▼                                                            │
[Discovery Agent] ──────────────────────────────────────────────────┤
        │                                                            │
        │ discovery_list                                             │
        ▼                                                            │
[Research Agent] ───────────────────────────────────────────────────┤
        │                                                            │
        │ research_report                                            │
        ▼                                                            │
[Thesis Agent] ─────────────────────────────────────────────────────┤
        │                                                            │
        │ investment_thesis                                          │
        ▼                                                            │
[Counter Thesis Agent] ─────────────────────────────────────────────┤  Escalation
        │                                                            │  khi cần
        │ verdict (PROCEED/CONDITIONAL/BLOCK)                        ▼
        ▼                                                      [CIO Agent]
[Risk Agent] ──────────────────────────────────────────────────────►│
        │                                                            │
        │ risk_dashboard, position_risk_check                        │
        ▼                                                            │
[Portfolio Agent] ──────────────────────────────────────────────────┤
        │                                                            │
        │ order_instruction                                          │
        ▼                                                            │
[Execution Agent] ──────────────────────────────────────────────────┘
        │
        │ execution_report
        ▼
[Monitoring Agent] ◄─────── (giám sát vị thế đang mở liên tục)
        │
        │ stop_loss_order, thesis_invalidation_alert
        ├──────────────────► [Execution Agent] (stop-loss bypass)
        └──────────────────► [Portfolio Agent] (invalidation review)

[Learning Agent] ◄─────── nhận data từ Portfolio, Execution, Research
        │
        │ ic_report, quarterly_review
        ▼
[Governance Agent] ──────────────────────────────────────────────────
        │
        │ change_approval, audit_report
        ▼
[CIO Agent]
```

---

## PHẦN 2: INFORMATION FLOW (Luồng Thông Tin)

### 2.1 Market Surveillance → All Agents

**Gửi:** `session_summary` cuối phiên, `anomaly_alert` real-time, `market_pulse` mỗi 5 phút

**Nhận bởi:**
- Discovery Agent: dùng market_pulse để cập nhật ADTV20 real-time
- Risk Agent: dùng market_breadth cho VN30F hedge trigger và ES recalculation
- Execution Agent: dùng session_context (Normal/Stress/Crisis) để chọn execution mode
- Portfolio Agent: dùng regime_signal để chuẩn bị decisions

**Điều kiện dừng:** Market Surveillance không gửi ngoài giờ giao dịch (15:00–08:45)

---

### 2.2 Discovery Agent → Research Agent

**Gửi:** `discovery_list` — danh sách ticker eligible kèm Factor Score sơ bộ

**Điều kiện gửi:** Hàng ngày 06:30 (sau pre-market scan)

**Research Agent chỉ nhận ticker khi:**
- Không có flag BENEISH_FAIL
- Không có flag GIL_CATASTROPHIC
- Trading status = NORMAL

**Điều kiện dừng:** Discovery Agent không gửi nếu data quality check failed (Phase 0 checklist)

---

### 2.3 Research Agent → Thesis Agent

**Gửi:** `research_report` — factor breakdown, moat score, CSS, conviction

**Điều kiện gửi:** Conviction ≥ B (CSS ≥ 60)

**Không gửi khi:**
- Conviction D hoặc E
- `hallucination_risk = HIGH` trong Moat AI mà chưa có cross-verification
- Thiếu ≥ 2 factor groups data

---

### 2.4 Thesis Agent → Counter Thesis Agent

**Gửi:** `investment_thesis` đầy đủ

**Điều kiện gửi:** Thesis có đủ 3 confirming signals độc lập

**Quan trọng:** Counter Thesis Agent nhận TOÀN BỘ thesis, không phải bản tóm tắt

---

### 2.5 Counter Thesis Agent → Risk Agent & Portfolio Agent

**Gửi:** `counter_thesis_report` + `verdict`

**Luồng:**
```
verdict = BLOCK  → Risk Agent log, Portfolio Agent không nhận ticker này
verdict = CONDITIONAL → Risk Agent nhận kèm điều kiện, Portfolio Agent nhận sau khi điều kiện satisfied
verdict = PROCEED → Risk Agent nhận, Portfolio Agent nhận
```

---

### 2.6 Risk Agent → Portfolio Agent

**Gửi:** `risk_dashboard` (daily update) + `position_risk_check` (trước mỗi lệnh)

**Portfolio Agent chỉ ra lệnh mua khi:**
- `position_risk_check` = APPROVED
- Drawdown protocol tier ≠ RED
- Cash target có đủ room sau khi mua

---

### 2.7 Portfolio Agent → Execution Agent

**Gửi:** `order_instruction` — ticker, direction, size (VND), max price, urgency, execution_mode hint

**Execution Agent xác nhận lại trước khi thực thi:**
- Failsafe không ACTIVE
- Order không vượt 20% ADTV20

---

### 2.8 Execution Agent → Learning Agent

**Gửi:** `execution_report` sau MỖI lệnh — slippage actual, execution mode used, fill time

**Không trì hoãn:** Phải gửi trong vòng 30 phút sau khi lệnh fill

---

### 2.9 Monitoring Agent → Portfolio Agent + Execution Agent

**Gửi đến Execution Agent (BYPASS Portfolio Agent):**
- `stop_loss_order` — khi vị thế loss ≥ 2% NAV
- Không cần Portfolio Agent approval
- Execution Agent phải thực thi trong phiên ngay lập tức

**Gửi đến Portfolio Agent:**
- `thesis_invalidation_alert` — khi điều kiện invalidation xảy ra nhưng chưa chạm stop-loss
- `hold_review` — khi vị thế đạt target hoặc hết timeline

---

### 2.10 Learning Agent → Governance Agent

**Gửi:** `quarterly_review` (hàng quý) + `decay_diagnosis` (khi phát hiện IC decay)

**Governance Agent review trong 5 ngày làm việc** trước khi escalate lên CIO

---

## PHẦN 3: DECISION FLOW (Luồng Quyết Định)

### Luồng quyết định mua một cổ phiếu:

```
Step 1: Discovery Agent → ticker vào discovery_list ✓
Step 2: Research Agent → CSS ≥ 60, Conviction ≥ B ✓
Step 3: Thesis Agent → 3 confirming signals, invalidation conditions ✓
Step 4: Counter Thesis Agent → verdict = PROCEED ✓
Step 5: Risk Agent → position_risk_check = APPROVED ✓
Step 6: Portfolio Agent → quarter kelly size, concentration check ✓
Step 7: Execution Agent → thực thi theo mode phù hợp ✓
Step 8: Monitoring Agent → bắt đầu giám sát vị thế ✓
Step 9: Learning Agent → capture decision vào database ✓
```

**Nếu bất kỳ step nào FAIL → dừng tại đó, không tiếp tục**

---

### Luồng quyết định bán (không phải stop-loss):

```
Step 1: Monitoring Agent phát hiện thesis invalidation HOẶC target đạt HOẶC timeline hết
Step 2: Portfolio Agent review và confirm sell
Step 3: Risk Agent check: còn vị thế nào khác bị ảnh hưởng không
Step 4: Execution Agent thực thi sell
Step 5: Learning Agent capture realized return và outcome
```

---

### Luồng stop-loss (khẩn cấp):

```
Step 1: Monitoring Agent phát hiện loss ≥ 2% NAV
Step 2: NGAY LẬP TỨC gửi stop_loss_order đến Execution Agent (bypass Portfolio Agent)
Step 3: Execution Agent thực thi trong phiên, ưu tiên tối đa
Step 4: Monitoring Agent notify Portfolio Agent và Risk Agent ĐỒNG THỜI
Step 5: Risk Agent cập nhật risk_dashboard
Step 6: Learning Agent capture và flag để phân tích nguyên nhân
```

---

## PHẦN 4: ESCALATION FLOW (Luồng Leo Thang)

### Mức 1 — Agent tự xử lý (không cần leo thang):
- Slippage cao hơn expected trong một lệnh: Execution Agent điều chỉnh mode, ghi log
- Một factor thiếu data: Research Agent dùng fallback hoặc null, ghi flag
- Market Surveillance phát anomaly INFO: broadcast, các agent tự quyết định có cần hành động không

---

### Mức 2 — Escalate lên Agent kề trên trong pipeline:
Trigger:
- Data quality check failed một phần → Discovery Agent gửi partial list kèm DATA_QUALITY_WARNING → Research Agent nhận và flag
- Verdict CONDITIONAL từ Counter Thesis Agent → Portfolio Agent phải verify điều kiện trước khi mua
- CDC trigger → Risk Agent → Portfolio Agent phải giảm sizing ngay

---

### Mức 3 — Escalate lên Governance Agent:
Trigger:
- Bất kỳ Hard Law violation (dù nhỏ)
- Learning Agent phát hiện IC decay cần structural change
- Data source primary fail kéo dài > 1 ngày
- Bất kỳ thay đổi nào đến thresholds, formula, weights

**Governance Agent phản hồi trong 24 giờ.** Nếu không phản hồi → giữ nguyên trạng thái cuối, không hành động mới.

---

### Mức 4 — Escalate lên CIO Agent:
Trigger:
- Drawdown protocol = RED (tự động)
- Failsafe ACTIVE > 30 phút (tự động)
- Governance Agent và Portfolio Agent xung đột về một quyết định
- Learning Agent đề xuất thay đổi cấu trúc factor (retire/add factor)
- Sự kiện thị trường ngoài mọi kịch bản có trong IOS

**CIO phản hồi trong 4 giờ.** Nếu không phản hồi → Governance Agent giữ status quo.

---

## PHẦN 5: FEEDBACK FLOW (Luồng Phản Hồi)

### 5.1 Learning → Research (Factor Feedback):
```
Learning Agent tính IC thực tế của factor F theo regime R
→ Gửi ic_report cho Research Agent
→ Research Agent điều chỉnh cách interpret factor score (nhưng KHÔNG tự thay đổi weights)
→ Đề xuất weight change lên Governance
```

### 5.2 Execution → Risk (Slippage Feedback):
```
Execution Agent ghi slippage_record
→ Learning Agent tổng hợp slippage_baseline
→ Risk Agent dùng slippage_baseline để tính CDC trigger (slippage spike)
→ Nếu CDC trigger → Risk Agent notify Portfolio Agent giảm sizing
```

### 5.3 Monitoring → Thesis (Outcome Feedback):
```
Monitoring Agent record: thesis outcome (success/fail), thesis_id
→ Learning Agent match với factor scores lúc entry
→ Tính conditional win_rate theo conviction level và regime
→ Trả về win_rate_table cho Portfolio Agent để dùng trong Kelly sizing
```

### 5.4 Quarterly Review Loop:
```
Learning Agent → quarterly_review (đề xuất)
→ Governance Agent review (5 ngày)
→ CIO Agent approve/reject
→ Nếu approve: Governance Agent update hệ thống với version log
→ Discovery Agent, Research Agent, Risk Agent nhận config mới
→ Learning Agent reset IC baseline cho factors được cập nhật
```

---

## PHẦN 6: FAILURE RECOVERY FLOW

### Scenario 1: Market Surveillance ngắt kết nối

```
1. Execution Agent phát hiện không nhận market_pulse > 5 phút
2. Execution Agent chuyển sang PASSIVE mode (không thực thi lệnh mới)
3. Failsafe Agent kích hoạt Heartbeat check
4. Notify Governance Agent
5. Khi restore: Market Surveillance replay lại anomaly_alert của khoảng thời gian ngắt
6. Risk Agent recalculate với data bổ sung
7. Execution Agent resume khi Failsafe confirmed INACTIVE
```

---

### Scenario 2: Counter Thesis Agent không phản hồi

```
1. Thesis Agent gửi investment_thesis, không nhận verdict sau 2 giờ
2. Thesis Agent notify Governance Agent
3. Governance Agent: ticker đó tạm BLOCK cho đến khi Counter Thesis resume
4. Không có exception dù ticker có CSS = 99
```

---

### Scenario 3: Risk Agent phát hiện Hard Law violation đã xảy ra

```
1. Risk Agent detect: vị thế X đang chiếm 16% NAV (vượt 15% limit)
2. Risk Agent gửi violation_report ngay đến Governance Agent
3. Risk Agent gửi risk_override_required đến Portfolio Agent
4. Portfolio Agent PHẢI giảm vị thế X về ≤ 15% trong phiên tiếp theo
5. Execution Agent thực thi sell một phần
6. Governance Agent ghi vào violation_log (bất kể do nguyên nhân gì)
7. Learning Agent phân tích nguyên nhân violation
```

---

### Scenario 4: Toàn bộ pipeline stall (không có output trong > 3 giờ)

```
1. Governance Agent monitor: nếu không có decision_log mới trong 3 giờ giao dịch
2. Governance Agent escalate lên CIO Agent
3. CIO Agent review: có phải market holiday không? Có data feed issue không?
4. Nếu không rõ nguyên nhân → CIO Agent issue system_halt_order
5. Mọi vị thế giữ nguyên, không thực thi lệnh mới
6. Governance Agent log incident
7. Resume chỉ khi CIO Agent xác nhận nguyên nhân và issue resume_directive
```

---

## PHẦN 7: ĐIỀU KIỆN ĐẶC BIỆT

### Khi Regime = Bear Trending:
- Market Surveillance tăng tần suất anomaly alert
- Counter Thesis Agent tự động tăng CTS của tất cả thesis
- Portfolio Agent không nhận order mua mới trừ khi conviction A+
- Risk Agent giảm Cash Target delay, tăng cash ngay

### Khi Failsafe ACTIVE:
- Execution Agent: không thực thi bất kỳ lệnh nào
- Portfolio Agent: không ra lệnh mới
- Monitoring Agent: tiếp tục giám sát, stop-loss trigger vẫn queue (thực thi ngay khi Failsafe inactive)
- Governance Agent: log toàn bộ, notify CIO

### Khi CDC ACTIVE:
- Portfolio Agent: tự động giảm Kelly từ 1/4 xuống 1/8
- Research Agent: flag factor có IC decay > 50% trong research reports
- Learning Agent: tăng tần suất IC monitoring từ daily lên intraday