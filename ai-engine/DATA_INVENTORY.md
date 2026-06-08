# DATA INVENTORY — AI Brain Investment Platform

> **Mục đích:** Tài liệu này mapping toàn bộ trường dữ liệu chứng khoán theo classification: Raw Data (cần lấy từ API) vs Calculated Data (cần tính toán).  
> Mỗi dòng ghi rõ **trạng thái hiện tại** để AI không cần research lại codebase.

---

## PHẦN 1: DỮ LIỆU THỊ TRƯỜNG (Market Data)

### 1A. OHLCV CORE

| Field | Type | Status | Nguồn/Vị trí hiện tại | Ghi chú |
|-------|------|--------|----------------------|---------|
| symbol | Raw | ✅ **CÓ** | `ohlcv` table (stocks.symbol FK) | |
| date / time | Raw | ✅ **CÓ** | `ohlcv.time` (TIMESTAMPTZ) | Composite PK with symbol |
| open | Raw | ✅ **CÓ** | `ohlcv.open` | DECIMAL(12,2) |
| high | Raw | ✅ **CÓ** | `ohlcv.high` | |
| low | Raw | ✅ **CÓ** | `ohlcv.low` | |
| close | Raw | ✅ **CÓ** | `ohlcv.close` | |
| volume | Raw | ✅ **CÓ** | `ohlcv.volume` | BIGINT |
| value / turnover | Calc | ✅ **CÓ** | `data_enricher.py:compute_market_extras()` | close × volume |
| adj_close | Calc | ❌ **THIẾU** | Chưa implement | Cần corporate_actions để tính adjustment factor |
| vwap | Calc | ✅ **CÓ** | `data_enricher.py:compute_market_extras()` | Σ(tp×vol)/Σ(vol) |
| nb_trades | Raw | ❌ **THIẾU** | DNSE intraday API có trade count | Thêm vào OHLCV hoặc riêng intraday table |
| is_trading_day | Calc | ✅ **CÓ** | `app/brain/dataflows/vendors/vn/calendar.py:VNCalendar.is_trading_day()` | Tính từ DNSE working dates + weekday |

### 1B. FOREIGN FLOW

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| foreign_buy_qty | Raw | ✅ **CÓ** | `data_enricher.py:fetch_foreign_flow()` | DNSE REST API, chưa persist DB |
| foreign_sell_qty | Raw | ✅ **CÓ** | Tương tự | |
| foreign_buy_value | Raw | ✅ **CÓ** | Tương tự | |
| foreign_sell_value | Raw | ✅ **CÓ** | Tương tự | |
| foreign_ownership_pct | Calc | ✅ **CÓ** | `data_enricher.py:fetch_foreign_flow()`: DNSE WS hub → `foreignerOrderLimitQuantity` + `foreignerBuyPossibleQuantity` | **Real** khi WS connected, fallback = hash |
| room_foreign | Calc | ✅ **CÓ** | Tương tự | **Real-time** từ DNSE WebSocket `ForeignInvestor.foreignerBuyPossibleQuantity` |
| net_foreign_qty | Calc | ✅ **CÓ** | `data_enricher.py:fetch_foreign_flow()` | buy_qty - sell_qty |
| net_foreign_value | Calc | ✅ **CÓ** | Tương tự | buy_value - sell_value |

### 1C. ORDER BOOK (Level 2)

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| bid_price_1..5 | Raw | ⚠️ **CÓ 1 PHẦN** | DNSE WebSocket stream có | Frontend `OrderBook.tsx` lấy real-time, ko persist |
| bid_qty_1..5 | Raw | ⚠️ CÓ 1 PHẦN | Tương tự | |
| ask_price_1..5 | Raw | ⚠️ CÓ 1 PHẦN | Tương tự | |
| ask_qty_1..5 | Raw | ⚠️ CÓ 1 PHẦN | Tương tự | |
| spread | Calc | ✅ **CÓ** | `data_enricher.py:compute_spread()` | best_ask - best_bid |
| spread_pct | Calc | ✅ **CÓ** | Tương tự | spread / mid_price × 100 |

### 1D. MARKET DEPTH / QUOTE

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| price / last_price | Raw | ⚠️ **CÓ 1 PHẦN** | `market_data_service.get_quote()` | Key là `price`, không phải `current_price` hay `last_price`
| price_change | Calc | ✅ **CÓ** | `ohlcv_tool.get_latest_price()` trả về change, change_percent |
| price_change_pct | Calc | ✅ **CÓ** | Tương tự |
| ceiling | Raw | ✅ **CÓ** | `stocks.ceiling` table |
| floor | Raw | ✅ **CÓ** | `stocks.floor` table |
| ref_price | Raw | ✅ **CÓ** | `stocks.ref_price` table |
| turnover_rate | Calc | ✅ **CÓ** | `data_enricher.py:compute_market_extras()` | volume / shares_outstanding × 100 |

---

## PHẦN 2: BÁO CÁO TÀI CHÍNH (Financial Statements)

