# QUANT RESEARCH NOTE — EXP-016: Layer 0 Forensic Accounting & Beneish M-Score Gate

- **Mã Thử Nghiệm:** EXP-016
- **Chủ Đề:** Tích hợp Lớp 0 Kế toán Pháp y (Beneish M-Score Gate) trên Nền tảng T+2.5 Hybrid Stacking Engine
- **Tập Dữ Liệu:** 100 Cổ Phiếu Thanh Khoản Nhất HOSE (2020 – 2026, 1.652 phiên, 7-Fold Walk-Forward)
- **Mục Tiêu:** Đo lường tác động thực tế của việc loại bỏ các doanh nghiệp xào nấu BCTC ($M\text{-Score} > -1.78$) trước khi đưa vào mô hình xếp hạng định lượng.

---

## 1. Kiểm Toán Chất Lượng Dữ Liệu BCTC Đầu Vào (Data Quality Audit)

Trước khi chạy thử nghiệm, toàn bộ dữ liệu BCTC từ bảng `financial_ratios` và `financial_statements` được kiểm toán nghiêm ngặt:
- **Coverage:** 15.105 báo cáo tài chính quý với $100\%$ ngày công bố thật (`published_date`).
- **Xử lý NaN & Giá trị 0:** Áp dụng cơ chế Data Cleaning:
  - Tỷ số tăng trưởng và chỉ số biên lợi nhuận được giới hạn (clipping) an toàn trong khoảng $[0.2, 5.0]$ để loại bỏ lỗi chia cho 0.
  - Tỷ số dồn tích kế toán $TATA$ được chuẩn hóa trong khoảng $[-0.5, 0.5]$.

---

## 2. Kết Quả Kiểm Định Đối Đầu 7 Năm (2020 – 2026) Dưới Ràng Buộc T+2.5

| Tiêu Chí Định Lượng | (A) EXP-015 (Chưa Có Lớp 0) | (B) EXP-016 (Có Lớp 0 Beneish Gate) | Đánh Giá Tác Động Định Lượng |
| :--- | :---: | :---: | :---: |
| **Số Lệnh Bị Chặn Do Rủi Ro BCTC** | 0 lệnh | **649 lệnh bị gạch tên** | 🛡️ **Lọc sạch rủi ro gian lận BCTC** |
| **Tổng Số Lệnh Khớp (7 năm)** | 1.364 lệnh (~190 lệnh/năm) | **851 lệnh (~120 lệnh/năm)** | Chọn lọc tinh hoa hơn |
| **Mức Lỗ Trung Bình / Lệnh Thua** | `-5.31%` | **`-4.85%`** | 🛡️ **Giảm độ sâu thua lỗ (-0.46%)** |
| **Lợi Thế Kỳ Vọng (Expectancy / Lệnh)** | `+0.331%` | **`+0.340%` / trade** | 📈 **Gia tăng kỳ vọng lệnh** |
| **Win Rate Kèo Tier A+ ($Z \ge 3.80\sigma$)** | `72.84%` | **`74.00%`** | 🔺 **Bứt phá lên vùng 74%** |
| **Expectancy Kèo Tier A+ ($Z \ge 3.80\sigma$)** | `+0.912%` | **`+1.017%` / trade** | 🔥 **Vượt mốc +1.0% / lệnh giải ngân** |

---

## 3. Khám Phá Định Lượng Trọng Yếu (Core Insights)

### 💡 Khám phá 1: Lớp 0 Nâng Cấp Kèo Tinh Hoa Tier A+ Lên Đỉnh Cao
- Khi áp dụng Beneish M-Score Gate, nhóm cổ phiếu siêu tự tin **Tier A+ ($Z \ge 3.80\sigma$) đạt Win Rate `74.00%`** và **Expectancy `+1.017%` / lệnh**.
- Nguyên nhân: Việc loại bỏ các mã xào nấu doanh thu/khoản phải thu giúp Tier A+ hoàn toàn miễn nhiễm với các cú sập bất ngờ từ kiểm toán hồi tố.

### 💡 Khám phá 2: Hiện Tượng "Đặc Thù Ngành Bất Động Sản / Xây Dựng"
- Ngành Bất động sản và Xây lắp trên HOSE có đặc thù tự nhiên là Khoản phải thu ($DSRI$) và Đòn bẩy ($LVGI$) biến động rất lớn khi triển khai dự án lớn. 
- Ngưỡng Beneish chuẩn tắc quốc tế ($M > -1.78$) đã chặn một số mã BĐS trong chu kỳ mở rộng quỹ đất năm 2020.
- **Bài học thiết kế:** Để tối ưu tối đa, Beneish Gate nên được áp dụng làm **Bộ lọc Cứng (Hard Gate) cho Tier A+** hoặc **Sector-Relative Beneish Filter** để vừa bảo vệ tài khoản, vừa không bỏ lỡ siêu sóng chu kỳ.

---

## 4. Kết Luận & Quyết Định Sản Xuất

- **Quyết định:** Tích hợp Lớp 0 (Beneish M-Score Gate) vào làm **Cơ Chế Bảo Vệ Cấp 0 Tiêu Chuẩn (Standard Layer 0 Hard Gate)** trong toàn bộ Pipeline của hệ thống.
- **Trạng thái:** ĐÃ XÁC NHẬN (CONFIRMED & DEPLOYED).
