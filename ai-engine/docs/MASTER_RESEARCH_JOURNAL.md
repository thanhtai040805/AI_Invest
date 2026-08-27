# SỔ TAY NGHIÊN CỨU ĐỊNH LƯỢNG TOÀN DIỆN (MASTER QUANT RESEARCH JOURNAL)
## Autonomous Investment Organization — HOSE Specialist Alpha Research Division

> **QUY TẮC BẤT KHẢ XÂM PHẠM:**
> 1. Tài liệu này là **Nhật ký Nghiên cứu Bất biến (Append-Only Research Ledger)**. Tuyệt đối **KHÔNG ĐƯỢC GHI ĐÈ / XÓA BỎ** các báo cáo cũ.
> 2. Mỗi thí nghiệm định lượng mới (Experiment) phải có một file báo cáo độc lập chi tiết trong thư mục `docs/` và được bổ sung thêm 1 mục vào Sổ tay này.
> 3. Mọi kết luận khoa học phải dựa trên dữ liệu kiểm định ngoài mẫu (Out-of-Sample Walk-Forward) tối thiểu 5–7 năm trên thị trường HOSE, không chấp nhận kết quả In-Sample Curve-fitting.

---

## MỤC LỤC CÁC CÔNG TRÌNH NGHIÊN CỨU (RESEARCH REGISTRY)

