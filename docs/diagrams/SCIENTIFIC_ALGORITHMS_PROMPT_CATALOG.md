# Master Scientific Diagram Prompt Catalog: AIInvest `ai-engine` Algorithms
> **Codified via `/scientific-diagram-prompt-crafter` Meta-Skill (v4.0.0)**  
> **Academic Standard**: NeurIPS / ICLR / SIGMOD Publication Grade  
> **Visual Mandate**: Fresh Light Pastel Palette (Strictly NO black item backgrounds), Dynamic Flow Opacity, Orthogonal Connectors ($r=8$), Interactive Item-Click Inspector Drawer.

---

## 1. Bản Đồ Tổng Quan Các Trục Giải Thuật (Algorithmic Pillars)

Dựa trên quá trình điều tra gốc mã nguồn (Root Research) toàn diện tại [ai-engine](file:///d:/AIInvest/ai-engine), hệ thống thuật toán của AIInvest được quy hoạch thành 6 trục toán học định lượng cốt lõi và 1 bản thiết kế vĩ mô hợp nhất:

| Trục Giải Thuật | File Mã Nguồn Gốc | Bản Chất Toán Học & Biên Kỹ Thuật |
|---|---|---|
| **Pillar 1: Vi phân Phân số & Vi cấu trúc** | [`frac_diff.py`](file:///d:/AIInvest/ai-engine/app/domain/services/ml/frac_diff.py), [`feature_forge.py`](file:///d:/AIInvest/ai-engine/app/domain/services/ml/feature_forge.py) | Cửa sổ cố định FFD $w_k = -w_{k-1} \frac{d-k+1}{k}$, $|w_k| \ge 10^{-5}$, ADF $p < 0.05$ tìm $d^*$, biến động Garman-Klass $\sigma_{\text{GK}}^2$, thanh khoản Amihud $\lambda_{\text{Amihud}}$. |
| **Pillar 2: Lan truyền Cú sốc Đồ thị & Tập đoàn** | [`graph_contagion_engine.py`](file:///d:/AIInvest/ai-engine/app/domain/services/ml/graph_contagion_engine.py) | Đồ thị định hướng 15 ngành (406 mã HOSE) & 9 Tập đoàn; Xung lực $\text{Shock}_{\text{lead}} = R \ln(1 + \max(0, \text{VR}))$; Thế năng bắt kịp 3 ngày; Bùng nổ khối lượng ngành $\text{VR} \ge 1.5$; Trễ Shift(1, 2). |
| **Pillar 3: Trung hòa Ngành, Trực giao & IC** | [`sector_neutralizer.py`](file:///d:/AIInvest/ai-engine/app/domain/services/quant/sector_neutralizer.py), [`factor_orthogonalization.py`](file:///d:/AIInvest/ai-engine/app/domain/services/quant/factor_orthogonalization.py), [`vn_ic_tester.py`](file:///d:/AIInvest/ai-engine/app/domain/services/quant/vn_ic_tester.py) | Nhận diện phân phối (nhị phân, rời rạc, liên tục); Winsorize $[q_{0.01}, q_{0.99}]$; Z-Score phòng thủ $N \ge 3$; Trực giao hóa Gram-Schmidt $\mathbf{u}_k = \mathbf{v}_k - \sum \text{proj}$; Lọc GTGD $\ge 5$ tỷ VND/ngày; Rank IC & T-stat. |
| **Pillar 4: Giám định Thao túng Beneish M-Score** | [`beneish.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/beneish.py) | Động cơ kép thích ứng: $M_8$ đầy đủ vs $M_5$ phi dòng tiền; Ngưỡng loại $M > -1.78$; Miễn trừ Ngân hàng, BĐS, Chứng khoán, Bảo hiểm; Chuẩn hóa key theo VAS Thông tư 200/BTC. |
| **Pillar 5: Hard Laws, Cắt lỗ Đa tầng & Kelly** | [`hard_laws.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/hard_laws.py), [`stop_loss.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/stop_loss.py), [`kelly_sizer.py`](file:///d:/AIInvest/ai-engine/app/domain/rules/kelly_sizer.py) | Điều 1 (Rủi ro 2% NAV, trần sàn T+2.5 $13.51\%$); Điều 2 (Lệnh $\le 15\%$, Vị thế $\le 25\%$ ADTV20); Điều 4 (Cổ phiếu $\le 15\%$, Ngành $\le 35\%$ NAV); Thang 6 bậc cắt lỗ T0-T5; Quarter Kelly $f^*$ scaling theo Regime. |
| **Pillar 6: BCTC AST Parsing & Dual-Store Ingestion** | [`bctc_to_sag_pipeline.py`](file:///d:/AIInvest/ai-engine/app/domain/pipeline/bctc_to_sag_pipeline.py), [`document_selector.py`](file:///d:/AIInvest/ai-engine/app/domain/services/document_selector.py) | Tuyển chọn Bộ 3 Tài liệu Vàng; MinerU OCR giải mã cấu trúc bảng AST; Content hash $\text{SHA-256}(\text{line})$; Chiếu vector 1024-d & Siêu đồ thị $M:N$; Nén hàng đợi ngữ cảnh $32 \to 24 \to 8$. |
| **Pillar 7: Bản Thiết Kế Vĩ Mô Hợp Nhất** | Full Pipeline | Kiến trúc tổng thể định lượng khép kín từ nạp thô đa phương thức -> Luyện tạo đặc trưng & Lan truyền đồ thị -> Trực giao hóa -> Thể chế Hard Laws & Beneish -> Định cỡ Kelly & Thực thi sàn HOSE. |

---

## 2. Kho Bách Khoa Toàn Thư 60+ Công Thức Tài Chính & Định Lượng Trong Hệ Thống

Toàn bộ hệ sinh thái `ai-engine` và `SAG` vận hành dựa trên **hơn 60 công thức toán học và tài chính vi mô**, phân bố trên 6 miền tri thức:

### Miền A: Kế Toán Tài Chính, Chất Lượng Doanh Nghiệp & Cảnh Báo Phá Sản (`factor_scores.py`, `beneish.py`)
1. **Altman Z'-Score cho Thị trường Mới nổi (Emerging Markets Z')**:
   $$Z' = 6.56 \frac{\text{Working Capital}}{\text{Total Assets}} + 3.26 \frac{\text{Retained Earnings}}{\text{Total Assets}} + 6.72 \frac{\text{EBIT}}{\text{Total Assets}} + 1.05 \frac{\text{Book Value Equity}}{\text{Total Liabilities}}$$
   - Vùng kiệt quệ (Distress): $Z' < 1.10$, Vùng xám (Grey): $1.10 \le Z' \le 2.60$, Vùng an toàn (Safe): $Z' > 2.60$.
2. **Piotroski F-Score Đầy Đủ 9 Điểm (Fundamental Quality 9/9)**:
   $$F = F_{\text{ROA}} + F_{\text{CFO}} + F_{\Delta \text{ROA}} + F_{\text{Accrual}} + F_{\Delta \text{Leverage}} + F_{\Delta \text{Liquidity}} + F_{\text{EquityOffer}} + F_{\Delta \text{GrossMargin}} + F_{\Delta \text{Turnover}}$$
   - $F_{\text{ROA}} = \mathbb{I}(\text{ROA}_t > 0)$
   - $F_{\text{CFO}} = \mathbb{I}(\text{CFO}_t > 0)$
   - $F_{\Delta \text{ROA}} = \mathbb{I}(\text{ROA}_t > \text{ROA}_{t-4})$
   - $F_{\text{Accrual}} = \mathbb{I}(\text{CFO}_t > \text{NI}_t)$
   - $F_{\Delta \text{Leverage}} = \mathbb{I}\left(\frac{\text{LongTermDebt}_t}{\text{Assets}_t} < \frac{\text{LongTermDebt}_{t-4}}{\text{Assets}_{t-4}}\right)$
   - $F_{\Delta \text{Liquidity}} = \mathbb{I}\left(\frac{\text{CurrentAssets}_t}{\text{CurrentLiabilities}_t} > \frac{\text{CurrentAssets}_{t-4}}{\text{CurrentLiabilities}_{t-4}}\right)$
   - $F_{\text{EquityOffer}} = \mathbb{I}(\text{Equity}_t \le 1.02 \times \text{Equity}_{t-4})$
   - $F_{\Delta \text{GrossMargin}} = \mathbb{I}\left(\frac{\text{Rev}_t - \text{COGS}_t}{\text{Rev}_t} > \frac{\text{Rev}_{t-4} - \text{COGS}_{t-4}}{\text{Rev}_{t-4}}\right)$
   - $F_{\Delta \text{Turnover}} = \mathbb{I}\left(\frac{\text{Rev}_t}{\text{Assets}_t} > \frac{\text{Rev}_{t-4}}{\text{Assets}_{t-4}}\right)$
3. **Chỉ Số Dồn Tích Sloan (Sloan Accrual Anomaly - Le & Tran 2022 VN)**:
   $$\text{Accrual Ratio} = -\frac{\text{Net Income} - \text{CFO}}{\text{Total Assets}}$$
4. **Hệ Số Chuyển Hóa Tiền Mặt (Cash Conversion Quality)**:
   $$\text{CFO\_TO\_NI} = \frac{\text{CFO}}{|\text{Net Income}|}$$
5. **Mô Hình Beneish M-Score 8 Biến Đầy Đủ (Full M8)**:
   $$M_8 = -4.84 + 0.920\text{DSRI} + 0.528\text{GMI} + 0.404\text{AQI} + 0.892\text{SGI} + 0.115\text{DEPI} - 0.172\text{SGAI} + 4.037\text{TATA} + 0.0327\text{LVGI}$$
6. **Mô Hình Beneish M-Score 5 Biến Thích Ứng (Adaptive Non-Cash M5)**:
   $$M_5 = -6.065 + 0.823\text{DSRI} + 0.906\text{GMI} + 0.593\text{AQI} + 0.717\text{SGI} + 0.107\text{LVGI}$$
7. **8 Chỉ Số Thành Phần Beneish (VAS TT 200/BTC)**:
   - $\text{DSRI} = \frac{\text{Rec}_t / \text{Rev}_t}{\text{Rec}_{t-1} / \text{Rev}_{t-1}}$, $\text{GMI} = \frac{\text{Margin}_{t-1}}{\text{Margin}_t}$, $\text{AQI} = \frac{1 - (\text{CA}_t + \text{PPE}_t)/\text{TA}_t}{1 - (\text{CA}_{t-1} + \text{PPE}_{t-1})/\text{TA}_{t-1}}$
   - $\text{SGI} = \frac{\text{Rev}_t}{\text{Rev}_{t-1}}$, $\text{DEPI} = \frac{\text{DepRate}_{t-1}}{\text{DepRate}_t}$, $\text{SGAI} = \frac{\text{SGA}_t / \text{Rev}_t}{\text{SGA}_{t-1} / \text{Rev}_{t-1}}$
   - $\text{LVGI} = \frac{\text{Debt}_t / \text{TA}_t}{\text{Debt}_{t-1} / \text{TA}_{t-1}}$, $\text{TATA} = \frac{\text{NetIncome}_t - \text{CFO}_t}{\text{TA}_t}$
8. **Tỷ Lệ Thu Nhập Lãi Thuần (NIM - Net Interest Margin)**:
   $$\text{NIM} = \frac{\text{Interest Income} - \text{Interest Expense}}{\text{Total Assets}}$$
9. **Vòng Quay Hàng Tồn Kho Bất Động Sản (Inventory Turnover)**:
   $$\text{Inv\_Turnover} = \frac{\text{Revenue}}{\text{Inventory}}$$
10. **Tỷ Suất Book-to-Market Thực (Fama-French HML Real)**:
    $$\text{HML\_REAL} = \frac{\text{Book Equity}}{\text{Market Cap}}$$
11. **Lợi Tức Lợi Nhuận (Earnings Yield - E/P)**:
    $$\text{Earnings\_Yield} = \frac{1}{\text{P/E}} \quad \text{hoặc} \quad \frac{4 \times \text{NetIncome}_{\text{latest}}}{\text{Market Cap}}$$
12. **Lợi Tức Dòng Tiền Tự Do (Free Cash Flow Yield)**:
    $$\text{FCF\_Yield} = \frac{\text{CFO} - \text{CapEx}}{\text{Market Cap}}$$
13. **Nghịch Đảo Định Giá Doanh Nghiệp (EV/EBITDA Inverse)**:
    $$\text{EVEBITDA\_INV} = \frac{\text{EBITDA}}{\text{Market Cap} + \text{Total Debt} - \text{Cash}}$$
14. **Phân Tích Dupont 3 Nhân Tố (ROE Deconstruction)**:
    $$\text{ROE} = \frac{\text{Net Income}}{\text{Sales}} \times \frac{\text{Sales}}{\text{Assets}} \times \frac{\text{Assets}}{\text{Equity}} = \text{Net Margin} \times \text{Asset Turnover} \times \text{Leverage}$$
15. **Gia Tốc Tăng Trưởng Cùng Kỳ (YoY Accelerators)**:
    $$\text{YoY\_Rev} = \frac{\text{Rev}_t - \text{Rev}_{t-4}}{\text{Rev}_{t-4}}, \quad \text{YoY\_Earn} = \frac{\text{NI}_t - \text{NI}_{t-4}}{|\text{NI}_{t-4}|}$$

### Miền B: Vi Cấu Trúc Thị Trường & Biến Đổi Dừng (`frac_diff.py`, `feature_forge.py`)
16. **Khai Triển Nhị Thức Trọng Số FFD (Marcos López de Prado)**:
    $$w_0 = 1, \quad w_k = -w_{k-1} \frac{d - k + 1}{k} \quad (|w_k| \ge 10^{-5})$$
17. **Tích Chập Chuỗi Dừng FFD Cửa Sổ Cố Định**:
    $$\tilde{P}_t(d) = \sum_{k=0}^{K-1} w_k P_{t-k}$$
18. **Cực Tiểu Hóa Nghiệm d* Qua Kiểm Định Augmented Dickey-Fuller**:
    $$\Delta y_t = \alpha + \beta t + \gamma y_{t-1} + \sum_{i=1}^p \delta_i \Delta y_{t-i} + \varepsilon_t, \quad d^* = \min \{ d \in [0, 1] \mid p_{\text{ADF}}(\tilde{P}_t(d)) < 0.05 \}$$
19. **Độ Biến Động Garman-Klass Hiệu Quả**:
    $$\sigma_{\text{GK}}^2 = 0.511 (\ln(H/L))^2 - 0.019 [\ln(C/O)\ln(HL/O^2) - 2\ln(H/O)\ln(L/O)] - 0.383(\ln(C/O))^2$$
20. **Độ Biến Động Parkinson High-Low**:
    $$\sigma_{\text{Parkinson}}^2 = \frac{(\ln(H/L))^2}{4 \ln 2}$$
21. **Chỉ Số Kém Thanh Khoản Amihud (Nguyen 2020 VN Illiquidity Premium)**:
    $$\lambda_{\text{Amihud}, t} = \frac{1}{20} \sum_{\tau=0}^{19} \frac{|R_{t-\tau}|}{P_{t-\tau} \cdot V_{t-\tau}}$$
22. **Xu Hướng Dòng Tiền Giá Trị (Dollar Volume Trend)**:
    $$\text{DVOL\_TREND} = \frac{\frac{1}{5}\sum_{\tau=0}^4 (P_\tau V_\tau)}{\frac{1}{20}\sum_{\tau=0}^{19} (P_\tau V_\tau)} - 1$$
23. **Chỉ Số Dòng Tiền MFI (Money Flow Index 14d)**:
    $$\text{Typical Price} = \frac{H+L+C}{3}, \quad \text{MFI} = 100 - \frac{100}{1 + \frac{\text{Positive Money Flow}}{\text{Negative Money Flow}}}$$
24. **Biên Độ Dao Động Thực Tế Trung Bình (Wilder ATR 14d)**:
    $$\text{TR}_t = \max\left(H_t - L_t, |H_t - C_{t-1}|, |L_t - C_{t-1}|\right), \quad \text{ATR}_t = \frac{13 \cdot \text{ATR}_{t-1} + \text{TR}_t}{14}$$
25. **Xung Lượng Giá Đa Kỳ Hạn (Dang & Nguyen 2021 Momentum Suite)**:
    $$\text{MOM}_{1M} = \frac{P_t}{P_{t-20}} - 1, \quad \text{MOM}_{3M} = \frac{P_{t-20}}{P_{t-60}} - 1, \quad \text{MOM}_{6M} = \frac{P_{t-20}}{P_{t-125}} - 1$$
26. **Xung Lượng Điều Kiện Theo Chế Độ Thị Trường (Regime-Conditional Momentum)**:
    $$\text{COND\_MOM} = \text{MOM}_{3M} \cdot \left(1.0 + 0.5 \cdot \text{VNINDEX\_REGIME}\right)$$

### Miền C: Hành Vi Thị Trường VN, Dòng Tiền Khối Ngoại & Lan Truyền Đồ Thị (`graph_contagion_engine.py`)
27. **Tín Hiệu Mùa Vụ Tết Âm Lịch (Tet Proximity Window)**:
    $$\text{TET\_SIGNAL} = \begin{cases} +1.0 & \text{nếu } 5 \le \text{DaysToTet} \le 20 \text{ (retail FOMO)} \\ -0.5 & \text{nếu } -10 \le \text{DaysToTet} < 0 \text{ (post-Tet selloff)} \\ 0.0 & \text{còn lại} \end{cases}$$
28. **Bẫy Giải Chấp Bắt Đáy (Forced Margin Selling Exhaustion)**:
    $$\text{FORCED\_SELLING} = \mathbb{I}\left(\text{FloorHits}_{5d} \ge 2 \;\land\; \frac{\text{Vol}_{5d}}{\text{Vol}_{20d}} > 3.0\right)$$
29. **Tần Suất Kịch Trần Đảo Chiều (Ceiling Streak Exhaustion)**:
    $$\text{CEILING\_STREAK} = \frac{\sum_{\tau=0}^9 \mathbb{I}(P_{t-\tau} \ge P_{\text{ceil}})}{10}$$
30. **Độ Khan Hiếm Room Khối Ngoại (Foreign Room Scarcity)**:
    $$\text{FOREIGN\_ROOM} = \begin{cases} -1.0 & \text{nếu } \text{RoomRemaining} / \text{RoomLimit} < 0.05 \text{ (kịch trần room)} \\ +0.5 & \text{nếu } \text{RoomRemaining} / \text{RoomLimit} > 0.30 \text{ (dồi dào room)} \\ 0.0 & \text{còn lại} \end{cases}$$
31. **Tỷ Lệ Mua Ròng Khối Ngoại 5 Phiên (Foreign Net 5D Ratio)**:
    $$\text{FOREIGN\_NET\_5D} = \frac{\sum_{\tau=0}^4 \text{NetValue}_{\tau}}{\text{Market Cap}}$$
32. **Chuỗi Ngày Mua Ròng Liên Tiếp (Foreign Accumulation Streak)**:
    $$\text{FOREIGN\_ACCUM} = \frac{\text{ConsecutiveDays}_{\text{NetBuy} > 0}}{10.0}$$
33. **Tỷ Lệ Giao Dịch Nội Bộ 30 Ngày (Insider Net 30D - UBCKNN)**:
    $$\text{INSIDER\_NET\_30D} = \frac{\sum \text{BuyQty} - \sum \text{SellQty}}{\text{Estimated Total Shares}}$$
34. **Quy Mô Vốn Hóa Thị Trường (Log Market Cap Size)**:
    $$\text{SIZE} = \ln(\text{Market Cap})$$
35. **Tỷ Số Vòng Quay Khối Lượng Đột Biến (Turnover Surge Ratio)**:
    $$\text{VR}_{i, t} = \frac{V_{i, t}}{\text{MA}_{20}(V_i) + 10^{-8}}$$
36. **Xung Lực Cú Sốc Trưởng Ngành (Leader Shock Impulse)**:
    $$\text{Shock}_{\text{lead}, t} = R_{\text{lead}, t} \cdot \ln\left(1 + \max(0, \text{VR}_{\text{lead}, t})\right) \xrightarrow{\text{Shift}(1, 2)} \text{sec\_hub\_shock\_1d/2d}$$
37. **Thế Năng Đuổi Kịp Leader-Follower 3 Phiên (Catch-up Potential)**:
    $$\Delta_{\text{catchup}, t-1} = \prod_{k=1}^3 (1 + R_{\text{sec}, t-k}) - \prod_{k=1}^3 (1 + R_{\text{stock}, t-k})$$
38. **Độ Rộng Bùng Nổ Khối Lượng Cụm Ngành (Volume Surge Breadth)**:
    $$B_{\text{surge}, t-1} = \frac{1}{|S|} \sum_{i \in S} \mathbb{I}\left(\text{VR}_{i, t-1} \ge 1.5 \land R_{i, t-1} > 0\right)$$
39. **Xung Lực Lan Tỏa Tập Đoàn Tài Phiệt (Ecosystem Spillover Impulse)**:
    $$\text{Eco\_Shock}_{t-1} = R_{\text{eco\_lead}, t-1} \cdot \ln\left(1 + \max(0, \text{VR}_{\text{eco\_lead}, t-1})\right)$$

### Miền D: Đại Số Trực Giao, Chuẩn Hóa Ngành & Kiểm Định IC (`sector_neutralizer.py`, `factor_orthogonalization.py`, `vn_ic_tester.py`)
40. **Toán Tử Nhận Diện Loại Phân Phối (Distribution Classifier)**:
    $$\text{Type}(X) = \begin{cases} \text{binary} & \text{nếu } |U| \le 2 \\ \text{discrete\_ordinal} & \text{nếu } |U| \le 11 \land \forall v \in U, v \equiv 0 \pmod{0.1} \\ \text{continuous} & \text{còn lại} \end{cases}$$
41. **Cắt Ngọn Phân Vị Ngành (Per-Sector Winsorization)**:
    $$x^*_i = \max\left(q_{0.01}(S_i), \min(x_i, q_{0.99}(S_i))\right)$$
42. **Z-Score Ngành Phòng Thủ Có Biên Mẫu (Defensive Sector Z-Score)**:
    $$z_i = \begin{cases} \frac{x^*_i - \mu_{S_i}}{\sigma_{S_i}} & \text{nếu } |S_i| \ge 3 \text{ và } \sigma_{S_i} > 0 \\ 0.0 & \text{nếu } |S_i| < 3 \text{ hoặc } \sigma_{S_i} = 0 \end{cases}$$
43. **Điểm Phần Trăm Nội Ngành (Within-Sector Percentile Rank)**:
    $$r_i = \frac{\text{Rank}(x_i) - 1}{N_{S_i} - 1} \times 100 \in [0, 100]$$
44. **Trực Giao Hóa Gram-Schmidt Khử Đa Cộng Tuyến (Orthogonal Residualization)**:
    $$\mathbf{u}_k = \mathbf{v}_k - \sum_{j=1}^{k-1} \frac{\mathbf{v}_k^T \mathbf{u}_j}{\|\mathbf{u}_j\|^2} \mathbf{u}_j \quad \implies \langle \mathbf{u}_k, \mathbf{u}_j \rangle = 0 \quad \forall j < k$$
45. **Chiếu Thành Phần Chính Nội Cụm (Within-Group PCA)**:
    $$X_{\text{orth}} = X \mathbf{W}_1, \quad \text{với } \mathbf{C} \mathbf{W}_1 = \lambda_1 \mathbf{W}_1$$
46. **Hệ Số Phóng Đại Phương Sai (VIF - Multicollinearity Diagnostic)**:
    $$\text{VIF}_j = \frac{1}{1 - R_j^2}, \quad \text{Ngưỡng an toàn: } \text{VIF} < 5.0$$
47. **Hệ Số Tương Quan Hạng Spearman (Rank IC Kỳ Hạn 5 Phiên)**:
    $$\rho_{\text{Rank}}(t) = 1 - \frac{6 \sum_{i=1}^n d_i^2}{n(n^2 - 1)}$$
48. **Chỉ Số Thông Tin IR & Thống Kê T-Stat Kiểm Định Ý Nghĩa Nhân Tố**:
    $$\text{IR}_{\text{factor}} = \frac{\overline{\text{IC}}}{\sigma_{\text{IC}}}, \quad t_{\text{stat}} = \text{IR} \cdot \sqrt{T} \quad (\text{Yêu cầu: } |t| > 2.0)$$
49. **Biên Độ Đơn Điệu 10 Phân Vị (Decile Monotonicity Spread)**:
    $$\text{Spread} = R_{Q_{10}} - R_{Q_1} = \frac{1}{|Q_{10}|} \sum_{i \in Q_{10}} R_{i, 5d} - \frac{1}{|Q_1|} \sum_{j \in Q_1} R_{j, 5d}$$

### Miền E: Thể Chế Ràng Buộc Sàn HOSE, Định Cỡ Vị Thế & Cắt Lỗ Đa Tầng (`hard_laws.py`, `kelly_sizer.py`, `stop_loss.py`)
50. **Bước Nhảy Lô Giá & Biên Độ Trần Sàn HOSE (\pm 7\%)**:
    $$P_{\text{ceil}} = \text{round}\left(\frac{P_{t-1} \times 1.07}{100}\right) \times 100, \quad P_{\text{floor}} = \text{round}\left(\frac{P_{t-1} \times 0.93}{100}\right) \times 100$$
51. **Luật Tồn Tại Rủi Ro Kẹt Sàn T+2.5 (Điều 1 Hard Law)**:
    $$\text{Downside}_{\text{T25}} = 1 - (1 - 0.07)^2 = 13.51\%$$
    $$\text{Risk Amount} = P_i \cdot \max(\text{StopLoss}_{\%}, 0.1351) \cdot Q_i \le 0.02 \cdot \text{NAV}$$
52. **Luật Thanh Khoản Khớp Lệnh & Sức Chứa Vị Thế (Điều 2 Hard Law)**:
    $$Q_{\text{order}} \le 0.15 \cdot \text{ADTV}_{20}, \quad \sum Q_{\text{position}} \le 0.25 \cdot \text{ADTV}_{20}$$
53. **Luật Tập Trung Cổ Phiếu & Phân Ngành (Điều 4 Hard Law)**:
    $$w_i = \frac{P_i Q_i}{\text{NAV}} \le 0.15, \quad \sum_{i \in S} w_i \le 0.35$$
54. **Tiêu Chuẩn Kelly Đầy Đủ & Thu Hẹp Phân Số (Fractional Kelly)**:
    $$f^* = W - \frac{1 - W}{R}, \quad f_{\text{baseline}} = 0.25 \cdot f^* \quad (\text{Quarter Kelly})$$
55. **Hệ Số Co Giãn Theo Chế Độ Thị Trường HMM (Regime Multiplier)**:
    $$w_i^* = \min\left(0.25 \cdot f^* \cdot M_{\text{regime}}, 0.15\right), \quad M_{\text{regime}} \in \{1.00 \text{ (Bull)}, 0.75 \text{ (Choppy)}, 0.50 \text{ (Bounce)}, 0.25 \text{ (Bear)}\}$$
56. **Toán Tử Làm Tròn Lô Chẵn Khớp Lệnh Sàn HOSE (Round Lot 100)**:
    $$Q_i^* = \left\lfloor \frac{w_i^* \cdot \text{NAV}}{100 \cdot P_i} \right\rfloor \times 100$$
57. **Thang Phòng Thủ Cắt Lỗ Đa Tầng Sinh Tử (Stop Loss Hierarchy T0 - T5)**:
    - **T0 (Hàng khả dụng)**: $Q_{\text{sell}} \le Q_{\text{available}}$ (đã qua chu kỳ T+2.5)
    - **T1 (Hard Stop Lớp 1)**: $\frac{\text{Unrealized PnL}}{\text{NAV}} \le -0.02 \implies \text{BÁN SẠCH TOÀN BỘ HÀNG KHẢ DỤNG}$
    - **T2 (Trailing Stop Khóa Lãi)**: $\frac{P_{\text{peak}} - P_{\text{entry}}}{P_{\text{entry}}} > 0.10 \;\land\; \frac{P_{\text{peak}} - P_t}{P_{\text{peak}} - P_{\text{entry}}} \ge 0.35 \implies \text{CHỐT LỜI}$
    - **T3 (Structural Support Exit)**: $P_t < P_{\text{swing\_low}} \implies \text{THOÁT KHI THỦNG HỖ TRỢ KỸ THUẬT}$
    - **T4 (VSA Bearish Rejection)**: $\frac{H_t - \max(O_t, C_t)}{H_t - L_t} > 0.50 \;\land\; V_t > 1.5 \cdot \text{MA}_{20}(V) \implies \text{THOÁT NHANH PHIÊN BỊ TỪ CHỐI}$
    - **T5 (Chi Phí Cơ Hội)**: $\text{DaysHeld} > 0.5 \cdot \text{ExpectedTimeline} \;\land\; \text{Return} < 0.02 \implies \text{THOÁT TÁI PHÂN BỔ}$

### Miền F: Đồ Thị Doanh Nghiệp Thời Gian & Giám Định Pháp Y Rút Ruột GIL (`gil_service.py`, `sag_connector.py`)
58. **Bất Biến Phạm Vi Kế Toán (Accounting Scope Boundary Invariance)**:
    $$\text{Scope}(x) = \text{Scope}(y) \iff \text{Giao dịch nội bộ hợp lệ}, \quad \text{Scope}(x) \ne \text{Scope}(y) \implies \text{Nghi vấn rút ruột chuyển giá}$$
59. **Tỷ Lệ Cho Vay Bên Liên Quan Trên Vốn Chủ Sở Hữu**:
    $$\text{RPL\_Ratio} = \frac{\sum \text{Loans to Related Parties}}{\text{Total Equity}}$$
60. **Đột Biến Phải Thu Khác Trên Tổng Tài Sản**:
    $$\Delta \text{OtherRec} = \frac{\text{Other Receivables}_t}{\text{Total Assets}_t} - \frac{\text{Other Receivables}_{t-1}}{\text{Total Assets}_{t-1}}$$
61. **Phát Hiện Chu Trình Rút Vốn Khép Kín Động (Motif M1 Cycle Detection via Tarjan SCC)**:
    $$\exists \text{ Directed Cycle } C = (v_1, v_2, \dots, v_k, v_1) \subset G_{\text{flow}} \text{ với chu kỳ } \Delta \tau \le 90 \text{ ngày}$$

---

## 3. Đặc Tả Chi Tiết 7 Master Prompt Đạt Chuẩn Khoa Học

### Pillar 1: Fractionally Differentiated Feature Engineering (`frac_diff.py`, `feature_forge.py`)

```markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/ICLR standard) dưới dạng file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, mô tả chính xác giải thuật Vi phân Phân số Cố định độ rộng cửa sổ (FFD) và luyện tạo đặc trưng vi cấu trúc:

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: Marcos López de Prado Fixed-width Window Fractional Differentiation (FFD) & Feature Forge Engine
- **Mục tiêu kỹ thuật**: Chuyển hóa chuỗi giá không dừng P_t thành chuỗi dừng có tính hiệp phương sai (Stationary) mà vẫn bảo toàn tối đa ký ức dài hạn (Long Memory), tránh sai lầm mất sạch thông tin của Integer Differencing d=1.
- **Đường dẫn mã nguồn gốc đã khảo sát**:
  - `ai-engine/app/domain/services/ml/frac_diff.py`
  - `ai-engine/app/domain/services/ml/feature_forge.py`

### 2. BỐ CỤC PHÂN RÃ HAI PHA (SPATIOTEMPORAL DUAL-PHASE LAYOUT)
- **Nửa trên: PHA 1 - OFFLINE CALIBRATION & OPTIMAL D SEARCH**
  * Đầu vào thô: Chuỗi thời gian giá đóng cửa P_t \in \mathbb{R}^T
  * Bộ sinh trọng số nhị thức: Tính toán chuỗi trọng số w_k đệ quy:
    w_0 = 1, \quad w_k = -w_{k-1} \frac{d - k + 1}{k}
  * Ngưỡng cắt lọc cửa sổ cố định (FFD Cutoff): Loại bỏ các trọng số |w_k| < 10^{-5}, cố định chiều dài cửa sổ width = K.
  * Quét tham số tối ưu (Grid Search d \in [0.0, 1.0], \Delta d = 0.05):
    - Chạy kiểm định tính dừng Augmented Dickey-Fuller (ADF) trên chuỗi vi phân (maxlag=1, regression='c').
    - Chọn nghiệm d^* nhỏ nhất thỏa mãn: p_{\text{ADF}} < 0.05.
- **Nửa dưới: PHA 2 - ONLINE ROLLING CONVOLUTION & MULTI-HORIZON ALPHA FORGING**
  * Trigger trực tuyến: Vector giá thanh nến mới P_{t}.
  * Tích chập hợp lệ (Valid Mode 1D Convolution):
    \tilde{P}_t = \sum_{k=0}^{K-1} w_k P_{t-k}
  * Bổ sung đệm rỗng: Pad (K-1) giá trị NaN phía trước để đồng bộ nhịp index thời gian.
  * Ghép nhánh đặc trưng vi cấu trúc (Microstructure Joint Feature Tensor):
    - Biến động Garman-Klass: \sigma_{\text{GK}}^2
    - Chỉ số thanh khoản Amihud: \lambda_{\text{Amihud}} = \frac{|R_t|}{\text{Turnover}_t}
    - Giới hạn biên độ sàn HOSE: \pm 7\%
  * Đầu ra chuẩn hóa: Ma trận đặc trưng dừng đa chiều cấp phát cho các bộ xếp hạng ML Alpha.

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- Công thức trọng số FFD: w_k = -w_{k-1} \cdot \frac{d - k + 1}{k}, \quad w_0 = 1.0.
- Ngưỡng cắt trọng số: |w_k| \ge 10^{-5} (`threshold = 1e-5`).
- Kiểm định dừng ADF: `adfuller(series, maxlag=1, regression='c')`, điều kiện biên: p_{\text{value}} < 0.05.
- Không gian tham số quét d: `np.arange(0.0, 1.01, 0.05)` (từ 0.0 đến 1.0, bước nhảy 0.05).
- Ngưỡng chiều dài tối thiểu kiểm định: T_{\text{diff}} \ge 20 mẫu.

### 4. BỘ KÝ HIỆU HÌNH HỌC (DATA GLYPHS SPECIFICATION)
- Glyph Hệ trục tọa độ hàm phân rã trọng số: Đồ thị đường cong suy giảm lũy thừa của w_k theo k.
- Glyph Băng trượt cửa sổ FFD (Sliding Window): Dải ô nối tiếp [w_0 | w_1 | ... | w_K] trượt trên dải giá [P_{t-K} ... P_t].
- Nhãn toán tử trên mũi tên: [w_0=1], [|w_k| < 10^{-5}], [ADF p < 0.05], [Conv1D Valid].

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE RULES)
- CẤM NỀN ĐEN CHO ITEM/NODE.
- Nền Canvas: Trắng ngà thanh lịch `#fafafa` với họa tiết chấm mờ kỹ thuật.
- Nền Node Trọng số FFD: Xanh da trời nhạt `#e0f2fe`, viền `#0284c7`.
- Nền Node Kiểm định ADF: Tím oải hương nhạt `#f3e8ff`, viền `#7c3aed`.
- Nền Node Tối ưu d*: Xanh bạc hà nhạt `#dcfce7`, viền `#16a34a`.
- Nền Node Cảnh báo / Fallback diff: Cam đào nhạt `#ffedd5`, viền `#ea580c`.
- Typography: Navy đậm `#0f172a`, sắc nét, tương phản tối ưu.

### 6. ĐỘNG LỰC HỌC LUỒNG & TƯƠNG TÁC BẢNG ĐIỀU TRA (INTERACTIVE SPECIFICATIONS)
- Dynamic Flow Opacity: Node/mũi tên không thuộc bước hiện tại mờ xuống `opacity: 0.15`. Bước hiện tại đạt `opacity: 1.0` với hạt photon chạy dọc kết nối.
- Interactive Drawer: Click vào bất kỳ khối nào mở drawer trượt bên phải:
  * Tên giải thuật & Module path: `app/domain/services/ml/frac_diff.py`
  * Công thức toán học LaTeX: w_k, p_{\text{ADF}}
  * Thông số biên: `threshold=1e-5`, `pval=0.05`, `step=0.05`
  * Cấu trúc I/O: Series Float -> Stationary Series Float.
```

---

### Pillar 2: Directed Graph Shock Propagation & Conglomerate Contagion (`graph_contagion_engine.py`)

```markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/SIGMOD standard) dưới dạng file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, mô tả chính xác giải thuật Lan truyền Cú sốc Đồ thị Định hướng & Cộng hưởng Hệ sinh thái Tập đoàn HOSE:

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: HOSE Graph Contagion & Syndicate Lead-Lag Engine
- **Mục tiêu kỹ thuật**: Dự báo biến động giá và dòng tiền lan truyền qua đồ thị định hướng 15 phân ngành (406 mã cổ phiếu niêm yết sàn HOSE) và 9 tập đoàn tài phiệt lớn (Syndicates/Conglomerates) tại Việt Nam, bắt trọn độ trễ (Lead-Lag) mà các mô hình phẳng bỏ sót.
- **Đường dẫn mã nguồn gốc đã khảo sát**:
  - `ai-engine/app/domain/services/ml/graph_contagion_engine.py`

### 2. BỐ CỤC PHÂN RÃ HAI PHA (SPATIOTEMPORAL DUAL-PHASE LAYOUT)
- **Nửa trên: PHA 1 - GRAPH TOPOLOGY CONSTRUCTION & ANCHOR NODES DEFINITION**
  * Chiếu 406 mã cổ phiếu vào 15 nhóm ngành ICB:
    Banking (23), Real Estate (60), Construction (44), Industrial Goods (44), Energy (35), Logistics (35), Retail (34), Securities (27), Chemicals (25), Consumer (18), Steel (17), Agriculture (16), Healthcare (12), Tech (9), Other (7).
  * Xác định các Nút Trọng Tâm Ngành (Sector Hub Leaders):
    VCB (Bank), SSI (Chứng), DIG (BĐS), HPG (Thép), PVD (Dầu khí), MWG (Bán lẻ), VNM (Tiêu dùng), FPT (Công nghệ), DGC (Hóa chất), GMD (Cảng), VCG (Xây dựng), VHC (Thủy sản), DHG (Dược), GEX (Thiết bị điện), DSN (Khác).
  * Xác định 9 Cụm Hệ sinh thái Tập đoàn (Conglomerate Ecosystems):
    - VinGroup (VIC -> VHM, VRE, VPL)
    - Gelex Group (GEX -> VIX, VGC, GEE)
    - DGC Group (DGC -> CSV, PAT)
    - Hoang Huy Group (TCH -> HHS, CRV)
    - CII Group (CII -> NBB)
    - Becamex Group (BCM -> IJC, TDC)
    - Dat Xanh Group (DXG -> DXS)
    - Masan Group (MSN -> MCH, MCM)
    - Bamboo Capital (BCG -> TCD)
- **Nửa dưới: PHA 2 - ONLINE DIRECTED SHOCK DYNAMICS & SPILLOVER CHANNELS**
  * Tỷ số thanh khoản đột biến:
    \text{VR}_{i, t} = \frac{\text{Volume}_{i, t}}{\text{MA}_{20}(\text{Volume}_{i}) + 10^{-8}}
  * Kênh 1: Cú sốc Hub Trưởng ngành định hướng (Lag 1 & Lag 2):
    \text{Shock}_{\text{lead}, t} = R_{\text{lead}, t} \cdot \ln(1 + \max(0, \text{VR}_{\text{lead}, t})) \xrightarrow{\text{Shift}(1, 2)} \text{sec\_hub\_shock\_1d/2d}
  * Kênh 2: Thế năng đuổi kịp Leader-Follower (3-Day Catch-up Potential):
    \Delta_{\text{catchup}, t} = \left[ \prod_{k=0}^2 (1 + R_{\text{sec}, t-k}) - \prod_{k=0}^2 (1 + R_{\text{stock}, t-k}) \right]_{\text{Shift}(1)}
  * Kênh 3: Độ rộng bùng nổ thanh khoản cụm ngành (Volume Surge Breadth):
    B_{\text{surge}, t-1} = \frac{1}{|S|} \sum_{i \in S} \mathbb{I}(\text{VR}_{i, t-1} \ge 1.5 \land R_{i, t-1} > 0)
  * Kênh 4: Xung lực lan tỏa Tập đoàn (Ecosystem Spillover Impulse):
    \text{Eco\_Shock}_{t-1} = R_{\text{eco\_lead}, t-1} \cdot \ln(1 + \max(0, \text{VR}_{\text{eco\_lead}, t-1}))
  * Hàng rào chống Lookahead: Bắt buộc 100% tín hiệu áp dụng toán tử trễ \text{Shift}(1) hoặc \text{Shift}(2).

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- Cửa sổ trung bình động khối lượng: \text{rolling}(20, \text{min\_periods}=5).
- Ngưỡng bùng nổ khối lượng ngành (Surge Threshold): \text{VR} \ge 1.5 và Return > 0.
- Công thức suy biến Leader Shock: S = R \cdot \ln(1 + \max(0, \text{VR})).
- Tỷ suất sinh lời tích lũy 3 phiên: \text{rolling}(3).\text{apply}(\prod) - 1.
- Bảo toàn thời gian thực: Zero-lookahead bias via \text{shift}(1) and \text{shift}(2).

### 4. BỘ KÝ HIỆU HÌNH HỌC (DATA GLYPHS SPECIFICATION)
- Glyph Đồ thị Trọng tâm (Hub-and-Spoke Topology): Nút trung tâm lớn (VCB, VIC, GEX) nối mũi tên định hướng tới các nút con vệ tinh.
- Glyph Cột sóng bùng nổ thanh khoản: Biểu đồ thanh 3 cột mô phỏng Surge Breadth \ge 1.5x.
- Nhãn toán tử trên mũi tên: [VR >= 1.5], [Shift(1) Lag], [Shift(2) Echo], [ln(1+VR)], [3d Cumprod Divergence].

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE RULES)
- CẤM MÀU NỀN ĐEN.
- Nút Hub Trưởng ngành (Leader): Tím oải hương nhạt `#f3e8ff`, viền tím thạch anh `#7c3aed`.
- Nút Cổ phiếu Vệ tinh (Follower): Xanh da trời nhạt `#e0f2fe`, viền `#0284c7`.
- Nút Lan tỏa Tập đoàn (Syndicate): Xanh bạc hà nhạt `#dcfce7`, viền `#16a34a`.
- Đường truyền cú sốc (Shock Edge): Cam cháy nhạt rực rỡ `#fed7aa` với viền `#ea580c`.

### 6. ĐỘNG LỰC HỌC LUỒNG & TƯƠNG TÁC BẢNG ĐIỀU TRA (INTERACTIVE SPECIFICATIONS)
- Dynamic Flow Opacity: Bước phát xung Leader Shock bừng sáng 100%, các nhánh khác mờ 15%.
- Inspector Drawer: Click vào nút Hub (VD: VCB, GEX, VIC) hiển thị danh sách vệ tinh nhận xung lực, công thức Shock logarit và chỉ số lan tỏa thời gian thực.
```

---

### Pillar 3: Cross-Sectional Factor Neutralization, Orthogonalization & IC Engine (`sector_neutralizer.py`, `factor_orthogonalization.py`, `vn_ic_tester.py`)

```markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/ICLR/SIGMOD standard) dưới dạng file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, mô tả chính xác giải thuật Trung hòa Ngành, Trực giao hóa Gram-Schmidt/PCA và Kiểm định IC nhân tố thị trường Việt Nam:

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: Cross-Sectional Sector Neutralization, Factor Orthogonalization & Purged IC Testing Suite
- **Mục tiêu kỹ thuật**: Triệt tiêu rủi ro đa cộng tuyến (Multicollinearity) giữa các nhân tố alpha, chuẩn hóa điểm số không bị lệch pha bởi độ biến động riêng của từng ngành, và đo lường hệ số thông tin (Information Coefficient) không thiên lệch.
- **Đường dẫn mã nguồn gốc đã khảo sát**:
  - `ai-engine/app/domain/services/quant/sector_neutralizer.py`
  - `ai-engine/app/domain/services/quant/factor_orthogonalization.py`
  - `ai-engine/app/domain/services/quant/vn_ic_tester.py`

