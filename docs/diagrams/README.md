# AIInvest Architectural Diagrams Hub
> **Standards Compliance**: Built using the editorial `/diagram-design` specification and the `scientific-diagram-prompt-crafter` meta-skill (Paper-grade NeurIPS/SIGMOD standard, fresh light pastel palette with NO black item backgrounds, orthogonal connectors `r=8`, label masks with &ge;6px margins, dynamic flow opacity dimming, and interactive click-to-inspect drawers).

Thư mục này chứa các sơ đồ trực quan tương tác khoa học độc lập (self-contained HTML / inline SVG) mô tả kiến trúc công nghệ thông tin và nghiệp vụ đầu tư định lượng của nền tảng **AIInvest**.

---

## 1. Danh Mục Sơ Đồ Tiêu Chuẩn Khoa Học (Scientific Diagrams Catalog)

| Sơ đồ | File | Loại trực quan | Trọng tâm mô tả kỹ thuật |
|---|---|---|---|
| **Luồng Biến Đổi Dữ Liệu & Thuật Toán Báo Cáo Khoa Học** | [paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html) | Scientific Data Flow (NeurIPS/SIGMOD) | Bố cục 2 pha (Offline Dual-Store vs Online Fork-Join), băm dòng `quote_hash` SHA-256, chiếu siêu đồ thị $M:N$, không gian vector 1024 chiều, bộ lọc ngưỡng sàn $S_{\text{raw}} \ge \max(0.35, 0.68 S_{\text{top}})$, mô hình Beneish $M_8 > -1.78$, bộ tích lũy Queue Accumulator $32 \to 24 \to 8$, điều khiển tương tác Step Motion, Dynamic Flow Opacity và bảng khảo sát chi tiết **Inspector Drawer** khi click vào từng node. |
| **Kiến Trúc Toàn Diện 12 Agent Định Lượng Tự Hành (IOS v5.1)** | [full-12-agents-sovereign-architecture.html](file:///d:/AIInvest/docs/diagrams/full-12-agents-sovereign-architecture.html) | Multi-Agent Sovereign Quant Organization (NeurIPS/ICLR/SIGMOD) | Hợp nhất toàn diện 12 Agent tự trị: **Tầng 1** (Giám sát Vĩ mô Sticky HMM, GJR-GARCH(1,1), CSAD Herding & Sàng lọc Universe Lớp 0 Beneish $M_8 \le -1.78$); **Tầng 2** (Nghiên cứu Đa nhân tố F1-F6, CSS Scoring, Luận điểm Ba Tín hiệu Độc lập Điều 3, Phản biện Đối nghịch Devil's Advocate CTS & Trọng tài Tối cao CIO); **Tầng 3** (Định cỡ Quarter Kelly $f^*$, Cổng Rủi ro Pre-trade 5 lớp, Rủi ro kẹt T+2.5 $13.51\%$, Khớp lệnh EAE VWAP lô 100, và Canh gác Dừng lỗ T0-T5); **Tầng 4** (Sổ cái Mật mã Bất biến SHA-256 Chaining & Causal RL Empirical Bayes Shrinkage). Tích hợp Layer Filter Switcher và 12 bảng **Inspector Drawer** chi tiết. |
| **Bản Thiết Kế Vĩ Mô Hợp Nhất Toàn Bộ 6 Trục Giải Thuật (Master Blueprint)** | [unified-quant-algorithms-blueprint.html](file:///d:/AIInvest/docs/diagrams/unified-quant-algorithms-blueprint.html) | Master Unified Quant & Risk Architecture (NeurIPS/ICLR/SIGMOD) | Hợp nhất toàn diện 5 tầng kiến trúc và 6 trục giải thuật định lượng: **Tầng 1** (BCTC AST băm SHA-256 song song Vi phân phân số FFD $w_k$, ADF $d^*$ và vi cấu trúc Garman-Klass / Amihud); **Tầng 2** (Đồ thị định hướng 15 Hub ngành & 9 cụm tập đoàn HOSE, xung lực Lead Shock Shift(1, 2) và thế năng bắt kịp 3 ngày); **Tầng 3** (Nhận diện phân phối, Winsorize 1%-99%, Safe Sector Z-score $N \ge 3$, Trực giao hóa Gram-Schmidt $u_k \perp u_j$ / PCA, Lọc GTGD $\ge 5$ tỷ VND & lọc giá trần, Rank IC Spearman & Decile Spread $Q_{10}-Q_1$); **Tầng 4** (Động cơ kép Beneish $M_8$ vs $M_5$ thích ứng với ngưỡng $M > -1.78$ FAIL loại trừ vĩnh viễn, Bộ ba Pre-trade Hard Laws: Điều 1 rủi ro 2% NAV kèm trần sàn T+2.5 $13.51\%$, Điều 2 thanh khoản 15%/25% ADTV20, Điều 4 tập trung 15% mã / 35% ngành); **Tầng 5** (HMM Regime Multiplier, Quarter Kelly Sizer $f^* \le 15\%$ NAV, Khớp lệnh lô chẵn 100 sàn HOSE, Thang 6 bậc Cắt lỗ Phòng thủ T0-T5). Tích hợp bộ lọc nhanh Pillar Filter Switcher và 12 bảng **Inspector Drawer** chi tiết. |
| **Cây Quyết Định Toán Học & Kiến Trúc TCG (GIL)** | [gil-decision-tree-gating.html](file:///d:/AIInvest/docs/diagrams/gil-decision-tree-gating.html) | Scientific TCG & Decision Tree (NeurIPS/ICLR/SIGMOD) | Kiến trúc 2 giai đoạn quy mô lớn: **Giai đoạn 1**: Thiết lập Đồ thị Doanh nghiệp Thời gian (**Temporal Corporate Graph - TCG** Pipeline từ `gil_service.py`) gồm Entity Layer phân giải $V_{\text{corp}}, V_{\text{person}}, V_{\text{spv}}$, chiếu ra 3 đồ thị đa tầng độc lập diện tích lớn (**Ownership Graph** $G_{\text{own}}$, **Governance Graph** $G_{\text{gov}}$, **Financial Flow Graph** $G_{\text{flow}}$) và hộp hợp nhất TCG với Bất biến phạm vi kế toán $\text{Scope}(x)=\text{Scope}(y)$; **Giai đoạn 2**: Cây quyết định bậc thang 4 cổng chặn với các đồ thị con thực tế, kích thước lớn ($r \ge 18\text{px}$, nhãn số tiền VND và % sở hữu rõ ràng): Gate 0 (Đồ thị đứt gãy), Gate 1 (Chu trình rút ruột động Motif M1), Gate 2 (Lưới 4 Motif thực tế: M2 Phễu nội bộ, M3 Rút vốn ngoại vi, M4 Lệch kiểm soát - dòng tiền, M5 Đảo kỳ hạn thời gian) và Gate 3 (Cây Arborescence DAG chuẩn mực & Kelly Sizing). Tích hợp Preset Switcher 4 kịch bản, Dynamic Flow Opacity và Inspector Drawer chi tiết. |
| **Danh Mục Master Prompt Sơ Đồ Khoa Học Cho Mọi Giải Thuật** | [SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md](file:///d:/AIInvest/docs/diagrams/SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md) | Academic Specification Engine (NeurIPS/ICLR/SIGMOD) | Đặc tả 6 tầng nhận thức và 7 bộ Master Prompt hoàn chỉnh cho toàn bộ hệ thống giải thuật `ai-engine` (Vi phân phân số FFD, Lan truyền cú sốc đồ thị 15 ngành/9 tập đoàn, Trung hòa & trực giao hóa nhân tố, Thao túng BCTC Beneish kép, Hard Laws & Kelly phân tầng, Pipeline AST băm SHA-256). |

> [!NOTE]
> Các sơ đồ phân rã cũ đã được tinh gọn và làm sạch khỏi thư mục nhằm tránh phân mảnh và không đồng nhất tiêu chuẩn thiết kế. Để tái tạo bất kỳ sơ đồ chuyên sâu nào theo chuẩn khoa học mới, sử dụng meta-skill: `scientific-diagram-prompt-crafter` và tham khảo [SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md](file:///d:/AIInvest/docs/diagrams/SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md).

---

## 2. Cách Xem và Tương Tác (How to View & Interact)

Do sơ đồ được xây dựng theo chuẩn **self-contained HTML / native SVG / zero-JS drawer**:
- **Trình duyệt**: Mở trực tiếp file [paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html) trong trình duyệt web (Edge, Chrome, Firefox, Safari).
- **Tương tác Khảo sát (Inspector Drawer)**: Nhấp (Click) vào bất kỳ hộp thuật toán, trạm dữ liệu hoặc kho lưu trữ nào trên sơ đồ để trượt ra bảng chi tiết công thức toán học ngầm, điều kiện biên và liên kết mã nguồn gốc.
- **Điều khiển Step Motion**: Hỗ trợ đầy đủ các nút Play, Pause, Next, Prev, Replay và các phím tắt bàn phím:
  - `Space`: Tạm dừng / Tiếp tục phát
  - `←` / `→`: Lùi / Tiến từng bước trong luồng xử lý
  - `R`: Phát lại từ đầu
  - `Home` / `End`: Nhảy đến bước đầu tiên / hoàn tất

---

## 3. Tiêu Chuẩn Thiết Kế & Hiệu Ứng Tuân Thủ (`/diagram-design` & `scientific-diagram-prompt-crafter`)
1. **Zero Diagonal Lines**: 100% đường nối sử dụng orthogonal right-angle elbows với bán kính bo góc `r=8`.
2. **Accessible SVG Contract**: Sơ đồ tuân thủ `<svg role="img" aria-labelledby="...">` với `<title>` và `<desc>` chi tiết.
3. **Strict Pastel Palette (No Black Backgrounds)**: Sử dụng các gam màu pastel tươi sáng, thanh lịch (`#f8fafc`, `#e0f2fe`, `#ffedd5`, `#ffe4e6`, `#dcfce7`) với chữ đậm nét có độ tương phản cao, triệt tiêu hoàn toàn nền đen tối kỵ cho item/node.
4. **Dynamic Flow Opacity**: Các luồng không thuộc bước hiện tại tự động giảm độ mờ về `0.14`, làm nổi bật bước đang được kích hoạt lên `1.0`.
5. **Quality Gate**: Sơ đồ vượt qua kiểm định `python "C:\Users\This PC\.gemini\config\skills\diagram-design\scripts\self_check.py" <file>` với kết quả `OK`.
