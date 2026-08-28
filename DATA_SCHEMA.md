# DATA_SCHEMA.md — IOS v5.1
## Mô Hình Dữ Liệu Nghiệp Vụ

> **Nguyên tắc:** File này mô tả WHAT (dữ liệu là gì, quan hệ thế nào, ràng buộc gì). Không mô tả HOW (lưu ở đâu, dùng database nào). Người implement chọn database phù hợp.

---

## ENTITY 1: SECURITY (Cổ Phiếu)

**Mô tả:** Đơn vị cơ bản của Universe. Mỗi cổ phiếu niêm yết trên HOSE là một Security.

```
Security {
  ticker          : String  [PRIMARY KEY, VD: "VHM", "FPT"]
  full_name       : String
  sector          : String  [ICB sector classification]
  sub_sector      : String
  listing_date    : Date
  charter_capital : Decimal  [VND]

  // Universe Classification
  universe_group  : Enum [A, B, C, SANDBOX, EXCLUDED]
  group_updated_at: Timestamp

  // Compliance Status
  trading_status  : Enum [NORMAL, WARNING, CONTROLLED, SUSPENDED]
  beneish_status  : Enum [PASS, FAIL, PENDING]
  beneish_score   : Decimal  [null khi chưa tính]
  beneish_updated : Date
  gil_flag        : Enum [PASS, WARNING, CATASTROPHIC, PENDING]
  audit_opinion   : Enum [UNQUALIFIED, QUALIFIED, ADVERSE, DISCLAIMER]
  audit_year      : Integer  [năm của audit opinion gần nhất]
}
```

**Constraints:**
- `ticker` là duy nhất, không thay đổi sau khi tạo
- `universe_group = EXCLUDED` khi bất kỳ: `trading_status != NORMAL`, `beneish_status = FAIL`, `gil_flag = CATASTROPHIC`
- `universe_group = SANDBOX` chỉ khi thỏa mãn đồng thời: ADTV20 ≥ 2 tỷ, vốn hóa ≥ 300 tỷ, revenue growth > 25% (3 quý), net_debt/equity < 15%

---

## ENTITY 2: MARKET_DATA_DAILY (Dữ Liệu Thị Trường Ngày)

**Mô tả:** Snapshot dữ liệu thị trường mỗi phiên giao dịch, đã corporate action adjusted.

```
MarketDataDaily {
  ticker          : String  [FK → Security]
  date            : Date
  [PRIMARY KEY: (ticker, date)]

  // OHLCV — tất cả đã adjusted
  open_adj        : Decimal
  high_adj        : Decimal
  low_adj         : Decimal
  close_adj       : Decimal
  vwap            : Decimal  [tính từ continuous session only]

  // Volume phân tách
  volume_continuous : Decimal  [KHÔNG bao gồm ATC/ATO]
  volume_atc        : Decimal
  volume_ato        : Decimal

  // Foreign Flow
  foreign_buy_vol   : Decimal
  foreign_sell_vol  : Decimal
  foreign_net_vol   : Decimal  [= buy - sell, tính tự động]
  is_etf_rebalance_day : Boolean

  // Derived (tính tự động)
  adtv20_continuous : Decimal  [rolling 20 phiên, volume_continuous only]
  market_cap        : Decimal  [close_adj × shares_outstanding]
}
```

**Constraints:**
- `high_adj >= max(open_adj, close_adj)`
- `low_adj <= min(open_adj, close_adj)`
- `|close_adj / close_adj_prev - 1| <= 0.075`  (HOSE ±7% limit)
- `volume_continuous >= 0`
- Không được có data ngày T+1 khi đang tính signal ngày T

---

## ENTITY 3: CORPORATE_ACTION (Sự Kiện Doanh Nghiệp)

**Mô tả:** Ghi nhận mọi sự kiện ảnh hưởng đến giá và khối lượng lịch sử.

```
CorporateAction {
  id              : UUID  [PRIMARY KEY]
  ticker          : String  [FK → Security]
  action_type     : Enum [SPLIT, MERGE, DIVIDEND_CASH, DIVIDEND_STOCK, RIGHTS]
  ex_date         : Date  [ngày giao dịch không hưởng quyền]
  record_date     : Date
  ratio           : Decimal  [VD: split 2:1 → ratio = 2.0]
  cash_amount     : Decimal  [VND/cổ phiếu, chỉ dùng với DIVIDEND_CASH]
  adjustment_factor : Decimal  [hệ số nhân để adjust giá lịch sử]
  applied         : Boolean  [đã apply vào MarketDataDaily chưa]
}
```

