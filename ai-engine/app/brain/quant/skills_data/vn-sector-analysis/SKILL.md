---
name: vn-sector-analysis
description: VN market sector analysis using ICB classification with 17 groups. Sector-specific factor behaviors, rotation patterns, and skip rules for BANKS/FINANCIAL_SERVICES.
category: analysis
---

# VN Sector Analysis

## Sector Classification

VN stocks use a modified ICB (Industry Classification Benchmark) system with **17 sector groups**:

| Sector | Count | Key Characteristics |
|--------|-------|-------------------|
| AGRICULTURE | 15 | Commodity-linked, seasonal |
| BANKS | 21 | High leverage, credit-driven, 30% foreign cap |
| BASIC_RESOURCES | 17 | Global commodity prices, cyclical |
| CHEMICALS | 25 | Petrochemicals, fertilizers |
| CONSTRUCTION | 29 | Real estate development, infrastructure |
| CONSTRUCTION_MATERIALS | 15 | Steel, cement, tiles |
| FINANCIAL_SERVICES | 26 | Securities, insurance, leasing |
| FOOD_BEVERAGE | 18 | Consumer staples, defensive |
| HEALTHCARE | 11 | Pharma, hospitals |
| INDUSTRIAL_GOODS | 44 | Manufacturing, industrial equipment |
| OIL_GAS | 3 | Oil price linked, small universe |
| OTHER_INDUSTRIALS | 6 | Miscellaneous |
| REAL_ESTATE | 60 | Largest sector, land banking |
| RETAIL_TRADE | 32 | Distribution, retailers |
| TECHNOLOGY | 9 | IT services, software |
| TRANSPORTATION | 35 | Logistics, aviation, shipping |
| UTILITIES | 32 | Power, water |

### Legacy Grouping
Some systems group sectors into 3 broad categories:
- **FINANCIALS** = BANKS + FINANCIAL_SERVICES
- **REAL_ESTATE** = CONSTRUCTION + REAL_ESTATE
- **OTHERS** = everything else

## Factor Behavior by Sector

### Best-Performing Factors (IC benchmark, 3-year VN data)

| Factor | Best Hold | Avg IC | Best Sectors |
|--------|-----------|--------|-------------|
| ROE_NORM | 20d | +0.077 | Consumer, Healthcare, Technology |
| NM | 20d | +0.051 | Broad — most sectors |
| YOY_REV | 20d | +0.032 | Retail, Construction, F&B |
| HML_REAL (reverse) | 20d | -0.075 | Growth sectors (Tech, Retail) |

### Sector-Specific Factor Behavior

**BANKS & FINANCIAL_SERVICES**
- Skip EVEBITDA_INV (EBITDA not applicable).
- Skip GM (no cost of goods sold).
- Skip ACCRUAL / CFO_TO_NI (different accrual structure).
- ROE_NORM and NM remain valid.
- SIZE has strong negative IC (large banks underperform small ones).

**REAL_ESTATE & CONSTRUCTION**
- HML_REAL reverse signal is strongest here (growth real estate firms beat value).
- GM and NM are weaker due to lumpy revenue recognition.

**INDUSTRIAL_GOODS**
- PIOTROSKI_F works well (conservative financing matters).
- VOL_20D_ORTHO is most informative (low vol = stable industrials outperforming).

**CONSUMER (F&B, RETAIL)**
- ROE_NORM and NM are most predictive.
- YOY_REV catches growth inflection points.

## Sector Rotation Patterns

### VN Market Rotation Signals

| Signal | Rotate Into | Rotate Out Of |
|--------|-------------|---------------|
| VNINDEX rising + low vol | Industrials, Tech, Consumer | Utilities, Healthcare |
| VNINDEX falling | Utilities, Healthcare, F&B | Real Estate, Construction |
| Rate cut cycle | Real Estate, Banks | — |
| Rate hike cycle | F&B, Healthcare, Cash | Real Estate, Banks |
| Commodity rally | Basic Resources, Oil & Gas | F&B (input cost pressure) |
| Foreign buying surge | Blue chips, VN30 | Small caps |

### Seasonal Patterns
- **Tết** (Jan–Feb): market tends to rally 2–3 weeks before, dip after.
- **Q3 earnings season**: Real Estate & Construction show highest volatility.
- **Year-end (Nov–Dec)**: Banks and Securities often rally on credit growth targets.

## Sector Data Queries

```python
# Get sector for a symbol
from app.brain.dataflows.vendors.vn.sector_groups import classify
sector = classify("VCB")  # Returns "BANKS"

# Get all symbols in a sector
# SQL: SELECT symbol FROM stocks WHERE sector = 'BANKS'

# Sector-neutral factor values
# Uses prepare_factor_for_ic() with sector_col="sector"
```

## VN30 Composition
- Top 30 large-cap liquid stocks.
- Review quarterly.
- Currently dominated by: VCB, BID, CTG (Banks); VHM, VRE (Real Estate); HPG (Steel); MWG, FPT (Retail/Tech).
- VN30 stocks account for ~70% of HOSE market cap.
- **For factor research**: VN30 universe has different factor behavior — ROE_NORM IC is weaker (compressed), VOL_20D_ORTHO is stronger.

## Sector-Neutral IC Impact
Sector neutralization typically reduces raw IC by 0.005–0.014:
- ROE_NORM: raw=0.077, SN=0.063 (delta −0.014)
- NM: raw=0.051, SN=0.042 (delta −0.009)
- GM: raw=0.023, SN=0.015 (delta −0.008)

This means ~15–20% of factor power is sector-driven.

## Related Skills
- `vn-trading-rules`: VN market rules affecting sector analysis.
- `vn-macro-calendar`: Macro events driving sector rotation.
