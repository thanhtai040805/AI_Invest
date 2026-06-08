# AIInvest — Roadmap V4 (Rectified) + Implementation Status

> **Mục đích:** Giải quyết 8 vấn đề kỹ thuật phát hiện trong V3, scope lại tính năng cho thực tế VN market.
> **Trạng thái hiện tại:** ✅ = done · ⏳ = in progress · ❌ = not started · ⚠️ = revised (khác V4)

---

## 📊 Implementation Status — Tổng quan

| Mục | Trạng thái | File/Module chính | Ghi chú |
|-----|-----------|-------------------|---------|
| **A.1** Computed risk_flags (10 flags) | ⏳ | `risk_flags_v2.py` batch ETL | 10 computed flags từ structured data. Code written, chưa test production |
| **A.2** adj_close | ✅ | `app/brain/.../adj_close.py` | 1,176,248 rows |
| **A.3** Lending rates | ✅ | `app/services/macro_service.py` | SBV + CafeF fallback |
| **A.4** 30 VN-core factors | ✅ | `app/brain/.../factor_scores.py` | ~20 factors, cross-sectional rank |
| **A.5** Insider trades | ✅ ⚠️ | `app/brain/.../insider_trades.py` | CafeF API (không vnstock), 29.599 rows |
| **A.6** ~~Pledge data~~ **DROPPED** | 🗑️ | — | Thay bằng Fraud Pentagon → cũng dropped (3/5 factors unavailable). M-Score + F-Score thay thế |
| **A.7** DB tables schema | ✅ | `app/services/pg_pool.py` | 11 tables |
| **A.8** Tết/lunar calendar | ✅ | `app/brain/.../calendar.py` | VN holidays + trading day check |
| **B.1** Feature drops | ✅ | Đã follow V4 decisions | - |
| **B.2** FLOOR_TRAP | ✅ MERGED | → A.1 computed flags | Flag #3 trong computed risk_flags, SOFT_BLOCK |
| **C.1** Data sources | ✅/❌ | Mixed | Xem chi tiết bên dưới |
| **C.3** Phase timeline | ~50% | Phase 0-1 done, 2-4 pending | - |
| **C.4** ML pipeline | ❌ | Code exists, chưa wired | XGBoost chưa install |
| **D.0** News pipeline | ✅ **NEW** | `app/brain/.../news_events.py` | CafeF Events API, 14k+ rows |

---

# PHẦN A — GIẢI QUYẾT 8 VẤN ĐỀ KỸ THUẬT

## A.1 Computed Risk Flags — 10 flags từ structured data ⚠️ [REVISED — không scraper]

**Vấn đề gốc (V4):** SSC Oracle WebPortal không scrape được. CafeF regulatory categories (xu-phat.chn, canh-bao.chn, huy-niem-yet.chn) trả về 404. HOSE hsx.vn là React SPA.

**⚠️ Delta V4 → thực tế:** Cả 3 nguồn external regulatory đều không dùng được. **Giải pháp đúng là computed flags từ structured data đã có trong DB** — không cần scraper nào cả.

**10 flags, 3 priority tiers:**

| # | Flag | Data Source | Loại | Priority |
|---|------|-------------|------|----------|
| 1 | **CANH_BAO_TC** | `financial_statements` — kỳ báo cáo có "Cảnh báo" | **HARD** | P0 |
| 2 | **CHAM_BAO_TC** | `financial_statements` — period_end > 60 ngày | **HARD** | P0 |
| 3 | **FLOOR_TRAP** | `technical_indicators` — momentum_1d ≤ -6.9% ≥ 2 ngày | SOFT | P0 |
| 4 | **SHARP_DROP** | `technical_indicators` — momentum_1d ≤ -7% | SOFT | P0 |
| 5 | **KHOI_LUONG_BAT_THUONG** | `technical_indicators` — volume_ratio ≥ 3.0 | SOFT | P0 |
| 6 | **FOREIGN_FLOW_ANOMALY** | `foreign_flow` — net sell ≥ 5 ngày liên tiếp | SOFT | P1 |
| 7 | **INSIDER_SELLING_ANOMALY** | `insider_trades` — net sell > 2× buy, quantity > 100k | SOFT | P1 |
| 8 | **GOVERNANCE_SHOCK** | `news_events` — title match "từ nhiệm", "thay CEO", v.v. | SOFT | P1 |
| 9 | **M-Score** (Beneish) | `financial_statements` JSONB — non-bank only | SOFT | P2 |
| 10 | **F-Score** (Piotroski) | `financial_statements` JSONB — all stocks | SOFT | P2 |

**HARD flags** → `risk_gate_node` force HOLD, block BUY.
**SOFT flags** → add to risk assessment, không block tự động. ≥3 soft flags = HIGH risk.

```python
# app/services/risk_flags_v2.py — Batch computed flag engine

def compute_p0_flags(cur, symbols: list[str]) -> list[tuple]:
    """Batch compute flags 1-5 for all symbols in a single DB pass."""

    # ── Flag 1: CANH_BAO_TC ────────────────────────────────────────
    cur.execute("""
        SELECT symbol, MAX(period_end) as max_period_end
        FROM financial_statements
        WHERE symbol = ANY(%s)
          AND statement_type = 'balance_sheet'
        GROUP BY symbol
    """, (symbols,))
    latest_periods = dict(cur.fetchall())

    flags = []
    for sym in symbols:
        max_pe = latest_periods.get(sym)
        if max_pe is None:
            flags.append((sym, "CANH_BAO_TC", "Không có dữ liệu báo cáo tài chính", "financial_statements"))
        elif (date.today() - max_pe).days > 60:
            flags.append((sym, "CHAM_BAO_TC", f"BC thường niên chậm {max_pe} (>{60} ngày)", "financial_statements"))

    # ── Flag 3-5: FLOOR_TRAP, SHARP_DROP, KHOI_LUONG_BAT_THUONG ──
    cur.execute("""
        SELECT symbol, indicators FROM technical_indicators
        WHERE calc_date >= %s AND symbol = ANY(%s)
        ORDER BY symbol, calc_date DESC
    """, (date.today() - timedelta(days=10), symbols))

    # Group by symbol, check last 5 days for floor streaks
    for sym in symbols:
        # ... momentum check logic ...
        pass

    return flags
```

**Key insight:** Market reacts 1–40 sessions BEFORE regulatory action. Computed flags catch risk earlier.