**Constraint:** Khi `applied = false` → toàn bộ signal calculation bị block cho ticker đó cho đến khi applied.

---

## ENTITY 4: FINANCIAL_STATEMENT (Báo Cáo Tài Chính)

**Mô tả:** BCTC theo quý với point-in-time integrity.

```
FinancialStatement {
  id              : UUID  [PRIMARY KEY]
  ticker          : String  [FK → Security]
  fiscal_year     : Integer
  fiscal_quarter  : Integer  [1, 2, 3, 4]
  [UNIQUE: (ticker, fiscal_year, fiscal_quarter)]

  // Thời gian — QUAN TRỌNG cho point-in-time
  period_end_date     : Date  [ngày kết thúc kỳ kế toán]
  announcement_date   : Date  [ngày công bố thực tế — đây là ngày signal được phép dùng]
  is_audited          : Boolean

  // Income Statement
  revenue             : Decimal
  gross_profit        : Decimal
  gross_margin        : Decimal  [= gross_profit / revenue, tính tự động]
  ebit                : Decimal
  ebt                 : Decimal
  tax_expense         : Decimal
  effective_tax_rate  : Decimal  [= tax_expense / ebt]
  net_income          : Decimal  [thuộc cổ đông công ty mẹ]
  eps_basic           : Decimal  [đã adjusted corporate action]
  sga_expense         : Decimal
  depreciation        : Decimal

  // Balance Sheet
  total_assets              : Decimal
  current_assets            : Decimal
  cash_and_equiv            : Decimal
  receivables               : Decimal
  inventory                 : Decimal
  ppe_net                   : Decimal
  total_equity              : Decimal
  total_debt                : Decimal
  long_term_debt            : Decimal
  current_liabilities       : Decimal
  non_interest_liabilities  : Decimal
  net_debt                  : Decimal  [= total_debt - cash_and_equiv]

  // Cash Flow
  cfo                 : Decimal
  capex               : Decimal  [absolute value]
  fcf                 : Decimal  [= cfo - capex]

  // Derived Metrics (tính tự động)
  roic                : Decimal
  accrual_ratio       : Decimal
  invested_capital    : Decimal
  piotroski_f_score   : Integer  [0–9]
  piotroski_profit_subscore : Integer  [0–4]
  altman_z_score      : Decimal  [null cho financial companies]

  // Data Quality
  data_source         : String
  has_data_flag       : Boolean  [false nếu có bất kỳ field quan trọng nào null]
}
```

**Constraints:**
- Signal chỉ được tính sau `announcement_date`, KHÔNG phải `period_end_date`
- `accrual_ratio = (net_income - cfo) / total_assets`
- `roic = ebit × (1 - effective_tax_rate) / invested_capital`
  - Dùng `effective_tax_rate` chỉ khi công ty có cam kết ưu đãi thuế > 5 năm, ngược lại dùng 20%
- `piotroski_profit_subscore >= 1` là điều kiện để Value Unlock thesis trigger

---

## ENTITY 5: OWNERSHIP_RECORD (Hồ Sơ Sở Hữu)

**Mô tả:** Lịch sử sở hữu của từng cổ đông trọng yếu.

```
OwnershipRecord {
  id                  : UUID  [PRIMARY KEY]
  ticker              : String  [FK → Security]
  shareholder_id      : String  [ID pháp nhân/thể nhân]
  shareholder_name    : String
  shareholder_type    : Enum [INDIVIDUAL, CORPORATE, FOREIGN, STATE]
  is_board_member     : Boolean
  is_related_party    : Boolean

  ownership_pct       : Decimal  [% sở hữu]
  shares_held         : Decimal

  // Point-in-time
  effective_date      : Date  [ngày thay đổi sở hữu thực tế]
  disclosure_date     : Date  [ngày SSC công bố — ĐÂY là signal date]

  related_entity_id   : String  [null nếu không có liên kết]
  notes               : String
}
```

**Constraint:** `disclosure_date >= effective_date`. Signal date = `disclosure_date`.

---

## ENTITY 6: INSIDER_TRANSACTION (Giao Dịch Nội Bộ)

**Mô tả:** Giao dịch cổ phiếu của người nội bộ.