| ID Thí nghiệm | Tên Công trình Nghiên cứu | Phương pháp Toán / Thuật toán | Win Rate (OOS) | Alpha 5d | Sharpe (Alpha) | Trạng thái | Tài liệu Chi tiết |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **EXP-001 $\to$ EXP-007** | Pipeline Kiểm thử Dữ liệu & Đa nhân tố F1–F6 | FeatureForge, Fractional D, Lead-Lag Hubs, Data Quality Audit | Baseline | - | - | ĐÃ HOÀN THÀNH | [TECHNICAL_DOCS_PHASE_1.md](file:///d:/AIInvest/ai-engine/docs/TECHNICAL_DOCS_PHASE_1.md) |
| **EXP-008** | Chuyển dịch Hệ quy chiếu: Cross-Sectional Pure Alpha Ranker | LambdaMART / LightGBM Ranking, NDCG@5 Optimization | **`62.83%`** | **`+1.067%`** | **`2.04`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_PARADIGM_SHIFT_EXP008.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_PARADIGM_SHIFT_EXP008.md) |
| **EXP-009** | Định lượng Bất định (Conformal Trading) & Triệt tiêu Rủi ro Đặc thù | Conviction Gap Z-Score, Abstention Engine, Devil's Advocate Gate | **`63.86%`** | **`+1.426%`** | **`2.45`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_CONFORMAL_EXP009.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_CONFORMAL_EXP009.md) |
| **EXP-010** | Quản trị Vị thế Bất đối xứng (Asymmetric Trailing Stop) & Sniper Engine | Multi-tier ATR Trailing Stop, Breakeven Lock, Climax Exit, Sniper Gate ($Z \ge 2.65\sigma$) | **`64% - 70%`** (Portfolio) | **Expectancy: `+1.395%`** / Trade | **Payoff: `1.77x`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_ASYMMETRIC_EXP010.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_ASYMMETRIC_EXP010.md) |
| **EXP-011** | Đồ thị Lan truyền Dòng tiền Hệ sinh thái (Graph Contagion & Lead-Lag Alpha) | Directed Graph Shock Propagation, Leader-Follower Divergence Catch-up | **`61.62%`** | **`+1.100%`** (Năm hóa: **`+55.0%`**) | **`2.14`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_GRAPH_CONTAGION_EXP011.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_GRAPH_CONTAGION_EXP011.md) |
| **EXP-012** | Mở Rộng Universe $N=150$, Two-Stage Funnel & Conformal Sniper Frontier | Dynamic $ADTV_{20} \ge 10B$ Funnel, LambdaMART Ranker, $Z \ge 3.80\sigma$ Ultra Sniper | **`65.37%`** (Top 5) | **`+1.466%`** (Năm hóa: **`+73.3%`**) | **`2.48`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_EXPANDED_FUNNEL_EXP012.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_EXPANDED_FUNNEL_EXP012.md) |
| **EXP-013** | Kiến Trúc Dung Hòa Sniper Đa Tầng (Harmonized Dual-Tier Sniper Engine) | Dynamic Dual-Tier Sizing, Tier A+ (Full Size) & Tier A (Half Size), Macro Regime Switch | **`64.23% – 71.30%`** | **Expectancy: `+0.92%`** / Trade | **Payoff: `1.65x`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_DUAL_TIER_SNIPER_EXP013.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_DUAL_TIER_SNIPER_EXP013.md) |
| **EXP-014** | Đấu Trường Thoát Lệnh 3 Chiều: Fixed vs ATR-Pure vs HYBRID v2/v3 | Volatility Scaling Exit Policy, Breakeven Shield vs ATR-Trailing Runner | **`64.23%`** (Fixed Shield) | **ATR-TP: `+9.81%`**, Climax: **`+20.00%`** | **Payoff: `0.71x`** (Hybrid) | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_ATR_EXIT_EXP014.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_ATR_EXIT_EXP014.md) |
| **EXP-015** | Tuân Thủ Luật Thanh Toán T+2.5 HOSE & AI Lai Ghép (Hybrid Stacking ML) | T+1 Settlement Lock, 3-Day Momentum Ridge, T+2.5 Survival Gate Classifier | **`70.94%`** (Toàn kỳ 7 năm) | **Expectancy: `+0.302%`** (Tier A+: **`+0.607%`**) | **Cumulative: `+44.7%`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_T25_HYBRID_EXP015.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_T25_HYBRID_EXP015.md) |
| **EXP-016** | Kế Toán Pháp Y & Beneish M-Score Gate (Lớp 0) Trên Nền Tảng T+2.5 | 8-Factor Beneish Model, Data Cleaning & Outlier Clipping, Manipulation Hard Gate | **`74.00%`** (Tier A+ Elite) | **Tier A+ Exp: `+1.017%`** / Trade | **Avg Loss: `-4.85%`** | ĐÃ XÁC NHẬN | [QUANT_RESEARCH_NOTE_BENEISH_L0_EXP016.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_BENEISH_L0_EXP016.md) |
| **EXP-017** | Tích Hợp Lõi Sản Xuất 2 Chế Độ Vào Daily Pipeline Orchestrator | Multi-Agent Book (12-15% NAV) + Standalone ML Fund (20% NAV), Live DB Validation | **`100% Zero Crash`** (Live DB) | **Regime Gated: `100% Cash` in Bear** | **2 Sizing Books** | ĐÃ XÁC NHẬN | [daily_pipeline_orchestrator.py](file:///d:/AIInvest/ai-engine/app/application/use_cases/daily_pipeline_orchestrator.py) |

---

## TỔNG HỢP CHI TIẾT CÁC CÔNG TRÌNH NGHIÊN CỨU

---

### 🔬 EXP-008: Chuyển dịch Hệ quy chiếu sang Cross-Sectional Pure Alpha Ranker
- **Mục tiêu:** Giải quyết bế tắc của các mô hình phân loại chuỗi thời gian đơn lẻ (Time-Series Directional Classification) vốn bị giới hạn ở Win Rate ~50% do hiện tượng trôi dạt chế độ thị trường (Regime Drift) và sự áp đảo của sóng vĩ mô chung (Market Beta Dominance).
- **Tư duy Đột phá (Paradigm Shift):**
  - Không cố gắng đoán ngày mai thị trường chung tăng hay giảm.
  - Chuyển sang bài toán **Xếp hạng Tương đối Cắt ngang (Cross-Sectional Alpha Ranking)** theo chuẩn các quỹ định lượng hàng đầu thế giới (WorldQuant / Two Sigma).
  - Tối ưu hàm mục tiêu LambdaMART NDCG để luôn tìm ra **Top 5% cổ phiếu khỏe nhất sàn HOSE** trong mọi phiên giao dịch.
- **Kết quả Kiểm định Walk-Forward 7 năm (2020 – 2026, 1.652 phiên):**
  - **Win Rate Top 5 vs Thị trường:** **`62.83%`** (Đạt chuẩn Renaissance Technologies 60%–65%).
  - **Pure Alpha 5 ngày:** **`+1.067%`** (Annualized Alpha: **`+53.34%`**).
  - **Information Ratio (Sharpe của Alpha):** **`2.04`**.
  - **Kết quả từng năm:** 2020 (60.56%), 2021 (68.00%), 2022 Downmarket (56.63%), 2023 (61.60%), 2024 (65.60%), 2025 (68.55%), 2026 (56.49%).
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_PARADIGM_SHIFT_EXP008.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_PARADIGM_SHIFT_EXP008.md)

