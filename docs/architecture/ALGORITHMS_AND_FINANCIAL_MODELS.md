# Thuật Toán & Mô Hình Định Lượng Tài Chính (Algorithms & Financial Models)

> **Tài liệu chuẩn kỹ thuật**: AIInvest Quantitative & Forensic Architecture Specification  
> **Phiên bản**: 2.0 (Production Grounded)  
> **Phạm vi**: Định nghĩa toán học, cơ chế hoạt động mã nguồn, điều kiện biên, đầu vào/đầu ra cho các kỹ sư hệ thống và chuyên gia định lượng (Quant).

---

## Mục Lục
1. [Triết Lý & Bản Đồ Thuật Toán Hệ Thống](#1-triết-lý--bản-đồ-thuật-toán-hệ-thống)
2. [Mô Hình Beneish M-Score (Phát Hiện Gian Lận BCTC)](#2-mô-hình-beneish-m-score-phát-hiện-gian-lận-bctc)
3. [Chỉ Số Sloan Accruals & Bất Thường Dòng Tiền (Le & Tran 2022)](#3-chỉ-số-sloan-accruals--bất-thường-dòng-tiền-le--tran-2022)
4. [Toán Học Rủi Ro Thanh Toán HOSE T+2.5 & Hard Law Điều 1](#4-toán-học-rủi-ro-thanh-toán-hose-t25--hard-law-điều-1)
5. [Quy Mô Vị Thế Tối Ưu Half/Quarter-Kelly Theo Market Regime](#5-quy-mô-vị-thế-tối-ưu-halfquarter-kelly-theo-market-regime)
6. [Hạn Mức Thanh Khoản Sức Chứa (ADTV20 & Hard Law Điều 2, 4)](#6-hạn-mức-thanh-khoản-sức-chứa-adtv20--hard-law-điều-2-4)
7. [Cơ Chế Tranh Luận Biện Chứng 12 Quant Agents & Chuỗi Băm Bất Biến](#7-cơ-chế-tranh-luận-biện-chứng-12-quant-agents--chuỗi-băm-bất-biến)
8. [Xác Thực Bằng Chứng Mù & Dấu Vân Tay Dòng (quote_hash SHA-256)](#8-xác-thực-bằng-chứng-mù--dấu-vân-tay-dòng-quote_hash-sha-256)

---

## 1. Triết Lý & Bản Đồ Thuật Toán Hệ Thống

AIInvest không sử dụng các mô hình "hộp đen" (black-box) thiếu kiểm soát rủi ro. Mọi quyết định đầu tư đều trải qua chu trình phòng thủ đa tầng:

```
[BCTC PDF thô] ──▶ MinerU OCR (Markdown Table) ──▶ quote_hash (SHA-256 Verification)
                                                              │
┌─────────────────────────────────────────────────────────────┘
▼
[Lớp 0: Hard Forensic Gate]
  ├── Beneish M-Score (M8 / M5 Adaptive) ──▶ M > -1.78 ? VETO GIL
  └── Sloan Accruals Anomaly Ratio       ──▶ Accrual < -0.10 ? Flag Quality Penalty
        │ (Passed)
        ▼
[Lớp 1: 12 Quant Agents Dialectic Debate]
  ├── Bull Thesis (Fundamental, Growth, Valuation DCF)
  ├── Bear Counter-Thesis (Margin Erosion, Governance Risk)
  └── Strategy CIO Synthesis ──▶ Cryptographic Decision Hash (Immutable Audit)
        │ (Buy Signal Approved)
        ▼
[Lớp 2: Dual-Book Risk & HOSE T+2.5 Execution]
  ├── T+2.5 Floor Gap Downside Math: max(StopLoss, 13.51%) <= 2% NAV
  ├── Quarter-Kelly Sizer * HMM Regime Multiplier (0.25x - 1.0x)
  └── Liquidity Hard Laws: Order <= 15% ADTV20, Position <= 25% ADTV20
        │ (All Passed)
        ▼
[Lệnh Mua Được Ký Số Dispatch Tới Gateway]
```

---

## 2. Mô Hình Beneish M-Score (Phát Hiện Gian Lận BCTC)

*File nguồn*: [`ai-engine/app/domain/rules/beneish.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/beneish.py)

### 2.1. Mục đích
Phát hiện sớm các doanh nghiệp "nấu nướng" sổ sách, ghi nhận doanh thu ảo, đẩy chi phí ra tương lai hoặc che giấu nợ xấu trước khi tin tức tiêu cực vỡ lở trên sàn chứng khoán.

### 2.2. Công thức toán học

AIInvest triển khai kiến trúc **Dual-Engine Adaptive Beneish** (Messod Beneish, 1999):

#### A. Mô hình chuẩn 8 biến (Full 8-Variable Model)
Áp dụng khi BCTC kiểm toán năm hoặc soát xét có đầy đủ thuyết minh chi tiết về khấu hao và lưu chuyển tiền tệ:

$$M_8 = -4.84 + 0.920 \cdot \text{DSRI} + 0.528 \cdot \text{GMI} + 0.404 \cdot \text{AQI} + 0.892 \cdot \text{SGI} + 0.115 \cdot \text{DEPI} - 0.172 \cdot \text{SGAI} + 4.037 \cdot \text{TATA} + 0.0327 \cdot \text{LVGI}$$

#### B. Mô hình thích ứng 5 biến (Adaptive 5-Variable Non-Cash-Flow Model)
Áp dụng khi BCTC quý tóm tắt không có dòng Khấu hao riêng biệt trong bảng Lưu chuyển tiền tệ. Mô hình sử dụng 100% số liệu thực tế, **tuyệt đối không điền số 0 giả**:

$$M_5 = -6.065 + 0.823 \cdot \text{DSRI} + 0.906 \cdot \text{GMI} + 0.593 \cdot \text{AQI} + 0.717 \cdot \text{SGI} + 0.107 \cdot \text{LVGI}$$

### 2.3. Chi tiết 8 chỉ số thành phần
| Ký hiệu | Tên chỉ số | Công thức chi tiết | Ý nghĩa tài chính |
|---|---|---|---|
| **DSRI** | Days Sales in Receivables Index | $\frac{\text{Receivables}_t / \text{Sales}_t}{\text{Receivables}_{t-1} / \text{Sales}_{t-1}}$ | Đo lường tỷ lệ phải thu / doanh thu. DSRI > 1 cho thấy nợ đọng tăng vọt, nguy cơ ghi nhận doanh thu trước. |
| **GMI** | Gross Margin Index | $\frac{(\text{Sales}_{t-1} - \text{COGS}_{t-1})/\text{Sales}_{t-1}}{(\text{Sales}_t - \text{COGS}_t)/\text{Sales}_t}$ | GMI > 1 cho thấy biên lãi gộp suy giảm, áp lực làm đẹp số liệu lợi nhuận. |
| **AQI** | Asset Quality Index | $\frac{1 - (\text{CA}_t + \text{PPE}_t)/\text{TA}_t}{1 - (\text{CA}_{t-1} + \text{PPE}_{t-1})/\text{TA}_{t-1}}$ | Tỷ lệ tài sản phi hiện vật (chi phí trả trước dài hạn, lợi thế thương mại). AQI > 1 chỉ ra việc tư bản hóa chi phí. |
| **SGI** | Sales Growth Index | $\frac{\text{Sales}_t}{\text{Sales}_{t-1}}$ | Tăng trưởng doanh thu. Tăng trưởng quá nóng dễ đi kèm với gian lận để duy trì kỳ vọng thị trường. |
| **DEPI** | Depreciation Index | $\frac{\text{DeprRate}_{t-1}}{\text{DeprRate}_t}$ với $\text{DeprRate} = \frac{\text{Depr}}{\text{PPE} + \text{Depr}}$ | DEPI > 1 biểu thị tốc độ trích khấu hao chậm lại để "thổi phồng" lợi nhuận kỳ hiện tại. |
| **SGAI** | SG&A Index | $\frac{\text{SGA}_t / \text{Sales}_t}{\text{SGA}_{t-1} / \text{Sales}_{t-1}}$ | Chi phí bán hàng & quản lý doanh nghiệp trên doanh thu. |
| **LVGI** | Leverage Index | $\frac{(\text{LTD}_t + \text{CL}_t)/\text{TA}_t}{(\text{LTD}_{t-1} + \text{CL}_{t-1})/\text{TA}_{t-1}}$ | Đòn bẩy nợ vay. LVGI > 1 biểu thị rủi ro tài chính gia tăng vi phạm cam kết nợ. |
| **TATA** | Total Accruals to Total Assets | $\frac{\text{Net Income from Ops}_t - \text{CFO}_t}{\text{Total Assets}_t}$ | Chênh lệch giữa lợi nhuận kế toán và dòng tiền thuần từ HĐKD. TATA cao = chất lượng lợi nhuận thấp. |

### 2.4. Điều kiện biên & Ngưỡng kích hoạt
- **Ngưỡng loại bỏ (Hard Law FAIL Gate)**:
  $$\text{Nếu } M > -1.78 \implies \text{Kết luận: BCTC có dấu hiệu thao túng (Manipulator), kích hoạt GIL VETO, cấm tuyệt đối giải ngân.}$$
  $$\text{Nếu } M \le -1.78 \implies \text{Kết luận: BCTC lành mạnh (Non-manipulator), thông qua vòng kiểm soát Lớp 0.}$$
- **Miễn trừ ngành đặc thù**:
  - Nhóm Ngân hàng, Bảo hiểm, Chứng khoán, Bất động sản (`EXCLUDED_SECTORS`) được miễn trừ Beneish cổ điển do cấu trúc bảng cân đối kế toán mang đặc thù đòn bẩy huy động và ghi nhận doanh thu theo tiến độ bàn giao dự án.

---

## 3. Chỉ Số Sloan Accruals & Bất Thường Dòng Tiền (Le & Tran 2022)

*File nguồn*: [`ai-engine/app/infrastructure/vendors/vn/factor_scores.py`](file:///d:/AIInvest/ai-engine/app/infrastructure/vendors/vn/factor_scores.py) (dòng 660-664)

### 3.1. Mục đích
Đo lường "chất lượng" của lợi nhuận. Nghiên cứu kinh điển của Sloan (1996) và nghiên cứu thực nghiệm tại thị trường Việt Nam của **Le & Tran (2022)** chỉ ra rằng: *Hiện tượng bất thường dồn tích (Accrual Anomaly) tại TTCK Việt Nam có sức dự báo đảo chiều giá mạnh hơn cả thị trường Mỹ*. Doanh nghiệp có lợi nhuận cao nhưng dòng tiền từ hoạt động kinh doanh (CFO) âm liên tục sẽ sụt giảm giá mạnh sau đó.

### 3.2. Công thức toán học
$$\text{Accrual Ratio} = - \frac{\text{Net Income} - \text{CFO}}{\text{Total Assets}}$$

Trong đó:
- $\text{Net Income}$: Lợi nhuận sau thuế của cổ đông công ty mẹ.
- $\text{CFO}$: Dòng tiền thuần từ hoạt động kinh doanh (Cash Flow from Operations).
- $\text{Total Assets}$: Tổng tài sản bình quân kỳ báo cáo.

Dấu âm $(-)$ được chuẩn hóa để giá trị càng cao thể hiện chất lượng lợi nhuận càng tốt ($\text{CFO} > \text{Net Income}$, tiền thực về tài khoản lớn hơn lãi sổ sách).

### 3.3. Tỷ lệ chuyển đổi tiền tệ bổ trợ (CFO-to-NI)
$$\text{CFO\_TO\_NI} = \frac{\text{CFO}}{|\text{Net Income}|}$$
- $\text{CFO\_TO\_NI} \ge 1.0$: Doanh nghiệp thu tiền mặt vượt lợi nhuận kế toán (xuất sắc).
- $\text{CFO\_TO\_NI} < 0.5$: Báo động đỏ về quản trị vốn lưu động (Working Capital Drain).

---

## 4. Toán Học Rủi Ro Thanh Toán HOSE T+2.5 & Hard Law Điều 1

*File nguồn*: [`ai-engine/app/domain/rules/hard_laws.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/hard_laws.py)

### 4.1. Bản chất thị trường HOSE (Việt Nam)
Tại sàn HOSE:
1. Biên độ dao động tối đa trong một phiên là $\pm 7\%$.
2. Chu kỳ thanh toán là $T+2.5$ (Cổ phiếu mua phiên $T+0$ chỉ có thể bán từ phiên chiều ngày $T+2$).
3. **Rủi ro kẹt thanh khoản (Liquidity Lock Trap)**: Nếu thị trường sập hoặc cổ phiếu có thông tin tiêu cực, giá sẽ chạm sàn "trắng bên mua". Nhà đầu tư buộc phải gánh chịu tối thiểu 2 phiên sàn liên tiếp trước khi hàng về tài khoản để cắt lỗ.

### 4.2. Công thức tính mức tổn thất đuôi tối thiểu (Tail Downside)
Mức sụt giảm sau 2 phiên sàn liên tiếp:
$$\text{Two-Floor Drop} = 1 - (1 - 0.07)^2 = 1 - 0.93^2 = 1 - 0.8649 = 0.1351 \quad (13.51\%)$$

Công thức xác định rủi ro thực tế có hiệu lực ($\text{Effective Downside \%}$):
$$\text{Effective Downside \%} = \max\left(\frac{\text{Order Price} - \text{Stop Loss Price}}{\text{Order Price}}, \; 13.51\%\right)$$

### 4.3. Luật Tồn Tại (Hard Law Điều 1 - Max Loss per Order)
Mọi lệnh Mua ($\text{BUY}$) bắt buộc phải có mức cắt lỗ danh nghĩa, nhưng quy mô rủi ro được thẩm định theo mức sụt giảm hiệu lực tối thiểu $13.51\%$:

$$\text{Risk Amount} = (\text{Order Price} \times \text{Effective Downside \%}) \times \text{Quantity}$$
$$\text{Điều kiện cho phép}: \quad \text{Risk Amount} \le 2\% \times \text{NAV}$$

> **Ý nghĩa kỹ thuật**: Nếu một kỹ sư định cấu hình lệnh mua cổ phiếu với Stop-Loss nông $3\%$ nhằm tăng đòn bẩy kích thước lệnh, Hard Law Điều 1 sẽ ngay lập tức ghi đè $13.51\%$ vào mẫu số. Điều này đảm bảo rằng ngay cả khi thị trường "cháy tài khoản" trong 2 ngày không thể bán, tổng NAV của quỹ chỉ giảm tối đa $2\%$.

---

## 5. Quy Mô Vị Thế Tối Ưu Half/Quarter-Kelly Theo Market Regime

*File nguồn*: [`ai-engine/app/domain/rules/kelly_sizer.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/kelly_sizer.py)

### 5.1. Mục đích
Xác định tỷ trọng vốn tối ưu cho mỗi mã cổ phiếu nhằm tối đa hóa tốc độ tăng trưởng vốn hình học (Geometric Growth Rate), đồng thời triệt tiêu rủi ro cháy tài khoản (Gambler's Ruin).

### 5.2. Công thức Kelly gốc
$$\text{Kelly}^* = W - \frac{1 - W}{R}$$
Trong đó:
- $W$: Xác suất thắng lịch sử (Win Probability), rút ra từ kiểm thử mô hình 60 phiên gần nhất ($0 < W < 1$).
- $R$: Tỷ lệ lãi bình quân trên lỗ bình quân (Win/Loss Ratio = $\frac{\text{Avg Win}}{\text{Avg Loss}}$).

Nếu $\text{Kelly}^* \le 0 \implies$ Vị thế được gán bằng $0$ (Không giải ngân).

### 5.3. Điều chỉnh phân số an toàn & Chế độ thị trường (Regime Multiplier)
Trong đầu tư thực tế, Full-Kelly ($1.0 \times \text{Kelly}$) gây biến động tài sản quá lớn (Drawdown sâu). Hệ thống quy định:
- **Baseline**: Áp dụng **Quarter Kelly** ($f_{\text{baseline}} = 0.25$).
- **Market Regime Multiplier ($M_{\text{regime}}$)** từ mô hình HMM (Hidden Markov Model):

| Chế độ thị trường (HMM Regime) | Hệ số nhân ($M_{\text{regime}}$) | Ý nghĩa vận hành |
|---|---|---|
| **BULL_TRENDING** | $1.00\times$ | Xu hướng tăng mạnh, thanh khoản dồi dào: Giữ nguyên Quarter Kelly. |
| **BULL_CHOPPY** | $0.75\times$ | Tăng trong biên độ hẹp, dòng tiền giằng co: Giảm $25\%$ quy mô. |
| **BEAR_BOUNCE** | $0.50\times$ | Sóng hồi kỹ thuật trong xu hướng giảm: Giảm $50\%$ quy mô. |
| **BEAR_TRENDING** | $0.25\times$ | Xu hướng sụt giảm toàn diện: Giảm $75\%$ quy mô (hoặc chuyển sang One-Eighth Kelly $1/8$). |

### 5.4. Công thức chốt kích thước vị thế
$$\text{Target Weight} = \min\left(\text{Kelly}^* \times f_{\text{baseline}} \times M_{\text{regime}}, \; 15\%\right)$$
$$\text{Target Capital (VND)} = \text{Target Weight} \times \text{NAV}$$

Mức trần $15\%$ là giới hạn cứng của Luật Tập Trung (Hard Law Điều 4).

---

## 6. Hạn Mức Thanh Khoản Sức Chứa (ADTV20 & Hard Law Điều 2, 4)

*File nguồn*: [`ai-engine/app/domain/rules/hard_laws.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/hard_laws.py) (dòng 71-125)

### 6.1. Hạn mức đơn lệnh & Vị thế theo ADTV20 (Average Daily Trading Volume 20 Sessions)
- **Hạn chế trượt giá lệnh đơn (Single Order Constraint)**:
  $$\text{Order Quantity} \le 15\% \times \text{ADTV}_{20}$$
  *Mục đích*: Tránh tạo áp lực cung/cầu đột biến làm xê dịch đường giá khớp liên tục trên bảng điện HOSE.
- **Hạn chế rủi ro kẹt hàng tổng vị thế (Cumulative Position Capacity)**:
  $$\text{Total Position Quantity} \le 25\% \times \text{ADTV}_{20}$$
  *Mục đích*: Đảm bảo khi cần thanh lý khẩn cấp toàn bộ vị thế, hệ thống có thể xả hàng xong trong vòng 1.5 đến 2 phiên mà không làm sập giá sàn của cổ phiếu.

### 6.2. Luật Tập Trung Danh Mục (Hard Law Điều 4)
- **Giới hạn cổ phiếu đơn lẻ**:
  $$\frac{\text{Position Value}}{\text{NAV}} \le 15\%$$
- **Giới hạn ngành kinh tế (Sector Exposure Limit)**:
  $$\frac{\sum_{\text{ticker} \in \text{Sector}} \text{Position Value}}{\text{NAV}} \le 35\%$$
  *Mục đích*: Đảm bảo danh mục không bị phụ thuộc thái quá vào chu kỳ của một nhóm ngành duy nhất (ví dụ: Ngân hàng hoặc Bất động sản).

---

## 7. Cơ Chế Tranh Luận Biện Chứng 12 Quant Agents & Chuỗi Băm Bất Biến

*File nguồn*: [`ai-engine/app/domain/agents/strategy_cio.py`](file:///d:/AIInvest/ai-engine/app/domain/agents/strategy_cio.py), [`counter_thesis.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/counter_thesis.py)

### 7.1. Sơ đồ tương tác biện chứng (Hegelian Dialectic)
```
       ┌────────────────────────┐
       │   Thesis Agent (Bull)  │ ── 1. Đề xuất Luận điểm Mua & Định giá
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │ Counter-Thesis (Bear)  │ ── 2. Tấn công luận điểm (Stress-test rủi ro)
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │ Strategy CIO Arbitrage │ ── 3. Tổng hợp mâu thuẫn & Ban hành phán quyết
       └───────────┬────────────┘
                   │
                   ▼
       ┌────────────────────────┐
       │   cio_resolutions DB   │ ── 4. Neo chuỗi băm SHA-256 bất biến
       └────────────────────────┘
```

### 7.2. Chuỗi băm bất biến (Cryptographic Audit Trail)
Mỗi phán quyết của `StrategyCIOAgent` không thể bị ghi đè hoặc chỉnh sửa hồi tố (Tamper-proof Ledger):

$$\text{Decision Hash}_k = \text{SHA256}\left(\text{JSON\_Canonical}(\text{Verdict Payload}) + \text{Decision Hash}_{k-1}\right)$$

Cột `decision_hash` được lưu tuần tự vào bảng PostgreSQL `cio_resolutions`. Bất kỳ sự sửa đổi nào trong lịch sử đều làm đứt gãy chuỗi băm, lập tức bị `SystemGovernanceAgent` phát hiện và cô lập.

---

## 8. Xác Thực Bằng Chứng Mù & Dấu Vân Tay Dòng (quote_hash SHA-256)

*File nguồn*: [`ai-engine/app/infrastructure/external/sag_connector.py`](file:///d:/AIInvest/ai-engine/app/infrastructure/external/sag_connector.py)

### 8.1. Mục đích
Loại bỏ hoàn toàn hiện tượng AI ảo giác (Hallucination) trong trích xuất BCTC. Các Agent không được phép tự "sáng tạo" ra số liệu tài chính để đưa vào mô hình định giá.

### 8.2. Thuật toán tạo dấu vân tay dòng
Khi MinerU OCR phân tích một tệp BCTC PDF:
1. Chuẩn hóa chuỗi văn bản của dòng dữ liệu (loại bỏ ký tự xuống dòng, khoảng trắng thừa, viết thường):
   $$S = \text{Normalize}(\text{Line Text})$$
2. Kết hợp với số trang và tọa độ ô bảng trong tài liệu gốc:
   $$\text{Seed} = S + \text{"\_P"} + \text{PageNumber} + \text{"\_C"} + \text{CellCoord}$$
3. Tính toán mã băm SHA-256:
   $$\text{quote\_hash} = \text{SHA256}(\text{Seed})$$

### 8.3. Quy tắc kiểm tra tính toàn vẹn (Integrity Gate)
Khi một Quant Agent (ví dụ: `FundamentalAgent` hoặc `ValuationAgent`) trích xuất chỉ số Lợi nhuận gộp hay Nợ vay:
- Yêu cầu gửi kèm `quote_hash`.
- `SAGConnector` đối chiếu `quote_hash` với cơ sở dữ liệu `sag_evidence_lines`.
- **Nếu băm không khớp**: Từ chối thông tin, hạ điểm tín nhiệm của luận điểm về 0 và gắn cờ cảnh báo rủi ro dữ liệu sai lệch.

---

## 9. Tóm Tắt Ma Trận Đầu Vào / Đầu Ra & Điều Kiện Kỹ Thuật

| Thành phần thuật toán | Đầu vào chính | Đầu ra chính | Điều kiện tiên quyết & Giới hạn |
|---|---|---|---|
| **Beneish M-Score** | Doanh thu, Phải thu, Giá vốn, Khấu hao, Chi phí QLDN, Lợi nhuận, CFO, Tổng tài sản qua 2 kỳ liên tiếp | Điểm $M$, Phân loại: MANIPULATOR / CLEAN, GIL Veto flag | Miễn trừ nhóm tài chính; $M > -1.78$ loại ngay lập tức. |
| **Sloan Accruals** | Lợi nhuận sau thuế, CFO từ bảng LCTT, Tổng tài sản | Tỷ lệ dồn tích chuẩn hóa $\text{Accrual Ratio}$, Điểm chất lượng lợi nhuận | $\text{Total Assets} > 0$; CFO âm kéo dài bị phạt tỷ trọng. |
| **HOSE T+2.5 Downside** | Giá đặt mua, Stop-loss danh nghĩa, NAV quỹ | Rủi ro tổn thất tuyệt đối (VND), Phán quyết Đạt/Không đạt | Tối thiểu chịu 2 phiên sàn liên tiếp ($13.51\%$); tổn thất $\le 2\%$ NAV. |
| **Quarter-Kelly Sizer** | Xác suất thắng $W$, Tỷ lệ Win/Loss $R$, Trạng thái HMM thị trường | Khối lượng cổ phiếu đặt mua tối ưu (VND) | $W > 0, R > 0$; Tỷ trọng tối đa $15\%$ NAV của một mã. |
| **Liquidity Gate** | Khối lượng lệnh, Khối lượng vị thế hiện tại, $\text{ADTV}_{20}$ | Chấp thuận lệnh / Yêu cầu chia nhỏ lệnh | Lệnh $\le 15\%$ $\text{ADTV}_{20}$; Vị thế $\le 25\%$ $\text{ADTV}_{20}$. |
| **Dialectic Debate** | Luận điểm Bull từ Thesis Agent, Báo cáo rủi ro Bear từ Counter-Thesis | Phán quyết CIO cuối cùng, Tỷ trọng phân bổ, Chuỗi băm lưu trữ | Ghi đè bất biến vào PostgreSQL; liên kết chéo Audit Trail. |
| **quote_hash Verifier** | Dòng văn bản số liệu trích xuất, Số trang, Tọa độ bảng | Boolean (Hợp lệ / Ảo giác) | Trùng khớp 100% SHA-256 từ cơ sở dữ liệu MinerU OCR. |
