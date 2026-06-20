---
name: vn-trading-rules
description: VN-specific equity market rules: T+2 settlement, HOSE ±7% price limits, lot sizes, trading sessions, and order types. Required for any VN market analysis or backtest.
category: analysis
---

# VN Trading Rules

## Market Structure

### Trading Sessions (HOSE)
| Session | Time (VN) | Notes |
|---------|-----------|-------|
| ATO (Opening) | 09:00–09:15 | Matching only, no cancels after 09:10 |
| Continuous 1 | 09:15–11:30 | Regular order matching |
| Break | 11:30–13:00 | Lunch |
| Continuous 2 | 13:00–14:30 | Regular order matching |
| ATC (Closing) | 14:30–14:45 | Matching only, no cancels after 14:35 |
| Put-through | 14:45–15:00 | Block trades |

### Settlement
- **T+2 settlement**: Trade settles 2 business days after execution.
- **T+1 availability** for short-term trading via margin/thỏa thuận.
- Forward return calculation must account for T+2: entry at T+1 close (earliest tradable exit).

### Price Limits (HOSE)
- **Daily ±7%** from reference price (previous close).
- Calculated to nearest price step tick.
- Reference price is the close price of the previous trading day.

### Price Steps (HOSE)
| Price Range | Step Size |
|-------------|-----------|
| < 10,000 VND | 10 VND |
| 10,000 – 49,950 VND | 50 VND |
| 50,000 – 99,500 VND | 100 VND |
| 100,000 – 499,500 VND | 500 VND |
| >= 500,000 VND | 1,000 VND |

### Lot Sizes
| Market | Standard Lot | Odd Lot |
|--------|-------------|---------|
| HOSE | 100 shares | 1–99 shares |
| HNX | 100 shares | 1–99 shares |
| UPCOM | 100 shares | 1–99 shares |

### Order Types
- **LO** (Limit Order): Good-till-cancelled within session.
- **MP** (Market Price): Matches at best available price.
- **ATO** (At The Opening): Matching only in ATO session.
- **ATC** (At The Closing): Matching only in ATC session.
- **PLO** (Put-through Limit Order): Block trade (1,000–500,000 lots).

## VN-Specific Index Rules

### VNINDEX
- Market-cap weighted, all HOSE stocks.
- Base date: 2000-07-28, base value: 100.
- Review: semi-annual (June, December).

### VN30
- Top 30 market cap + liquidity on HOSE.
- Free-float adjusted.
- Review: quarterly (March, June, September, December).
- **Key for factor research**: VN30 stocks have better liquidity, lower price impact, more foreign room.

## Factor Computation Constraints

### Look-ahead Bias Prevention
- Financial statement data: use `release_date` if available; otherwise apply heuristics:
  - Quarterly reports: available ~20 days after period end.
  - Annual reports: available ~30 days after period end.
- Price data: T+1 forward return entry.

### Liquidity Filter
- Minimum 5 billion VND average daily value over 20 trading days.
- This excludes ~60% of HOSE stocks; only the most liquid 150–180 remain.

### Foreign Ownership Limits
- Most VN stocks have a 30% (unregulated) or 49% (regulated) foreign ownership cap.
- Banks: 30% cap.
- When foreign room = 0, foreign investors can only sell.

### Sector Skip Rules
- BANKS and FINANCIAL_SERVICES sectors must skip:
  - EVEBITDA_INV (not meaningful for banks).
  - GM (gross margin not defined for banks).
  - ACCRUAL / CFO_TO_NI (financial accruals differ).

### Price Limit Impact
- Daily ±7% limits cause price discovery delays.
- Momentum factors need longer lookback (20d+ vs 5d) to avoid limit-induced noise.
- Gap days (limit up/down with no trades) must be handled in return calculation.

## Data Sources for VN Market

| Data | Source | Table |
|------|--------|-------|
| OHLCV | DNSE REST API | `ohlcv` |
| Corporate actions | corporate_actions table | `corporate_actions` |
| Financial statements | AlphaStock API | `financial_statements` |
| Foreign flow | CafeF API | `foreign_flow` |
| Insider trades | CafeF API | `insider_trades` |
| Risk flags | Computed (risk_flags_v2) | `risk_flags` |
| Factor scores | Computed (daily ETL) | `factor_scores` |
| Macro indicators | SBV / vi.money / yfinance | `macro_indicators` |

## Key Constants

```python
SETTLEMENT_LAG = 2       # T+2
ENTRY_OFFSET = 1         # T+1: first tradable close
PRICE_LIMIT = 0.07       # HOSE ±7%
MIN_VALUE_BN = 5.0       # Liquidity filter threshold (billion VND)
MIN_STOCKS = 30          # Minimum stocks for IC computation
MIN_DATES = 20           # Minimum evaluation dates
```

## Related Skills
- `vn-sector-analysis`: VN industry classification and sector-specific factors.
- `vn-macro-calendar`: VN macro events and economic calendar.
