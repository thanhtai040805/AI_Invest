---
name: fundamental-filter
description: Fundamental factor screening — filter stocks by PE/PB/ROE, financial statement fields, and other metrics for value or growth selection. Supports VN stocks (via vnstock PF reports and market cap data).
category: flow
---
# Fundamental Factor Screening

## Purpose

Filter stocks using fundamental financial data (PE/PB/ROE, etc.) to build value or growth screen signals for backtesting. Supports multiple markets with different data sources.

## Market Support

| Market | Data Source | Method | Supported Metrics |
|--------|-----------|--------|------------------|
| VN stocks | vnstock PF (financial reports) | `fundamental_fields` in config.json | Income statement, balance sheet, cash flow, financial indicators |
| VN stocks | DNSE market data | `extra_fields` in config.json | pe, pb, pe_ttm, ps_ttm, total_mv, roe |

## Signal Logic

### Value Filter (Default)

1. PE < pe_max AND PE > 0 (exclude loss-making stocks)
2. PB < pb_max
3. ROE > roe_min
4. All conditions met → long (1), otherwise → flat (0)

### Growth Filter (Optional)

1. PE_TTM within reasonable range (0 < PE_TTM < pe_ttm_max)
2. ROE > roe_min (profitability floor)
3. Market cap > mv_min (exclude micro-caps)

## VN Stock Usage (DNSE / vnstock)

### config.json (extra_fields via DNSE)

```json
{
  "source": "vietfin",
  "codes": ["VIC", "VHM", "FPT"],
  "start_date": "2023-01-01",
  "end_date": "2024-12-31",
  "extra_fields": ["pe", "pb", "pe_ttm", "roe", "total_mv"],
  "initial_cash": 1000000,
  "commission": 0.001
}
```

The `extra_fields` columns are automatically merged into the daily DataFrame by the DataLoader (fetched from DNSE market data).

### VN Stock Statement Pre-Filter (via vnstock PF)

Use `fundamental_fields` when the strategy needs point-in-time financial statement data:

```json
{
  "source": "vietfin",
  "codes": ["VIC", "VHM", "FPT"],
  "start_date": "2023-01-01",
  "end_date": "2024-12-31",
  "fundamental_fields": {
    "income": ["total_revenue", "n_income"],
    "balancesheet": ["total_equity"],
    "fina_indicator": ["roe", "debt_to_assets"]
  },
  "initial_cash": 1000000,
  "commission": 0.001
}
```

The backtest runner queries VN financial reports through the vnstock PF provider and merges each published statement snapshot into daily bars only after its announcement date. Statement columns are prefixed by table name:

| Requested field | SignalEngine column |
|-----------------|---------------------|
| `income.total_revenue` | `income_total_revenue` |
| `income.n_income` | `income_n_income` |
| `balancesheet.total_equity` | `balancesheet_total_equity` |
| `fina_indicator.roe` | `fina_indicator_roe` |

Representative financial-quality pre-filter:

```python
revenue = row.get("income_total_revenue")
profit = row.get("income_n_income")
net_assets = row.get("balancesheet_total_equity")
roe = row.get("fina_indicator_roe")

passes = (
    revenue is not None and revenue > 0
    and profit is not None and profit > 0
    and net_assets is not None and net_assets > 0
    and roe is not None and roe >= 8.0
)
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| pe_max | 20.0 | PE ceiling (exclude overvalued) |
| pb_max | 3.0 | PB ceiling |
| roe_min | 8.0 | ROE floor (%), exclude low-profitability |
| pe_min | 0.0 | PE floor (exclude loss-making stocks) |
| mcap_min | 0 | Market cap floor (VND) |

## Common Pitfalls

- `extra_fields` columns may contain NaN (new listings, suspended stocks) — must `fillna` or `dropna`
- `fundamental_fields` columns are prefixed by table and may be NaN before the first statement is published in the backtest window
- Do not forward-fill statement rows manually before their announcement date; the runner's merge already enforces point-in-time visibility
- Negative PE means loss-making — always filter with `pe > 0`
- ROE from DNSE/extra_fields is in percentage (e.g., 15 = 15%), from financial statements may be decimal (e.g., 0.15 = 15%)
- For portfolio strategies: N stocks passing the screen each get weight 1/N

## Dependencies

```bash
pip install pandas numpy yfinance
```

## Signal Convention

- `1/N` = selected for long (N = number of stocks passing the screen), `0` = not selected
