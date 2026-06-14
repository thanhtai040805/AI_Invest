# ASSUMPTION_REGISTER.md — IOS v5.1

> **Mục đích:** Liệt kê mọi thứ hệ thống đang tin là đúng nhưng chưa chứng minh bằng dữ liệu HOSE thực tế. Đây không phải danh sách lỗi — đây là danh sách rủi ro cần được kiểm soát và theo dõi.

> **Nguyên tắc đọc:** Severity = mức độ hệ thống bị phá vỡ NẾU giả định này sai. HIGH = hệ thống thua lỗ hệ thống. MEDIUM = hiệu suất giảm đáng kể. LOW = ảnh hưởng nhỏ, có thể bù đắp bằng phần khác.

---

## NHÓM A — GIẢ ĐỊNH ĐẦU TƯ (Investment Assumptions)

### A-01: Momentum có hiệu quả trên HOSE

| Field | Value |
|:---|:---|
| **Mô tả** | Cổ phiếu tăng giá trong 12M-1M tiếp tục outperform trong 1-3 tháng tới |
| **Liên quan** | F3.1 (Price Momentum), CSS weighting 35% khi Bull Trending |
| **Tại sao tồn tại** | Momentum là factor được chứng minh rộng rãi trên thị trường phát triển (Jegadeesh & Titman 1993) |
| **Rủi ro nếu sai** | 35% trọng số CSS đang dùng một factor vô nghĩa trên HOSE. Hệ thống mua đỉnh, bán đáy |
| **Severity** | HIGH |
| **Validate bằng** | Backtest IC của F3.1 trên HOSE 2014-2024, phân theo regime |
| **Data cần** | OHLCV daily 10 năm, đã adjusted corporate action |
| **Pass threshold** | IC(F3.1) > 0.05 với p-value < 0.05 trong ít nhất 60% rolling 12M windows |

---

### A-02: Quality factors (ROIC, F-Score) outperform trên HOSE

| Field | Value |
|:---|:---|
| **Mô tả** | Doanh nghiệp có ROIC cao và F-Score cao sẽ outperform thị trường |
| **Liên quan** | F2.1, F2.4, Nhóm 2 — trọng số 25-35% trong CSS |
| **Tại sao tồn tại** | Piotroski (2000) chứng minh trên thị trường Mỹ. Warren Buffett ủng hộ |
| **Rủi ro nếu sai** | HOSE có tỷ lệ NĐT cá nhân 85%, có thể không phản ứng với quality signal đủ nhanh để tạo alpha |
| **Severity** | HIGH |
| **Validate bằng** | Quintile return analysis: top 20% ROIC vs bottom 20% ROIC, rolling annual return |
| **Data cần** | BCTC 10 năm + OHLCV |
| **Pass threshold** | Top quintile ROIC outperform bottom quintile ≥ 5% annualized, consistent qua ít nhất 7/10 năm |

---

### A-03: Beneish M-Score > -1.78 là dấu hiệu gian lận kế toán đáng tin cậy trên HOSE

| Field | Value |
|:---|:---|
| **Mô tả** | Ngưỡng -1.78 được calibrate trên data Mỹ từ 1987-1993. HOSE có đặc thù kế toán Việt Nam |
| **Liên quan** | M03 — Gate lọc trước khi vào pipeline |
| **Rủi ro nếu sai** | Loại bỏ oan cổ phiếu tốt (False Positive) HOẶC bỏ qua gian lận thực sự (False Negative) |
| **Severity** | MEDIUM (False Positive làm giảm Universe, False Negative gây rủi ro cụ thể) |
| **Validate bằng** | Tính M-Score cho các DN đã bị SSC xử phạt gian lận BCTC giai đoạn 2010-2024. Kiểm tra precision/recall |
| **Data cần** | Danh sách DN bị SSC xử phạt + BCTC tương ứng |
| **Pass threshold** | Recall ≥ 70% (bắt được ≥ 70% gian lận thực sự). Nếu không đạt → calibrate lại ngưỡng |

---

### A-04: Quarter Kelly (1/4 Kelly) là mức sizing phù hợp cho HOSE

