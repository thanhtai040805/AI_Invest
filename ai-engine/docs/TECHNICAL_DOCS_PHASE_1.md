# TECHNICAL DOCUMENTATION — PHASE 1: NỀN TẢNG AN TOÀN

## Overview
Phase 1 xây dựng các module nền tảng về dữ liệu và an toàn hệ thống (failsafe). Đảm bảo mọi quyết định đầu tư sau này đều dựa trên dữ liệu sạch và tuân thủ các luật cứng (Hard Laws) bất khả xâm phạm.

## Modules

### 1. Data Foundation (EPIC 1.1)

#### TASK-101: Data Quality Check Engine
- **Module:** `app/core/quality/data_quality.py`
- **Chức năng:** Thực hiện 8 kiểm tra chất lượng trước khi chạy pipeline.
- **Checks:** OHLCV completeness, Price limits (7%), Volume non-negative, Volume separation, Financial freshness, Corporate action applied, Announcement date integrity, Point-in-time integrity.

#### TASK-102: Corporate Action Adjustment Engine
- **Module:** `app/core/quality/corporate_action.py`
- **Chức năng:** Điều chỉnh giá lịch sử (adjusted price) khi có sự kiện chia tách, cổ tức.
- **Loại hỗ trợ:** SPLIT, DIVIDEND_STOCK, DIVIDEND_CASH.

#### TASK-103: OHLCV Ingestion Engine
- **Module:** `app/services/ohlcv_ingestion_service.py`
- **Chức năng:** Lấy dữ liệu từ DNSE (Primary) và yfinance (Fallback).
- **Đặc tính:** Phân tách `volume_continuous` và `volume_atc` từ dữ liệu intraday để tính ADTV20 chính xác.

#### TASK-104: Financial Ingestion Engine
- **Module:** `app/services/financial_ingestion_service.py`
- **Chức năng:** Lấy BCTC quý/năm.
- **Đặc tính:** Enforce `announcement_date` để đảm bảo tính Point-in-Time. Tự động tính ROIC (20% tax), Accrual Ratio, và FCF.

### 2. FailSafe & Hard Laws (EPIC 1.2)

#### TASK-111: Hard Law Enforcement Engine
- **Module:** `app/core/risk/hard_laws.py`
- **Chức năng:** Chặn mọi lệnh vi phạm luật cứng.
- **Luật kiểm tra:** 
    - Điều 1: Rủi ro vị thế <= 2% NAV.
    - Điều 2: Thanh khoản (thoát trong 5 phiên).
    - Điều 4: Tập trung (Stock <= 15%, Sector <= 35% NAV).

#### TASK-112: Failsafe & Heartbeat System
- **Module:** `app/core/risk/failsafe.py`
- **Chức năng:** Giám sát kết nối Broker.
- **Trigger:** 3 missed heartbeats (30s interval) hoặc latency > 1500ms trong > 5s.
- **Hành động:** Chuyển trạng thái ACTIVE, chặn giao dịch, thực thi callback khẩn cấp.

#### TASK-113: Stop-Loss Engine
- **Module:** `app/core/risk/stop_loss.py`
- **Chức năng:** Giám sát P&L thời gian thực.
- **Trigger:** Lỗ chưa thực hiện <= -2% NAV.
- **Lệnh:** EMERGENCY stop-loss order gửi trực tiếp đến Execution Engine.

## Testing Summary
Tất cả các module đã có unit tests bao gồm happy path và edge cases (bad data, connection loss, limit violations).
Tổng số test đã pass trong Phase 1: ~80 tests.
