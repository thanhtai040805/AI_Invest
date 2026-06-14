# DATA_REQUIREMENTS.md — IOS v5.1

> **Mục đích:** Liệt kê toàn bộ data fields hệ thống cần. Developer đọc file này biết chính xác cần lấy gì, từ đâu, bao lâu một lần, và validate thế nào. Không cần đọc IOS v5.1 để hiểu.

---

## NGUYÊN TẮC DATA

**Point-in-time integrity:** Mọi data tài chính phải được ghi nhận đúng thời điểm nó có thể biết được thực tế — không dùng data tương lai để tính signal quá khứ (look-ahead bias).

**Single source of truth:** Mỗi field chỉ có 1 nguồn chính (primary). Nguồn backup chỉ dùng khi primary fail. Không được mix 2 nguồn cho cùng 1 field trong cùng 1 calculation.

**Corporate action adjusted:** Toàn bộ OHLCV và EPS phải backward-adjusted trước khi vào bất kỳ module nào.

---

## NHÓM 1 — MARKET DATA

### 1.1 OHLCV Daily

| Field | Type | Description | Freshness | Source Priority |
|:---|:---|:---|:---|:---|
| `ticker` | str | Mã cổ phiếu HOSE | — | SSC |
| `date` | date | Ngày giao dịch | T+0 trước 16:00 | HOSE feed |
| `open_adj` | float | Giá mở cửa, đã corporate action adjusted | T+0 | VNDirect / SSI iBoard |
| `high_adj` | float | Giá cao nhất, đã adjusted | T+0 | VNDirect / SSI iBoard |
| `low_adj` | float | Giá thấp nhất, đã adjusted | T+0 | VNDirect / SSI iBoard |
| `close_adj` | float | Giá đóng cửa, đã adjusted | T+0 | VNDirect / SSI iBoard |
| `volume_continuous` | float | Khớp lệnh liên tục (KHÔNG bao gồm ATC, ATO) | T+0 | HOSE raw feed |
| `volume_atc` | float | Khối lượng ATC riêng biệt | T+0 | HOSE raw feed |
| `volume_ato` | float | Khối lượng ATO riêng biệt | T+0 | HOSE raw feed |
| `vwap` | float | VWAP phiên, tính từ volume_continuous | T+0 | Tính tự động |

**Validation rules:**
```
open_adj > 0
high_adj >= max(open_adj, close_adj)
low_adj <= min(open_adj, close_adj)
volume_continuous >= 0
volume_total = volume_continuous + volume_atc + volume_ato
|close_adj / close_adj_prev - 1| <= 0.075  # HOSE limit ±7%
```

**Tại sao tách volume_continuous:** ADTV20 dùng để lọc Universe và Liquidity Limit PHẢI loại ATC/ATO. ATC dễ bị thao túng và không đại diện cho thanh khoản thực.

---

### 1.2 Intraday Order Book (chỉ cần khi thực thi)

| Field | Type | Description | Freshness |
|:---|:---|:---|:---|
| `bid_price_1..10` | float | Giá mua 10 bước | Real-time |
| `bid_volume_1..10` | float | Khối lượng mua 10 bước | Real-time |
| `ask_price_1..10` | float | Giá bán 10 bước | Real-time |
| `ask_volume_1..10` | float | Khối lượng bán 10 bước | Real-time |
| `spread_bps` | float | Bid-ask spread tính bằng basis points | Real-time |

**Dùng bởi:** M16 (EAE) — tính Max Order Size, phát hiện STRESS mode.

---

### 1.3 Foreign Flow

| Field | Type | Description | Freshness | Source |
|:---|:---|:---|:---|:---|
| `foreign_buy_vol` | float | Khối lượng mua nước ngoài | T+0 | HOSE |
| `foreign_sell_vol` | float | Khối lượng bán nước ngoài | T+0 | HOSE |
| `foreign_net_vol` | float | = buy - sell | T+0 | Tính tự động |
| `is_etf_rebalance_day` | bool | Ngày rebalance ETF định kỳ (cuối quý) | T-1 | Manual calendar |

**Lưu ý:** F4.1 chỉ tính dòng tiền chủ động, PHẢI loại ngày `is_etf_rebalance_day = True`.

---

### 1.4 VN-Index

| Field | Type | Description | Freshness |
|:---|:---|:---|:---|
| `vnindex_close` | float | Đóng cửa VN-Index | T+0 |
| `vnindex_ma50` | float | MA50 của VN-Index | T+0, tính tự động |
| `advance_count` | int | Số mã tăng giá toàn sàn | T+0 |
| `decline_count` | int | Số mã giảm giá toàn sàn | T+0 |
| `unchanged_count` | int | Số mã đứng giá | T+0 |
| `market_breadth` | float | advance / (advance + decline) | T+0, tính tự động |

