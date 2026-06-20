# TASK-101: Data Quality Check Engine

**Module:** `app/core/quality/data_quality.py`
**Tests:** `tests/unit/test_data_quality.py`
**IOS v5.1 Reference:** DRY_RUN_CHECKLIST.md Phase 0, DATA_SCHEMA.md

---

## Overview

The Data Quality Check Engine is the first module to run each day before any
pipeline begins. It validates the integrity, completeness, and freshness of
all input data sources.

If any **CRITICAL** check fails, the entire pipeline is halted.

---

## Check List

| ID | Name | Severity | Description |
|:---|:---|:---|:---|
| CHECK-01 | OHLCV completeness | CRITICAL | All tickers have data for today |
| CHECK-02 | Price limit validation | CRITICAL | `\|close/prev_close - 1\| <= 7%` (HOSE rule) |
| CHECK-03 | Volume non-negative | CRITICAL | No negative volume fields |
| CHECK-04 | Volume separation integrity | WARNING | `continuous + atc + ato ≈ total` |
| CHECK-05 | Financial statement freshness | WARNING | `announcement_date` within 90 days |
| CHECK-06 | Corporate actions applied | CRITICAL | All corp actions have `applied = true` |
| CHECK-07 | Announcement date completeness | CRITICAL | `< 5%` null `announcement_date` |
| CHECK-08 | Point-in-time integrity | CRITICAL | No future-dated data |

---

## API

### `run_all_checks(...)` → `DataQualityReport`

```python
from app.core.quality import run_all_checks

report = run_all_checks(
    tickers=["VHM", "FPT", "MSN"],
    market_data=[...],      # list[dict] theo MarketDataDaily schema
    financials=[...],        # list[dict] theo FinancialStatement schema
    corp_actions=[...],      # list[dict] theo CorporateAction schema
    target_date=date(2026, 6, 15),
)

if report.overall == "FAIL":
    # Inspect individual failures
    for check in report.checks:
        if not check.passed:
            print(f"{check.check_id}: {check.reason}")
```

### `DataQualityReport` properties

| Property | Returns |
|:---|:---|
| `overall` | `"PASS"`, `"FAIL"`, or `"SKIP"` |
| `summary` | `dict` with counts of passed/failed/skipped |
| `checks` | `list[DataQualityCheck]` |
| `log_summary()` | Logs results via `logging` |

### `DataQualityCheck` fields

| Field | Type | Description |
|:---|:---|:---|
| `check_id` | `str` | e.g. `"CHECK-01"` |
| `name` | `str` | Human-readable name |
| `severity` | `CheckSeverity` | `CRITICAL`, `WARNING`, or `INFO` |
| `status` | `CheckStatus` | `PASS`, `FAIL`, or `SKIP` |
| `reason` | `str` | Failure reason (empty on PASS) |
| `details` | `dict` | Optional structured details |
| `passed` | `bool` | Shorthand for `status == PASS` |

---

## Adding a New Check

1. Write a function with signature:
   ```python
   def check_my_rule(data: ...) -> DataQualityCheck:
   ```
2. Add it to the `checks` list inside `run_all_checks()`.
3. Add corresponding tests in `tests/unit/test_data_quality.py`.
4. Update this document.

---

## Dependencies & TODO

- **TOD-101-01:** The module currently accepts data as `list[dict]` for
  testability. When the ORM models for DATA_SCHEMA entities are created,
  `run_all_checks` should support querying directly from the database.
- **TOD-101-02:** Add a configuration class to allow per-check threshold
  overrides (e.g., max_age_days, max_change_pct).
- **TOD-101-03:** Currently no separate `prev_close_adj` field is required
  in the input dicts; the check uses it if present. Document the expected
  schema contract once MarketDataDaily ORM model is finalized.

---

## Test Coverage

42 unit tests covering:
- `DataQualityReport` core logic (5 tests)
- CHECK-01 through CHECK-08 individual checks (32 tests)
- `run_all_checks` integration (5 tests)

Run:
```bash
cd ai-engine
python -m pytest tests/unit/test_data_quality.py -v
```
