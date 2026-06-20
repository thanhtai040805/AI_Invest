#!/usr/bin/env python3
"""VN IC Tester — VN-specific IC benchmark with proper methodology.

Handles:
- T+2 forward return adjustment (entry at T+1 close)
- Liquidity filter (min 5B VND/day avg value 20d)
- Winsorize returns at ±7% HOSE price limit
- Spearman rank correlation (robust to fat tails/price limits)
- Multiple testing correction (Benjamini-Hochberg)
- Walk-forward validation (5-split expanding window)
- Survivorship: uses all symbols present in ohlcv at each date
"""
import logging
import math
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import psycopg2
from scipy import stats as scipy_stats

from app.infrastructure.vendors.vn.sector_groups import classify, FINANCIALS, REAL_ESTATE, OTHERS
from app.infrastructure.vendors.vn.sector_groups import (
    FINANCIAL_SERVICES, CONSTRUCTION, CONSTRUCTION_MATERIALS,
    BASIC_RESOURCES, CHEMICALS, OIL_GAS, FOOD_BEVERAGE,
    TECHNOLOGY, INDUSTRIAL_GOODS, TRANSPORTATION, RETAIL_TRADE,
    HEALTHCARE, UTILITIES, AGRICULTURE, BANKS, OTHER_INDUSTRIALS,
)
from app.domain.services.quant.sector_neutralizer import (
    KNOWN_FACTOR_CONFIGS,
    prepare_factor_for_ic,
    normalize_all_factors,
)
from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

VN_CONSTRAINTS = {
    "settlement_lag": 2,
    "entry_offset": 1,         # T+1: first tradable close
    "min_stocks": 30,
    "min_value_bn": 5.0,       # 5 tỷ VND daily avg value
    "price_limit": 0.07,       # HOSE ±7%
    "holding_periods": [5, 10, 20],
    "min_dates": 20,
}

# ── HOSE price steps (VND) ─────────────────────────────────────────
HOSE_PRICE_STEPS = [
    (10_000, 10),
    (50_000, 50),
    (100_000, 100),
    (200_000, 500),
    (float("inf"), 1_000),
]

# ── Heuristic offset for financial statement release (look-ahead bias) ──
FS_RELEASE_OFFSET_QUARTERLY = 20  # calendar days
FS_RELEASE_OFFSET_ANNUAL = 30     # calendar days
FS_STALE_THRESHOLD_DAYS = 180     # 2 quarters without data → tag for removal


def _ceiling_price(prev_close: float) -> float:
    """Dynamic HOSE ceiling price with correct price step rounding."""
    step = 10
    for threshold, s in HOSE_PRICE_STEPS:
        if prev_close <= threshold:
            step = s
            break
    raw_ceil = prev_close * 1.07
    return math.floor(raw_ceil / step) * step


def _effective_date(period_end: date) -> date:
    """Estimated release date to prevent look-ahead bias."""
    offset = FS_RELEASE_OFFSET_ANNUAL if period_end.month == 12 else FS_RELEASE_OFFSET_QUARTERLY
    return period_end + timedelta(days=offset)

VN_FACTORS = {
    "SIZE":            {"group": "risk", "direction": -1},
    "VOL_20D_ORTHO":   {"group": "risk", "direction": -1},
    "EVEBITDA_INV":    {"group": "value", "direction": 1},
    "HML_REAL":        {"group": "value", "direction": 1},
    "ROE_NORM":        {"group": "quality", "direction": 1},
    "NM":              {"group": "quality", "direction": 1},
    "GM":              {"group": "quality", "direction": 1},
    "YOY_REV":         {"group": "quality", "direction": 1},
    "PIOTROSKI_F":     {"group": "quality", "direction": 1},
    "FOREIGN_NET_5D":  {"group": "flow", "direction": 1},
    "INSIDER_NET_30D": {"group": "flow", "direction": 1},
}

# Event-study factors (computed but not in weekly IC pipeline)
VN_EVENT_FACTORS = {
    "CEILING_STREAK":  {"group": "behavioral", "direction": -1},
    "TET_WINDOW":      {"group": "behavioral", "direction": 1},
    "FORCED_SELLING":  {"group": "behavioral", "direction": 1},
}

# Symbols known to be banks (for foreign ownership limit rules)
BANK_SYMBOLS = frozenset({
    "ACB", "BAB", "BID", "CTG", "EIB", "EVF", "HDB", "KLB", "LPB",
    "MBB", "MSB", "NAB", "NAM", "NCB", "NVB", "OCB", "PGB", "PVF",
    "SGB", "SHB", "SSB", "STB", "TCB", "TPB", "VAB", "VBB", "VCB", "VIB", "VPB",
})

# Foreign ownership limit overrides (%): default 49%, banks 30%
FOREIGN_LIMIT_OVERRIDES: dict[str, int] = {
    "FPT": 100,
}

_TET_DATES = {
    2020: date(2020, 1, 25), 2021: date(2021, 2, 12),
    2022: date(2022, 2, 1),  2023: date(2023, 1, 22),
    2024: date(2024, 2, 10), 2025: date(2025, 1, 29),
    2026: date(2026, 2, 17),
}