---

## NHÓM 2 — FINANCIAL DATA (BCTC)

> **Point-in-time rule:** Dữ liệu Q1 chỉ available sau ngày công bố thực tế (thường 45 ngày sau cuối quý). KHÔNG được dùng số Q1 để tính signal trước ngày công bố.

### 2.1 Income Statement (Quý)

| Field | Type | Description | Frequency | Source |
|:---|:---|:---|:---|:---|
| `revenue` | float | Doanh thu thuần | Quarterly | VietStock / CafeF / SSC |
| `gross_profit` | float | Lợi nhuận gộp | Quarterly | — |
| `gross_margin` | float | = gross_profit / revenue | Quarterly | Tính tự động |
| `ebit` | float | EBIT (trước lãi và thuế) | Quarterly | — |
| `ebt` | float | Lợi nhuận trước thuế | Quarterly | — |
| `tax_expense` | float | Chi phí thuế TNDN | Quarterly | — |
| `net_income` | float | LNST (thuộc cổ đông công ty mẹ) | Quarterly | — |
| `eps_basic` | float | EPS cơ bản, đã adjusted corporate action | Quarterly | — |
| `sga_expense` | float | Chi phí bán hàng + QLDN | Quarterly | — |
| `depreciation` | float | Khấu hao TSCĐ | Quarterly | — |
| `announcement_date` | date | Ngày công bố BCTC thực tế | Per release | SSC |

---

### 2.2 Balance Sheet (Quý)

| Field | Type | Description | Frequency |
|:---|:---|:---|:---|
| `total_assets` | float | Tổng tài sản | Quarterly |
| `current_assets` | float | Tài sản ngắn hạn | Quarterly |
| `cash_and_equiv` | float | Tiền và tương đương tiền | Quarterly |
| `receivables` | float | Phải thu ngắn hạn | Quarterly |
| `inventory` | float | Hàng tồn kho | Quarterly |
| `ppe_net` | float | TSCĐ hữu hình (net) | Quarterly |
| `total_equity` | float | Vốn chủ sở hữu | Quarterly |
| `total_debt` | float | Tổng nợ vay (ngắn + dài hạn) | Quarterly |
| `long_term_debt` | float | Nợ dài hạn | Quarterly |
| `current_liabilities` | float | Nợ ngắn hạn | Quarterly |
| `non_interest_liabilities` | float | Nợ không lãi suất (phải trả nhà cung cấp, người mua trả trước...) | Quarterly |
| `net_debt` | float | = total_debt - cash_and_equiv | Quarterly, tính tự động |

---

### 2.3 Cash Flow Statement (Quý)

| Field | Type | Description | Frequency |
|:---|:---|:---|:---|
| `cfo` | float | Dòng tiền từ hoạt động kinh doanh | Quarterly |
| `capex` | float | Chi tiêu vốn (âm trong BCTC, lấy absolute value) | Quarterly |
| `fcf` | float | = cfo - capex | Quarterly, tính tự động |

---

### 2.4 Derived Financial Metrics (Tính tự động)

| Field | Công thức | Dùng bởi |
|:---|:---|:---|
| `roic` | EBIT × (1 - tax_rate) / (total_assets - cash - non_interest_liabilities) | F2.1, M-Score nâng cấp |
| `accrual_ratio` | (net_income - cfo) / total_assets | F2.3 |
| `altman_z` | Công thức Z-Score điều chỉnh | F2.4 (chỉ non-financial) |
| `piotroski_f` | 9-point score | M07, Discovery |
| `sue_proxy` | (eps_Q / eps_Q-4) - 1 | F3.2 fallback |
| `invested_capital` | total_assets - cash_and_equiv - non_interest_liabilities | F2.1 |

---

## NHÓM 3 — CORPORATE ACTIONS

> Đây là nhóm data dễ bị bỏ qua nhất và gây lỗi nghiêm trọng nhất trong backtest.

| Field | Type | Description | Source |
|:---|:---|:---|:---|
| `action_type` | enum | SPLIT / MERGE / DIVIDEND_CASH / DIVIDEND_STOCK / RIGHTS | SSC / HOSE |
| `ex_date` | date | Ngày giao dịch không hưởng quyền | SSC |
| `record_date` | date | Ngày chốt danh sách | SSC |
| `ratio` | float | Tỷ lệ (VD: 2:1 split → ratio = 2.0) | SSC |
| `cash_amount` | float | Giá trị cổ tức tiền mặt (VND/cổ phiếu) | SSC |
| `adjustment_factor` | float | Hệ số điều chỉnh giá lịch sử | Tính tự động |