### 2. BỐ CỤC PHÂN RÃ HAI PHA (SPATIOTEMPORAL DUAL-PHASE LAYOUT)
- **Nửa trên: PHA 1 - DEFENSIVE SECTOR NEUTRALIZATION & ORTHOGONALIZATION**
  * Tự động nhận diện phân phối:
    - Nhị phân (|U| \le 2)
    - Thứ bậc rời rạc (|U| \le 11 \land \text{mod}(0.1) = 0)
    - Liên tục (Continuous)
  * Phân vị Winsorize cắt ngọn 2 đầu: Clip giá trị trong [q_{0.01}, q_{0.99}] (có cấu hình ghi đè theo phân ngành đặc thù).
  * Điểm Z-Score theo ngành phòng thủ (Safe Sector Z-Score):
    z_{i} = \frac{x_i - \mu_{\text{sector}}}{\sigma_{\text{sector}}} \quad (\text{với } \text{ddof}=1)
    Điều kiện biên an toàn: Nếu số lượng quan sát trong ngành N < 3 hoặc \sigma_{\text{sector}} = 0 \implies z_i = 0.0.
  * Cụm tương quan & Trực giao hóa nhân tố (Orthogonalization):
    - Thuật toán Gram-Schmidt theo thứ tự ưu tiên:
      \mathbf{u}_k = \mathbf{v}_k - \sum_{j=1}^{k-1} \frac{\langle \mathbf{v}_k, \mathbf{u}_j \rangle}{\langle \mathbf{u}_j, \mathbf{u}_j \rangle} \mathbf{u}_j
    - Trích xuất PCA nội cụm (Within-group PCA): Chiếu nhân tố lên thành phần chính giải thích phương sai lớn nhất.