| Field | Value |
|:---|:---|
| **Mô tả** | Kelly Formula yêu cầu win_rate và payoff ratio chính xác. Dùng 1/4 để bảo thủ |
| **Liên quan** | M11 — toàn bộ position sizing |
| **Rủi ro nếu sai** | Nếu win_rate ước lượng sai → Kelly sizing sai → có thể over-bet hoặc under-bet đều tệ |
| **Severity** | HIGH |
| **Validate bằng** | So sánh Kelly-sized portfolio vs Equal-weighted portfolio trên historical trades. Track max drawdown |
| **Data cần** | Minimum 100 completed trades với entry/exit price và rationale |
| **Pass threshold** | Max drawdown của Kelly portfolio < 1.5× max drawdown của Equal-weighted trong cùng period |

---

### A-05: Correlation trung bình giữa các vị thế < 0.5 đủ để diversify

| Field | Value |
|:---|:---|
| **Mô tả** | Danh mục 12-18 cổ phiếu với avg pairwise correlation < 0.5 đủ để giảm rủi ro idiosyncratic |
| **Liên quan** | M12 — Portfolio Optimizer constraint |
| **Rủi ro nếu sai** | HOSE có correlation tăng mạnh trong market stress (tất cả cùng giảm). Đa dạng hóa sụp đổ đúng lúc cần nhất |
| **Severity** | MEDIUM |
| **Validate bằng** | Tính correlation matrix của HOSE trong các giai đoạn stress (COVID tháng 3/2020, tháng 11/2022). So sánh với normal period |
| **Pass threshold** | Trong stress period, avg correlation < 0.75 (nếu > 0.75, constraint 0.5 vô nghĩa thực tế) |

---

### A-06: Stop-loss 2% NAV per position là đủ bảo vệ nhưng không quá chật

| Field | Value |
|:---|:---|
| **Mô tả** | Hard stop tại mức tổn thất 2% NAV mỗi vị thế |
| **Liên quan** | M13, Điều 1 — Hard Law |
| **Rủi ro nếu sai** | Nếu stop-loss quá chật so với volatility HOSE → bị stopped out quá thường xuyên, chi phí giao dịch ăn hết alpha |
| **Severity** | MEDIUM |
| **Validate bằng** | Tính ATR (Average True Range) của Universe. So sánh với implied stop-loss distance từ sizing |
| **Data cần** | OHLCV 5 năm |
| **Pass threshold** | Stop-loss distance > 1.5× ATR(20) của cổ phiếu trong ít nhất 70% cases |

---

## NHÓM B — GIẢ ĐỊNH THỐNG KÊ (Statistical Assumptions)

### B-01: HMM với 3 observable variables đủ để phân loại 4 regime

| Field | Value |
|:---|:---|
| **Mô tả** | VN-Index vs MA50 + AD Ratio + Volume Trend đủ để HMM học ra 4 regime riêng biệt |
| **Liên quan** | M09 — toàn bộ Macro Regime classification |
| **Rủi ro nếu sai** | Regime labels không ổn định → CSS weighting theo regime sai → toàn bộ Decision Layer bị ảnh hưởng |
| **Severity** | HIGH |
| **Validate bằng** | Train HMM, visualize regime labels trên chart lịch sử. Kiểm tra xem Bear Trending có match với các đợt giảm lịch sử (2018, 2020, 2022) không |
| **Pass threshold** | ≥ 85% ngày trong crash period (VN-Index -20% từ đỉnh) được label là Bear Trending |

---

### B-02: GARCH(1,1) mô phỏng tốt volatility của VN-Index

| Field | Value |
|:---|:---|
| **Mô tả** | VIX_VN_analog dùng GARCH(1,1) để forecast daily volatility |
| **Liên quan** | M10 — Cash allocation |
| **Rủi ro nếu sai** | VIX_VN_analog không phản ánh đúng volatility thực tế → Cash holding sai lúc cần thiết nhất |
| **Severity** | MEDIUM |
| **Validate bằng** | So sánh GARCH(1,1) forecast vs realized volatility out-of-sample. Thử EGARCH, GJR-GARCH |
| **Pass threshold** | MAPE của GARCH(1,1) volatility forecast < 20% so với realized volatility |

