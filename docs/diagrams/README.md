# AIInvest Architectural Diagrams Hub
> **Standards Compliance**: Built using the editorial `/diagram-design` specification and the `scientific-diagram-prompt-crafter` meta-skill (Paper-grade NeurIPS/SIGMOD standard, fresh light pastel palette with NO black item backgrounds, orthogonal connectors `r=8`, label masks with &ge;6px margins, dynamic flow opacity dimming, and interactive click-to-inspect drawers).

Thư mục này chứa các sơ đồ trực quan tương tác khoa học độc lập (self-contained HTML / inline SVG) mô tả kiến trúc công nghệ thông tin và nghiệp vụ đầu tư định lượng của nền tảng **AIInvest**.

---

## 1. Danh Mục Sơ Đồ Tiêu Chuẩn Khoa Học (Scientific Diagrams Catalog)

| Sơ đồ | File | Loại trực quan | Trọng tâm mô tả kỹ thuật |
|---|---|---|---|
| **Luồng Biến Đổi Dữ Liệu & Thuật Toán Báo Cáo Khoa Học** | [paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html) | Scientific Data Flow (NeurIPS/SIGMOD) | Bố cục 2 pha (Offline Dual-Store vs Online Fork-Join), băm dòng `quote_hash` SHA-256, chiếu siêu đồ thị $M:N$, không gian vector 1024 chiều, bộ lọc ngưỡng sàn $S_{\text{raw}} \ge \max(0.35, 0.68 S_{\text{top}})$, mô hình Beneish $M_8 > -1.78$, bộ tích lũy Queue Accumulator $32 \to 24 \to 8$, điều khiển tương tác Step Motion, Dynamic Flow Opacity và bảng khảo sát chi tiết **Inspector Drawer** khi click vào từng node. |

> [!NOTE]
> Các sơ đồ phân rã cũ đã được tinh gọn và làm sạch khỏi thư mục nhằm tránh phân mảnh và không đồng nhất tiêu chuẩn thiết kế. Để tái tạo bất kỳ sơ đồ chuyên sâu nào theo chuẩn khoa học mới, sử dụng meta-skill: `scientific-diagram-prompt-crafter`.

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