- **Nửa dưới: PHA 2 - VN-SPECIFIC PURGED IC TESTING & PERFORMANCE ATTRIBUTION**
  * Bộ lọc thanh khoản sàn HOSE: Loại bỏ cổ phiếu có giá trị giao dịch trung bình ngày < 5 tỷ VND (`min_value_bn = 5.0`).
  * Bộ lọc giá trần HOSE: Không thể mua cổ phiếu nếu giá ở mức trần (`P_t = \text{Ceiling Price}`).
  * Tỷ suất sinh lời kỳ hạn 5 ngày:
    R_{t+1 \to t+5} = \frac{P_{t+5}}{P_{t}} - 1
  * Đo lường IC đa chiều:
    - Rank IC (Spearman Rank Correlation): \rho_{\text{Rank}} = 1 - \frac{6 \sum d_i^2}{n(n^2 - 1)}
    - Normal IC (Pearson Correlation): \rho_{\text{Pearson}}
    - Thống kê kiểm định T-Stat: t = \frac{\overline{\text{IC}}}{\sigma_{\text{IC}} / \sqrt{N}}
    - Phân bổ 10 nhóm Decile Spread: Lợi nhuận Q10 (Top 10%) trừ Q1 (Bottom 10%).

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- Ngưỡng nhận diện phân phối: Cardinality \le 2 (nhị phân), \le 11 và bội số 0.1 (thứ bậc).
- Ngưỡng mẫu ngành tối thiểu: `min_valid = 3`.
- Cận phân vị Winsorize: Mặc định (0.01, 0.99).
- Bộ lọc thanh khoản: Giá trị giao dịch \ge 5.0 tỷ VND/ngày.
- Chu kỳ nắm giữ: `holding = 5` phiên giao dịch.
- Công thức giá trần HOSE: P_{\text{ceil}} = \text{round}(P_{t-1} \times 1.07 / 100) \times 100.