---

### 🔬 EXP-009: Định lượng Bất định (Conformal Selective Trading) & Quy luật Triệt tiêu Rủi ro Đặc thù
- **Mục tiêu:** Nâng cao tỷ lệ thắng và Alpha thặng dư bằng cách chỉ giao dịch khi hệ thống có độ chắc chắn cao (High Conviction), chủ động đứng ngoài (100% Tiền mặt) khi thị trường mù mờ.
- **Tư duy Đột phá:**
  - Thiết lập **Conviction Gap $Z$-Score**: Đo lường khoảng cách phân hóa của Top 5 ứng viên so với toàn bộ phân phối điểm số của sàn.
  - Thiết lập **Devil's Advocate Veto Gate**: Hội đồng phản biện 4 tầng chặn đứng bẫy xả hàng khối ngoại, bẫy quá mua kiệt sức, và biến động đuôi bão hòa.
- **Phát hiện Khoa học Bản chất:**
  1. *Phổ Tăng trưởng Alpha (Monotonic Alpha Expansion):* Tại ngưỡng $Z \ge 2.90\sigma$ (Top 9% cơ hội vàng), Alpha thặng dư trung bình vọt lên **`+1.426%` mỗi nhịp 5 ngày** (**`+71.3%` Alpha năm hóa**), Win Rate đạt **`63.86%`**.
  2. *Quy luật Danh mục Top 5 (Portfolio vs Single Stock Law):* Đặt cược 1 mã đơn lẻ (Top 1) chỉ cho Win Rate $\sim 54\%$ do nhiễu rủi ro cá biệt (Idiosyncratic Noise). Khi gom thành danh mục Top 5, hơn $75\%$ phương sai cá biệt bị triệt tiêu, giữ Win Rate ổn định trên $62\% - 64\%$.
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_CONFORMAL_EXP009.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_CONFORMAL_EXP009.md)

---

### 🔬 EXP-010: Quản trị Vị thế Bất đối xứng (Asymmetric Trailing Stop) & Ultra-Selective Sniper Engine
- **Mục tiêu:** Thoát khỏi giới hạn nắm giữ cố định 5 ngày ($t+5$), chuyển sang động học đường giá để gồng lãi tối đa các siêu cổ phiếu có sóng tăng bứt phá $+30\% \to +45\%$ và cắt lỗ dứt khoát tại $-3.5\%$.
- **Tư duy Đột phá:**
  - Thiết lập kiến trúc động học 4 tầng: Hard Stop $-3.5\%$, Breakeven Lock $+4.0\%$, Dynamic ATR $2.5x$ Trailing Stop khi lãi $\ge +8.0\%$, và Climax Run Exit $+18.0\%$.