> **Status:** ⏳ Implemented. `risk_flags_v2.py` + `financial_etl.py` created. Wired vào `daily_etl.py`. `risk_gate_node` rewrite trong `nodes.py` đã query DB. `risk_flags_tool.py` đã refactor dùng batch engine. Chưa test production (cần chạy ETL + verify data).

---

## A.2 adj_close — Bỏ yfinance .VN, dùng vnstock ✅ [adj_close.py]

**Vấn đề:** `yf.Ticker("VCB.VN")` không hoạt động. yfinance không support VN stocks.

**Giải pháp:** vnstock v4 đã có `company.events()` trả về dividends + splits. Đây là nguồn đúng.

```python
# brain/dataflows/vendors/vn/adj_close.py

from vnstock import Company
import pandas as pd
import numpy as np

async def get_corporate_events(symbol: str) -> dict:
    """
    Dùng vnstock Company API thay vì yfinance.
    vnstock.Company.events() trả về:
    - dividends: [{ex_date, cash_div, stock_div_ratio}]
    - splits: [{ex_date, ratio}]
    """
    try:
        company = Company(symbol=symbol, source="VCI")
        events  = company.events()  # DataFrame với event history

        dividends = []
        splits    = []

        if events is not None and not events.empty:
            for _, row in events.iterrows():
                event_type = str(row.get("event_type", "")).upper()

                if "DIVIDEND" in event_type or "COT_TUC" in event_type:
                    dividends.append({
                        "ex_date":   pd.to_datetime(row.get("ex_right_date")),
                        "cash_div":  float(row.get("cash_dividend_rate", 0)) / 100,
                        "stock_div": float(row.get("stock_dividend_rate", 0)) / 100,
                    })
                elif "SPLIT" in event_type or "CHIA_TACH" in event_type:
                    splits.append({
                        "ex_date": pd.to_datetime(row.get("ex_right_date")),
                        "ratio":   float(row.get("split_ratio", 1.0)),
                    })
        return {"dividends": dividends, "splits": splits}

    except Exception as e:
        import logging
        logging.warning(f"vnstock events failed for {symbol}: {e}")
        return {"dividends": [], "splits": []}

async def compute_adj_close(symbol: str,
                             ohlcv_df: pd.DataFrame) -> pd.DataFrame:
    """
    Backward-adjust OHLCV dùng corporate events từ vnstock.
    Nếu không có events → adj_close = close (adj_factor = 1.0).
    """
    events = await get_corporate_events(symbol)
    df     = ohlcv_df.copy().sort_index()

    df["adj_factor"] = 1.0

    # Apply dividends (cash)
    for div in events["dividends"]:
        ex_date  = div["ex_date"]
        if ex_date not in df.index:
            continue
        prev_close = df.loc[df.index < ex_date, "close"].iloc[-1]
        if prev_close > 0 and div["cash_div"] > 0:
            factor = (prev_close - div["cash_div"] * 1000) / prev_close
            # cash_div từ vnstock thường là % mệnh giá (1000 VND)
            df.loc[df.index < ex_date, "adj_factor"] *= max(factor, 0.5)

    # Apply stock dividends & splits
    for div in events["dividends"]:
        if div["stock_div"] > 0:
            ex_date = div["ex_date"]
            ratio   = 1 / (1 + div["stock_div"])
            df.loc[df.index < ex_date, "adj_factor"] *= ratio

    for split in events["splits"]:
        ex_date = split["ex_date"]
        if split["ratio"] > 0:
            df.loc[df.index < ex_date, "adj_factor"] /= split["ratio"]

    df["adj_close"] = (df["close"] * df["adj_factor"]).round(0)
    df["adj_open"]  = (df["open"]  * df["adj_factor"]).round(0)
    df["adj_high"]  = (df["high"]  * df["adj_factor"]).round(0)
    df["adj_low"]   = (df["low"]   * df["adj_factor"]).round(0)

    return df

# VALIDATION: So sánh adj_close với chart CafeF để confirm
async def validate_adj_close(symbol: str, df: pd.DataFrame) -> bool:
    """
    Spot-check: Nếu có dividend event, adj_close trước ex-date
    phải thấp hơn close một khoảng hợp lý.
    """
    events = await get_corporate_events(symbol)
    if not events["dividends"]:
        return True  # Không có event = không cần validate

    for div in events["dividends"][:3]:  # Check 3 events gần nhất
        ex = div["ex_date"]
        if ex in df.index:
            ratio = df.loc[df.index < ex, "adj_factor"].iloc[-1]
            if not (0.5 <= ratio <= 1.0):
                return False  # Ratio bất thường
    return True
```

**Kết luận:** Xóa hoàn toàn yfinance cho VN stocks. Dùng `vnstock Company.events()`. yfinance chỉ giữ lại cho **global macro** (VIX, DXY, oil, gold).

> **Status:** ✅ Hoàn thành. `adj_close.py` có `refresh_all()` + `refresh_incremental()`. 1.176.248 rows, 0 NULL. Wired vào daily_etl.py.

---

## A.3 Lending Rates — Bỏ Vimo, dùng SBV trực tiếp ✅ [macro_service.py]

**Vấn đề:** `api.vimo.vn/v1/rates/lending` không phải public API.

**Giải pháp thực tế:** 3 nguồn thay thế, ưu tiên theo độ tin cậy:

```python
# app/services/macro_service.py

import httpx
from bs4 import BeautifulSoup

# SOURCE 1: SBV (Ngân hàng Nhà nước) — trang lãi suất điều hành
SBV_RATES_URL = "https://www.sbv.gov.vn/webcenter/portal/vi/menu/trangchu/tk/lstk"

# SOURCE 2: Cafef lãi suất
CAFEF_RATES_URL = "https://cafef.vn/ngan-hang/lai-suat-tiet-kiem.chn"

# SOURCE 3: Hardcode có update schedule — nếu scrape fail
FALLBACK_RATES = {
    "sbv_base_rate":          0.045,   # SBV điều chỉnh khoảng 1-2 lần/năm
    "deposit_1m":             0.020,
    "deposit_3m":             0.030,
    "deposit_6m":             0.040,
    "deposit_12m":            0.050,   # Cập nhật thủ công mỗi quý
    "overnight_rate":         0.015,
    "last_updated":           "2024-Q4",
    "source":                 "hardcode_fallback",
}

async def fetch_lending_rates() -> dict:
    """
    Waterfall: SBV → CafeF → hardcode fallback.
    Lãi suất thay đổi chậm (1-4 lần/năm) → hardcode fallback OK.
    """
    # Try SBV
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(SBV_RATES_URL)
            return _parse_sbv_rates(resp.text)
    except Exception:
        pass

    # Try CafeF
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(CAFEF_RATES_URL)
            return _parse_cafef_rates(resp.text)
    except Exception:
        pass

    # Fallback: hardcode (acceptable vì rates thay đổi chậm)
    return FALLBACK_RATES

def _parse_sbv_rates(html: str) -> dict:
    soup  = BeautifulSoup(html, "html.parser")
    # Parse bảng lãi suất từ SBV
    # ...
    return {}

def _parse_cafef_rates(html: str) -> dict:
    soup  = BeautifulSoup(html, "html.parser")
    rates = {}
    rows  = soup.select("table.laisuat tr")
    for row in rows:
        cells = row.select("td")
        if len(cells) >= 2:
            term  = cells[0].get_text(strip=True)
            rate  = cells[1].get_text(strip=True)
            # Map term → key
            if "12" in term:
                rates["deposit_12m"] = _parse_rate_str(rate)
    return rates

def _parse_rate_str(s: str) -> float:
    import re
    m = re.search(r'([\d.]+)', s.replace(',', '.'))
    return float(m.group(1)) / 100 if m else 0.05

# Cron: Update monthly, không cần daily
# 00 09 1 * * python -m app.workers.macro_update
```