```
InsiderTransaction {
  id                  : UUID  [PRIMARY KEY]
  ticker              : String  [FK → Security]
  insider_id          : String  [FK → OwnershipRecord.shareholder_id]
  insider_role        : String  [CEO, CFO, Chairman, Director, Major_Shareholder]

  transaction_type    : Enum [BUY_MARKET, SELL_MARKET, BUY_AGREEMENT,
                              SELL_AGREEMENT, TRANSFER_INTERNAL, ESOP]
  volume              : Decimal
  price               : Decimal
  total_value         : Decimal  [= volume × price]

  // Point-in-time
  transaction_date    : Date
  disclosure_date     : Date  [signal date]

  // Chỉ dùng trong F4.3 khi type IN [BUY_MARKET, SELL_MARKET, BUY_AGREEMENT, SELL_AGREEMENT]
  is_signal_eligible  : Boolean  [tính tự động từ transaction_type]
}
```

---

## ENTITY 7: FACTOR_SCORE (Điểm Factor)

**Mô tả:** Lưu toàn bộ factor scores tại thời điểm tính. Cần cho Learning Agent.

```
FactorScore {
  id          : UUID  [PRIMARY KEY]
  ticker      : String  [FK → Security]
  date        : Date  [ngày tính]
  regime      : Enum [Bull_Trending, Bull_Choppy, Bear_Trending, Bear_Bounce]

  // 6 nhóm factor (0–100 percentile rank trong Universe ngày đó)
  f1_value    : Decimal  [null nếu không tính được]
  f2_quality  : Decimal
  f3_momentum : Decimal
  f4_earnings : Decimal
  f5_flow     : Decimal
  f6_technical : Decimal

  // Composite
  css         : Decimal  [0–100]
  conviction  : Enum [A_PLUS, A, B, C, D]

  // Moat
  moat_score        : Decimal  [0–100, null nếu chưa có docs]
  moat_intangible   : Decimal
  moat_switching    : Decimal
  moat_network      : Decimal
  moat_cost         : Decimal
  moat_hallucination_risk : Enum [LOW, MEDIUM, HIGH]

  // Meta
  universe_size     : Integer  [số ticker trong Universe ngày đó — để normalize]
  data_completeness : Decimal  [% factors có data đủ]
}
```

---

## ENTITY 8: INVESTMENT_THESIS (Luận Điểm Đầu Tư)

**Mô tả:** Thesis đầy đủ cho mỗi quyết định đầu tư.

```
InvestmentThesis {
  thesis_id       : UUID  [PRIMARY KEY]
  ticker          : String  [FK → Security]
  created_at      : Timestamp
  created_by      : String  [agent: Thesis_Agent]
  regime_at_creation : Enum

  // Thesis Content
  catalyst_type   : Enum [EARNINGS_SURPRISE, SECTOR_ROTATION,
                          UNDERVALUATION, VALUE_UNLOCK, MOMENTUM_CONTINUATION]
  catalyst_detail : String  [mô tả cụ thể]
  timeline_months : Integer  [1, 3, 6]
  target_price_low  : Decimal
  target_price_high : Decimal
  entry_price     : Decimal  [null cho đến khi mua thực tế]

  // 3 Confirming Signals (Hard Law Điều 3)
  signal_1        : String  [tên signal]
  signal_1_source : String  [nguồn độc lập]
  signal_2        : String
  signal_2_source : String
  signal_3        : String
  signal_3_source : String

  // Invalidation
  invalidation_condition_1 : String
  invalidation_condition_2 : String
  invalidation_condition_3 : String

  // Pre-mortem
  failure_scenario_1  : String
  failure_scenario_2  : String
  failure_scenario_3  : String

  // Status
  status          : Enum [DRAFT, ACTIVE, INVALIDATED, COMPLETED, STOPPED]
  status_updated_at : Timestamp
  outcome         : Enum [WIN, LOSS, BREAKEVEN, null]
  realized_return : Decimal  [null cho đến khi close]
}
```

**Constraint:**
- `signal_1_source`, `signal_2_source`, `signal_3_source` phải là 3 nguồn khác nhau
- Không được tạo thesis khi `gil_flag = CATASTROPHIC`

---

## ENTITY 9: RISK_ASSESSMENT (Đánh Giá Rủi Ro)

**Mô tả:** Snapshot rủi ro danh mục cuối mỗi phiên.

