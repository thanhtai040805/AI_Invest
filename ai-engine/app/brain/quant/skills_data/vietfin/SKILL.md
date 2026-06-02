---
name: vietfin
description: VietFin + vnstock Vietnam stock market data interface — OHLCV (VietFin DNSE), fundamentals (vnstock), mutual funds (VietFin Fmarket). Covers all VN stocks, ETFs, indices, and funds.
category: data-source
---

# VietFin + vnstock

## Overview

VietFin is used for **OHLCV price data** (DNSE provider) and **mutual funds** (Fmarket provider) — these endpoints still work.
vnstock replaces VietFin for **fundamental data** (profile, ratios, income, cash flow, balance sheet) since VietFin's TCBS/SSI APIs are permanently dead.

The project has a built-in DataLoader (`backtest/loaders/vietfin_loader.py`). When backtesting, set `source: "vietfin"` or `source: "auto"` to invoke it automatically.

## Quick Start

```bash
pip install vietfin vnstock pandas
```

```python
from vietfin import vf
from vnstock import Vnstock
from vnstock.api.financial import Finance

# VNM (Vinamilk) daily bars — VietFin DNSE (works)
df = vf.equity.price.historical(symbol="vnm", start_date="2025-01-01", end_date="2026-01-01", provider="dnse")
print(df.to_df().head())

# Company profile — vnstock (replaces dead TCBS API)
stock = Vnstock().stock(symbol="VNM", source="KBS")
profile = stock.company.overview().iloc[0].to_dict()
print(profile["organ_name"], profile["exchange"])

# Fundamental ratios — vnstock (replaces dead TCBS API)
ratios = Finance(symbol="VNM", source="KBS").ratio()
```

## Symbol Format

VietFin uses lowercase VN tickers (e.g. `"vnm"`, `"vcb"`). vnstock uses uppercase tickers. The DataLoader handles conversion automatically.

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

### 1. Historical OHLCV (VietFin DNSE ✅)

```python
from vietfin import vf

df = vf.equity.price.historical(symbol="vnm", start_date="2025-01-01", end_date="2026-01-01", provider="dnse")
print(df.to_df().tail())

# Custom interval (1d, 1w, 1mo)
df = vf.equity.price.historical(symbol="fpt", start_date="2025-01-01", end_date="2026-01-01", interval="1w", provider="dnse")
```

**Supported intervals:** `1d`, `1w`, `1mo`

### 2. Company Profile (vnstock ✅)

```python
from vnstock import Vnstock

stock = Vnstock().stock(symbol="VNM", source="KBS")
profile = stock.company.overview().iloc[0].to_dict()
# => organ_name, exchange, company_type, number_of_employees, website, sector, market_cap, etc.
```

Available fields: `organ_name`, `exchange`, `sector`, `company_type`, `number_of_employees`, `website`, `market_cap`, `free_float`, `foreigner_percentage`, `is_bank`, `listing_date`, `company_profile` (description).

### 3. Fundamental Data (vnstock ✅)

```python
from vnstock.api.financial import Finance

f = Finance(symbol="VNM", source="KBS")

# Key financial ratios (P/E, P/B, ROE, ROA, EPS, beta, dividend yield, etc.)
ratios = f.ratio()
# DataFrame with Vietnamese item names; map: P/E, P/B, EPS, Beta, ROE, ROA, BVPS, Tỷ suất cổ tức

# Income statement (26 items × quarters)
income = f.income_statement()

# Balance sheet (86 items × quarters)
balance = f.balance_sheet()

# Cash flow statement (52 items × quarters)
cashflow = f.cash_flow()
```

### 4. Dividends — Not Available ❌

No replacement API. Both VietFin (TCBS 404) and vnstock do not provide dividend data.

### 5. Management & Ownership (vnstock ✅ partial)

```python
from vnstock.api.company import Company

c = Company(symbol="VNM", source="VCI")

# Key executives (10 people)
officers = c.officers()

# Major shareholders (40 largest)
shareholders = c.shareholders()

# Subsidiaries
subs = c.subsidiaries()

# Affiliated companies
aff = c.affiliate()

# Corporate events (50 items)
events = c.events()
```

### 6. Index Constituents (vnstock Listing ✅)

```python
from vnstock.api.listing import Listing

l = Listing()

# VN30 constituents
vn30 = l.symbols_by_group(group="VN30")

# All stocks by exchange
all_hose = l.symbols_by_exchange()
# => symbol, organ_name, exchange, type

# ETFs list
etfs = l.all_etf()

# Futures list
futures = l.all_future_indices()
```

