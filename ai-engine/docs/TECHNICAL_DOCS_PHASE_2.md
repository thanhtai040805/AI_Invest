# TECHNICAL DOCUMENTATION — PHASE 2: CORE SIGNAL ENGINE (EPIC 2.1 & 2.2)

## Overview
Phase 2 xây dựng bộ máy tính toán tín hiệu cốt lõi, bao gồm việc quản lý danh mục cổ phiếu (Universe) và tính toán các nhân tố định lượng (Factors) để xếp hạng cổ phiếu.

## Modules

### 1. Universe & Filtering (EPIC 2.1)

#### TASK-201: Universe Manager
- **Module:** `app/services/universe_manager.py`
- **Chức năng:** Phân loại toàn bộ HOSE thành các nhóm:
    - **Group A:** VN30 và Bluechips thanh khoản cực cao.
    - **Group B:** Cổ phiếu tiêu chuẩn.
    - **Group C:** Cổ phiếu nhỏ/thanh khoản thấp.
    - **SANDBOX:** Cổ phiếu tăng trưởng cao thỏa mãn 4 điều kiện (ADTV > 2 tỷ, Cap > 300 tỷ, Revenue Growth > 25%, Debt/Equity < 15%).
    - **EXCLUDED:** Loại trừ do vi phạm trạng thái giao dịch, Beneish FAIL hoặc GIL CATASTROPHIC.
- **Liquidity:** Luôn dùng `adtv20_continuous` (loại bỏ ATC/ATO).

#### TASK-202: Beneish M-Score Engine
- **Module:** `app/core/quality/beneish.py`
- **Chức năng:** Tính toán M-Score dựa trên 8 biến số tài chính để phát hiện rủi ro thao túng báo cáo.
- **Logic:** 
    - Nếu M-Score > -1.78 → Trạng thái `FAIL` (Đưa vào nhóm EXCLUDED).
    - Thiếu BCTC năm t-1 → Trạng thái `PENDING`.

#### TASK-203: Graph Intelligence Layer (GIL)
- **Module:** `app/core/quality/graph_intelligence.py`
- **Chức năng:** Giám sát rủi ro sở hữu chéo và giao dịch vòng qua cấu trúc đồ thị.
- **Tính năng:**
    - **Cycle Detection:** Tự động phát hiện các vòng lặp giao dịch (A -> B -> C -> A).
    - **OCR (Ownership Concentration Ratio):** Tính toán độ tập trung sở hữu của các thực thể liên quan.
    - **Catastrophic Risk:** Tự động đánh dấu rủi ro cực cao nếu giá trị giao dịch vòng vượt 15% Doanh thu hoặc Tài sản.

### 2. Factor Engine (EPIC 2.2)

#### Factor Engine Base
- **Module:** `app/core/factors/base.py`
- **Chức năng:** Cung cấp hàm chuẩn hóa Percentile Rank (0-100) và bộ lọc Universe chung.

#### TASK-211: Value Factor Engine (F1)
- **Module:** `app/core/factors/value.py`
- **Factors:** P/E, P/B.
- **Logic:** Rank thấp -> Score cao (Invert). Đảm bảo Point-in-Time qua `published_date`.

#### TASK-212: Quality Factor Engine (F2)
- **Module:** `app/core/factors/quality.py`
- **Factors:** ROIC, Accrual Ratio, GPM Stability.
- **Logic:** GPM Stability tính bằng độ lệch chuẩn của thay đổi YoY quarterly trong 8 quý.

#### TASK-213: Momentum Factor Engine (F3)
- **Module:** `app/core/factors/momentum.py`
- **Factors:** Price Momentum (1m, 3m, 12m).
- **Logic:** Tính tổng lợi nhuận (return) dựa trên giá `close_adj`.

## Testing Summary
- **Universe Manager:** Verify phân loại VN30, Sandbox và Hard Filters.
- **Factor Engines:** Verify tính toán lợi nhuận, chuẩn hóa percentile và xử lý Point-in-Time.
- Tất cả unit tests đã pass.