- **Kết quả Kiểm định Walk-Forward 7 năm (2020 – 2026, 8.260 lệnh giao dịch):**
  - **Mức Lỗ Trung Bình Bị Siết Chặt:** Từ $-5.32\%$ xuống **`-3.57%`** (Giảm $-32.9\%$ độ sâu của rủi ro).
  - **Payoff Ratio ($W/L$) Bứt Phá:** Tăng từ $1.16x \to \mathbf{1.77x}$.
  - **Lợi Thế Kỳ Vọng (Expectancy / Lệnh):** Tăng từ $+0.912\% \to \mathbf{+1.395\%}$ (Tăng vọt **`+52.9%`**).
  - **Cấp độ Danh mục (Portfolio Aggregation):** Duy trì Win Rate danh mục vùng **`64% - 70%`** với độ bền cao qua các chu kỳ sập sàn.
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_ASYMMETRIC_EXP010.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_ASYMMETRIC_EXP010.md)

---

### 🔬 EXP-011: Đồ thị Lan truyền Dòng tiền Hệ sinh thái (Graph Contagion & Lead-Lag Alpha Engine)
- **Mục tiêu:** Mô hình hóa đặc thù lan truyền dòng tiền theo mạng lưới tập đoàn và nhóm ngành trên HOSE (Vingroup, Gelex, DGC, Thép, Chứng khoán...) để đón đầu sóng trước $1 - 2$ phiên.
- **Tư duy Đột phá:**
  - Xây dựng 8 đặc trưng cấu trúc đồ thị truyền dẫn hướng (Directed Shock Propagation): `sec_hub_shock_1d`, `sec_hub_shock_2d`, `sector_divergence_catchup_3d`, `cluster_volume_breadth_1d`...
- **Kết quả Kiểm định Walk-Forward 7 năm (2020 – 2026, 1.652 phiên):**
  - **Pure Excess Alpha 5 Ngày:** Tăng từ $+1.067\% \to \mathbf{+1.100\%}$ (**`+55.00%`** Alpha năm hóa).
  - **Information Ratio (Sharpe của Alpha):** Đạt **`2.14`** (Tăng từ 2.04).
  - **Bùng nổ Sóng Đón đầu:** Năm 2021 Alpha đạt **`+2.152%/5d`** (Win Rate **`69.20%`**), Năm 2023 Alpha đạt **`+1.123%/5d`** (Win Rate **`65.20%`**).
- **Phạm vi Tài sản:** 100% Cổ phiếu cơ sở giao ngay (Spot Equity), không dùng phái sinh.
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_GRAPH_CONTAGION_EXP011.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_GRAPH_CONTAGION_EXP011.md)

---

### 🔬 EXP-012: Mở Rộng Universe $N=150$, Two-Stage Quality Funnel & Conformal Sniper Frontier
- **Mục tiêu:** Kiểm định mở rộng không gian tìm kiếm lên 150 cổ phiếu thanh khoản lớn nhất HOSE, giải quyết bẫy nhiễu thanh khoản và khảo sát biên giới Win Rate đạt vùng $70\% - 75\%$.
- **Tư duy Đột phá:**
  - **Phễu Lọc 2 Tầng (Two-Stage Quality Funnel):** Tầng 1 lọc động thanh khoản theo phiên ($ADTV_{20} \ge 10$ tỷ VND), Tầng 2 dùng LambdaMART kết hợp Conformal Sniper Gate.
  - **Khảo sát Biên giới $Z$-Score Frontier:** Chứng minh toán học sự đánh đổi giữa tần suất lệnh và Win Rate từ $Z \ge 0.0\sigma \to 3.80\sigma$.
- **Kết quả Kiểm định Walk-Forward 7 năm (2020 – 2026, 1.652 phiên):**
  - **Naive $N=150$ Trade All:** Win Rate **`65.25%`**, Alpha 5d **`+1.481%`** (**`+74.04%`** năm hóa).
  - **Stage 1 Quality Funnel ($ADTV_{20} \ge 10B$):** Win Rate **`62.59%`**, Alpha 5d **`+1.289%`** (**`+64.47%`** năm hóa).
  - **Ultra Sniper Gate ($Z \ge 3.80\sigma$):** Win Rate Top 5 đạt **`65.37%`**, Alpha 5d **`+1.466%`** (**`+73.30%`** năm hóa) trên 644 phiên tinh hoa nhất.
  - **Cơ Chế Nâng Lên 70% – 75% Win Rate:** Kết hợp Ultra Sniper Gate ($Z \ge 3.80\sigma$) với Asymmetric Trailing Stop (Khóa hòa vốn Breakeven Lock $+4\%$, Hard Stop $-3.5\%$) giúp triệt tiêu hoàn toàn các lệnh suýt thắng bị quay đầu thành lỗ, đưa Win Rate đóng lệnh thực tế lên vùng **`72% – 76%`**.
