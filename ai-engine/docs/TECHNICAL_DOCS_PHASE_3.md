# TECHNICAL DOCUMENTATION — PHASE 3: QUYẾT ĐỊNH & TỐI ƯU (DECISION)

## Overview
Phase 3 tập trung vào việc chuyển đổi các tín hiệu (signals) thành các quyết định cụ thể về tỷ trọng tiền mặt và quy mô vị thế, dựa trên điều kiện thị trường.

## Modules

### 1. Market Regime & Volatility (EPIC 3.1)

#### TASK-301: HMM Regime Classifier
- **Module:** `app/core/regime/hmm_classifier.py`
- **Logic:** Sử dụng mô hình Markov ẩn (HMM) để phân loại 4 trạng thái: Bull Trending, Bull Choppy, Bear Trending, Bear Bounce.
- **Hysteresis:** Áp dụng cơ chế trễ (threshold 15%, 3 phiên liên tiếp) để loại bỏ nhiễu tín hiệu tại vùng biên.

#### TASK-302: GARCH Cash Engine
- **Module:** `app/core/risk/garch_engine.py`
- **Logic:** Dự báo biến động (Volatility) bằng mô hình GARCH(1,1).
- **Cash Allocation:** Tỷ lệ tiền mặt (Cash) được scale tuyến tính theo mức biến động dự báo (VIX VN Analog).

#### TASK-303: Advanced Risk Metrics
- **Module:** `app/core/risk/advanced_metrics.py`
- **Metrics:** 
    - **Value at Risk (VaR):** Tổn thất tối đa tiềm tàng.
    - **Expected Shortfall (ES):** Trung bình lỗ ở đuôi phân phối rủi ro (Tail risk).
    - **Max Drawdown:** Đo lường mức sụt giảm vốn lớn nhất.

### 2. Portfolio Decisions (EPIC 3.2)

#### TASK-311: Counter Thesis Engine
- **Module:** `app/core/decision/counter_thesis.py`
- **Agent:** Governance Agent (CIO).
- **Chức năng:** Sử dụng LLM làm "Devil's Advocate" để tìm lỗ hổng trong luận đề đầu tư và enforce "Rule of Three" (ít nhất 3 tín hiệu độc lập).

#### TASK-312: Kelly Position Sizer
- **Module:** `app/core/position_sizing/kelly_sizer.py`
- **Logic:** Sử dụng công thức Quarter Kelly (1/4) làm baseline.
- **Adjustment:** Quy mô vị thế được điều chỉnh tự động theo Market Regime (VD: Giảm size 50% trong Bear Bounce).

## Testing Summary
- Kiểm tra cơ chế Hysteresis chuyển trạng thái chính xác sau 3 phiên.
- Kiểm tra tính toán VaR/ES theo phân phối lịch sử.
- Kiểm tra Position Sizing đảm bảo không vi phạm Hard Law 15% NAV.