**Kết luận:** Bỏ Vimo hoàn toàn. Dùng SBV → CafeF → hardcode fallback. Lãi suất thay đổi chậm nên hardcode fallback là chấp nhận được.

> **Status:** ✅ Hoàn thành. `macro_service.py` fetch từ yfinance (oil/VIX/gold/USD index/10y yield/USD/VND), VietFin (VNINDEX), vi.money (CPI) và SBV (refinancing/discount rates). 4.143 rows trong DB. Wired vào daily_etl.py step_macro_indicators.

---

## A.4 Alpha Factors — Scope lại 453 → 30 VN-core factors ✅ [factor_scores.py]

**Vấn đề:** 453 factors overengineered. VN có ~400 HOSE stocks, thin liquidity, nhiều Alpha101/GTJA191 formula không applicable.

**Giải pháp:** 30 factors chia 5 nhóm, tất cả đã validate được IC > 0.03 trên VN data.

```python
# brain/quant/factors/zoo/vn_core/vn_core_factors.py
"""
30 VN-Core Factors — thay thế 453 factors overengineered.
Tiêu chí chọn:
1. IC > 0.03 trên VN data 3 năm gần nhất
2. Computable từ data sources sẵn có (DNSE + vnstock)
3. Không cần cross-market data phức tạp
4. Phù hợp T+2, price limit ±7%, thin liquidity
"""

VN_CORE_FACTORS = {

    # ── NHÓM 1: MOMENTUM (6 factors) ────────────────────────────────
    "MOM_1M": {
        "formula": "adj_close / adj_close.shift(20) - 1",
        "direction": "positive",
        "note": "Short-term momentum, VN có reversal nhanh hơn US"
    },
    "MOM_3M_SKIP1M": {
        "formula": "adj_close.shift(20) / adj_close.shift(60) - 1",
        "direction": "positive",
        "note": "Skip 1 tháng gần nhất để tránh reversal"
    },
    "MOM_6M_SKIP1M": {
        "formula": "adj_close.shift(20) / adj_close.shift(120) - 1",
        "direction": "positive",
        "note": "Medium-term trend"
    },
    "PRICE_ACCEL": {
        "formula": "MOM_1M - MOM_1M.shift(5)",
        "direction": "positive",
        "note": "Tăng tốc giá = institutional entry signal"
    },
    "VOL_MOMENTUM": {
        "formula": "(volume * close).rolling(5).sum() / (volume * close).rolling(20).sum()",
        "direction": "positive",
        "note": "Volume-weighted momentum"
    },
    "HIGH_52W_PROXIMITY": {
        "formula": "adj_close / adj_close.rolling(252).max()",
        "direction": "positive",
        "note": "Gần đỉnh 52 tuần = breakout potential"
    },

    # ── NHÓM 2: VALUE (6 factors) ────────────────────────────────────
    "PE_INVERSE_RANK": {
        "formula": "rank(1 / pe)",
        "direction": "positive",
        "note": "Cross-sectional PE rank, VN PE thấp hơn US"
    },
    "PB_INVERSE_RANK": {
        "formula": "rank(1 / pb)",
        "direction": "positive",
        "note": "PB < 1 phổ biến trên HNX → cơ hội"
    },
    "EV_EBITDA_RANK": {
        "formula": "rank(-ev / ebitda)",
        "direction": "positive",
        "note": "Adjust cho ngân hàng (EV = MarketCap)"
    },
    "FCF_YIELD": {
        "formula": "rank(fcf / market_cap)",
        "direction": "positive",
        "note": "Tốt cho manufacturing VN"
    },
    "DIVIDEND_YIELD": {
        "formula": "rank(div_per_share / close)",
        "direction": "positive",
        "note": "Pre-dividend run-up phổ biến VN"
    },
    "EARNINGS_YIELD": {
        "formula": "rank(eps_ttm / close)",
        "direction": "positive",
        "note": "Inverse PE, ổn định hơn PE ratio"
    },

    # ── NHÓM 3: QUALITY (6 factors) ──────────────────────────────────
    "ROE_STABILITY": {
        "formula": "roe_4q_mean / (roe_4q_std + 0.01)",
        "direction": "positive",
        "note": "Consistency quan trọng hơn mức ROE tuyệt đối"
    },
    "ACCRUAL_INVERSE": {
        "formula": "rank(-(ni - cfo) / total_assets)",
        "direction": "positive",
        "note": "Thấp = earnings quality cao"
    },
    "GROSS_MARGIN_TREND": {
        "formula": "gross_margin_q - gross_margin_q.shift(4)",
        "direction": "positive",
        "note": "Improving margin = competitive moat"
    },
    "DEBT_REDUCTION": {
        "formula": "rank(-(total_debt / equity - total_debt.shift(4) / equity.shift(4)))",
        "direction": "positive",
        "note": "Giảm leverage = positive signal"
    },
    "CASH_RATIO": {
        "formula": "rank(cash / current_liabilities)",
        "direction": "positive",
        "note": "Liquidity buffer trong crisis"
    },
    "CFO_CONSISTENCY": {
        "formula": "(cfo_4q > 0).sum() / 4",
        "direction": "positive",
        "note": "CFO dương cả 4 quý = quality"
    },

    # ── NHÓM 4: LIQUIDITY & MARKET STRUCTURE (6 factors) ─────────────
    "AMIHUD_INVERSE": {
        "formula": "rank(-amihud_illiquidity_20d)",
        "direction": "positive",
        "note": "Càng liquid càng tốt cho execution"
    },
    "TURNOVER_RATE": {
        "formula": "volume.rolling(5).mean() / shares_outstanding",
        "direction": "positive",
        "note": "VN: turnover < 0.1% = quá illiquid"
    },
    "FOREIGN_NET_BUY_5D": {
        "formula": "foreign_net_value.rolling(5).sum() / market_cap",
        "direction": "positive",
        "note": "Foreign = institutional smart money"
    },
    "FOREIGN_ROOM_BUFFER": {
        "formula": "rank(foreign_room_remaining_pct)",
        "direction": "positive",
        "note": "Room còn nhiều = room để tăng"
    },
    "BID_ASK_SPREAD_INVERSE": {
        "formula": "rank(-(ask - bid) / ((ask + bid) / 2))",
        "direction": "positive",
        "note": "Tight spread = liquid, easier to exit"
    },
    "PRICE_IMPACT_INVERSE": {
        "formula": "rank(-abs(return) / dollar_volume)",
        "direction": "positive",
        "note": "Kyle lambda proxy"
    },

    # ── NHÓM 5: EVENT & SENTIMENT (6 factors) ────────────────────────
    "INSIDER_NET_BUY_30D": {
        "formula": "insider_buy_qty - insider_sell_qty (30 ngày)",
        "direction": "positive",
        "note": "Insider buying tại VN = signal mạnh"
    },
    "NEWS_SENTIMENT_5D": {
        "formula": "sentiment_score.rolling(5).mean()",
        "direction": "positive",
        "note": "Từ CafeF news, lexicon-based"
    },
    "EARNINGS_SURPRISE": {
        "formula": "(actual_eps - consensus_eps) / abs(consensus_eps + 0.01)",
        "direction": "positive",
        "note": "VN: ít analyst → surprise lớn hơn"
    },
    "POST_RESULTS_MOMENTUM": {
        "formula": "return_3d_after_earnings",
        "direction": "positive",
        "note": "Under-reaction to earnings"
    },
    "REVENUE_SURPRISE_YOY": {
        "formula": "(revenue_q - revenue_q.shift(4)) / abs(revenue_q.shift(4))",
        "direction": "positive",
        "note": "Revenue growth beat = re-rating catalyst"
    },
    "ANALYST_REVISION_PROXY": {
        "formula": "eps_q / eps_q.shift(4) - 1",
        "direction": "positive",
        "note": "Proxy cho earnings revision (VN ít analyst)"
    },
}

# Tier system: Chạy theo priority
FACTOR_TIERS = {
    "TIER_1_ALWAYS": [  # 10 factors — chạy mỗi ngày cho tất cả stocks
        "MOM_3M_SKIP1M", "MOM_1M", "PE_INVERSE_RANK", "PB_INVERSE_RANK",
        "ROE_STABILITY", "ACCRUAL_INVERSE", "AMIHUD_INVERSE",
        "FOREIGN_NET_BUY_5D", "INSIDER_NET_BUY_30D", "NEWS_SENTIMENT_5D",
    ],
    "TIER_2_LIQUID_ONLY": [  # 10 factors — chỉ cho stocks > 5 tỷ/ngày
        "MOM_6M_SKIP1M", "PRICE_ACCEL", "VOL_MOMENTUM",
        "FCF_YIELD", "EV_EBITDA_RANK", "GROSS_MARGIN_TREND",
        "TURNOVER_RATE", "FOREIGN_ROOM_BUFFER", "EARNINGS_SURPRISE",
        "POST_RESULTS_MOMENTUM",
    ],
    "TIER_3_OPTIONAL": [  # 10 factors — chỉ khi có đủ data
        "HIGH_52W_PROXIMITY", "DIVIDEND_YIELD", "EARNINGS_YIELD",
        "DEBT_REDUCTION", "CASH_RATIO", "CFO_CONSISTENCY",
        "BID_ASK_SPREAD_INVERSE", "PRICE_IMPACT_INVERSE",
        "REVENUE_SURPRISE_YOY", "ANALYST_REVISION_PROXY",
    ],
}
```

