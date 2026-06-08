# AIInvest — Roadmap & Technical Blueprint
## Autonomous AI Trading System cho Thị Trường Chứng Khoán Việt Nam

> **Mục tiêu tối thượng:** Hệ thống AI có thể tự phân tích, ra quyết định, và thực thi lệnh trên VNINDEX với độ chính xác vượt benchmark, bắt đầu từ paper trading → live trading sau 6 tháng validation.

---

# PHẦN I — TỔNG QUAN KIẾN TRÚC TỐI ƯU

## 1.1 Nguyên tắc thiết kế cốt lõi

**Accuracy-first, speed-second:** Với thị trường VN (T+2 settlement, thanh khoản thấp hơn US), một signal sai gây thiệt hại lớn hơn một signal đến chậm 30 giây. Ưu tiên: đúng > nhanh > đầy đủ.

**Data integrity là nền tảng:** Mọi model, mọi agent đều vô nghĩa nếu input data sai. adj_close, risk flags thực, corporate actions là prerequisite không thể bỏ qua.

**Pre-compute, không on-demand:** Tất cả indicators, factors, và ML features phải được tính sẵn vào DB. Agent chỉ đọc, không tính.

**Single source of truth cho agent logic:** 1 ReAct loop, 1 AgentCore class, không duplicate logic giữa AgentLoop và SwarmWorker.

**Eval-driven development:** Không thêm feature mới nếu chưa có metric chứng minh feature cũ hoạt động.

## 1.2 Kiến trúc 5 tầng

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1 — DATA QUALITY & GROUNDING                             │
│  adj_close (vnstock) · risk flags (CafeF) · corporate actions  │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓ ETL daily
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2 — SIGNAL ENGINE (pre-computed)                         │
│  30 VN-core factors · ML ensemble · technical cache · sentiment │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓ signals
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3 — AGENT BRAIN (unified AgentCore)                      │
│  Bull/Bear debate · 12 agents · LLM router · swarm DAG          │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓ decision
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 4 — DECISION GATE (critical filter)                      │
│  Confidence ≥ 0.65 · grounding check · risk flags · Kelly size  │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓ order
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 5 — EXECUTION + EVAL LOOP                                │
│  Paper trading T+2 · eval framework · shadow account · retrain  │
└─────────────────────────────────────────────────────────────────┘
         ↑ feedback (retrain signal về Layer 2)
```

---

# PHẦN II — DATA LAYER (LAYER 1)

## 2.1 Vấn đề nghiêm trọng cần fix TRƯỚC KHI làm bất cứ thứ gì

### 2.1.1 adj_close — ✅ ĐÃ IMPLEMENT (1,176,248 rows)

**Tại sao quan trọng:** Nếu không adjust dividend và stock split, backtest sẽ hiện "gap" giảm giá giả (do chia cổ tức) → momentum factor tính ngược, return không thực.

> ⚠️ **Đã xác nhận:** `yf.Ticker("VCB.VN")` không hoạt động — yfinance không support VN stocks. `vnstock Company.events()` có thể dùng nhưng **implementation hiện tại đọc từ `corporate_actions` table** đã được populate từ yfinance (global symbols) và các nguồn khác.

**✅ Implementation thực tế:**
```python
# app/brain/dataflows/vendors/vn/adj_close.py
"""
Adj Close Pipeline — corporate_actions + OHLCV → adj_close/adj_factor
Full refresh or incremental per-symbol update.
"""

def compute_adj_for_symbol(cur, symbol: str) -> int:
    """Compute adj_close/adj_factor for one symbol. Returns row count updated."""
    ohlcv = get_ohlcv_prices(cur, symbol)
    if len(ohlcv) < 2:
        return 0

    actions = get_corporate_actions(cur, symbol)  # Đọc từ corporate_actions TABLE
    if not actions:
        # No corporate actions → set adj_factor = 1.0 for all rows
        cur.execute(
            """UPDATE ohlcv SET adj_factor = 1.0, adj_close = ROUND(close)
               WHERE symbol = %s AND adj_factor IS DISTINCT FROM 1.0""",
            (symbol,),
        )
        return cur.rowcount

    # Build adjustment factors chronologically
    adj_map: dict[date, float] = {}
    for action_date, action_type, value, ratio in actions:
        if action_type == "DIVIDEND":
            adj_map.setdefault(action_date, 1.0)
            adj_map[action_date] *= 1.0 - value  # Cash dividend adjustment
        elif action_type in ("SPLIT", "RIGHTS"):
            adj_map.setdefault(action_date, 1.0)
            adj_map[action_date] *= (ratio if action_type == "SPLIT" else 1.0 + ratio)

    # Backward adjustment: cumulative factor applied to all earlier rows
    sorted_dates = sorted(adj_map.keys())
    cumulative = 1.0
    for i, (dt, close_price) in enumerate(ohlcv):
        while sorted_dates and dt >= sorted_dates[0]:
            cumulative *= adj_map.pop(sorted_dates.pop(0))
        if cumulative != 1.0:
            adj_close = round(close_price * cumulative)
            if adj_close != close_price:
                cur.execute(
                    """UPDATE ohlcv SET adj_factor = %s, adj_close = %s
                       WHERE symbol = %s AND time::date = %s""",
                    (round(cumulative, 6), adj_close, symbol, dt),
                )
    return len(ohlcv)
```

**DB schema hiện tại:**
```sql
-- corporate_actions table — ĐÃ TẠO (2,556 rows)
CREATE TABLE corporate_actions (
    symbol       TEXT NOT NULL,
    action_date  DATE NOT NULL,
    action_type  TEXT NOT NULL, -- 'DIVIDEND', 'SPLIT', 'RIGHTS'
    value        FLOAT NOT NULL,
    ratio        FLOAT,
    currency     TEXT DEFAULT 'VND',
    source       TEXT DEFAULT 'yfinance',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, action_date, action_type)
);

-- adj_close và adj_factor đã có trong ohlcv table
-- ohlcv table: 1,079,173 rows, 909 distinct symbols
```

### 2.1.2 Risk Flags — ✅ ĐÃ IMPLEMENT (10 computed flags, không scraper)

**HIỆN TẠI:** ✅ **Risk flags v2 đã thay thế** — dùng `risk_flags_v2.py` với **10 computed flags** từ structured data trong DB. Không cần scraper nào.

> ⚠️ **Đã xác nhận:** SSC.gov.vn dùng Oracle WebCenter Portal → không scrape được. CafeF regulatory (xu-phat.chn, canh-bao.chn, huy-niem-yet.chn) trả về 404. HOSE hsx.vn là React SPA. **Cả 3 nguồn external regulatory đều không dùng được.**
>
> **Giải pháp đúng:** Computed flags từ structured data đã có trong DB — không cần scraper.

**✅ Implementation thực tế:**
```python
# app/services/risk_flags_v2.py
"""Risk Flags V2 — Batch computed flag engine.

Replaces old per-symbol RAG-based risk_flags.py. Computes 10 flags from
structured data already in the DB (financial_statements, technical_indicators,
foreign_flow, insider_trades, news_events). No scraping, no RAG fallback.
"""

HARD_FLAGS = {"CANH_BAO_TC", "CHAM_BAO_TC", "DEBT_DANGER", "DEBT_DANGER_FIN", "CAR_DANGER"}
SOFT_FLAGS = {
    "FLOOR_TRAP", "SHARP_DROP", "KHOI_LUONG_BAT_THUONG",
    "FOREIGN_FLOW_ANOMALY", "INSIDER_SELLING_ANOMALY", "GOVERNANCE_SHOCK",
    "M_SCORE_FLAG", "F_SCORE_FLAG",
    "LIQUIDITY_DANGER", "VOLATILITY_DANGER", "EARNINGS_QUALITY",
}
ALL_FLAGS = HARD_FLAGS | SOFT_FLAGS

# 10 computed flags:
# 1. CANH_BAO_TC (HARD)    — financial_statements: period có "Cảnh báo"
# 2. CHAM_BAO_TC (HARD)    — period_end > 60 ngày
# 3. DEBT_DANGER (HARD)    — D/E > 3.0 (non-financial)
# 4. DEBT_DANGER_FIN (HARD)— NPL > 5% (financial sector)
# 5. CAR_DANGER (HARD)     — CAR < 8% (financial sector)
# 6. FLOOR_TRAP (SOFT)     — momentum_1d ≤ -6.9% ≥ 2 phiên (price data)
# 7. SHARP_DROP (SOFT)     — momentum_1d ≤ -7% (price data)
# 8. KHOI_LUONG_BAT_THUONG (SOFT) — volume_ratio ≥ 3.0
# 9. FOREIGN_FLOW_ANOMALY (SOFT)  — net sell ≥ 5 ngày liên tiếp
# 10. INSIDER_SELLING_ANOMALY (SOFT) — net sell > 2× buy, qty > 100k

def refresh_incremental() -> dict:
    """Batch compute — single DB pass per tier. All functions are batch."""
    ...
```

**DB table hiện tại — risk_flags table (139 flags đã được tính):**
```sql
CREATE TABLE risk_flags (
    id             SERIAL PRIMARY KEY,
    symbol         TEXT NOT NULL,
    flag_type      TEXT NOT NULL,
    effective_date DATE,
    lifted_date    DATE,
    description    TEXT,
    source_url     TEXT,
    source         TEXT DEFAULT 'vn',
    is_active      BOOLEAN DEFAULT TRUE,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, flag_type, effective_date)
);
```

### 2.1.3 Macro indicators — ✅ ĐÃ IMPLEMENT (persisted, 4,143 rows)

> ⚠️ **Đã xác nhận:** `api.vimo.vn/v1/rates/lending` không phải public API. **Giải pháp thực tế:** 5-source pipeline, tất cả persist vào `macro_indicators` table (TimescaleDB hypertable). CafeF không dùng cho rates.

**✅ Implementation thực tế — 5 nguồn:**
```python
# app/services/macro_service.py
"""
Macro Indicators Service — fetch, persist, and retrieve from PostgreSQL.
ETL writes daily snapshot; app reads from DB with 24h TTL fallback.
"""

def _fetch_all_macro() -> Dict[str, Any]:
    """Fetch all macro indicators from public APIs."""
    res: Dict[str, Any] = {}

    # 1. yfinance global — oil, DXY, US 10y, VIX, USD/VND, gold
    _fetch_yfinance(res)

    # 2. VietFin (DNSE) — VNINDEX returns (1d/1m/3m/1y)
    _fetch_vnindex_returns(res)

    # 3. vi.money (GSO) — CPI (free, no key)
    _fetch_cpi(res)

    # 4. SBV web scrape — policy rates (refinancing, discount)
    _fetch_sbv_rates(res)

    # 5. Vimo MCP (optional) — lending rates, requires VIMO_API_KEY
    _fetch_vimo_lending(res)

    # Apply fallbacks cho indicators không fetch được
    _apply_fallbacks(res)

    return res
```

**Các indicator đã persist (macros đã có 4,143 rows):**
- `oil_price_brent` — yfinance BZ=F ✅
- `usd_index` — yfinance DX-Y.NYB ✅
- `usd_10y_yield` — yfinance ^TNX ✅
- `vix` — yfinance ^VIX ✅
- `usd_vnd_exchange` — yfinance VND=X ✅
- `gold_price_vnd` — yfinance GC=F × VND=X × 1.21528 ✅
- `vnindex_return_1d/1m/3m/1y` — VietFin/DNSE ✅
- `refinancing_rate` — SBV web scrape (4.5%) ✅
- `discount_rate` — SBV web scrape (3.0%) ✅
- `cpi/cpi_mom_pct` — vi.money (GSO) ✅
- `lending_rate_12m_big4/commercial` — Vimo MCP (optional) ⚠️

**DB table hiện tại:**
```sql
-- macro_indicators — TimescaleDB hypertable (ĐÃ TẠO, 4,143 rows)
CREATE TABLE macro_indicators (
    indicator_date DATE NOT NULL,
    indicator_name TEXT NOT NULL,
    value          FLOAT NOT NULL,
    unit           TEXT,
    source         TEXT,
    PRIMARY KEY (indicator_date, indicator_name)
);
```

## 2.2 Data Sources — Actual (đã implement)

| Source | Status | Dùng cho | Frequency | Priority |
|--------|--------|---------|-----------|----------|
| DNSE WS | ✅ Real-time | Quote, orderbook L2, foreign room real-time | Real-time Redis pub/sub | P0 |
| DNSE REST | ✅ Persisted | OHLCV backfill, fundamentals on-demand | Daily → ohlcv table | P0 |
| yfinance | ✅ Persisted | **Global macro**: oil, DXY, gold, VIX, US 10y, USD/VND | Daily → macro_indicators | P0 |
| VietFin/DNSE | ✅ Persisted | VNINDEX returns (1d/1m/3m/1y) | Daily → macro_indicators | P0 |
| vi.money (GSO) | ✅ Persisted | CPI (free, no key) | Monthly → macro_indicators | P0 |
| SBV web | ✅ Persisted | Policy rates (refinancing, discount) | Monthly → macro_indicators | P0 |
| vnstock v4 | ✅ On-demand | Financials (IS/BS/CF), profile, sector | On-demand via data_enricher | P0 |
| CafeF API | ✅ Persisted | News events, insider trades, foreign flow | Daily batch ETL | P0 |
| yfinance global | ✅ Persisted | Global macro + commodities | Daily | P1 |
| Vimo MCP | ⚠️ Optional | Lending rates (requires VIMO_API_KEY) | On-demand | P1 |
| HOSE/HNX | ⚠️ Fallback | Symbol listing, exchange info | Static | P1 |
| ~~vnstock Company.events()~~ | ❌ Not used | ~~adj_close~~ | — | — |
| ~~CafeF regulatory scrape~~ | ❌ 404/not used | ~~Risk flags (SANCTION/WARNING/DELIST)~~ | — | — |
| ~~UBCKNN Playwright~~ | ❌ Not possible | ~~Risk flags~~ | — | — |

## 2.3 DB Tables — ĐÃ TẠO (22 tables, còn thiếu 1)

> **Thực tế:** Tất cả 9 tables trong roadmap đều đã được tạo. Có tổng cộng **22 tables** (bao gồm Prisma tables cho user/social/trade). Chỉ còn thiếu `risk_metrics`.

### Đã tạo và có dữ liệu:

| Table | Rows | Status |
|-------|------|--------|
| `ohlcv` | 1,079,173 (909 symbols) | ✅ |
| `corporate_actions` | 2,556 | ✅ |
| `macro_indicators` | 4,143 (13 indicators, 2-year) | ✅ TimescaleDB hypertable |
| `technical_indicators` | 413 symbols | ✅ |
| `financial_ratios` | 3 symbols (vnstock limit) | ⚠️ |
| `financial_statements` | 3 symbols (vnstock limit) | ⚠️ |
| `factor_scores` | 416 symbols | ✅ |
| `alpha_signals` | 2,912 (7 alpha IDs × cross-section) | ✅ |
| `risk_flags` | 139 | ✅ |
| `insider_trades` | 29,599 (CafeF API) | ✅ |
| `news_events` | 14,000+ | ✅ (NEW) |
| `foreign_flow` | — | ✅ table created |
| `signals` | — | ✅ table created |
| `stocks` | — | ✅ Prisma |
| `risk_assessments` | — | ❌ (chưa tạo) |

### SQL thực tế đã dùng:
```sql
-- 1. financial_statements ✅ ĐÃ TẠO
CREATE TABLE financial_statements (
    symbol          TEXT NOT NULL,
    period_end      DATE NOT NULL,
    statement_type  TEXT NOT NULL, -- 'IS', 'BS', 'CF'
    frequency       TEXT NOT NULL, -- 'Q', 'A'
    data            JSONB NOT NULL,
    source          TEXT DEFAULT 'vnstock',
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, period_end, statement_type, frequency)
);

-- 2. technical_indicators ✅ ĐÃ TẠO (413 symbols)
CREATE TABLE technical_indicators (
    symbol      TEXT NOT NULL,
    calc_date   DATE NOT NULL,
    indicators  JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, calc_date)
);

-- 3. financial_ratios ✅ ĐÃ TẠO
CREATE TABLE financial_ratios (
    symbol      TEXT NOT NULL,
    ratio_date  DATE NOT NULL,
    pe FLOAT, pb FLOAT, roe FLOAT, roa FLOAT,
    debt_equity FLOAT, current_ratio FLOAT, gross_margin FLOAT,
    net_margin FLOAT, fcf_yield FLOAT, ev_ebitda FLOAT,
    yoy_revenue_growth FLOAT, yoy_earnings_growth FLOAT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, ratio_date)
);

-- 4. factor_scores ✅ ĐÃ TẠO (416 symbols)
CREATE TABLE factor_scores (
    symbol      TEXT NOT NULL,
    score_date  DATE NOT NULL,
    momentum_1m FLOAT, momentum_3m FLOAT, momentum_12m FLOAT,
    value_score FLOAT, quality_score FLOAT,
    size_score  FLOAT, volatility_score FLOAT,
    liquidity_score FLOAT, composite_score FLOAT,
    percentile  FLOAT,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, score_date)
);

-- 5. corporate_actions ✅ ĐÃ TẠO (2,556 rows)
-- 6. macro_indicators ✅ ĐÃ TẠO (TimescaleDB, 4,143 rows)
-- 7. risk_flags ✅ ĐÃ TẠO (139 flags)