### 7. ETFs & Mutual Funds

```python
from vnstock.api.listing import Listing
from vnstock.api.quote import Quote
from vietfin import vf

# ETF symbols list (vnstock)
l = Listing()
etf_symbols = l.all_etf()  # Series: E1VFVN30, FUEVFVND, FUESSV50, etc.

# ETF historical price (vnstock)
etf = Quote(symbol="E1VFVN30", source="KBS").history(start="2025-01-01", end="2026-01-01")

# Mutual fund search (VietFin Fmarket ✅)
funds = vf.funds.search()
print(funds.to_df())

# Fund NAV history (VietFin Fmarket ✅)
nav = vf.funds.historical(symbol="vesaf")
```

### 8. Corporate Events (vnstock Company ✅)

```python
from vnstock.api.company import Company

c = Company(symbol="VNM", source="VCI")
events = c.events()  # 50 latest events
news = c.news()      # 50 latest news
```

### 9. Full Universe (vnstock Listing ✅)

```python
from vnstock.api.listing import Listing

l = Listing()
all_symbols = l.all_symbols()                  # 1532 symbols
by_exchange = l.symbols_by_exchange()          # 3236 instruments
vn30 = l.symbols_by_group(group="VN30")        # VN30 index members
industries = l.industries_icb()                # ICB industry classification
```

### 10. Derivatives (vnstock Listing ✅)

```python
from vnstock.api.listing import Listing

l = Listing()
futures = l.all_future_indices()
# => 41I1G6000, 41I1G7000, ...
```

## Dead VietFin APIs (do NOT use)

These VietFin API endpoints are permanently dead — use vnstock equivalents above:

| API | Error | Replacement |
|-----|-------|-------------|
| `vf.equity.profile(sym)` | TCBS 404 | `Vnstock().stock().company.overview()` |
| `vf.equity.fundamental.ratios(sym)` | TCBS 404 | `Finance(sym, "KBS").ratio()` |
| `vf.equity.fundamental.income(sym)` | TCBS 404 | `Finance(...).income_statement()` |
| `vf.equity.fundamental.balance(sym)` | TCBS 404 | `Finance(...).balance_sheet()` |
| `vf.equity.fundamental.cash(sym)` | TCBS 404 | `Finance(...).cash_flow()` |
| `vf.equity.fundamental.dividends(sym)` | TCBS 404 | No replacement |
| `vf.equity.fundamental.management(sym)` | TCBS 404 | `Company(...).officers()` |
| `vf.equity.search()` | SSI 403 | `Listing().symbols_by_exchange()` |
| `vf.equity.price.quote(sym)` | TCBS 404 | `Quote(sym).history(count_back=1)` |
| `vf.index.constituents(sym)` | FireAnt 404 | `Listing().symbols_by_group("VN30")` |
| `vf.equity.discovery.*` | FireAnt 404 | No replacement |
| `vf.news.company(sym)` | TCBS 404 | `Company(...).news()` |
| `vf.derivatives.futures.search()` | FireAnt 404 | `Listing().all_future_indices()` |

## Data Sources

| Provider | Status | Used For |
|----------|--------|----------|
| VietFin DNSE | ✅ Working | OHLCV price data |
| VietFin Fmarket | ✅ Working | Mutual funds search + NAV |
| vnstock KBS | ✅ Working | Company profile, fundamentals |
| vnstock VCI | ✅ Working | Financial statements, cash flow |
| VietFin TCBS | ❌ Dead (404) | Replaced by vnstock |
| VietFin SSI | ❌ Dead (403) | Replaced by vnstock |
| VietFin FireAnt | ❌ Dead (404) | Replaced by vnstock |

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

`source: "auto"` routes VN tickers → DNSE → VietFin fallback.

## Notes

- **Free, no API key**: VietFin scrapes public brokerage APIs — no registration needed
- **Rate limits**: be respectful — cache results when possible instead of re-fetching
- **Uppercase tickers**: the DataLoader converts to lowercase automatically; when calling directly use `vf.equity.price.historical(symbol="vnm")`
- **Provider variation**: volume data can differ across providers (TCBS vs SSI vs DNSE) due to different data aggregation methods
- **Date range**: if not provided, default is last 60 trading days
- **Adjustment**: VietFin returns adjusted prices by default
- **Stale data**: some endpoints may be delayed by 15-20 minutes vs real-time
- **extra_fields not supported**: OHLCV fetch returns only open/high/low/close/volume; fundamentals require separate `profile()`, `ratios()`, etc. calls