### 4. BỘ KÝ HIỆU HÌNH HỌC (DATA GLYPHS SPECIFICATION)
- Glyph Chiếu hình học Không gian Vector: Vẽ vector nhân tố v_k bị trừ hình chiếu tạo thành vector trực giao u_k (vuông góc 90 độ có ký hiệu góc vuông).
- Glyph Phân vị 10 cột Decile: Biểu đồ cột bậc thang từ D1 đến D10 thể hiện tính đơn điệu của nhân tố (Monotonicity).
- Nhãn toán tử trên mũi tên: [Winsorize 1%-99%], [Sector Z-Score (N>=3)], [Gram-Schmidt u_k], [Liquidity >= 5B VND], [Ceiling Filter].

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE RULES)
- CẤM NỀN ĐEN HOÀN TOÀN.
- Khối Chuẩn hóa ngành: Xanh da trời nhạt `#e0f2fe`, viền `#0284c7`.
- Khối Trực giao hóa Gram-Schmidt: Tím thạch anh nhạt `#f3e8ff`, viền `#7c3aed`.
- Khối Bộ lọc Thanh khoản & Giá trần: Cam đào nhạt `#ffedd5`, viền `#ea580c`.
- Khối Đánh giá IC & Decile Spread: Xanh bạc hà nhạt `#dcfce7`, viền `#16a34a`.

