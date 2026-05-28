---
name: vietfin
description: VietFin Vietnam stock market data interface — retrieve OHLCV, fundamentals, dividends, financial statements, and company profiles for all VN stocks, ETFs, indices, and mutual funds. Free, no API key required.
category: data-source
---

# VietFin

## Overview

VietFin is an open-source Python wrapper that scrapes publicly available APIs from Vietnamese brokerage firms (TCBS, SSI, DNSE, VND, etc.), providing comprehensive Vietnam market data. **Completely free, no registration or API key required.**

The project has a built-in VietFin DataLoader (`backtest/loaders/vietfin_loader.py`). When backtesting, set `source: "vietfin"` or `source: "auto"` to invoke it automatically.

## Quick Start

```bash
pip install vietfin pandas
```

```python
from vietfin import vf

# VNM (Vinamilk) daily bars
df = vf.equity.price.historical(symbol="vnm", start_date="2025-01-01", end_date="2026-01-01")
print(df.to_df().head())
```

## Symbol Format

VietFin uses lowercase VN tickers (e.g. `"vnm"`, `"vcb"`, `"fpt"`). The DataLoader handles conversion automatically.

| Ticker | Company | Exchange |
|--------|---------|----------|
| VCB | Vietcombank | HOSE |
| VIC | Vinhomes | HOSE |
| VNM | Vinamilk | HOSE |
| FPT | FPT Corporation | HOSE |
| HPG | Hoa Phat Group | HOSE |
| MSN | Masan Group | HOSE |
| MWG | Mobile World Group | HOSE |
| STB | Sacombank | HOSE |
| ACB | Asia Commercial Bank | HOSE |
| SSI | SSI Securities | HOSE |

## Supported Data Types

### 1. Historical OHLCV

```python
from vietfin import vf

# Single stock — daily
df = vf.equity.price.historical(symbol="vnm", start_date="2025-01-01", end_date="2026-01-01")
print(df.to_df().tail())

# Custom interval (1d, 1w, 1mo)
df = vf.equity.price.historical(symbol="fpt", start_date="2025-01-01", end_date="2026-01-01", interval="1w")
```

**Supported intervals:** `1d`, `1w`, `1mo`

**Default:** `start_date` = 60 days ago, `end_date` = today, `interval` = `1d`

### 2. Company Profile

```python
from vietfin import vf

profile = vf.equity.profile(symbol="vnm")
data = profile.to_dict()
# => symbol, name, legal_name, stock_exchange, industry, employees, website, etc.
```

### 3. Fundamental Data

```python
from vietfin import vf

# Key financial ratios (PE, PB, ROE, ROA, EPS, etc.)
ratios = vf.equity.fundamental.ratios(symbol="vnm")
print(ratios.to_df())

# Income statement
income = vf.equity.fundamental.income(symbol="vnm")
print(income.to_df())

# Balance sheet
balance = vf.equity.fundamental.balance(symbol="vnm")

# Cash flow statement
cashflow = vf.equity.fundamental.cash(symbol="vnm")

# Profitability over time (quarterly)
# Each returns a DataFrame with historical data
```

### 4. Dividends

```python
from vietfin import vf

dividends = vf.equity.fundamental.dividends(symbol="vnm")
print(dividends.to_df())
# => ex_date, cash_dividend, payment_date, etc.
```

### 5. Management & Ownership

```python
from vietfin import vf

# Key executives
mgmt = vf.equity.fundamental.management(symbol="vnm")
print(mgmt.to_df())

# Major shareholders
holders = vf.equity.ownership.holders(symbol="vnm")

# Insider trading
insider = vf.equity.ownership.insider(symbol="vnm")

# Foreign ownership
foreign = vf.equity.ownership.foreign(symbol="vnm")
```

### 6. Corporate Events

```python
from vietfin import vf

events = vf.equity.calendar.events(symbol="vnm")
print(events.to_df())
# => event_type, ex_date, announcement_date, etc.
```

### 7. Index Constituents

```python
from vietfin import vf

# VN30 constituents
vn30 = vf.index.constituents(symbol="vn30")
print(vn30.to_df())

# VN-Index historical
vnindex = vf.index.price.historical(symbol="vnindex")
```