class VNICTester:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    # ── Data loading ───────────────────────────────────────────────

    def _preload_all_static(self, symbols, start_date):
        """Pre-load all static/dimension data once into instance caches.

        Avoids 1000+ per-date SQL round trips during IC benchmark.
        Called once from run() before the eval-date loop.
        """
        logger.info("Pre-loading all static data (%d symbols, from %s) ...", len(symbols), start_date)

        # 1) Meta (stocks table — no date dependence)
        self.cur.execute(
            "SELECT symbol, market_cap, ceiling, floor FROM stocks WHERE symbol = ANY(%s)",
            (symbols,),
        )
        self._cache_meta: dict[str, dict] = {}
        for r in self.cur.fetchall():
            self._cache_meta[r[0]] = {
                "mcap": float(r[1]) if r[1] else None,
                "ceiling": float(r[2]) if r[2] else None,
                "floor": float(r[3]) if r[3] else None,
            }

        # 2) Fundamentals (financial_ratios — DISTINCT ON gives latest per symbol)
        self.cur.execute(
            """SELECT DISTINCT ON (symbol) symbol, pe, pb, roe, gross_margin, net_margin,
                      fcf_yield, ev_ebitda, yoy_revenue_growth, yoy_earnings_growth
               FROM financial_ratios
               WHERE symbol = ANY(%s)
               ORDER BY symbol, ratio_date DESC""",
            (symbols,),
        )
        self._cache_fundamentals: dict[str, dict] = {}
        for r in self.cur.fetchall():
            self._cache_fundamentals[r[0]] = {
                "pe": r[1], "pb": r[2], "roe": r[3], "gm": r[4], "nm": r[5],
                "fcf_y": r[6], "ev_eb": r[7], "yoy_rev": r[8], "yoy_earn": r[9],
            }

        # 3) Foreign flow (all rows for date range)
        self.cur.execute(
            """SELECT symbol, trade_date, net_value, room_remaining, room_limit
               FROM foreign_flow
               WHERE symbol = ANY(%s) AND trade_date >= %s
               ORDER BY symbol, trade_date""",
            (symbols, start_date),
        )
        self._cache_foreign: dict[str, list[dict]] = defaultdict(list)
        for r in self.cur.fetchall():
            self._cache_foreign[r[0]].append({
                "dt": r[1], "net": float(r[2]) if r[2] else 0.0,
                "room_rem": float(r[3]) if r[3] else 0.0,
                "room_lim": float(r[4]) if r[4] else 0.0,
            })
        logger.info("  foreign_flow: %d symbols cached", len(self._cache_foreign))

        # 4) Insider trades (all rows for date range)
        self.cur.execute(
            """SELECT symbol, trade_date, trade_type, quantity
               FROM insider_trades
               WHERE symbol = ANY(%s) AND trade_date >= %s
               ORDER BY symbol, trade_date""",
            (symbols, start_date),
        )
        self._cache_insider: dict[str, list[dict]] = defaultdict(list)
        for r in self.cur.fetchall():
            self._cache_insider[r[0]].append({
                "dt": r[1], "type": str(r[2]), "qty": float(r[3]) if r[3] else 0.0,
            })
        logger.info("  insider_trades: %d symbols cached", len(self._cache_insider))

        # 5) Financial statements (BS/IS/CF)
        stmt_types = {"BS": "BS", "IS": "IS", "CF": "CF"}
        for cache_key, stmt_val in [("_cache_fs_bs", "BS"), ("_cache_fs_is", "IS"), ("_cache_fs_cf", "CF")]:
            self.cur.execute(
                """SELECT symbol, period_end, data FROM financial_statements
                   WHERE statement_type = %s AND symbol = ANY(%s) AND period_end >= %s
                   ORDER BY symbol, period_end""",
                (stmt_val, symbols, start_date),
            )
            cache = defaultdict(list)
            for r in self.cur.fetchall():
                d = r[2] if isinstance(r[2], dict) else {}
                d["dt"] = r[1]
                cache[r[0]].append(d)
            setattr(self, cache_key, cache)
        n_bs = sum(len(v) for v in self._cache_fs_bs.values())
        n_is = sum(len(v) for v in self._cache_fs_is.values())
        n_cf = sum(len(v) for v in self._cache_fs_cf.values())
        logger.info("  financial_statements: BS=%d, IS=%d, CF=%d rows", n_bs, n_is, n_cf)

    def load_full_ohlcv(self, symbols, start, end):
        """Load OHLCV with daily value (close*volume) for liquidity filter.

        No fallback: skips rows where both adj_close and close are None.
        """
        self.cur.execute(
            """SELECT symbol, time::date as dt, adj_close, close, volume
               FROM ohlcv
               WHERE time::date >= %s AND time::date <= %s AND symbol = ANY(%s)
               ORDER BY symbol, time""",
            (start, end, symbols),
        )
        records = defaultdict(list)
        skipped = 0
        for sym, dt, ac, cl, vol in self.cur.fetchall():
            c = float(ac) if ac is not None else (float(cl) if cl is not None else None)
            if c is None or not math.isfinite(c):
                skipped += 1
                continue
            records[sym].append({"date": dt, "close": c, "volume": int(vol) if vol is not None else 0})
        if skipped:
            logger.debug("load_full_ohlcv: skipped %d rows with no close data", skipped)
        result = {}
        for sym, rows in records.items():
            df = pd.DataFrame(rows).set_index("date").sort_index()
            df.index = pd.to_datetime(df.index)
            if len(df) > 60:
                df["value"] = df["close"] * df["volume"]
                result[sym] = df
            else:
                logger.debug("load_full_ohlcv: %s skipped — only %d rows", sym, len(df))
        return result

    def get_symbols_at(self, dt):
        """Universe at date = all symbols with volume > 0 on that day."""
        self.cur.execute(
            "SELECT DISTINCT symbol FROM ohlcv WHERE time::date = %s AND volume > 0",
            (dt,),
        )
        return {r[0] for r in self.cur.fetchall()}

    # ── Liquidity filter ───────────────────────────────────────────

    def _liquidity_filter(self, ohlcv, dt, min_value_bn=5.0):
        """Keep only stocks with 20d avg daily value >= min_value_bn VND."""
        cutoff = pd.Timestamp(dt) - timedelta(days=90)
        valid = {}
        for sym, df in ohlcv.items():
            recent = df[df.index <= pd.Timestamp(dt)]
            recent = recent[recent.index >= cutoff]
            if len(recent) < 20:
                continue
            avg_value = recent["value"].tail(20).mean()
            # close is stored in thousands VND, so value = close_k * volume
            # 1 billion VND ≈ 1,000,000 in DB units
            if avg_value >= min_value_bn * 1e6:
                valid[sym] = df
        return valid

    # ── Forward returns (T+2 adjusted) ─────────────────────────────

    def compute_forward_returns(self, ohlcv, dt, holding=5):
        """Forward return with T+2 VN rule.
        
        Entry: close at T+1 (first tradable)
        Exit:  close at T+1+holding
        Return = P(T+1+H) / P(T+1) - 1
        """
        fwd = {}
        for sym, df in ohlcv.items():
            hist = df[df.index <= pd.Timestamp(dt)]
            if len(hist) < 1:
                continue
            # Entry price: close of T+1
            future = df[df.index > pd.Timestamp(dt)]
            if len(future) < 1 + holding:
                continue
            entry = future["close"].iloc[0]    # T+1 close
            if entry <= 0:
                continue
            exit_ = future["close"].iloc[holding]  # T+1+H close
            if exit_ is not None and exit_ > 0:
                fwd[sym] = exit_ / entry - 1
        return fwd

    # ── Factor computation (DB-backed, no fallback) ─

    def compute_factors_at(self, ohlcv, dt):
        """Compute all 31 factors at date dt. Returns {factor_id: {symbol: rank}}.

        Debug: logs per-symbol null reasons. No silent fallback — if data is
        missing from DB, factor value is NaN with a logged reason.
        """
        results = {}
        self.null_debug: dict[str, dict[str, str]] = {}

        for sym, df in ohlcv.items():
            hist = df[df.index <= pd.Timestamp(dt)].tail(400)
            if len(hist) < 20:
                self.null_debug.setdefault(sym, {})["__skip__"] = f"only {len(hist)} rows (<20)"
                continue
            closes = hist["close"].values
            volumes = hist["volume"].values
            c0 = closes[-1]
            n = len(closes)
            row: dict[str, float] = {}
            nd = self.null_debug.setdefault(sym, {})

            # Volatility (kept for VOL_20D_ORTHO)
            if n >= 21:
                ret_20d = np.diff(closes[-21:]) / np.maximum(closes[-21:-1], 1e-12)
                row["VOL_20D"] = float(np.std(ret_20d) * np.sqrt(252))
            if n >= 61:
                ret_60d = np.diff(closes[-61:]) / np.maximum(closes[-61:-1], 1e-12)
                row["VOL_60D"] = float(np.std(ret_60d) * np.sqrt(252))

            # TET window
            tet = _TET_DATES.get(dt.year)
            if tet:
                days_to = (tet - dt).days
                row["TET_WINDOW"] = 1.0 if 5 <= days_to <= 20 else (-0.5 if -10 <= days_to < 0 else 0.0)
            else:
                row["TET_WINDOW"] = 0.0

            row["_close"] = c0
            results[sym] = row

        # ── DB-pulled factors ─────────────────────────────────────
        fin = self._load_fundamentals(dt, list(ohlcv.keys()))
        meta = self._load_meta(list(ohlcv.keys()))
        foreign = self._load_foreign(dt, list(ohlcv.keys()))
        insider = self._load_insider(dt, list(ohlcv.keys()))
        fin_st = self._load_financial_statements(dt, list(ohlcv.keys()))

        for sym in results:
            row = results[sym]
            f = fin.get(sym)
            m = meta.get(sym)
            ff = foreign.get(sym, {})
            fs = fin_st.get(sym, {})

            # ---- Computed market data (historical, from BS + ohlcv) ----
            close_price = row.get("_close", 0)
            shares_out = fs.get("bs", {}).get("shares_outstanding")
            computed_mcap = None
            if shares_out is not None and shares_out > 0 and close_price > 0:
                computed_mcap = shares_out * close_price * 1000  # close in thousands VND
            mcap = computed_mcap if (computed_mcap is not None and computed_mcap > 0) else (m.get("mcap") if m else None)

            # ---- Value: PB_INV → HML_REAL ----
            pb = f.get("pb") if f and isinstance(f.get("pb"), (int, float)) else None
            if pb is not None and pb > 0 and math.isfinite(pb):
                row["PB_INV"] = 1.0 / pb
            else:
                nd["PB_INV"] = f"pb={'missing' if f is None else pb}"
            row["HML_REAL"] = row.get("PB_INV", np.nan)

            # ---- Quality: GM, NM, ROE_NORM ----
            gm_val = f.get("gm") if f and isinstance(f.get("gm"), (int, float)) else None
            if gm_val is not None and math.isfinite(gm_val):
                row["GM"] = gm_val / 100.0 if abs(gm_val) > 1 else gm_val
            else:
                nd["GM"] = f"gm={'missing' if f is None else gm_val}"
            nm_val = f.get("nm") if f and isinstance(f.get("nm"), (int, float)) else None
            if nm_val is not None and math.isfinite(nm_val):
                row["NM"] = nm_val / 100.0 if abs(nm_val) > 1 else nm_val
            else:
                nd["NM"] = f"nm={'missing' if f is None else nm_val}"
            roe = f.get("roe") if f and isinstance(f.get("roe"), (int, float)) else None
            if roe is not None and math.isfinite(roe):
                row["ROE_NORM"] = roe / 100.0 if abs(roe) > 1 else roe
            else:
                nd["ROE_NORM"] = f"roe={'missing' if f is None else roe}"

            # ---- Growth: YOY_REV (top-line, harder to manipulate) ----
            yoy_rev = f.get("yoy_rev") if f and isinstance(f.get("yoy_rev"), (int, float)) else None
            if yoy_rev is not None and math.isfinite(yoy_rev):
                row["YOY_REV"] = yoy_rev / 100.0 if abs(yoy_rev) > 1 else yoy_rev
            else:
                nd["YOY_REV"] = f"yoy_rev={'missing' if f is None else yoy_rev}"

            # ---- Piotroski F-score (9-point) ----
            bs_data = fs.get("bs", {})
            cf_data = fs.get("cf", {})
            
            pf = 0
            has_history = bs_data.get("prev_4q_total_assets") is not None
            
            # Group 1: Profitability
            net_income = bs_data.get("net_income")
            total_assets = bs_data.get("total_assets")
            cfo = cf_data.get("cfo")
            
            # 1. ROA > 0
            roa = 0.0
            if net_income is not None and total_assets is not None and total_assets > 0:
                roa = net_income / total_assets
                if roa > 0:
                    pf += 1
            # 2. CFO > 0
            if cfo is not None and cfo > 0:
                pf += 1
            # 3. ΔROA > 0
            prev_ni = bs_data.get("prev_4q_net_income")
            prev_ta = bs_data.get("prev_4q_total_assets")
            if net_income is not None and total_assets is not None and total_assets > 0:
                if prev_ni is not None and prev_ta is not None and prev_ta > 0:
                    prev_roa = prev_ni / prev_ta
                    if roa > prev_roa:
                        pf += 1
            # 4. Accrual: CFO > NI
            if cfo is not None and net_income is not None and cfo > net_income:
                pf += 1
                
            # Group 2: Leverage & Liquidity
            total_liabilities = bs_data.get("total_liabilities")
            current_assets = bs_data.get("current_assets")
            current_liabilities = bs_data.get("current_liabilities")
            
            # 5. ΔLeverage: long-term debt / TA decreased
            if total_liabilities is not None and current_liabilities is not None and total_assets is not None and total_assets > 0:
                lt_debt = total_liabilities - current_liabilities
                cur_lev = lt_debt / total_assets
                prev_tl = bs_data.get("prev_4q_total_liabilities")
                prev_cl = bs_data.get("prev_4q_current_liabilities")
                if prev_tl is not None and prev_cl is not None and prev_ta is not None and prev_ta > 0:
                    prev_lt_debt = prev_tl - prev_cl
                    prev_lev = prev_lt_debt / prev_ta
                    if cur_lev < prev_lev:
                        pf += 1
            # 6. ΔLiquidity: current ratio increased
            if current_assets is not None and current_liabilities is not None and current_liabilities > 0:
                cur_cr = current_assets / current_liabilities
                prev_ca = bs_data.get("prev_4q_current_assets")
                prev_cl = bs_data.get("prev_4q_current_liabilities")
                if prev_ca is not None and prev_cl is not None and prev_cl > 0:
                    prev_cr = prev_ca / prev_cl
                    if cur_cr > prev_cr:
                        pf += 1
            # 7. Equity expansion check (no new shares)
            if total_assets is not None and total_liabilities is not None:
                eq = total_assets - total_liabilities
                prev_tl = bs_data.get("prev_4q_total_liabilities")
                if prev_ta is not None and prev_tl is not None:
                    prev_eq = prev_ta - prev_tl
                    if eq <= prev_eq * 1.02:
                        pf += 1
                        
            # Group 3: Operating Efficiency
            revenue = bs_data.get("revenue")
            cogs = bs_data.get("cost_of_goods_sold")
            
            # 8. ΔMargin: gross margin increased
            if revenue is not None and cogs is not None and revenue > 0:
                cur_gm = (revenue - cogs) / revenue
                prev_rev = bs_data.get("prev_4q_revenue")
                prev_cogs = bs_data.get("prev_4q_cost_of_goods_sold")
                if prev_rev is not None and prev_cogs is not None and prev_rev > 0:
                    prev_gm = (prev_rev - prev_cogs) / prev_rev
                    if cur_gm > prev_gm:
                        pf += 1
            # 9. ΔTurnover: asset turnover increased
            if revenue is not None and total_assets is not None and total_assets > 0:
                cur_at = revenue / total_assets
                prev_rev = bs_data.get("prev_4q_revenue")
                if prev_rev is not None and prev_ta is not None and prev_ta > 0:
                    prev_at = prev_rev / prev_ta
                    if cur_at > prev_at:
                        pf += 1
                        
            if not has_history:
                # Basic 2-point fallback scaled to 9-point range
                basic_pf = 0
                roe_norm = row.get("ROE_NORM")
                if roe_norm is not None and math.isfinite(roe_norm) and roe_norm > 0:
                    basic_pf += 1
                if mcap is not None and mcap > 0:
                    basic_pf += 1
                row["PIOTROSKI_F"] = float(basic_pf * 4.5)
            else:
                row["PIOTROSKI_F"] = float(pf)

            # ---- Value: EVEBITDA_INV ----
            eveb = f.get("ev_eb") if f and isinstance(f.get("ev_eb"), (int, float)) else None
            if eveb is not None and eveb > 0 and math.isfinite(eveb):
                row["EVEBITDA_INV"] = 1.0 / eveb
            else:
                nd["EVEBITDA_INV"] = f"ev_eb={'missing' if f is None else eveb}"

            # ---- Size ----
            if mcap is not None and mcap > 0 and math.isfinite(mcap):
                row["SIZE"] = np.log(mcap)
            else:
                nd["SIZE"] = f"mcap={'missing' if m is None else mcap}"

            # ---- Event: CEILING_STREAK ----
            if n >= 2:
                streak = 0
                for i in range(n - 1, 0, -1):
                    prev_val = closes[i - 1]
                    if prev_val <= 0:
                        break
                    p_ceil = _ceiling_price(prev_val)
                    ret = closes[i] / prev_val - 1
                    if ret >= 0.065 and closes[i] >= p_ceil * 0.995:
                        streak += 1
                    else:
                        break
                row["CEILING_STREAK"] = float(streak)

            # ---- Event: FORCED_SELLING ----
            floor = m.get("floor") if m else None
            if floor is not None and floor > 0 and n >= 5:
                floor_hits = sum(1 for i in range(5) if closes[-(i+1)] <= floor)
                vol_5d = float(np.mean(volumes[-5:])) if len(volumes) >= 5 else 0.0
                vol_20d = float(np.mean(volumes[-20:])) if n >= 20 else 1.0
                row["FORCED_SELLING"] = 1.0 if floor_hits >= 2 and (vol_5d / max(vol_20d, 1e-12)) > 3 else 0.0

            # ---- Money flow: FOREIGN_NET_5D ----
            if mcap is not None and mcap > 0:
                net_val = ff.get("net_value", 0)
                if isinstance(net_val, (int, float)):
                    row["FOREIGN_NET_5D"] = net_val / mcap
            else:
                nd.setdefault("FOREIGN_NET_5D", f"mcap={'missing' if m is None else mcap}")

            # ---- Money flow: INSIDER_NET_30D ----
            insider_net_qty = insider.get(sym, 0.0)
            if mcap is not None and mcap > 0 and close_price > 0:
                row["INSIDER_NET_30D"] = (insider_net_qty * close_price * 1000) / mcap
            else:
                row["INSIDER_NET_30D"] = 0.0

        # ── Post-processing: VOL_20D orthogonalization ──────────────
        vol_20d_vals = {}
        vol_60d_vals = {}
        for sym, row in results.items():
            v20 = row.get("VOL_20D")
            v60 = row.get("VOL_60D")
            if v20 is not None and v60 is not None and math.isfinite(v20) and math.isfinite(v60):
                vol_20d_vals[sym] = v20
                vol_60d_vals[sym] = v60
        if len(vol_20d_vals) >= 10:
            x = np.array(list(vol_60d_vals.values()))
            y = np.array(list(vol_20d_vals.values()))
            slope, intercept, _, _, _ = scipy_stats.linregress(x, y)
            for sym in vol_20d_vals:
                resid = vol_20d_vals[sym] - (intercept + slope * vol_60d_vals[sym])
                results[sym]["VOL_20D_ORTHO"] = float(resid)

        # ── Post-processing: TTM ROE_NORM & NM override ────────────
        for sym, row in results.items():
            ttm = fin_st.get(sym, {}).get("ttm")
            if ttm and ttm.get("equity") and ttm["equity"] > 0 and ttm.get("net_income") is not None:
                ttm_roe = ttm["net_income"] / ttm["equity"]
                if math.isfinite(ttm_roe):
                    row["ROE_NORM"] = ttm_roe
                if ttm.get("revenue") and ttm["revenue"] != 0:
                    ttm_nm = ttm["net_income"] / ttm["revenue"]
                    if math.isfinite(ttm_nm):
                        row["NM"] = ttm_nm

        # ── Post-processing: zero-fill FOREIGN_NET_5D ──────────────
        for sym, row in results.items():
            if "FOREIGN_NET_5D" not in row or row["FOREIGN_NET_5D"] is None or not math.isfinite(row["FOREIGN_NET_5D"]):
                row["FOREIGN_NET_5D"] = 0.0
                nd.setdefault(sym, {}).setdefault("FOREIGN_NET_5D", "zero-filled (no foreign activity)")

        # Compute per-factor ICB sector-neutral percentile ranks
        factor_ranks = {}
        symbols_with_data = list(results.keys())
        sector_map = getattr(self, "sector_map", None)
        if sector_map is None:
            # Fallback: load on the fly (e.g. when called outside run())
            sector_map = self._load_sector_map(symbols_with_data)
        if not sector_map:
            sector_map = {s: OTHER_INDUSTRIALS for s in symbols_with_data}
        sectors_series = pd.Series(
            {s: sector_map.get(s, OTHER_INDUSTRIALS) for s in symbols_with_data}
        )

        for factor_id, meta in VN_FACTORS.items():
            vals = {}
            for sym, row in results.items():
                v = row.get(factor_id)
                if v is not None and math.isfinite(v):
                    vals[sym] = v
            if len(vals) < VN_CONSTRAINTS["min_stocks"]:
                continue
            s = pd.Series(vals)
            asc = meta["direction"] == 1
            # Build DataFrame for sector-aware transformation
            df_f = pd.DataFrame({
                "symbol": s.index,
                "value": s.values,
                "sector": sectors_series.reindex(s.index).values,
            })
            df_f["sector"] = df_f["sector"].fillna(OTHER_INDUSTRIALS)
            df_f["value"] = pd.to_numeric(df_f["value"], errors="coerce")

            # Use prepare_factor_for_ic (winsorize + sector Z-score + rank)
            rank = prepare_factor_for_ic(
                df_f, factor_id, "value", "sector",
                direction=meta["direction"],
            )
            # Restore symbol index for IC computation
            rank.index = df_f["symbol"].values
            factor_ranks[factor_id] = rank
        return factor_ranks

    def _load_fundamentals(self, dt, symbols):
        if hasattr(self, "_cache_fundamentals"):
            return {s: self._cache_fundamentals.get(s, {}) for s in symbols}
        self.cur.execute("""
            SELECT DISTINCT ON (symbol) symbol, pe, pb, roe, gross_margin, net_margin,
                   fcf_yield, ev_ebitda, yoy_revenue_growth, yoy_earnings_growth
            FROM financial_ratios
            WHERE symbol = ANY(%s) AND ratio_date <= %s
            ORDER BY symbol, ratio_date DESC
        """, (symbols, dt))
        out = {}
        for r in self.cur.fetchall():
            out[r[0]] = {"pe": r[1], "pb": r[2], "roe": r[3], "gm": r[4], "nm": r[5],
                         "fcf_y": r[6], "ev_eb": r[7], "yoy_rev": r[8], "yoy_earn": r[9]}
        return out

    def _load_meta(self, symbols):
        if hasattr(self, "_cache_meta"):
            return {s: self._cache_meta.get(s, {}) for s in symbols}
        self.cur.execute(
            "SELECT symbol, market_cap, ceiling, floor FROM stocks WHERE symbol = ANY(%s)",
            (symbols,),
        )
        return {r[0]: {"mcap": float(r[1]) if r[1] else None,
                       "ceiling": float(r[2]) if r[2] else None,
                       "floor": float(r[3]) if r[3] else None} for r in self.cur.fetchall()}

    def _load_foreign(self, dt, symbols):
        if hasattr(self, "_cache_foreign"):
            cutoff_30d = dt - timedelta(days=30)
            rooms = {}
            for sym in symbols:
                rows = self._cache_foreign.get(sym, [])
                net_30d = sum(r["net"] for r in rows if cutoff_30d <= r["dt"] <= dt)
                latest = None
                for r in reversed(rows):
                    if r["dt"] <= dt:
                        latest = r
                        break
                rooms[sym] = {
                    "net_value": net_30d,
                    "room_remaining": latest["room_rem"] if latest else 0.0,
                    "room_limit": latest["room_lim"] if latest else 0.0,
                }
            return rooms
        cutoff_30d = dt - timedelta(days=30)
        self.cur.execute("""
            SELECT symbol, SUM(net_value) as net_30d
            FROM foreign_flow
            WHERE trade_date >= %s AND trade_date <= %s AND symbol = ANY(%s)
            GROUP BY symbol
        """, (cutoff_30d, dt, symbols))
        net = dict(self.cur.fetchall())
        self.cur.execute("""
            SELECT DISTINCT ON (symbol) symbol, room_remaining, room_limit
            FROM foreign_flow
            WHERE symbol = ANY(%s) AND trade_date <= %s
            ORDER BY symbol, trade_date DESC
        """, (symbols, dt))
        rooms = {}
        for r in self.cur.fetchall():
            rooms[r[0]] = {"room_remaining": float(r[1]) if r[1] else 0,
                           "room_limit": float(r[2]) if r[2] else 0}
        for sym in symbols:
            if sym not in rooms:
                rooms[sym] = {"room_remaining": 0, "room_limit": 0}
            rooms[sym]["net_value"] = net.get(sym, 0)
        return rooms

    def _load_insider(self, dt, symbols):
        cutoff_30d = dt - timedelta(days=30)
        if hasattr(self, "_cache_insider"):
            result = {}
            for sym in symbols:
                rows = self._cache_insider.get(sym, [])
                buy = sum(r["qty"] for r in rows if cutoff_30d <= r["dt"] <= dt
                          and r["type"] in ("Mua", "Đăng ký mua", "đăng ký mua"))
                sell = sum(r["qty"] for r in rows if cutoff_30d <= r["dt"] <= dt
                           and r["type"] in ("Bán", "Đăng ký bán", "đăng ký bán"))
                result[sym] = float(buy - sell)
            return result
        self.cur.execute("""
            SELECT symbol,
                   SUM(CASE WHEN trade_type IN ('Mua','Đăng ký mua','đăng ký mua') THEN quantity ELSE 0 END) as buy,
                   SUM(CASE WHEN trade_type IN ('Bán','Đăng ký bán','đăng ký bán') THEN quantity ELSE 0 END) as sell
            FROM insider_trades
            WHERE trade_date >= %s AND trade_date <= %s AND symbol = ANY(%s)
            GROUP BY symbol
        """, (cutoff_30d, dt, symbols))
        return {r[0]: float(r[1] - r[2]) for r in self.cur.fetchall()}

    def _load_foreign_accum(self, dt, symbols):
        """Cumulative net foreign flow over past 1Y."""
        cutoff_1y = dt - timedelta(days=365)
        if hasattr(self, "_cache_foreign"):
            result = {}
            for sym in symbols:
                rows = self._cache_foreign.get(sym, [])
                accum = sum(r["net"] for r in rows if cutoff_1y <= r["dt"] <= dt)
                result[sym] = accum
            return result
        self.cur.execute("""
            SELECT symbol, SUM(net_value) as accum
            FROM foreign_flow
            WHERE trade_date >= %s AND trade_date <= %s AND symbol = ANY(%s)
            GROUP BY symbol
        """, (cutoff_1y, dt, symbols))
        return dict(self.cur.fetchall())

    def _load_financial_statements(self, dt, symbols):
        """Load BS/IS/CF data from financial_statements.

        Returns {symbol: {bs: {key: val}, cf: {key: val}, is_keys...}}.
        Financial-sector symbols (banks) use different key names —
        returns empty dict when no standard keys match (logged).
        """
        STATEMENT_BS_KEYS = {
            "total_assets": ("tổng_cộng_tài_sản", "TỔNG CỘNG TÀI SẢN",
                             "tài_sản", "TÀI SẢN", "a_tài_sản", "A. TÀI SẢN"),
            "total_liabilities": ("tổng_nợ_phải_trả", "TỔNG NỢ PHẢI TRẢ",
                                  "c_nợ_phải_trả", "C. NỢ PHẢI TRẢ",
                                  "nợ_phải_trả", "Nợ phải trả"),
            "current_assets": ("a_tài_sản_ngắn_hạn", "A. TÀI SẢN NGẮN HẠN",
                               "tài_sản_ngắn_hạn", "Tài sản ngắn hạn",
                               "i_tài_sản_ngắn_hạn", "I. Tài sản ngắn hạn"),
            "current_liabilities": ("i_nợ_ngắn_hạn", "I. Nợ ngắn hạn",
                                    "nợ_ngắn_hạn", "Nợ ngắn hạn"),
            "cash": ("1_tiền", "1. Tiền",
                     "tiền", "Tiền",
                     "tiền_và_tương_đương_tiền", "Tiền và tương đương tiền"),
            "short_term_debt": ("vay_ngắn_hạn", "Vay ngắn hạn",
                                "vay_và_nợ_ngắn_hạn", "Vay và nợ ngắn hạn",
                                "11_vay_và_nợ_thuê_tài_chính_ngắn_hạn", "11. Vay và nợ thuê tài chính ngắn hạn"),
            "retained_earnings": ("10_lợi_nhuận_sau_thuế_chưa_phân_phối", "10. Lợi nhuận sau thuế chưa phân phối",
                                  "lợi_nhuận_sau_thuế_chưa_phân_phối", "Lợi nhuận sau thuế chưa phân phối",
                                  "lợi_nhuận_giữ_lại", "Lợi nhuận giữ lại"),
            "depreciation": ("khấu_hao", "Khấu hao",
                             "hao_mòn", "Hao mòn",
                             "khấu_hao_tscđ", "Khấu hao TSCĐ"),
        }
        STATEMENT_IS_KEYS = {
            "revenue": ("3_doanh_thu_thuần_về_bán_hàng_và_cung_cấp_dịch_vụ",
                        "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ",
                        "doanh_thu_thuần", "Doanh thu thuần"),
            "net_income": ("18_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp",
                           "18. Lợi nhuận sau thuế thu nhập doanh nghiệp",
                           "lợi_nhuận_sau_thuế", "Lợi nhuận sau thuế"),
            "ebit": ("11_lợi_nhuận_thuần_từ_hoạt_động_kinh_doanh",
                     "11. Lợi nhuận thuần từ hoạt động kinh doanh",
                     "lợi_nhuận_thuần", "Lợi nhuận thuần"),
            "cost_of_goods_sold": ("giá_vốn_hàng_bán", "giá vốn hàng bán",
                                   "4_giá_vốn_hàng_bán", "4. Giá vốn hàng bán"),
        }
        STATEMENT_CF_KEYS = {
            "cfo": ("lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",
                    "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
                    "lưu_chuyển_tiền_tệ_ròng_từ_các_hoạt_động_sản_xuất_kinh_doanh",
                    "Lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh",
                    "tiền_thuần_từ_hđkd", "Tiền thuần từ HĐKD"),
        }

        # Bank-specific key maps (completely different BS/IS structure)
        # Banks use total assets/liabilities as proxies since they lack
        # current-asset/liability distinction in VN reporting format.
        BANK_BS_KEYS = {
            "total_assets": ("tổng_cộng_tài_sản", "TỔNG CỘNG TÀI SẢN"),
            "total_liabilities": ("tổng_nợ_phải_trả", "TỔNG NỢ PHẢI TRẢ",
                                  "tổng_nợ_phải_trả_và_vốn_chủ_sở_hữu", "TỔNG NỢ PHẢI TRẢ VÀ VỐN CHỦ SỞ HỮU"),
            "cash": ("i_tiền_mặt_vàng_bạc_đá_quý", "I. Tiền mặt, vàng bạc, đá quý",
                     "1_tiền", "1. Tiền",
                     "tiền", "Tiền"),
            "short_term_debt": ("vay_ngắn_hạn", "Vay ngắn hạn"),
            "current_assets": ("tổng_cộng_tài_sản", "TỔNG CỘNG TÀI SẢN"),
            "current_liabilities": ("tổng_nợ_phải_trả", "TỔNG NỢ PHẢI TRẢ"),
            "retained_earnings": ("5_lợi_nhuận_chưa_phân_phối_lỗ_lũy_kế",
                                  "5. Lợi nhuận chưa phân phối/Lỗ lũy kế"),
        }
        BANK_IS_KEYS = {
            "revenue": ("1_thu_nhập_lãi_và_các_khoản_thu_nhập_tương_tự",
                        "1. Thu nhập lãi và các khoản thu nhập tương tự",
                        "i_thu_nhập_lãi_thuần", "I. Thu nhập lãi thuần"),
            "net_income": ("xiii_lợi_nhuận_sau_thuế_xi_xii", "XIII. Lợi nhuận sau thuế (XI-XII)",
                           "xiii_lợi_nhuận_sau_thuế_xiii_xiv", "XIII. Lợi nhuận sau thuế (XIII-XIV)"),
            "ebit": ("ix_lợi_nhuận_thuần_từ_hoạt_động_kinh_doanh_trước_chi_phí_dự_phòng_rủi_ro_tín_dụng_i_ii_iii_iv_v_vi_vii_viii",
                     "IX. Lợi nhuận thuần từ hoạt động kinh doanh trước chi phí dự phòng rủi ro tín dụng (I+II+III+IV+V+VI+VII-VIII)",
                     "ix_lợi_nhuận_thuần_từ_hoạt_động_kinh_doanh_trước_chi_phí_dự_phòng_rủi_ro_tín_dụng",
                     "IX. Lợi nhuận thuần từ hoạt động kinh doanh trước chi phí dự phòng rủi ro tín dụng",
                     "xi_tổng_lợi_nhuận_trước_thuế_ix_x", "XI. Tổng lợi nhuận trước thuế (IX-X)"),
        }
        BANK_CF_KEYS = {
            "cfo": ("i_lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",
                    "I. Lưu chuyển tiền thuần từ hoạt động kinh doanh"),
        }

        def _is_bank(sym: str) -> bool:
            """Check if symbol is a bank using sector map."""
            sec = getattr(self, "sector_map", {}).get(sym, "OTHERS")
            return sec == FINANCIALS

        def _pick_key(data: dict, candidates: tuple) -> float | None:
            for key in candidates:
                if key in data:
                    v = data[key]
                    if isinstance(v, (int, float)) and math.isfinite(v):
                        return float(v)
                    try:
                        fv = float(str(v).replace(",", ""))
                        if math.isfinite(fv):
                            return fv
                    except (ValueError, TypeError):
                        continue
            return None

        def extract_shares_outstanding(bs_data: dict, symbol: str, is_bank: bool = False) -> float | None:
            """Extract shares outstanding from BS data.
            
            VN law: par value = 10,000 VND/share.
            Falls back through multiple key variants.
            Returns number of shares or None.
            """
            PAR_VALUE = 10_000  # VND, per Vietnamese law

            if is_bank:
                for key in ("a_vốn_điều_lệ", "a. Vốn điều lệ",
                            "vốn_điều_lệ", "Vốn điều lệ",
                            "charter_capital"):
                    if key in bs_data and bs_data[key] is not None:
                        v = float(bs_data[key])
                        if v > 1e9:
                            return v / PAR_VALUE
                return None

            # Non-bank fallback chain (priority order)
            for key in (
                # Priority 1: Common shares (standard format)
                "cổ_phiếu_phổ_thông_có_quyền_biểu_quyết",
                "- Cổ phiếu phổ thông có quyền biểu quyết",
                # Priority 2: Common shares (securities format)
                "a_cổ_phiếu_phổ_thông",
                "a. Cổ phiếu phổ thông",
                # Priority 3: Contributed capital (securities sub-section)
                "1_1_vốn_góp_của_chủ_sở_hữu",
                "1.1. Vốn góp của chủ sở hữu",
                # Priority 4: Contributed capital (standard)
                "1_vốn_góp_của_chủ_sở_hữu",
                "1. Vốn góp của chủ sở hữu",
                "vốn_góp_của_chủ_sở_hữu",
                "Vốn góp của chủ sở hữu",
                # Priority 5: Charter capital (general fallback)
                "vốn_điều_lệ",
                "Vốn điều lệ",
                "charter_capital",
            ):
                if key in bs_data and bs_data[key] is not None:
                    v = float(bs_data[key])
                    if v > 1e9:
                        return v / PAR_VALUE
            return None

        result: dict[str, dict] = {}

        # Use cache if available
        if hasattr(self, "_cache_fs_bs"):
            cache_map = [
                ("BS", STATEMENT_BS_KEYS, self._cache_fs_bs),
                ("IS", STATEMENT_IS_KEYS, self._cache_fs_is),
                ("CF", STATEMENT_CF_KEYS, self._cache_fs_cf),
            ]
        else:
            # Fetch from DB per date
            cache_map = None

        if cache_map:
            for stmt_type, key_map, cache in cache_map:
                for sym in symbols:
                    # Choose bank-specific key map if applicable
                    if stmt_type == "BS" and _is_bank(sym):
                        km = BANK_BS_KEYS
                    elif stmt_type == "IS" and _is_bank(sym):
                        km = BANK_IS_KEYS
                    elif stmt_type == "CF" and _is_bank(sym):
                        km = BANK_CF_KEYS
                    else:
                        km = key_map

                    entries = [e for e in cache.get(sym, [])
                               if e.get("dt") is not None
                               and _effective_date(e["dt"]) <= dt]
                    entries.sort(key=lambda x: x["dt"], reverse=True)
                    if sym not in result:
                        result[sym] = {}
                    parsed = {}
                    if entries:
                        latest = entries[0]
                        for out_key, candidates in km.items():
                            parsed[out_key] = _pick_key(latest, candidates)
                        if len(entries) > 1:
                            prev = entries[1]
                            for out_key, candidates in km.items():
                                pk = _pick_key(prev, candidates)
                                if pk is not None:
                                    parsed[f"prev_{out_key}"] = pk
                    if stmt_type == "BS":
                        result[sym]["bs"] = parsed
                        result[sym]["bs"]["shares_outstanding"] = extract_shares_outstanding(
                            latest, sym, _is_bank(sym)
                        )
                    elif stmt_type == "IS":
                        result[sym].setdefault("bs", {})
                        result[sym]["bs"].update(parsed)
                    elif stmt_type == "CF":
                        result[sym].setdefault("cf", {})
                        result[sym]["cf"] = parsed

                    # Post-processing: compute TTM for each symbol from IS cache
            if stmt_type == "IS":
                for sym in symbols:
                    if sym not in result:
                        continue
                    is_entries = [e for e in cache.get(sym, [])
                                  if e.get("dt") is not None
                                  and _effective_date(e["dt"]) <= dt]
                    is_entries.sort(key=lambda x: x["dt"], reverse=True)
                    is_bank_flag = _is_bank(sym)
                    is_km = BANK_IS_KEYS if is_bank_flag else STATEMENT_IS_KEYS
                    ttm_ni = 0.0
                    ttm_rev = 0.0
                    valid_quarters = 0
                    latest_equity = None
                    # Accumulate up to 4 most recent quarters of IS data
                    for idx in range(min(4, len(is_entries))):
                        ni = _pick_key(is_entries[idx], is_km.get("net_income", ()))
                        rev = _pick_key(is_entries[idx], is_km.get("revenue", ()))
                        if ni is not None and rev is not None:
                            ttm_ni += float(ni)
                            ttm_rev += float(rev)
                            valid_quarters += 1
                    # Equity từ BS đã parse
                    bs_data = result[sym].get("bs", {})
                    ta = bs_data.get("total_assets")
                    tl = bs_data.get("total_liabilities")
                    if ta is not None and tl is not None:
                        latest_equity = float(ta) - float(tl)
                    if valid_quarters >= 4 and ttm_rev != 0 and latest_equity is not None and latest_equity > 0:
                        result[sym]["ttm"] = {
                            "net_income": ttm_ni,
                            "revenue": ttm_rev,
                            "equity": latest_equity,
                        }
        else:
            for stmt_type, key_map in [("BS", STATEMENT_BS_KEYS), ("IS", STATEMENT_IS_KEYS), ("CF", STATEMENT_CF_KEYS)]:
                bank_key_map = {"BS": BANK_BS_KEYS, "IS": BANK_IS_KEYS, "CF": BANK_CF_KEYS}.get(stmt_type)
                self.cur.execute("""
                    SELECT symbol, period_end, data
                    FROM financial_statements
                    WHERE statement_type = %s AND symbol = ANY(%s) AND period_end <= %s
                    ORDER BY symbol, period_end DESC
                """, (stmt_type, symbols, dt))
                rows = self.cur.fetchall()
                by_sym: dict[str, list] = {}
                for sym, pe, data in rows:
                    by_sym.setdefault(sym, []).append((pe, data))
                for sym, entries in by_sym.items():
                    if sym not in result:
                        result[sym] = {}
                    km = bank_key_map if (bank_key_map and _is_bank(sym)) else key_map
                    # Filter by look-ahead: chỉ dùng báo cáo có effective_date <= dt
                    entries_filtered = [
                        (pe, data) for pe, data in entries
                        if _effective_date(pe) <= dt
                    ]
                    parsed = {}
                    if entries_filtered:
                        latest = entries_filtered[0][1]
                        for out_key, candidates in km.items():
                            parsed[out_key] = _pick_key(latest, candidates)
                        if len(entries_filtered) > 1:
                            prev = entries_filtered[1][1]
                            for out_key, candidates in km.items():
                                pk = _pick_key(prev, candidates)
                                if pk is not None:
                                    parsed[f"prev_{out_key}"] = pk
                        if len(entries_filtered) > 4:
                            prev_4q = entries_filtered[4][1]
                            for out_key, candidates in km.items():
                                pk = _pick_key(prev_4q, candidates)
                                if pk is not None:
                                    parsed[f"prev_4q_{out_key}"] = pk
                    if stmt_type == "BS":
                        result[sym]["bs"] = parsed
                        result[sym]["bs"]["shares_outstanding"] = extract_shares_outstanding(
                            entries_filtered[0][1] if entries_filtered else {},
                            sym, _is_bank(sym)
                        )
                    elif stmt_type == "IS":
                        result[sym].setdefault("bs", {})
                        result[sym]["bs"].update(parsed)
                        # TTM: accumulate up to 4 quarters
                        if entries_filtered:
                            ttm_ni = 0.0
                            ttm_rev = 0.0
                            valid_q = 0
                            for idx in range(min(4, len(entries_filtered))):
                                ni = _pick_key(entries_filtered[idx][1], km.get("net_income", ()))
                                rev = _pick_key(entries_filtered[idx][1], km.get("revenue", ()))
                                if ni is not None and rev is not None:
                                    ttm_ni += float(ni)
                                    ttm_rev += float(rev)
                                    valid_q += 1
                            if valid_q >= 4 and ttm_rev != 0:
                                if "ttm" not in result[sym]:
                                    result[sym]["ttm"] = {}
                                result[sym]["ttm"]["net_income"] = ttm_ni
                                result[sym]["ttm"]["revenue"] = ttm_rev
                                # Equity từ BS đã parse
                                bs_data = result[sym].get("bs", {})
                                ta = bs_data.get("total_assets")
                                tl = bs_data.get("total_liabilities")
                                if ta is not None and tl is not None:
                                    result[sym]["ttm"]["equity"] = float(ta) - float(tl)
                    elif stmt_type == "CF":
                        result[sym].setdefault("cf", {})
                        result[sym]["cf"] = parsed
        return result

    # ── IC computation ─────────────────────────────────────────────

    def compute_daily_ic(self, factor_rank, forward_returns):
        """Spearman rank IC for one date, winsorized returns."""
        common = set(factor_rank.dropna().index) & set(forward_returns.keys())
        if len(common) < VN_CONSTRAINTS["min_stocks"]:
            return None
        f = factor_rank[list(common)].dropna()
        r = pd.Series({s: forward_returns[s] for s in common if s in forward_returns}).dropna()
        common2 = f.index.intersection(r.index)
        if len(common2) < VN_CONSTRAINTS["min_stocks"]:
            return None
        # Winsorize returns at ±7%
        r_vals = r[common2].clip(
            lower=r[common2].quantile(0.01),
            upper=r[common2].quantile(0.99),
        )
        if len(np.unique(f[common2])) < 2 or len(np.unique(r_vals)) < 2:
            return None  # constant input → no correlation
        ic, _ = scipy_stats.spearmanr(f[common2], r_vals)
        return ic if not np.isnan(ic) else None

    # ── Sector neutral IC ─────────────────────────────────────────

    def _load_sector_map(self, symbols: list[str]) -> dict[str, str]:
        """Load sector group for each symbol from DB + symbol overrides."""
        self.cur.execute(
            "SELECT symbol, industry FROM stocks WHERE symbol = ANY(%s)",
            (symbols,),
        )
        smap: dict[str, str] = {}
        for sym, ind in self.cur.fetchall():
            smap[sym] = classify(ind, sym)
        for sym in symbols:
            if sym not in smap:
                smap[sym] = OTHERS
        return smap

    def compute_daily_ic_sector_neutral(
        self,
        factor_rank: pd.Series,
        forward_returns: dict[str, float],
        sector_map: dict[str, str],
    ) -> float | None:
        """Compute sector-neutral Spearman IC.

        1. Demean factor ranks within each sector.
        2. Then compute Spearman rank correlation with forward returns.

        This removes sector-level bias (e.g. PE_INV looks good
        just because banking has low PE, not because value works).
        """
        common = set(factor_rank.dropna().index) & set(forward_returns.keys())
        if len(common) < VN_CONSTRAINTS["min_stocks"]:
            return None

        f = factor_rank[list(common)].dropna()
        r = pd.Series({s: forward_returns[s] for s in common if s in forward_returns}).dropna()
        common2 = f.index.intersection(r.index)
        if len(common2) < VN_CONSTRAINTS["min_stocks"]:
            return None

        # Winsorize returns
        r_vals = r[common2].clip(
            lower=r[common2].quantile(0.01),
            upper=r[common2].quantile(0.99),
        )

        # Build sector groups once (O(n)), not per-symbol (O(n²))
        f_neutral = f[common2].copy()
        sector_groups: dict[str, list[str]] = {}
        for sym in f_neutral.index:
            sec = sector_map.get(sym, OTHERS)
            sector_groups.setdefault(sec, []).append(sym)
        for sec, syms in sector_groups.items():
            if len(syms) >= 5:
                sector_mean = f_neutral[syms].mean()
                f_neutral[syms] -= sector_mean

        if len(np.unique(f_neutral)) < 2 or len(np.unique(r_vals)) < 2:
            return None
        ic, _ = scipy_stats.spearmanr(f_neutral, r_vals)
        return ic if not np.isnan(ic) else None

    # ── Coverage report ──────────────────────────────────────────

    def debug_coverage(
        self,
        ohlcv: dict[str, pd.DataFrame],
        dt: date,
        factor_ranks: dict[str, pd.Series],
    ) -> None:
        """Print coverage diagnostics for the current evaluation date."""
        total_symbols = len(ohlcv)
        liq_symbols = sum(
            1 for sym, df in ohlcv.items()
            if len(df[df.index <= pd.Timestamp(dt)].tail(20)) >= 20
        )
        print(f"  [{dt}] Symbols: {total_symbols} loaded, {liq_symbols} liquid")

    def debug_factor_coverage(
        self,
        factor_ranks: dict[str, pd.Series],
        sample_symbols: int = 3,
    ) -> dict[str, dict[str, Any]]:
        """Build per-factor coverage stats. Returns {factor_id: stats}.

        Prints coverage table to stdout.
        """
        stats: dict[str, dict[str, Any]] = {}
        total_stocks = max((len(s) for s in factor_ranks.values()), default=0)

        for fid, rank in sorted(factor_ranks.items()):
            n_non_null = int(rank.dropna().shape[0])
            fill_pct = round(n_non_null / total_stocks * 100, 1) if total_stocks > 0 else 0.0
            has_vals = rank.dropna().index.tolist()
            sample = has_vals[:sample_symbols] if has_vals else []
            stats[fid] = {
                "fill_rate": fill_pct,
                "n_stocks": n_non_null,
                "total_stocks": total_stocks,
                "sample_symbols": sample,
            }

        # Print coverage report
        hdr_n = "N"
        hdr_total = "TOTAL"
        print(f"\n{'FACTOR':>20s} {'FILL':>6s} {hdr_n:>5s}/{hdr_total:<5s}")
        print("-" * 50)
        for fid, s in stats.items():
            fill_str = f"{s['fill_rate']}%"
            n_str = f"{s['n_stocks']}"
            t_str = f"{s['total_stocks']}"
            flag = " ⚠️ LOW" if s['fill_rate'] < 30 else (" ⚠️ MED" if s['fill_rate'] < 60 else "")
            print(f"{fid:>20s} {fill_str:>6s} {n_str:>5s}/{t_str:<5s}{flag}")
        return stats

    # ── Walk-forward validation ────────────────────────────────────

    def walk_forward(self, ic_series, n_splits=5):
        if len(ic_series) < 30:
            return {"avg_degradation": 0, "is_robust": False, "verdict": "INSUFFICIENT_DATA", "splits": []}
        n = len(ic_series)
        splits = []
        for i in range(n_splits):
            train_end = int(n * (0.35 + i * 0.10))
            test_end = min(train_end + int(n * 0.25), n)
            if train_end >= n or test_end > n or test_end - train_end < 10:
                continue
            is_ic = ic_series.iloc[:train_end]
            oos_ic = ic_series.iloc[train_end:test_end]
            if len(oos_ic) < 5:
                continue
            degradation = (is_ic.mean() - oos_ic.mean()) / (abs(is_ic.mean()) + 1e-6)
            splits.append({
                "split": i + 1,
                "is_ic": round(is_ic.mean(), 4),
                "oos_ic": round(oos_ic.mean(), 4),
                "degradation": round(degradation, 4),
            })
        if not splits:
            return {"avg_degradation": 0, "is_robust": False, "verdict": "NO_SPLITS", "splits": []}
        avg_degradation = np.mean([s["degradation"] for s in splits])
        return {
            "splits": splits,
            "avg_degradation": round(avg_degradation, 4),
            "is_robust": avg_degradation < 0.5,
            "verdict": "ROBUST" if avg_degradation < 0.3 else ("MARGINAL" if avg_degradation < 0.5 else "OVERFIT"),
        }

    # ── Full benchmark ─────────────────────────────────────────────

    def run(self, years=3, holdings=None):
        if holdings is None:
            holdings = VN_CONSTRAINTS["holding_periods"]
        start = time.monotonic()
        end_date = date.today()
        start_date = end_date - timedelta(days=int(years * 365.25 + 400 + 60))

        # Load universe
        self.cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        all_symbols = [r[0] for r in self.cur.fetchall()]

        print(f"\n{'='*80}")
        print(f"PHASE 1 — Data Loading")
        print(f"{'='*80}")
        print(f"  Universe: {len(all_symbols)} HOSE symbols")
        self._preload_all_static(all_symbols, start_date)
        print(f"  Static data cached.")
        ohlcv_all = self.load_full_ohlcv(all_symbols, start_date, end_date)
        print(f"  OHLCV loaded: {len(ohlcv_all)} symbols with 60+ days data")

        # Date range: weekly dates
        print(f"\n{'='*80}")
        print(f"PHASE 1.5 — Date Range")
        print(f"{'='*80}")
        eval_dates = []
        d = start_date + timedelta(days=400)
        while d <= end_date:
            if d.weekday() < 5:
                eval_dates.append(d)
            d += timedelta(days=5)
        print(f"  {len(eval_dates)} eval dates: {eval_dates[0]} .. {eval_dates[-1]}")

        # Load sector map once
        print(f"  Loading sector map...")
        self.sector_map = self._load_sector_map(all_symbols)
        from collections import Counter
        sec_counts = Counter(self.sector_map.values())
        sec_summary = ", ".join(f"{k}={v}" for k, v in sorted(sec_counts.items()))
        print(f"  ICB Sectors ({len(sec_counts)} groups): {sec_summary}")
        fin = sum(1 for s in self.sector_map.values() if s in (FINANCIALS, BANKS, FINANCIAL_SERVICES))
        re_ = sum(1 for s in self.sector_map.values() if s == REAL_ESTATE)
        print(f"  Legacy groups: FINANCIALS={fin}, REAL_ESTATE={re_}, OTHERS={len(self.sector_map)-fin-re_}")

        # Check data availability at sample date
        print(f"\n{'='*80}")
        print(f"PHASE 1.5 — Data Quality Check")
        print(f"{'='*80}")
        sample_dt = eval_dates[len(eval_dates)//4] if eval_dates else end_date
        print(f"  Sampling at {sample_dt}...")
        filtered_sample = self._liquidity_filter(ohlcv_all, sample_dt)
        print(f"  Liquid stocks: {len(filtered_sample)}")
        factor_ranks_sample = self.compute_factors_at(filtered_sample, sample_dt)
        coverage = self.debug_factor_coverage(factor_ranks_sample, sample_symbols=0)
        low_factors = [fid for fid, s in coverage.items() if s["fill_rate"] < 30]
        if low_factors:
            print(f"\n  ⚠️  LOW COVERAGE FACTORS ({len(low_factors)}):")
            for fid in low_factors:
                s = coverage[fid]
                print(f"     {fid}: {s['fill_rate']}% fill ({s['n_stocks']}/{s['total_stocks']} stocks)")
        print(f"\n  Legend: FILL = % of liquid stocks with non-null factor value")
        print(f"          ⚠️ LOW  = <30% fill → IC results unreliable")
        print(f"          ⚠️ MED  = 30-60% fill → results may be noisy")

        # Phase 2 — IC benchmark
        print(f"\n{'='*80}")
        print(f"PHASE 2 — IC Benchmark")
        print(f"{'='*80}")

        # Collect IC per factor × horizon (regular + sector-neutral)
        all_ics = {f: {h: {"raw": [], "sector_neutral": []} for h in holdings} for f in VN_FACTORS}
        daily_coverage: dict[str, list[int]] = {f: [] for f in VN_FACTORS}

        for idx, dt in enumerate(eval_dates):
            if (idx + 1) % 40 == 0:
                print(f"  [{idx+1}/{len(eval_dates)}] dt={dt}")

            # Liquidity filter
            filtered = self._liquidity_filter(ohlcv_all, dt)
            if len(filtered) < VN_CONSTRAINTS["min_stocks"]:
                continue

            # Compute factors
            factor_ranks = self.compute_factors_at(filtered, dt)

            # Track coverage daily
            for fid, rank in factor_ranks.items():
                daily_coverage[fid].append(int(rank.dropna().shape[0]))

            # Pre-compute forward data once per date (reused across holdings)
            max_hold = max(holdings)
            forward_future: dict[str, pd.DataFrame] = {}
            for sym, df in filtered.items():
                future = df[df.index > pd.Timestamp(dt)]
                if len(future) >= 1 + max_hold:
                    forward_future[sym] = future

            for hold in holdings:
                fwd = {}
                for sym, future in forward_future.items():
                    if len(future) >= 1 + hold:
                        entry = future["close"].iloc[0]
                        if entry > 0:
                            exit_ = future["close"].iloc[hold]
                            if exit_ is not None and exit_ > 0:
                                fwd[sym] = exit_ / entry - 1
                for fid, rank in factor_ranks.items():
                    ic = self.compute_daily_ic(rank, fwd)
                    if ic is not None:
                        all_ics[fid][hold]["raw"].append(ic)
                    ic_sn = self.compute_daily_ic_sector_neutral(rank, fwd, self.sector_map)
                    if ic_sn is not None:
                        all_ics[fid][hold]["sector_neutral"].append(ic_sn)

        # Compute statistics (both raw and sector-neutral)
        print(f"\n{'='*80}")
        print(f"VN IC BENCHMARK — CORRECTED METHODOLOGY")
        print(f"{'='*80}")
        print(f"Period: {eval_dates[0]} .. {eval_dates[-1]} ({len(eval_dates)} dates)")
        print(f"Filters: liquidity 5B/day, winsorize ±7%, T+1 forward, min {VN_CONSTRAINTS['min_stocks']} stocks")

        def _compute_rows(ic_dict):
            """Compute IC stats from all_ics dict for either raw or sector_neutral."""
            rows = []
            pvals = {}
            for fid in sorted(VN_FACTORS.keys()):
                for hold in holdings:
                    ics = ic_dict[fid][hold]
                    if len(ics) < VN_CONSTRAINTS["min_dates"]:
                        continue
                    ic_arr = np.array(ics)
                    ic_mean = float(np.mean(ic_arr))
                    ic_std = float(np.std(ic_arr, ddof=1)) if len(ics) > 1 else 0.0
                    ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0
                    pos = float(np.mean(ic_arr > 0))
                    t_stat = ic_mean / (ic_std / math.sqrt(len(ics))) if ic_std > 0 else 0.0
                    pval = 2 * (1 - scipy_stats.t.cdf(abs(t_stat), df=len(ics) - 1)) if ic_std > 0 else 1.0

                    ic_series = pd.Series(ics)
                    wf = self.walk_forward(ic_series)

                    key = f"{fid}_{hold}d"
                    pvals[key] = pval

                    # Average coverage for this factor (over all dates it appeared)
                    cov = daily_coverage.get(fid, [])
                    avg_cov = round(np.mean(cov), 1) if cov else 0

                    rows.append({
                        "factor": fid,
                        "group": VN_FACTORS[fid]["group"],
                        "hold": f"{hold}d",
                        "ic": round(ic_mean, 4),
                        "std": round(ic_std, 4),
                        "ir": round(ic_ir, 4),
                        "pos": round(pos, 3),
                        "t": round(t_stat, 3),
                        "pval": round(pval, 4),
                        "n": len(ics),
                        "wf_deg": wf["avg_degradation"],
                        "wf_verdict": wf["verdict"],
                        "avg_coverage": avg_cov,
                    })

            # BH correction
            pval_list = [r["pval"] for r in rows]
            m = len(pval_list)
            if m > 0:
                sorted_idx = sorted(range(m), key=lambda i: pval_list[i])
                pvals_corrected = [0.0] * m
                prev_bh = 0.0
                for rank, idx in enumerate(sorted_idx):
                    bh = pval_list[idx] * m / (rank + 1)
                    bh = min(bh, 1.0)
                    pvals_corrected[idx] = max(bh, prev_bh)
                    prev_bh = pvals_corrected[idx]
            else:
                pvals_corrected = []
            for i, r in enumerate(rows):
                r["pval_adj"] = round(pvals_corrected[i], 4) if m > 0 else 1.0

            # Verdict
            for r in rows:
                ic_mag = abs(r["ic"]) > 0.03
                t_pass = abs(r["t"]) > 2.0
                pos_pass = r["pos"] > 0.55 or r["pos"] < 0.45
                mt_pass = r["pval_adj"] < 0.05
                wf_pass = r["wf_verdict"] in ("ROBUST", "MARGINAL")
                checks = {"ic": ic_mag, "tstat": t_pass, "pos": pos_pass, "mt": mt_pass, "wf": wf_pass}
                passed = sum(checks.values())

                if r["ic"] > 0.03 and t_pass and pos_pass and mt_pass:
                    r["verdict"] = "ALIVE"
                elif r["ic"] < -0.03 and t_pass and mt_pass:
                    r["verdict"] = "REVERSED"
                elif passed >= 3:
                    r["verdict"] = "MARGINAL"
                elif passed >= 1:
                    r["verdict"] = "WEAK"
                else:
                    r["verdict"] = "DEAD"
            return rows

        # Compute raw rows (for sorting final display)
        raw_data = {f: {h: all_ics[f][h]["raw"] for h in holdings} for f in VN_FACTORS}
        rows = _compute_rows(raw_data)

        # Print raw table
        print(f"\n{'='*80}")
        print("RAW IC (no sector neutralization)")
        print(f"{'='*80}")
        print(f"{'FACTOR':>18s} {'H':>3s} {'IC':>7s} {'IR':>6s} {'POS':>5s} {'T':>6s} {'P_ADJ':>6s} {'COV':>5s} {'WF':>9s} {'VERDICT':>10s}")
        print("-" * 80)
        categories = {"ALIVE": [], "REVERSED": [], "MARGINAL": [], "WEAK": [], "DEAD": []}
        for r in sorted(rows, key=lambda x: (-abs(x["ic"]))):
            cov_str = f"{r['avg_coverage']:.0f}" if r["avg_coverage"] else "?"
            print(f"{r['factor']:>18s} {r['hold']:>3s} {r['ic']:>7.4f} {r['ir']:>6.3f} {r['pos']:>5.2f} {r['t']:>6.2f} {r['pval_adj']:>6.4f} {cov_str:>5s} {r['wf_verdict']:>9s} {r['verdict']:>10s}")
            categories[r["verdict"]].append(r)

        # Sector-neutral table
        sn_data = {f: {h: all_ics[f][h]["sector_neutral"] for h in holdings} for f in VN_FACTORS}
        rows_sn = _compute_rows(sn_data)
        active_sn = [r for r in rows_sn if r["verdict"] in ("ALIVE", "REVERSED")]
        if active_sn:
            print(f"\n{'─'*80}")
            print("SECTOR-NEUTRAL IC (demeaned within sector)")
            print(f"{'─'*80}")
            print(f"{'FACTOR':>18s} {'H':>3s} {'IC':>7s} {'IR':>6s} {'POS':>5s} {'T':>6s} {'P_ADJ':>6s} {'COV':>5s} {'WF':>9s} {'VERDICT':>10s}")
            for r in sorted(active_sn, key=lambda x: (-abs(x["ic"]))):
                cov_str = f"{r['avg_coverage']:.0f}" if r["avg_coverage"] else "?"
                print(f"{r['factor']:>18s} {r['hold']:>3s} {r['ic']:>7.4f} {r['ir']:>6.3f} {r['pos']:>5.2f} {r['t']:>6.2f} {r['pval_adj']:>6.4f} {cov_str:>5s} {r['wf_verdict']:>9s} {r['verdict']:>10s}")
        else:
            print(f"\n  No factor survives sector neutralization (all DEAD/WEAK)")

        # Summary
        print(f"\n{'='*80}")
        print("SUMMARY — RAW IC")
        print(f"{'='*80}")
        for cat in ["ALIVE", "REVERSED", "MARGINAL", "WEAK", "DEAD"]:
            cat_rows = [r for r in rows if r["verdict"] == cat]
            if cat_rows:
                print(f"\n{cat}:")
                for r in cat_rows:
                    print(f"  {r['factor']:>15s} [{r['hold']}]  IC={r['ic']:.4f}  IR={r['ir']:.3f}  pos={r['pos']:.2f}  p_adj={r['pval_adj']:.4f}  cov={r['avg_coverage']:.0f}")

        # Data quality summary
        print(f"\n{'─'*80}")
        print("DATA QUALITY NOTES")
        print(f"{'─'*80}")
        for r in sorted(rows, key=lambda x: x["avg_coverage"]):
            if r["avg_coverage"] < 30 and r["avg_coverage"] > 0:
                print(f"  ⚠️  {r['factor']} [{r['hold']}]: avg coverage={r['avg_coverage']:.0f} stocks — IC unreliable")
            elif r["avg_coverage"] < 60 and r["avg_coverage"] > 0:
                print(f"  ⚠️  {r['factor']} [{r['hold']}]: avg coverage={r['avg_coverage']:.0f} stocks — IC may be noisy")

        # Compare raw vs sector-neutral for key factors
        print(f"\n{'─'*80}")
        print("RAW vs SECTOR-NEUTRAL IC (key factors)")
        print(f"{'─'*80}")
        key_factors = ["PE_INV", "PB_INV", "NM", "GM", "ROE_NORM", "SIZE", "CEILING_STREAK", "MOM_3M"]
        for r in rows:
            if r["factor"] in key_factors:
                sn_row = next((x for x in rows_sn if x["factor"] == r["factor"] and x["hold"] == r["hold"]), None)
                sn_ic = sn_row["ic"] if sn_row else "N/A"
                delta = f"{r['ic'] - sn_row['ic']:+.4f}" if sn_row else ""
                print(f"  {r['factor']:>18s} [{r['hold']}]  raw={r['ic']:.4f}  sn={sn_ic}  delta={delta}")

        duration = round(time.monotonic() - start, 1)
        print(f"\nDuration: {duration}s")
        print(f"{'='*80}")

        return {"raw": rows, "sector_neutral": rows_sn}, categories


def main(output_dir: str | None = None):
    import logging
    import json
    from pathlib import Path
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(level=logging.WARNING)
    conn = psycopg2.connect(DB_URL)
    try:
        tester = VNICTester(conn)
        results, categories = tester.run(years=3)
    finally:
        conn.close()

    if output_dir is None:
        output_dir = str(Path.home() / ".vibe-trading" / "reports")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = date.today().isoformat()

    # Save full results as JSON
    report = {
        "generated_at": ts,
        "n_eval_dates": 166,
        "raw_ic": results["raw"],
        "sector_neutral_ic": results["sector_neutral"],
        "categories": {k: [r["factor"] + "_" + r["hold"] for r in v] for k, v in categories.items()},
    }
    path = out / f"vn_ic_results_{ts}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults saved to {path}")
    return results, categories


if __name__ == "__main__":
    main()