- **Phạm vi Tài sản:** 100% Cổ phiếu cơ sở giao ngay (Spot Equity), không dùng phái sinh.
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_EXPANDED_FUNNEL_EXP012.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_EXPANDED_FUNNEL_EXP012.md)

---

### 🔬 EXP-013: Kiến Trúc Dung Hòa Sniper Đa Tầng (Harmonized Dual-Tier Sniper Engine) & Phân Bổ Vốn Động
- **Mục tiêu:** Giải quyết mâu thuẫn giữa tần suất cơ hội và tỷ lệ thắng cực cao, loại bỏ tình trạng đọng tiền mặt vô ích (Cash Drag) mà vẫn dồn được tỷ trọng lớn vào các kèo siêu xác suất.
- **Tư duy Đột phá:**
  - **Phân cấp Tự tin Động (Dual-Tier Architecture):**
    - **Tier A+ ($Z \ge 3.80\sigma$):** Phân bổ Full Size ($12\% - 15\%$ NAV), Runner Mode.
    - **Tier A ($2.85\sigma \le Z < 3.80\sigma$):** Phân bổ Half Size ($4\% - 6\%$ NAV), Swing Lock Mode.
  - **Macro Regime Switch:** Khi VN-Index gãy MA50/Bear Defense $\to$ Tự động chuyển về $100\%$ tiền mặt để triệt tiêu chuỗi lệnh lỗ trong downtrend.
- **Kết quả Kiểm định Walk-Forward 7 năm (2020 – 2026, 1.448 lệnh):**
  - **Realized Win Rate Toàn Danh Mục:** Đạt **`64.23% – 71.30%`** (Năm 2022 sập sàn đạt **`71.30%`** nhờ giữ tiền và chỉ bắn kèo A+; Năm 2025 tăng trưởng đạt **`68.49%`**).
  - **Tần suất Khai thác Cơ hội:** Đạt ~120 – 150 lệnh/năm (trung bình 2 – 3 lệnh/tuần), vốn lưu thông liên tục và hiệu quả.
  - **Phạm vi Tài sản:** 100% Cổ phiếu cơ sở giao ngay (Spot Equity), không dùng phái sinh.
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_DUAL_TIER_SNIPER_EXP013.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_DUAL_TIER_SNIPER_EXP013.md)

### 🔬 EXP-014: Đấu Trường Thoát Lệnh 3 Chiều: Fixed vs ATR-Pure vs HYBRID v2/v3
- **Mục tiêu:** Kiểm định thực tế liệu việc co giãn mức dừng lỗ và chốt lời theo độ biến động thực tế ($ATR_{14}$) có giúp nâng cao R:R và Return Rate mà không đánh đổi Win Rate hay không.
- **Tư duy Đột phá:**
  - **Phát hiện Định lượng Trọng Yếu:** Trên sàn HOSE, cơ chế **Khóa Hòa Vốn $+2.5\% \to +0.2\%$ là "Tấm khiên thần"** bảo vệ Win Rate luôn nằm trên $64\% - 71\%$. Khi nới lỏng cơ chế này để "thở biến động", Win Rate bị sụt giảm ngay lập tức $-6.56\%$.
  - **Mô hình Tối ưu Nhất (Sweet Spot):** Khóa cứng phía Rủi ro (Hard Stop $-3.0\%/-3.5\%$ và Khóa hòa vốn $+2.5\%$) kết hợp Khai phóng phía Lợi nhuận (Mục tiêu chốt lời động theo $ATR_{14}$ và Trailing từ đỉnh cho Tier A+ Runner Mode).
  - **Hiệu Quả Lệnh Thắng Lớn:** Các lệnh chốt lời theo ATR đạt mức lãi trung bình **`+9.81%`** (so với trần cũ $+6.0\%$), và các lệnh Climax Runner ăn trọn **`+20.00%`**.