### 2A. INCOME STATEMENT

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| revenue | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `y_finance.py` | |
| cost_of_revenue | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) | |
| gross_profit | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `y_finance.py` | |
| operating_income | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `y_finance.py` | |
| net_income | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `fundamentals_tool.py`, `y_finance.py` | |
| ebitda | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `y_finance.py` | |
| interest_expense | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) | |
| income_tax | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) | |
| eps_basic | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py` (DNSE) | |
| eps_diluted | Calc | ✅ **CÓ** | `data_enricher.py` | Fallback = net_income / shares |
| period_type | Raw | ⚠️ **CÓ 1 PHẦN** | `FundamentalsTool` gọi DNSE, `data_enricher.py` từ vnstock | Chưa persist DB |
| fiscal_year | Raw | ⚠️ **CÓ 1 PHẦN** | Tương tự | |
| quarter | Raw | ⚠️ **CÓ 1 PHẦN** | Tương tự | |

### 2B. BALANCE SHEET

| Field | Type | Status | Vị trí |
|-------|------|--------|--------|
| cash_and_equivalents | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| total_assets | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `fundamentals_tool.py` |
| total_liabilities | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `fundamentals_tool.py` |
| total_equity | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| inventory | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| receivables | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| payables | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| short_term_debt | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| long_term_debt | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| shares_outstanding | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback); `market_data_service` |

### 2C. CASH FLOW

| Field | Type | Status | Vị trí |
|-------|------|--------|--------|
| CFO (operating) | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| CFI (investing) | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| CFF (financing) | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| capital_expenditures | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |
| dividends_paid | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock on-demand + fallback) |

### 2D. TỪ BCTC → CHỈ SỐ TÍNH TOÁN

| Field | Type | Status | Công thức / Vị trí |
|-------|------|--------|--------------------|
| gross_margin | Calc | ✅ **CÓ** | `data_enricher.py`; `y_finance.py` |
| net_margin | Calc | ✅ **CÓ** | `data_enricher.py`; `y_finance.py` |
| ebitda_margin | Calc | ✅ **CÓ** | `data_enricher.py`: ebitda / revenue (fallback) |
| bvps | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py` (DNSE) |
| revenue_yoy | Calc | ✅ **CÓ** | `data_enricher.py`: multi-period vnstock (real) + hash fallback | ⚠️ Real nếu có 4+ quý dữ liệu |
| net_income_yoy | Calc | ✅ **CÓ** | `data_enricher.py`: multi-period vnstock (real) + hash fallback | |
| eps_yoy | Calc | ✅ **CÓ** | `data_enricher.py`: multi-period vnstock (real) + hash fallback | |
| free_cash_flow (FCF) | Calc | ✅ **CÓ** | `data_enricher.py`: CFO - Capex; `y_finance.py` |
| fcfe | Calc | ✅ **CÓ** | `data_enricher.py`: CFO - Capex + net_borrowing |
| fcff | Calc | ✅ **CÓ** | `data_enricher.py`: EBIT×(1-tax) + D&A - Capex - ΔWC |

---

## PHẦN 3: CHỈ SỐ TÀI CHÍNH (Financial Ratios)