```
RiskAssessment {
  id              : UUID  [PRIMARY KEY]
  date            : Date
  timestamp       : Timestamp  [giờ tính]

  // Portfolio Risk
  portfolio_es_975      : Decimal  [Expected Shortfall 97.5%]
  portfolio_es_window   : Integer  [số phiên dùng — target 500]
  drawdown_from_peak    : Decimal  [% từ NAV peak]
  drawdown_protocol     : Enum [NORMAL, ALERT, YELLOW, ORANGE, RED]

  // Cash & Exposure
  cash_target_pct       : Decimal  [từ GARCH model]
  current_cash_pct      : Decimal
  gross_exposure_pct    : Decimal
  net_exposure_pct      : Decimal

  // VN30F Hedge
  hedge_active          : Boolean
  hedge_ratio           : Decimal  [0.0–0.8]
  vn30f_short_contracts : Integer

  // CDC
  cdc_active            : Boolean
  kelly_multiplier      : Decimal  [0.25 normal, 0.125 khi CDC]

  // Breach Flags
  es_breach             : Boolean  [ES > 4% NAV]
  concentration_breach  : Boolean
  sector_breach         : Boolean
}
```

---

## ENTITY 10: PORTFOLIO (Danh Mục)

**Mô tả:** Trạng thái danh mục tại một thời điểm.

```
Portfolio {
  snapshot_id     : UUID  [PRIMARY KEY]
  date            : Date
  timestamp       : Timestamp

  nav             : Decimal  [VND]
  peak_nav        : Decimal  [NAV cao nhất từ trước đến nay]
  cash_amount     : Decimal
  cash_pct        : Decimal

  // Tổng hợp
  total_positions : Integer
  gross_exposure  : Decimal
  regime          : Enum
}
```

---

## ENTITY 11: POSITION (Vị Thế)

**Mô tả:** Một vị thế cổ phiếu trong danh mục.

```
Position {
  position_id     : UUID  [PRIMARY KEY]
  ticker          : String  [FK → Security]
  thesis_id       : UUID  [FK → InvestmentThesis]
  opened_at       : Timestamp
  closed_at       : Timestamp  [null nếu đang mở]

  // Entry
  entry_price     : Decimal
  shares          : Decimal
  cost_vnd        : Decimal  [= entry_price × shares]
  cost_pct_nav    : Decimal  [% NAV lúc mở]

  // Current (cập nhật mỗi phiên)
  current_price   : Decimal
  current_value   : Decimal
  unrealized_pnl  : Decimal
  unrealized_pnl_pct : Decimal
  pct_nav_current : Decimal

  // Stop-loss
  stop_loss_price : Decimal  [= entry_price × (1 - stop_pct)]
  stop_loss_pct_nav : Decimal  [= 2% — Hard Law]
  stop_loss_triggered : Boolean

  // Exit
  exit_price      : Decimal  [null nếu đang mở]
  realized_pnl    : Decimal
  exit_reason     : Enum [STOP_LOSS, TARGET_REACHED, THESIS_INVALIDATED,
                          TIMELINE_EXPIRED, PORTFOLIO_REBALANCE, RISK_REDUCTION]
}
```

**Constraints:**
- `cost_pct_nav <= 0.15` (Hard Law Điều 4)
- `stop_loss_pct_nav <= 0.02` (Hard Law Điều 1)
- Mỗi position phải có `thesis_id` hợp lệ

---

## ENTITY 12: ORDER (Lệnh)

**Mô tả:** Ghi nhận mọi lệnh từ lúc tạo đến khi fill/hủy.

```
Order {
  order_id        : UUID  [PRIMARY KEY]
  position_id     : UUID  [FK → Position]
  ticker          : String
  direction       : Enum [BUY, SELL]

  // Instruction (từ Portfolio Agent)
  instructed_size_vnd  : Decimal
  instructed_max_price : Decimal
  urgency              : Enum [LOW, MEDIUM, HIGH, EMERGENCY]
  execution_mode_hint  : Enum [NORMAL, STRESS, CRISIS]

  // Execution (từ Execution Agent)
  execution_mode_actual : Enum [NORMAL, STRESS, CRISIS]
  fill_price           : Decimal  [null nếu chưa fill]
  fill_volume          : Decimal
  fill_value_vnd       : Decimal
  fill_timestamp       : Timestamp
  status               : Enum [PENDING, PARTIAL, FILLED, CANCELLED, FAILED]

  // Slippage
  expected_slippage_bps : Decimal
  actual_slippage_bps   : Decimal  [null cho đến khi fill]
  slippage_variance     : Decimal  [actual - expected]

  // Audit
  created_by           : String  [Portfolio_Agent hoặc Monitoring_Agent]
  created_at           : Timestamp
}
```