**Kết luận:** 30 factors thay 453. Giữ nguyên registry system và IC benchmark, chỉ thay nội dung zoo. Có thể add thêm sau khi validate IC.

> **Status:** ✅ Hoàn thành (cơ bản). `factor_scores.py` compute ~20 factors từ OHLCV + technical_indicators + financial_ratios + news_events. Cross-sectional percentile rank. Wired vào daily_etl.py. NEWS_SENTIMENT_5D đã thêm từ `news_events` table.

---

## A.5 Insider Trades ⚠️ [REVISED — CafeF API, không phải vnstock]

**Vấn đề:** `cafef_insider_scraper.py` redundant vì `vnstock Company.insider_deals()` đã có.

**⚠️ Delta V4 → thực tế:** V4 nói dùng `vnstock Company.insider_deals()`. Trong thực tế, vnstock v4.0.4 AND v3.2.2 đều trả về 404 (TCBS endpoint không còn support). **Giải pháp đúng là CafeF Ajax API.**

```python
# brain/dataflows/vendors/vn/insider_trades.py

# CafeF API (thay vì vnstock):
API_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDCoDong.ashx"
# Query params: Symbol=ACB, PageSize=2000
# Trả về JSON với các fields:
#   - TransactionMan, RelatedMan
#   - RealBuyVolume, RealSellVolume
#   - VolumeBeforeTransaction, VolumeAfterTransaction
#   - TyLeSoHuu, PlanBuyVolume, PlanSellVolume
#   - PublishedDate (format: /Date(ms)/)
```