**Validation:**
```
Mọi OHLCV historical phải được re-adjusted khi có corporate action mới
EPS historical phải được adjusted tương tự
KHÔNG được dùng close_unadjusted trong bất kỳ signal calculation nào
```

---

## NHÓM 4 — OWNERSHIP DATA

> Dùng bởi M04 (GIL) và F4.3 (Insider Signal).

| Field | Type | Description | Frequency | Source |
|:---|:---|:---|:---|:---|
| `shareholder_id` | str | ID pháp nhân / thể nhân | Per change | SSC |
| `shareholder_name` | str | Tên cổ đông | Per change | SSC |
| `ownership_pct` | float | % sở hữu | Per change | SSC |
| `shareholder_type` | enum | INDIVIDUAL / CORPORATE / FOREIGN / STATE | Per change | SSC |
| `is_board_member` | bool | Có phải HĐQT / BĐH không | Per change | SSC |
| `related_company_id` | str | Công ty liên quan (nếu có) | Per change | SSC |
| `change_date` | date | Ngày thay đổi sở hữu | Per change | SSC |
| `disclosure_date` | date | Ngày công bố thực tế (thường trễ 3-5 ngày) | Per change | SSC |

**Point-in-time:** Dùng `disclosure_date`, KHÔNG dùng `change_date`, để tránh look-ahead bias.

---

## NHÓM 5 — INSIDER TRANSACTION DATA

| Field | Type | Description | Source |
|:---|:---|:---|:---|
| `transaction_type` | enum | BUY_MARKET / SELL_MARKET / BUY_AGREEMENT / SELL_AGREEMENT / TRANSFER_INTERNAL / ESOP | SSC |
| `volume` | float | Khối lượng giao dịch | SSC |
| `price` | float | Giá giao dịch | SSC |
| `transaction_date` | date | Ngày thực hiện | SSC |
| `disclosure_date` | date | Ngày công bố (signal date thực tế) | SSC |
| `insider_role` | str | CEO / CFO / Chairman / Director / Major_Shareholder | SSC |

**Lọc khi tính F4.3:**
```python
VALID_TYPES = ["BUY_MARKET", "SELL_MARKET", "BUY_AGREEMENT", "SELL_AGREEMENT"]
# Loại bỏ: TRANSFER_INTERNAL, ESOP
# Signal date = disclosure_date + 0 ngày (đã trễ sẵn 3-5 ngày làm việc thực tế)
```

---

## NHÓM 6 — MACRO DATA

| Field | Type | Description | Frequency | Source |
|:---|:---|:---|:---|:---|
| `sbv_policy_rate` | float | Lãi suất điều hành SBV | Per change | SBV |
| `credit_growth_ytd` | float | Tăng trưởng tín dụng YTD (%) | Monthly | SBV |
| `usd_vnd_rate` | float | Tỷ giá USD/VND | Daily | Vietcombank / SBV |
| `public_investment_disbursement` | float | Giải ngân đầu tư công (tỷ VND, YTD) | Monthly | MPI |
| `cpi_yoy` | float | CPI YoY (%) | Monthly | GSO |

**Dùng bởi:** M09 (HMM, Sector Rotation Clock), Portfolio sector allocation.

---

## NHÓM 7 — ALTERNATIVE DATA

> Nhóm này có độ nhiễu cao nhất. Phải chuẩn hóa trước khi dùng.

### 7.1 Google Trends

| Field | Type | Description | Frequency | Source |
|:---|:---|:---|:---|:---|
| `keyword` | str | Từ khóa tương ứng với brand/product cốt lõi của công ty | — | Manual mapping |
| `svi_raw` | float | Search Volume Index (0-100, relative) | Weekly | Google Trends API (pytrends) |
| `svi_zscore` | float | Z-score chuẩn hóa rolling 20 tuần | Weekly | Tính tự động |
| `polarity_score` | float | Sentiment của tin tức liên quan keyword (+1 / 0 / -1) | Weekly | NLP từ news feed |
| `svi_signal` | float | = svi_zscore × polarity_score | Weekly | Tính tự động |

**Validation:**
```
Phải có keyword mapping cho ticker trước khi dùng F6.1
Nếu polarity_score chưa có → svi_signal = 0 (không dùng)
Không interpolate SVI trong tuần — chỉ update khi có data mới
```

### 7.2 Power Consumption (IPCN)

