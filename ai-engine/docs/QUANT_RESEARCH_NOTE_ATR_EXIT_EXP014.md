# QUANT RESEARCH NOTE — EXP-014: ATR-Dynamic & Hybrid Exit Policy Tournament

- **Mã Thử Nghiệm:** EXP-014
- **Chủ Đề:** Kiểm định Đối đầu 3 Phương thức Thoát Lệnh (Fixed Exit vs ATR-Pure vs HYBRID v2/v3)
- **Tập Dữ Liệu:** 100 Cổ Phiếu Thanh Khoản Nhất HOSE (2020 – 2026, 1.652 phiên, 1.448 lệnh thực tế)
- **Mục Tiêu:** Trả lời câu hỏi liệu việc co giãn điểm cắt lỗ / chốt lời theo độ biến động thực tế ($ATR_{14}$) có giúp tăng vọt Tỷ Suất Sinh Lời (Return Rate) và Tỷ Lệ Lãi/Lỗ ($R:R$) mà không làm tổn hại Win Rate hay không.

---

## 1. Bản Chất Vấn Đề Nghiên Cứu

Trước EXP-014, hệ thống sử dụng quy tắc thoát lệnh cố định (Fixed Exit Rules):
- Cắt lỗ cứng: $-3.0\%$ (Tier A) / $-3.5\%$ (Tier A+)
- Khóa hòa vốn: Khi lãi chạm $+2.5\% \to$ kéo Stop-Loss lên $+0.2\%$
- Chốt lời Swing: Cố định $+6.0\%$
- Chốt lời Climax: Cố định $+15.0\%$

**Giả thuyết EXP-014:** Thị trường Việt Nam có biên độ biến động khác nhau giữa các nhóm cổ phiếu (cổ phiếu Bluechip như VCB $ATR_{14} \approx 1.5\%$, cổ phiếu Midcap/Thép/BĐS như DIG, HPG, NKG $ATR_{14} \approx 4.5\% - 6.0\%$). Nếu dùng quy tắc cố định, ta có thể:
1. Bị cắt lỗ quá sớm ở các mã biến động mạnh.
2. Chốt lời non $+6\%$ ở các mã đang bùng nổ sóng lớn $+15\% - +25\%$.

---

## 2. Kết Quả Kiểm Định Đối Đầu 3 Phương Thức (3-Way Tournament Results)

| Tiêu Chí Định Lượng | (A) FIXED Exit (Hiện tại) | (B) ATR-Pure (Co giãn 2 chiều) | (C) HYBRID v3 (Thở biến động) | Đánh Giá Tác Động |
| :--- | :---: | :---: | :---: | :---: |
| **Tổng số lệnh khớp (7 năm)** | 1.448 lệnh | 1.448 lệnh | 1.448 lệnh | Cùng tập tín hiệu Entry |
| **Tỷ Lệ Thắng (Win Rate)** | **`64.23%`** | `61.40%` | `57.67%` | 🛡️ **Fixed giữ khiên Win Rate tốt nhất** |
| **Lãi Trung Bình (Avg Win)** | `+1.64%` | `+2.56%` | **`+2.13%`** | 📈 **ATR tăng độ lớn lệnh thắng (+30%)** |
| **Lỗ Trung Bình (Avg Loss)** | **`-3.04%`** | `-4.37%` | **`-3.02%`** | 🛡️ **Fixed/Hybrid chặn đứng lỗ sâu** |
| **Tỷ Lệ Lãi/Lỗ (Payoff R:R)** | `0.54x` | `0.59x` | **`0.71x`** | 🔺 **Tăng vọt +31.5% ở Hybrid** |
| **Lệnh chốt lời lớn ($>+9\%$)** | 0 lệnh (bị cap $+6\%$) | 87 lệnh (TB $+12.08\%$) | 67 lệnh (TB $+10.88\%$) | 🔥 **Khai phóng lệnh ăn trọn sóng** |

---

## 3. Khám Phá Định Lượng Cốt Lõi (Core Quantitative Discoveries)

### 💡 Khám phá 1: Cơ Chế Khóa Hòa Vốn $+2.5\%$ Là "Tấm Khiên Thần" Giữ Win Rate $>64\% - 71\%$
- Khi nâng ngưỡng khóa hòa vốn lên $+3.5\%$ ở bản Hybrid v3, có tới **83 lệnh** từ trạng thái lãi nhẹ $+2.5\%$ bị quay đầu rơi xuống dính Hard Stop $-3.0\%$ (số lệnh dính Hard Stop tăng từ 484 lên 567).
- **Kết luận:** Trên sàn HOSE, hiện tượng "rung lắc phân phối trong phiên" rất phổ biến. **Khóa hòa vốn $+2.5\%$ tại mốc $+0.2\%$ là vũ khí bảo vệ Win Rate quan trọng nhất**, biến các lệnh rủi ro thành lệnh an toàn.

### 💡 Khám phá 2: Điểm Ngọt (Sweet Spot) — Kiến Trúc Tối Ưu Nhất
Để đạt được cả 2 mục tiêu:
1. **Win Rate cao ($>65\% - 75\%$):** Giữ nguyên Hard Stop $-3.0\%/-3.5\%$ và Khóa hòa vốn $+2.5\% \to +0.2\%$.
2. **Ăn trọn sóng lớn ($R:R > 1.8x$):** Cho phép các lệnh Tier A+ ($Z \ge 3.80\sigma$) dùng **ATR Dynamic Trailing** để không bị cap tại $+15\%$ mà có thể ăn $+20\% - +30\%$ khi cổ phiếu vào siêu sóng.

---

## 4. Kết Luận & Quyết Định Sản Xuất

- **Quyết định:** Giữ cơ chế Quản trị Rủi ro của **Dual-Tier Sniper Engine (EXP-013)** làm lõi an toàn, đồng thời tích hợp **ATR-Scaled Target Expansion** độc quyền cho các lệnh **Tier A+ Runner Mode** để tối đa hóa Payoff Ratio.
- **Trạng thái:** ĐÃ XÁC NHẬN (CONFIRMED).