| Chỉ số | Type | Status | Vị trí | Ghi chú |
|--------|------|--------|--------|---------|
| pe_ratio | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py`, `y_finance.py` | |
| pe_ratio_ttm | Calc | ✅ **CÓ** | `data_enricher.py` (= pe_ratio) | Vnstock ratio endpoint trả P/E |
| pb_ratio | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py`, `y_finance.py` | |
| ps_ratio | Calc | ✅ **CÓ** | `data_enricher.py`: equity × PB / revenue | |
| peg_ratio | Calc | ✅ **CÓ** | `data_enricher.py`: PE / eps_yoy | ⚠️ eps_yoy là hash-based |
| ev_ebitda | Calc | ✅ **CÓ** | `data_enricher.py`: (equity + liab - cash) / ebitda | |
| dividend_yield | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py`, `y_finance.py` | |
| payout_ratio | Calc | ✅ **CÓ** | `data_enricher.py`: dividends / net_income | |
| roe | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py`, `y_finance.py` | |
| roa | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py` | |
| roic | Calc | ✅ **CÓ** | `data_enricher.py`: NOPAT / (debt + equity) | |
| operating_margin | Calc | ✅ **CÓ** | `y_finance.py` | |
| current_ratio | Calc | ✅ **CÓ** | `data_enricher.py`: (cash + inv + rec) / (liab×0.4); `y_finance.py` | |
| quick_ratio | Calc | ✅ **CÓ** | `data_enricher.py`: (cash + rec) / (liab×0.4) | |
| debt_to_equity | Calc | ✅ **CÓ** | `data_enricher.py`; `fundamentals_tool.py`, `y_finance.py` | |
| net_debt_to_ebitda | Calc | ✅ **CÓ** | `data_enricher.py`: (liab - cash) / ebitda | |
| interest_coverage | Calc | ✅ **CÓ** | `data_enricher.py`: op_income / interest_expense | |
| quality_of_earnings | Calc | ✅ **CÓ** | `data_enricher.py`: CFO / net_income | |
| fcf_yield | Calc | ✅ **CÓ** | `data_enricher.py`: FCF / (equity × PB) × 100 | |
| revenue_growth_1y/3y/5y | Calc | ✅ **CÓ** | `data_enricher.py`: CAGR from revenue_yoy | ⚠️ revenue_yoy là hash-based |
| eps_growth_1y/3y/5y | Calc | ✅ **CÓ** | `data_enricher.py`: CAGR from eps_yoy | ⚠️ eps_yoy là hash-based |

---

## PHẦN 4: CHỈ SỐ KỸ THUẬT (Technical Indicators)

### 4A. MOVING AVERAGES

| Chỉ số | Type | Status | Vị trí | Ghi chú |
|--------|------|--------|--------|---------|
| ma5 | Calc | ✅ **CÓ** | `data_enricher.py`; `ml_alpha_predictor.py`, `indicators_tool.py` | |
| ma10 | Calc | ✅ **CÓ** | `data_enricher.py`; `ml_alpha_predictor.py` | |
| ma20 | Calc | ✅ **CÓ** | `data_enricher.py`; `indicators_tool.py` | |
| ma50 | Calc | ✅ **CÓ** | `data_enricher.py`; `indicators_tool.py` | |
| ma200 | Calc | ✅ **CÓ** | `data_enricher.py`; `app/brain/dataflows/stockstats_utils.py` fallback | |
| ema5 | Calc | ✅ **CÓ** | `data_enricher.py` | |
| ema12 | Calc | ✅ **CÓ** | `data_enricher.py`; `ml_alpha_predictor.py` (qua MACD) | |
| ema26 | Calc | ✅ **CÓ** | `data_enricher.py`; `ml_alpha_predictor.py` (qua MACD) | |
| ema200 | Calc | ✅ **CÓ** | `data_enricher.py` | |

### 4B. OSCILLATORS

| Chỉ số | Type | Status | Vị trí |
|--------|------|--------|--------|
| rsi_14 | Calc | ✅ **CÓ** | `data_enricher.py`; `indicators_tool.py`, `ml_alpha_predictor.py`, `app/brain/dataflows/stockstats_utils.py` |
| rsi_7, rsi_21 | Calc | ✅ **CÓ** | `data_enricher.py` | |
| stoch_k, stoch_d | Calc | ✅ **CÓ** | `data_enricher.py` | |
| macd | Calc | ✅ **CÓ** | `data_enricher.py`; `indicators_tool.py`, `ml_alpha_predictor.py` |
| macd_signal | Calc | ✅ **CÓ** | `data_enricher.py`; EMA 9 của MACD |
| macd_histogram | Calc | ✅ **CÓ** | `data_enricher.py`; MACD - Signal |
| adx_14, +DI, -DI | Calc | ✅ **CÓ** | `data_enricher.py` | |
| mfi_14 | Calc | ✅ **CÓ** | `data_enricher.py` | |

### 4C. BOLLINGER BANDS

| Chỉ số | Type | Status | Vị trí |
|--------|------|--------|--------|
| bb_middle | Calc | ✅ **CÓ** | `data_enricher.py`; `indicators_tool.py` |
| bb_upper | Calc | ✅ **CÓ** | `data_enricher.py`; `indicators_tool.py` |
| bb_lower | Calc | ✅ **CÓ** | `data_enricher.py`; `indicators_tool.py` |
| bb_width | Calc | ✅ **CÓ** | `data_enricher.py`: (upper - lower) / middle |
| bb_pct | Calc | ✅ **CÓ** | `data_enricher.py`: (close - lower) / (upper - lower) |

### 4D. VOLATILITY

| Chỉ số | Type | Status | Vị trí |
|--------|------|--------|--------|
| atr_14 | Calc | ✅ **CÓ** | `data_enricher.py`; `ml_alpha_predictor.py` |
| volatility_10d | Calc | ✅ **CÓ** | `data_enricher.py`; `ml_alpha_predictor.py` (annualized × √252) |
| volatility_20d | Calc | ✅ **CÓ** | `data_enricher.py` |
| volatility_60d | Calc | ✅ **CÓ** | `data_enricher.py` |
| volatility_252d | Calc | ✅ **CÓ** | `data_enricher.py` |

### 4E. VOLUME

| Chỉ số | Type | Status | Vị trí |
|--------|------|--------|--------|
| volume_ma5 | Calc | ✅ **CÓ** | `data_enricher.py`; `ml_alpha_predictor.py` (5/10/20/60d) |
| volume_ma20 | Calc | ✅ **CÓ** | `data_enricher.py` |
| volume_ratio | Calc | ✅ **CÓ** | `data_enricher.py`; volume / volume_ma20 |
| obv | Calc | ✅ **CÓ** | `data_enricher.py` |
| vpt (Volume Price Trend) | Calc | ✅ **CÓ** | `ml_alpha_predictor.py` |

### 4F. MOMENTUM

| Chỉ số | Type | Status | Vị trí |
|--------|------|--------|--------|
| momentum_1d | Calc | ✅ **CÓ** | `data_enricher.py` |
| momentum_5d | Calc | ✅ **CÓ** | `data_enricher.py`; return_5d |
| momentum_1m | Calc | ✅ **CÓ** | `data_enricher.py`; return_20d |
| momentum_3m | Calc | ✅ **CÓ** | `data_enricher.py`; return_60d |
| momentum_6m | Calc | ✅ **CÓ** | `data_enricher.py` |
| momentum_1y | Calc | ✅ **CÓ** | `data_enricher.py` |
| trend_strength | Calc | ✅ **CÓ** | `data_enricher.py` = ADX_14 |
| trend_direction | Calc | ✅ **CÓ** | `data_enricher.py`: "UP" if close > MA50 else "DOWN" |

---

## PHẦN 5: RỦI RO & HIỆU SUẤT (Risk & Performance)

### 5A. RISK METRICS

| Chỉ số | Type | Status | Vị trí |
|--------|------|--------|--------|
| beta_1y | Calc | ✅ **CÓ** | `data_enricher.py`: hash-based deterministic simulation |
| beta_3y | Calc | ✅ **CÓ** | `data_enricher.py`: hash-based deterministic simulation |
| alpha_1y | Calc | ✅ **CÓ** | `data_enricher.py`: hash-based deterministic simulation |
| sharpe_ratio_1y | Calc | ✅ **CÓ** | `data_enricher.py`; `backtest/metrics.py` |
| sortino_ratio_1y | Calc | ✅ **CÓ** | `data_enricher.py`; `backtest/metrics.py` |
| treynor_ratio_1y | Calc | ✅ **CÓ** | `data_enricher.py`: (return_1y - 5%) / beta_1y |
| calmar_ratio_1y | Calc | ✅ **CÓ** | `data_enricher.py`; `backtest/metrics.py` |
| information_ratio | Calc | ✅ **CÓ** | `data_enricher.py`; `backtest/metrics.py` |
| max_drawdown_1y | Calc | ✅ **CÓ** | `data_enricher.py`; `backtest/metrics.py` |
| max_drawdown_3y | Calc | ✅ **CÓ** | `data_enricher.py` |
| var_95_1d | Calc | ✅ **CÓ** | `data_enricher.py`; `backtest/metrics.py` (historical) |
| var_99_1d | Calc | ✅ **CÓ** | `data_enricher.py` |
| cvar_95 | Calc | ✅ **CÓ** | `data_enricher.py`; `backtest/metrics.py` |
| downside_deviation | Calc | ✅ **CÓ** | `data_enricher.py` |
| garch_vol | Calc | ✅ **CÓ** | `backtest/metrics.py` (scipy optimization); `data_enricher.py` (daily std proxy) |

### 5B. RETURNS

| Chỉ số | Type | Status | Vị trí |
|--------|------|--------|--------|
| return_1d | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_5d | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_1m | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_3m | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_6m | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_1y | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_3y | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_5y | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_ytd | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_3y_cagr | Calc | ✅ **CÓ** | `data_enricher.py` |
| return_5y_cagr | Calc | ✅ **CÓ** | `data_enricher.py` |

---

## PHẦN 6: FACTOR INVESTING

| Score | Type | Status | Vị trí |
|-------|------|--------|--------|
| 450+ alpha factors (WorldQuant 101, GTJA 191, Qlib 158, Academic) | Calc | ✅ **CÓ** | `app/brain/quant/factors/zoo/` — 4 thư viện, registry quản lý |
| value_score | Calc | ✅ **CÓ** | `data_enricher.py:compute_factor_scores()` | PE/PB/DY/EV-EBITDA weighted |
| momentum_score | Calc | ✅ **CÓ** | Tương tự | 6m return + RSI + MACD + ADX |
| quality_score | Calc | ✅ **CÓ** | Tương tự | ROE/ROA/QoE/CR/IC weighted |
| low_vol_score | Calc | ✅ **CÓ** | Tương tự | Inverse vol + drawdown |
| size_score | Calc | ✅ **CÓ** | Tương tự | Market-cap based (large/mid/small) |
| growth_score | Calc | ✅ **CÓ** | Tương tự | Revenue/EPS/NI growth weighted |
| dividend_score | Calc | ✅ **CÓ** | Tương tự | Dividend yield + payout |
| total_factor_score | Calc | ✅ **CÓ** | Tương tự | Equal-weighted composite |
| factor_rank, factor_percentile | Calc | ⚠️ **CÓ 1 PHẦN** | Tương tự | factor_rank = "N/A" (cần cross-stock), factor_percentile = total_score round proxy |

---

## PHẦN 7: THÔNG TIN CỔ PHIẾU (Stock Info)

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| symbol | Raw | ✅ **CÓ** | `stocks` table (PK) | |
| name | Raw | ✅ **CÓ** | `stocks.name`; `data_enricher.py` fallback = "Công ty CP {symbol}" | |
| exchange | Raw | ✅ **CÓ** | `stocks.exchange`; `data_enricher.py` (vnstock + fallback) | HOSE/HNX/UPCOM |
| sector | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock company_type + fallback) | ⚠️ vnstock company_type = "Công ty cổ phần" (legal form, ko phải sector thực) |
| industry | Raw | ⚠️ **CÓ** | `stocks.industry`; `data_enricher.py` fallback | |
| listing_date | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock + fallback) | |
| isin | Raw | ✅ **CÓ** | `data_enricher.py` (mocked: VN0000...) | |
| website | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock + fallback) | |
| description | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock business_model + fallback) | |
| ceo | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock + fallback); `scraper_insider.py` có officers | |
| cfo | Raw | ✅ **CÓ** | `data_enricher.py` (fallback) | |
| board_chairman | Raw | ✅ **CÓ** | `data_enricher.py` (fallback) | |
| employees | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock + fallback) | |
| founded_year | Raw | ✅ **CÓ** | `data_enricher.py` (fallback) | |
| headquarters | Raw | ✅ **CÓ** | `data_enricher.py` (fallback) | |
| lot_size | Raw | ✅ **CÓ** | `data_enricher.py` (default 100) | |
| tick_size | Raw | ✅ **CÓ** | `data_enricher.py` (default 10/50) | |
| price_limit_up / down | Calc/Raw | ⚠️ **CÓ 1 PHẦN** | `app/brain/dataflows/vendors/vn/calendar.py:calculate_price_limit()` | floor/ceiling lưu trong stocks table |
| free_float | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock + fallback) | |
| shares_outstanding | Raw | ✅ **CÓ** | `data_enricher.py` (vnstock + fallback); `market_data_service` | |
| shares_float | Calc | ✅ **CÓ** | `data_enricher.py`: shares_outstanding × free_float% | |
| market_cap | Calc | ✅ **CÓ** | `stocks.market_cap`, `fundamentals_tool.py` | |
| currency | Raw | ✅ **CÓ** | `data_enricher.py` (fixed "VND") | |
| avg_volume_10d/30d/90d | Calc | ✅ **CÓ** | `data_enricher.py:compute_market_extras()` | Rolling mean từ ohlcv |

---

## PHẦN 8: CỔ TỨC & SỰ KIỆN (Dividend & Events)

| Field | Type | Status | Vị trí |
|-------|------|--------|--------|
| ex_dividend_date | Raw | ❌ **THIẾU** | CafeF/vnstock có, chưa crawl |
| record_date | Raw | ❌ **THIẾU** | |
| payment_date | Raw | ❌ **THIẾU** | |
| dividend_amount | Raw | ❌ **THIẾU** | |
| dividend_type | Raw | ❌ **THIẾU** | Cash / Stock |
| event_type (Rights/Split) | Raw | ❌ **THIẾU** | |
| ex_date (event) | Raw | ❌ **THIẾU** | |
| ratio | Raw | ❌ **THIẾU** | Split ratio / Rights ratio |
| adjustment_factor | Raw | ❌ **THIẾU** | Cần cho adjusted close |
| dividend_yield | Calc | ✅ **CÓ** | Từ fundamentals_tool (DNSE trả) |
| payout_ratio | Calc | ✅ **CÓ** | `data_enricher.py:fetch_vnstock_financials()` | dividends / net_income |

---

## PHẦN 9: GIAO DỊCH NỘI BỘ (Insider Trading)

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| person_name | Raw | ✅ **CÓ** | `scraper_insider.py:_get_vnstock_officers()` | |
| position | Raw | ✅ **CÓ** | Tương tự | |
| transaction_date | Raw | ⚠️ **CÓ 1 PHẦN** | `_search_cafef_insider_news()` tìm news | Chưa có structured data |
| transaction_type | Raw | ⚠️ **CÓ 1 PHẦN** | Buy/Sell từ keyword matching | |
| quantity | Raw | ❌ **THIẾU** | Trong news content, chưa parse structured | |
| price | Raw | ❌ **THIẾU** | Tương tự | |
| value | Raw | ❌ **THIẾU** | Tương tự | |
| shares_after | Raw | ❌ **THIẾU** | | |
| percent_after | Calc | ❌ **THIẾU** | shares_after / shares_outstanding | |

**Note:** `insider_trading_tool.py` (LangChain tool) gọi `scraper_insider.py` để phục vụ AI agents. Dữ liệu có nhưng dạng text/NLP, chưa có structured table.

---

## PHẦN 10: TIN TỨC & SENTIMENT (News & Sentiment)

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| title | Raw | ✅ **CÓ** | `news.title`, `news_ingestion.py` crawl CafeF | |
| content | Raw | ✅ **CÓ** | `news.content` | |
| source | Raw | ✅ **CÓ** | Cafef.vn (hardcode) | |
| url | Raw | ✅ **CÓ** | `news.url` | |
| published_date | Raw | ✅ **CÓ** | `news.publish_date` | |
| author | Raw | ❌ **THIẾU** | CafeF có, chưa crawl | |
| category | Raw | ❌ **THIẾU** | CafeF có 7 categories, crawl nhưng ko lưu | |
| entities | Raw | ❌ **THIẾU** | Chưa có entity extraction | |
| keywords | Raw | ❌ **THIẾU** | | |
| sentiment_score | Calc | ✅ **CÓ** | `sentiment_scorer.py` lexicon-based (-1..1) | **Chỉ keyword counting, ko có AI/NLP** |
| sentiment_label | Calc | ✅ **CÓ** | POSITIVE / NEUTRAL / NEGATIVE | |
| sentiment_1d/5d/10d | Calc | ✅ **CÓ** | `data_enricher.py:compute_sentiment_rolling()` | Rolling mean |
| news_count_1d/5d/10d | Calc | ✅ **CÓ** | Tương tự | |

**Sentiment hiện tại:** Lexicon-based với 39 positive + 24 negative từ.  
❌ **Không có:** negation handling, context, PhoBERT, underthesea, transformer model.

---

## PHẦN 11: RISK FLAGS (v2 — 10 computed flags)

| # | Flag | Type | Status | Vị trí | Logic |
|---|------|------|--------|--------|-------|
| 1 | CANH_BAO_TC | Calc | ⏳ **v2** | `risk_flags_v2.py` | financial_statements: VCSH âm hoặc period có "Cảnh báo" |
| 2 | CHAM_BAO_TC | Calc | ⏳ **v2** | `risk_flags_v2.py` | financial_statements: period_end > 60 ngày |
| 3 | FLOOR_TRAP | Calc | ⏳ **v2** | `risk_flags_v2.py` | technical_indicators: momentum_1d ≤ -6.9% ≥ 2 phiên |
| 4 | SHARP_DROP | Calc | ⏳ **v2** | `risk_flags_v2.py` | technical_indicators: momentum_1d ≤ -7% |
| 5 | KHOI_LUONG_BAT_THUONG | Calc | ⏳ **v2** | `risk_flags_v2.py` | technical_indicators: volume_ratio ≥ 3.0 |
| 6 | FOREIGN_FLOW_ANOMALY | Calc | ⏳ **v2** | `risk_flags_v2.py` | foreign_flow: net sell ≥ 5 phiên liên tiếp |
| 7 | INSIDER_SELLING_ANOMALY | Calc | ⏳ **v2** | `risk_flags_v2.py` | insider_trades: net sell > 2× buy, qty > 100k |
| 8 | GOVERNANCE_SHOCK | Calc | ⏳ **v2** | `risk_flags_v2.py` | news_events: title match "từ nhiệm", "thay CEO" |
| 9 | M_SCORE_FLAG | Calc | ⏳ **v2** | `risk_flags_v2.py` | financial_statements: M > -2.22 (non-bank only) |
| 10 | F_SCORE_FLAG | Calc | ⏳ **v2** | `risk_flags_v2.py` | financial_statements: F < 4/9 |

> **Note:** `risk_flags.py` (v1) và `scraper_ubcknn.py` đã bị xóa. Thay bằng `risk_flags_v2.py`. Các flag cũ (LEGAL_NEWS, HIGH_DEBT, NEGATIVE_ROE, DELIST_RISK, PLEDGE_SHARES, v.v.) đã được thay thế bằng 10 computed flags mới hoặc dropped.

---

## PHẦN 12: DỮ LIỆU VĨ MÔ (Macro Data)

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| vn_index | Raw | ✅ **CÓ** | `market_data_service.get_indices()` (DNSE REST) | |
| hnx_index | Raw | ✅ **CÓ** | Tương tự | |
| vn30 | Raw | ✅ **CÓ** | Tương tự | |
| upcom | Raw | ✅ **CÓ** | Tương tự | |
| vnindex_return_1d | Calc | ✅ **CÓ** | `macro_service` (VietFin VNINDEX) + `data_enricher.py` fallback | Persisted to `macro_indicators` table ✅ |
| vnindex_return_1m | Calc | ✅ **CÓ** | Tương tự | |
| vnindex_return_3m/1y | Calc | ✅ **CÓ** | Tương tự | |
| interest_rate_cod | Raw | ❌ **FALLBACK** | (hardcode 4.75%) | ⚠️ Deposit rates chưa có API public |
| interest_rate_on/1w/1m/3m/6m/1y | Raw | ❌ **FALLBACK** | (range 3.25–4.75%) | |
| lending_rate_12m_big4 | Raw | ⚠️ **CÓ** | `macro_service` (Vimo MCP with VIMO_API_KEY) | Persisted to `macro_indicators` table. Fallback 9.9% nếu không có key |
| lending_rate_12m_commercial | Raw | ⚠️ **CÓ** | Tương tự | Fallback 12.4% |
| refinancing_rate | Raw | ✅ **CÓ** | `macro_service` (SBV web scrape) | Persisted. Real từ SBV (4.5%) |
| discount_rate | Raw | ✅ **CÓ** | `macro_service` (SBV web scrape) | Persisted. Real từ SBV (3.0%) |
| usd_vnd_exchange | Raw | ✅ **CÓ** | `macro_service` (yfinance VND=X) | Persisted. Real data |
| cpi | Raw | ✅ **CÓ** | `macro_service` (vi.money GSO) | Persisted. Real từ GSO (free, no key) |
| cpi_headline_index | Raw | ✅ **CÓ** | Tương tự | Persisted |
| cpi_mom_pct | Raw | ✅ **CÓ** | Tương tự | Persisted |
| ppi | Raw | ❌ **FALLBACK** | (hardcode 2.1) | |
| gdp_growth | Raw | ❌ **FALLBACK** | (hardcode 6.2) | |
| inflation_rate | Raw | ❌ **FALLBACK** | (hardcode 3.2) | |
| unemployment_rate | Raw | ❌ **FALLBACK** | (hardcode 2.3) | |
| gold_price_vnd | Calc | ✅ **CÓ** | `macro_service` (GC=F × VND=X × 1.21528) | Persisted. Real data |
| oil_price_brent | Raw | ✅ **CÓ** | `macro_service` (yfinance BZ=F + fallback) | Persisted |
| usd_index | Raw | ✅ **CÓ** | `macro_service` (yfinance DX-Y.NYB + fallback) | Persisted |
| usd_10y_yield | Raw | ✅ **CÓ** | `macro_service` (yfinance ^TNX + fallback) | Persisted |
| vix | Raw | ✅ **CÓ** | `macro_service` (yfinance ^VIX + fallback) | Persisted |

---

## PHẦN 13: RAG DOCUMENTS (Annual Reports)

| Field | Type | Status | Vị trí | Ghi chú |
|-------|------|--------|--------|---------|
| document_type | Raw | ❌ **THIẾU** | `app/brain/tools/vn_qualitative_rag_tool.py` crawl PDF từ CafeF | Chưa có structured metadata |
| fiscal_year | Raw | ❌ **THIẾU** | | |
| title | Raw | ❌ **THIẾU** | | |
| url | Raw | ⚠️ **CÓ** | PDF URLs từ CafeF | |
| file_path | Raw | ❌ **THIẾU** | | |
| file_size | Raw | ❌ **THIẾU** | | |
| total_pages | Raw | ❌ **THIẾU** | | |
| published_date | Raw | ❌ **THIẾU** | | |
| uploaded_date | Raw | ❌ **THIẾU** | | |
| text_content (chunk) | Calc | ⚠️ **CÓ 1 PHẦN** | `app/brain/tools/vn_qualitative_rag_tool.py` dùng pypdfium2 | Chưa persist (in-memory TF-IDF) |
| chunk_index | Calc | ❌ **THIẾU** | | |
| page_number | Calc | ❌ **THIẾU** | | |
| char_count | Calc | ❌ **THIẾU** | | |
| vector_embedding | Calc | ❌ **THIẾU** | Hiện tại dùng TF-IDF trong NewsRAGService | Chưa có embedding model |
| is_processed | Calc | ❌ **THIẾU** | | |
| chunk_count | Calc | ❌ **THIẾU** | | |
| vector_db_id | Calc | ❌ **THIẾU** | Chưa có vector DB (pgvector, Chroma) | |

---

## TỔNG HỢP GAP ANALYSIS

### RAW DATA THIẾU NẶNG (cần crawl/lấy mới)

| Nhóm | Fields thiếu | Ưu tiên |
|------|-------------|---------|
| **Financial Statements** (~80 fields) | Balance Sheet, Income Statement, Cash Flow chi tiết — đã có qua `data_enricher.py` on-demand + fallback, nhưng **chưa persist DB** | 🔴 **CAO** |
| **Macro Data** (~25 fields) | USD/VND & gold **đã real** (yfinance). CPI, GDP, lãi suất SBV vẫn hardcode | 🟡 **TB** |
| **Corporate Actions** (~15 fields) | dividend dates, splits, rights, adjustment factors | 🔴 **CAO** |
| **Stock Profile** (~20 fields) | Đã có qua `data_enricher.py` (vnstock + fallback), cần persist | 🟢 **THẤP** |
| **Insider Trading** (~11 fields) | Structured insider transactions (quantity, price, value) | 🟡 **TRUNG BÌNH** |
| **Order Book** (~12 fields) | Bid/Ask Level 2 (hiện chỉ real-time, ko persist) | 🟢 **THẤP** |
| **Foreign Flow** (~8 fields) | Đã có qua `data_enricher.py:fetch_foreign_flow()`, cần persist DB | 🟡 **TRUNG BÌNH** |
| **VN Index returns** (~5 fields) | **Đã real** (vietfin DNSE), fallback khi API lỗi | 🟢 **THẤP** |

### TÍNH TOÁN THIẾU (cần xây compute engine)

| Nhóm | Fields thiếu | Ưu tiên |
|------|-------------|---------|
| **Beta/Alpha thực** | Hiện hash-based, cần VNINDEX OHLCV trong DB để tính real covariance | 🟡 **TB** |
| **YoY Growth real** | ✅ ĐÃ CÓ multi-period, real khi có 4+ quý dữ liệu | 🟢 **THẤP** |
| **Enhanced Sentiment** | Transformer model (PhoBERT) thay lexicon | 🟢 **THẤP** |
| **RAG Embeddings** | Vector DB, embedding model cho annual reports | 🟢 **THẤP** |

### DATABASE THIẾU (cần tạo table mới)

| Table | Mục đích | Priority | Status |
|-------|----------|----------|--------|
| `macro_indicators` | Time-series macro data | 🔴 **CAO** | ✅ **CREATED** (TimescaleDB hypertable) |
| `technical_indicators` | Cache indicators | 🟡 **TB** | ✅ **413 symbols** |
| `financial_ratios` | Lưu tất cả ratios | 🟡 **TB** | ⚠️ **3 symbols (vnstock limit)** |
| `factor_scores` | Composite factor scores | 🟢 **THẤP** | ✅ **416 symbols** |
| `risk_flags` | Lịch sử risk flags | 🟢 **THẤP** | ✅ **139 flags** |
| `financial_statements` | Lưu Balance Sheet, Income Statement, Cash Flow | 🔴 **CAO** | ⚠️ **3 symbols (vnstock limit)** |
| `insider_trades` | Structured insider transactions | 🟡 **TB** | ✅ **CREATED (empty)** |
| `alpha_signals` | Alpha factor output cache | 🟢 **THẤP** | ✅ **2,912 rows** |
| `corporate_actions` | Dividend history, splits, rights | 🔴 **CAO** | ✅ **CREATED + DATA (2,556 rows)** |
| `risk_metrics` | Sharpe, Beta, VaR theo symbol | 🟢 **THẤP** | ❌ **CHƯA TẠO** |

---

## KIẾN TRÚC XỬ LÝ HIỆN TẠI

```
Data Sources:
├── DNSE REST API ───→ OHLCV backfill ───→ TimescaleDB (ohlcv table) ✅
│                    └── Fundamentals ───→ On-demand (ko persist) ⚠️
├── DNSE WebSocket ──→ Real-time quotes ──→ Redis pub/sub ──→ Frontend ✅
│                    ├── Order Book ──────→ Frontend (ko persist) ⚠️
│                    └── Foreign (room, buy/sell) ──→ `hub._foreign` + Redis cache ⚠️
├── vnstock ─────────→ Financials, Profile, Listing ──→ `data_enricher.py` on-demand ⚠️
├── CafeF ───────────→ News ──→ RAG (TF-IDF in-memory) ──→ news table (backend) ⚠️
│                    └── PDF annual reports ──→ On-demand extraction ⚠️
├── yfinance ────────→ Global stocks, fundamentals, macro commodities ──→ `macro_service` → `macro_indicators` table ✅
├── vi.money ───────────────→ CPI (GSO source, free, no key) ──→ `macro_service` → `macro_indicators` table ✅
├── SBV (sbv.gov.vn) ───────→ Policy interest rates (refinancing, discount) ──→ `macro_service` → `macro_indicators` table ✅
├── vietfin (DNSE) ───→ VN Index VNINDEX history ──→ `macro_service` → `macro_indicators` table ✅
├── Vimo MCP ─────────→ Lending rates (optional, VIMO_API_KEY) ──→ `macro_service` → `macro_indicators` table ⚠️
└── UBCKNN ──────────→ Regulatory disclosures ──→ On-demand (via scraper) ⚠️

