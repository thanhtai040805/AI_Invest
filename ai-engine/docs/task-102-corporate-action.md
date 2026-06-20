# TASK-102: Corporate Action Adjustment Engine

**Module:** `app/core/quality/corporate_action.py`
**Tests:** `tests/unit/test_corporate_action.py`
**Migration:** `scripts/migration_102_ca_adjustment.py`
**IOS v5.1 Reference:** DATA_SCHEMA.md Entity 3 (CorporateAction), Entity 2 (MarketDataDaily)

---

## Overview

Adjusts backward historical prices when a corporate action occurs.
Supports 5 action types: SPLIT, MERGE, DIVIDEND_CASH, DIVIDEND_STOCK, RIGHTS.

---

## DB Migration

Run once:
```bash
cd ai-engine
python scripts/migration_102_ca_adjustment.py
```

Creates:
- `corporate_actions.applied` (BOOLEAN) — tracks if adjustment was applied
- `corporate_actions.adjustment_factor` (DOUBLE PRECISION) — stores computed factor
- `market_data_daily` table — per DATA_SCHEMA.md Entity 2

---

## Adjustment Logic

| Action Type | Adjustment Factor | Example |
|:---|---:|:---|
| SPLIT n:1 | `1/n` | 2:1 → ×0.5 |
| MERGE n:1 | `n` | 2:1 → ×2.0 |
| DIVIDEND_STOCK n:m | `m/(m+n)` | 10% → ×0.909 |
| DIVIDEND_CASH | `(price - cash) / price` | needs close price |
| RIGHTS n:m | `m/(m+n)` | similar to dividend stock |

All factors are applied **backward** (prices before ex-date are multiplied by factor).

---

## API

### In-memory adjustment

```python
from app.core.quality.corporate_action import (
    CorporateActionRecord, MarketDataRow, ActionType,
    adjust_prices_historical, apply_all_pending_adjustments,
)

# Single adjustment
rows = [MarketDataRow("VHM", date(2026, 5, 25), close_adj=50.0)]
ca = CorporateActionRecord("VHM", ActionType.SPLIT, date(2026, 6, 1), ratio=2.0)
adjusted, factor = adjust_prices_historical(rows, ca)
```

### DB-backed adjustment

```python
from app.core.quality.corporate_action import CorporateActionAdjuster

engine = CorporateActionAdjuster(
    db_url="postgresql://postgres:123@localhost:5432/aiinvest"
)
report = engine.apply_pending()
print(f"Applied: {report.succeeded}, Failed: {report.failed}")
```

---

## Test Coverage

22 unit tests covering:
- `compute_adjustment_factor` — all 5 action types + edge cases (8 tests)
- `_compute_split_factor` — basic math (3 tests)
- `adjust_prices_historical` — split halves prices, cash dividend, all fields,
  factor accumulation, skip when applied (5 tests)
- `apply_all_pending_adjustments` — single/multiple CAs, missing ticker,
  multiple tickers (4 tests)
- `AdjustmentReport` — empty, mixed (2 tests)

Run:
```bash
cd ai-engine
python -m pytest tests/unit/test_corporate_action.py -v
```

---

## TODO

- **TOD-102-01:** Refine DIVIDEND_CASH factor — currently returns 1.0 until
  close price on ex-date is provided. Need to integrate with market data
  lookup at adjustment time.
- **TOD-102-02:** `CorporateActionAdjuster` creates a new DB connection per
  call. Should use connection pool when ORM integration is complete.
- **TOD-102-03:** Add support for adjusting EPS and other derived metrics
  (not just prices) when corporate action occurs.
- **TOD-102-04:** Add `ohlcv.adj_close` sync — when `market_data_daily` is
  updated, the `ohlcv` table (Prisma-managed) should also be updated.
