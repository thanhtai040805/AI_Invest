#!/usr/bin/env python3
"""IC test for 3 remaining factors: ACCRUAL, CFO_TO_NI, ALTMAN_Z.
Loads financial_statements JSONB, computes factors, runs IC.
"""
import sys
import math
import json
import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

sys.path.insert(0, ".")
from app.services.pg_pool import DB_URL

logger = logging.getLogger("test_factors")

TZ_VN = timedelta(hours=7)

# ── Key search helpers ──────────────────────────────────────────────

def _search_keys(data: dict, keywords: list[str]) -> float | None:
    """Find first numeric value whose key contains any keyword (case-insensitive)."""
    if not data or not isinstance(data, dict):
        return None
    for k, v in data.items():
        for kw in keywords:
            if kw.lower() in k.lower():
                if isinstance(v, (int, float)) and not math.isnan(v) and not math.isinf(v):
                    return float(v)
    return None


def _diff(older: dict, newer: dict, keywords: list[str]) -> float | None:
    """Compute newer_value - older_value for a key."""
    new_val = _search_keys(newer, keywords)
    old_val = _search_keys(older, keywords)
    if new_val is not None and old_val is not None:
        return new_val - old_val
    return None


def _avg(a: float | None, b: float | None) -> float | None:
    if a is not None and b is not None:
        return (a + b) / 2.0
    return a if a is not None else b


# ── Key word lists ──────────────────────────────────────────────────

CURRENT_ASSETS = [
    "tài sản ngắn hạn", "a_tài_sản_ngắn_hạn", "a. tài sản ngắn hạn",
    "tài_sản_ngắn_hạn", "ngắn hạn", "ngan han",
]
CURRENT_LIAB = [
    "nợ ngắn hạn", "no ngan han", "nợ_ngắn_hạn",
    "nợ ngắn hạn (310=311+312+...+322)", "nợ ngắn hạn",
]
CASH = [
    "tiền và tương đương tiền", "tien va tuong duong tien",
    "tiền_và_tương_đương_tiền", "1_tiền_và_tương_đương_tiền",
    "tiền", "tien", "cash",
]
STD = [
    "vay ngắn hạn", "vay ngan han", "vay_ngắn_hạn",
]
TOTAL_ASSETS = [
    "tổng cộng tài sản", "tong cong tai san", "tổng_cộng_tài_sản",
    "tổng cộng tài sản (270=100+200)",
]
TOTAL_LIAB = [
    "tổng nợ phải trả", "tong no phai tra", "c_nợ_phải_trả",
    "C. NỢ PHẢI TRẢ", "nợ phải trả", "nợ phải trả (300=210+330)",
]
NET_INCOME = [
    "18_lợi_nhuận_sau_thuế", "lợi nhuận sau thuế", "loi nhuan sau thue",
    "lợi_nhuận_sau_thuế", "29_lợi_nhuận_sau_thuế",
    "lợi nhuận sau thuế thu nhập doanh nghiệp",
]
EQUITY = [
    "vốn chủ sở hữu", "von chu so huu", "i_vốn_chủ_sở_hữu",
    "vốn chủ sở hữu (400=410+430)", "b_vốn_chủ_sở_hữu",
]
CFO = [
    "lưu chuyển tiền thuần từ hoạt động kinh doanh",
    "luu chuyen tien thuan tu hoat dong kinh doanh",
    "lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",
]
REVENUE = [
    "doanh thu thuần", "doanh thu thuan", "3_doanh_thu_thuần",
    "doanh thu bán hàng",
]
DEPRECIATION = [
    "khấu hao", "khau hao", "khấu_hao",
    "khấu hao tài sản cố định",
    "14_khấu_hao_tài_sản_cố_định",
]
EBIT = [
    "lợi nhuận từ hoạt động kinh doanh", "loi nhuan tu hoat dong kinh doanh",
    "20_lợi_nhuận_từ_hoạt_động_kinh_doanh",
    "lợi nhuận thuần từ hoạt động kinh doanh",
    "lợi nhuận từ hđkd",
]
RETAINED_EARNINGS = [
    "lợi nhuận sau thuế chưa phân phối", "loi nhuan sau thue chua phan phoi",
    "lợi_nhuận_sau_thuế_chưa_phân_phối",
]
INTEREST_EXPENSE = [
    "chi phí lãi vay", "chi phi lai vay", "chi_phí_lãi_vay",
    "chi phí tài chính", "9_chi_phí_tài_chính",
]
INVENTORY = [
    "hàng tồn kho", "hang ton kho", "hàng_tồn_kho",
]
ACCOUNTS_RECEIVABLE = [
    "phải thu ngắn hạn", "phai thu ngan han", "các khoản phải thu ngắn hạn",
    "2_các_khoản_phải_thu_ngắn_hạn",
]
ACCOUNTS_PAYABLE = [
    "phải trả người bán", "phai tra nguoi ban",
    "phải trả người bán ngắn hạn",
]
TAX_PAYABLE = [
    "thuế và các khoản phải nộp nhà nước", "thue",
    "thuế thu nhập doanh nghiệp",
]