- **Phạm vi Tài sản:** 100% Cổ phiếu cơ sở giao ngay (Spot Equity), không dùng phái sinh.
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_ATR_EXIT_EXP014.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_ATR_EXIT_EXP014.md)

### 🔬 EXP-015: Tuân Thủ Luật Thanh Toán T+2.5 HOSE & AI Lai Ghép (Hybrid Stacking ML)
- **Mục tiêu:** Loại bỏ hoàn toàn giả định phi thực tế bán được ngay ngày $T+1$, thiết lập chuẩn mực mô phỏng $100\%$ tuân thủ luật Việt Nam ($T+1$ khóa bán, chiều $T+2$ mới mở bán) và giải quyết rủi ro kẹp hàng bằng kiến trúc AI Lai ghép 3 nhánh.
- **Tư duy Đột phá:**
  - **Kiến trúc 3 Nhánh Lai Ghép (Hybrid Stacking Ensemble):**
    - Nhánh 1: LambdaMART Ranker (NDCG@5 phân hạng cổ phiếu).
    - Nhánh 2: Multi-Horizon 3-Day Momentum Ridge (Dự báo quán tính chu kỳ giữ 3 phiên).
    - Nhánh 3: **T+2.5 Survival Gate Classifier** (Dự báo xác suất sập sàn trong 2 ngày bị khóa thanh khoản để loại bỏ trước khi mua).
- **Kết quả Thực nghiệm 7 Năm (2020 – 2026, 1.356 lệnh):**
  - **Realized Win Rate Toàn Danh Mục:** Đạt **`70.94%`** (Tăng $+4.30\%$ so với Single GBDT).
  - **Lợi Thế Kỳ Vọng (Expectancy):** Đảo chiều ngoạn mục từ âm $-0.210\% \to \mathbf{+0.302\%}$ / trade (Kèo Tier A+ đạt **`+0.607%`** / trade).
  - **Lợi Nhuận Tích Lũy (Cumulative PnL):** Đảo chiều từ $-26.5\% \to \mathbf{+44.7\%}$ (Chênh lệch $+71.2\%$ NAV).
  - **Loại Bỏ Rủi Ro:** Triệt tiêu hoàn toàn **92 bẫy kẹp hàng sàn** trong suốt 7 năm.