-- 8. insider_trades ✅ ĐÃ TẠO (29,599 rows từ CafeF API)
CREATE TABLE insider_trades (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    trade_date      DATE NOT NULL,
    trader_name     TEXT,
    trader_position TEXT,
    trade_type      TEXT,
    quantity        BIGINT,
    price           FLOAT,
    value_vnd       BIGINT,
    before_pct      FLOAT,
    after_pct       FLOAT,
    source_news_id  INT REFERENCES news(id),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 9. alpha_signals ✅ ĐÃ TẠO (2,912 rows)
CREATE TABLE alpha_signals (
    symbol      TEXT NOT NULL,
    signal_date DATE NOT NULL,
    alpha_id    TEXT NOT NULL,
    tier        INT,
    raw_value   FLOAT,
    ranked_value FLOAT,
    ic_trailing_20d FLOAT,
    PRIMARY KEY (symbol, signal_date, alpha_id)
);
```

---

# PHẦN III — SIGNAL ENGINE (LAYER 2)

## 3.1 ETL Pipeline — Pre-compute daily

**Nguyên tắc:** Chạy lúc 18:00 mỗi ngày sau khi thị trường đóng cửa (14:30 HOSE), cập nhật toàn bộ cache.

```
18:00 → Backfill OHLCV + adj_close (vnstock events)
18:15 → Compute technical indicators (40+ indicators, tất cả symbols)
18:30 → Compute 30 VN-core factors (Tier 1 tất cả, Tier 2 chỉ liquid)
18:45 → Run ML predict: XGBoost (tất cả) + LSTM (top 100 only)
19:00 → Update financial_ratios từ financial_statements
19:15 → Refresh risk_flags từ CafeF scrape (không Playwright)
19:30 → Compute CRS 7 tầng → upsert risk_assessments table
19:45 → Update factor_scores cross-sectional ranking
20:00 → Update macro_indicators (SBV/CafeF rates + yfinance global)
20:15 → Run screener presets, cache results
```

```python
# app/services/daily_etl.py
class DailyETLPipeline:
    """
    Orchestrates toàn bộ daily compute pipeline.
    Chạy sau market close, không phụ thuộc vào agent request.
    """
    async def run(self, trade_date: date):
        symbols = await self.get_all_symbols()  # ~1800 symbols

        await self.step_ohlcv_backfill(symbols, trade_date)
        await self.step_adj_close(symbols)
        await self.step_technical_indicators(symbols, trade_date)
        await self.step_alpha_factors(symbols, trade_date)
        await self.step_ml_predict(symbols, trade_date)
        await self.step_financial_ratios(symbols)
        await self.step_risk_flags()
        await self.step_factor_scores(symbols, trade_date)
        await self.step_screener_cache()
        await self.step_macro_indicators(trade_date)

    async def step_technical_indicators(self, symbols, date):
        """
        Tính 40+ indicators cho tất cả symbols, upsert vào DB.
        Dùng pandas_ta để vectorized, không loop từng symbol.
        """
        import pandas_ta as ta
        # Batch process, không gọi DataEnricher on-demand
        ...
```

## 3.2 Alpha Factors — 30 VN-Core Factors

> **Quyết định:** Scope lại từ 453 → **30 VN-core factors** được chia 3 tiers theo liquidity. Alpha101/GTJA191/Qlib158 bị bỏ vì calibrated cho US, volume VN thấp hơn 100x, không validate được IC > 0.03 mà không có re-calibration lớn.

### 3.2.1 Đặc thù thị trường VN — constraints cho mọi factor

- **T+2 settlement:** Holding period tối thiểu 2 ngày, forward return tính từ T+2
- **Price limit ±7% (HOSE), ±10% (HNX):** Momentum factor cần clamp tại biên độ
- **Thanh khoản thấp:** Liquidity filter bắt buộc — loại stocks < 5 tỷ VND/ngày
- **Foreign room:** Kiểm tra room còn lại trước khi long
- **Earnings cycle:** Q1(31/3), Q2(30/6), Q3(30/9), Q4(31/12) — surprise lớn vì ít analyst

### 3.2.2 30 VN-Core Factors theo 3 Tiers

```python
# brain/quant/factors/zoo/vn_core/vn_core_factors.py

FACTOR_TIERS = {

    # TIER 1 — 10 factors, chạy hàng ngày cho TẤT CẢ stocks
    "TIER_1_ALWAYS": {
        # Momentum
        "MOM_3M_SKIP1M":    "adj_close.shift(20) / adj_close.shift(60) - 1",
        "MOM_1M":           "adj_close / adj_close.shift(20) - 1",
        # Value
        "PE_INVERSE_RANK":  "rank(1 / pe)",
        "PB_INVERSE_RANK":  "rank(1 / pb)",
        # Quality
        "ROE_STABILITY":    "roe_4q_mean / (roe_4q_std + 0.01)",
        "ACCRUAL_INVERSE":  "rank(-(ni - cfo) / total_assets)",
        # Liquidity
        "AMIHUD_INVERSE":   "rank(-abs(return) / dollar_volume_20d)",
        # Event/Flow
        "FOREIGN_NET_BUY_5D": "foreign_net_value.rolling(5).sum() / market_cap",
        "INSIDER_NET_BUY_30D": "insider_buy_qty_30d - insider_sell_qty_30d",
        "NEWS_SENTIMENT_5D":   "sentiment_score.rolling(5).mean()",
    },

    # TIER 2 — 10 factors, chỉ stocks có volume > 5 tỷ/ngày
    "TIER_2_LIQUID_ONLY": {
        # Momentum
        "MOM_6M_SKIP1M":    "adj_close.shift(20) / adj_close.shift(120) - 1",
        "PRICE_ACCEL":      "MOM_1M - MOM_1M.shift(5)",
        "VOL_MOMENTUM":     "(volume * close).rolling(5).sum() / (volume * close).rolling(20).sum()",
        # Value
        "FCF_YIELD":        "rank(fcf / market_cap)",
        "EV_EBITDA_RANK":   "rank(-ev / ebitda)",
        # Quality
        "GROSS_MARGIN_TREND": "gross_margin_q - gross_margin_q.shift(4)",
        # Liquidity
        "TURNOVER_RATE":    "volume.rolling(5).mean() / shares_outstanding",
        "FOREIGN_ROOM_BUFFER": "rank(foreign_room_remaining_pct)",
        # Event
        "EARNINGS_SURPRISE":  "(actual_eps - consensus_eps) / abs(consensus_eps + 0.01)",
        "POST_RESULTS_MOM":   "return_3d_after_earnings",
    },

    # TIER 3 — 10 factors, chỉ khi có đủ data
    "TIER_3_OPTIONAL": {
        "HIGH_52W_PROXIMITY": "adj_close / adj_close.rolling(252).max()",
        "DIVIDEND_YIELD":     "rank(div_per_share / close)",
        "EARNINGS_YIELD":     "rank(eps_ttm / close)",
        "DEBT_REDUCTION":     "rank(-(total_debt/equity - total_debt.shift(4)/equity.shift(4)))",
        "CASH_RATIO":         "rank(cash / current_liabilities)",
        "CFO_CONSISTENCY":    "(cfo_4q > 0).sum() / 4",
        "BID_ASK_SPREAD_INV": "rank(-(ask - bid) / ((ask+bid)/2))",
        "PRICE_IMPACT_INV":   "rank(-abs(return) / dollar_volume)",
        "REVENUE_SURPRISE":   "(revenue_q - revenue_q.shift(4)) / abs(revenue_q.shift(4))",
        "ANALYST_REV_PROXY":  "eps_q / eps_q.shift(4) - 1",
    },
}
```

### 3.2.3 IC Benchmark — Ngưỡng cho VN

| Metric | Threshold | VN-specific note |
|--------|----------|-----------------|
| IC mean | > 0.03 | Thấp hơn US (0.05) vì ít stocks, noise cao hơn |
| Positive ratio | > 0.55 | Factor đúng chiều > 55% ngày |
| t-stat | > 2.0 | Minimum significance |
| Min stocks | 30 | Cross-sectional minimum |

### 3.2.4 Benchmark Runner

```python
# brain/quant/factors/vn_bench_runner.py
class VNFactorBenchRunner:
    VN_CONSTRAINTS = {
        "min_daily_value_bn": 5.0,  # 5 tỷ VND/ngày
        "settlement_lag":     2,     # T+2
        "price_limit_hose":   0.07,
        "holding_periods":    [5, 10, 20],
    }

    def compute_ic_series(self, factor_df, returns_df, holding: int = 5):
        from scipy.stats import spearmanr
        forward_ret = returns_df.shift(-holding)
        ic_series   = {}
        for date in factor_df.index:
            fac    = factor_df.loc[date].dropna()
            ret    = forward_ret.loc[date].dropna()
            common = fac.index.intersection(ret.index)
            if len(common) >= 30:
                ic_series[date] = spearmanr(fac[common], ret[common])[0]
        return pd.Series(ic_series)

    def categorize(self, ic: pd.Series) -> str:
        mean, pos, t = ic.mean(), (ic > 0).mean(), ic.mean()/(ic.std()/len(ic)**0.5)
        if abs(mean) > 0.03 and abs(t) > 2.0:
            return "alive" if pos > 0.55 else "reversed" if pos < 0.45 else "weak"
        return "dead"
```

---

# PHẦN IV — AGENT BRAIN (LAYER 3)

## 4.1 AgentLoop — ĐÃ IMPLEMENT (loop.py ~897 dòng)

**Thực tế:** `AgentLoop` (`app/brain/agents/core/loop.py`) đã có **5-layer context management** + heartbeat + tool registry + batch execution. `agent_core.py` **không tồn tại** — không có unified AgentCore riêng.

**Kiến trúc hiện tại:**

```
app/brain/agents/core/
├── loop.py        # AgentLoop: 5-layer context, ReAct loop, heartbeat (~897 dòng)
├── context.py     # ContextBuilder
├── memory.py      # WorkspaceMemory
├── progress.py    # HeartbeatTimer, ProgressEvent
├── tools.py       # ToolRegistry
├── trace.py       # TraceWriter
├── frontmatter.py
├── skills.py
└── tools/         # Tool implementations
```

**Worker hiện tại** (`app/brain/state/worker.py`) duy trì logic worker riêng, **chưa kế thừa AgentLoop**. Đây là technical debt cần xử lý sau.

```python
# app/brain/agents/core/loop.py — thực tế đã chạy
class AgentLoop:
    """
    AgentLoop: ReAct core loop with 5-layer context management:
      Layer 1 (microcompact)     — silently prunes old tool results
      Layer 2 (context_collapse) — folds long text blocks (zero LLM cost)
      Layer 3 (auto_compact)     — LLM structured summary with token-budget tail
      Layer 4 (compact tool)     — model explicitly calls compact tool
      Layer 5 (iterative update) — Nth compression updates previous summary
    """
    def __init__(self, config, tools):
        self.tools = ToolRegistry(tools)
        self.memory = WorkspaceMemory()
        self.trace = TraceWriter()
        self.context_builder = ContextBuilder(self.memory)

    async def run(self, task: str, state=None) -> AgentOutput:
        """Main ReAct loop."""
        # Implementation with heartbeat, 5-layer context, tool execution
        ...
```

## 4.2 LLM Router — ✅ ĐÃ IMPLEMENT (GROQ + NVIDIA, chưa có Gemini)

**Thực tế:** Router đã implement với 3 model types — **GROQ (qwen3-32b)** cho reasoning/chat, **NVIDIA (minimax-m2.7)** cho document/news. **Chưa có Gemini integration** — Gemni client chưa được tạo.

```python
# app/brain/providers/router.py — thực tế
class IntentType(str, Enum):
    CHAT = "CHAT"
    RESEARCH = "RESEARCH"
    SIGNAL = "SIGNAL"

class ModelType(str, Enum):
    GROQ0 = "groq0"       # qwen/qwen3-32b (API key 0): reasoning, chat, synthesis
    GROQ1 = "groq1"       # qwen/qwen3-32b (API key 1): structured output, classification
    NVIDIA = "nvidia"     # minimaxai/minimax-m2.7: document/news analysis

class IntentRouter:
    INTENT_PATTERNS = {
        IntentType.CHAT: [...],
        IntentType.RESEARCH: [r"phan tich|danh gia|analyze|analysis", ...],
        IntentType.SIGNAL: [r"tin hieu|signal|buy|sell", ...],
    }
```

**Gemini integration:** ❌ Chưa implement. Cần tạo `gemini_client.py` và thêm vào router. Dependencies đã có: `google-genai>=1.0.0` trong requirements.txt.

**LLM Orchestrator hiện tại:** `app/brain/providers/orchestrator.py` quản lý multi-model orchestration. `chat.py` là ChatLLM wrapper. `groq_client.py` cho Groq API calls.

## 4.3 Multi-Agent System — ✅ Agent roles đã implement

**Thực tế:** Các agent roles đã có trong `app/brain/agents/`:
```
app/brain/agents/
├── analysts/       # Market, Fundamentals, Sentiment, Macro analysts
├── core/           # AgentLoop, context, tools
├── debaters/       # Debate logic
├── managers/       # Research Manager
├── researchers/    # Bull/Bear researchers
├── trader/         # Trader agent
└── utils/          # Context builder utilities
```

**Tuy nhiên:** Các timeout cụ thể cho VN chưa được config. Workflow debate 8-agent đã có architecture nhưng chưa có VN-specific preset. Grounding check đã implement trong `app/brain/state/grounding.py`.

```python
# Agent timeout hiện tại được config trong swarm preset YAML files, không hardcode
# Xem presets tại app/brain/state/presets/*.yaml (27 presets, chưa có VN equity desk)
```

## 4.4 VN-Specific Skills — ❌ CHƯA CÓ (57 skills khác đã có)

**Thực tế:** Thư mục `app/brain/quant/skills_data/` có **57 skills** nhưng **không có skill nào dành riêng cho thị trường VN**. Các skills hiện tại là global/generic (technical-basic, financial-statement, macro-analysis, global-macro, yfinance, vietfin, v.v.)

**Cần tạo 3 VN-specific skills:**
- `vn-trading-rules/SKILL.md` — T+2, price limit, room ngoại, ATO/ATC, margin rules
- `vn-sector-analysis/SKILL.md` — Banking, Real Estate, Steel, Retail, Tech, Utilities VN
- `vn-macro-calendar/SKILL.md` — Tết seasonality, Q4 earnings, SBV decisions, VND/USD

> **Note:** Trading rules đã được hardcode trong `app/brain/tools/backtest/engines/vietnam_equity.py` và `app/brain/dataflows/vendors/vn/calendar.py`. Cần chuyển thành skill để agent có thể đọc.

---

# PHẦN V — HYPOTHESIS SYSTEM

## 5.1 Kiến trúc Hypothesis

```python
# brain/quant/hypotheses/registry.py (bổ sung)

class HypothesisRegistry:
    """
    Lifecycle đầy đủ: exploring → testing → validated/rejected → monitoring
    """
    LIFECYCLE = ["exploring", "testing", "validated", "rejected", "monitoring"]

    def create(self, hypothesis: dict) -> Hypothesis:
        """
        hypothesis = {
            "title": "Momentum 3M outperforms sau khi foreign net buy > 50B/tuần",
            "category": "momentum" | "value" | "quality" | "event" | "macro",
            "assets": ["VCB", "VHM"] | "HOSE_TOP100",
            "signal_definition": "...",
            "expected_holding": "5d" | "10d" | "20d",
            "confidence": 0.7,
            "vn_specific": True,
            "notes": "...",
        }
        """

    def link_backtest(self, hypothesis_id: str, backtest_run_id: str):
        """Kết nối hypothesis với backtest result."""

    def auto_promote(self, hypothesis_id: str):
        """
        Auto-promote exploring → testing nếu:
        - IC > 0.03 trên 60 ngày data
        - t-stat > 1.5
        """
```

## 5.2 Hypothesis Templates cho VN Market

Các hypothesis cần test ngay:

```yaml
# H001: Mua trước Tết
title: "Stocks tăng 2-3 tuần trước Tết Nguyên Đán"
category: "seasonal"
signal: "days_to_tet <= 15 AND days_to_tet > 0"
expected_holding: "10d"
assets: "HOSE_LIQUID_TOP50"
hypothesis: "Liquidity rút lui, retail investors mua trước Tết"

# H002: Foreign net buy signal
title: "Foreign net buy > 3 ngày liên tiếp = signal tăng 5d"
category: "flow"
signal: "foreign_net_buy_3d_streak > 0 AND foreign_net_value_3d > 30e9"
expected_holding: "5d"
assets: "HOSE_FOREIGN_FAVORITE"
hypothesis: "Institutional smart money signal"

# H003: Insider buy signal
title: "CEO/CFO mua cổ phiếu > 1% outstanding = signal tăng"
category: "event"
signal: "insider_buy_pct_outstanding > 0.01 AND role IN ('CEO','CFO','Chairman')"
expected_holding: "20d"
assets: "ALL_LISTED"
hypothesis: "Insider knowledge premium"

# H004: Post-earnings momentum
title: "EPS surprise > 20% → momentum 10d tiếp theo"
category: "earnings"
signal: "eps_surprise_pct > 20 AND days_since_earnings <= 3"
expected_holding: "10d"
assets: "HOSE_TOP200"
hypothesis: "Under-reaction to earnings news (ít analyst coverage)"

# H005: Liquidity premium reversal
title: "Stocks bị bán bởi margin call → reversal sau 3d"
category: "technical"
signal: "price_drop_1d < -0.06 AND volume_ratio_vs_20d > 3"
expected_holding: "3d"
assets: "HOSE_MARGIN_ELIGIBLE"
hypothesis: "Forced selling = temporary mispricing"
```

---

# PHẦN VI — BACKTEST SYSTEM

## 6.1 VN-Specific Backtest Engine — ✅ ĐÃ IMPLEMENT

**Thực tế:** `VietnamEquityEngine` đã implement tại `app/brain/tools/backtest/engines/vietnam_equity.py` (~197 dòng).

```python
# app/brain/tools/backtest/engines/vietnam_equity.py — thực tế
"""Vietnam equity backtest engine.

Market rules:
  - T+2: can sell shares on T+2 (settlement cycle)
  - No short selling for retail investors
  - Board-based price limits: HOSE ±7%, HNX ±10%, UPCoM ±15%
  - Board-based tick sizes
  - Minimum lot: 100 shares
  - Brokerage fee: 0.15% bilateral
  - Sell tax: 0.1% sell-side only
"""

class VietnamEquityEngine(BaseEngine):
    TRANSACTION_COSTS = {
        "commission_pct": 0.0020,
        "tax_pct": 0.001,
        "slippage_pct": 0.0005,
    }
    CONSTRAINTS = {
        "t_plus": 2,
        "price_limit_hose": 0.07,
        "price_limit_hnx": 0.10,
        "price_limit_upcom": 0.15,
        "lot_size": 100,
        "max_position_pct": 0.05,
        "min_liquidity_vnd": 5e9,
    }

    def can_sell(self, symbol, buy_date, current_date): ...
    def apply_price_limit(self, order_price, prev_close, exchange): ...
    def compute_realistic_fill(self, symbol, date, side, qty): ...

# Backtest system đầy đủ tại:
# app/brain/tools/backtest/
# ├── engines/        # vietnam_equity.py + base.py
# ├── metrics.py      # Sharpe, Sortino, VaR, CVaR, GARCH
# ├── runner.py       # Backtest runner
# ├── validation.py   # Walk-forward validation
# ├── benchmark.py    # Benchmark comparison
# ├── correlation.py  # Correlation analysis
# ├── models.py       # Data models
# └── optimizers/     # Parameter optimization
```

## 6.2 Backtest Workflow

```
1. Signal generation
   ├── Factor scores từ DB (pre-computed)
   ├── ML predictions (XGBoost/LSTM)
   └── Agent analysis output

2. Universe filter
   ├── Liquidity: turnover > 5B VND/ngày
   ├── Exchange: HOSE + HNX (không UPCOM cho live trading)
   ├── Risk flags: loại DELIST, SANCTION, SUSPEND
   └── Min market cap: > 500B VND

3. Portfolio construction
   ├── Long-only (VN không có short selling dễ)
   ├── Max 15-20 positions
   ├── Kelly sizing với max 5% per stock
   └── Sector diversification: max 30% per sector

4. Execution simulation
   ├── T+2 settlement constraint
   ├── Price limit constraint
   ├── Transaction costs (0.25% round-trip)
   └── Realistic fill price (VWAP + market impact)

5. Performance metrics
   ├── Total return vs VNINDEX benchmark
   ├── Sharpe ratio (annualized, risk-free = SBV rate)
   ├── Max drawdown
   ├── Win rate (% trades profitable)
   ├── Information Ratio vs VNINDEX
   ├── Calmar ratio
   └── Monthly P&L breakdown
```

## 6.3 Backtest Validation Rules

```python
# Overfitting prevention cho VN market (ít data hơn US)
VALIDATION_RULES = {
    "min_backtest_years": 3,     # Ít nhất 3 năm data
    "min_trades": 100,           # Ít nhất 100 trades
    "walk_forward_splits": 5,    # 5-fold walk-forward
    "out_of_sample_pct": 0.30,  # 30% data là out-of-sample
    "max_sharpe_in_sample": 3.0, # Sharpe > 3 trên IS → suspect overfitting
    "min_sharpe_oos": 0.5,       # Sharpe < 0.5 trên OOS → rejected
}
```

---

# PHẦN VII — DECISION GATE (LAYER 4)

## 7.1 Confidence Scoring

```python
# brain/state/signal_processing.py (bổ sung)

class ConfidenceScorer:
    """
    Tổng hợp confidence từ nhiều nguồn.
    Chỉ pass qua gate nếu tổng score >= threshold.
    """
    WEIGHTS = {
        "bull_bear_consensus":  0.35,  # Bull/Bear debate agreement
        "factor_percentile":    0.25,  # Alpha factor score (0-100)
        "ml_prediction_proba":  0.20,  # XGBoost probability
        "technical_alignment":  0.10,  # Trend direction alignment
        "macro_alignment":      0.10,  # Macro không adverse
    }
    THRESHOLD = 0.65  # Minimum để pass gate

    def score(self, analysis: dict) -> float:
        score = 0
        score += self.WEIGHTS["bull_bear_consensus"]  * analysis["consensus"]
        score += self.WEIGHTS["factor_percentile"]    * analysis["factor_pct"] / 100
        score += self.WEIGHTS["ml_prediction_proba"]  * analysis["ml_proba"]
        score += self.WEIGHTS["technical_alignment"]  * analysis["tech_align"]
        score += self.WEIGHTS["macro_alignment"]      * analysis["macro_align"]
        return score
```

## 7.2 Risk Gate (Hard Blocks)

```python
HARD_BLOCKS = {
    "DELIST":       "Stock đang bị xem xét hủy niêm yết",
    "SANCTION":     "Công ty bị UBCKNN xử phạt",
    "SUSPEND":      "Cổ phiếu bị đình chỉ giao dịch",
    "INVESTIGATE":  "Đang bị điều tra",
    "AUDIT_QUALIFY":"BCTC có ý kiến kiểm toán ngoại trừ",
    "ZERO_VOLUME":  "Không có giao dịch 5 ngày liên tiếp",
    "NEAR_CEILING": "Giá đã tăng trần 3 ngày liên tiếp",
}

SOFT_WARNINGS = {
    "HIGH_PLEDGE":    "Cổ đông lớn cầm cố > 70% cổ phiếu",
    "LARGE_ACCRUAL":  "Accrual ratio bất thường",
    "INSIDER_SELL":   "Insider bán mạnh trong 30 ngày",
    "FOREIGN_SELL":   "Foreign net sell > 5 ngày liên tiếp",
}
```

---

# PHẦN VIII — EVAL FRAMEWORK (LAYER 5) — ⚠️ THIẾT KẾ (chưa implement)

> **⚠️ Thực tế:** Cả `SignalTracker` và `LLMJudge` đều **chưa được implement**. Code dưới đây là thiết kế tham khảo cho phase sau. Daily ETL hiện tại có step `signals` compute buy/sell nhưng chưa có tracking accuracy.

## 8.1 Signal Accuracy Tracking (❌ chưa implement — tham khảo)

```python
# brain/eval/signal_tracker.py

class SignalTracker:
    """
    Track mọi signal được tạo ra và so sánh với actual outcome.
    Đây là foundation để biết hệ thống đang tốt lên hay không.
    """

    async def record_signal(self, signal: dict):
        """
        signal = {
            "symbol": "VCB",
            "signal_date": "2024-01-15",
            "direction": "BUY",  # BUY / SELL / HOLD
            "confidence": 0.72,
            "target_price": 92000,
            "holding_period": 5,
            "source_agents": ["bull_researcher", "market_analyst"],
            "factors_used": ["MOM_3M", "ROE_STABILITY"],
        }
        """
        await self.db.insert("signal_log", signal)

    async def evaluate_signal(self, signal_id: str, days_after: int = 5):
        """
        Sau N ngày, so sánh prediction với actual.
        Tự động chạy trong daily ETL.
        """
        signal = await self.db.get("signal_log", signal_id)
        actual_return = await self.get_actual_return(
            signal["symbol"],
            signal["signal_date"],
            days_after
        )
        hit = (signal["direction"] == "BUY" and actual_return > 0) or \
              (signal["direction"] == "SELL" and actual_return < 0)

        await self.db.update("signal_log", signal_id, {
            "actual_return_5d": actual_return,
            "hit": hit,
            "evaluated_at": datetime.now(),
        })
```

## 8.2 LLM-as-Judge (❌ chưa implement — tham khảo)

```python
# brain/eval/llm_judge.py

JUDGE_PROMPT = """
Bạn là chuyên gia phân tích tài chính VN. Đánh giá chất lượng của bài phân tích sau.

Phân tích: {analysis_text}
Kết quả thực tế sau 5 ngày: {actual_outcome}

Chấm điểm 1-10 cho từng tiêu chí:
1. Factual accuracy (dữ liệu có đúng không?)
2. Reasoning quality (luận điểm có logic không?)
3. Risk awareness (có nhận ra risk không?)
4. VN market context (có hiểu đặc thù VN không?)
5. Actionability (recommendation có cụ thể không?)

Trả về JSON: {scores, overall, key_errors, key_strengths}
"""

class LLMJudge:
    async def evaluate(self, analysis: str, outcome: dict) -> EvalResult:
        prompt = JUDGE_PROMPT.format(
            analysis_text=analysis,
            actual_outcome=json.dumps(outcome, ensure_ascii=False)
        )
        # Dùng Gemini Flash (rẻ + nhanh) cho judge
        response = await self.gemini_client.chat([
            {"role": "user", "content": prompt}
        ])
        return EvalResult.parse(response.text)
```

**Cần làm:**
- [ ] Tạo `app/brain/eval/signal_tracker.py` — track signal → actual outcome
- [ ] Tạo `app/brain/eval/llm_judge.py` — LLM evaluation
- [ ] Persist evaluation results vào `signal_log` table
- [ ] Tích hợp vào daily ETL workflow

---

# PHẦN IX — SWARM SYSTEM

## 9.1 VN-Specific Swarm Presets

**Preset quan trọng nhất cần tạo:**

```yaml
# brain/state/presets/vn_equity_desk.yaml
name: "VN Equity Desk"
description: "Full-stack VN stock analysis team"
agents:
  - role: vn_market_analyst
    model: groq/qwen-32b
    tools: [technical_indicators, ohlcv_vn, orderbook_vn]
    vn_context: true

  - role: vn_fundamentals_analyst
    model: gemini/flash
    tools: [financial_statements, ratios, insider_trades]
    vn_context: true

  - role: vn_news_analyst
    model: groq/llama-70b
    tools: [cafef_news, ubcknn_announcements, rag_annual_reports]
    language: vietnamese

  - role: vn_macro_analyst
    model: groq/llama-70b
    tools: [sbv_rates, foreign_flow, vnd_usd, vnindex_breadth]

  - role: vn_risk_officer
    model: groq/llama-70b
    tools: [risk_flags, position_limits, liquidity_check]
    gate: hard_block_on_flags  # Không cho phép bypass
```

## 9.2 DAG Workflow cho Full Analysis

```
START
  ├──[parallel]──→ vn_market_analyst      (30s)
  ├──[parallel]──→ vn_fundamentals_analyst (45s)
  ├──[parallel]──→ vn_news_analyst         (30s)
  └──[parallel]──→ vn_macro_analyst        (20s)
         ↓ [wait all 4]
  ├──→ vn_risk_officer                     (15s) ← HARD GATE
  │      ↓ [if flags → STOP]
  ├──→ bull_researcher                     (40s)
  ├──→ bear_researcher                     (40s)
         ↓ [wait both]
  ├──→ debate_rounds (3×)                  (90s)
         ↓
  ├──→ research_manager → ResearchPlan     (30s)
         ↓
  ├──→ confidence_scorer → score           (5s)
  │      ↓ [if score < 0.65 → HOLD]
  ├──→ portfolio_manager → PortfolioDecision (25s)
         ↓
  └──→ trader → TraderProposal + execution  (20s)
END
```

---

# PHẦN X — ROADMAP THEO GIAI ĐOẠN (Cập nhật theo thực tế)

> **Thực tế:** Phase 0 và phần lớn Phase 1 đã hoàn thành. Phase 2-4 cần tiếp tục.

## Phase 0 — Foundation ✅ ~90% DONE
**Target: Data integrity 100%**

- [x] Build `adj_close` pipeline — ✅ **Done** (1,176,248 rows, đọc từ corporate_actions table)
- [x] Build `corporate_actions` table — ✅ **Done** (2,556 rows, nguồn: yfinance ban đầu)
- [x] Build risk flags — ✅ **Done** (risk_flags_v2: 10 computed flags từ structured data, không scraper)
- [x] Build macro ETL — ✅ **Done** (5-source pipeline: yfinance + VietFin + vi.money + SBV + Vimo)
- [x] Tạo 10 DB tables — ✅ **Done** (22 tables, thiếu risk_metrics + risk_assessments)
- [ ] Validate adj_close với CafeF chart — ❌ **Chưa validate**

## Phase 1 — Signal Engine ✅ ~80% DONE
**Target: Pre-compute pipeline hoạt động**

- [x] Build `daily_etl.py` pipeline — ✅ **Done** (13 steps, từ OHLCV → macro indicators)
- [x] Pre-compute 40+ technical indicators — ✅ **Done** (413 symbols có technical_indicators)
- [x] Implement factor scores — ✅ **Done** (7-factor composite: 416 symbols)
- [x] Cross-sectional scoring → `factor_scores` table — ✅ **Done**
- [x] Compute signals (buy/sell) — ✅ **Done** (`signals` step trong ETL)
- [x] Alpha signals pre-computed — ✅ **Done** (7 alpha IDs, 2,912 rows)
- [ ] 30 VN-core factors — ⚠️ **Partial** (20 factors, cần mở rộng lên 30)
- [ ] IC benchmark trên 3 năm VN data — ❌ **Chưa làm**
- [ ] CRS 7 tầng — ❌ **Chưa làm** (risk_flags_v2 thay thế với 10 computed flags)
- [ ] GARCH/EWMA volatility — ❌ **Chưa tích hợp vào ETL**

## Phase 2 — Agent Consolidation ⚠️ ~30% DONE
**Target: Agent pipeline hoạt động**

- [x] AgentLoop 5-layer context — ✅ **Done** (`app/brain/agents/core/loop.py`, ~897 dòng)
- [x] LLM Router — ✅ **Done** (GROQ + NVIDIA, chưa có Gemini)
- [x] Agent roles (analysts, debaters, researchers, trader) — ✅ **Done**
- [x] 27 swarm presets — ✅ **Done** (but chưa có VN-specific)
- [ ] Refactor SwarmWorker kế thừa AgentLoop — ❌ **Chưa làm** (technical debt)
- [ ] Tích hợp CRS/risk_flags vào Decision Gate — ⚠️ **Partial**
- [ ] Create VN-specific skills (3 skills) — ❌ **Chưa có**
- [ ] Create `vn_equity_desk.yaml` swarm preset — ❌ **Chưa có**
- [ ] Build ConfidenceScorer — ❌ **Chưa có** (`signal_processing.py` có sẵn)

## Phase 3 — Hypothesis & Backtest ✅ ~60% DONE
**Target: Validated hypotheses**

- [x] Build `VietnamEquityEngine` — ✅ **Done** (T+2, price limit, realistic costs)
- [x] Backtest metrics (Sharpe, Sortino, VaR, CVaR, GARCH) — ✅ **Done**
- [x] Walk-forward validation — ✅ **Done** (`validation.py`)
- [x] Hypothesis registry — ✅ **Done** (`hypotheses/registry.py`)
- [ ] Test 3 hypotheses ưu tiên (Tết, Foreign flow, Insider) — ❌ **Chưa test**
- [ ] Eval framework (SignalTracker + LLMJudge) — ❌ **Chưa có**
- [ ] Add Gemini Flash vào LLM router — ❌ **Chưa có**

## Phase 4 — Paper Trading Validation ❌ NOT STARTED
**Target: 3 tháng paper trading với metrics**

- [ ] Activate daily signal generation pipeline
- [ ] Track mọi signal trong `signal_log` table
- [ ] Weekly performance review: win rate, Sharpe, drawdown
- [ ] A/B test: agent analysis vs pure factor model
- [ ] Identify failure patterns → feed back vào hypothesis

## Phase 5 — Live Trading Ready ❌ NOT STARTED
**Target: Production-grade với risk management**

- [ ] Chỉ live nếu Phase 4 milestone đạt được
- [ ] Start với vốn nhỏ (< 50 triệu VND)
- [ ] Hard limits: max drawdown 10% → auto-stop
- [ ] Human approval cho lệnh > 20 triệu VND
- [ ] Daily P&L report tự động

---

# PHẦN XI — CHI TIẾT KỸ THUẬT QUAN TRỌNG

## 11.1 VN Calendar — Ngày giao dịch (Lunar Calendar chính xác)

> ⚠️ **Fix:** Tết âm lịch thay đổi mỗi năm — không dùng `range(330, 365)` cố định.

```python
# brain/tools/vn_calendar.py

# Ngày mồng 1 Tết Nguyên Đán — Gregorian dates (đến 2030)
TET_DATES = {
    2020: "2020-01-25", 2021: "2021-02-12", 2022: "2022-02-01",
    2023: "2023-01-22", 2024: "2024-02-10", 2025: "2025-01-29",
    2026: "2026-02-17", 2027: "2027-02-06", 2028: "2028-01-26",
    2029: "2029-02-13", 2030: "2030-02-03",
}

VN_HOLIDAYS = {
    2024: [
        "2024-01-01",
        "2024-02-08","2024-02-09","2024-02-10","2024-02-11",
        "2024-02-12","2024-02-13","2024-02-14",  # Tết
        "2024-04-18",  # Giỗ Tổ Hùng Vương
        "2024-04-30","2024-05-01","2024-09-02",
    ],
    2025: [
        "2025-01-01",
        "2025-01-27","2025-01-28","2025-01-29",
        "2025-01-30","2025-01-31","2025-02-01","2025-02-02",  # Tết
        "2025-04-07","2025-04-30","2025-05-01",
        "2025-09-01","2025-09-02",
    ],
}

def get_tet_window(year: int, days_before: int = 15,
                   days_after: int = 7) -> tuple:
    """Trả về (start, end) window Tết theo ngày thực tế."""
    from datetime import date, timedelta
    tet_str  = TET_DATES.get(year, "2025-01-29")  # fallback
    tet_date = date.fromisoformat(tet_str)
    return (tet_date - timedelta(days=days_before),
            tet_date + timedelta(days=days_after))

def is_in_tet_window(check_date, days_before: int = 15) -> bool:
    from datetime import date
    start, end = get_tet_window(check_date.year, days_before)
    # Check cả năm trước nếu Tết rơi vào tháng 1
    if check_date.month == 1:
        s2, e2 = get_tet_window(check_date.year - 1, days_before)
        if s2 <= check_date <= e2:
            return True
    return start <= check_date <= end

def is_trading_day(check_date) -> bool:
    if check_date.weekday() >= 5:
        return False
    year_holidays = VN_HOLIDAYS.get(check_date.year, [])
    return str(check_date) not in year_holidays

def get_next_n_trading_days(start_date, n: int) -> list:
    from datetime import timedelta
    days, current = [], start_date + timedelta(days=1)
    while len(days) < n:
        if is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days

TRADING_HOURS = {
    "HOSE": {
        "ato": ("09:00", "09:15"), "morning": ("09:15", "11:30"),
        "break": ("11:30", "13:00"), "afternoon": ("13:00", "14:30"),
        "atc": ("14:30", "14:45"),
    },
    "HNX": {
        "ato": ("09:00", "09:15"), "morning": ("09:15", "11:30"),
        "break": ("11:30", "13:00"), "afternoon": ("13:00", "15:00"),
        "atc": ("15:00", "15:15"),
    }
}
```

## 11.2 Performance Metrics — VN Benchmark

```python
# brain/tools/backtest/metrics.py (bổ sung VN-specific)

def compute_vn_metrics(returns: pd.Series, vnindex_returns: pd.Series) -> dict:
    risk_free = 0.0475  # SBV deposit rate 12 tháng (từ macro_indicators table)
    rf_daily = (1 + risk_free) ** (1/252) - 1

    excess = returns - rf_daily
    tracking_error = (returns - vnindex_returns).std() * np.sqrt(252)
    information_ratio = (returns.mean() - vnindex_returns.mean()) / tracking_error * np.sqrt(252)

    return {
        "total_return":        (1 + returns).prod() - 1,
        "annualized_return":   (1 + returns).prod() ** (252/len(returns)) - 1,
        "sharpe_ratio":        excess.mean() / returns.std() * np.sqrt(252),
        "sortino_ratio":       excess.mean() / returns[returns < 0].std() * np.sqrt(252),
        "max_drawdown":        compute_max_drawdown(returns),
        "calmar_ratio":        annualized_return / abs(max_drawdown),
        "information_ratio":   information_ratio,
        "tracking_error":      tracking_error,
        "beta_vs_vnindex":     np.cov(returns, vnindex_returns)[0,1] / vnindex_returns.var(),
        "alpha_vs_vnindex":    annualized_return - beta * vnindex_annualized,
        "win_rate":            (returns > 0).mean(),
        "avg_win_loss_ratio":  returns[returns > 0].mean() / abs(returns[returns < 0].mean()),
    }
```

---

# PHỤ LỤC — CHECKLIST TRƯỚC KHI LIVE TRADING

## Checklist kỹ thuật

- [ ] adj_close được tính đúng và validate với ít nhất 3 nguồn
- [ ] Risk flags real (không phải hash-based) cho tất cả 1800 stocks
- [ ] T+2 constraint được enforce trong mọi backtest và paper trading order
- [ ] Daily ETL chạy ổn định 30 ngày liên tiếp không lỗi
- [ ] Signal accuracy tracking hoạt động (mọi signal được log)
- [ ] Hard stop-loss 10% drawdown được implement và test

## Checklist performance

- [ ] Backtest Sharpe > 1.5 trên IS data (3+ năm)
- [ ] OOS Sharpe > 0.8 (walk-forward 30% holdout)
- [ ] Paper trading win rate > 55% trên 3 tháng
- [ ] Paper trading monthly return > VNINDEX benchmark
- [ ] Max drawdown paper trading < 8%

## Checklist risk management

- [ ] Không có single position > 5% portfolio
- [ ] Không có single sector > 30% portfolio
- [ ] Tất cả orders qua RiskGate (confidence score + risk flags)
- [ ] Human approval cho orders > ngưỡng
- [ ] Daily P&L report tự động

---

---

# PHẦN XII — SYSTEM PROMPTS CHO TỪNG AGENT

> Đây là phần quan trọng nhất quyết định chất lượng output. Mỗi agent cần prompt riêng, VN-specific, không dùng prompt generic.

## 12.1 Nguyên tắc viết prompt cho VN trading agents

**Luôn bắt đầu bằng context VN:** Agent cần biết mình đang làm việc với thị trường VN, không phải US. Các khái niệm như T+2, biên độ ±7%, ATO/ATC, room ngoại phải được nhắc đến.

**Cung cấp dữ liệu thực trong prompt:** Không để agent "đoán" giá hay chỉ số. Luôn inject OHLCV thực, ratios thực, news thực vào context trước khi agent phân tích.

**Yêu cầu structured output:** Dùng Pydantic schema. Agent trả về JSON, không trả về text tự do để downstream dễ parse.

**Calibrate confidence:** Agent VN nên thận trọng hơn US vì: ít analyst coverage, data quality thấp hơn, thanh khoản mỏng hơn.

## 12.2 Market Analyst Agent Prompt

```python
MARKET_ANALYST_SYSTEM = """
Bạn là chuyên gia phân tích kỹ thuật cho thị trường chứng khoán Việt Nam (HOSE/HNX/UPCOM).

## Đặc thù thị trường VN bạn PHẢI hiểu:
- Biên độ dao động: HOSE ±7%, HNX ±10%, UPCOM ±15% mỗi phiên
- Thanh khoản: Kiểm tra giá trị giao dịch trung bình 20 ngày, nếu < 5 tỷ VND/ngày = quá mỏng
- Giờ giao dịch: ATO 9:00-9:15, Sáng 9:15-11:30, Chiều 13:00-14:30, ATC 14:30-14:45
- T+2 settlement: Không thể bán cổ phiếu trước ngày T+2
- Room ngoại: Kiểm tra % room còn lại, nếu < 5% = khó tăng tiếp
- Price action: Chuỗi tăng trần/giảm sàn liên tiếp thường xuất hiện nhiều hơn US

## Nhiệm vụ của bạn:
Phân tích kỹ thuật cho cổ phiếu {symbol} dựa trên dữ liệu được cung cấp.

## Dữ liệu đầu vào:
- OHLCV 60 ngày gần nhất (đã adj_close): {ohlcv_data}
- Technical indicators pre-computed: {indicators}
- Orderbook snapshot hiện tại: {orderbook}
- Thông tin cơ bản: Exchange={exchange}, Sector={sector}

## Yêu cầu phân tích:
1. **Xu hướng chính** (1-3 tháng): Uptrend / Downtrend / Sideways — với bằng chứng cụ thể
2. **Hỗ trợ / Kháng cự** (top 3 mức mỗi loại, đơn vị VND)
3. **Volume analysis**: Xác nhận hay phân kỳ với price action
4. **Momentum**: RSI, MACD, Stochastic — overbought/oversold?
5. **Tín hiệu ngắn hạn** (5-10 ngày tới): BUY / SELL / HOLD với lý do
6. **Rủi ro kỹ thuật**: Mức nào breakout sẽ fail?

## Định dạng output (JSON):
{
  "trend": "UPTREND|DOWNTREND|SIDEWAYS",
  "trend_strength": 0.0-1.0,
  "support_levels": [float, float, float],
  "resistance_levels": [float, float, float],
  "volume_confirmation": true|false,
  "momentum_signal": "BULLISH|BEARISH|NEUTRAL",
  "short_term_signal": "BUY|SELL|HOLD",
  "signal_confidence": 0.0-1.0,
  "key_risk_level": float,
  "analysis_summary": "string (tiếng Việt, 2-3 câu)",
  "data_quality_warning": "string|null"
}

## Quan trọng:
- Nếu volume quá thấp (< 5 tỷ/ngày), hãy flag rõ trong data_quality_warning
- Nếu giá đang ở vùng trần/sàn, hãy ghi nhận trong analysis_summary
- Đừng đưa ra signal nếu không có đủ evidence (dùng HOLD khi uncertain)
"""
```

## 12.3 Fundamentals Analyst Agent Prompt

```python
FUNDAMENTALS_ANALYST_SYSTEM = """
Bạn là chuyên gia phân tích cơ bản cho thị trường chứng khoán Việt Nam.

## Đặc thù kế toán VN bạn PHẢI biết:
- BCTC VN theo VAS (Vietnamese Accounting Standards), khác IFRS ở một số điểm
- Nhiều công ty VN có accrual cao bất thường → cần kiểm tra CFO vs Net Income
- Nợ vay ngân hàng là nguồn tài trợ chính → D/E ratio > 2x là bình thường với real estate
- Ngành ngân hàng: dùng NIM, NPL, CAR thay vì PE/PB thông thường
- Seasonal: Q4 thường tốt nhất (booking doanh thu cuối năm), Q1 thường yếu nhất
- Cổ tức bằng tiền mặt + cổ phiếu thưởng đều phổ biến tại VN

## Dữ liệu đầu vào:
- Income Statement (4 quý gần nhất): {income_statement}
- Balance Sheet (4 quý gần nhất): {balance_sheet}
- Cash Flow Statement (4 quý gần nhất): {cash_flow}
- Financial Ratios pre-computed: {ratios}
- Sector benchmark ratios: {sector_benchmarks}

## Yêu cầu phân tích:
1. **Chất lượng tăng trưởng**: Doanh thu / lợi nhuận có thực hay do accrual?
2. **Sức khỏe tài chính**: Thanh khoản, đòn bẩy, khả năng trả nợ
3. **Định giá**: PE/PB so với lịch sử và ngành. Rẻ hay đắt?
4. **Chất lượng lợi nhuận**: ROE/ROA trend, margin trend
5. **Red flags**: Accrual bất thường, nợ tăng vọt, CFO âm liên tiếp
6. **Catalyst tiếp theo**: Earnings mùa tới dự kiến ra sao?

## Định dạng output (JSON):
{
  "growth_quality": "HIGH|MEDIUM|LOW",
  "financial_health": "STRONG|ADEQUATE|WEAK|DISTRESSED",
  "valuation": "CHEAP|FAIR|EXPENSIVE",
  "valuation_pe_vs_sector": float,
  "valuation_pb_vs_sector": float,
  "earnings_quality_score": 0.0-1.0,
  "key_strengths": ["string", "string"],
  "key_risks": ["string", "string"],
  "red_flags": ["string"] or [],
  "fundamental_signal": "BUY|HOLD|SELL",
  "signal_confidence": 0.0-1.0,
  "next_catalyst": "string",
  "analysis_summary": "string (tiếng Việt, 3-4 câu)"
}
"""
```

## 12.4 Sentiment Analyst Agent Prompt

```python
SENTIMENT_ANALYST_SYSTEM = """
Bạn là chuyên gia phân tích tâm lý thị trường và tin tức cho chứng khoán Việt Nam.

## Nguồn sentiment VN quan trọng:
- CafeF, VnExpress Kinh Doanh, Tinnhanhchungkhoan: tin tức doanh nghiệp chính thống
- UBCKNN announcements: thông tin pháp lý, xử phạt, cảnh báo
- Insider trading reports: CEO/CFO mua bán cổ phiếu (signal mạnh tại VN)
- Foreign flow: Khối ngoại mua/bán ròng (institutional smart money signal)
- Cộng đồng đầu tư: Facebook groups, Telegram channels (FOMO/FUD indicator)

## Dữ liệu đầu vào:
- Tin tức 7 ngày gần nhất liên quan {symbol}: {news_list}
- Insider trading 30 ngày gần nhất: {insider_trades}
- Foreign flow 10 ngày gần nhất: {foreign_flow}
- Sentiment score rolling (1d/5d/10d): {sentiment_scores}

## Yêu cầu phân tích:
1. **Tone tin tức tổng thể**: Tích cực / Tiêu cực / Trung lập — với ví dụ cụ thể
2. **Insider signal**: Ban lãnh đạo đang mua hay bán? Quy mô?
3. **Foreign flow signal**: Khối ngoại đang accumulate hay distribute?
4. **Catalyst tin tức**: Có tin tức nào sắp tới ảnh hưởng lớn không?
5. **Risk events**: Có tin xấu nào chưa được thị trường phản ánh không?

## Định dạng output (JSON):
{
  "news_sentiment": "POSITIVE|NEGATIVE|NEUTRAL",
  "news_sentiment_score": -1.0 to 1.0,
  "insider_signal": "BUYING|SELLING|NEUTRAL|NO_DATA",
  "insider_net_quantity": int,
  "foreign_signal": "ACCUMULATING|DISTRIBUTING|NEUTRAL",
  "foreign_net_value_5d_bn_vnd": float,
  "upcoming_catalyst": "string|null",
  "risk_events": ["string"] or [],
  "sentiment_signal": "BULLISH|BEARISH|NEUTRAL",
  "signal_confidence": 0.0-1.0,
  "analysis_summary": "string (tiếng Việt, 2-3 câu)"
}

## Lưu ý:
- Insider buying tại VN thường là signal tốt hơn US (ít quy định hơn, thông tin nội bộ nhiều hơn)
- Foreign selling không nhất thiết là xấu nếu do rebalancing, hãy xem context
- Tin tức CafeF có thể bị delay 1-2 ngày so với thực tế
"""
```

## 12.5 Bull Researcher Agent Prompt

```python
BULL_RESEARCHER_SYSTEM = """
Bạn là nhà nghiên cứu lạc quan (Bull Researcher) cho cổ phiếu {symbol}.

## Nhiệm vụ:
Xây dựng luận điểm TẠI SAO NÊN MUA cổ phiếu này dựa trên tất cả dữ liệu được cung cấp.
Bạn PHẢI tìm kiếm bằng chứng ủng hộ quan điểm tăng giá, ngay cả khi dữ liệu hỗn hợp.

## Dữ liệu đầu vào (từ các analyst):
- Market analysis: {market_analysis}
- Fundamental analysis: {fundamental_analysis}
- Sentiment analysis: {sentiment_analysis}
- Macro context: {macro_context}
- Factor scores: {factor_scores}

## Cấu trúc luận điểm Bull:
1. **Primary catalyst** (lý do mạnh nhất để mua ngay bây giờ)
2. **Valuation case** (tại sao giá hiện tại là rẻ?)
3. **Growth drivers** (điều gì sẽ thúc đẩy giá tăng 6-12 tháng tới?)
4. **Technical setup** (chart nói gì?)
5. **Asymmetric risk/reward** (upside so với downside?)
6. **Catalyst timeline** (khi nào thesis được kiểm chứng?)

## Định dạng output (JSON):
{
  "bull_thesis_title": "string (1 câu mạnh, ví dụ: 'VCB sẽ re-rate khi NIM phục hồi Q3')",
  "primary_catalyst": "string",
  "valuation_argument": "string",
  "price_target_12m": float,
  "upside_pct": float,
  "key_bull_arguments": ["string", "string", "string"],
  "supporting_data_points": ["string", "string"],
  "bull_confidence": 0.0-1.0,
  "catalyst_timeline": "string",
  "what_could_make_thesis_wrong": "string"
}
"""
```

## 12.6 Bear Researcher Agent Prompt

```python
BEAR_RESEARCHER_SYSTEM = """
Bạn là nhà nghiên cứu bi quan (Bear Researcher) cho cổ phiếu {symbol}.

## Nhiệm vụ:
Xây dựng luận điểm TẠI SAO KHÔNG NÊN MUA hoặc NÊN BÁN cổ phiếu này.
Bạn PHẢI tìm kiếm rủi ro và bằng chứng ủng hộ quan điểm giảm giá.
Đặc biệt chú ý các rủi ro VN-specific thường bị bỏ qua.

## Dữ liệu đầu vào:
- Market analysis: {market_analysis}
- Fundamental analysis: {fundamental_analysis}
- Sentiment analysis: {sentiment_analysis}
- Risk flags: {risk_flags}
- Factor scores: {factor_scores}

## VN-specific risks cần kiểm tra:
- Cổ đông lớn cầm cố cổ phiếu cao → forced selling risk
- Nợ margin của nhà đầu tư cá nhân cao → margin call cascade
- Công ty vừa tăng vốn (dilution) hoặc sắp tăng vốn
- Đang bị điều tra, xử phạt bởi UBCKNN hoặc cơ quan thuế
- Lãnh đạo chủ chốt từ chức đột ngột
- BCTC kiểm toán có ý kiến ngoại trừ hoặc chú thích bất thường

## Định dạng output (JSON):
{
  "bear_thesis_title": "string",
  "primary_risk": "string",
  "valuation_concern": "string",
  "downside_target_12m": float,
  "downside_pct": float,
  "key_bear_arguments": ["string", "string", "string"],
  "vn_specific_risks": ["string"] or [],
  "red_flags_found": ["string"] or [],
  "bear_confidence": 0.0-1.0,
  "bear_scenario_trigger": "string",
  "what_could_make_thesis_wrong": "string"
}
"""
```

## 12.7 Portfolio Manager Agent Prompt

```python
PORTFOLIO_MANAGER_SYSTEM = """
Bạn là Portfolio Manager cho quỹ đầu tư cổ phiếu Việt Nam.

## Nguyên tắc quản lý danh mục VN:
- Long-only (không short selling trực tiếp tại VN)
- Tối đa 5% vốn cho 1 cổ phiếu (position sizing)
- Tối đa 30% cho 1 ngành (sector concentration)
- Tối thiểu 20% tiền mặt khi thị trường downtrend
- T+2: Kế hoạch exit phải tính T+2 settlement
- Ưu tiên thanh khoản: Không mua cổ phiếu có volume < 5 tỷ/ngày

## Dữ liệu đầu vào:
- Research synthesis: {research_plan}
- Danh mục hiện tại: {current_portfolio}
- Confidence score: {confidence_score}
- Macro regime: {macro_regime}
- Risk flags: {risk_flags}

## Quyết định cần đưa ra:
1. **Action**: BUY / ADD / HOLD / REDUCE / SELL / PASS
2. **Position size**: % of portfolio
3. **Entry strategy**: Mua 1 lần hay chia làm nhiều lần?
4. **Exit conditions**: Khi nào bán? (price target, stop-loss, time-based)
5. **Risk management**: Stop-loss level, max acceptable loss

## Định dạng output (PortfolioDecision JSON):
{
  "action": "BUY|ADD|HOLD|REDUCE|SELL|PASS",
  "position_size_pct": float,
  "entry_price_range": [float, float],
  "entry_strategy": "SINGLE|DCA_3|DCA_5",
  "price_target": float,
  "stop_loss": float,
  "expected_holding_days": int,
  "risk_reward_ratio": float,
  "rationale": "string (tiếng Việt, 3-5 câu)",
  "conditions_to_add": "string|null",
  "conditions_to_exit_early": "string",
  "portfolio_fit": "CORE|SATELLITE|SPECULATIVE",
  "vn_constraints_check": {
    "liquidity_ok": true|false,
    "risk_flags_clear": true|false,
    "sector_limit_ok": true|false,
    "position_limit_ok": true|false
  }
}

## QUAN TRỌNG:
- Nếu confidence_score < 0.65: action PHẢI là PASS hoặc HOLD
- Nếu có bất kỳ risk_flag nào (DELIST, SANCTION, SUSPEND): action PHẢI là PASS
- Nếu vn_constraints_check có bất kỳ false: giải thích rõ lý do
"""
```

## 12.8 Research Manager (Synthesis) Prompt

```python
RESEARCH_MANAGER_SYSTEM = """
Bạn là Research Manager, tổng hợp kết quả từ tất cả analysts và debate.

## Đầu vào:
- Kết quả 4 analysts song song: {analyst_outputs}
- Bull thesis: {bull_thesis}
- Bear thesis: {bear_thesis}
- Kết quả 3 vòng debate: {debate_rounds}

## Nhiệm vụ:
Tổng hợp thành ResearchPlan khách quan, không thiên vị Bull hay Bear.

## Định dạng output (ResearchPlan JSON):
{
  "symbol": "string",
  "analysis_date": "ISO date",
  "overall_signal": "STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL",
  "consensus_score": 0.0-1.0,
  "bull_bear_balance": "BULL_DOMINANT|BALANCED|BEAR_DOMINANT",
  "key_conclusions": ["string", "string", "string"],
  "primary_opportunity": "string|null",
  "primary_risk": "string",
  "data_quality_note": "string|null",
  "analyst_agreement": {
    "market": "BULLISH|BEARISH|NEUTRAL",
    "fundamental": "BULLISH|BEARISH|NEUTRAL",
    "sentiment": "BULLISH|BEARISH|NEUTRAL",
    "macro": "BULLISH|BEARISH|NEUTRAL"
  },
  "recommendation_summary": "string (tiếng Việt, 4-6 câu, đủ để người đọc ra quyết định)",
  "next_review_trigger": "string"
}
"""
```

---

# PHẦN XIII — TOOL SPECIFICATIONS

## 13.1 Core VN Tools — Interface chuẩn

Mỗi tool phải implement interface sau để AgentCore có thể gọi uniform:

```python
# brain/tools/base_vn_tool.py
from langchain.tools import BaseTool
from pydantic import BaseModel, Field

class VNToolInput(BaseModel):
    symbol: str = Field(..., description="Mã cổ phiếu VN, ví dụ: VCB, HPG, VHM")
    date: str = Field(None, description="Ngày phân tích (YYYY-MM-DD), None = hôm nay")

class BaseVNTool(BaseTool):
    """Base class cho tất cả VN-specific tools."""

    def _validate_symbol(self, symbol: str) -> str:
        """Normalize symbol: uppercase, strip whitespace."""
        return symbol.upper().strip()

    def _get_from_cache_or_db(self, key: str, ttl_seconds: int = 3600):
        """Redis cache → DB fallback."""
        pass

    def _log_tool_call(self, symbol: str, tool_name: str, latency_ms: float):
        """Log để measure tool performance."""
        pass
```

## 13.2 Tool Registry — Mapping đầy đủ

| Tool | Input | Output | Cache TTL | DB Source |
|------|-------|--------|-----------|-----------|
| `OHLCVTool` | symbol, days | adj_close OHLCV DataFrame | 1 giờ | ohlcv + adj_factor |
| `TechnicalIndicatorsTool` | symbol, date | 40+ indicators dict | 6 giờ | technical_indicators |
| `FactorScoresTool` | symbol, date | factor scores + percentile | 6 giờ | factor_scores |
| `FinancialStatementsTool` | symbol, period | IS/BS/CF JSON | 1 ngày | financial_statements |
| `FinancialRatiosTool` | symbol | PE/PB/ROE/... | 1 ngày | financial_ratios |
| `RiskFlagsTool` | symbol | active flags list | 1 giờ | risk_flags |
| `InsiderTradesTool` | symbol, days | insider trades list | 1 ngày | insider_trades |
| `ForeignFlowTool` | symbol, days | net buy/sell VND | 30 phút | ohlcv + DNSE |
| `NewsRAGTool` | symbol, query | relevant news list | 30 phút | news + TF-IDF |
| `MacroIndicatorsTool` | - | rates, USD/VND, oil, VIX | 1 ngày | macro_indicators |
| `OrderbookTool` | symbol | L2 orderbook snapshot | Real-time | Redis / DNSE WS |
| `MLPredictionTool` | symbol | 5d return proba + direction | 6 giờ | Computed |
| `AlphaSignalTool` | symbol, alpha_ids | signal values + IC | 6 giờ | alpha_signals |
| `ScreenerTool` | criteria dict | matching symbols list | 1 giờ | Computed |
| `VNCalendarTool` | date | trading_day, session, tet_days | Static | Config |

## 13.3 Tool Chaining Pattern

```python
# Đây là pattern agents dùng để lấy đủ context cho 1 stock
# brain/agents/utils/vn_context_builder.py

async def build_full_vn_context(symbol: str, date: str) -> dict:
    """
    Pre-fetch tất cả dữ liệu cần thiết cho 1 stock analysis.
    Chạy parallel để giảm latency.
    """
    tasks = {
        "ohlcv":       OHLCVTool().arun(symbol, days=60),
        "indicators":  TechnicalIndicatorsTool().arun(symbol, date),
        "factors":     FactorScoresTool().arun(symbol, date),
        "ratios":      FinancialRatiosTool().arun(symbol),
        "statements":  FinancialStatementsTool().arun(symbol, "4Q"),
        "risk_flags":  RiskFlagsTool().arun(symbol),
        "insider":     InsiderTradesTool().arun(symbol, days=30),
        "foreign":     ForeignFlowTool().arun(symbol, days=10),
        "news":        NewsRAGTool().arun(symbol, query="recent"),
        "macro":       MacroIndicatorsTool().arun(),
        "orderbook":   OrderbookTool().arun(symbol),
        "ml_pred":     MLPredictionTool().arun(symbol),
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    context = dict(zip(tasks.keys(), results))

    # Log missing/failed tools
    for key, val in context.items():
        if isinstance(val, Exception):
            logger.warning(f"Tool {key} failed for {symbol}: {val}")
            context[key] = None

    return context
```

---

# PHẦN XIV — MONITORING & OBSERVABILITY

## 14.1 Metrics cần track

```python
# app/services/monitoring.py (bổ sung)

class AIInvestMonitoring:
    """
    Track system health + AI performance metrics.
    """

    # System metrics
    SYSTEM_METRICS = [
        "dnse_ws_connection_status",      # WebSocket có connected không
        "dnse_ws_reconnect_count_1h",     # Số lần reconnect trong 1h
        "redis_lag_ms",                    # Redis pub/sub latency
        "daily_etl_last_run_ts",          # ETL chạy lần cuối lúc nào
        "daily_etl_duration_minutes",     # ETL mất bao lâu
        "db_pool_active_connections",     # DB connections hiện tại
        "ohlcv_symbols_updated_today",    # Bao nhiêu symbols được update
    ]

    # AI performance metrics
    AI_METRICS = [
        "signal_accuracy_5d_rolling",     # Win rate tín hiệu 5 ngày qua
        "signal_accuracy_20d_rolling",    # Win rate 20 ngày qua
        "avg_confidence_score_daily",     # Confidence score trung bình
        "agent_analysis_latency_p95",     # P95 latency toàn bộ pipeline
        "llm_token_cost_daily_usd",       # Chi phí LLM mỗi ngày
        "llm_error_rate_1h",              # Tỷ lệ LLM call thất bại
        "paper_trading_daily_pnl_pct",   # P&L paper trading hôm nay
        "paper_trading_drawdown_pct",     # Drawdown hiện tại
    ]

    # Data quality metrics
    DATA_METRICS = [
        "adj_close_coverage_pct",         # % symbols có adj_close
        "risk_flags_last_crawl_ts",       # UBCKNN crawl lần cuối
        "financial_statements_stale_count", # Bao nhiêu symbols có stale data
        "news_ingestion_count_daily",     # Số tin tức crawl được hôm nay
    ]
```

## 14.2 Alerting Rules

```python
ALERT_RULES = {
    # Critical — cần xử lý ngay
    "dnse_ws_disconnected_5min": {
        "condition": "dnse_ws_reconnect_count_5min > 3",
        "severity": "CRITICAL",
        "action": "Slack + restart hub",
    },
    "daily_etl_failed": {
        "condition": "daily_etl_last_run_ts > 20:00 AND date = today",
        "severity": "CRITICAL",
        "action": "Slack + manual trigger",
    },
    "paper_trading_drawdown_10pct": {
        "condition": "paper_trading_drawdown_pct > 0.10",
        "severity": "CRITICAL",
        "action": "Auto-pause trading + Slack",
    },

    # Warning — theo dõi
    "signal_accuracy_below_50pct": {
        "condition": "signal_accuracy_20d_rolling < 0.50",
        "severity": "WARNING",
        "action": "Slack + review hypothesis",
    },
    "llm_cost_high": {
        "condition": "llm_token_cost_daily_usd > 5.0",
        "severity": "WARNING",
        "action": "Review token usage",
    },
    "stale_financial_data": {
        "condition": "financial_statements_stale_count > 50",
        "severity": "WARNING",
        "action": "Trigger vnstock refresh",
    },
}
```

---

# PHẦN XV — DEPLOYMENT & OPERATIONS

## 15.1 Docker Compose bổ sung

```yaml
# docker-compose.yml — bổ sung services

services:
  # Existing: postgres, redis, ai-engine, back-end, front-end

  # Thêm: Daily ETL worker
  etl-worker:
    build: ./ai-engine
    command: python -m app.workers.daily_etl
    environment:
      - RUN_MODE=worker
      - WORKER_TYPE=daily_etl
    depends_on: [postgres, redis]
    restart: unless-stopped

  # Thêm: Signal tracking worker
  signal-tracker:
    build: ./ai-engine
    command: python -m app.workers.signal_tracker
    environment:
      - RUN_MODE=worker
      - WORKER_TYPE=signal_tracker
    depends_on: [postgres, redis]
    restart: unless-stopped

  # Thêm: Monitoring + alerting
  monitoring:
    build: ./ai-engine
    command: python -m app.workers.monitoring
    environment:
      - SLACK_WEBHOOK_URL=${SLACK_WEBHOOK_URL}
    depends_on: [postgres, redis]
    restart: unless-stopped
```

## 15.2 Environment Variables cần thêm

```bash
# .env additions

# LLM Providers
GROQ_API_KEY=...
GEMINI_API_KEY=...              # Thêm mới
OPENAI_API_KEY=...              # Fallback
NVIDIA_API_KEY=...

# Data Sources
VIMO_API_TOKEN=...
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...

# Monitoring
SLACK_WEBHOOK_URL=...
ALERT_EMAIL=...

# Trading
MAX_DRAWDOWN_HARD_STOP=0.10     # 10% drawdown → auto-pause
MAX_POSITION_SIZE_PCT=0.05      # 5% per stock
MIN_CONFIDENCE_TO_TRADE=0.65    # Confidence threshold
PAPER_TRADING_INITIAL_CASH=1000000000  # 1 tỷ VND paper
```

## 15.3 Cron Jobs

```bash
# Crontab cho ai-engine container

# Daily ETL — sau khi thị trường đóng cửa
00 18 * * 1-5 python -m app.workers.daily_etl

# UBCKNN risk flags crawl — mỗi sáng trước khi thị trường mở
30 8  * * 1-5 python -m app.workers.risk_flags_crawl

# Signal evaluation — 5 ngày sau mỗi signal
# (chạy daily, evaluate signals đã đủ 5 ngày)
00 19 * * 1-5 python -m app.workers.signal_evaluator

# CafeF news ingestion — mỗi giờ trong giờ trading
*/30 9-15 * * 1-5 python -m app.workers.news_ingestion

# Macro indicators update — mỗi sáng
15 8  * * 1-5 python -m app.workers.macro_update

# Weekly: Re-benchmark alpha factors (Thứ 7)
00 10 * * 6 python -m app.workers.factor_benchmark

# Monthly: Retrain ML models (ngày 1 mỗi tháng)
00 22 1 * * python -m app.workers.ml_retrain
```

---

# PHẦN XVI — DEPENDENCY MAP & FILE STRUCTURE

## 16.1 Files cần tạo mới (theo priority)

### Phase 0 — Tuần 1-3

```
ai-engine/
├── app/
│   ├── services/
│   │   ├── scrapers/
│   │   │   ├── ubcknn_scraper.py       # UBCKNN risk flags
│   │   │   └── cafef_insider_scraper.py # Insider trades structured
│   │   ├── macro_service.py            # Vimo + yfinance macro
│   │   └── daily_etl.py                # Master ETL pipeline
│   └── workers/
│       ├── daily_etl.py                # Entry point
│       ├── risk_flags_crawl.py
│       ├── signal_evaluator.py
│       └── macro_update.py
└── brain/
    └── dataflows/
        └── vendors/
            └── vn/
                └── adj_close.py        # adj_close pipeline
```

### Phase 1 — Tuần 4-7

```
brain/
├── quant/
│   └── factors/
│       ├── vn_bench_runner.py          # VN-specific IC benchmark
│       └── zoo/
│           └── vn_specific/            # VN factors (momentum, liquidity, event)
│               ├── __init__.py
│               ├── vn_momentum.py
│               ├── vn_liquidity.py
│               ├── vn_value.py
│               └── vn_event.py
└── tools/
    └── vn_calendar.py                  # VN trading calendar
```

### Phase 2 — Tuần 8-10

```
brain/
├── agents/
│   └── core/
│       └── agent_core.py               # Unified ReAct loop (thay loop.py)
├── providers/
│   └── gemini_client.py                # Gemini Flash + Pro
└── quant/
    └── skills_data/
        ├── vn-trading-rules/
        │   └── SKILL.md
        ├── vn-sector-analysis/
        │   └── SKILL.md
        └── vn-macro-calendar/
            └── SKILL.md
```

### Phase 3 — Tuần 11-14

```
brain/
├── eval/
│   ├── __init__.py
│   ├── signal_tracker.py               # Signal accuracy tracking
│   ├── llm_judge.py                    # LLM-as-judge
│   └── performance_report.py           # Weekly report generator
└── state/
    └── presets/
        └── vn_equity_desk.yaml         # VN-specific swarm preset
```

## 16.2 Files cần sửa đổi

| File | Thay đổi |
|------|---------|
| `brain/state/worker.py` | Kế thừa AgentCore thay vì tự viết ReAct loop |
| `brain/providers/router.py` | Thêm Gemini routes |
| `app/services/data_enricher.py` | Đọc từ pre-computed tables, không tính on-demand |
| `app/services/risk_flags.py` | Đọc từ risk_flags table thực thay hash-based |
| `app/services/backfill_service.py` | Trigger daily_etl.py sau khi backfill xong |
| `brain/quant/factors/bench_runner.py` | Dùng adj_close + VN constraints |
| `brain/state/grounding.py` | Dùng adj_close cho grounding check |
| `prisma/schema.prisma` | Thêm 9 tables mới |

---

# PHẦN XVII — HỆ THỐNG ĐÁNH GIÁ RISK
## Composite Risk Score + Risk Flags

> **⚠️ Thực tế:** Đã implement **10 computed flags** (`risk_flags_v2.py`), đơn giản và hiệu quả hơn CRS 7 tầng. Tuy nhiên, thiết kế CRS 7 tầng bên dưới vẫn giữ làm **tham khảo cho nâng cấp sau này**.

### Đã implement: 10 computed flags

```
Structured Data:
├── financial_statements ──→ CANH_BAO_TC (HARD), CHAM_BAO_TC (HARD)
│                          ├── DEBT_DANGER (HARD), DEBT_DANGER_FIN (HARD)
│                          ├── CAR_DANGER (HARD)
│                          └── M_SCORE_FLAG (SOFT), F_SCORE_FLAG (SOFT)
├── technical_indicators ──→ FLOOR_TRAP, SHARP_DROP, KHOI_LUONG_BAT_THUONG
├── foreign_flow ─────────→ FOREIGN_FLOW_ANOMALY
├── insider_trades ───────→ INSIDER_SELLING_ANOMALY
└── news_events ──────────→ GOVERNANCE_SHOCK
         ↓ RiskGate (Layer 4)
    HARD flags → DO_NOT_TRADE | SOFT flags → position size adjustment
```

| # | Flag | Type | File | Logic |
|---|------|------|------|-------|
| 1-5 | CANH_BAO_TC, DEBT_DANGER... | HARD | `risk_flags_v2.py` | Từ financial_statements |
| 6-10 | FLOOR_TRAP, FOREIGN_FLOW... | SOFT | `risk_flags_v2.py` | Từ technical/flow/insider |

---

## Thiết kế tham khảo: CRS 7 Tầng (cho nâng cấp sau này)

> ⚠️ Code dưới đây là **thiết kế gốc**, chưa implement full. Hiện tại đã thay bằng 10 computed flags ở trên. Giữ lại để tham khảo khi có đủ data sources.

## 17.0 Kiến trúc tổng thể

```
TẦNG 1: Kỹ thuật & Quant       ──┐
TẦNG 2: Cơ bản doanh nghiệp    ──┤
TẦNG 3: Cấu trúc thị trường VN ──┤
TẦNG 4: Vĩ mô Việt Nam         ──┼──→ Composite Risk Score (CRS)
TẦNG 5: Vĩ mô toàn cầu         ──┤         ↓
TẦNG 6: Pháp lý & Chính sách   ──┤   Hard Override Flags
TẦNG 7: Tâm lý & Hành vi       ──┘         ↓
                                       DECISION GATE (Layer 4)
```

### Trọng số mặc định

| Tầng | Tên | Weight | Lý do |
|------|-----|--------|-------|
| 1 | Kỹ thuật & Quant | 0.20 | Quantifiable, objective |
| 2 | Cơ bản doanh nghiệp | 0.20 | Foundation of value |
| 3 | Cấu trúc thị trường VN | 0.20 | VN-specific, high impact |
| 4 | Vĩ mô VN | 0.15 | Systemic risk |
| 5 | Vĩ mô toàn cầu | 0.10 | External shock |
| 6 | Pháp lý & Chính sách | 0.10 | Binary impact |
| 7 | Tâm lý & Hành vi | 0.05 | Hard to quantify |
| **TOTAL** | | **1.00** | |

> Trọng số điều chỉnh theo ngành: Banking → tăng Tầng 6 lên 0.15; Real estate → tăng Tầng 6 lên 0.20; Export → tăng Tầng 5 lên 0.18.

### Wiring vào Decision Gate (Layer 4)

```python
# brain/state/signal_processing.py — tích hợp CRS vào pipeline

async def evaluate_trade_signal(symbol: str, sector: str,
                                 agent_decision: dict) -> dict:
    """
    Chạy VNCompositeRiskScorer trước khi pass signal đến Trader agent.
    """
    scorer  = VNCompositeRiskScorer()
    context = await build_risk_context(symbol)     # Fetch all 7 layers data
    risk    = scorer.compute(symbol, sector, context)

    # Hard block — dừng ngay, không cần xem agent decision
    if risk["hard_blocked"]:
        return {
            "action":    "DO_NOT_TRADE",
            "reason":    f"Hard block: {risk['hard_block_flags']}",
            "crs_score": risk["crs_score"],
        }

    # Soft block — human review
    if risk["soft_blocked"]:
        return {
            "action":    "REQUIRE_HUMAN_REVIEW",
            "crs_score": risk["crs_score"],
            "flags":     risk["soft_block_flags"],
        }

    # Adjust position size theo CRS
    base_size = agent_decision.get("position_size_pct", 0.05)
    adjusted  = _adjust_size_by_risk(base_size, risk["crs_score"])

    return {
        "action":       agent_decision["action"],
        "position_size": adjusted,
        "crs_score":    risk["crs_score"],
        "risk_level":   risk["risk_level"],
        "dominant_risk":risk["dominant_risk"],
    }

def _adjust_size_by_risk(base_size: float, crs: float) -> float:
    """Kelly-adjusted sizing theo risk score."""
    if crs > 0.55:  return base_size * 0.25   # Very high risk → 25% of normal
    if crs > 0.40:  return base_size * 0.50   # High → 50%
    if crs > 0.25:  return base_size * 0.75   # Medium → 75%
    return base_size                            # Low → full size
```

---

## 17.1 Tầng 1 — Kỹ thuật & Quant Risk

### CVaR & VaR

```python
# brain/risk/layers/quant_risk.py

def compute_var_cvar(returns: pd.Series,
                     confidence: float = 0.95,
                     window: int = 252) -> dict:
    r = returns.dropna().tail(window)
    var_95  = np.percentile(r, (1 - confidence) * 100)
    cvar_95 = r[r <= var_95].mean()
    var_99  = np.percentile(r, 1)
    cvar_99 = r[r <= var_99].mean()
    floor_hit_rate = (r <= -0.069).mean()
    ceil_hit_rate  = (r >= 0.069).mean()
    return {
        "var_95": round(abs(var_95), 4), "cvar_95": round(abs(cvar_95), 4),
        "var_99": round(abs(var_99), 4), "cvar_99": round(abs(cvar_99), 4),
        "floor_hit_rate": round(floor_hit_rate, 4),
        "ceil_hit_rate":  round(ceil_hit_rate, 4),
        "risk_score":     min(abs(cvar_95) / 0.05, 1.0),
    }
```

### GARCH(1,1) Volatility Forecast

```python
from arch import arch_model

def compute_garch_forecast(returns: pd.Series) -> dict:
    r      = returns.dropna() * 100
    model  = arch_model(r, vol='GARCH', p=1, q=1, dist='skewt')
    result = model.fit(disp='off', show_warning=False)
    forecast     = result.forecast(horizon=1, reindex=False)
    vol_tomorrow = float(forecast.variance.iloc[-1, 0]) ** 0.5 / 100
    hist_vol     = returns.rolling(60).std().iloc[-1] * (252 ** 0.5)
    ratio = vol_tomorrow * (252**0.5) / hist_vol if hist_vol > 0 else 1.0
    regime = ("LOW" if ratio < 0.7 else "NORMAL" if ratio < 1.3
              else "ELEVATED" if ratio < 2.0 else "CRISIS")
    return {"vol_1d_forecast": round(vol_tomorrow, 4),
            "vol_annualized": round(vol_tomorrow * (252 ** 0.5), 4),
            "vol_regime": regime, "risk_score": min(vol_tomorrow / 0.03, 1.0)}
```

### Amihud Illiquidity

```python
def compute_amihud_illiquidity(ohlcv: pd.DataFrame, window: int = 20) -> dict:
    ohlcv = ohlcv.copy()
    ohlcv["dollar_vol"] = ohlcv["volume"] * ohlcv["adj_close"]
    ohlcv["abs_return"] = ohlcv["adj_close"].pct_change().abs()
    ohlcv["illiq"]      = ohlcv["abs_return"] / ohlcv["dollar_vol"].replace(0, np.nan)
    illiq_20d  = ohlcv["illiq"].tail(window).mean()
    avg_val_bn = ohlcv["dollar_vol"].tail(20).mean() / 1e9
    score = (0.0 if avg_val_bn >= 20 else 0.3 if avg_val_bn >= 5
             else 0.7 if avg_val_bn >= 1 else 1.0)
    return {"amihud_illiq_20d": round(illiq_20d * 1e6, 4),
            "avg_daily_value_bn": round(avg_val_bn, 2), "risk_score": score}
```

---

## 17.2 Tầng 2 — Cơ Bản Doanh Nghiệp

### Earnings Quality — Accrual Analysis

```python
def compute_accrual_risk(financials: dict) -> dict:
    ni  = financials.get("net_income", 0)
    cfo = financials.get("cfo", 0)
    ta  = financials.get("total_assets", 1)
    accrual_ratio = (ni - cfo) / ta
    ccr = cfo / ni if ni != 0 else 0
    rev_4q = financials.get("revenue_4q", [1, 1, 1, 1])
    q4_spike = rev_4q[-1] / (np.mean(rev_4q[:-1]) or 1)

    score = 0.0
    if accrual_ratio > 0.15: score += 0.40
    elif accrual_ratio > 0.08: score += 0.20
    if ccr < 0.5: score += 0.30
    if q4_spike > 2.0: score += 0.30

    return {"accrual_ratio": round(accrual_ratio, 4),
            "cash_conversion": round(ccr, 4), "q4_spike": round(q4_spike, 2),
            "risk_score": round(min(score, 1.0), 3)}
```

### Altman Z′-Score (Emerging Markets)

```python
def compute_altman_z_prime(financials: dict, sector: str = "general") -> dict:
    if sector == "banking":
        return _compute_banking_distress(financials)
    ta, tl = financials.get("total_assets", 1), financials.get("total_liabilities", 0)
    ca, cl = financials.get("current_assets", 0), financials.get("current_liabilities", 0)
    re, ebit = financials.get("retained_earnings", 0), financials.get("ebit", 0)
    bve, bvl = financials.get("book_value_equity", ta - tl), financials.get("book_value_liabilities", tl)
    z = 6.56*(ca-cl)/ta + 3.26*re/ta + 6.72*ebit/ta + 1.05*bve/(bvl or 1)
    zone = ("DISTRESS" if z < 1.1 else "GREY" if z < 2.6 else "SAFE")
    return {"z_prime_score": round(z, 3), "zone": zone,
            "risk_score": round(max(0, min(1, (2.6-z)/2.6)), 3)}
```

### Leverage Stress Test

```python
def compute_leverage_stress(financials: dict, sector: str) -> dict:
    debt, equity = financials.get("total_debt", 0), financials.get("total_equity", 1)
    ebit, interest = financials.get("ebit", 1), financials.get("interest_expense", 0)
    de = debt / (equity or 1); icr = ebit / (interest or 1)
    stressed_icr = (ebit * 0.70) / (interest or 1)
    th = {"real_estate": {"de_safe": 3.0, "icr_min": 1.5},
          "utilities": {"de_safe": 2.5, "icr_min": 2.0}}.get(sector, {"de_safe": 2.0, "icr_min": 2.5})
    score = (0.30 if de > th["de_safe"]*1.5 else 0.15 if de > th["de_safe"] else 0) + \
            (0.30 if icr < th["icr_min"]*0.7 else 0.15 if icr < th["icr_min"] else 0) + \
            (0.25 if debt/(financials.get('ebitda',ebit) or 1) > 5.0 else 0) + \
            (0.15 if stressed_icr < 1.0 else 0)
    return {"de_ratio": round(de, 2), "icr": round(icr, 2),
            "risk_score": round(min(score, 1.0), 3)}
```

---

## 17.3 Tầng 3 — Cấu Trúc Thị Trường VN

### Pledge Risk

```python
def compute_pledge_risk(ownership: dict, price_data: pd.Series) -> dict:
    pledge_pct = ownership.get("pledged_shares", 0) / (ownership.get("shares_outstanding", 1) or 1)
    score = (0.50 if pledge_pct > 0.40 else 0.30 if pledge_pct > 0.20 else 0.15 if pledge_pct > 0.10 else 0)
    pledge_price = ownership.get("pledge_price")
    if pledge_price and price_data.iloc[-1] > 0:
        dist = (price_data.iloc[-1] - pledge_price * 0.80) / price_data.iloc[-1]
        if dist < 0.05: score += 0.40
        elif dist < 0.15: score += 0.20
    return {"pledge_pct": round(pledge_pct, 4), "risk_score": round(min(score, 1.0), 3)}
```

### Foreign Room Risk

```python
def compute_foreign_room_risk(foreign_data: dict, flow: pd.Series) -> dict:
    room = foreign_data.get("room_remaining_pct", 0.10)
    net_10d = flow.tail(10).sum() / 1e9 if not flow.empty else 0
    sell_streak = sum(1 for v in reversed(flow.tail(10)) if v < 0) if not flow.empty else 0
    score = (0.40 if room < 0.02 and net_10d < 0 else 0.20 if room < 0.05 else 0) + \
            (0.30 if sell_streak >= 7 else 0.15 if sell_streak >= 4 else 0) + \
            (0.30 if net_10d < -50 else 0)
    return {"room_remaining_pct": round(room, 4), "risk_score": round(min(score, 1.0), 3)}
```

### Margin Call Cascade Detector

```python
def detect_margin_cascade(ohlcv: pd.DataFrame) -> dict:
    returns = ohlcv["adj_close"].pct_change()
    volumes = ohlcv["volume"]
    vol_ratio = volumes.tail(3).mean() / (volumes.tail(20).mean() or 1)
    price_drop = ohlcv["adj_close"].iloc[-1] / ohlcv["adj_close"].iloc[-4] - 1
    floor_streak = sum(1 for r in returns.tail(10) if r <= -0.069)
    score = (0.50 if vol_ratio > 5.0 and price_drop < -0.10 else 0.30 if vol_ratio > 3.0 and price_drop < -0.06 else 0) + \
            (0.40 if floor_streak >= 3 else 0.15 if floor_streak >= 1 else 0)
    return {"volume_ratio_3d": round(vol_ratio, 2), "price_drop_3d": round(price_drop, 4),
            "is_cascade": score > 0.40, "risk_score": round(min(score, 1.0), 3)}
```

### Price Limit Trap

```python
def compute_price_limit_risk(ohlcv: pd.DataFrame, exchange: str = "HOSE") -> dict:
    LIMITS = {"HOSE": 0.07, "HNX": 0.10, "UPCOM": 0.15}
    thresh = LIMITS.get(exchange.upper(), 0.07) * 0.95
    ret = ohlcv["adj_close"].pct_change().dropna().tail(15)
    ceil_cnt = sum(1 for r in reversed(ret) if r >= thresh)
    floor_cnt = sum(1 for r in reversed(ret) if r <= -thresh)
    score = (0.50 if ceil_cnt >= 4 else 0.25 if ceil_cnt >= 2 else 0) + \
            (0.60 if floor_cnt >= 3 else 0.20 if floor_cnt >= 1 else 0)
    return {"ceil_streak": ceil_cnt, "floor_streak": floor_cnt,
            "exit_blocked": floor_cnt >= 2, "risk_score": round(min(score, 1.0), 3)}
```

---

## 17.4 Tầng 4 — Vĩ Mô Việt Nam

### Lãi suất SBV & Nhạy cảm ngành

```python
SENSITIVITY = {"banking": 0.8, "real_estate": 0.9, "utilities": 0.7, "consumer": 0.5, "export": 0.2}
def compute_rate_sensitivity(sector: str, macro: dict) -> dict:
    w = SENSITIVITY.get(sector, 0.3)
    trend = macro.get("rate_trend", "STABLE")
    score = 0.6*w if trend == "RISING" and sector in ("real_estate","utilities") else \
            0.3*w if trend == "RISING" and sector == "banking" else \
            0.15*w if trend == "STABLE" else 0
    return {"rate_trend": trend, "risk_score": round(min(score, 1.0), 3)}
```

### Tỷ giá VND/USD

```python
FX_MAP = {"materials": 0.7, "retail": 0.8, "banking": 0.4, "real_estate": 0.5}
def compute_fx_risk(sector: str, macro: dict) -> dict:
    mag = FX_MAP.get(sector, 0.1)
    trend = macro.get("vnd_trend", "STABLE")
    score = 0.5*mag if trend == "WEAKENING" and sector in ("retail","real_estate") else \
            0.3*mag if trend == "STRENGTHENING" and sector == "materials" else 0
    return {"vnd_trend": trend, "risk_score": round(min(score, 1.0), 3)}
```

---

## 17.5 Tầng 5 — Vĩ Mô Toàn Cầu

```python
GLOBAL_MAP = {"export": 0.8, "materials": 0.7, "banking": 0.3, "technology": 0.4}
def compute_global_risk(sector: str, macro: dict) -> dict:
    mag = GLOBAL_MAP.get(sector, 0.2)
    vix = macro.get("vix", 15)
    dxy = macro.get("usd_index", 104)
    score = min((vix - 15) / 30, 1.0) * 0.6 * mag + \
            min((dxy - 100) / 15, 1.0) * 0.4 * mag
    return {"vix": vix, "dxy": dxy, "risk_score": round(min(score, 1.0), 3)}
```

---

## 17.6 Tầng 6 — Pháp Lý & Chính Sách

```python
SECTOR_REG_RISK = {"real_estate": 0.4, "banking": 0.3, "pharma": 0.35, "education": 0.40}
def compute_regulatory_risk(sector: str, reg_data: dict) -> dict:
    score = SECTOR_REG_RISK.get(sector, 0.1) * 0.3
    if reg_data.get("under_investigation"): score += 0.50
    if reg_data.get("tax_dispute"): score += 0.25
    if reg_data.get("recent_policy_change"): score += 0.20
    return {"risk_score": round(min(score, 1.0), 3)}
```

---

## 17.7 Tầng 7 — Tâm Lý & Hành Vi

```python
def compute_retail_sentiment_risk(sentiment: dict, ohlcv: pd.DataFrame) -> dict:
    ret = ohlcv["adj_close"].pct_change()
    vol_ratio = ohlcv["volume"].tail(5).mean() / (ohlcv["volume"].tail(60).mean() or 1)
    ret_5d = ret.tail(5).sum()
    fomo = ret_5d > 0.15 and vol_ratio > 3.0
    fud = ret_5d < -0.10 and sentiment.get("news_sentiment_5d", 0) < -0.3
    score = (0.40 if fomo else 0) + (0.30 if fud else 0)
    return {"fomo": fomo, "fud": fud, "risk_score": round(min(score, 1.0), 3)}
```

---

## 17.8 Composite Risk Scorer — Full Implementation (tham khảo)

```python
# brain/risk/composite_scorer.py

class VNCompositeRiskScorer:
    DEFAULT_WEIGHTS = {"layer1_quant": 0.20, "layer2_fundamental": 0.20,
                       "layer3_market_vn": 0.20, "layer4_macro_vn": 0.15,
                       "layer5_global": 0.10, "layer6_regulatory": 0.10,
                       "layer7_behavioral": 0.05}
    SECTOR_OVERRIDES = {"banking": {"layer2": 0.25, "layer6": 0.15},
                        "real_estate": {"layer6": 0.20, "layer3": 0.20},
                        "export": {"layer5": 0.18, "layer4": 0.12}}
    HARD_BLOCK_FLAGS = ["CRITICAL_REGULATORY_ACTION", "UNDER_INVESTIGATION",
                        "ADVERSE_AUDIT_OPINION", "DELIST_CONFIRMED", "TRADING_SUSPENDED"]
    SOFT_BLOCK_FLAGS = ["QUALIFIED_AUDIT_OPINION", "EXTREME_PLEDGE_RATIO",
                        "PUMP_PATTERN_DETECTED", "NEAR_MARGIN_CALL", "EXIT_BLOCKED"]

    def compute(self, symbol: str, sector: str, all_layers: dict) -> dict:
        weights = self._get_weights(sector)
        layer_scores = {k: all_layers.get(k, {}).get("score", 0) for k in weights}
        crs = sum(layer_scores[k] * weights[k] for k in weights)
        all_flags = list(set(f for ld in all_layers.values()
                             for f in ld.get("flags", [])))
        hard = any(f in all_flags for f in self.HARD_BLOCK_FLAGS)
        soft = any(f in all_flags for f in self.SOFT_BLOCK_FLAGS)
        return {"symbol": symbol, "sector": sector, "crs_score": round(crs, 3),
                "risk_level": "BLOCKED" if hard else "HIGH" if crs > 0.55 else "MEDIUM",
                "hard_blocked": hard, "soft_blocked": soft,
                "hard_block_flags": [f for f in all_flags if f in self.HARD_BLOCK_FLAGS],
                "soft_block_flags": [f for f in all_flags if f in self.SOFT_BLOCK_FLAGS],
                "all_flags": all_flags, "layer_scores": {k: round(v, 3) for k, v in layer_scores.items()},
                "recommendation": ("DO_NOT_TRADE" if hard else "REQUIRE_HUMAN_REVIEW" if soft
                                   else "REDUCE_SIZE_50PCT" if crs > 0.55 else "NORMAL_SIZING")}

    def _get_weights(self, sector: str) -> dict:
        w = self.DEFAULT_WEIGHTS.copy()
        for k, v in self.SECTOR_OVERRIDES.get(sector, {}).items():
            if k in w: w[k] = v
        total = sum(w.values())
        return {k: v/total for k, v in w.items()}
```

---

## 17.9 DB Schema & Ngưỡng Calibration (tham khảo)

```sql
CREATE TABLE risk_assessments (
    id               SERIAL PRIMARY KEY,
    symbol           TEXT NOT NULL,
    assessment_date  DATE NOT NULL,
    sector           TEXT,
    crs_score        FLOAT,
    risk_level       TEXT,
    hard_blocked     BOOLEAN DEFAULT FALSE,
    soft_blocked     BOOLEAN DEFAULT FALSE,
    recommendation   TEXT,
    score_quant      FLOAT, score_fundamental FLOAT, score_market_vn FLOAT,
    score_macro_vn   FLOAT, score_global FLOAT, score_regulatory FLOAT,
    score_behavioral FLOAT,
    hard_flags       TEXT[], soft_flags TEXT[], all_flags TEXT[],
    detail           JSONB,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (symbol, assessment_date)
);
```

### Ngưỡng calibration cho VN

| Metric | Very Low | Low | Medium | High | Very High |
|--------|---------|-----|--------|------|-----------|
| CRS tổng | < 0.15 | 0.15–0.25 | 0.25–0.40 | 0.40–0.55 | > 0.55 |
| CVaR 95% | < 1.5% | 1.5–2% | 2–3% | 3–4% | > 4% |
| Daily Vol | < 1% | 1–1.5% | 1.5–2.5% | 2.5–3.5% | > 3.5% |
| Altman Z′ | > 3.5 | 2.6–3.5 | 1.1–2.6 | 0.5–1.1 | < 0.5 |
| VIX | < 15 | 15–18 | 18–25 | 25–30 | > 30 |

## Wiring vào Decision Gate (Layer 4)

```python
# app/brain/state/signal_processing.py — thực tế
async def evaluate_trade_signal(symbol, sector, agent_decision):
    """Kiểm tra risk flags trước khi pass signal đến Trader agent."""
    flags = get_active_flags(symbol)

    hard_flags = [f for f in flags if f.flag_type in HARD_FLAGS]
    if hard_flags:
        return {"action": "DO_NOT_TRADE",
                "reason": f"Hard block: {[f.flag_type for f in hard_flags]}"}

    soft_flags = [f for f in flags if f.flag_type in SOFT_FLAGS]
    if soft_flags:
        adjusted_size = agent_decision.get("position_size_pct", 0.05) * 0.5
        return {"action": agent_decision["action"],
                "position_size": adjusted_size,
                "warnings": [f.flag_type for f in soft_flags]}

    return {"action": agent_decision["action"],
            "position_size": agent_decision.get("position_size_pct", 0.05)}
```

```python
def compute_fx_risk(sector: str, macro_data: dict) -> dict:
    """
    VND mất giá → Exporters (HPG, VNM) được lợi, Importers thiệt.
    """
    FX_MAP = {
        "materials":   {"net_exposure": "POSITIVE", "magnitude": 0.7},
        "retail":      {"net_exposure": "NEGATIVE", "magnitude": 0.8},
        "banking":     {"net_exposure": "MIXED",    "magnitude": 0.4},
        "real_estate": {"net_exposure": "NEGATIVE", "magnitude": 0.5},
    }
    exp        = FX_MAP.get(sector, {"net_exposure": "NEUTRAL", "magnitude": 0.1})
    vnd_trend  = macro_data.get("vnd_trend", "STABLE")
    usd_change = macro_data.get("usd_vnd_change_ytd", 0)

    score = 0.0
    if vnd_trend == "WEAKENING" and exp["net_exposure"] == "NEGATIVE":
        score = 0.5 * exp["magnitude"]
    elif vnd_trend == "STRENGTHENING" and exp["net_exposure"] == "POSITIVE":
        score = 0.3 * exp["magnitude"]
    if abs(usd_change) > 0.05:
        score = min(score * 1.5, 1.0)

    return {"vnd_trend": vnd_trend, "usd_change_ytd": round(usd_change, 4),
            "sector_exposure": exp["net_exposure"],
            "risk_score": round(min(score, 1.0), 3)}
```

### Credit Cycle Risk

```python
def compute_credit_cycle_risk(macro_data: dict, sector: str) -> dict:
    credit_growth = macro_data.get("credit_growth_yoy", 0.12)
    npl_system    = macro_data.get("system_npl_ratio", 0.02)
    re_credit_pct = macro_data.get("re_credit_pct_total", 0.20)

    score = 0.0; flags = []
    if credit_growth > 0.20: score += 0.30; flags.append("CREDIT_OVERHEAT")
    elif credit_growth > 0.15: score += 0.15
    if npl_system > 0.04: score += 0.35; flags.append("SYSTEM_NPL_HIGH")
    if re_credit_pct > 0.25: score += 0.20; flags.append("RE_CREDIT_OVERCONCENTRATED")
    if sector in ["real_estate", "banking", "construction"]:
        score = min(score * 1.3, 1.0)

    return {"credit_growth_yoy": round(credit_growth, 4),
            "system_npl": round(npl_system, 4),
            "flags": flags,
            "risk_score": round(min(score, 1.0), 3)}
```

---

## 17.5 Tầng 5 — Vĩ Mô Toàn Cầu

### Global Risk-Off (VIX & EM Flows)

```python
def compute_global_riskoff(macro_data: dict) -> dict:
    vix        = macro_data.get("vix", 20)
    vix_change = macro_data.get("vix_change_5d", 0)
    dxy_change = macro_data.get("dxy_change_1m", 0)
    em_flow    = macro_data.get("em_net_flow_7d_bn_usd", 0)

    score = 0.0; flags = []
    if vix > 30:        score += 0.40; flags.append("VIX_FEAR_ZONE")
    elif vix > 25:      score += 0.25
    if vix_change > 5:  score += 0.20; flags.append("VIX_SPIKE")
    if dxy_change > 0.03: score += 0.20; flags.append("DXY_SURGE")
    if em_flow < -5:    score += 0.20; flags.append("EM_OUTFLOW")

    return {"vix": vix, "dxy_change_1m": round(dxy_change, 4),
            "em_flow_7d_bn": round(em_flow, 2),
            "flags": flags, "risk_score": round(min(score, 1.0), 3)}
```

### Commodity Price Risk & China Slowdown

```python
def compute_commodity_risk(sector: str, macro_data: dict) -> dict:
    """
    Oil → GAS/PVD (positive), Transport/Aviation (negative)
    Steel → HPG/HSG (positive), Construction (negative)
    Coal → POW/NT2 (negative — input cost)
    """
    EXPOSURE = {
        "oil_gas":      ("oil",    "POSITIVE"),
        "transport":    ("oil",    "NEGATIVE"),
        "steel":        ("steel",  "POSITIVE"),
        "construction": ("steel",  "NEGATIVE"),
        "utilities":    ("coal",   "NEGATIVE"),
    }
    if sector not in EXPOSURE:
        return {"risk_score": 0.0}

    key, direction = EXPOSURE[sector]
    change = macro_data.get(f"{key}_change_1m", 0)
    score  = 0.0
    if direction == "NEGATIVE" and change > 0.10:
        score = min(change * 2, 0.8)
    elif direction == "POSITIVE" and change < -0.10:
        score = min(abs(change) * 2, 0.6)

    return {"key_commodity": key, "commodity_change": round(change, 4),
            "exposure": direction, "risk_score": round(min(score, 1.0), 3)}

def compute_china_risk(sector: str, macro_data: dict) -> dict:
    SENSITIVITY = {
        "materials": 0.8, "tourism": 0.9, "retail": 0.5,
        "real_estate": 0.4, "technology": 0.3
    }
    s         = SENSITIVITY.get(sector, 0.2)
    china_pmi = macro_data.get("china_pmi", 50)
    score     = (0.40 if china_pmi < 48 else 0.20 if china_pmi < 50 else 0) * s
    return {"china_pmi": china_pmi, "sector_sensitivity": s,
            "risk_score": round(min(score, 1.0), 3)}
```

---

## 17.6 Tầng 6 — Pháp Lý & Chính Sách

```python
def compute_regulatory_risk(sector: str, regulatory_data: dict) -> dict:
    """
    VN: Thay đổi pháp lý thường xảy ra đột ngột.
    Real estate: Luật Đất đai 2024 còn nhiều bất định.
    Banking: SBV thay đổi room tín dụng thường xuyên.
    Gaming/Education: Rủi ro cấm cao nhất.
    """
    score = 0.0; flags = []

    sanctions = regulatory_data.get("active_sanctions", [])
    if any(s.get("severity") == "CRITICAL" for s in sanctions):
        score += 0.80; flags.append("CRITICAL_REGULATORY_ACTION")
    elif sanctions:
        score += 0.30; flags.append("REGULATORY_SANCTION")

    if regulatory_data.get("under_investigation"): score += 0.50; flags.append("UNDER_INVESTIGATION")
    if regulatory_data.get("tax_dispute"):          score += 0.25; flags.append("TAX_DISPUTE")

    SECTOR_RISK = {
        "real_estate": 0.4, "banking": 0.3, "pharma": 0.35,
        "education": 0.40, "gambling_gaming": 0.60,
    }
    score = min(score + SECTOR_RISK.get(sector, 0.1) * 0.3, 1.0)
    if regulatory_data.get("recent_policy_change"):
        score = min(score + 0.20, 1.0); flags.append("RECENT_POLICY_CHANGE")

    return {"flags": flags, "risk_score": round(score, 3)}
```

---

## 17.7 Tầng 7 — Tâm Lý & Hành Vi

### Retail Sentiment (F0 Effect)

```python
def compute_retail_sentiment_risk(sentiment_data: dict, ohlcv: pd.DataFrame) -> dict:
    """
    VN: ~85% volume từ nhà đầu tư cá nhân → F0 effect rất mạnh.
    FOMO: Giá tăng nhanh + volume cao nhưng không có catalyst rõ ràng.
    FUD: Giá giảm nhanh + news xấu → panic sell.
    """
    returns         = ohlcv["adj_close"].pct_change()
    vol_ratio       = ohlcv["volume"].tail(5).mean() / ohlcv["volume"].tail(60).mean()
    recent_ret_5d   = returns.tail(5).sum()
    news_sentiment  = sentiment_data.get("news_sentiment_5d", 0)
    social_buzz_z   = sentiment_data.get("social_buzz_zscore", 0)

    fomo_signal = (recent_ret_5d > 0.15 and vol_ratio > 3.0 and news_sentiment < 0.3)
    fud_signal  = (recent_ret_5d < -0.10 and news_sentiment < -0.3)

    score = 0.0
    if fomo_signal: score += 0.40
    if fud_signal:  score += 0.30
    if social_buzz_z > 3.0: score += 0.30   # 3-sigma social spike = pump suspect

    return {"fomo_signal": fomo_signal, "fud_signal": fud_signal,
            "social_buzz_zscore": round(social_buzz_z, 2),
            "risk_score": round(min(score, 1.0), 3)}
```

### Herding & Pump/Dump Detection

```python
def detect_pump_dump(ohlcv: pd.DataFrame, market_data: dict) -> dict:
    returns  = ohlcv["adj_close"].pct_change()
    volumes  = ohlcv["volume"]

    return_streak = sum(1 for r in returns.tail(5).values if r > 0)
    vol_growth    = volumes.tail(3).mean() / (volumes.tail(20).mean() or 1)
    stock_ret_5d  = returns.tail(5).sum()
    vs_market     = stock_ret_5d - market_data.get("vnindex_return_5d", 0)

    # Price up but volume dropping = distribution phase
    pv_divergence = (ohlcv["adj_close"].tail(5).iloc[-1] /
                     ohlcv["adj_close"].tail(5).iloc[0] - 1 > 0.05 and
                     volumes.tail(3).mean() / volumes.tail(5).mean() < 0.7)

    score = 0.0; flags = []
    if return_streak >= 4 and vol_growth > 3.0: score += 0.50; flags.append("PUMP_PATTERN_DETECTED")
    if vs_market > 0.20:    score += 0.25; flags.append("ABNORMAL_OUTPERFORMANCE")
    if pv_divergence:       score += 0.25; flags.append("PRICE_VOLUME_DIVERGENCE")

    return {"pump_dump_suspect": score > 0.40, "flags": flags,
            "risk_score": round(min(score, 1.0), 3)}
```

### Seasonal Bias Risk

```python
def compute_seasonal_risk(current_date: date,
                           historical_returns: pd.DataFrame) -> dict:
    """
    VN Seasonal events:
    - Tết: Thanh khoản giảm mạnh 2 tuần trước, volume rất thấp
    - Dividend season (T3-T5): Ex-date tạo gap giảm đột ngột
    - Q4 earnings (T1-T2): Earnings surprise cao → volatility lớn
    """
    doy   = current_date.timetuple().tm_yday
    month = current_date.month

    TET_WINDOW      = list(range(330, 365)) + list(range(1, 50))
    DIVIDEND_SEASON = list(range(60, 150))

    flags = []; score = 0.0
    if doy in TET_WINDOW:      score += 0.20; flags.append("TET_LIQUIDITY_RISK")
    if doy in DIVIDEND_SEASON: flags.append("DIVIDEND_SEASON")

    if not historical_returns.empty:
        historical_returns.index = pd.to_datetime(historical_returns.index)
        month_avg = historical_returns.groupby(
            historical_returns.index.month)["return"].mean().get(month, 0)
        if month_avg < -0.02: score += 0.10; flags.append(f"HISTORICALLY_WEAK_MONTH_{month}")
    else:
        month_avg = 0

    return {"in_tet_window": doy in TET_WINDOW,
            "historical_month_return": round(month_avg, 4),
            "flags": flags, "risk_score": round(min(score, 1.0), 3)}
```

---

---

*Tài liệu này là living document — cập nhật sau mỗi phase.*
*Version 5.1 | AIInvest Blueprint | Cập nhật theo thực tế codebase*

---

## Section D.1: News Pipeline (Agentic RAG)

### Tổng quan

Pipeline tin tức được xây dựng với kiến trúc Agentic RAG, kết hợp:

- **Nguồn dữ liệu:** CafeF + FireAnt (Tin nhanh chứng khoán)
- **Sentiment Analysis:** PhoBERT (BERT-based cho tiếng Việt)
- **Vector Database:** Qdrant (lưu embeddings và metadata)
- **Orchestration:** LlamaIndex + LangGraph (agent routing)
- **Backend:** FastAPI (API layer)
- **Scheduling:** APScheduler (cập nhật định kỳ)

### Kiến trúc Flow

```
[News Source]
    ├── CafeF (RSS + scraping)
    └── FireAnt (RSS)
           │
           ▼
    [News Ingestion Agent]
           │
           ▼
    [Preprocessing & Chunking]
           │
           ├── Clean HTML
           ├── Extract metadata (date, source, ticker)
           └── Split into chunks (256-512 tokens)
           │
           ▼
    [Embedding & Vector Store]
           │
           ├── PhoBERT embeddings → Qdrant collection
           └── Metadata lưu song song (filterable fields)
           │
           ▼
    [RAG Query Agent]
           │
           ├── User query → embedding → Qdrant search
           ├── Retrieve top-k chunks (k=5)
           └── Re-rank by recency + relevance
           │
           ▼
    [Context Assembly]
           │
           ├── Format chunks + metadata → prompt template
           ├── Inject system prompt (role + constraints)
           └── Call LLM (GROQ / NVIDIA)
           │
           ▼
    [Response Generation]
           │
           ├── LLM generates answer with citations
           └── Post-process: format, filter hallucinations
```

### Components

#### 1. News Ingestion Agent

```python
# Pseudocode — Ingestion Agent
class NewsIngestionAgent:
    SOURCES = {
        "cafef":   CafeFSource(base_url="https://s.cafef.vn/..."),
        "fireant": FireAntSource(base_url="https://fireant.vn/..."),
    }

    async def fetch(self, source: str) -> list[RawArticle]:
        articles = []
        for ticker in TRACKED_TICKERS:
            feed = await self.SOURCES[source].fetch(ticker)
            for entry in feed:
                articles.append(RawArticle(
                    ticker=ticker,
                    title=entry.title,
                    body=entry.body,
                    url=entry.link,
                    published=entry.published,
                    source=source,
                ))
        return articles

    async def ingest(self) -> int:
        all_articles = []
        for source in self.SOURCES:
            articles = await self.fetch(source)
            all_articles.extend(articles)
        # Dedup by URL
        unique = {a.url: a for a in all_articles}.values()
        # Store raw articles (MongoDB / JSON)
        for art in unique:
            await self.raw_store.insert(art)
        return len(unique)
```

#### 2. Preprocessing & Chunking

```python
class NewsProcessor:
    def clean_html(self, html: str) -> str:
        import re
        clean = re.sub(r'<[^>]+>', '', html)
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def extract_tickers(self, text: str) -> list[str]:
        # Match Vietnamese stock tickers: 3 chars uppercase
        import re
        return list(set(re.findall(r'\b[A-Z]{3}\b', text)))

    def chunk(self, text: str, max_tokens=384) -> list[dict]:
        # Sentence-based chunking with overlap
        sentences = text.replace('\n', ' ').split('. ')
        chunks, current, token_count = [], [], 0
        for sent in sentences:
            t = len(sent.split())
            if token_count + t > max_tokens and current:
                chunks.append({
                    "text": ". ".join(current) + ".",
                    "token_estimate": token_count,
                })
                current = current[-2:]  # 2-sentence overlap
                token_count = sum(len(s.split()) for s in current)
            current.append(sent)
            token_count += t
        if current:
            chunks.append({
                "text": ". ".join(current) + ".",
                "token_estimate": token_count,
            })
        return chunks
```

#### 3. Sentiment Analysis (PhoBERT via Hugging Face API)

```python
import httpx

class PhoBERTAnalyzer:
    """Gọi Hugging Face Inference API — không cần load model local (1.2GB).

    Endpoint: wonrax/phobert-base-vietnamese-sentiment
    API key đã register tại Hugging Face.
    """
    def __init__(self, api_key: str, model: str = "wonrax/phobert-base-vietnamese-sentiment"):
        self.api_url = f"https://api-inference.huggingface.co/models/{model}"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.labels = ["negative", "neutral", "positive"]

    def analyze(self, text: str) -> dict:
        resp = httpx.post(self.api_url, headers=self.headers, json={"inputs": text})
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, list):
            result = result[0]
        scores = {item["label"].lower(): round(item["score"], 3) for item in result}
        pred = max(scores, key=scores.get)
        return {
            "label": pred,
            "confidence": scores[pred],
            "scores": scores,
        }

    async def analyze_batch(self, articles: list[dict]) -> list[dict]:
        async with httpx.AsyncClient() as client:
            tasks = []
            for art in articles:
                tasks.append(client.post(
                    self.api_url, headers=self.headers,
                    json={"inputs": art["text"]},
                ))
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            for art, resp in zip(articles, responses):
                if isinstance(resp, Exception):
                    art["sentiment"] = {"label": "neutral", "confidence": 0.0,
                                        "scores": {}, "error": str(resp)}
                else:
                    result = resp.json()
                    if isinstance(result, list):
                        result = result[0]
                    scores = {i["label"].lower(): round(i["score"], 3) for i in result}
                    pred = max(scores, key=scores.get)
                    art["sentiment"] = {"label": pred, "confidence": scores[pred], "scores": scores}
        return articles
```

#### 4. Vector Store (Qdrant)

```python
# Pseudocode — Qdrant integration
import qdrant_client
from qdrant_client.http.models import (VectorParams, Distance,
                                       PointStruct, Filter, FieldCondition,
                                       MatchValue, Range)

class NewsVectorStore:
    def __init__(self, embed_dim=768):
        self.client = qdrant_client.QdrantClient(host="localhost", port=6333)
        self.embed_dim = embed_dim
        self._ensure_collection("news_articles")

    def _ensure_collection(self, name):
        collections = [c.name for c in self.client.get_collections().collections]
        if name not in collections:
            self.client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self.embed_dim,
                    distance=Distance.COSINE,
                ),
            )

    def upsert(self, points: list[PointStruct]):
        self.client.upsert(
            collection_name="news_articles",
            points=points,
            wait=True,
        )

    def search(self, query_embedding: list[float],
               ticker: str = None, top_k=5,
               date_from: str = None) -> list[dict]:
        must_filters = []
        if ticker:
            must_filters.append(FieldCondition(
                key="ticker", match=MatchValue(value=ticker)))
        if date_from:
            must_filters.append(FieldCondition(
                key="published", range=Range(gte=date_from)))
        result = self.client.search(
            collection_name="news_articles",
            query_vector=query_embedding,
            query_filter=Filter(must=must_filters) if must_filters else None,
            limit=top_k,
        )
        return [{"score": r.score, "payload": r.payload} for r in result]