### 6. ĐỘNG LỰC HỌC LUỒNG & TƯƠNG TÁC BẢNG ĐIỀU TRA (INTERACTIVE SPECIFICATIONS)
- Dynamic Flow Opacity: Từng trạm xử lý biến đổi từ dữ liệu thô -> chuẩn hóa -> trực giao -> kiểm định sáng 100%, các vùng khác mờ 15%.
- Inspector Drawer: Click vào trạm trực giao hóa hiển thị ma trận tương quan trước và sau trực giao, công thức Gram-Schmidt và giải thích loại trừ đa cộng tuyến.
```

---

### Pillar 4: Dual-Engine Adaptive Beneish M-Score Forensic Accounting (`beneish.py`)

```markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/SIGMOD standard) dưới dạng file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, mô tả chính xác giải thuật Bóc tách Gian lận Kế toán Thích ứng Kép Beneish M-Score (VAS Thông tư 200/BTC):

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: Dual-Engine Adaptive Beneish M-Score Forensic Engine (TASK-202)
- **Mục tiêu kỹ thuật**: Tự động phát hiện thủ thuật thao túng lợi nhuận (Earnings Manipulation) và làm đẹp báo cáo tài chính của các công ty niêm yết trên HOSE/HNX, đóng vai trò Hard Law Lớp 0 (Veto Gate tuyệt đối trước khi danh mục đầu tư hình thành).
- **Đường dẫn mã nguồn gốc đã khảo sát**:
  - `ai-engine/app/domain/rules/beneish.py`

### 2. BỐ CỤC PHÂN RÃ HAI PHA (SPATIOTEMPORAL DUAL-PHASE LAYOUT)
- **Nửa trên: PHA 1 - VAS FINANCIAL EXTRACTION & ADAPTIVE ENGINE ROUTING**
  * Nạp dữ liệu BCTC chuẩn hóa theo Thông tư 200/BTC (Doanh thu thuần, Phải thu ngắn hạn, Giá vốn, Tổng tài sản, Tài sản cố định PPE, Chi phí SG&A, Nợ phải trả, Khấu hao & Dòng tiền hoạt động CFO).
  * Bộ lọc miễn trừ ngành tài chính đặc thù:
    Bỏ qua Ngân hàng, Bất động sản, Chứng khoán, Bảo hiểm, Dịch vụ tài chính (do cấu trúc bảng cân đối kế toán khác biệt).
  * Bộ chuyển mạch thích ứng kép (Adaptive Switch):
    - Động cơ 1: Mô hình Đầy đủ 8 Biến gốc (Full 8-Variable Model) khi có đầy đủ dữ liệu dòng tiền và khấu hao.
    - Động cơ 2: Mô hình Thích ứng 5 Biến phi dòng tiền (Adaptive 5-Variable Non-Cash-Flow Model) khi BCTC quý tóm tắt không bóc tách riêng dòng Khấu hao (Cam kết 100% số liệu thực tế, cấm điền số 0 giả).