| Field | Type | Description | Frequency | Source |
|:---|:---|:---|:---|:---|
| `province` | str | Tỉnh/thành phố nơi nhà máy đặt | — | Manual mapping |
| `power_consumption_gwh` | float | Tiêu thụ điện tỉnh (GWh) | Monthly | EVN monthly report |
| `ipcn_growth_yoy` | float | Tăng trưởng YoY (%) | Monthly | Tính tự động |

**Chỉ áp dụng cho:** Ngành sản xuất, thép, xi măng, hóa chất. KHÔNG áp dụng cho dịch vụ, ngân hàng.

### 7.3 Logistics & Port Throughput (LPTV)

| Field | Type | Description | Frequency | Source |
|:---|:---|:---|:---|:---|
| `port_name` | str | Tên cảng | — | Manual mapping |
| `throughput_teus` | float | Sản lượng container (TEU) | Monthly | Cảng vụ / VLA |
| `vessel_calls` | int | Số lượt tàu cập cảng | Monthly | Cảng vụ |
| `lptv_zscore` | float | Z-score chuẩn hóa rolling 12 tháng | Monthly | Tính tự động |

---

## NHÓM 8 — AUDIT & COMPLIANCE DATA

| Field | Type | Description | Frequency | Source |
|:---|:---|:---|:---|:---|
| `audit_opinion` | enum | UNQUALIFIED / QUALIFIED / ADVERSE / DISCLAIMER | Annual | Kiểm toán báo cáo |
| `audit_firm` | str | Tên công ty kiểm toán | Annual | SSC |
| `is_going_concern` | bool | Có nghi ngờ hoạt động liên tục không | Annual | Kiểm toán |
| `trading_status` | enum | NORMAL / WARNING / CONTROLLED / SUSPENDED | Daily | HOSE |
| `halt_history_count` | int | Số lần bị tạm dừng giao dịch trong 12 tháng | Monthly | HOSE |

**Universe filter:** `audit_opinion` in (QUALIFIED, ADVERSE, DISCLAIMER) trong 2 năm gần nhất → loại khỏi Universe.

---

## NHÓM 9 — GRAPH / RELATIONSHIP DATA (cho GIL)

> Lưu trong Neo4j, không phải PostgreSQL.

| Node/Edge | Fields | Source |
|:---|:---|:---|
| `Company` node | ticker, name, sector, listed_date, is_listed | SSC + HOSE |
| `Person` node | person_id, name, roles[], nationality | SSC |
| `LegalEntity` node | entity_id, name, country, is_foreign | SSC + manual |
| `OWNS` edge | from, to, pct, effective_date, disclosure_date | SSC ownership |
| `TRANSACTION` edge | from, to, amount, date, type, as_pct_revenue | SSC RPT |
| `GUARANTEES` edge | from, to, amount, date | SSC |
| `TRANSFER` edge | from, to, shares, price, date | SSC |

**Update frequency:** Sau mỗi lần SSC có disclosure mới (event-driven, không phải batch).

---

## BẢNG TÓM TẮT — MODULE SỬ DỤNG DATA GÌ

| Module | Data Groups Needed |
|:---|:---|
| M01 Universe Manager | 1 (OHLCV), 8 (Audit/Compliance) |
| M03 Beneish Filter | 2.1, 2.2, 2.3 (Financial) |
| M04 GIL | 9 (Graph), 4 (Ownership) |
| M05 Alt Data | 7 (Alternative) |
| M06 Factor Engine | 1, 2, 5 (Market + Financial + Insider) |
| M09 HMM | 1.4 (VN-Index) |
| M10 GARCH | 1.4 (VN-Index returns) |
| M14 VN30F Hedge | 1.4 (market breadth) |
| M16 EAE | 1.2 (Order Book), 1.1 (VWAP) |

---

## DATA QUALITY CHECKLIST (chạy hàng ngày trước khi mở pipeline)

```python
def daily_data_quality_check():
    checks = [
        check_ohlcv_completeness(universe, date=today),       # Tất cả ticker có đủ OHLCV không
        check_price_limit_violations(universe, date=today),   # Giá có vượt ±7% không hợp lệ
        check_volume_non_negative(universe, date=today),
        check_financial_freshness(universe, max_lag_days=90), # BCTC không cũ hơn 90 ngày
        check_corporate_action_applied(universe),              # CA đã được adjust chưa
        check_announcement_date_lag(universe),                 # Dùng đúng announcement_date chưa
    ]
    if any(check.failed for check in checks):
        halt_pipeline()  # Không chạy nếu data có vấn đề
        send_alert(checks)
```