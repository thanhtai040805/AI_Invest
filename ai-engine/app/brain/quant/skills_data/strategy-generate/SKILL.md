---
name: strategy-generate
description: Create, modify, and optimize quantitative trading strategies for Vietnam stocks, then backtest and evaluate them.
category: strategy
---

## Workflow

1. **Requirements parsing**: parse user intent, extract instrument codes, time range, and strategy logic, then write `config.json`
2. **Strategy design**: think through the 5 questions of data / signal / position sizing / backtest / validation
3. **Strategy coding**: write `code/signal_engine.py` (following the `SignalEngine` contract)
4. **Syntax check**: `bash("python -c \"import ast; ast.parse(open('code/signal_engine.py').read()); print('OK')\"")`
5. **Run backtest**: call the `backtest` tool (built into the engine; no need to write `run_backtest.py`)
6. **Evaluate results**: read `artifacts/metrics.csv` and judge by the review criteria
7. **Iterative fixing**: if results are poor, modify with `edit_file` → run `backtest` → re-evaluate

**You only need to write `signal_engine.py` and `config.json`. The `backtest` tool automatically handles data loading and backtest execution.**

## Requirements Parsing

Extract the following from the user's description:
- **Instrument codes**: Vietnam stock symbols (e.g. VCB, VNM, FPT, HPG)
- **Time range**: if the user does not specify dates, default to **5 years back from today**
- **Strategy logic**: entry / exit conditions and indicator parameters

**If critical information is missing, you must ask the user instead of guessing:**
- Instrument not specified → ask which stock they want to backtest (offer several popular VN suggestions)
- Strategy description is vague → provide 2-3 strategy directions for the user to choose from

**Write `config.json` first, then write code.** `config.json` must be placed in the root of `run_dir`.

## Strategy Design

Before writing code, think through these 5 questions:

1. **Data requirements**: basic OHLCV (open, high, low, close, volume). For fundamental factors, use `vn_factor_data` first then pass CSVs to `factor_analysis`.
2. **Signal logic**: what are the entry conditions? What are the exit conditions? Direction (long / short / long-short)? Are there filters?
3. **Position management**: equal-weight allocation or scaling in/out? Risk control (stop-loss, maximum position)?
4. **Backtest parameters**: time range, initial capital (default 1,000,000), commission (default 0.1%)
5. **Validation checklist**: signal consistency (no NaN signals), position check, and completeness of generated artifacts

## `SignalEngine` Contract

```python
class SignalEngine:
    def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        """
        Args:
            data_map: code -> DataFrame (columns: open, high, low, close, volume, DatetimeIndex)
        Returns:
            code -> signal Series, value range [-1.0, 1.0]
            1.0 = fully long, 0.5 = half position, 0.0 = flat, -1.0 = fully short
            Portfolio strategy: selected stocks split weights equally (e.g. top 10 -> each 0.1)
            Legacy integer signals {-1, 0, 1} remain compatible
        """
```

**Hard constraints:**
- The signal `Series` index must align exactly with the input `DataFrame` index
- Include all required imports (`numpy`, `pandas`, and so on)
- Do not hardcode dates or stock codes (read them from `config.json`)
- Do not include an `if __name__ == "__main__"` block
- Pure pandas / numpy implementation, with no external signal libraries
- Output plain Python code, not Markdown fences

## Quality Checklist

Self-check after writing `signal_engine.py`:
- [ ] All imports are included (`numpy`, `pandas`, `typing`, and so on)
- [ ] No undefined variables
- [ ] Signal logic is consistent with the strategy description
- [ ] Boundary handling: for empty data or insufficient history before the lookback window, use `fillna(0)` or skip
- [ ] Portfolio strategy: once N stocks are selected, each weight = 1/N (e.g. top 10 → each 0.1), unselected names = 0
- [ ] Signal values stay within `[-1.0, 1.0]`

## Instrument Codes

- Vietnam stocks on HOSE/HNX/UPCOM: simple uppercase symbols, e.g. VCB, VNM, FPT, HPG, VIC
- No suffix or prefix needed
- Recommended minimum 5 years of history for meaningful backtest

## Data Sources

| Source | Config value | How |
|--------|-------------|-----|
| VietFin (vnstock fallback) | `"vietfin"` | Primary source for VN OHLCV + fundamentals |
| DNSE (broker) | `"dnse"` | Alternative VN OHLCV source |
| Auto | `"auto"` | Recommended — auto-selects best available source |

Do NOT use tushare, akshare, or okx — they are not supported.

## `config.json` Format

```json
{
  "source": "auto",
  "codes": ["VCB"],
  "start_date": "2020-01-01",
  "end_date": "2025-12-31",
  "interval": "1D",
  "initial_cash": 1000000,
  "commission": 0.001,
  "extra_fields": null,
  "fundamental_fields": null,
  "optimizer": null,
  "optimizer_params": {},
  "engine": "daily",
  "validation": null
}
```

- `source`: `"auto"` (recommended) / `"vietfin"` / `"dnse"`
- `interval`: candlestick interval, default `"1D"`
- `extra_fields`: not supported for VN (use `vn_factor_data` tool instead)
- `fundamental_fields`: not supported (use `vn_factor_data` instead)
- `optimizer`: optional, one of `"equal_volatility"` / `"risk_parity"` / `"mean_variance"` / `"max_diversification"` / `null` (equal-weight by default)
- `optimizer_params`: optimizer parameters, such as `{"lookback": 60}`
- `engine`: backtest engine, default `"daily"`
- `initial_cash`: default 1,000,000
- `commission`: default 0.1%
- `validation`: optional statistical validation

For factor-based strategies: first call `vn_factor_data(universe="vn-index", factor="pe")`, then use the output CSVs with `factor_analysis` tool.

## Review Criteria

### Hard Gates (any failure → `passed=false`)

1. `artifacts/metrics.csv` exists and is non-empty
2. `artifacts/equity.csv` exists and is non-empty
3. `exit_code == 0` (backtest exits normally)
4. The `equity` column in `equity.csv` contains no `NaN` values
5. `trade_count > 0` (zero trades = signal bug)

### Scoring Rules

- Successful backtest + complete artifacts + at least 1 trade → `score ≥ 60` → **passed**
- Poor return / low Sharpe alone should not push the score below 60; they are optimization suggestions only
- `score ≥ 60` = `passed=true`

### Bug Categories (reduce the score)

1. **Zero trades** (`trade_count=0`): signal-logic bug, conditions may be too strict
2. **Late first trade** (first trade > 2 years after backtest start): data-filtering bug or overly long lookback window
3. **Capital utilization < 50%**: position-management bug, portfolio is flat most of the time
4. **Open position at the end** (positions still open when backtest ends): exit-signal timing bug

### `action_items` Format

If improvements are needed after evaluation, write `action_items`:
- Format: `"Change X from A to B"` or `"Add X logic in signal_engine.py"`
- Must be specific down to parameter values, file names, and function names
- At least 2 items
- Examples:
  - `"Change short MA from 5 to 10 days to reduce whipsaw signals"`
  - `"Add stop-loss: force close when loss exceeds 5%"`
  - `"Add volume filter: only trigger buy on high volume days"`

## Supporting Files

- [examples.md](examples.md) — example call sequence
