---
name: data-routing
category: data-source
description: Data source selection — VN-only. All data comes from vnstock (fundamentals + OHLCV) and VietFin DNSE (OHLCV fallback).
---

## Data Source Overview

| Source | Markets | Auth Required | How |
|--------|---------|---------------|-----|
| vnstock | VN equities, indices, ETFs, futures | Optional (`VN_STOCK_KEY` in .env) | Tool: `vn_stock_analyze`, `vn_factor_data`, `alpha_bench(universe="vn-index")` |
| VietFin DNSE | VN OHLCV | No | Backend fallback for OHLCV (used automatically by loaders) |
| DNSE REST API | VN equities intraday | DNSE_API_KEY + DNSE_API_SECRET | Direct REST call — independent of WebSocket; supports 1m/5m/15m/30m/1H/1D |

## Tool Routing

| Use Case | Tool | Example |
|----------|------|---------|
| Single stock analysis | `vn_stock_analyze(symbol="VCB", days=365)` | Price + profile + ratios + financials |
| Multi-stock factor data | `vn_factor_data(universe="vn-index", factor="pe", period="2024-2025")` | Factor CSV + return CSV → `factor_analysis` |
| VN factor benchmark | `python run_ic_test.py` | Bench 31 VN-core factors on HOSE → IC/IR report |
| Backtest | `backtest(run_dir=...)` with `config.json` `source: "vietfin"` or `"auto"` | Backtest signal engine on VN stocks |
| Market index | `vn_index(symbol="VNINDEX", days=365)` | Index OHLCV |
| Mutual funds | `vn_fund_search()` + `vn_fund_history()` | Fund NAV data |
| **Intraday OHLCV** | `GET /api/stock/intraday/{symbol}?resolution=5&start=...&end=...` | Direct REST: **DNSE REST API**, resolutions: 1, 5, 15, 30, 1H, 1D |
| **Minute analysis** | `vn_intraday_analysis(symbol="VIC", resolution="5m", days=5)` | Intraday VWAP/TWAP/volume profile via DNSE REST |

## config.json Source Field

For backtest config.json:

- `"source": "vietfin"` — VietFin data (vnstock fundamentals fallback)
- `"source": "dnse"` — DNSE broker data (supports intraday intervals: `"interval": "5m"`)
- `"source": "auto"` — automatic routing (recommended)

For minute-level backtests, use `"interval"` in config.json:

```json
{
  "source": "dnse",
  "codes": ["VIC"],
  "start_date": "2026-05-25",
  "end_date": "2026-06-02",
  "interval": "5m",
  "initial_cash": 100000000,
  "commission": 0.00025
}
```

Do NOT use tushare, akshare, or okx — they are not available.
