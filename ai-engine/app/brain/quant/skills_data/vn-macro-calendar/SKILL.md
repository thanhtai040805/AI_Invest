---
name: vn-macro-calendar
description: VN macroeconomic calendar — SBV rate decisions, CPI releases, GDP data, Tết schedule, credit growth targets, and PMI releases. Covers 5-source macro pipeline.
category: analysis
---

# VN Macro Calendar

## Economic Data Releases

### Regular Schedule

| Indicator | Frequency | Source | Lag | Typical Date |
|-----------|-----------|--------|-----|-------------|
| CPI | Monthly | GSO (vi.money) | 1 month | ~20th of month |
| GDP | Quarterly | GSO | 2–3 months | Late Mar/Jun/Sep/Dec |
| PMI Manufacturing | Monthly | S&P Global | 1–2 days | 1st business day |
| PMI Services | Monthly | S&P Global | 1–2 days | 3rd business day |
| Industrial Production (IIP) | Monthly | GSO | 1 month | ~20th of month |
| Retail Sales | Monthly | GSO | 1 month | ~20th of month |
| Trade Balance | Monthly | Customs | 10 days | ~15th of month |
| Credit Growth | Monthly | SBV | 15 days | ~20th of month |
| FDI | Monthly | MPI | 1 month | ~25th of month |
| VNINDEX rebalance | Semi-annual | HOSE | — | June, December |
| VN30 rebalance | Quarterly | HOSE | — | Mar, Jun, Sep, Dec |

### SBV Policy Rate Decisions
- **Frequency**: Ad-hoc (no fixed schedule).
- Current rate: check `macro_indicators` table with `indicator_name = 'SBV_REFI_RATE'`.
- Typically changes 1–2 times per year.
- Key rates: Refinancing rate, Discount rate, OMO rate.

### Data Sources Pipeline
The 5-source macro pipeline runs daily (step 11 of ETL):

| Source | Data | Update |
|--------|------|--------|
| yfinance | Global commodities (oil, steel, rice), USD/VND, VIX, US yields, DXY | Daily |
| vi.money | Vietnam CPI, IIP, retail sales, trade balance | Monthly |
| VietFin | GDP, FDI, credit growth, PMI | As released |
| SBV | Policy rates, interbank rates, OMO | Daily |
| CafeF | Market news, foreign flow, sector flows | Daily |

## VN Market Holidays

### Fixed Holidays
| Date | Holiday |
|------|---------|
| Jan 1 | New Year |
| Apr 30 | Reunification Day |
| May 1 | Labour Day |
| Sep 2 | National Day |

### Lunar Calendar Holidays (Tết — variable)
| Holiday | Typical Date | Market Impact |
|---------|-------------|---------------|
| Tết Nguyên Đán | Late Jan–Feb | 5–7 day closure |
| Tết Nguyên Tiêu | Feb–Mar | No closure |
| Giỗ Tổ Hùng Vương | April 10 | 1 day closure |
| Tết Đoan Ngọ | June | No closure |
| Trung Thu | Sep–Oct | No closure |

Tết closure is typically 5–7 trading days. The market shows a consistent pattern:
- 2–3 weeks **before Tết**: bullish (institutional window dressing, personal bonuses)
- 1 week **before closure**: cautious (margin reduction)
- 1–2 weeks **after Tết**: mixed (retail returning, new year positioning)
- **Tết hypothesis**: long 3 weeks before, exit before closure.

## Macro Data Queries

```python
# Get latest macro indicators
import psycopg2
conn = psycopg2.connect("postgresql://postgres:123@localhost:5432/aiinvest")
cur = conn.cursor()
cur.execute("""
    SELECT indicator_date, indicator_name, value, unit
    FROM macro_indicators
    ORDER BY indicator_date DESC
    LIMIT 20
""")

# Check if today is a trading day
from app.services.daily_etl import is_trading_day
is_open = is_trading_day()  # Returns True/False
```

## Key Macro Relationships for VN Equities

| Macro Signal | Equity Impact | Typical Lead |
|-------------|---------------|--------------|
| SBV rate cut | +Real Estate, +Banks | 1–3 months |
| SBV rate hike | −Real Estate, +Cash | Immediate |
| CPI > 4% | −Market (SBV may tighten) | 1 month |
| PMI > 50 + rising | +Industrials, +Materials | 1–2 months |
| Credit growth > 14% | +Banks | 3 months |
| DXY falling | +VNINDEX (foreign inflow) | 1–2 weeks |
| Oil price shock | +Oil & Gas, −Airlines | 1–2 weeks |

## Related Skills
- `vn-trading-rules`: Market rules affected by calendar and macro events.
- `vn-sector-analysis`: Sector-specific macro sensitivity.