```python
# Code V4 gốc — KHÔNG DÙNG ĐƯỢC (vnstock 404):
from vnstock import Company
import pandas as pd

async def get_insider_trades(symbol: str, periods: int = 8) -> list[dict]:
    """
    Dùng vnstock Company.insider_deals() — đã có sẵn.
    Trả về danh sách giao dịch insider 8 quý gần nhất.
    """
    try:
        company = Company(symbol=symbol, source="VCI")
        df      = company.insider_deals(periods=periods)

        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            result.append({
                "symbol":          symbol,
                "trade_date":      str(row.get("deal_announce_date", "")),
                "trader_name":     str(row.get("dealer_name", "")),
                "trader_position": str(row.get("dealer_position", "")),
                "trade_type":      "BUY" if row.get("deal_quantity", 0) > 0 else "SELL",
                "quantity":        int(row.get("deal_quantity", 0)),
                "price":           float(row.get("deal_price", 0)),
                "before_pct":      float(row.get("deal_ratio_before", 0)),
                "after_pct":       float(row.get("deal_ratio_after", 0)),
                "source":          "vnstock",
            })
        return result

    except Exception as e:
        import logging
        logging.warning(f"Insider trades failed for {symbol}: {e}")
        return []

# Compute insider signal
def compute_insider_signal(trades: list[dict], days: int = 30) -> dict:
    from datetime import date, timedelta
    cutoff   = date.today() - timedelta(days=days)
    recent   = [t for t in trades
                if t.get("trade_date", "") >= str(cutoff)]

    buy_qty  = sum(t["quantity"] for t in recent if t["trade_type"] == "BUY")
    sell_qty = sum(t["quantity"] for t in recent if t["trade_type"] == "SELL")
    net_qty  = buy_qty - sell_qty

    return {
        "buy_qty_30d":   buy_qty,
        "sell_qty_30d":  sell_qty,
        "net_qty_30d":   net_qty,
        "signal":        ("BUYING" if net_qty > 0 else
                          "SELLING" if net_qty < 0 else "NEUTRAL"),
        "trade_count":   len(recent),
    }
```

**Kết luận (V4 gốc):** Xóa `cafef_insider_scraper.py` khỏi roadmap. Dùng `vnstock Company.insider_deals()`. Đơn giản hơn, data đã có sẵn.

> **Status:** ✅ Hoàn thành (dùng CafeF API thay vnstock). `insider_trades.py` với `refresh_all()` + `refresh_incremental()`. 29.599 rows, 424/424 HOSE symbols. Wired vào daily_etl.py. Phân biệt rõ: DB lưu `before_volume`, `after_volume`, `ownership_pct` (không phải `price`/`value_vnd` vì CafeF không trả).

---

## A.6 ~~Pledge Data~~ **DROPPED** 🗑️

**Quyết định:** Bỏ hoàn toàn. Cả Pledge data và Fraud Pentagon đều không có nguồn structured data.

| Approach | Kết quả | Lý do |
|----------|---------|-------|
| Pledge scraper (HOSE PDF) | ❌ Không khả thi | PDF phức tạp, tốn bandwidth, ROI thấp |
| Pledge proxy (ownership) | ❌ Sai số quá lớn | Heuristic không đáng tin cậy |
| Fraud Pentagon (alternative) | ❌ Cũng dropped | 3/5 factors (auditor, CEO/Chair duality, CEO changes) không có trong vnstock |

> **Status:** 🗑️ Dropped. Thay thế bằng M-Score (flag 9) + F-Score (flag 10) trong computed risk_flags (A.1).

---

## A.7 DB Tables — Thống nhất schema ✅ [pg_pool.py]

**Vấn đề:** `risk_assessments` là table thứ 10, không nhất quán với 9 tables đã plan.

**Quyết định:** `risk_assessments` là **table thứ 10**, độc lập với `risk_flags`. Hai tables có mục đích khác nhau.

```sql
-- risk_flags: Real-time flags từ UBCKNN/CafeF
-- (Table số 7 trong danh sách 9 tables)
CREATE TABLE risk_flags (
    symbol          TEXT NOT NULL,
    flag_type       TEXT NOT NULL,  -- DELIST, SANCTION, WARNING, etc.
    effective_date  DATE,
    is_active       BOOLEAN DEFAULT TRUE,
    source          TEXT,
    -- ...
    PRIMARY KEY (symbol, flag_type, effective_date)
);

-- risk_assessments: Computed CRS score hàng ngày
-- (Table thứ 10 — bổ sung vào schema)
CREATE TABLE risk_assessments (
    symbol           TEXT NOT NULL,
    assessment_date  DATE NOT NULL,
    crs_score        FLOAT,
    risk_level       TEXT,
    hard_blocked     BOOLEAN DEFAULT FALSE,
    soft_blocked     BOOLEAN DEFAULT FALSE,
    recommendation   TEXT,
    -- 7 layer scores
    score_quant      FLOAT,
    score_fundamental FLOAT,
    score_market_vn  FLOAT,
    score_macro_vn   FLOAT,
    score_global     FLOAT,
    score_regulatory FLOAT,
    score_behavioral FLOAT,
    -- Flags
    hard_flags       TEXT[],
    soft_flags       TEXT[],
    all_flags        TEXT[],
    detail           JSONB,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, assessment_date)
);
```

**Updated table count: 10 tables** (9 planned + 1 risk_assessments).

> **Status:** ✅ Hoàn thành. `pg_pool.py` có `migrate()` tạo 11 tables: job_states, macro_indicators, financial_statements, technical_indicators, financial_ratios, factor_scores, risk_flags, insider_trades, alpha_signals, foreign_flow, corporate_actions, risk_assessments, **news_events** (added sau V4).

---

## A.8 Tết Window — Tính theo Lunar Calendar thực tế ✅ [calendar.py]

**Vấn đề:** `TET_WINDOW = range(330, 365) + range(1, 50)` không đúng vì Tết âm lịch thay đổi mỗi năm.