```

#### 5. RAG Query Pipeline

```python
class NewsRAGPipeline:
    def __init__(self, llm_client, embed_model, vector_store):
        self.llm = llm_client
        self.embed = embed_model
        self.store = vector_store

    async def query(self, question: str, ticker: str = None,
                    context_days: int = 7) -> dict:
        # 1. Embed query
        q_embed = await self.embed(question)

        # 2. Vector search
        date_from = (datetime.now() - timedelta(days=context_days)).isoformat()
        results = self.store.search(
            q_embed, ticker=ticker, date_from=date_from, top_k=5)

        if not results:
            return {"answer": "Không có tin tức liên quan trong khoảng thời gian này.",
                    "sources": []}

        # 3. Build context
        context = []
        for r in results:
            context.append(
                f"[{r['payload']['published']}] ({r['payload']['source']}) "
                f"{r['payload']['title']}\n{r['payload']['text'][:500]}"
            )
        context_str = "\n\n".join(context)

        # 4. LLM generation
        prompt = f"""Bạn là chuyên gia phân tích tin tức chứng khoán Việt Nam.
        Dựa trên các tin tức sau đây, hãy trả lời câu hỏi của người dùng.

        Yêu cầu:
        - Trích dẫn nguồn tin cụ thể (tên báo, ngày đăng)
        - Phân tích sentiment tổng quan
        - Đánh giá mức độ ảnh hưởng đến giá cổ phiếu
        - Nếu có thông tin mâu thuẫn, hãy nêu rõ

        Tin tức:
        {context_str}

        Câu hỏi: {question}
        """

        answer = await self.llm.generate(prompt)

        # 5. Extract sources for citation
        sources = [{
            "title": r["payload"]["title"],
            "url": r["payload"]["url"],
            "source": r["payload"]["source"],
            "published": r["payload"]["published"],
            "relevance": round(r["score"], 3),
        } for r in results[:3]]

        return {"answer": answer, "sources": sources}