- **Nửa dưới: PHA 2 - MATHEMATICAL FACTOR SYNTHESIS & VETO GATING**
  * Tính toán 8 chỉ số tài chính vi mô:
    1. DSRI = \frac{\text{Rec}_t / \text{Rev}_t}{\text{Rec}_{t-1} / \text{Rev}_{t-1}} (Phải thu trên doanh thu)
    2. GMI = \frac{\text{Margin}_{t-1}}{\text{Margin}_t} (Suy giảm biên lợi nhuận gộp)
    3. AQI = \frac{1 - (\text{CA}_t + \text{PPE}_t) / \text{TA}_t}{1 - (\text{CA}_{t-1} + \text{PPE}_{t-1}) / \text{TA}_{t-1}} (Chất lượng tài sản)
    4. SGI = \frac{\text{Rev}_t}{\text{Rev}_{t-1}} (Tăng trưởng doanh thu)
    5. DEPI = \frac{\text{DepRate}_{t-1}}{\text{DepRate}_t} (Tỷ lệ khấu hao suy giảm)
    6. SGAI = \frac{\text{SGA}_t / \text{Rev}_t}{\text{SGA}_{t-1} / \text{Rev}_{t-1}} (Tỷ lệ chi phí bán hàng & quản lý)
    7. LVGI = \frac{\text{Debt}_t / \text{TA}_t}{\text{Debt}_{t-1} / \text{TA}_{t-1}} (Đòn bẩy tài chính)
    8. TATA = \frac{\text{NetIncome}_t - \text{CFO}_t}{\text{TA}_t} (Tổng biến tích dồn tích)
  * Tổng hợp tuyến tính đa biến:
    - M_8 = -4.84 + 0.920 \cdot \text{DSRI} + 0.528 \cdot \text{GMI} + 0.404 \cdot \text{AQI} + 0.892 \cdot \text{SGI} + 0.115 \cdot \text{DEPI} - 0.172 \cdot \text{SGAI} + 4.037 \cdot \text{TATA} + 0.0327 \cdot \text{LVGI}
    - M_5 = -6.065 + 0.823 \cdot \text{DSRI} + 0.906 \cdot \text{GMI} + 0.593 \cdot \text{AQI} + 0.717 \cdot \text{SGI} + 0.107 \cdot \text{LVGI}
  * Cổng phán quyết Lớp 0 (Hard Law Veto Gate):
    - Nếu M > -1.78 \implies \text{FAIL (Red Flag: Khả năng gian lận cao, loại khỏi Universe)}.
    - Nếu M \le -1.78 \implies \text{PASS (Báo cáo tài chính tin cậy)}.

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- Ngưỡng loại trừ (Cutoff Threshold): M > -1.78.
- Trọng số hệ số M8: Hệ số dồn tích TATA có trọng số lớn nhất (+4.037), kế đến là DSRI (+0.920) và SGI (+0.892).
- Trọng số hệ số M5: Hằng số chặn -6.065, GMI (+0.906), DSRI (+0.823), SGI (+0.717), AQI (+0.593), LVGI (+0.107).
- Ngành miễn trừ tuyệt đối: Ngân hàng, Bất động sản, Chứng khoán, Bảo hiểm, Dịch vụ tài chính.

### 4. BỘ KÝ HIỆU HÌNH HỌC (DATA GLYPHS SPECIFICATION)
- Glyph Cân đòn bẩy tài chính: Biểu tượng cán cân so sánh giữa Lợi nhuận Kế toán và Dòng tiền thật CFO (TATA Accruals).
- Glyph Cổng chặn Phủ quyết Veto: Cổng kim loại màu cam đào với chốt khóa biểu tượng Veto khi M > -1.78.
- Nhãn toán tử trên mũi tên: [Sector Exempt?], [Has Cashflow?], [M8 Full], [M5 Adaptive], [M > -1.78 FAIL], [M <= -1.78 PASS].

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE RULES)
- CẤM MÀU NỀN ĐEN.
- Khối BCTC đầu vào: Trắng tinh khiết `#ffffff`, viền xám `#cbd5e1`.
- Khối Động cơ 8 biến M8: Xanh da trời nhạt `#e0f2fe`, viền `#0284c7`.
- Khối Động cơ 5 biến M5: Tím oải hương nhạt `#f3e8ff`, viền `#7c3aed`.
- Khối Cảnh báo Thao túng (FAIL): Cam đào nhạt `#ffedd5`, viền cam cháy `#ea580c`.
- Khối Đạt chuẩn An toàn (PASS): Xanh bạc hà nhạt `#dcfce7`, viền xanh lá `#16a34a`.

### 6. ĐỘNG LỰC HỌC LUỒNG & TƯƠNG TÁC BẢNG ĐIỀU TRA (INTERACTIVE SPECIFICATIONS)
- Dynamic Flow Opacity: Luồng kiểm tra rẽ nhánh sáng rực rỡ, các nhánh phụ mờ 15%.
- Inspector Drawer: Click vào từng biến số (VD: DSRI, TATA) hiển thị chi tiết tên khoản mục tài chính theo VAS 200, công thức bóc tách và tác động lên xác suất gian lận.
```

---

### Pillar 5: Institutional Hard Laws, Multi-Tier Stop Loss & Regime Kelly Sizing (`hard_laws.py`, `stop_loss.py`, `kelly_sizer.py`)

```markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/SIGMOD standard) dưới dạng file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, mô tả chính xác Bộ ba Thể chế Quản trị Rủi ro: Hard Laws Sàn HOSE, Hệ thống Cắt lỗ Đa tầng và Phân bổ Vị thế Kelly Thích ứng Regime:

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: Institutional Risk & Capital Allocation Suite (TASK-111, TASK-312, IOS v5.1 Senior Broker Edition)
- **Mục tiêu kỹ thuật**: Đảm bảo sự tồn tại vĩnh cửu của danh mục trước rủi ro kẹt hàng thanh khoản T+2.5 của thị trường chứng khoán Việt Nam, tối ưu hóa kích thước vị thế theo tỷ lệ thắng và bảo vệ thành quả lợi nhuận.
- **Đường dẫn mã nguồn gốc đã khảo sát**:
  - `ai-engine/app/domain/rules/hard_laws.py`
  - `ai-engine/app/domain/rules/stop_loss.py`
  - `ai-engine/app/domain/rules/kelly_sizer.py`

### 2. BỐ CỤC PHÂN RÃ HAI PHA (SPATIOTEMPORAL DUAL-PHASE LAYOUT)
- **Nửa trên: PHA 1 - PRE-TRADE HARD LAW ENFORCEMENT & REGIME-SCALED KELLY**
  * Điều 1 (Luật Tồn Tại): Rủi ro tối đa vị thế \le 2\% NAV.
    Tính toán kẹt hàng 2 phiên sàn liên tiếp T+2.5 trên sàn HOSE (\pm 7\%):
    \text{Downside}_{\text{T25}} = 1 - (1 - 0.07)^2 = 13.51\%
    \text{Effective Downside} = \max(\text{StopLoss}_{\text{order}}, 13.51\%)
    \text{Risk Amount} = P \times \text{Effective Downside} \times Q \le 0.02 \times \text{NAV}
  * Điều 2 (Luật Thanh Khoản):
    - Lệnh đơn phiên: Q_{\text{order}} \le 0.15 \times \text{ADTV}_{20}
    - Tổng vị thế tích lũy: \sum Q \le 0.25 \times \text{ADTV}_{20}
  * Điều 4 (Luật Tập Trung):
    - Tỷ trọng cổ phiếu đơn lẻ: \text{Value}_{\text{stock}} \le 15\% \times \text{NAV}
    - Tỷ trọng phân ngành: \text{Value}_{\text{sector}} \le 35\% \times \text{NAV}
  * Bộ phân bổ vị thế Kelly (Quarter Kelly Baseline with Regime Scaling):
    f^* = W - \frac{1 - W}{R}, \quad f_{\text{baseline}} = 0.25 \cdot f^*
    Nhân tử trạng thái thị trường (HMM Regime Multiplier M_{\text{regime}}):
    - Bull Trending: 1.0\times
    - Bull Choppy: 0.75\times
    - Bear Bounce: 0.50\times
    - Bear Trending: 0.25\times (hoặc 1/8 Kelly)
    Trần cứng vị thế: \text{Size} = \min(f_{\text{target}}, 0.15) \times \text{NAV}.
- **Nửa dưới: PHA 2 - POST-TRADE MULTI-TIER DEFENSE & STOP-LOSS HIERARCHY**
  * Tầng 0 (Kiểm tra hàng khả dụng T+2.5 & Kẹt sàn): Phân tách Hàng khả dụng (Available Shares) vs Hàng đang về; xử lý nẹp sàn (Floor Lock).
  * Tầng 1 (Hard Stop Lỗ \ge 2\% NAV): Lệnh khẩn cấp bán sạch 100% cổ phiếu khả dụng.
  * Tầng 2 (Trailing Stop Bảo vệ Lãi): Nếu lợi nhuận đỉnh > 10\%, kích hoạt chốt lời khi giá sụt giảm \ge 35\% từ đỉnh.
  * Tầng 3 (Structural Exit Phá Hỗ Trợ): Giá thủng đáy gần nhất (Swing Low Support).
  * Tầng 4 (VSA Fast Exit): Râu nến trên chiếm > 50% biên độ nến (Bearish Rejection) kèm Khối lượng > 1.5\times \text{MA}_{20}.
  * Tầng 5 (Time Stop Chi Phí Cơ Hội): Nắm giữ > 50% thời gian kỳ vọng (VD: > 45/90 ngày) mà tỷ suất sinh lời < 2\%.
  * Quy chuẩn lô giao dịch sàn HOSE: Làm tròn xuống bội số của 100 cổ phiếu.

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- Biên độ 2 cây sàn HOSE: 13.51% (`0.1351`).
- Mức chịu đựng rủi ro danh mục: 2.0% NAV (`max_stop_loss_pct = 0.02`).
- Thanh khoản lệnh / vị thế: 15% ADTV20 và 25% ADTV20.
- Giới hạn tập trung: 15% NAV cho 1 mã, 35% NAV cho 1 ngành.
- Baseline Kelly: Quarter Kelly (`baseline_fraction = 0.25`).
- Trailing Stop Pullback: Sụt giảm \ge 35% từ đỉnh lãi.
- Fast Exit Vol: Khối lượng phiên > 1.5x MA20.

### 4. BỘ KÝ HIỆU HÌNH HỌC (DATA GLYPHS SPECIFICATION)
- Glyph Thang 6 bậc Phòng thủ (Defense Ladder): 6 tầng xếp chồng thẳng đứng từ T0 đến T5 có mã màu rủi ro.
- Glyph Đồng hồ đo Tỷ lệ Kelly: Kim chỉ phân bổ theo 4 cung Regime (Bull -> Bear).
- Nhãn toán tử trên mũi tên: [Risk <= 2% NAV], [Q <= 15% ADTV20], [Single <= 15% NAV], [Pullback >= 35% Peak], [Round Lot 100].

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE RULES)
- CẤM HOÀN TOÀN MÀU NỀN ĐEN.
- Khối Hard Laws Cổng kiểm duyệt: Cam đào nhạt `#ffedd5`, viền cam cháy `#ea580c`.
- Khối Định cỡ Kelly: Xanh da trời nhạt `#e0f2fe`, viền `#0284c7`.
- Khối Trạng thái Regime: Tím oải hương nhạt `#f3e8ff`, viền `#7c3aed`.
- Khối Lệnh thực thi An toàn: Xanh bạc hà nhạt `#dcfce7`, viền xanh lá tươi `#16a34a`.
- Khối Cắt lỗ khẩn cấp: Đỏ hồng nhạt `#ffe4e6`, viền `#e11d48`.