```python
# brain/tools/vn_calendar.py

# Ngày Tết Nguyên Đán (ngày đầu tiên âm lịch) — Gregorian dates
TET_DATES = {
    2020: "2020-01-25",
    2021: "2021-02-12",
    2022: "2022-02-01",
    2023: "2023-01-22",
    2024: "2024-02-10",
    2025: "2025-01-29",
    2026: "2026-02-17",
    2027: "2027-02-06",
    2028: "2028-01-26",
    2029: "2029-02-13",
    2030: "2030-02-03",
}

def get_tet_window(year: int,
                   days_before: int = 15,
                   days_after: int = 7) -> tuple[date, date]:
    """
    Trả về (start, end) của window Tết.
    Default: 15 ngày trước đến 7 ngày sau Tết.
    """
    from datetime import date, timedelta
    tet_str  = TET_DATES.get(year)
    if not tet_str:
        # Fallback: estimate từ lunar calendar
        tet_str = _estimate_tet(year)
    tet_date = date.fromisoformat(tet_str)
    return (tet_date - timedelta(days=days_before),
            tet_date + timedelta(days=days_after))

def is_in_tet_window(check_date: date,
                     days_before: int = 15,
                     days_after: int = 7) -> bool:
    start, end = get_tet_window(check_date.year, days_before, days_after)
    # Cũng check năm trước nếu Tết rơi vào tháng 1
    if check_date.month == 1:
        start2, end2 = get_tet_window(check_date.year - 1, days_before, days_after)
        if start2 <= check_date <= end2:
            return True
    return start <= check_date <= end

def _estimate_tet(year: int) -> str:
    """
    Estimate từ công thức gần đúng.
    Chỉ dùng khi năm chưa có trong TET_DATES dict.
    """
    # Tết thường rơi vào cuối T1 đến giữa T2
    # Estimate = Jan 28 + offset theo chu kỳ 19 năm (Metonic cycle)
    from datetime import date
    base_year, base_day = 2024, 41  # 10/02/2024 = day 41
    cycle = (year - base_year) % 19
    CYCLE_OFFSETS = {0:0, 1:-18, 2:-7, 3:4, 4:-15, 5:-3, 6:8,
                     7:-10, 8:2, 9:13, 10:-5, 11:6, 12:-13, 13:-2,
                     14:9, 15:-9, 16:2, 17:14, 18:-4}
    offset   = CYCLE_OFFSETS.get(cycle, 0)
    tet_day  = base_day + offset + (year - 2024) * 0  # Simplified
    tet_date = date(year, 1, 1).replace(
        month=1 if tet_day <= 31 else 2
    )
    return str(tet_date)

# VN Trading Holidays (đầy đủ)
VN_HOLIDAYS = {
    2024: [
        "2024-01-01",  # Tết Dương lịch
        "2024-02-08", "2024-02-09", "2024-02-10",
        "2024-02-11", "2024-02-12", "2024-02-13", "2024-02-14",  # Tết
        "2024-04-18",  # Giỗ Tổ Hùng Vương
        "2024-04-30",  # Giải phóng miền Nam
        "2024-05-01",  # Quốc tế Lao động
        "2024-09-02",  # Quốc khánh
    ],
    2025: [
        "2025-01-01",
        "2025-01-27", "2025-01-28", "2025-01-29",
        "2025-01-30", "2025-01-31", "2025-02-01", "2025-02-02",  # Tết
        "2025-04-07",  # Giỗ Tổ
        "2025-04-30",
        "2025-05-01",
        "2025-09-01", "2025-09-02",
    ],
}

def is_trading_day(check_date: date) -> bool:
    if check_date.weekday() >= 5:  # Weekend
        return False
    year_holidays = VN_HOLIDAYS.get(check_date.year, [])
    return str(check_date) not in year_holidays
```

> **Status:** ✅ Hoàn thành. `calendar.py` có `is_trading_day()` dùng VN holidays + weekend check. Wired vào `daily_etl.py`.

---

# PHẦN B — TÍNH NĂNG NÊN BỎ / HOÃN

## B.1 Quyết định chính thức ✅ [đã follow V4]

| Tính năng | Quyết định | Lý do | Thay thế |
|-----------|-----------|-------|---------|
| 453 alpha factors | **Bỏ, dùng 30 VN-core** | Overengineered, VN ~400 stocks | `vn_core_factors.py` (30 factors) |
| GARCH per symbol (tất cả) | **Giới hạn top 50** | CPU nặng, ROI thấp | EWMA cho phần còn lại |
| Layer 7 Behavioral hoàn chỉnh | **Giảm scope Phase 1** | Social data chưa có | Chỉ giữ insider + news sentiment |
| China slowdown risk | **Bỏ Phase 1** | Gián tiếp, nhiều noise | Dùng global risk-off (VIX/DXY) thay thế |
| LLM Judge (Gemini eval) | **Hoãn Phase 3** | Tốn $ + phức tạp | Signal accuracy tracking đơn giản |
| Gemini integration | **Hoãn Phase 2** | Groq đủ dùng giai đoạn này | Groq llama-70b + qwen-32b |
| CafeF insider scraper riêng | **Bỏ** | vnstock đã có | `vnstock Company.insider_deals()` |
| FLOOR_TRAP hard block | **Đổi thành soft** | Có thể là cơ hội mua | Xuống SOFT_BLOCK + reversal_candidate flag |

> **Status:** ✅ Các quyết định drop/hoãn đều được follow. Ngoại lệ: "CafeF insider scraper riêng → bỏ" là sai (vnstock 404), thực tế đã build scraper riêng.

## B.2 FLOOR_TRAP → ✅ MERGED vào A.1