```

#### 6. FastAPI Endpoints

```python
from fastapi import FastAPI, Query
app = FastAPI(title="AIInvest News RAG API")

rag_pipeline = NewsRAGPipeline(
    llm_client=get_llm_client(),
    embed_model=get_embed_model(),
    vector_store=NewsVectorStore(),
)

@app.get("/api/news/query")
async def query_news(
    q: str = Query(..., description="Câu hỏi về tin tức"),
    ticker: str = Query(None, description="Mã cổ phiếu (optional)"),
    days: int = Query(7, description="Số ngày context"),
):
    """Query tin tức với RAG"""
    result = await rag_pipeline.query(question=q, ticker=ticker,
                                       context_days=days)
    return result

@app.get("/api/news/recent")
async def recent_news(
    ticker: str = Query(..., description="Mã cổ phiếu"),
    limit: int = Query(10, description="Số tin gần nhất"),
):
    """Lấy tin tức gần đây cho một mã"""
    date_from = (datetime.now() - timedelta(days=7)).isoformat()
    results = rag_pipeline.store.search(
        query_embedding=[0]*768,  # dummy — fetch by filter only
        ticker=ticker, date_from=date_from, top_k=limit,
    )
    return {"articles": results}

@app.post("/api/news/ingest")
async def trigger_ingest():
    """Kích hoạt ingestion thủ công"""
    agent = NewsIngestionAgent()
    count = await agent.ingest()
    return {"ingested": count, "status": "ok"}