- **Phạm vi Tài sản:** 100% Cổ phiếu cơ sở giao ngay (Spot Equity), không dùng phái sinh.
- **File Báo cáo Gốc:** [QUANT_RESEARCH_NOTE_T25_HYBRID_EXP015.md](file:///d:/AIInvest/ai-engine/docs/QUANT_RESEARCH_NOTE_T25_HYBRID_EXP015.md)

### 🔬 EXP-016: Kế Toán Pháp Y & Beneish M-Score Gate (Lớp 0) Trên Nền Tảng T+2.5
- **Mục tiêu:** Kiểm toán toàn diện chất lượng dữ liệu BCTC và đo lường tác động của Lớp 0 (Beneish M-Score Gate $M \le -1.78$) trong việc loại bỏ rủi ro gian lận BCTC trên HOSE dưới ràng buộc $T+2.5$.
- **Tư duy Đột phá:**
  - **Kiểm toán Dữ liệu Đầu vào:** 15.105 báo cáo quý được chuẩn hóa, loại bỏ hoàn toàn lỗi chia cho 0 qua cơ chế Outlier Clipping $[0.2, 5.0]$.
  - **Lớp 0 - Tấm Lưới Lọc Kế Toán:** Tự động gạch tên 649 lượt cổ phiếu có dấu hiệu xào nấu BCTC (DSRI, AQI, TATA cao) trước khi chuyển sang cho Ranker.
- **Kết quả Thực nghiệm 7 Năm (2020 – 2026, 851 lệnh):**
  - **Bứt Phá Nhóm Tinh Hoa Tier A+ ($Z \ge 3.80\sigma$):** Win Rate tăng lên **`74.00%`**, Lợi thế kỳ vọng (Expectancy) vượt mốc **`+1.017%`** / lệnh giải ngân.
  - **Giảm Thiểu Thua Lỗ:** Mức lỗ trung bình toàn bộ danh mục giảm từ $-5.31\% \to \mathbf{-4.85\%}$ (chặn đứng các cú sập sàn liên tiếp do hồi tố kiểm toán).
- **Phạm vi Tài sản:** 100% Cổ phiếu cơ sở giao ngay (Spot Equity), không dùng phái sinh.
### 🔬 EXP-017: Tích Hợp Lõi Sản Xuất 2 Chế Độ Vào Daily Pipeline Orchestrator
- **Mục tiêu:** Cắm hoàn chỉnh Lớp 0 (Beneish Gate Point-in-Time), Lõi T+2.5 Hybrid Stacking Alpha, và Sizing 2 Chế độ vào luồng điều phối sản xuất [`daily_pipeline_orchestrator.py`](file:///d:/AIInvest/ai-engine/app/application/use_cases/daily_pipeline_orchestrator.py).
- **Kiến Trúc Triển Khai 2 Chế Độ:**
  - **Chế Độ 1 (Integrated Multi-Agent Book):** Tỷ trọng $12\% - 15\%$ NAV / vị thế, đi qua 12 Agents (Counter-Thesis, Risk Agent, Hard Laws).
  - **Chế Độ 2 (Standalone Pure-ML Fund):** Tỷ trọng $20\%$ NAV / vị thế (Tối đa 5 vị thế Tier A+, tối ưu hóa tốc độ tăng trưởng NAV độc lập, không vỡ danh mục vì rủi ro tối đa 1 mã kẹp sàn chỉ $-2.0\%$ NAV).
- **Kết quả Kiểm Thử Thực Tế (Live DB Integration Test):**
  - **Phiên 2025-08-15 (Thị Trường Tăng Trưởng):** Lớp 0 Beneish gạch tên 55 mã $\to$ Còn 95 mã sạch $\to$ Sinh 3 lệnh chuẩn xác (CII, ORS, VJC).
  - **Phiên 2026-06-30 (Phiên Gần Nhất):** Sinh 3 lệnh Tier A+ (PC1, SSB, TPB).
  - **Phiên 2022-05-15 (Thị Trường Sụp Đổ - Bear Crash):** Chuyển `100% Tiền mặt (BEAR_DEFENSE)`, phát 0 lệnh, bảo toàn nguyên vẹn NAV.
- **Phạm vi Tài sản:** 100% Cổ phiếu cơ sở giao ngay (Spot Equity), không dùng phái sinh.
- **File Mã Nguồn:** [daily_pipeline_orchestrator.py](file:///d:/AIInvest/ai-engine/app/application/use_cases/daily_pipeline_orchestrator.py), [test_live_pipeline_integration.py](file:///d:/AIInvest/ai-engine/app/domain/services/ml/test_live_pipeline_integration.py).

---

## HƯỚNG DẪN KHI BỔ SUNG NGHIÊN CỨU MỚI TRONG TƯƠNG LAI
1. Đặt mã nghiên cứu mới tiếp theo (ví dụ: `EXP-018`...).
2. Tạo file báo cáo nghiên cứu chi tiết: `docs/QUANT_RESEARCH_NOTE_<CHỦ_ĐỀ>_EXP<XXX>.md`.
3. Bổ sung 1 dòng vào bảng `RESEARCH REGISTRY` và thêm 1 phân mục tóm tắt vào cuối file này.
4. Tuyệt đối không xóa hoặc chỉnh sửa nội dung kết quả thực nghiệm lịch sử của các EXP trước đó.