Compute:
├── **macro_service** ──→ **Macro indicators ETL** (DB-backed, persisted) ✅
│   ├── `get_latest_macro()` → Read from `macro_indicators` table, fallback to on-demand fetch + persist
│   ├── `refresh_macro()` → Force fetch from yfinance/vi.money/SBV/VietFin, upsert to DB
│   ├── `get_macro_history()` → Historical values for any indicator
│   └── Fetches: oil, DXY, US 10y, VIX, USD/VND, gold VND, VNINDEX returns, CPI, refinancing/discount/lending rates
├── **DataEnricher** ───→ **Central compute engine** (technical, risk, returns, ratios, profile, risk flags, factors, extras)
│   ├── `compute_technical_indicators()` → 40+ indicators (MA, RSI, MACD, Stoch, ADX, MFI, BB, ATR, OBV, Momentum...)
│   ├── `compute_risk_metrics()` → 30+ metrics (Sharpe, Sortino, Calmar, VaR, CVaR, Beta, Alpha, Treynor, IR, Drawdown...)
│   ├── `fetch_vnstock_financials()` → Full financials + 30+ ratios (P/E, P/B, EV/EBITDA, ROIC, FCF, growth rates...)
│   ├── `fetch_vnstock_profile()` → 25+ profile fields (CEO, employees, listing_date, free_float, website...)
│   ├── `get_macro_indicators()` → NOW reads from `macro_service` (DB-backed), inline fallback if service unavailable
│   ├── `evaluate_risk_flags()` → 7 risk flags (DELIST, CFO, DELAYED, AUDITOR, PLEDGE, LAWSUIT, LOSS)
│   ├── `compute_market_extras()` → avg_volume, turnover_rate, VWAP, value
│   ├── `compute_spread()` → bid-ask spread, spread_pct
│   ├── `fetch_foreign_flow()` → foreign buy/sell qty & value, net, ownership, room
│   ├── `compute_factor_scores()` → 7 factor scores + total + percentile
│   └── `compute_sentiment_rolling()` → rolling sentiment + news count (1d/5d/10d)
├── ML Alpha Predictor ──→ Features: RSI, MACD, BB, ATR, VPT, Momentum, Volatility, Volume MA
│                        └── Model: XGBoost / RF → forward 5d return
├── Factor Zoo ─────────→ 450+ alpha factors (WorldQuant 101, GTJA 191, Qlib 158, Academic)
│                        └── Registry: lazy-load, compute-on-demand
├── Risk Flags ─────────→ 8+ flags từ price/volume/news/fundamental (bổ sung cho data_enricher)
├── Backtest Metrics ───→ Sharpe, Sortino, Calmar, VaR, CVaR, GARCH, Max DD, IR
└── Sentiment ─────────→ Lexicon-based scoring (TF-IDF RAG)
```

---

## TÓM TẮT SỐ LIỆU

| Khoản mục | Count | Status |
|-----------|-------|--------|
| **Total classification fields** | ~492 | — |
| **Đã implement (Raw + Calc)** | ~444 | **90.2%** |
| **Thiếu Raw Data cần crawl** | ~33 | **6.7%** |
| **Thiếu Calculated cần compute engine** | ~15 | **3.1%** |
| **Database tables hiện tại** | 22 | All 9 Roadmap tables + 13 Prisma (user/social/trade) + corporate_actions |
| **Database tables thiếu** | 1 | risk_metrics |
| **OHLCV rows** | 1,079,173 | 909 distinct symbols, last date 2026-05-25 |
| **Macro indicators** | 4,143 | 13 indicators, 2-year history |
| **Corporate actions** | 2,556 | Dividends + splits for top 300 symbols via yfinance |
| **Technical indicators** | 413 | 40+ indicators per symbol (MA, RSI, MACD, BB, ATR, OBV...) |
| **Risk flags** | 139 | Price/volume flags from OHLCV (limit-down, spike, low liq) |
| **Factor scores** | 416 | 7-factor composite: value, quality, momentum, size, volatility |
| **Alpha signals** | 2,912 | 7 alpha IDs cross-sectionally ranked (416 symbols × 7) |
| **Tất cả tính toán đã có trong** | `data_enricher.py` | On-demand, nhiều fallback |
| **Macro data** | ✅ **PERSISTED** | `macro_service` → `macro_indicators` table (TimescaleDB hypertable), 24h TTL |
| **Cần persist DB** | Financials, Profile | Hiện chỉ on-demand qua `data_enricher.py` |
| **daily_etl.py persistence** | ⚠️ **1/4 persisted** | `technical_indicators` ✅; `financial_statements` ❌ ko đc gọi; `financial_ratios` ❌ stub; `risk_flags` ❌ stub |

---

## HƯỚNG DẪN SỬ DỤNG CHO AI

Khi được hỏi về bất kỳ field dữ liệu chứng khoán nào:
1. **Tra cứu trong file này** — tra trạng thái tại cột "Status"
2. **Nếu status là ✅ CÓ** — Tìm file tương ứng trong codebase để biết implementation
3. **Nếu status là ✅ CÓ (data_enricher.py)** — Hầu hết các field tính toán đều có trong `ai-engine/app/services/data_enricher.py`
4. **Macro data** (`cpi`, `refinancing_rate`, `gold_price_vnd`, `vnindex_return_*`, ...) → Đã persist trong `macro_indicators` table (TimescaleDB hypertable). Đọc qua `macro_service.get_latest_macro()` hoặc `data_enricher.get_macro_indicators()` (tự động routing qua DB).
5. **Nếu status là ❌ THIẾU** — Xác định:
   - **Raw thiếu** → Cần tạo data collector (crawl API/Web)
   - **Calc thiếu** → Cần tạo compute function
   - **Table thiếu** → Cần tạo Prisma model + SQL migration
6. **Nếu status là ⚠️ CÓ 1 PHẦN** — Kiểm tra ghi chú để biết giới hạn
7. **Lưu ý về data_enricher.py:** Dữ liệu tính toán on-demand, có fallback (mocked/hardcoded) khi API không trả về. Cần persist DB để có dữ liệu lịch sử.
8. **Lưu ý về đường dẫn file:** Một số file được liệt kê dạng short name (vd `calendar.py`, `y_finance.py`, `indicators_tool.py`) nhưng thực tế nằm dưới `app/brain/dataflows/` hoặc `app/brain/dataflows/vendors/vn/`. Tra cứu bằng glob hoặc grep nếu không tìm thấy tại path mặc định.
```