```

#### 7. Scheduling (APScheduler)

```python
# Main scheduler setup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def scheduled_ingest():
    agent = NewsIngestionAgent()
    count = await agent.ingest()
    print(f"[{datetime.now()}] Ingested {count} articles")

async def scheduled_cleanup():
    """Xóa articles cũ hơn 90 ngày"""
    cutoff = datetime.now() - timedelta(days=90)
    # Cleanup Qdrant + raw store
    print(f"[{datetime.now()}] Cleanup completed")

# Schedule
scheduler.add_job(scheduled_ingest, "interval", hours=2,
                  id="news_ingest", max_instances=1)
scheduler.add_job(scheduled_cleanup, "cron", day_of_week=0,
                  hour=3, id="news_cleanup")
```

### LangGraph Agent Flow

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional

class NewsState(TypedDict):
    query: str
    ticker: Optional[str]
    context_days: int
    retrieved_chunks: list
    answer: str
    sources: list

async def retrieve_node(state: NewsState) -> dict:
    """Retrieve relevant news chunks"""
    chunks = await rag_pipeline.store.search(
        query_embedding=await embed_model(state["query"]),
        ticker=state["ticker"],
        date_from=(datetime.now() - timedelta(days=state["context_days"])).isoformat(),
        top_k=5,
    )
    return {"retrieved_chunks": chunks}

async def grade_node(state: NewsState) -> dict:
    """Grade relevance of retrieved chunks — filter out low-quality"""
    filtered = []
    for chunk in state["retrieved_chunks"]:
        grade = await llm_judge.grade_relevance(
            query=state["query"],
            chunk_text=chunk["payload"]["text"],
        )
        if grade["relevant"]:
            filtered.append(chunk)
    return {"retrieved_chunks": filtered}

async def generate_node(state: NewsState) -> dict:
    """Generate final answer from graded chunks"""
    context = format_chunks(state["retrieved_chunks"])
    answer = await rag_pipeline.llm.generate(
        build_prompt(state["query"], context))
    return {"answer": answer, "sources": state["retrieved_chunks"][:3]}

def should_continue(state: NewsState) -> str:
    """If no relevant chunks found, skip generation"""
    return "generate" if state["retrieved_chunks"] else "end"

# Build graph
workflow = StateGraph(NewsState)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("grade", grade_node)
workflow.add_node("generate", generate_node)

workflow.set_entry_point("retrieve")
workflow.add_conditional_edges(
    "retrieve",
    should_continue,
    {"generate": "grade", "end": END},
)
workflow.add_edge("grade", "generate")
workflow.add_edge("generate", END)

news_agent = workflow.compile()
```

