# TECHNICAL DOCUMENTATION — PHASE 4: THỰC THI (EXECUTION)

## Overview
Phase 4 chịu trách nhiệm nhận các quyết định từ Phase 3 và chuyển chúng thành các lệnh giao dịch an toàn, tối ưu trượt giá (slippage) và quản lý phòng vệ (hedging).

## Modules

### 1. Order Execution (EPIC 4.1)

#### TASK-401: Execution Adaptation Engine (EAE)
- **Module:** `app/core/execution/eae.py`
- **Chức năng:** Xử lý và chia nhỏ các lệnh lớn (Order Slicing).
- **Quy tắc:**
    - **Normal Order:** Cắt nhỏ lệnh sao cho mỗi phần không vượt quá 5% ADTV20 (Average Daily Trading Volume 20 ngày).
    - **Emergency Order:** Bỏ qua chia nhỏ, thực thi toàn bộ bằng lệnh MP/ATC nếu Stop-Loss kích hoạt.
- **Market Phase:** Tự động nhận diện phiên giao dịch (ATO, Continuous, ATC) để chọn loại lệnh phù hợp (LIMIT vs MP).

#### TASK-402: VN30F Hedge Controller
- **Module:** `app/core/execution/hedge_controller.py`
- **Chức năng:** Hệ thống phòng vệ danh mục bằng phái sinh (Hợp đồng tương lai VN30F).
- **Triggers (CDC Active):** 
    - Market Breadth (MA50) < 15%.
    - HMM Regime xác nhận Bear Trending với xác suất > 80%.
- **Action:** Đề xuất số lượng hợp đồng Short VN30F cần thiết để bao phủ giá trị danh mục (100% hedge in emergency).

## Testing Summary
- Kiểm thử EAE chia nhỏ lệnh chính xác dựa trên thanh khoản.
- Kiểm thử EAE xử lý lệnh Emergency ưu tiên tốc độ (không chia nhỏ).
- Kiểm thử Hedge Controller tính toán đúng số lượng hợp đồng VN30F tương đương giá trị NAV.
