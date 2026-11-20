# Thuật Toán & Mô Hình Định Lượng Tài Chính (Algorithms & Financial Models)

> **Tài liệu chuẩn kỹ thuật**: AIInvest Quantitative & SAG v2 Stock Research Architecture Specification  
> **Phiên bản**: 2.1 (Chặng 2 Phase 1 Synchronized)  
> **Phạm vi**: Định nghĩa toán học, cơ chế hoạt động mã nguồn, điều kiện biên, đầu vào/đầu ra cho cả lõi định lượng `ai-engine` (12 Agents) và lõi nghiên cứu cổ phiếu `SAG v2` (Pha 1 Research Chat; chưa nối vào 12 Agents theo Rule 06).

---

## Mục Lục
1. [Triết Lý & Bản Đồ Thuật Toán Hệ Thống](#1-triết-lý--bản-đồ-thuật-toán-hệ-thống)
2. [Mô Hình Beneish M-Score (Phát Hiện Gian Lận BCTC)](#2-mô-hình-beneish-m-score-phát-hiện-gian-lận-bctc)
3. [Chỉ Số Sloan Accruals & Bất Thường Dòng Tiền (Le & Tran 2022)](#3-chỉ-số-sloan-accruals--bất-thường-dòng-tiền-le--tran-2022)
4. [Toán Học Rủi Ro Thanh Toán HOSE T+2.5 & Hard Law Điều 1](#4-toán-học-rủi-ro-thanh-toán-hose-t25--hard-law-điều-1)
5. [Quy Mô Vị Thế Tối Ưu Half/Quarter-Kelly Theo Market Regime](#5-quy-mô-vị-thế-tối-ưu-halfquarter-kelly-theo-market-regime)
6. [Hạn Mức Thanh Khoản Sức Chứa (ADTV20 & Hard Law Điều 2, 4)](#6-hạn-mức-thanh-khoản-sức-chứa-adtv20--hard-law-điều-2-4)
7. [Cơ Chế Tranh Luận Biện Chứng 12 Quant Agents & Chuỗi Băm Bất Biến](#7-cơ-chế-tranh-luận-biện-chứng-12-quant-agents--chuỗi-băm-bất-biến)
8. [Xác Thực Bằng Chứng Mù, `quote_hash` Post-Verifier & 3-Layer Abstention (`A7`)](#8-xác-thực-bằng-chứng-mù-quote_hash-post-verifier--3-layer-abstention-a7)
9. [SAG v2 Core Adaptations (`P1..P5`, `A1..A6`) & HOSE Input Layer (`S01..S13`)](#9-sag-v2-core-adaptations-p1p5-a1a6--hose-input-layer-s01s13)
10. [Dual-Representation Financial Table Engine, Sandboxed Calculator & Discrepancy Detector](#10-dual-representation-financial-table-engine-sandboxed-calculator--discrepancy-detector)
11. [Đồ Thị Tri Thức 3 Tầng Zero-Hardcode & Cổng Kiểm Nghiệm Pha 1 (`G1..G5`)](#11-đồ-thị-tri-thức-3-tầng-zero-hardcode--cổng-kiểm-nghiệm-pha-1-g1g5)
12. [Tóm Tắt Ma Trận Đầu Vào / Đầu Ra & Điều Kiện Kỹ Thuật](#12-tóm-tắt-ma-trận-đầu-vào--đầu-ra--điều-kiện-kỹ-thuật)

---

## 1. Triết Lý & Bản Đồ Thuật Toán Hệ Thống

AIInvest không sử dụng các mô hình "hộp đen" (black-box) thiếu kiểm soát rủi ro. Mọi quyết định đầu tư đều trải qua chu trình phòng thủ đa tầng:

```
[BCTC PDF thô] ──▶ MinerU OCR (Markdown Table) ──▶ quote_hash (SHA-256 Verification)
                                                              │
┌─────────────────────────────────────────────────────────────┘
▼
[Lớp 0: Hard Accounting Gate]
  ├── Beneish M-Score (M8 / M5 Adaptive) ──▶ M > -1.78 ? LAYER-0 HARD VETO
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
  $$\text{Nếu } M > -1.78 \implies \text{Kết luận: BCTC có dấu hiệu thao túng (Manipulator), kích hoạt LAYER-0 HARD VETO, cấm tuyệt đối giải ngân.}$$
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

## 8. Xác Thực Bằng Chứng Mù, `quote_hash` Post-Verifier & 3-Layer Abstention (`A7`)

*File nguồn*: [`SAG/apps/api/sag_api/parsing/canonical.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/parsing/canonical.py), [`sag_api/services/knowledge_quality_service.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/services/knowledge_quality_service.py), [`sag_api/services/retrieval_service.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/services/retrieval_service.py), [`sag_api/research/quote_verifier.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/research/quote_verifier.py)

### 8.1. Mục đích
Triệt tiêu ảo giác (Hallucination) và rò rỉ thông tin tương lai (Look-ahead Bias) trong **SAG v2 Research Chat (Pha 1)**. Mọi trích dẫn từ Báo cáo Thường niên, Thuyết minh BCTC kiểm toán và NQ ĐHĐCĐ đều phải neo ngược được tới `canonical_blocks` theo đúng số trang PDF gốc (`<!-- PAGE N -->`) và mã băm SHA-256 chuẩn hóa.

### 8.2. Chuẩn hóa dấu vân tay & Post-Synthesis Verification (`quote_verifier.py`)
1. **Chuẩn hóa Unicode NFC & khoảng trắng**:
   $$\text{normalize\_ws}(s) = \text{collapse\_spaces}(\text{NFC}(s)).\text{strip}()$$
2. **Dấu vân tay SHA-256**:
   $$\text{quote\_hash} = \text{SHA256}(\text{normalize\_ws}(\text{quote}))$$
3. **Cổng kiểm định hậu tổng hợp (Fail-Closed)**: Với mọi citation trong phản hồi LLM:
   - Kiểm tra $\text{SHA256}(\text{normalize\_ws}(\text{quote})) == \text{quote\_hash}$;
   - Kiểm tra `normalize_ws(quote)` là chuỗi con chính xác (`exact substring`) trong `CanonicalBlock.normalized_text` của đúng `document_version_id`, thỏa `page_from <= page <= page_to` và `sub_doc_slot`;
   - Kiểm tra Point-in-Time: `published_date <= as_of_date`.
   - Bất kỳ trích dẫn nào trượt sẽ bị gán `verified = False`, loại khỏi `verified_citations`, gắn tag cảnh báo `[C# UNVERIFIED_CLAIM]` trong câu trả lời và đẩy blindspot `UNVERIFIED_CITATION` / `OUT_OF_PIT_WINDOW` vào `declared_blindspots[]`.

### 8.3. Chấm điểm chất lượng tri thức (`A7`) & 3-Layer Abstention
- **Phân rã độ tươi theo hộc (`half_life_days`)**: Thay thế hằng số `90.0` ngày cố định bằng chu kỳ bán rã riêng theo loại tài liệu (`365` ngày cho `S01..S11` năm, `180` ngày cho `S12` AGM, `90` ngày cho `S13` quý đột biến):
  $$\text{freshness}(u) = 2^{-\frac{\Delta t_{\text{days}}(u)}{\text{half\_life}(u)}}$$
- **Chất lượng 5 thành phần (`KnowledgeQualityService`)**: Đọc trực tiếp batch metrics (không bị chặn bởi `eligible=False` khi `query_demand=None`), kết hợp `0.30 coverage + 0.15 source_diversity + 0.15 freshness + 0.25 contradiction + 0.15 uncertainty`, cộng tín hiệu từ `DiscrepancyDetector`.
- **3-Layer Abstention -> `declared_blindspots[]`**:
  1. *Layer 1 (Evidence Pack Sufficiency)*: Kiểm tra độ phủ anchor & ngưỡng điểm trước khi gọi LLM (`NO_DOC_COVERAGE`).
  2. *Layer 2 (System Prompt Guard)*: Ép LLM trả `INSUFFICIENT_EVIDENCE` cho từng khía cạnh thiếu dữ liệu.
  3. *Layer 3 (Post-Generation Guard)*: `_is_abstention_response` phát hiện cả cụm từ từ chối tiếng Anh và tiếng Việt (`"không đủ bằng chứng"`, `"không đủ dữ liệu"`, `"không đủ cơ sở"`, `"không đủ căn cứ"`) khi thiếu citation `[n]`, phân loại `status = "no_answer"` hoặc đẩy phần thiếu vào `declared_blindspots[]` (`PARTIAL_MISSING_DATA`).

---

## 9. SAG v2 Core Adaptations (`P1..P5`, `A1..A6`) & HOSE Input Layer (`S01..S13`)

*File nguồn*: [`SAG/apps/api/sag_api/research/input_{selector,inspector,builder}.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/research/input_selector.py), [`sag_api/services/{document_service,search_index_service,knowledge_service,routing_tree_service,query_analysis,query_routing_service,search_unit_retrieval_service,retrieval_service}.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/services/search_index_service.py), [`sag_api/parsing/{service,mineru_official,canonical}.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/parsing/service.py)

### 9.1. HOSE Input Selection, Triage & 11+2 Logical Sub-Documents (`0` LLM)
- **Bộ chọn 3 Cốt lõi + 2 Bổ trợ + 4 Loại bỏ**: Lấy Core DB 5 năm (`0đ`), `annual_report` (4 phần `S01..S04`), `CONSOLIDATED` audited BCTC notes (7 phần `S05..S11`, fallback `SEPARATE` chỉ cho mã đơn lẻ không công ty con), khử trùng nếu BCTN đã nhúng trọn BCTC & kiểm toán; kích hoạt có điều kiện `agm_resolution` (`S12`) và `LATEST_QUARTER` (`S13` khi đột biến giữa năm); loại bỏ `SEPARATE` holding, `governance_report`, `analyst_report`, `news`.
- **Cổng kiểm định đầu vào (`InputInspector`)**:
  - Giải nén `.zip` an toàn (chặn Zip-Slip path traversal & giới hạn byte giải nén), multi-source 404 fallback, chuẩn hóa `effective_published_at` (`announcement_date -> signing_date -> filing_date -> published_date`, fail-closed `MissingPublishedDateError`).
  - Chặn `NOTICE_ONLY` (`pages < 15`) và `BAD_FONT` (`vn_readability_score < 0.72` với `text_len > 200`); phát hiện sidebar lặp & trang đôi; định tuyến **4 Nhánh (A/B/C/D)** (Digital `0đ` vs 2-pass TOC-First MinerU `language="vi"`).
  - **Fail-closed (`P1..P3`)**: Cấm fallback im lặng sang `MarkItDown` trên PDF scan khi MinerU lỗi; chèn neo trang gốc `<!-- PAGE N -->` trước mỗi trang để bảo toàn `page_from`, `page_to`.

### 9.2. Core RAG Adaptations (`P4`, `P5`, `A1..A6`)
- **`P4` — VN-Dot BM25 Normalization (`search_index_service.py` & `search_unit_store.py`)**: Chuẩn hóa dấu chấm hàng nghìn tiếng Việt (`16.012.813 -> 16012813`) trước khi tách token BM25 (`k_1=1.5, b=0.75, avg_doc_len=256.0`), đồng thời bảo toàn số thập phân (`15.29%`) và mã phân cấp (`1.2.3`) bằng lookbehind/lookahead:
  $$\text{re.sub}\big(\texttt{r"(?<!\textbackslash d\textbackslash.)(?<=\textbackslash d)\textbackslash.(?=\textbackslash d\{3\}(?:\textbackslash.\textbackslash d\{3\})*(?!\textbackslash d))"},\; \texttt{""},\; \text{nfc\_text.lower}()\big)$$
- **`P5` — Bi-Temporal PIT & Qdrant Payload Indexes**: Đặt `DocumentVersion.valid_from = source_published_at` (thay vì `now_utc`); lập chỉ mục `PAYLOAD_INDEX_FIELDS` (`valid_from_ts`, `fiscal_year`, `fiscal_quarter`, `doc_type`, `sub_doc_slot`, `sector_group`, `table_id`), lọc cứng `valid_from_ts <= as_of_ts`, mở rộng `research_search_top_k = 10` (`8..12`).
- **`A1` — PIT-Safe HippoRAG 2 Cross-Slot Multi-Hop**: Trong `_expand_multi_hop_sections`, mọi bước nhảy giữa `S01..S13` (Lineage, DenseGraph, Bridge Entity, Bounded 2-Hop Artifact Code) bắt buộc qua bộ lọc `target_ku.valid_from_ts <= as_of_ts`.
- **`A2` — Leiden Routing Tree Tuning**: `min_cluster_size=2`, `target_cluster_size=6`, `max_cluster_size=20`, `leiden_resolution=0.08`, kèm PIT node mask trên cây Blue-Green (`SLOT_A/B`).
- **`A3` — VN Accounting Artifact Bridge (`0` LLM)**: Chuẩn hóa `Thuyết minh/TM/Note/Mục {num}` -> `TM-{num}`, nhận diện `B0x-DN/HN`, `TT334`, `TT200`, gỡ chặn `"note"` khi đi kèm số, và nạp từ điển ~120 thuật ngữ tài chính Việt viết thường.
- **`A4` — DenseGraph `temporal` & `citation` Calibration**: Giữ trọng số 6 tín hiệu ($\alpha = \{0.40, 0.20, 0.15, 0.10, 0.10, 0.05\}$, ngưỡng giữ cạnh $w \ge 0.55$, `max_degree=32`, `max_same_doc_degree=16`), đặt sàn `temporal >= 0.50`, thưởng `1.0` cho liên kết `S03 (T-1) <-> S01/S03 (T)` & `S13 (T) <-> S03/S12 (T)`, và `citation = 0.85` khi hai hộc khác nhau chia sẻ mã `TM-{num}` / `B0x-DN/HN`.
- **`A5` — Table-Safe Jaccard MMR**: Khóa shingle theo `(fiscal_year, fiscal_quarter, sub_doc_slot, table_id)` và chỉ cho phép gộp ở `duplicate_threshold = 0.98` khi ma trận số chuẩn hóa đồng nhất, bảo vệ bảng so sánh đa kỳ.
- **`A6` — `QueryAnalysis (vi)`**: Giữ `"mới nhất"`, `"gần đây"` thành `recency_intent = True`; bắt kỳ báo cáo Việt (`9T/2026`, `Quý 3/2026`, `FY2025`) và mã `Thuyết minh 12`, `B09-DN/HN`.

---

## 10. Dual-Representation Financial Table Engine, Sandboxed Calculator & Discrepancy Detector

*File nguồn*: [`SAG/apps/api/sag_api/research/table_engine.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/research/table_engine.py), [`calculator.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/research/calculator.py), [`discrepancy_detector.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/research/discrepancy_detector.py)

### 10.1. Đường ống Bảng Biểu Diễn Kép (`0` LLM)
1. **Hybrid Matrix Extraction & Unmerge**: Nhánh Digital dùng `pdfplumber` `lattice` với tự động phục hồi tọa độ (`stream` / bounding-box reconstructor) chống rơi cột phải; nhánh Scan giải nén DOM HTML `<table>` với `rowspan`/`colspan` thành lưới 2D và `col_path` phân cấp.
2. **Cross-Page Stitching**: Hợp nhất hai bảng liên tiếp $T_{k-1}, T_k$ khi `page_start(T_k) == page_end(T_{k-1}) + 1`, phần xen giữa là nhãn `"(Tiếp theo)"` / header lặp lại, và chữ ký cột tương thích.
3. **Unit/Currency Binding & Locale Detection**: Quét ngược tiêu đề/góc trên phải để khóa `(currency, unit_multiplier)` (`tỷ` $10^9$, `triệu` $10^6$, `nghìn` $10^3$, `VND` $1$, tách biệt `USD`); suy luận quy ước dấu chấm/phẩy cấp toàn bảng (`detect_table_locale`) và xử lý số âm ngoặc kế toán `(56.500) -> -56500.0`.
4. **Arithmetic & Hierarchical Sanity Gate (`<= 0.5%`, Fail-Closed)**:
   $$\varepsilon = \frac{\left|\sum_{r \in \text{sub-rows}} v_{r,c} - v_{\text{total},c}\right|}{\max(|v_{\text{total},c}|,\, 1)} \le 0.005$$
   Kiểm tra cả tổng dọc và các hằng đẳng thức P&L phân cấp; nếu trượt sau nhánh OCR -> gán `verification_status = "TABLE_UNVERIFIED"` (cấm nội suy tính toán).
5. **Dual Representation & Full-Table Hydration**: Lưu ma trận 2D chuẩn hóa vào PostgreSQL (`sag_extracted_tables` với `table_hash` SHA-256 & PIT `valid_from`) và phát sinh `Row-Linearized Search Cards` vào Qdrant (`table_id` payload). Khi retrieval online khớp card, hệ thống hydrate toàn bộ bảng 2D gốc vào ngữ cảnh.

### 10.2. Sandboxed Python Financial Calculator (`Zero Mental Math`)
- Phân tích biểu thức bằng `ast.parse(..., mode="eval")` chỉ trên tập node và hàm whitelist an toàn (`abs`, `min`, `max`, `round`, `sum`, `safe_div`, `cagr`, `yoy`, `pct_change`, `margin`, `roe`, `roa`, `ev_ebitda`, `bps_diff`, `debt_to_equity`, `casa_ratio`, `npl_ratio`) — **tuyệt đối không dùng `eval()`/`exec()`**.
- **Fail-closed guards**: Ném `UnsafeFormulaError` nếu AST ngoài whitelist; ném `UnverifiedTableInputError` nếu đầu vào đến từ bảng `TABLE_UNVERIFIED`; ném `CurrencyMismatchError` nếu trộn tiền tệ (`VND` vs `USD`); tự động đồng bộ `unit_multiplier` khác nhau; xuất `CalculatorTrace` (`calc_id`, `formula`, `inputs` có provenance, `result`, `formatted`).

### 10.3. Deterministic Quant-vs-Narrative Discrepancy Detector (`0` LLM)
- Đối chất tất định giữa narrative claims (`S01..S03`, thư PR) với `sag_extracted_tables` trạng thái `VERIFIED` (`S06..S10`) và `FinancialTrendSnapshot` (Core DB 5Y), bỏ qua hoàn toàn bảng `TABLE_UNVERIFIED`, xuất `DiscrepancyAlert` có `severity` (`HIGH | MEDIUM`), `delta_pct` và `citations`.

---

## 11. Đồ Thị Tri Thức 3 Tầng Zero-Hardcode & Cổng Kiểm Nghiệm Pha 1 (`G1..G5`)

*File nguồn*: [`SAG/apps/api/sag_api/research/kg_{entity_linker,macro_coupling,true_peer,store}.py`](file:///d:/AIInvest/SAG/apps/api/sag_api/research/kg_macro_coupling.py), [`back-end/src/services/sagResearchChat.service.ts`](file:///d:/AIInvest/back-end/src/services/sagResearchChat.service.ts)

### 11.1. Ba Động Cơ Xây Đồ Thị Tự Động (0 Hardcoded Maps)
1. **Engine 1 — `Document Entity-Linker` (`kg_entity_linker.py`)**: Xây `DynamicIssuerRegistry` trực tiếp từ Core DB `stocks` + `instrument_master` (khử trùng tên ngắn/slogan ngân hàng); trích xuất cạnh vi mô có provenance (`OWNS_SUBSIDIARY`, `ASSOCIATE_OF`, `SUPPLIER_OF`, `CUSTOMER_OF`, `CREDITOR_OF`, `COMPETES_WITH`) với `published_date <= as_of_date`, và tổng hợp bottom-up thành cạnh liên ngành theo $\sqrt{|S_u| \cdot |S_v|}$.
2. **Engine 2 — `Quantitative Macro & Cross-Sector Coupling` (`kg_macro_coupling.py`, thuần `numpy`/`pandas`/`math`)**:
   - **Fundamental Channel**: Resample về lưới lịch quý đầy đủ `QE` trước khi `.diff(1)` / `.shift(lag)` ($\ell \in \{0, 1, 2\}$) để không méo độ trễ khi thiếu quý.
   - **Market Excess Channel**: Hồi quy OLS trên excess return tuần/tháng với hiệp phương sai **Newey-West Bartlett HAC** (`maxlags=4` tuần) và tính chính xác two-sided Student-$t$ $p$-value qua hàm beta bất toàn chuẩn hóa $I_x(a, b)$ (`regularized_incomplete_beta`).
   - **Chống tương quan giả**: Hiệu chỉnh đa kiểm định **Benjamini-Hochberg FDR** trên toàn bộ không gian `(metric × indicator × lag)` + kiểm định **Non-Overlapping Tercile Sign Stability** (trả `0.0` fail-closed nếu $w \ge n$, kèm half-sample regime-flip guard).
   - **Audited Cross-Check**: Đối chiếu định lượng với thuyết minh `S10`/`S04` -> phân hạng `CONFIRMED_BOTH`, `QUANT_DOC_NEUTRAL`, `CONFLICT_FLAG`, `QUANT_ONLY`, `DOC_ONLY`.
3. **Engine 3 — `True-Peer & Thematic Leiden Clustering` (`kg_true_peer.py`)**:
   - Loại bỏ tiền tố/hậu tố pháp lý tiếng Việt (`strip_corporate_boilerplate`), nhúng bằng `Qwen/Qwen3-Embedding-0.6B` (`1024d`, khử dị hướng bằng corpus mean-centering; fallback TF-IDF nội bộ thuần NumPy khi offline), ghép với `MacroBeta` và ma trận chuẩn hóa Z-score cấu trúc tài chính `FinStruct`:
     $$\operatorname{Sim}_{\text{peer}}(i, j) = \tilde{w}_{\text{biz}} \cos(\mathbf{e}^{\text{biz}}_i, \mathbf{e}^{\text{biz}}_j) + \tilde{w}_{\text{macro}} \cos(\boldsymbol{\beta}_i, \boldsymbol{\beta}_j) + \tilde{w}_{\text{fin}} \exp\!\left(-\frac{\|\mathbf{f}_i - \mathbf{f}_j\|_2}{\sqrt{d_f}}\right)$$
     *(trọng số gốc `0.45 / 0.30 / 0.25`, tái chuẩn hóa động theo tập đặc trưng khả dụng).*
   - Dựng đồ thị **Mutual k-NN** (`k=6`, `sim_threshold=0.45`, `strong_threshold=0.60`, `min_biz_sim=0.25`, `weight_power=2.5`) và chạy `leidenalg.RBConfigurationVertexPartition` (`resolution=2.5`).
4. **Lưu trữ & Truy vấn PIT (`kg_store.py`)**: Mọi truy vấn trên `kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots` khóa cứng `effective_from <= as_of_date < valid_to` và fail-closed nếu thiếu `effective_from`.

### 11.2. Cổng Kiểm Nghiệm Định Lượng Pha 1 -> Pha 2 (`GET /api/v1/ai/research-chat/eval-summary`)
Theo Rule 06 thu hẹp, Pha 2 (nối Read-Only vào Agent 04/05 và `/research`) **chỉ được mở** khi vượt qua `>= 40` phiên trên 8 mã Pilot đạt đồng thời 5 chỉ số cổng đo từ `chat_messages.metadata` và `sag_chat_eval_feedback`:
- **G1 (`g1CitationVerifiedRate >= 0.98`)**: Tỷ lệ trích dẫn vượt qua `quote_hash` Post-Verifier.
- **G2 (`g2TableCalcVerifiedRate >= 0.98`)**: Tỷ lệ câu trả lời có bảng `VERIFIED` & `CalculatorTrace` khớp PDF gốc (`0` sai đơn vị tỷ/triệu).
- **G3 (`g3AbstentionComplianceRate == 1.00`)**: Tỷ lệ tuân thủ từ chối/khai báo `declared_blindspots[]` khi thiếu chứng cứ.
- **G4 (`g4PitZeroLeakCount == 0`)**: Số vụ rò rỉ `published_date > as_of_date` hoặc `effective_from > as_of_date` (bắt buộc `0%`).
- **G5 (`g5HumanPassRate >= 0.90`)**: Tỷ lệ phán quyết `PASS` từ chủ dự án trên UI `/research-chat`.

---

## 12. Tóm Tắt Ma Trận Đầu Vào / Đầu Ra & Điều Kiện Kỹ Thuật

| Thành phần thuật toán | Đầu vào chính | Đầu ra chính | Điều kiện tiên quyết & Giới hạn |
|---|---|---|---|
| **Beneish M-Score** (`ai-engine`) | Doanh thu, Phải thu, Giá vốn, Khấu hao, Chi phí QLDN, Lợi nhuận, CFO, Tổng tài sản qua 2 kỳ liên tiếp | Điểm $M$, Phân loại: MANIPULATOR / CLEAN, Layer-0 Veto flag | Miễn trừ nhóm tài chính; $M > -1.78$ loại ngay lập tức. |
| **Sloan Accruals** (`ai-engine`) | Lợi nhuận sau thuế, CFO từ bảng LCTT, Tổng tài sản | Tỷ lệ dồn tích chuẩn hóa $\text{Accrual Ratio}$, Điểm chất lượng lợi nhuận | $\text{Total Assets} > 0$; CFO âm kéo dài bị phạt tỷ trọng. |
| **HOSE T+2.5 Downside** (`ai-engine`) | Giá đặt mua, Stop-loss danh nghĩa, NAV quỹ | Rủi ro tổn thất tuyệt đối (VND), Phán quyết Đạt/Không đạt | Tối thiểu chịu 2 phiên sàn liên tiếp ($13.51\%$); tổn thất $\le 2\%$ NAV. |
| **Quarter-Kelly Sizer** (`ai-engine`) | Xác suất thắng $W$, Tỷ lệ Win/Loss $R$, Trạng thái HMM thị trường | Khối lượng cổ phiếu đặt mua tối ưu (VND) | $W > 0, R > 0$; Tỷ trọng tối đa $15\%$ NAV của một mã. |
| **Liquidity Gate** (`ai-engine`) | Khối lượng lệnh, Khối lượng vị thế hiện tại, $\text{ADTV}_{20}$ | Chấp thuận lệnh / Yêu cầu chia nhỏ lệnh | Lệnh $\le 15\%$ $\text{ADTV}_{20}$; Vị thế $\le 25\%$ $\text{ADTV}_{20}$. |
| **Dialectic Debate** (`ai-engine`) | Luận điểm Bull từ Thesis Agent, Báo cáo rủi ro Bear từ Counter-Thesis | Phán quyết CIO cuối cùng, Tỷ trọng phân bổ, Chuỗi băm lưu trữ | Ghi đè bất biến vào PostgreSQL; liên kết chéo Audit Trail (`SAG_CLOSED`). |
| **HOSE Input & Dual-Rep Table Engine** (`SAG v2`) | R2 PDFs + Core DB 5Y (`S01..S13`), HTML/lattice tables | `sag_extracted_tables` (2D), `Row-Linearized Search Cards`, `CanonicalBlock` | `NOTICE_ONLY < 15p`, `BAD_FONT < 0.72`, `sanity <= 0.5%` (`TABLE_UNVERIFIED` fail-closed). |
| **Sandboxed Calculator & Discrepancy** (`SAG v2`) | Bảng `VERIFIED`, Core DB 5Y, Narrative `S01..S03`, Biểu thức tài chính | `CalculatorTrace`, `DiscrepancyAlert` (`0 LLM`) | Cấm `eval()`, cấm trộn tiền tệ, cấm dùng bảng `TABLE_UNVERIFIED`. |
| **Zero-Hardcode 3-Layer KG** (`SAG v2`) | `stocks`, `macro_indicators`, `financial_*`, `S01..S10` | `kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots` (`CONFIRMED_BOTH`) | Pure NumPy/Pandas HAC + BH-FDR + Tercile stability; Mutual k-NN Leiden; PIT `effective_from <= as_of_date < valid_to`. |
| **`quote_hash` Post-Verifier & `A7`** (`SAG v2`) | Draft LLM synthesis, `canonical_blocks`, `document_versions` | `verified_citations[]`, `declared_blindspots[]`, `UNVERIFIED_CLAIM` | Khớp `SHA-256(normalize_ws(quote))`, exact substring, page/slot & PIT `<= as_of_date`. |