### 8. ETFs & Mutual Funds

```python
from vietfin import vf

# Search for ETFs
etfs = vf.etf.search()
print(etfs.to_df())

# ETF historical price
etf = vf.etf.historical(symbol="e1vfvn30")

# Search for mutual funds
funds = vf.funds.search()

# Fund NAV history
fund = vf.funds.historical(symbol="vesaf")
```

### 9. Market Discovery

```python
from vietfin import vf

# Most active stocks
active = vf.equity.discovery.active()
print(active.to_df())

# Top gainers
gainers = vf.equity.discovery.gainers()

# Top losers
losers = vf.equity.discovery.losers()

# Real-time quote
quote = vf.equity.price.quote(symbol="vnm")
```

### 10. Derivatives

```python
from vietfin import vf

# List available futures contracts
futures = vf.derivatives.futures.search()
print(futures.to_df())

# Futures quote
fq = vf.derivatives.futures.quote(symbol="vnf30f2406")

# Futures historical
fh = vf.derivatives.futures.historical(symbol="vnf30f2406")
```

### 11. News

```python
from vietfin import vf

news = vf.news.company(symbol="vnm")
print(news.to_df())
```

## Data Sources / Providers

VietFin provides a `provider` parameter on most methods:

| Provider | Coverage | Notes |
|----------|----------|-------|
| `tcbs` | TCBS — equities, ETFs, indices | Default, best coverage |
| `ssi` | SSI — equities | Second source for verification |
| `dnse` | DNSE — equities | Third source |
| `vnd` | VNDirect — equities | Fundamental data |
| `fmarket` | FMARKET — mutual funds | Fund NAV data |

```python
# Compare volumes from different providers
tcbs = vf.equity.price.historical(symbol="fpt", provider="tcbs").to_df()
ssi  = vf.equity.price.historical(symbol="fpt", provider="ssi").to_df()
dnse = vf.equity.price.historical(symbol="fpt", provider="dnse").to_df()
```

## Response Format

Every VietFin command returns a `VfObject` with:

| Attribute | Type | Description |
|-----------|------|-------------|
| `results` | `list[Data]` | Parsed, standardized results |
| `provider` | `str` | Provider name used |
| `extra` | `dict` | Metadata (run time, records count, API URL) |
| `raw_data` | `Any` | Raw API response |

**Helper methods:**
- `to_df()` → `pandas.DataFrame`
- `to_dict()` → `dict`
- `to_numpy()` → `numpy.ndarray`
- `to_csv()` → saves to CSV file
- `to_polars()` → `polars.DataFrame`

## Backtest Usage

### config.json Example

```json
{
  "source": "vietfin",
  "codes": ["VCB", "VNM", "FPT"],
  "start_date": "2020-01-01",
  "end_date": "2026-05-25",
  "initial_cash": 100000000,
  "commission": 0.0027,
  "extra_fields": null
}
```

### Auto Mode (VN stocks routed to vietfin)

```json
{
  "source": "auto",
  "codes": ["VCB", "FPT", "VNM", "000001.SZ", "AAPL.US"],
  "start_date": "2024-01-01",
  "end_date": "2026-05-25",
  "initial_cash": 100000000,
  "commission": 0.001
}
```

`source: "auto"` routes VN tickers → DNSE → VietFin fallback, A-shares → tushare, US/HK → yfinance.

## Notes

- **Free, no API key**: VietFin scrapes public brokerage APIs — no registration needed
- **Rate limits**: be respectful — cache results when possible instead of re-fetching
- **Uppercase tickers**: the DataLoader converts to lowercase automatically; when calling directly use `vf.equity.price.historical(symbol="vnm")`
- **Provider variation**: volume data can differ across providers (TCBS vs SSI vs DNSE) due to different data aggregation methods
- **Date range**: if not provided, default is last 60 trading days
- **Adjustment**: VietFin returns adjusted prices by default
- **Stale data**: some endpoints may be delayed by 15-20 minutes vs real-time
- **extra_fields not supported**: OHLCV fetch returns only open/high/low/close/volume; fundamentals require separate `profile()`, `ratios()`, etc. calls