### 6. ĐỘNG LỰC HỌC LUỒNG & TƯƠNG TÁC BẢNG ĐIỀU TRA (INTERACTIVE SPECIFICATIONS)
- Dynamic Flow Opacity: Thể hiện rõ nét dòng chảy từ Lệnh đề xuất -> Cổng Hard Law -> Định cỡ Kelly -> Thực thi -> Phòng thủ hậu giao dịch.
- Inspector Drawer: Click vào từng tầng cắt lỗ hiển thị công thức kích hoạt, điều kiện biên, và code tham chiếu chính xác tại `rules/hard_laws.py` và `rules/stop_loss.py`.
```

---

### Pillar 6: Multimodal BCTC AST Parsing, Content Hashing & Dual-Store Ingestion (`bctc_to_sag_pipeline.py`, `document_selector.py`)

```markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/SIGMOD standard) dưới dạng file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, mô tả chính xác luồng nạp và xử lý tài liệu tài chính đa phương thức BCTC to SAG Pipeline:

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: Multimodal Financial Document Parsing, Line-Level Content Hashing & Dual-Store RAG Ingestion Pipeline
- **Mục tiêu kỹ thuật**: Chuyển hóa các tệp PDF BCTC và Báo cáo quản trị tiếng Việt phức tạp thành tri thức cấu trúc và không gian vector định lượng, bảo toàn trọn vẹn số liệu kiểm toán, chống hallucination bằng cơ chế băm dòng SHA-256.
- **Đường dẫn mã nguồn gốc đã khảo sát**:
  - `ai-engine/app/domain/pipeline/bctc_to_sag_pipeline.py`
  - `ai-engine/app/domain/services/document_selector.py`
  - `ai-engine/app/domain/services/r2_storage.py`
  - `ai-engine/app/adapters/sag_connector.py`

### 2. BỐ CỤC PHÂN RÃ HAI PHA (SPATIOTEMPORAL DUAL-PHASE LAYOUT)
- **Nửa trên: PHA 1 - OFFLINE BATCH INGESTION, OCR & DUAL PERSISTENCE**
  * Tuyển chọn Bộ 3 Tài liệu Vàng (Golden Document Trinity Selection):
    - Annual Audited Backbone (Kiểm toán cả năm)
    - Latest Quarter (BCTC quý gần nhất)
    - Governance Report (Báo cáo Quản trị bán niên / năm)
  * Bộ giải mã tài liệu sâu MinerU OCR Engine:
    - Trích xuất bảng biểu đa tầng phức tạp (Complex Multi-level Financial Tables).
    - Phân tích cú pháp Cây cú pháp trừu tượng Markdown AST (Tree Structure Parsing).
  * Băm dòng chống bịa đặt (Content Hashing):
    \text{quote\_hash} = \text{SHA-256}(\text{line\_text})
  * Phân đoạn ngữ nghĩa & Chiếu Không gian Kép:
    - Chiếu Dense Vector Embedding (1024 chiều) mô tả ngữ nghĩa chú thuyết.
    - Chiếu Siêu đồ thị Bipartite Hypergraph M:N (Doanh nghiệp - Chỉ tiêu - Kỳ báo cáo - Giá trị VND).
  * Lưu trữ Kép Bất biến (Dual Store Persistence):
    - PostgreSQL Relational Store: Bảng cân đối, kết quả kinh doanh, lưu chuyển tiền tệ chuẩn VAS.
    - Vector & Graph Engine: Milvus/pgvector và Đồ thị quan hệ pháp nhân GIL.
- **Nửa dưới: PHA 2 - ONLINE REAL-TIME QUERY INFERENCE & REASONING PATH**
  * Nhận tín hiệu / Câu hỏi truy vấn từ Quản lý danh mục.
  * Phân nhánh song song Fork-Join:
    - Nhánh Vector Tìm kiếm Tương đồng Cosine: S_{\text{vector}} = \frac{\mathbf{q}\cdot\mathbf{d}}{\|\mathbf{q}\|\|\mathbf{d}\|}
    - Nhánh Lexical Tra cứu Số liệu Thực nghiệm: Khớp chính xác mã chỉ tiêu VAS.
    - Nhánh Forensic Integrity Gate: Kiểm tra dấu vết băm quote_hash.
  * Bộ tích lũy Hàng đợi Nén (Dynamic Queue Accumulator):
    Nén từ 32 đoạn trích ban đầu \to 24 ứng viên lọc biên \to 8 bằng chứng vàng tối thượng (Top-8 Evidence Pack).
  * Đóng gói Context đẩy vào Multi-Agent CIO Reasoning Engine.

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- Đồng thời nạp OCR: `SAG_OCR_ACTIVE_JOBS = 20`, Upload: `20`, Extraction: `3`.
- Không gian vector nhúng: 1024 chiều.
- Độ băm toàn vẹn: SHA-256 (64 hex characters).
- Ngưỡng lọc độ tương đồng: S_{\text{raw}} \ge \max(0.35, 0.68 \cdot S_{\text{top}}).
- Tỷ lệ nén hàng đợi ngữ cảnh: 32 \to 24 \to 8 chunks.

### 4. BỘ KÝ HIỆU HÌNH HỌC (DATA GLYPHS SPECIFICATION)
- Glyph Cây AST Markdown: Cấu trúc phân nhánh cây từ tiêu đề đến bảng số liệu.
- Glyph Thẻ Hash SHA-256: Huy hiệu ổ khóa bảo mật mang mã băm trên từng hàng dữ liệu.
- Glyph Không gian Vector 3D & Siêu đồ thị: Trục tọa độ 3D kết hợp lưới chấm tròn đan xen.
- Nhãn toán tử trên mũi tên: [PDF to AST], [SHA-256], [1024-d Dense], [M:N Hypergraph], [Queue 32->24->8].

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE RULES)
- CẤM NỀN ĐEN.
- Trạm BCTC PDF & AST: Trắng tinh khiết `#ffffff`, viền xám `#cbd5e1`.
- Trạm MinerU OCR: Tím oải hương nhạt `#f3e8ff`, viền `#7c3aed`.
- Trạm Vector hóa: Xanh da trời nhạt `#e0f2fe`, viền `#0284c7`.
- Trạm Bằng chứng Xác minh: Xanh bạc hà nhạt `#dcfce7`, viền `#16a34a`.

### 6. ĐỘNG LỰC HỌC LUỒNG & TƯƠNG TÁC BẢNG ĐIỀU TRA (INTERACTIVE SPECIFICATIONS)
- Dynamic Flow Opacity: Luồng nạp chạy từ file gốc đến kho lưu trữ và phản xạ truy vấn sáng bừng theo từng bước.
- Inspector Drawer: Click vào trạm lưu trữ hoặc trạm trích xuất hiển thị cấu trúc dữ liệu JSON, cơ chế băm dòng và API endpoint kết nối tương ứng.
```

---

### Pillar 7: The Master Macro Blueprint (Unified Full-Stack Quant Architecture)

```markdown
Hãy tạo một sơ đồ kiến trúc khoa học vĩ mô tương tác đỉnh cao đạt chuẩn xuất bản quốc tế (NeurIPS/ICLR/SIGMOD standard) dưới dạng file HTML độc lập chứa inline SVG, CSS hiện đại và JavaScript điều khiển, hợp nhất toàn bộ 6 trục giải thuật của nền tảng AIInvest ai-engine thành một Bản Thiết Kế Tổng Thể Định Lượng Thích Ứng (Unified Adaptive Quantitative & Forensic Alpha Pipeline):

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống**: AIInvest Full-Stack Algorithmic & Risk Architecture
- **Mục tiêu**: Tích hợp toàn diện dòng chảy dữ liệu từ nạp dữ liệu thô (BCTC, Tick OHLCV) -> Trích xuất đặc trưng vi phân phân số FFD -> Lan truyền cú sốc đồ thị 15 ngành/9 tập đoàn -> Trung hòa & trực giao hóa nhân tố -> Kiểm định IC -> Cổng chặn gian lận Beneish -> Phân bổ Kelly thích ứng Regime -> Cổng Hard Law & Thực thi sàn HOSE.
- **Mã nguồn tích hợp**: `ai-engine/app/` (Domain, Services, Rules, Pipelines, Backtest).

### 2. BỐ CỤC PHÂN TẦNG VĨ MÔ
- **Tầng 1: Lớp Cảm Ứng Dữ Liệu & Biến Đổi Vật Lý (Physical Sensing & Ingestion Layer)**
  * Nạp BCTC đa phương thức -> MinerU OCR -> Markdown AST -> SHA-256 Hash -> Dual Store (PostgreSQL + Vector 1024-d).
  * Nạp OHLCV Tick Stream -> FFD Convolve (Optimal d*, threshold 1e-5) -> Chuỗi dừng bảo toàn ký ức dài hạn.
- **Tầng 2: Lớp Trí Tuệ Đồ Thị & Lan Truyền Không Gian (Spatial & Graph Contagion Layer)**
  * Chiếu 406 mã cổ phiếu vào Đồ thị định hướng 15 phân ngành và 9 Tập đoàn tài phiệt.
  * Tính toán Xung lực Leader Shock (Shift 1, 2) và Thế năng bắt kịp 3 ngày.
- **Tầng 3: Lớp Kỹ Thuật Nhân Tố Định Lượng (Quantitative Factor Engineering Layer)**
  * Nhận diện phân phối -> Winsorize [1%, 99%] -> Safe Sector Z-Score.
  * Cụm tương quan -> Trực giao hóa Gram-Schmidt / Within-group PCA -> Đánh giá Rank IC & Decile Spread.
- **Tầng 4: Lớp Thể Chế Phủ Quyết & Giám Định Pháp Y (Forensic & Institutional Veto Layer)**
  * Mô hình Thích ứng Kép Beneish M-Score (M8 vs M5, ngưỡng -1.78).
  * Bộ ba Hard Laws sàn HOSE (Điều 1: Rủi ro 2% NAV kèm trần sàn 13.51%, Điều 2: Thanh khoản 15%/25% ADTV20, Điều 4: Tập trung 15% mã / 35% ngành).
- **Tầng 5: Lớp Tối Ưu Phân Bổ & Thực Thi Thích Ứng (Regime Allocation & Execution Layer)**
  * Nhận diện Market Regime -> Quarter Kelly scaling -> Giới hạn trần 15% NAV.
  * Khớp lệnh lô chẵn 100 sàn HOSE -> Kích hoạt Thang 6 bậc Cắt lỗ Phòng thủ hậu giao dịch.