---

### B-03: Ledoit-Wolf Shrinkage ổn định IC-weighted scores đủ để dùng sau 100 trades

| Field | Value |
|:---|:---|
| **Mô tả** | Sau 100 trades, chuyển từ Equal Weight sang IC-weighted với Ledoit-Wolf |
| **Liên quan** | M07 — Scoring Engine |
| **Rủi ro nếu sai** | 100 trades có thể không đủ để IC estimate ổn định, ngay cả với shrinkage |
| **Severity** | LOW |
| **Validate bằng** | Walk-forward test: so sánh portfolio performance trước và sau khi chuyển sang IC-weighted tại ngưỡng 100 trades |
| **Pass threshold** | IC-weighted không làm tăng tracking error > 20% so với equal-weighted trong giai đoạn đầu sau chuyển đổi |

---

### B-04: Historical Simulation với rolling 500 phiên đủ để ước lượng ES 97.5%

| Field | Value |
|:---|:---|
| **Mô tả** | ES 97.5% dùng 500 phiên gần nhất (khoảng 2 năm data) |
| **Liên quan** | M13 — Risk Engine |
| **Rủi ro nếu sai** | 500 phiên có thể không bao gồm đủ tail events của HOSE. ES bị underestimate |
| **Severity** | HIGH |
| **Validate bằng** | Backtesting ES: đếm xem thực tế có bao nhiêu ngày loss vượt ES. Nên < 2.5% số ngày |
| **Pass threshold** | Breach rate ≤ 3% (cho phép tolerance nhỏ). Nếu > 5% → tăng window hoặc dùng EVT |

---

## NHÓM C — GIẢ ĐỊNH THỊ TRƯỜNG (Market Structure Assumptions)

### C-01: ADTV20 (loại ATC) là proxy tốt cho thanh khoản thực tế có thể thực thi

| Field | Value |
|:---|:---|
| **Mô tả** | Hệ thống giả định có thể thực thi lệnh với tổng slippage có thể dự đoán dựa trên ADTV20 |
| **Liên quan** | M01, M11, M16 |
| **Rủi ro nếu sai** | Trong stress period, spread tăng mạnh và ADTV20 không còn ý nghĩa |
| **Severity** | MEDIUM |
| **Validate bằng** | Tính slippage thực tế của các lệnh theo ADTV20 bucket. So sánh normal vs stress period |
| **Pass threshold** | Slippage < 0.5% trong normal period khi order size < 10% ADTV20 |

---

### C-02: VN30F có đủ thanh khoản để hedge quy mô danh mục mục tiêu

| Field | Value |
|:---|:---|
| **Mô tả** | Khi cần short VN30F để hedge 80% danh mục, thị trường phái sinh đủ depth |
| **Liên quan** | M14 — VN30F Hedge |
| **Rủi ro nếu sai** | Đúng lúc cần hedge (thị trường panic), VN30F cũng thiếu thanh khoản → không hedge được |
| **Severity** | HIGH |
| **Validate bằng** | Kiểm tra open interest và volume VN30F trong các đợt sell-off lịch sử |
| **Pass threshold** | Volume VN30F trong stress period ≥ 5,000 contracts/ngày (đủ để build position tương đương 20 tỷ VND) |

---

### C-03: Dữ liệu ngôn ngữ trong báo cáo thường niên là đủ trung thực để Moat AI dùng

| Field | Value |
|:---|:---|
| **Mô tả** | Moat AI đọc annual report, IR docs để chấm điểm lợi thế cạnh tranh |
| **Liên quan** | M08 — Moat AI Engine |
| **Rủi ro nếu sai** | Nhiều DN Việt Nam viết annual report theo template, nội dung PR. AI bị hallucinate moat không có thật |
| **Severity** | MEDIUM |
| **Validate bằng** | Lấy sample 20 DN, so sánh Moat Score của AI với đánh giá analyst thực tế. Tính correlation |
| **Pass threshold** | Correlation Moat Score AI vs analyst consensus ≥ 0.60 |

