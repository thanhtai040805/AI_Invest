# TECHNICAL DOCUMENTATION — PHASE 5: ĐIỀU PHỐI & GIÁM SÁT (ORCHESTRATION)

## Overview
Phase 5 là bộ não vận hành, đóng vai trò như một hệ thống Orchestrator điều khiển dòng chảy dữ liệu từ Phase 1 đến Phase 4 một cách tuần tự và an toàn. Đồng thời giám sát sự sai lệch giữa mô hình và thực tế.

## Modules

### 1. System Orchestration (EPIC 5.1)

#### TASK-501: Daily Pipeline Orchestrator
- **Module:** `app/workflows/daily_pipeline_orchestrator.py`
- **Chức năng:** Điều phối toàn bộ quy trình đầu tư hàng ngày.
- **Workflow:**
    1. Lấy dữ liệu (Data Ingestion - TASK 103, 104).
    2. Phân loại Regime (HMM Classifier - TASK 301).
    3. Dự báo Biến động và tỷ lệ Cash (GARCH - TASK 302).
    4. Cập nhật Universe & Phân loại (Universe Manager - TASK 201).
    5. Kiểm tra Hedging (Hedge Controller - TASK 402).
- **Thiết kế:** Chạy độc lập như một background worker (Cronjob hoặc Celery/Airflow).

#### TASK-502: Model Reality Alignment Layer (MRAL)
- **Module:** `app/eval/mral.py`
- **Chức năng:** Thu thập dữ liệu phản hồi (feedback loop) để đánh giá sức khỏe của các mô hình định lượng.
- **Metrics Tracking:**
    - **HMM Accuracy:** So sánh dự báo Regime với thực tế thị trường.
    - **Slippage:** So sánh giá trị khớp lệnh thực tế (Filled Price) với giá kỳ vọng (Target Price).
    - **IC Decay:** Cảnh báo nếu Information Coefficient của các Factor giảm quá 50% so với trung bình lịch sử.
- **Action:** Dữ liệu từ MRAL được sử dụng bởi Learning Agent (hoặc quá trình Retrain định kỳ) để cập nhật tham số.

## Integration Outlook
Pipeline này đóng vai trò là "Bộ não" (Brain). Khi được kết nối với hệ thống hiện tại, nó sẽ chạy ngầm, sinh ra tín hiệu và lệnh, sau đó đẩy sang hệ thống cũ (Executor) để khớp lệnh.