def get_period(cur, symbol: str, as_of: date, stmt_type: str) -> dict | None:
    """Get the most recent financial statement data (yearly or quarterly) for a symbol."""
    cur.execute(
        """SELECT data FROM financial_statements
           WHERE symbol = %s AND statement_type = %s
             AND period_end <= %s
           ORDER BY period_end DESC LIMIT 1""",
        (symbol, stmt_type, as_of),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_period_pair(cur, symbol: str, as_of: date, stmt_type: str):
    """Get current and prior period data for YoY comparisons (any frequency)."""
    cur.execute(
        """SELECT period_end, data FROM financial_statements
           WHERE symbol = %s AND statement_type = %s
             AND period_end <= %s
           ORDER BY period_end DESC LIMIT 2""",
        (symbol, stmt_type, as_of),
    )
    rows = cur.fetchall()
    if len(rows) >= 2:
        if rows[0][0].year == rows[1][0].year:
            # Same year → try to get next distinct year
            y1 = rows[0][0].year
            cur.execute(
                """SELECT period_end, data FROM financial_statements
                   WHERE symbol = %s AND statement_type = %s
                     AND period_end <= %s AND period_end < %s
                   ORDER BY period_end DESC LIMIT 1""",
                (symbol, stmt_type, as_of, date(y1, 1, 1)),
            )
            older = cur.fetchone()
            if older:
                return rows[0][1], older[1]
        return rows[0][1], rows[1][1]
    if len(rows) == 1:
        return rows[0][1], None
    return None, None


def compute_accrual(bs_cur: dict | None, bs_prev: dict | None, ta_avg: float | None) -> float | None:
    """Total Accruals = ΔWC - Depreciation (simplified).
    
    ΔWC = (ΔCurrentAssets - ΔCash - ΔCurrentLiabilities + ΔShortTermDebt)
    """
    if bs_cur is None or bs_prev is None:
        return None
    
    d_ca = _diff(bs_prev, bs_cur, CURRENT_ASSETS)
    d_cash = _diff(bs_prev, bs_cur, CASH)
    d_cl = _diff(bs_prev, bs_cur, CURRENT_LIAB)
    d_std = _diff(bs_prev, bs_cur, STD)
    d_inv = _diff(bs_prev, bs_cur, INVENTORY)
    d_ar = _diff(bs_prev, bs_cur, ACCOUNTS_RECEIVABLE)
    d_ap = _diff(bs_prev, bs_cur, ACCOUNTS_PAYABLE)
    d_tax = _diff(bs_prev, bs_cur, TAX_PAYABLE)
    
    # ΔWC approach
    if d_ca is not None and d_cl is not None and d_cash is not None:
        delta_wc = d_ca - d_cash - d_cl
        # Simplified: total accruals ≈ -ΔWC
        # Actually: Total Accruals = (ΔCA - ΔCash) - (ΔCL - ΔSTD - ΔTaxPayable) - Dep
        # More standard: Accruals = (ΔCurrent Assets - ΔCash) - (ΔCurrent Liabilities - ΔSTD - ΔTaxPayable) - Depreciation
        # The simplest: Accruals = (ΔCA - ΔCash - ΔCL + ΔSTD)
        # We'll use balance sheet approach
        if d_std is not None:
            accrual_bs = d_ca - d_cash - d_cl + d_std
        else:
            accrual_bs = d_ca - d_cash - d_cl
        
        if ta_avg and ta_avg != 0:
            return accrual_bs / ta_avg
    
    return None


def compute_cfo_to_ni(cf_cur: dict | None, is_cur: dict | None) -> float | None:
    """CFO / Net Income"""
    cfo = _search_keys(cf_cur, CFO) if cf_cur else None
    ni = _search_keys(is_cur, NET_INCOME) if is_cur else None
    if cfo is not None and ni is not None and ni != 0:
        return cfo / ni
    return None


def compute_altman_z(cur, symbol: str, as_of: date) -> float | None:
    """Altman Z' for Emerging Markets.
    
    Z' = 3.25 + 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
    
    X1 = Working Capital / Total Assets
    X2 = Retained Earnings / Total Assets
    X3 = EBIT / Total Assets
    X4 = Book Value of Equity / Total Liabilities
    """
    bs = get_period(cur, symbol, as_of, "BS")
    is_ = get_period(cur, symbol, as_of, "IS")
    if bs is None or is_ is None:
        return None
    
    ca = _search_keys(bs, CURRENT_ASSETS)
    cl = _search_keys(bs, CURRENT_LIAB)
    ta = _search_keys(bs, TOTAL_ASSETS)
    tl = _search_keys(bs, TOTAL_LIAB)
    eq = _search_keys(bs, EQUITY)
    re = _search_keys(bs, RETAINED_EARNINGS)
    ni = _search_keys(is_, NET_INCOME)
    rev = _search_keys(is_, REVENUE)
    
    # X1: Working Capital / Total Assets
    x1 = None
    if ca is not None and cl is not None and ta is not None and ta != 0:
        wc = ca - cl
        x1 = wc / ta
    
    # X2: Retained Earnings / Total Assets
    x2 = None
    if re is not None and ta is not None and ta != 0:
        x2 = re / ta
    elif ni is not None and ta is not None and ta != 0:
        # Approximate RE = cumulative retained earnings (use net income as proxy for small firms)
        x2 = ni / ta * 2  # rough estimate
    
    # X3: EBIT / Total Assets
    x3 = None
    if ni is not None and ta is not None and ta != 0:
        int_exp = _search_keys(is_, INTEREST_EXPENSE)
        tax = _search_keys(is_, TAX_PAYABLE)
        ebit = ni
        if int_exp is not None:
            ebit += int_exp
        if tax is not None:
            ebit += tax
        x3 = ebit / ta
    
    # X4: Book Value / Total Liabilities
    x4 = None
    if eq is not None and tl is not None and tl != 0:
        x4 = eq / tl
    elif ta is not None and tl is not None and tl != 0:
        x4 = (ta - tl) / tl
    
    if all(v is not None for v in [x1, x2, x3, x4]):
        z = 3.25 + 6.56*x1 + 3.26*x2 + 6.72*x3 + 1.05*x4
        return z
    
    return None


# ── IC Testing ──────────────────────────────────────────────────────

def test_factor(
    cur,
    factor_id: str,
    compute_fn,
    symbols: list[str],
    eval_dates: list[date],
    ohlcv_all: dict,
    horizon: int = 20,
    min_stocks: int = 15,
):
    """Compute IC for a factor across evaluation dates."""
    ic_values = []
    
    for dt in eval_dates:
        factor_vals = {}
        for sym in symbols:
            try:
                val = compute_fn(cur, sym, dt)
                if val is not None and math.isfinite(val):
                    factor_vals[sym] = val
            except Exception:
                continue
        
        if len(factor_vals) < min_stocks:
            continue
        
        # Rank the factor
        vals = pd.Series(factor_vals)
        factor_rank = vals.rank(pct=True, ascending=True, na_option="keep") * 100
        
        # Get forward returns
        fwd = {}
        for sym in factor_rank.index:
            df = ohlcv_all.get(sym)
            if df is None:
                continue
            hist = df[df.index <= pd.Timestamp(dt)]
            if len(hist) < 1:
                continue
            cur_c = hist["close"].iloc[-1]
            fwd_hist = df[df.index > pd.Timestamp(dt)]
            if len(fwd_hist) < horizon:
                continue
            fwd_c = fwd_hist["close"].iloc[min(horizon - 1, len(fwd_hist) - 1)]
            if cur_c > 0 and fwd_c is not None and fwd_c > 0:
                fwd[sym] = fwd_c / cur_c - 1
        
        common = set(factor_rank.dropna().index) & set(fwd.keys())
        if len(common) < min_stocks:
            continue
        
        fwd_s = pd.Series({s: fwd[s] for s in common})
        rank_s = pd.Series({s: factor_rank[s] for s in common})
        ic = fwd_s.rank().corr(rank_s.rank(), method="pearson")
        if not np.isnan(ic):
            ic_values.append(ic)
    
    return ic_values


# ── Main ────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Get symbols
    cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
    all_symbols = [r[0] for r in cur.fetchall()]
    logger.info("Universe: %d symbols", len(all_symbols))
    
    # Load OHLCV
    end_date = date.today()
    start_date = end_date - timedelta(days=int(3 * 365.25 + 400 + 60))
    cur.execute(
        """SELECT symbol, time::date as dt, adj_close, close, volume
           FROM ohlcv
           WHERE symbol = ANY(%s) AND time::date >= %s AND time::date <= %s
           ORDER BY symbol, time""",
        (all_symbols, start_date, end_date),
    )
    ohlcv_all: dict[str, pd.DataFrame] = {}
    for sym, dt, ac, cl, vol in cur.fetchall():
        c = float(ac or cl or 0)
        ohlcv_all.setdefault(sym, []).append({"date": dt, "close": c, "volume": int(vol or 0)})
    for sym in ohlcv_all:
        df = pd.DataFrame(ohlcv_all[sym]).set_index("date").sort_index()
        df.index = pd.to_datetime(df.index)
        ohlcv_all[sym] = df if len(df) > 100 else None
    ohlcv_all = {k: v for k, v in ohlcv_all.items() if v is not None}
    logger.info("OHLCV loaded: %d symbols", len(ohlcv_all))
    
    # Generate eval dates (quarterly to match financial statements frequency)
    eval_dates = []
    d = date(2023, 6, 30)
    while d <= end_date:
        if d.weekday() < 5:
            eval_dates.append(d)
        d += timedelta(days=21)
    logger.info("Eval dates: %d (%s .. %s)", len(eval_dates), eval_dates[0], eval_dates[-1])
    
    # First, check available frequencies and keys
    cur.execute("SELECT DISTINCT frequency FROM financial_statements")
    freqs = [r[0] for r in cur.fetchall()]
    logger.info("Available frequencies: %s", freqs)
    
    for freq in freqs:
        cur.execute("""
            SELECT DISTINCT jsonb_object_keys(data) as key
            FROM financial_statements 
            WHERE symbol='FPT' AND statement_type='BS' AND frequency=%s
            ORDER BY key
        """, (freq,))
        bs_keys = [r[0] for r in cur.fetchall()]
        if bs_keys:
            logger.info("FPT BS keys (freq=%s, %d): %s", freq, len(bs_keys), ", ".join(bs_keys[:30]))
        
        cur.execute("""
            SELECT DISTINCT jsonb_object_keys(data) as key
            FROM financial_statements 
            WHERE symbol='FPT' AND statement_type='IS' AND frequency=%s
            ORDER BY key
        """, (freq,))
        is_keys = [r[0] for r in cur.fetchall()]
        if is_keys:
            logger.info("FPT IS keys (freq=%s, %d): %s", freq, len(is_keys), ", ".join(is_keys[:30]))
        
        cur.execute("""
            SELECT DISTINCT jsonb_object_keys(data) as key
            FROM financial_statements 
            WHERE symbol='FPT' AND statement_type='CF' AND frequency=%s
            ORDER BY key
        """, (freq,))
        cf_keys = [r[0] for r in cur.fetchall()]
        if cf_keys:
            logger.info("FPT CF keys (freq=%s, %d): %s", freq, len(cf_keys), ", ".join(cf_keys[:30]))
    
    # Check actual values for a few symbols to find keys
    for sym in ["FPT", "HPG", "VNM"]:
        bs = get_period(cur, sym, date(2024, 12, 31), "BS")
        is_ = get_period(cur, sym, date(2024, 12, 31), "IS")
        cf = get_period(cur, sym, date(2024, 12, 31), "CF")
        
        if bs:
            ca_match = _search_keys(bs, CURRENT_ASSETS)
            cl_match = _search_keys(bs, CURRENT_LIAB)
            ta_match = _search_keys(bs, TOTAL_ASSETS)
            eq_match = _search_keys(bs, EQUITY)
            cash_match = _search_keys(bs, CASH)
            logger.info("%s BS: CA=%s CL=%s TA=%s EQ=%s CASH=%s", sym, ca_match, cl_match, ta_match, eq_match, cash_match)
        
        if is_:
            ni_match = _search_keys(is_, NET_INCOME)
            rev_match = _search_keys(is_, REVENUE)
            logger.info("%s IS: NI=%s REV=%s", sym, ni_match, rev_match)
        
        if cf:
            cfo_match = _search_keys(cf, CFO)
            logger.info("%s CF: CFO=%s", sym, cfo_match)
        
        # Preview financial_ratios for same symbol
        cur.execute("""
            SELECT pe, pb, roe, roa, gross_margin, net_margin FROM financial_ratios
            WHERE symbol = %s ORDER BY ratio_date DESC LIMIT 1
        """, (sym,))
        fr = cur.fetchone()
        if fr:
            logger.info("%s FR: PE=%s PB=%s ROE=%s ROA=%s GM=%s NM=%s", sym, *fr)
    
    # Now test each factor
    symbols_with_data = list(ohlcv_all.keys())
    
    # ── ACCRUAL ────────────────────────────────────────────────────
    logger.info("\n=== ACCRUAL IC test ===")
    accrual_ics = []
    for dt in eval_dates:
        vals = {}
        for sym in symbols_with_data[:200]:  # limit to 200 for speed
            bs_cur, bs_prev = get_period_pair(cur, sym, dt, "BS")
            if bs_cur is None:
                continue
            ta = _search_keys(bs_cur, TOTAL_ASSETS)
            fallback = compute_accrual(bs_cur, bs_prev, ta)
            vals[sym] = fallback
        
        vals = {k: v for k, v in vals.items() if v is not None and math.isfinite(v)}
        if len(vals) < 15:
            continue
        
        vals_s = pd.Series(vals)
        rank = vals_s.rank(pct=True, ascending=True, na_option="keep") * 100
        
        fwd = {}
        for sym in rank.index:
            df = ohlcv_all.get(sym)
            if df is None: continue
            hist = df[df.index <= pd.Timestamp(dt)]
            if len(hist) < 1: continue
            cur_c = hist["close"].iloc[-1]
            fwd_hist = df[df.index > pd.Timestamp(dt)]
            if len(fwd_hist) < 20: continue
            fwd_c = fwd_hist["close"].iloc[19]
            if cur_c > 0 and fwd_c > 0:
                fwd[sym] = fwd_c / cur_c - 1
        
        common = set(rank.dropna().index) & set(fwd.keys())
        if len(common) < 15: continue
        fwd_s = pd.Series({s: fwd[s] for s in common})
        rank_s = pd.Series({s: rank[s] for s in common})
        ic = fwd_s.rank().corr(rank_s.rank(), method="pearson")
        if not np.isnan(ic):
            accrual_ics.append(ic)
    
    logger.info("ACCRUAL: %d dates, IC_mean=%.4f, IC_std=%.4f, pos_ratio=%.3f",
                len(accrual_ics),
                np.mean(accrual_ics) if accrual_ics else 0,
                np.std(accrual_ics, ddof=1) if len(accrual_ics) > 1 else 0,
                sum(1 for x in accrual_ics if x > 0) / len(accrual_ics) if accrual_ics else 0)
    
    # ── CFO_TO_NI ──────────────────────────────────────────────────
    logger.info("\n=== CFO_TO_NI IC test ===")
    cfo_ics = []
    for dt in eval_dates:
        vals = {}
        for sym in symbols_with_data[:200]:
            cf = get_period(cur, sym, dt, "CF")
            is_ = get_period(cur, sym, dt, "IS")
            if cf is None or is_ is None:
                continue
            val = compute_cfo_to_ni(cf, is_)
            if val is not None:
                vals[sym] = val
        
        vals = {k: v for k, v in vals.items() if v is not None and math.isfinite(v)}
        if len(vals) < 15: continue
        
        vals_s = pd.Series(vals)
        rank = vals_s.rank(pct=True, ascending=True, na_option="keep") * 100
        
        fwd = {}
        for sym in rank.index:
            df = ohlcv_all.get(sym)
            if df is None: continue
            hist = df[df.index <= pd.Timestamp(dt)]
            if len(hist) < 1: continue
            cur_c = hist["close"].iloc[-1]
            fwd_hist = df[df.index > pd.Timestamp(dt)]
            if len(fwd_hist) < 20: continue
            fwd_c = fwd_hist["close"].iloc[19]
            if cur_c > 0 and fwd_c > 0:
                fwd[sym] = fwd_c / cur_c - 1
        
        common = set(rank.dropna().index) & set(fwd.keys())
        if len(common) < 15: continue
        fwd_s = pd.Series({s: fwd[s] for s in common})
        rank_s = pd.Series({s: rank[s] for s in common})
        ic = fwd_s.rank().corr(rank_s.rank(), method="pearson")
        if not np.isnan(ic):
            cfo_ics.append(ic)
    
    logger.info("CFO_TO_NI: %d dates, IC_mean=%.4f, IC_std=%.4f, pos_ratio=%.3f",
                len(cfo_ics),
                np.mean(cfo_ics) if cfo_ics else 0,
                np.std(cfo_ics, ddof=1) if len(cfo_ics) > 1 else 0,
                sum(1 for x in cfo_ics if x > 0) / len(cfo_ics) if cfo_ics else 0)
    
    # ── ALTMAN_Z ───────────────────────────────────────────────────
    logger.info("\n=== ALTMAN_Z IC test ===")
    altman_ics = []
    for dt in eval_dates:
        vals = {}
        for sym in symbols_with_data[:200]:
            val = compute_altman_z(cur, sym, dt)
            if val is not None and math.isfinite(val):
                vals[sym] = val
        
        vals = {k: v for k, v in vals.items() if v is not None and math.isfinite(v)}
        if len(vals) < 15: continue
        
        vals_s = pd.Series(vals)
        rank = vals_s.rank(pct=True, ascending=True, na_option="keep") * 100
        
        fwd = {}
        for sym in rank.index:
            df = ohlcv_all.get(sym)
            if df is None: continue
            hist = df[df.index <= pd.Timestamp(dt)]
            if len(hist) < 1: continue
            cur_c = hist["close"].iloc[-1]
            fwd_hist = df[df.index > pd.Timestamp(dt)]
            if len(fwd_hist) < 20: continue
            fwd_c = fwd_hist["close"].iloc[19]
            if cur_c > 0 and fwd_c > 0:
                fwd[sym] = fwd_c / cur_c - 1
        
        common = set(rank.dropna().index) & set(fwd.keys())
        if len(common) < 15: continue
        fwd_s = pd.Series({s: fwd[s] for s in common})
        rank_s = pd.Series({s: rank[s] for s in common})
        ic = fwd_s.rank().corr(rank_s.rank(), method="pearson")
        if not np.isnan(ic):
            altman_ics.append(ic)
    
    logger.info("ALTMAN_Z: %d dates, IC_mean=%.4f, IC_std=%.4f, pos_ratio=%.3f",
                len(altman_ics),
                np.mean(altman_ics) if altman_ics else 0,
                np.std(altman_ics, ddof=1) if len(altman_ics) > 1 else 0,
                sum(1 for x in altman_ics if x > 0) / len(altman_ics) if altman_ics else 0)
    
    # ── Print summary ──────────────────────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY — 3 Missing Factors")
    logger.info("=" * 60)
    for name, ics in [("ACCRUAL", accrual_ics), ("CFO_TO_NI", cfo_ics), ("ALTMAN_Z", altman_ics)]:
        if ics:
            mean = np.mean(ics)
            std = np.std(ics, ddof=1) if len(ics) > 1 else 0
            pos = sum(1 for x in ics if x > 0) / len(ics)
            ir = mean / std if std > 0 else 0
            t = mean / (std / math.sqrt(len(ics))) if std > 0 else 0
            
            if mean > 0.02 and pos >= 0.55 and abs(t) > 2:
                cat = "ALIVE"
            elif mean < -0.02 and abs(t) > 2:
                cat = "REVERSED"
            else:
                cat = "dead"
            
            logger.info("  %-15s IC=%.4f IR=%.3f pos=%.2f t=%.2f n=%d [%s]",
                       name, mean, ir, pos, t, len(ics), cat)
        else:
            logger.info("  %-15s NO DATA", name)
    
    conn.close()

if __name__ == "__main__":
    main()