---

### C-04: Google Trends SVI đồng biến với doanh thu thực tế của DN

| Field | Value |
|:---|:---|
| **Mô tả** | Tăng SVI → tăng nhu cầu → tăng doanh thu (F6.1 assumption) |
| **Liên quan** | M05, F6.1 |
| **Rủi ro nếu sai** | SVI tăng do scandal, sự kiện tiêu cực → signal sai chiều |
| **Severity** | LOW (đã có polarity_score để filter một phần) |
| **Validate bằng** | Lag analysis: SVI tháng T vs doanh thu quý T và T+1. Tính correlation |
| **Pass threshold** | Correlation(SVI_t, Revenue_t+1) > 0.30 trên sample ≥ 10 DN, ≥ 3 năm data |

---

## NHÓM D — GIẢ ĐỊNH DỮ LIỆU (Data Assumptions)

### D-01: Data BCTC từ VietStock/CafeF đủ sạch để tính factors

| Field | Value |
|:---|:---|
| **Mô tả** | Hệ thống phụ thuộc vào dữ liệu BCTC được scrape/mua từ vendor |
| **Rủi ro nếu sai** | Số liệu sai → factor scores sai → mọi thứ phía sau sai |
| **Severity** | HIGH |
| **Validate bằng** | Spot check 50 DN ngẫu nhiên: so sánh data từ vendor với BCTC gốc trên SSC |
| **Pass threshold** | Error rate < 2% trên các field quan trọng (revenue, net_income, cfo, total_assets) |

---

### D-02: Announcement date có thể được xác định chính xác để tránh look-ahead bias

| Field | Value |
|:---|:---|
| **Mô tả** | Hệ thống chỉ dùng data sau announcement_date để tính signal |
| **Rủi ro nếu sai** | Nếu announcement_date sai → backtest bị look-ahead bias → kết quả backtest quá đẹp nhưng live trading tệ |
| **Severity** | HIGH |
| **Validate bằng** | Kiểm tra announcement_date database bằng cách cross-reference với news archive (CafeF, VNDIRECT) |
| **Pass threshold** | 100% announcement_date phải ≤ ngày signal đầu tiên được dùng trong backtest |

---

## BẢNG TÓM TẮT RỦI RO

| ID | Giả định | Severity | Validate được chưa | Priority |
|:---|:---|:---|:---|:---|
| A-01 | Momentum hiệu quả trên HOSE | HIGH | ❌ | 1 |
| A-02 | Quality factors outperform | HIGH | ❌ | 1 |
| B-01 | HMM phân loại regime chính xác | HIGH | ❌ | 1 |
| B-04 | ES 97.5% calibrate đúng | HIGH | ❌ | 1 |
| C-02 | VN30F đủ thanh khoản để hedge | HIGH | ❌ | 1 |
| D-01 | Data BCTC đủ sạch | HIGH | ❌ | 1 |
| D-02 | Announcement date chính xác | HIGH | ❌ | 1 |
| A-04 | Quarter Kelly phù hợp | HIGH | ❌ | 2 |
| A-06 | Stop-loss 2% NAV không quá chật | MEDIUM | ❌ | 2 |
| A-03 | Beneish calibrate đúng HOSE | MEDIUM | ❌ | 2 |
| B-02 | GARCH(1,1) đủ tốt | MEDIUM | ❌ | 2 |
| C-01 | ADTV20 proxy cho execution | MEDIUM | ❌ | 2 |
| C-03 | Moat AI không hallucinate | MEDIUM | ❌ | 3 |
| A-05 | Correlation < 0.5 đủ diversify | MEDIUM | ❌ | 3 |
| B-03 | 100 trades đủ cho IC-weighted | LOW | ❌ | 4 |
| C-04 | Google Trends đồng biến doanh thu | LOW | ❌ | 4 |

> **Kết luận:** Toàn bộ 16 giả định chưa được validate trên HOSE data. Đây là lý do tại sao không deploy production trước khi chạy Validation Plan.