### 3. QUY CHUẨN THỊ GIÁC & TƯƠNG TÁC
- 100% Pastel Tươi Sáng (Xanh da trời, Tím oải hương, Xanh bạc hà, Cam đào, Trắng ngà). TUYỆT ĐỐI CẤM NỀN ĐEN.
- Dynamic Flow Opacity (Step Motion dimming 0.15 vs 1.0).
- Inspector Drawer trượt bên phải mở rộng đầy đủ công thức toán, hằng số biên và đường dẫn code khi click vào từng trạm giải thuật.
```

---

### Pillar 8: The 12-Agent Sovereign Quantitative Organization (IOS v5.1 Multi-Agent Architecture)

```markdown
Hãy tạo một sơ đồ kỹ thuật tương tác đạt chuẩn bài báo khoa học quốc tế (NeurIPS/ICLR/SIGMOD standard) dưới dạng một file HTML độc lập chứa inline SVG và CSS hiện đại, mô tả chính xác kiến trúc Tam Giác Quyền Lực & Phân Lập Thể Chế của Hệ thống 12 Agent Định Lượng Tự Hành (AIInvest Sovereign Quantitative Organization):

### 1. TỔNG QUAN HỆ THỐNG & ĐỐI TƯỢNG BÓC TÁCH TỪ GỐC
- **Hệ thống / Module**: 12 Multi-Agent Semantic Registry Package & Daily Investment Pipeline (IOS v5.1).
- **Mục tiêu kỹ thuật**: Hiện thực hóa cơ chế kiểm soát chéo và cân bằng quyền lực (Checks & Balances) giữa 12 Agent nghiệp vụ độc lập, loại bỏ triệt để hiện tượng "Agent ảo giác", tự ý bẻ cong rủi ro, và hiện tượng mù danh mục (Portfolio Blindness).
- **Đường dẫn mã nguồn gốc đã khảo sát**:
  - `ai-engine/app/domain/agents/` (Full 12 Agent implementation files).
  - `ai-engine/app/domain/pipeline/daily_pipeline_orchestrator.py` (Chu trình điều phối 12 pha khép kín).
  - `ai-engine/app/eval/audit_trail.py` (Sổ cái mật mã SHA-256 Hash Chaining).

### 2. BỐ CỤC PHÂN RÃ BỐN TẦNG THỂ CHẾ (SPATIOTEMPORAL 4-LAYER SOVEREIGN LAYOUT)
- **Tầng 1 (Đỉnh - Phía Trên): GIÁM SÁT VĨ MÔ & KHÁM PHÁ UNIVERSE LỚP 0**
  * Agent-01 (Market Surveillance): Quan sát 6 phiên HOSE, Sticky HMM 3-State Regime, GJR-GARCH(1,1), CSAD Herding bầy đàn, đo méo mó VN30 "Xanh vỏ đỏ lòng" (A/D < 0.4) và sóng sàn bán tháo (<= -6.9%).
  * Agent-02 (Universe Discovery): Sàng lọc 406 mã HOSE, Hard Filters (normal trading, unqualified audit, ADTV20 >= 15 tỷ VND), Cổng Lớp 0 Beneish M-Score <= -1.78, Đồ thị GIL loại trừ rút ruột vốn.
- **Tầng 2 (Trung Tâm - Phía Giữa): NGHIÊN CỨU ĐA NHÂN TỐ, TRANH BIỆN ĐỐI NGHỊCH & TRỌNG TÀI CIO**
  * Agent-03 (Equity Research): Chấm điểm 6 nhóm Factor F1-F6 (Value, Quality, Momentum, Earnings, Flow, Technical), Composite Stock Score (CSS), phân định Conviction Tiers {A+, A, B}.
  * Agent-04 (Investment Thesis): Thiết lập Luận điểm đầu tư, tuân thủ Hiến pháp Điều 3 (3 Tín hiệu Độc lập), định giá đa mô hình P_target, 3 kịch bản Pre-Mortem và điều kiện hủy bỏ Invalidation.
  * Agent-05 (Counter Thesis - Devil's Advocate): Tính điểm phản biện CTS 3 tầng (Base + ML) * M_regime, quét 4 bẫy (thanh khoản, FOMO, bull-trap, giải chấp), phán quyết {PROCEED, CONDITIONAL, BLOCK}.
  * Agent-12 (Strategy CIO): Trọng tài Tối cao phân định xung đột Thesis vs Counter-Thesis, cấp phép ngoại lệ có biên (<= 5% NAV, <= 48h), quyết định trần tỷ trọng weight_cap, băm SHA-256 Chained Resolution.
- **Tầng 3 (Hạ Tầng - Phía Dưới): ĐỊNH CỠ KELLY, CỔNG RỦI RO PRE-TRADE, KHỚP LỆNH & CANH GÁC VỊ THẾ**
  * Agent-06 (Portfolio Allocation): 8 Engine điều phối chuẩn định chế, định cỡ vị thế Quarter Kelly f* = 0.25 (p - (1-p)/R) co giãn theo Regime, áp trần Điều 4 (mã <= 15%, ngành <= 35% NAV), làm tròn lô chẵn 100 sàn HOSE.
  * Agent-07 (Portfolio Risk): Cổng Thẩm định Rủi ro Tối cao Pre-trade 5 lớp (Hard Laws, đệm rủi ro kẹt T+2.5 13.51%, dị thường nến VSA, EGARCH-t CVaR 97.5% df=5, CDC Controller khi IC Decay >= 50%).
  * Agent-08 (Trade Execution): Động cơ EAE khớp lệnh thích ứng, tuân thủ bước giá HOSE (10đ, 50đ, 100đ), trần lệnh 500k, hạ nhãn ADTV động, Failsafe Guard 1500ms, chế độ kép LIVE vs SHADOW.
  * Agent-09 (Position Monitoring): Canh gác thời gian thực chu kỳ 5 phút, phân tách T+2.5 (Available vs Locked), thang 6 bậc dừng lỗ T0-T5 (Hard stop 2% NAV, Trailing 35%), SLA 14:00 Auto-Exit.
- **Tầng 4 (Vòng Lặp Hồi Tiếp & Causal Learning - Khép Kín Hệ Thống)**:
  * Agent-11 (System Governance): Tam Giác Quyền Lực (Compliance - Audit - Change), sổ cái bất biến SHA-256 liên tục có khóa giao dịch Advisory Lock, kiểm định toàn chuỗi Full Chain Verifier.
  * Agent-10 (Reinforcement Learning & Causal Adaptation): Đo lường Spearman Rank IC đa chân trời, hiệu chuẩn xác suất Bayes (Empirical Bayes Shrinkage N0=25), chẩn đoán 4 nguyên nhân IC decay, OOS Gatekeeper (Sharpe >= 1.2, DD <= 10%).

### 3. CÁC THAM SỐ BIÊN & CÔNG THỨC TOÁN ĐỊNH LƯỢNG (GROUND TRUTH TỪ CODE)
- Công thức Sticky HMM & GJR-GARCH(1,1) Volatility: \sigma_t^2 = \omega + (\alpha + \gamma \mathbb{I}(\epsilon_{t-1} < 0)) \epsilon_{t-1}^2 + \beta \sigma_{t-1}^2.
- Công thức CSAD Herding (Chang et al. 2000): CSAD_t = \alpha + \beta_1 |R_{m,t}| + \beta_2 R_{m,t}^2 (\beta_2 < 0 \implies Herding).
- Ngưỡng Lớp 0 Beneish M-Score: M \le -1.78 (VAS Thông tư 200/BTC).
- Công thức Phản biện CTS: CTS = \min(100.0, (CTS_{\text{base}} + \Delta_{\text{ML}}) \times M_{\text{regime}}).
- Công thức Quarter Kelly: f^* = 0.25 (p - \frac{1-p}{R}), áp trần Điều 4 Hiến pháp \le 15\% NAV, làm tròn lô chẵn 100 sàn HOSE.
- Hệ số đệm kẹt sàn T+2.5: \text{Downside}_{T25} = 1 - (1 - 0.07)^2 = 13.51\%, Risk \le 2\% NAV.
- Conditional Tail Expected Shortfall (EGARCH-t, df=5): ES_{97.5\%} = \sigma_{\text{EGARCH}} \times 3.37.
- Hiệu chuẩn Co ngót Bayes: p_{\text{calibrated}} = \frac{N_0}{N_0 + N} p_{\text{prior}} + \frac{N}{N_0 + N} p_{\text{sample}} với N_0 = 25.0 lệnh.
- Sổ cái Băm Chuỗi Khối SHA-256: Block_k = \text{SHA-256}(Block_{k-1} \parallel Timestamp \parallel AgentID \parallel Event \parallel Details).

### 4. BỘ KÝ HIỆU HÌNH HỌC & ĐƯỜNG NỐI (GLYPHS & ORTHOGONAL CONNECTORS)
- 100% đường nối sử dụng orthogonal right-angle elbows (r=8).
- Nhãn toán tử bo góc (Pill Badges) trên đường nối: [Regime Flow], [CSS >= 65], [CTS Score], [Order f*], [Approved w_cap], [FILLED], [AUDIT].
- Vòng lặp hồi tiếp nét đứt (Dashed Lines) biểu thị chu trình học tăng cường Agent-10 và giám sát sổ cái Agent-11.

### 5. QUY TẮC MÀU SẮC "TƯƠI NHƯNG NHẠT" (FRESH PASTEL PALETTE)
- TUYỆT ĐỐI KHÔNG DÙNG NỀN ĐEN CHO ITEM/NODE.
- Nền Canvas: #fafafa kèm họa tiết chấm lưới kỹ thuật.
- Nền Node phân định chức năng:
  * Surveillance & Research (Agent 01, 02, 03, 04, 09): Xanh da trời nhạt (#e0f2fe), viền xanh đậm (#0284c7).
  * Devil's Advocate & Risk (Agent 05, 07): Cam đào nhạt (#ffedd5), viền cam cháy (#ea580c).
  * CIO Arbitration (Agent 12): Hồng đào nhạt (#ffe4e6), viền đỏ tươi (#e11d48).
  * Kelly Allocation & Execution (Agent 06, 08): Xanh bạc hà nhạt (#dcfce7), viền xanh lá đậm (#16a34a).
  * Reinforcement Learning (Agent 10): Tím oải hương nhạt (#f3e8ff), viền tím đậm (#7c3aed).
  * System Governance (Agent 11): Xám ngà thanh lịch (#f1f5f9), viền slate (#64748b).
- Chữ: Navy/Slate đậm (#0f172a), tương phản cao, sắc nét.

### 6. TƯƠNG TÁC BẢNG ĐIỀU TRA (INSPECTOR DRAWER)
- Hỗ trợ bộ lọc tầng kiến trúc (Layer Filter Switcher) làm mờ các tầng không chọn về opacity 0.16.
- Nhấp vào bất kỳ Agent nào trượt ra bảng điều tra toán học chi tiết (Inspector Drawer) hiển thị:
  1. Trách nhiệm thể chế & cơ chế vận hành.
  2. Công thức toán học ngầm & LaTeX.
  3. Tham số biên và điều kiện cứng khai quật từ mã nguồn.
  4. Hình thái dữ liệu vào/ra (Data Morphology).
  5. Đường dẫn mã nguồn thực tế trong repo Clean Architecture.
```