---

## ENTITY 13: PERFORMANCE_METRIC (Số Liệu Hiệu Năng)

**Mô tả:** Theo dõi hiệu năng danh mục và từng factor theo thời gian.

```
PerformanceMetric {
  id              : UUID  [PRIMARY KEY]
  date            : Date
  period_type     : Enum [DAILY, WEEKLY, MONTHLY, QUARTERLY, ANNUAL]

  // Portfolio Performance
  nav_return      : Decimal  [% return trong period]
  vnindex_return  : Decimal  [benchmark]
  active_return   : Decimal  [= nav_return - vnindex_return]
  sharpe_ratio    : Decimal
  max_drawdown    : Decimal
  win_rate        : Decimal  [% trades có return > 0]

  // Factor IC
  ic_f1_value     : Decimal  [Information Coefficient]
  ic_f2_quality   : Decimal
  ic_f3_momentum  : Decimal
  ic_f4_earnings  : Decimal
  ic_f5_flow      : Decimal
  ic_f6_technical : Decimal
  ic_composite    : Decimal

  // By Regime (chỉ relevant periods)
  regime          : Enum  [null cho tổng thể]
}
```

---

## ENTITY 14: LEARNING_RECORD (Hồ Sơ Học Tập)

**Mô tả:** Ghi nhận mọi insight Learning Agent rút ra để cải thiện hệ thống.

```
LearningRecord {
  id              : UUID  [PRIMARY KEY]
  created_at      : Timestamp
  record_type     : Enum [IC_DECAY, SLIPPAGE_SPIKE, FACTOR_RETIRE_PROPOSAL,
                          WEIGHT_CHANGE_PROPOSAL, ASSUMPTION_VALIDATION,
                          QUARTERLY_REVIEW]

  // Content
  subject         : String  [Factor name / assumption ID / etc.]
  finding         : String  [mô tả phát hiện]
  evidence        : String  [số liệu cụ thể]
  proposed_action : String
  severity        : Enum [INFO, WARNING, CRITICAL]

  // Lifecycle
  status          : Enum [OPEN, UNDER_REVIEW, APPROVED, REJECTED, IMPLEMENTED]
  reviewed_by     : String  [Governance_Agent / CIO_Agent]
  reviewed_at     : Timestamp
  implemented_at  : Timestamp
}
```

---

## RELATIONSHIPS

```
Security ──────────────────────── 1:N ── MarketDataDaily
Security ──────────────────────── 1:N ── CorporateAction
Security ──────────────────────── 1:N ── FinancialStatement
Security ──────────────────────── 1:N ── OwnershipRecord
Security ──────────────────────── 1:N ── InsiderTransaction
Security ──────────────────────── 1:N ── FactorScore
Security ──────────────────────── 1:N ── InvestmentThesis
InvestmentThesis ───────────────── 1:1 ── Position
Position ───────────────────────── 1:N ── Order
Portfolio ──────────────────────── 1:N ── Position  [snapshot link]
FactorScore ─────────────────────── N:1 ── InvestmentThesis  [same ticker, same date]
InvestmentThesis ───────────────── 1:1 ── RiskAssessment  [per-position risk snapshot at entry]
LearningRecord ──────────────────── N:1 ── FactorScore  [learning từ factor performance]
```

---

## GLOBAL CONSTRAINTS

```
1. Mọi entity có financial data phải có timestamp nguồn gốc (data_source, created_at)

2. Point-in-time rule: Signal ngày T chỉ được dùng data có announcement_date ≤ T

3. Immutability: Order, Position (sau khi closed), InvestmentThesis (sau khi completed)
   KHÔNG được xóa hoặc update — chỉ được thêm records mới

4. Cascade constraint:
   - Security.universe_group = EXCLUDED → không có FactorScore mới được tạo
   - Security.beneish_status = FAIL → InvestmentThesis không được tạo
   - Security.gil_flag = CATASTROPHIC → InvestmentThesis không được tạo

5. Audit constraint: Mọi thay đổi trạng thái (Position.status, Order.status,
   InvestmentThesis.status) phải có timestamp và agent_id của người thay đổi

6. Không có orphan records: Position phải có thesis_id hợp lệ.
   Order phải có position_id hợp lệ.
```