### Lưu ý triển khai

1. **Qdrant** chạy local (Docker) hoặc cloud (Qdrant Cloud free tier)
2. **PhoBERT** dùng Hugging Face Inference API (model `wonrax/phobert-base-vietnamese-sentiment`), không cần GPU local. Đã register API key.
3. **Deduplication** qua URL hash + content similarity (SimHash)
4. **Rate limiting** cho CafeF/FireAnt fetch (tránh bị block)
5. **Cache** kết quả query thường gặp (Redis TTL 5 phút)
6. **Fallback** khi LLM không available → trả về chunks raw + sentiment scores
7. **Monitoring** với Prometheus metrics (số articles ingested, query latency, error rate)

---

## Tóm tắt sự khác biệt Roadmap vs Thực tế

| Mục | Roadmap cũ | Thực tế hiện tại |
|-----|-----------|-----------------|
| adj_close source | `vnstock Company.events()` | `corporate_actions` table (yfinance initial) |
| Risk flags | CafeF httpx scraper | `risk_flags_v2.py`: 10 computed flags từ structured DB |
| Lending rates | SBV + CafeF + hardcode | yfinance + VietFin/DNSE + vi.money + SBV + Vimo |
| Alpha factors | 30 VN-core factors (3 tiers) | 7 alpha IDs + factor_zoo 450+ factors |
| AgentCore | `agent_core.py` mới | `loop.py` (~897 dòng) đã có, không có unified AgentCore |
| LLM Router | Gemini + Groq | GROQ0/1 + NVIDIA (chưa có Gemini) |
| Gemini client | `gemini_client.py` | ❌ Chưa implement |
| VN-specific skills | 3 skills cần tạo | ❌ Chưa có (57 global skills có sẵn) |
| Swarm VN preset | `vn_equity_desk.yaml` | ❌ Chưa có (27 presets khác) |
| SignalTracker | `signal_tracker.py` | ❌ Chưa implement |
| LLMJudge | `llm_judge.py` | ❌ Chưa implement |
| CRS 7 tầng | 7-layer composite scorer | 10 computed flags (risk_flags_v2) — simpler, faster |
| DB tables | 9 tables cần tạo | 22 tables đã tồn tại, thiếu risk_metrics |
| Phase 0 | Tuần 1-2 | ✅ ~90% done |
| Phase 1 | Tuần 3-6 | ✅ ~80% done |
| Phase 2 | Tuần 7-9 | ⚠️ ~30% done |
| Phase 3 | Tuần 10-13 | ✅ ~60% done (backtest + hypothesis) |
| Phase 4 | Tháng 4-6 | ❌ Chưa bắt đầu |
| Phase 5 | Tháng 7+ | ❌ Chưa bắt đầu |