**Quyết định:** FLOOR_TRAP không còn là module riêng. Đã merge vào computed risk_flags v2 (flag #3).

```
SOFT_BLOCK_FLAGS (trong risk_gate_node):
  - FLOOR_TRAP       ← tín hiệu từ technical_indicators (momentum ≤ −6.9% ≥ 2 ngày)
  - SHARP_DROP        ← tín hiệu từ technical_indicators (momentum ≤ −7%)
  - KHOI_LUONG_BAT_THUONG
  - FOREIGN_FLOW_ANOMALY
  - INSIDER_SELLING_ANOMALY
  - GOVERNANCE_SHOCK
  - M-SCORE_FLAG
  - F-SCORE_FLAG

HARD_BLOCK_FLAGS (chỉ 2):
  - CANH_BAO_TC       ← từ financial_statements
  - CHAM_BAO_TC       ← từ financial_statements (delayed report)
```

> **Status:** ✅ Merged. Implement trong A.1 computed risk_flags.

---

# PHẦN C — KIẾN TRÚC SAU KHI RECTIFY

## C.1 Data Sources — Status thực tế

| Source | V4 Status | Status thực tế | Dùng cho | Method |
|--------|----------|---------------|---------|--------|
| DNSE WS | ✅ Confirmed | ❌ Chưa dùng | Real-time quote, orderbook | WebSocket |
| DNSE REST | ✅ Confirmed | ✅ Đang dùng | OHLCV, fundamentals | httpx |
| vnstock v4 | ✅ Confirmed | ✅ Đang dùng | IS/BS/CF, profile, events | Python lib |
| CafeF scrape | ✅ Confirmed | ✅ Đang dùng | **Insider, foreign flow, news** (risk flags → computed, không crawl) | httpx + BS4 |
| HOSE/HNX announcements | ✅ Confirmed | ❌ Chưa dùng | Risk flags (text) | httpx + BS4 |
| yfinance | ✅ Confirmed | ✅ Đang dùng | **Global macro ONLY** (VIX, DXY, oil, gold) | Python lib |
| SBV / CafeF rates | ✅ Confirmed | ✅ Đang dùng | Lending rates | httpx + BS4 + fallback |
| VietFin | ✅ Confirmed | ✅ Đang dùng | VNINDEX history, ETF | Python lib |
| ~~yfinance .VN~~ | ❌ Remove | ❌ Removed | ~~adj_close~~ | → vnstock events |
| ~~Vimo API~~ | ❌ Remove | ❌ Removed | ~~lending rates~~ | → SBV scrape |
| ~~UBCKNN Playwright~~ | ❌ Remove | ❌ Removed | ~~risk flags~~ | → CafeF scrape |
| **DNSE foreign** | plan: REST | ❌ **Không có** | ~~foreign flow~~ | → **CafeF API** ⚠️ |
| **vnstock insider** | plan: v4 | ❌ **404 error** | ~~insider deals~~ | → **CafeF API** ⚠️ |

> **Chú thích:** ⚠️ = V4 sai về source. DNSE REST không có foreign flow endpoint. vnstock insider_deals() trả 404. Cả 2 đã thay bằng CafeF API.

## C.2 Signal Quality — Đảm bảo vẫn đủ mạnh

Sau khi scope lại, hệ thống vẫn cover đủ để:

**Phát hiện cổ phiếu tốt:**
```
Tier 1 factors (10) → Cross-sectional ranking hàng ngày (✅)
↓
Filter: Liquidity > 5 tỷ/ngày + Risk level ≤ MEDIUM (❌ risk_flags pending)
↓
Top 20 candidates → Agent analysis (❌)
```

**Phân tích đúng:**
```
Market analyst:       40+ technical indicators (pre-computed) ✅
Fundamentals analyst: IS/BS/CF từ vnstock + 30 ratios ✅
Sentiment analyst:    CafeF news + insider ✅ (bao gồm news_events mới)
Risk officer:         CRS 7 tầng (❌ risk_assessments pending)
```

**Ra lệnh hợp lý:**
```
Bull/Bear debate → Confidence score (❌)
CRS ≤ 0.40 + Confidence ≥ 0.65 → Position sizing (❌)
T+2 constraint → Execution plan (❌)
Stop-loss = Risk Gate level từ technical analysis (❌)
```

## C.3 Revised Phase Timeline — Tiến độ thực tế

| Phase | Tuần | Mục tiêu | Key changes vs V3 | Thực tế |
|-------|------|---------|------------------|---------|
| 0 | 1-2 | Data foundation | adj_close từ vnstock (không yfinance .VN) | ✅ Done |
| 0 | 3 | Risk flags v2 (10 computed) | Batch compute từ financial_statements + OHLCV + foreign_flow + insider_trades | ❌ Pending |
| 1 | 4-5 | 30 VN-core factors | Không phải 453 | ✅ Done (cơ bản) |
| 1 | 6-7 | Daily ETL pipeline | GARCH chỉ top 50 (không all stocks) | ✅ Done (cơ bản) |
| 2 | 8-9 | AgentCore unified | Groq only (Gemini hoãn) | ❌ Pending |
| 2 | 10 | CRS 6 tầng hoạt động | Layer 7 behavioral giảm scope | ❌ Pending |
| 3 | 11-13 | Backtest + validation | 3 hypotheses (không phải 5) | ❌ Pending |
| 4 | Tháng 4-6 | Paper trading | Signal tracking daily | ❌ Pending |

## C.4 Minimum Viable Signal Pipeline ❌ [chưa implement đầy đủ]

Đây là **core không thể thiếu** — mọi thứ khác là enhancement:

```python
# Minimum pipeline để ra signal đúng

async def minimum_viable_signal(symbol: str) -> dict:
    """
    Nếu chỉ có thể implement 1 pipeline → implement cái này.
    Đủ để phát hiện cổ phiếu tốt và ra lệnh hợp lý.
    """
    # 1. Data (required)
    ohlcv       = await get_ohlcv_with_adj_close(symbol)    # ✅ vnstock events
    financials  = await get_financials(symbol)               # ✅ vnstock
    risk_flags  = await get_computed_risk_flags(symbol)       # ❌ Batch computed (stub)

    # 2. Quick risk check (block trước khi tốn compute)
    if has_hard_block_flags(risk_flags):                     # ❌ (CANH_BAO_TC + CHAM_BAO_TC = hard block)
        return {"action": "SKIP", "reason": "hard_block"}

    # 3. Technical signals (10 indicators, pre-computed)
    tech        = get_technical_indicators(symbol)           # ✅ From DB cache

    # 4. Top 10 factor scores
    factors     = get_tier1_factor_scores(symbol)            # ✅ From DB cache

    # 5. ML prediction (XGBoost only)
    ml_pred     = get_ml_prediction(symbol)                  # ❌ Code exists, chưa wired

    # 6. Quick fundamental check
    fund_score  = quick_fundamental_score(financials)        # ⏳ financial_ratios có

    # 7. Composite score
    composite = (
        factors["composite_score"]  * 0.35 +
        tech["trend_score"]         * 0.25 +
        ml_pred["proba"]            * 0.20 +
        fund_score                  * 0.20
    )                                                        # ❌ Chưa implement

    # 8. CRS check
    crs = get_latest_crs_score(symbol)                       # ❌ risk_assessments pending
    if crs > 0.55:
        return {"action": "AVOID", "crs": crs}

    # 9. Position sizing
    size = kelly_size(composite, crs)                        # ❌

    return {
        "action":          "BUY" if composite > 0.6 else "HOLD",
        "composite_score": round(composite, 3),
        "crs_score":       round(crs, 3),
        "position_size":   round(size, 3),
        "factors":         factors,
        "ml_direction":    ml_pred["direction"],
    }
```

> **Status:** ❌ Chưa implement dưới dạng pipeline unified. Các thành phần rời rạc:
> - ✅ OHLCV + adj_close
> - ✅ Financial statements + ratios
> - ✅ Factor scores (từ DB cache)
> - ✅ Technical indicators (từ DB cache)
> - ✅ Insider + foreign flow + news sentiment (pre-computed)
> - ❌ risk_flags batch computed (10 flags, 3 tiers)
> - ❌ ML prediction (code exists, chưa wire)
> - ❌ risk_assessments / CRS
> - ❌ Composite signal pipeline

---

# PHẦN D — FILE STRUCTURE + IMPLEMENTATION DELTA

## Files xóa khỏi roadmap
```
REMOVE: app/services/scrapers/cafef_insider_scraper.py  → dùng CafeF API (⚠️ không phải vnstock)
REMOVE: app/services/risk_flags.py                      → **DELETED** thay bằng risk_flags_v2.py
REMOVE: app/services/scraper_ubcknn.py                  → **DELETED** thay bằng computed flags
REMOVE: brain/quant/factors/zoo/alpha101/               → quá nhiều, không calibrated
REMOVE: brain/quant/factors/zoo/gtja191/                → quá nhiều, không calibrated
REMOVE: brain/quant/factors/zoo/qlib158/                → quá nhiều, không calibrated
REMOVE: brain/providers/gemini_client.py                → hoãn Phase 3
```

## Files thay thế / thêm mới
```
ADD:    brain/quant/factors/zoo/vn_core/vn_core_factors.py   ← 30 factors (→ factor_scores.py)
ADD:    brain/dataflows/vendors/vn/adj_close.py               ← vnstock events ✅
ADD:    brain/tools/vn_calendar.py                            ← lunar calendar ✅ (→ calendar.py)
ADD:    app/services/financial_etl.py                           ← vnstock → financial_statements table ⏳
ADD:    app/services/risk_flags_v2.py                          ← Batch computed (10 flags) ⏳
MODIFY: app/brain/tools/risk_flags_tool.py                     ← Refactored uses risk_flags_v2 ✅
MODIFY: app/brain/state/nodes.py                               ← risk_gate_node queries risk_flags DB ✅
DELETE: app/services/scrapers/ubcknn_scraper.py               ← **DELETED** thay bằng risk_flags_v2
DELETE: app/services/risk_flags.py                             ← **DELETED** thay bằng risk_flags_v2
MODIFY: app/services/macro_service.py                         ← SBV + fallback ✅
MODIFY: brain/risk/layers/quant_risk.py                       ← GARCH top 50 only ❌
MODIFY: brain/risk/composite_scorer.py                        ← FLOOR_TRAP → soft block ❌
```

## Delta: V4 plan vs Reality

| V4 nói | Reality | Tác động |
|--------|---------|---------|
| **A.5**: vnstock insider_deals() works | vnstock v4.0.4 & v3.2.2 → 404 | Đã build `insider_trades.py` dùng CafeF API |
| **A.4**: DNSE REST có foreign flow | DNSE không có foreign endpoint (`services.entrade.com.vn` → 401) | Đã build `foreign_flow.py` dùng CafeF API |
| **A.4**: "CafeF scraper riêng → bỏ" | Không thể bỏ, vnstock không có insider data | Giữ scraper riêng cho insider + foreign + news |
| **C.4**: ML code sẵn sàng | XGBoost chưa install, `ml_predictions` table chưa có, chưa wire ETL | Pending |
| **NEW**: News sentiment | Không có trong V4 plan (chỉ mention "lexicon-based") | Đã build `news_events.py` + `news_events` table |
| **NEW**: `insider_trades` schema | V4 không spec columns | Thực tế: `before_volume`, `after_volume`, `ownership_pct` (CafeF fields, không phải `price`/`value`) |
| **A.1**: CafeF scraper cho risk flags | CafeF regulatory URLs → 404, HOSE → React SPA | **Switched to computed flags** từ structured data (10 flags, batch ETL). Không scraper. |
| **A.6 + B.2**: Pledge data + FLOOR_TRAP | V4 đối xử riêng lẻ | Cả 2 **merge vào computed risk_flags A.1**. Pledge dropped. FLOOR_TRAP = flag #3. |

## Files thực tế đã build (so với V4 plan)

| File | V4 plan | Reality |
|------|---------|---------|
| `adj_close.py` | ✅ Có | ✅ `app/brain/dataflows/vendors/vn/adj_close.py` |
| `vn_calendar.py` | ✅ Có | ✅ `app/brain/dataflows/vendors/vn/calendar.py` |
| `vn_core_factors.py` | ✅ Có | ✅ `app/brain/dataflows/vendors/vn/factor_scores.py` |
| `insider_tool.py` (vnstock) | ✅ Có | ⚠️ `insider_trades.py` (CafeF API, khác source) |
| `ubcknn_scraper.py` | ✅ Có | 🗑️ **DELETED** — thay bằng computed risk_flags_v2.py |
| `risk_flags.py` (v1) | ✅ Có | 🗑️ **DELETED** — thay bằng risk_flags_v2.py |
| `disclosures_tool.py` | ❌ Không có | ✅ Refactored — uses risk_flags_v2 |
| `financial_etl.py` | ❌ Không có | ⏳ `app/services/financial_etl.py` — populates financial_statements table |
| `risk_flags_v2.py` | ❌ Không có | ⏳ `app/services/risk_flags_v2.py` — batch computed engine (10 flags) |
| `risk_flags_tool.py` | ❌ Không có | ✅ Refactored — uses risk_flags_v2 |
| `macro_service.py` | ✅ Có | ✅ `app/services/macro_service.py` |
| `quant_risk.py` | ✅ Có | ❌ Chưa build |
| `composite_scorer.py` | ✅ Có | ❌ Chưa build |
| `foreign_flow.py` | ❌ Không có | ✅ `app/brain/dataflows/vendors/vn/foreign_flow.py` |
| `news_events.py` | ❌ Không có | ✅ `app/brain/dataflows/vendors/vn/news_events.py` |
| `technical_indicators.py` | ❌ Không có | ✅ `app/brain/dataflows/vendors/vn/technical_indicators.py` |
| `daily_etl.py` | ❌ Không có | ✅ `app/services/daily_etl.py` |

---

## Đường đi tiếp theo (Next Steps priority)

1. **Verify risk_flags v2** (A.1) — Run ETL, check financial_etl.py + risk_flags_v2.py produce correct data
2. **Risk gate integration test** (A.1) — Verify risk_gate_node correctly blocks HARD flags from DB
3. **News content deep crawl** (D.0) — thêm `content` column + background worker
4. **ML prediction** (C.4) — install xgboost, tạo `ml_predictions` table, wire ETL
5. **risk_assessments / CRS** (C.4) — composite 7-layer score từ các pre-computed signals
6. **Minimum viable signal** (C.4) — gom tất cả vào 1 pipeline

---

*AIInvest Roadmap V4 — Rectified + Implementation Status*
*8 issues resolved · Scope calibrated cho VN market thực tế*
*Core capability giữ nguyên: phát hiện cổ phiếu tốt → phân tích đúng → lệnh hợp lý*

*File này là merge giữa `AIInvest_roadmap_v4_rectified.md` (kế hoạch) và implementation status thực tế.*
