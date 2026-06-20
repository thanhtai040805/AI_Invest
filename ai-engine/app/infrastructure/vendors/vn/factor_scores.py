"""
Factor Scores V3 — full VN-core factor engine (Tier A + B + C + academic salvage).

Academic basis:
  Momentum:    Dang & Nguyen (2021) — 3-6m optimal, regime-conditional
  Liquidity:   Nguyen (2020) — Amihud premium strongest VN factor
  Value:       Fama-French VN (Vo & Bui 2016, Nguyen & Nguyen 2019)
  Quality:     Le & Tran (2022) — accrual anomaly stronger than US
  Flow:        Bui (2020) — foreign net flow 3-5d predictive, insider signal (UBCKNN)
  Behavioral:  Tet seasonality, price-limit clustering, margin-call reversal
  Distress:    Altman Z' for EM — adapted to Vietnamese financials
  Earnings:    Surprise proxy via YoY growth (no analyst consensus)
  Size:        log(mcap) — small cap premium confirmed in VN

31 factors across 9 groups, stored as cross-sectional percentile ranks (0-100).
"""
import json
import logging
from datetime import date, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from app.infrastructure.database.pg_pool import DB_URL
from app.infrastructure.vendors.vn.sector_groups import (
    classify_major as classify,
    FINANCIALS, REAL_ESTATE, OTHERS,
)
from app.domain.services.quant.factor_orthogonalization import (
    FactorOrthogonalizer,
    OrthogonalizationMethod,
    DEFAULT_GROUP_MAP,
    KNOWN_HIGH_CORR_PAIRS,
)

logger = logging.getLogger(__name__)

# ── Full VN factor registry (Tier A + B + C + salvage) ─────────────────
VN_FACTORS = {
    # ===== Momentum — Dang & Nguyen (2021) =====
    "MOM_3M":     {"group": "momentum", "direction": 1},
    "MOM_6M":     {"group": "momentum", "direction": 1},
    "COND_MOM":   {"group": "momentum", "direction": 1},

    # ===== Liquidity — Nguyen (2020), strongest VN factor =====
    "AMIHUD":     {"group": "liquidity", "direction": -1},
    "DVOL_TREND": {"group": "liquidity", "direction": 1},

    # ===== Value — Fama-French VN =====
    "PE_INV":     {"group": "value", "direction": 1},
    "PB_INV":     {"group": "value", "direction": 1},
    "EARN_YLD":   {"group": "value", "direction": 1},
    "FCF_YLD":    {"group": "value", "direction": 1},
    "EVEBITDA_INV": {"group": "value", "direction": 1},
    "HML_REAL":   {"group": "value", "direction": 1},  # book-to-market ratio

    # ===== Quality — Le & Tran (2022) accrual anomaly =====
    "ACCRUAL":    {"group": "quality", "direction": 1},
    "CFO_TO_NI":  {"group": "quality", "direction": 1},
    "ROE_NORM":   {"group": "quality", "direction": 1},
    "GM":         {"group": "quality", "direction": 1},
    "NM":         {"group": "quality", "direction": 1},
    "YOY_REV":    {"group": "quality", "direction": 1},
    "YOY_EARN":   {"group": "quality", "direction": 1},
    "PIOTROSKI_F": {"group": "quality", "direction": 1},  # full 9-point

    # ===== Earnings surprise (Tier B) =====
    "EARN_SURP":  {"group": "earnings", "direction": 1},

    # ===== Distress / bankruptcy risk (Tier B: Altman Z') =====
    "ALTMAN_Z":   {"group": "distress", "direction": 1},  # Altman Z' for EM

    # ===== VN-specific flow — Bui (2020) =====
    "FOREIGN_NET_5D":  {"group": "flow", "direction": 1},
    "FOREIGN_ACCUM":   {"group": "flow", "direction": 1},
    "INSIDER_NET_30D": {"group": "flow", "direction": 1},
    "FOREIGN_ROOM":    {"group": "flow", "direction": -1},  # room scarcity, low room = cap on upside

    # ===== Behavioral VN-specific (Tier C) =====
    "TET_WINDOW":      {"group": "behavioral", "direction": 1},
    "CEILING_STREAK":  {"group": "behavioral", "direction": -1},
    "FORCED_SELLING":  {"group": "behavioral", "direction": 1},

    # ===== Risk / size =====
    "SIZE":       {"group": "risk", "direction": -1},
    "VOL_20D":    {"group": "risk", "direction": -1},
    "VOL_60D":    {"group": "risk", "direction": -1},
}

# Composite weight schema (sums to 1.0)
# 8 groups: value, quality, momentum, earnings, flow, liquidity, distress, risk, behavioral
COMPOSITE_WEIGHTS = {
    # Updated from VN IC benchmark (2024-05 to 2026-05, 36 monthly dates, 405 stocks)
    # PE_INV dominates (IR=0.94, IC=0.078, pos=0.86), all value factors alive at 20d
    "value":     0.25,
    # GM (IR=0.39), NM (IR=0.55), YOY_REV (IR=0.45), YOY_EARN (IR=0.71) alive at 20d
    "quality":   0.20,
    # MOM_3M reversed at 5d (IC=-0.05), MOM_6M dead — momentum doesn't work in VN
    "momentum":  0.03,
    # EARN_SURP alive at 20d (IR=0.71)
    "earnings":  0.10,
    # All flow factors dead — FOREIGN_NET_5D IC=-0.01
    "flow":      0.02,
    # AMIHUD, DVOL_TREND dead — IC≈0
    "liquidity": 0.02,
    # Not computed in IC benchmark (needs financial statements)
    "distress":  0.02,
    # VOL_60D very strong (IR=0.60, IC=0.07, pos=0.77), VOL_20D alive (IR=0.48)
    "risk":      0.28,
    # FORCED_SELLING alive (IR=0.60), CEILING_STREAK reversed (IC=-0.30)
    "behavioral": 0.08,
}


# ── Helpers ───────────────────────────────────────────────────────────

def _rank_series(s: pd.Series) -> pd.Series:
    """Cross-sectional percentile rank (0–100) within the series."""
    if s is None or len(s) == 0:
        return s
    return s.rank(pct=True, ascending=True, na_option="keep") * 100


def _rank_desc(s: pd.Series) -> pd.Series:
    """Reverse-rank: high values get low rank (for direction=-1 factors)."""
    if s is None or len(s) == 0:
        return s
    return s.rank(pct=True, ascending=False, na_option="keep") * 100


def _safe_div(a, b, default=0.0):
    if b is None or (isinstance(b, (int, float)) and b == 0):
        return default
    return a / b


def _extract_fin_stmts(cur, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Extract latest BS/IS/CF from financial_statements for each symbol."""
    cur.execute(
        """SELECT DISTINCT ON (fs.symbol, fs.statement_type)
                  fs.symbol, fs.statement_type, fs.period_end, fs.data
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
             AND fs.statement_type IN ('BS', 'IS', 'CF')
             AND fs.frequency = 'quarterly'
           ORDER BY fs.symbol, fs.statement_type, fs.period_end DESC""",
        (symbols,),
    )
    result: dict[str, dict[str, Any]] = {}
    for sym, st, pe, raw in cur.fetchall():
        data = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
        result.setdefault(sym, {})[st] = {"period_end": pe, "data": data}
    return result


def _get_val(data: dict, keywords: list[str], default: Optional[float] = None) -> Optional[float]:
    """Extract numeric value from flat dict by keyword substring match."""
    for k, v in data.items():
        if isinstance(k, str) and any(kw.lower() in k.lower() for kw in keywords):
            if isinstance(v, (int, float)):
                return float(v)
    return default


# ── Tet lunar calendar lookup (approximate dates 2020-2030) ────────────
TET_DATES: dict[int, date] = {
    2020: date(2020, 1, 25),  2021: date(2021, 2, 12),
    2022: date(2022, 2, 1),   2023: date(2023, 1, 22),
    2024: date(2024, 2, 10),  2025: date(2025, 1, 29),
    2026: date(2026, 2, 17),  2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),  2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
}


def _tet_signal(d: date) -> float:
    """Tet proximity: +1 pre-Tet, -0.5 post-Tet, 0 otherwise."""
    tet = TET_DATES.get(d.year)
    if tet is None:
        return 0.0
    days_to = (tet - d).days
    if 5 <= days_to <= 20:
        return 1.0   # retail FOMO window
    if -10 <= days_to < 0:
        return -0.5  # post-Tet selloff
    return 0.0


def _extract_multi_stmts(cur, symbols: list[str], n_quarters: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Extract last N quarters of BS/IS/CF for each symbol (for Piotroski F)."""
    cur.execute(
        """SELECT fs.symbol, fs.statement_type, fs.period_end, fs.data
           FROM financial_statements fs
           WHERE fs.symbol = ANY(%s)
             AND fs.statement_type IN ('BS', 'IS', 'CF')
             AND fs.frequency = 'quarterly'
           ORDER BY fs.symbol, fs.statement_type, fs.period_end DESC""",
        (symbols,),
    )
    result: dict[str, list[dict[str, Any]]] = {}
    for sym, st, pe, raw in cur.fetchall():
        data = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
        result.setdefault(sym, []).append({
            "statement_type": st, "period_end": pe, "data": data,
        })
    # Keep only the first n_quarters per symbol per statement_type
    filtered: dict[str, dict[str, list[dict]]] = {}
    for sym, entries in result.items():
        by_type: dict[str, list[dict]] = {}
        for e in entries:
            by_type.setdefault(e["statement_type"], []).append(e)
        for st in list(by_type.keys()):
            by_type[st] = sorted(by_type[st], key=lambda x: x["period_end"], reverse=True)[:n_quarters]
        filtered[sym] = by_type
    return filtered


# ── Sector-group scoring (unchanged from v1) ─────────────────────────

def compute_value_score(df: pd.DataFrame, group: str) -> pd.Series:
    """Compute Value Score per sector group (unchanged)."""
    if df.empty:
        return pd.Series(dtype=float)
    df = df.copy()
    if group == FINANCIALS:
        components = {
            "pb_inv": df.get("pb_inv", pd.Series(dtype=float)),
            "roe_norm": df.get("roe_norm", pd.Series(dtype=float)),
            "nim": df.get("nim", pd.Series(dtype=float)),
            "fcf_yield": df.get("fcf_yield", pd.Series(dtype=float)),
        }
        weights = [0.35, 0.30, 0.20, 0.15]
    elif group == REAL_ESTATE:
        components = {
            "pe_inv": df.get("pe_inv", pd.Series(dtype=float)),
            "pb_inv": df.get("pb_inv", pd.Series(dtype=float)),
            "evebitda_inv": df.get("evebitda_inv", pd.Series(dtype=float)),
            "debt_inv": df.get("debt_inv", pd.Series(dtype=float)),
            "fcf_yield": df.get("fcf_yield", pd.Series(dtype=float)),
        }
        weights = [0.25, 0.25, 0.20, 0.15, 0.15]
    else:
        components = {
            "pe_inv": df.get("pe_inv", pd.Series(dtype=float)),
            "pb_inv": df.get("pb_inv", pd.Series(dtype=float)),
            "fcf_yield": df.get("fcf_yield", pd.Series(dtype=float)),
            "evebitda_inv": df.get("evebitda_inv", pd.Series(dtype=float)),
            "gross_margin": df.get("gross_margin", pd.Series(dtype=float)),
            "net_margin": df.get("net_margin", pd.Series(dtype=float)),
        }
        weights = [0.20, 0.20, 0.20, 0.15, 0.15, 0.10]
    score = pd.Series(0.0, index=df.index)
    weight_sum = 0
    for col, w in zip(components.keys(), weights):
        series = components[col]
        if series is not None and not series.isna().all():
            ranked = _rank_series(series)
            score += ranked.fillna(0) * w
            weight_sum += w
    if weight_sum > 0:
        score = score / weight_sum
    return score


def compute_quality_score(df: pd.DataFrame, group: str) -> pd.Series:
    """Compute Quality Score per sector group (unchanged)."""
    if df.empty:
        return pd.Series(dtype=float)
    df = df.copy()
    if group == FINANCIALS:
        components = {
            "roe_norm": df.get("roe_norm", pd.Series(dtype=float)),
            "yoy_earnings_growth": df.get("yoy_earnings_growth", pd.Series(dtype=float)),
        }
        weights = [0.50, 0.50]
    elif group == REAL_ESTATE:
        components = {
            "roe_norm": df.get("roe_norm", pd.Series(dtype=float)),
            "net_margin": df.get("net_margin", pd.Series(dtype=float)),
            "yoy_revenue_growth": df.get("yoy_revenue_growth", pd.Series(dtype=float)),
            "yoy_earnings_growth": df.get("yoy_earnings_growth", pd.Series(dtype=float)),
        }
        weights = [0.30, 0.25, 0.25, 0.20]
    else:
        components = {
            "roe_norm": df.get("roe_norm", pd.Series(dtype=float)),
            "yoy_revenue_growth": df.get("yoy_revenue_growth", pd.Series(dtype=float)),
            "yoy_earnings_growth": df.get("yoy_earnings_growth", pd.Series(dtype=float)),
            "accrual_inv": df.get("accrual_inv", pd.Series(dtype=float)),
            "f_score": df.get("f_score", pd.Series(dtype=float)),
            "net_margin": df.get("net_margin", pd.Series(dtype=float)),
        }
        weights = [0.25, 0.20, 0.20, 0.15, 0.10, 0.10]
    score = pd.Series(0.0, index=df.index)
    weight_sum = 0
    for col, w in zip(components.keys(), weights):
        series = components[col]
        if series is not None and not series.isna().all():
            ranked = _rank_series(series)
            score += ranked.fillna(0) * w
            weight_sum += w
    if weight_sum > 0:
        score = score / weight_sum
    return score


def compute_momentum_score(df: pd.DataFrame) -> pd.Series:
    """Momentum Score — same for ALL groups (unchanged)."""
    if df.empty:
        return pd.Series(dtype=float)
    components = {
        "momentum_1m_raw": df.get("momentum_1m_raw", pd.Series(dtype=float)),
        "momentum_3m_raw": df.get("momentum_3m_raw", pd.Series(dtype=float)),
        "momentum_6m_raw": df.get("momentum_6m_raw", pd.Series(dtype=float)),
    }
    weights = [0.40, 0.30, 0.30]
    score = pd.Series(0.0, index=df.index)
    weight_sum = 0
    for col, w in zip(components.keys(), weights):
        series = components[col]
        if series is not None and not series.isna().all():
            ranked = _rank_series(series)
            score += ranked.fillna(0) * w
            weight_sum += w
    if weight_sum > 0:
        score = score / weight_sum
        
    # Cap momentum score if ceiling streak >= 3 (Fix 3)
    if "ceiling_streak" in df.columns:
        score = np.where(df["ceiling_streak"] >= 3, np.minimum(score, 60.0), score)
        
    return pd.Series(score, index=df.index)


# ── New Tier A factor computations ───────────────────────────────────

def compute_factor_scores(
    symbols: list[str],
    score_date: date,
    cur,
    orthogonalizer: Optional[FactorOrthogonalizer] = None,
) -> list[tuple]:
    """Compute VN-core factor scores with new Tier A factors."""
    # 1. Load technical indicators
    cur.execute(
        """SELECT symbol, indicators
           FROM technical_indicators
           WHERE calc_date = %s AND symbol = ANY(%s)""",
        (score_date, symbols),
    )
    tech_map: dict[str, dict] = {}
    for sym, ind_json in cur.fetchall():
        tech_map[sym] = ind_json if isinstance(ind_json, dict) else json.loads(ind_json)
    logger.info("  Loaded technical indicators for %d symbols", len(tech_map))

    # 2. Load OHLCV (400 days back)
    cur.execute(
        """SELECT symbol, time::date, adj_close, volume
           FROM ohlcv
           WHERE time::date <= %s AND time::date >= %s
             AND symbol = ANY(%s)
           ORDER BY symbol, time DESC""",
        (score_date, score_date - timedelta(days=400), symbols),
    )
    ohlcv_map: dict[str, list[tuple[date, float, float]]] = {}
    for sym, dt, ac, vol in cur.fetchall():
        ohlcv_map.setdefault(sym, []).append((dt, float(ac or 0), float(vol or 0)))
    logger.info("  Loaded OHLCV for %d symbols", len(ohlcv_map))

    # 3. Load financial ratios (latest)
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, pe, pb, roe, roa, debt_equity, gross_margin,
                  net_margin, fcf_yield, ev_ebitda,
                  yoy_revenue_growth, yoy_earnings_growth
           FROM financial_ratios
           WHERE symbol = ANY(%s)
           ORDER BY symbol, ratio_date DESC""",
        (symbols,),
    )
    fin_rows = cur.fetchall()
    fin_map: dict[str, dict] = {}
    for r in fin_rows:
        fin_map[r[0]] = {
            "pe": r[1], "pb": r[2], "roe": r[3], "roa": r[4],
            "debt_equity": r[5], "gross_margin": r[6],
            "net_margin": r[7], "fcf_yield": r[8], "ev_ebitda": r[9],
            "yoy_revenue_growth": r[10], "yoy_earnings_growth": r[11],
        }
    logger.info("  Loaded financial ratios for %d symbols", len(fin_map))

    # 4. Load news sentiment
    from app.infrastructure.vendors.vn.news_events import compute_sentiment_5d
    news_sentiment = compute_sentiment_5d(symbols, score_date, cur)
    logger.info("  Loaded news sentiment for %d symbols", len(news_sentiment))

    # 5. Load financial statements
    stmt_data = _extract_fin_stmts(cur, symbols)
    logger.info("  Loaded financial statements for %d symbols", len(stmt_data))

    # 6. Load market cap from stocks table
    cur.execute(
        "SELECT symbol, market_cap FROM stocks WHERE symbol = ANY(%s)",
        (symbols,),
    )
    mcap_map: dict[str, Optional[int]] = dict(cur.fetchall())
    logger.info("  Loaded market cap for %d symbols", len(mcap_map))

    # 7. Load foreign flow (last 30 days per symbol)
    cutoff_30d = score_date - timedelta(days=30)
    cur.execute(
        """SELECT symbol, trade_date, net_value, net_volume
           FROM foreign_flow
           WHERE trade_date >= %s AND trade_date <= %s
             AND symbol = ANY(%s)
           ORDER BY symbol, trade_date""",
        (cutoff_30d, score_date, symbols),
    )
    foreign_map: dict[str, list[dict]] = {}
    for sym, td, nv, nvol in cur.fetchall():
        foreign_map.setdefault(sym, []).append({
            "trade_date": td, "net_value": float(nv or 0), "net_volume": int(nvol or 0),
        })
    logger.info("  Loaded foreign flow for %d symbols", len(foreign_map))

    # 8. Load insider trades (last 30 days)
    cur.execute(
        """SELECT symbol, trade_date,
                  CASE WHEN trade_type IN ('Mua','Đăng ký mua','đăng ký mua') THEN quantity ELSE 0 END as buy_qty,
                  CASE WHEN trade_type IN ('Bán','Đăng ký bán','đăng ký bán') THEN quantity ELSE 0 END as sell_qty
           FROM insider_trades
           WHERE trade_date >= %s AND trade_date <= %s
             AND symbol = ANY(%s)
           ORDER BY symbol, trade_date""",
        (cutoff_30d, score_date, symbols),
    )
    insider_map: dict[str, list[dict]] = {}
    for sym, td, bq, sq in cur.fetchall():
        insider_map.setdefault(sym, []).append({
            "trade_date": td, "buy_qty": int(bq or 0), "sell_qty": int(sq or 0),
        })
    logger.info("  Loaded insider trades for %d symbols", len(insider_map))

    # 9. Load VNINDEX return for conditional momentum
    vnindex_regime = 0.0  # default: neutral
    try:
        cur.execute(
            """SELECT value FROM macro_indicators
               WHERE indicator_name = 'vnindex_return_1m'
                 AND indicator_date = %s
               LIMIT 1""",
            (score_date,),
        )
        row = cur.fetchone()
        if row:
            vnindex_regime = 1.0 if float(row[0]) > 0 else -1.0
    except Exception:
        pass
    # Fallback: try latest available
    if vnindex_regime == 0.0:
        try:
            cur.execute(
                """SELECT value FROM macro_indicators
                   WHERE indicator_name = 'vnindex_return_1m'
                   ORDER BY indicator_date DESC LIMIT 1"""
            )
            row = cur.fetchone()
            if row:
                vnindex_regime = 1.0 if float(row[0]) > 0 else -1.0
        except Exception:
            pass

    # 10. Load ceiling/floor for price limit detection
    cur.execute(
        "SELECT symbol, ceiling, floor FROM stocks WHERE symbol = ANY(%s)",
        (symbols,),
    )
    price_limit_map: dict[str, dict[str, Optional[float]]] = {}
    for sym, ceil, flr in cur.fetchall():
        price_limit_map[sym] = {
            "ceiling": float(ceil) if ceil else None,
            "floor": float(flr) if flr else None,
        }
    logger.info("  Loaded price limits for %d symbols", len(price_limit_map))

    # 11. Load multi-period financial statements (for Piotroski F)
    multi_stmt = _extract_multi_stmts(cur, symbols, n_quarters=5)
    logger.info("  Loaded multi-period statements for %d symbols", len(multi_stmt))

    # 12. Load foreign_flow room_remaining (latest per symbol)
    cur.execute(
        """SELECT DISTINCT ON (symbol) symbol, room_remaining, room_limit, ownership_pct
           FROM foreign_flow
           WHERE symbol = ANY(%s) AND trade_date <= %s
           ORDER BY symbol, trade_date DESC""",
        (symbols, score_date),
    )
    room_map: dict[str, dict[str, float]] = {}
    for sym, rr, rl, op in cur.fetchall():
        room_map[sym] = {
            "room_remaining": float(rr) if rr else 0,
            "room_limit": float(rl) if rl else 0,
            "ownership_pct": float(op) if op else 0,
        }
    logger.info("  Loaded foreign room for %d symbols", len(room_map))

    # ─── Classify symbols into sector groups ────────────────────────
    fin_syms, re_syms, other_syms = [], [], []
    for sym in symbols:
        cur.execute("SELECT industry FROM stocks WHERE symbol = %s", (sym,))
        row = cur.fetchone()
        industry = row[0] if row else None
        g = classify(industry, sym)
        if g == FINANCIALS:
            fin_syms.append(sym)
        elif g == REAL_ESTATE:
            re_syms.append(sym)
        else:
            other_syms.append(sym)

    sym_groups: dict[str, str] = {}
    for sym in fin_syms:
        sym_groups[sym] = FINANCIALS
    for sym in re_syms:
        sym_groups[sym] = REAL_ESTATE
    for sym in other_syms:
        sym_groups[sym] = OTHERS
    logger.info("  Groups: FINANCIALS=%d, REAL_ESTATE=%d, OTHERS=%d",
                len(fin_syms), len(re_syms), len(other_syms))

    # ─── Build raw factor DataFrame ─────────────────────────────────
    records = []
    for sym in symbols:
        tech = tech_map.get(sym, {})
        ohlcv = ohlcv_map.get(sym, [])
        fin = fin_map.get(sym, {})
        stmt = stmt_data.get(sym, {})
        mcap = mcap_map.get(sym)
        foreign = foreign_map.get(sym, [])
        insider = insider_map.get(sym, [])

        row: dict[str, Any] = {"symbol": sym}
        group = sym_groups.get(sym, OTHERS)
        row["sector_group"] = group
        row["ceiling_streak"] = tech.get("ceiling_streak", 0)

        # ── Existing value factor raw inputs ─────────────────────
        pe = fin.get("pe")
        pb = fin.get("pb")
        row["pe_inv"] = 1.0 / pe if pe and pe > 0 else None
        row["pb_inv"] = 1.0 / pb if pb and pb > 0 else None
        row["fcf_yield"] = fin.get("fcf_yield")
        row["gross_margin"] = fin.get("gross_margin")
        row["net_margin"] = fin.get("net_margin")
        row["debt_equity"] = fin.get("debt_equity")
        evebitda = fin.get("ev_ebitda")
        row["evebitda_inv"] = 1.0 / evebitda if evebitda and evebitda > 0 else None

        # ── Existing quality factor raw inputs ───────────────────
        roe = fin.get("roe")
        row["roe_norm"] = roe / 100.0 if roe is not None else None
        row["yoy_revenue_growth"] = fin.get("yoy_revenue_growth")
        row["yoy_earnings_growth"] = fin.get("yoy_earnings_growth")
        row["de_inv"] = 1.0 / row["debt_equity"] if row["debt_equity"] and row["debt_equity"] > 0 else None

        # ── NIM for FINANCIALS ───────────────────────────────────
        if group == FINANCIALS:
            bs = stmt.get("BS", {}).get("data", {})
            inc = stmt.get("IS", {}).get("data", {})
            interest_income = _get_val(inc, ["doanh thu hoạt động tài chính", "6_doanh_thu_hoạt_động_tài_chính"])
            interest_expense = _get_val(inc, ["chi phí tài chính", "8_chi_phí_tài_chính", "trong_đó_chi_phí_lãi_vay"])
            net_interest = (interest_income or 0) - (interest_expense or 0)
            total_assets = _get_val(bs, ["tổng cộng tài sản", "tổng_cộng_tài_sản"])
            row["nim"] = net_interest / total_assets if total_assets and total_assets > 0 else None
            row["car"] = None
            row["npl_inv"] = None
            row["loan_growth"] = None
            row["cir_inv"] = None

        # ── Inventory turnover for REAL_ESTATE ──────────────────
        if group == REAL_ESTATE:
            bs = stmt.get("BS", {}).get("data", {})
            inc = stmt.get("IS", {}).get("data", {})
            _rev = _get_val(inc, ["doanh thu thuần", "3_doanh_thu_thuần"])
            _inv = _get_val(bs, ["hàng tồn kho", "iv_hàng_tồn_kho", "1_hàng_tồn_kho", "hàng tồn kho ròng"])
            if _rev is not None and _inv is not None and _inv > 0:
                row["inv_turnover"] = _rev / _inv
            else:
                row["inv_turnover"] = None

        # ── Existing accrual / F_Score (for backward compat) ────
        cf = stmt.get("CF", {}).get("data", {})
        cfo = _get_val(cf, ["lưu chuyển tiền thuần từ hoạt động kinh doanh", "lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh"])
        ni = _get_val(stmt.get("IS", {}).get("data", {}), ["lợi nhuận sau thuế", "18_lợi_nhuận_sau_thuế"])
        ta = _get_val(stmt.get("BS", {}).get("data", {}), ["tổng cộng tài sản", "tổng_cộng_tài_sản"])
        if ni is not None and cfo is not None and ta is not None and ta > 0:
            accrual = (ni - cfo) / ta
            row["accrual_inv"] = 1.0 / abs(accrual) if accrual != 0 else 10.0
        else:
            row["accrual_inv"] = None

        if group == OTHERS:
            bs_data = stmt.get("BS", {}).get("data", {})
            inc_data = stmt.get("IS", {}).get("data", {})
            fscore = 0
            if roe is not None and roe > 0:
                fscore += 1
            if cfo is not None and cfo > 0:
                fscore += 1
            row["f_score"] = fscore
        else:
            row["f_score"] = None

        # ── Momentum raw inputs (existing) ──────────────────────
        if len(ohlcv) >= 130:
            closes = [c for _, c, _ in ohlcv]
            c = np.array(closes)
            c0 = c[0] if c[0] > 0 else None
            c20 = c[20] if len(c) > 20 and c[20] > 0 else None
            c60 = c[60] if len(c) > 60 and c[60] > 0 else None
            c125 = c[125] if len(c) > 125 and c[125] > 0 else None
            if c0 and c20:
                row["momentum_1m_raw"] = c0 / c20 - 1
            if c20 and c60:
                row["momentum_3m_raw"] = c20 / c60 - 1
            if c20 and c125:
                row["momentum_6m_raw"] = c20 / c125 - 1

        # ── Volatility / Liquidity (existing) ───────────────────
        for key in ["volatility_20d", "volatility_60d", "volume_ratio", "mfi_14"]:
            val = tech.get(key)
            if val is not None:
                row[key] = val
        if len(ohlcv) >= 21:
            closes_arr = np.array([c for _, c, _ in ohlcv[:21]])
            vols_arr = np.array([v for _, _, v in ohlcv[:21]])
            returns = abs(np.diff(closes_arr) / closes_arr[:-1])
            dollar_vol = closes_arr[:20] * vols_arr[:20]
            illiq = np.nanmean(returns / dollar_vol) if dollar_vol.sum() > 0 else np.nan
            row["amihud_illiq"] = float(illiq) if not np.isnan(illiq) else None
        ns = news_sentiment.get(sym)
        if ns is not None:
            row["news_sentiment_5d"] = ns

        # ═══════════════════════════════════════════════════════════
        # NEW TIER A FACTOR RAW VALUES
        # ═══════════════════════════════════════════════════════════

        # ── EARNINGS_YIELD: EPS_TTM / price ──────────────────────
        # If PE > 0: earnings_yield = 1/PE
        # If PE <= 0 or None: try NI_ttm/market_cap from statements
        if pe is not None and pe > 0:
            row["earnings_yield_raw"] = 1.0 / pe
        elif ni is not None and mcap is not None and mcap > 0:
            # Annualize latest quarter NI
            row["earnings_yield_raw"] = (ni * 4) / float(mcap)
        else:
            row["earnings_yield_raw"] = None

        # ── ACCRUAL_RATIO: -(NI - CFO) / TA (high = good quality) ─
        if ni is not None and cfo is not None and ta is not None and ta > 0:
            row["accrual_ratio_raw"] = -(ni - cfo) / ta
        else:
            row["accrual_ratio_raw"] = None

        # ── CFO_TO_NI: cash conversion quality ────────────────────
        if cfo is not None and ni is not None and ni != 0:
            row["cfo_to_ni_raw"] = cfo / abs(ni)
        else:
            row["cfo_to_ni_raw"] = None

        # ── CONDITIONAL_MOM: momentum * market regime ─────────────
        mom_3m = row.get("momentum_3m_raw")
        if mom_3m is not None:
            row["conditional_mom_raw"] = mom_3m * (1.0 + 0.5 * vnindex_regime)
        else:
            row["conditional_mom_raw"] = None

        # ── DOLLAR_VOL_TREND: 5d avg / 20d avg - 1 ───────────────
        if len(ohlcv) >= 20:
            closes_arr = np.array([c for _, c, _ in ohlcv[:20]])
            vols_arr = np.array([v for _, _, v in ohlcv[:20]])
            dollar_vols = closes_arr * vols_arr
            dvol_5d = np.mean(dollar_vols[:5]) if len(dollar_vols) >= 5 else None
            dvol_20d = np.mean(dollar_vols)
            if dvol_5d is not None and dvol_20d is not None and dvol_20d > 0:
                row["dollar_vol_trend_raw"] = dvol_5d / dvol_20d - 1
            else:
                row["dollar_vol_trend_raw"] = None
        else:
            row["dollar_vol_trend_raw"] = None

        # ── FOREIGN_NET_5D: cumulative foreign net value / mcap ──
        if foreign and mcap and mcap > 0:
            recent_foreign = sorted(foreign, key=lambda x: x["trade_date"], reverse=True)[:5]
            net_5d = sum(f["net_value"] for f in recent_foreign)
            row["foreign_net_5d_raw"] = net_5d / float(mcap)
        else:
            row["foreign_net_5d_raw"] = None

        # ── FOREIGN_ACCUM: consecutive foreign buy streak ────────
        if foreign:
            sorted_f = sorted(foreign, key=lambda x: x["trade_date"], reverse=True)
            streak = 0
            for f in sorted_f:
                if f["net_value"] > 0:
                    streak += 1
                else:
                    break
            row["foreign_accum_raw"] = streak / 10.0  # normalize to [0, ~1]
        else:
            row["foreign_accum_raw"] = None

        # ── INSIDER_NET_30D: net buy / total shares ─────────────
        if insider and mcap and mcap > 0:
            total_buy = sum(i["buy_qty"] for i in insider)
            total_sell = sum(i["sell_qty"] for i in insider)
            net_shares = total_buy - total_sell
            # Estimate total shares from mcap / price
            if len(ohlcv) > 0 and ohlcv[0][1] > 0:
                est_shares = float(mcap) / ohlcv[0][1]
                if est_shares > 0:
                    row["insider_net_30d_raw"] = net_shares / est_shares
                else:
                    row["insider_net_30d_raw"] = None
            else:
                row["insider_net_30d_raw"] = None
        else:
            row["insider_net_30d_raw"] = None

        # ── SIZE: log(market_cap) ──────────────────────────────
        if mcap is not None and mcap > 0:
            row["size_raw"] = np.log(float(mcap))
        else:
            row["size_raw"] = None

        # ═══════════════════════════════════════════════════════════
        # TIER B + C + SALVAGE FACTOR RAW VALUES
        # ═══════════════════════════════════════════════════════════

        # ── EARNINGS_SURPRISE: YoY earnings growth (proxy) ─────────
        row["earnings_surprise_raw"] = fin.get("yoy_earnings_growth")

        # ── ALTMAN Z' for EM: 6.56*WC/TA + 3.26*RE/TA + 6.72*EBIT/TA + 1.05*BV/TL
        bs_data = stmt.get("BS", {}).get("data", {})
        inc_data = stmt.get("IS", {}).get("data", {})
        ca = _get_val(bs_data, ["a_tài_sản_ngắn_hạn", "tài sản ngắn hạn"])
        cl = _get_val(bs_data, ["i_nợ_ngắn_hạn", "nợ ngắn hạn"])
        re = _get_val(bs_data, ["lợi nhuận sau thuế chưa phân phối", "lợi nhuận chưa phân phối"])
        ebit = _get_val(inc_data, ["lợi nhuận từ hoạt động kinh doanh", "ebit", "lợi nhuận thuần từ hoạt động kinh doanh"])
        eq = _get_val(bs_data, ["vốn chủ sở hữu", "d_vốn_chủ_sở_hữu", "i_vốn_chủ_sở_hữu"])
        tl = _get_val(bs_data, ["c_nợ_phải_trả", "tổng nợ phải trả", "nợ phải trả"])
        if all(v is not None for v in [ca, cl, ta, re, ebit, eq, tl]) and ta > 0 and tl > 0:
            wc = ca - cl
            altman_z = (
                6.56 * (wc / ta) +
                3.26 * (re / ta) +
                6.72 * (ebit / ta) +
                1.05 * (eq / tl)
            )
            row["altman_z_raw"] = altman_z
        else:
            row["altman_z_raw"] = None

        # ── PIOTROSKI F-SCORE (full 9-point) ──────────────────────
        piotroski = 0
        multi = multi_stmt.get(sym, {})
        bs_hist = multi.get("BS", [])
        is_hist = multi.get("IS", [])
        cf_hist = multi.get("CF", [])

        # Get current and 4-quarter-ago values
        def _stmt_val(hist: list[dict], st: str, keywords: list[str]) -> Optional[float]:
            for h in hist:
                if h["statement_type"] == st and h.get("data"):
                    v = _get_val(h["data"], keywords)
                    if v is not None:
                        return v
            return None

        def _stmt_val_offset(hist: list[dict], st: str, keywords: list[str], offset: int) -> Optional[float]:
            """Value from Nth statement back (offset=0 = latest)."""
            matches = [h for h in hist if h["statement_type"] == st]
            if offset < len(matches):
                return _get_val(matches[offset].get("data", {}), keywords)
            return None

        # 1. ROA > 0
        roa_q = _get_val(inc_data, ["lợi nhuận sau thuế", "18_lợi_nhuận_sau_thuế"])
        roa_ta = ta
        if roa_q is not None and roa_ta is not None and roa_ta > 0 and roa_q > 0:
            piotroski += 1

        # 2. CFO > 0
        if cfo is not None and cfo > 0:
            piotroski += 1

        # 3. ΔROA > 0 (ROA improved)
        roa_prev = _stmt_val_offset(is_hist, "IS", ["lợi nhuận sau thuế", "18_lợi_nhuận_sau_thuế"], 4)
        ta_prev = _stmt_val_offset(bs_hist, "BS", ["tổng cộng tài sản", "tổng_cộng_tài_sản"], 4)
        if roa_q is not None and roa_ta is not None and roa_ta > 0:
            cur_roa = roa_q / roa_ta
            if roa_prev is not None and ta_prev is not None and ta_prev > 0:
                prev_roa = roa_prev / ta_prev
                if cur_roa > prev_roa:
                    piotroski += 1

        # 4. Accrual: CFO > NI (quality of earnings)
        if cfo is not None and ni is not None and cfo > ni:
            piotroski += 1

        # 5. ΔLeverage: long-term debt / total assets decreased
        lt_debt = _get_val(bs_data, ["vay và nợ thuê tài chính dài hạn", "nợ dài hạn", "vay dài hạn"])
        lt_debt_prev = _stmt_val_offset(bs_hist, "BS", ["vay và nợ thuê tài chính dài hạn", "nợ dài hạn", "vay dài hạn"], 4)
        if lt_debt is not None and ta is not None and ta > 0:
            cur_lev = lt_debt / ta
            if lt_debt_prev is not None and ta_prev is not None and ta_prev > 0:
                prev_lev = lt_debt_prev / ta_prev
                if cur_lev < prev_lev:
                    piotroski += 1

        # 6. ΔLiquidity: current ratio increased
        if ca is not None and cl is not None and cl > 0:
            cur_cr = ca / cl
            ca_prev = _stmt_val_offset(bs_hist, "BS", ["a_tài_sản_ngắn_hạn", "tài sản ngắn hạn"], 4)
            cl_prev = _stmt_val_offset(bs_hist, "BS", ["i_nợ_ngắn_hạn", "nợ ngắn hạn"], 4)
            if ca_prev is not None and cl_prev is not None and cl_prev > 0:
                prev_cr = ca_prev / cl_prev
                if cur_cr > prev_cr:
                    piotroski += 1

        # 7. No new shares (equity / par value ~ unchanged)
        # Simplified: check if total equity hasn't grown faster than retained earnings
        eq_prev = _stmt_val_offset(bs_hist, "BS", ["vốn chủ sở hữu", "d_vốn_chủ_sở_hữu", "i_vốn_chủ_sở_hữu"], 4)
        if eq is not None and eq_prev is not None and eq <= eq_prev * 1.02:
            piotroski += 1

        # 8. ΔMargin: gross margin increased
        gm_cur = _get_val(inc_data, ["giá vốn hàng bán", "4_giá_vốn_hàng_bán"])
        rev_cur = _get_val(inc_data, ["doanh thu thuần", "3_doanh_thu_thuần"])
        gm_prev = _stmt_val_offset(is_hist, "IS", ["giá vốn hàng bán", "4_giá_vốn_hàng_bán"], 4)
        rev_prev = _stmt_val_offset(is_hist, "IS", ["doanh thu thuần", "3_doanh_thu_thuần"], 4)
        if rev_cur is not None and gm_cur is not None and rev_cur > 0:
            cur_gm_pct = (rev_cur - gm_cur) / rev_cur
            if rev_prev is not None and gm_prev is not None and rev_prev > 0:
                prev_gm_pct = (rev_prev - gm_prev) / rev_prev
                if cur_gm_pct > prev_gm_pct:
                    piotroski += 1

        # 9. ΔTurnover: asset turnover increased
        if rev_cur is not None and ta is not None and ta > 0:
            cur_at = rev_cur / ta
            if rev_prev is not None and ta_prev is not None and ta_prev > 0:
                prev_at = rev_prev / ta_prev
                if cur_at > prev_at:
                    piotroski += 1

        row["piotroski_f_raw"] = float(piotroski)

        # ── HML_REAL: book-to-market ratio (salvage academic) ──────
        if eq is not None and mcap is not None and mcap > 0:
            row["hml_real_raw"] = eq / float(mcap)
        else:
            row["hml_real_raw"] = None

        # ── TET_WINDOW: seasonal signal ─────────────────────────────
        row["tet_window_raw"] = _tet_signal(score_date)

        # ── CEILING_STREAK: % ceiling hits in last 10d ─────────────
        price_limits = price_limit_map.get(sym, {})
        ceiling = price_limits.get("ceiling")
        floor = price_limits.get("floor")
        if ceiling and len(ohlcv) >= 10:
            # Need high prices — approximate with OHLCV first entries
            ceil_hits = 0
            for i in range(min(10, len(ohlcv))):
                if ohlcv[i][1] >= ceiling:
                    ceil_hits += 1
            row["ceiling_streak_raw"] = ceil_hits / 10.0
        else:
            row["ceiling_streak_raw"] = None

        # ── FORCED_SELLING: floor hits + volume spike → reversal ──
        if floor and len(ohlcv) >= 10:
            floor_hits = 0
            for i in range(5):
                if i < len(ohlcv) and ohlcv[i][1] <= floor:
                    floor_hits += 1
            vol_5d = np.mean([v for _, _, v in ohlcv[:5]]) if len(ohlcv) >= 5 else 0
            vol_20d = np.mean([v for _, _, v in ohlcv[:20]]) if len(ohlcv) >= 20 else 1
            vol_spike = vol_5d / max(vol_20d, 1)
            if floor_hits >= 2 and vol_spike > 3:
                row["forced_selling_raw"] = 1.0  # reversal opportunity
            else:
                row["forced_selling_raw"] = 0.0
        else:
            row["forced_selling_raw"] = None

        # ── FOREIGN_ROOM: scarcity signal ─────────────────────────
        room_info = room_map.get(sym, {})
        room_remaining = room_info.get("room_remaining", 0)
        room_limit = room_info.get("room_limit", 0)
        if room_limit > 0:
            room_pct = room_remaining / room_limit
            if room_pct < 0.05:
                row["foreign_room_raw"] = -1.0  # scarce → downside risk
            elif room_pct > 0.30:
                row["foreign_room_raw"] = 0.5   # ample → foreign can buy
            else:
                row["foreign_room_raw"] = 0.0
        else:
            row["foreign_room_raw"] = None

        records.append(row)

    if not records:
        return []

    df = pd.DataFrame(records).set_index("symbol")

    # ─── Compute existing per-group scores ─────────────────────────
    all_value = pd.Series(0.0, index=df.index)
    all_quality = pd.Series(0.0, index=df.index)
    for group_name, group_df in df.groupby("sector_group"):
        if group_df.empty:
            continue
        idx = group_df.index
        all_value.loc[idx] = compute_value_score(group_df, group_name)
        all_quality.loc[idx] = compute_quality_score(group_df, group_name)
    m = compute_momentum_score(df)

    # ─── Composite (V1 backward compatible) ─────────────────────────
    composite = 0.4 * all_value + 0.4 * all_quality + 0.2 * m
    percentile = _rank_series(composite.fillna(0))

    # ─── Rank ALL factors cross-sectionally ─────────────────────────
    factor_ranks: dict[str, pd.Series] = {}

    # Existing factors
    for col in ["pe_inv", "pb_inv", "fcf_yield", "evebitda_inv",
                "gross_margin", "net_margin", "roe_norm",
                "yoy_revenue_growth", "yoy_earnings_growth",
                "momentum_1m_raw", "momentum_3m_raw", "momentum_6m_raw",
                "volatility_20d", "volatility_60d", "amihud_illiq",
                "volume_ratio"]:
        if col in df.columns:
            factor_ranks[col] = _rank_series(df[col])

    # All factors ranked with direction from VN_FACTORS registry
    new_factor_map = {
        "earnings_yield_raw": "EARN_YLD",
        "accrual_ratio_raw": "ACCRUAL",
        "cfo_to_ni_raw": "CFO_TO_NI",
        "conditional_mom_raw": "COND_MOM",
        "dollar_vol_trend_raw": "DVOL_TREND",
        "foreign_net_5d_raw": "FOREIGN_NET_5D",
        "foreign_accum_raw": "FOREIGN_ACCUM",
        "insider_net_30d_raw": "INSIDER_NET_30D",
        "size_raw": "SIZE",
        # Tier B
        "earnings_surprise_raw": "EARN_SURP",
        "altman_z_raw": "ALTMAN_Z",
        "piotroski_f_raw": "PIOTROSKI_F",
        # Salvage
        "hml_real_raw": "HML_REAL",
        # Tier C
        "tet_window_raw": "TET_WINDOW",
        "ceiling_streak_raw": "CEILING_STREAK",
        "forced_selling_raw": "FORCED_SELLING",
        "foreign_room_raw": "FOREIGN_ROOM",
    }
    for raw_col, factor_id in new_factor_map.items():
        if raw_col in df.columns:
            meta = VN_FACTORS.get(factor_id, {})
            direction = meta.get("direction", 1)
            if direction == -1:
                factor_ranks[raw_col] = _rank_desc(df[raw_col])
            else:
                factor_ranks[raw_col] = _rank_series(df[raw_col])

    # ─── Build factor_id → raw_col map for all factors ──────────────
    # Covers both new_factor_map and legacy factors
    ALL_FACTOR_MAP: dict[str, str] = {
        # Value
        "EARN_YLD": "earnings_yield_raw",
        "PE_INV": "pe_inv",
        "PB_INV": "pb_inv",
        "FCF_YLD": "fcf_yield",
        "EVEBITDA_INV": "evebitda_inv",
        "HML_REAL": "hml_real_raw",
        # Quality
        "ACCRUAL": "accrual_ratio_raw",
        "CFO_TO_NI": "cfo_to_ni_raw",
        "ROE_NORM": "roe_norm",
        "GM": "gross_margin",
        "NM": "net_margin",
        "YOY_REV": "yoy_revenue_growth",
        "YOY_EARN": "yoy_earnings_growth",
        "PIOTROSKI_F": "piotroski_f_raw",
        # Momentum
        "MOM_3M": "momentum_3m_raw",
        "MOM_6M": "momentum_6m_raw",
        "COND_MOM": "conditional_mom_raw",
        # Liquidity
        "AMIHUD": "amihud_illiq",
        "DVOL_TREND": "dollar_vol_trend_raw",
        # Earnings
        "EARN_SURP": "earnings_surprise_raw",
        # Distress
        "ALTMAN_Z": "altman_z_raw",
        # Flow
        "FOREIGN_NET_5D": "foreign_net_5d_raw",
        "FOREIGN_ACCUM": "foreign_accum_raw",
        "INSIDER_NET_30D": "insider_net_30d_raw",
        "FOREIGN_ROOM": "foreign_room_raw",
        # Behavioral
        "TET_WINDOW": "tet_window_raw",
        "CEILING_STREAK": "ceiling_streak_raw",
        "FORCED_SELLING": "forced_selling_raw",
        # Risk
        "SIZE": "size_raw",
        "VOL_20D": "volatility_20d",
        "VOL_60D": "volatility_60d",
        # Legacy aliases
        "MOM_1M": "momentum_1m_raw",
        "VOLUME_RATIO": "volume_ratio",
    }

    # ─── Apply factor orthogonalization (optional) ──────────────────
    def _build_factor_matrix(
        fr: dict[str, pd.Series],
        fid_map: dict[str, str],
    ) -> pd.DataFrame:
        """Build a factor_id-columned DataFrame from factor_ranks dict."""
        components: dict[str, pd.Series] = {}
        for fid, raw_col in fid_map.items():
            if raw_col in fr:
                components[fid] = fr[raw_col]
        if not components:
            return pd.DataFrame()
        return pd.DataFrame(components)

    def _update_factor_ranks_from_matrix(
        fr: dict[str, pd.Series],
        orth_df: pd.DataFrame,
        fid_map: dict[str, str],
    ) -> dict[str, pd.Series]:
        """Replace factor_ranks entries with orthogonalized values."""
        rev_map = {raw: fid for fid, raw in fid_map.items()}
        for raw_col in list(fr.keys()):
            fid = rev_map.get(raw_col)
            if fid and fid in orth_df.columns:
                fr[raw_col] = orth_df[fid]
        return fr

    if orthogonalizer is not None:
        factor_matrix = _build_factor_matrix(factor_ranks, ALL_FACTOR_MAP)
        if not factor_matrix.empty:
            # Ensure columns match what orthogonalizer expects (factor_ids)
            available = [c for c in factor_matrix.columns if not factor_matrix[c].isna().all()]
            if len(available) >= 3:
                orth_matrix = orthogonalizer.transform(factor_matrix[available])
                factor_ranks = _update_factor_ranks_from_matrix(
                    factor_ranks, orth_matrix, ALL_FACTOR_MAP,
                )
                logger.info(
                    "Orthogonalization applied: %d factors → %d columns",
                    factor_matrix.shape[1], orth_matrix.shape[1],
                )

    # ─── Compute group-level scores from (orthogonalized) ranks ─────
    def group_mean_rank(factor_ids: list[str]) -> pd.Series:
        """Average percentile rank across a group of factors."""
        valid = []
        for fid in factor_ids:
            raw_col = ALL_FACTOR_MAP.get(fid) or {v: k for k, v in new_factor_map.items()}.get(fid)
            if raw_col and raw_col in factor_ranks:
                valid.append(factor_ranks[raw_col])
        if not valid:
            return pd.Series(dtype=float)
        stack = pd.concat(valid, axis=1)
        return stack.mean(axis=1, skipna=True).fillna(0)

    # Compute group scores from individual factor ranks
    value_group = group_mean_rank(["EARN_YLD", "PE_INV", "PB_INV", "FCF_YLD", "EVEBITDA_INV", "HML_REAL"])
    quality_group = group_mean_rank(["ACCRUAL", "CFO_TO_NI", "ROE_NORM", "GM", "NM", "YOY_REV", "YOY_EARN", "PIOTROSKI_F"])
    momentum_group = group_mean_rank(["MOM_3M", "MOM_6M", "COND_MOM"])
    earnings_group = group_mean_rank(["EARN_SURP"])
    flow_group = group_mean_rank(["FOREIGN_NET_5D", "FOREIGN_ACCUM", "INSIDER_NET_30D", "FOREIGN_ROOM"])
    liquidity_group = group_mean_rank(["AMIHUD", "DVOL_TREND"])
    distress_group = group_mean_rank(["ALTMAN_Z"])
    risk_group = group_mean_rank(["SIZE", "VOL_20D", "VOL_60D"])
    behavioral_group = group_mean_rank(["TET_WINDOW", "CEILING_STREAK", "FORCED_SELLING"])

    # ─── Extended composite (V2) — weighted across all 9 groups ────
    comp_v2 = (
        COMPOSITE_WEIGHTS["value"] * value_group.fillna(0) +
        COMPOSITE_WEIGHTS["quality"] * quality_group.fillna(0) +
        COMPOSITE_WEIGHTS["momentum"] * momentum_group.fillna(0) +
        COMPOSITE_WEIGHTS["earnings"] * earnings_group.fillna(0) +
        COMPOSITE_WEIGHTS["flow"] * flow_group.fillna(0) +
        COMPOSITE_WEIGHTS["liquidity"] * liquidity_group.fillna(0) +
        COMPOSITE_WEIGHTS["distress"] * distress_group.fillna(0) +
        COMPOSITE_WEIGHTS["risk"] * risk_group.fillna(0) +
        COMPOSITE_WEIGHTS["behavioral"] * behavioral_group.fillna(0)
    )
    comp_v2 = comp_v2.fillna(0)

    # ─── Build output rows with new columns ─────────────────────────
    output_rows = []
    for sym in records:
        sym_idx = sym["symbol"]
        s = sym["sector_group"]

        # Legacy scores (same as v1)
        vs = all_value.get(sym_idx, 0.0)
        qs = all_quality.get(sym_idx, 0.0)
        ms = m.get(sym_idx, 0.0)
        cs = composite.get(sym_idx, 0.0)
        pr = percentile.get(sym_idx, 0.0)

        # Legacy factor sub-scores (same as v1)
        value_scores = []
        if s == FINANCIALS:
            for c in ["pb_inv", "roe_norm", "nim", "fcf_yield"]:
                val = None
                r = factor_ranks.get(c)
                if r is not None and sym_idx in r.index:
                    vv = r.get(sym_idx)
                    val = float(vv) if pd.notna(vv) else None
                value_scores.append(val)
        elif s == REAL_ESTATE:
            for c in ["pe_inv", "pb_inv", "evebitda_inv", "fcf_yield"]:
                val = None
                r2 = factor_ranks.get(c)
                if r2 is not None and sym_idx in r2.index:
                    vv = r2.get(sym_idx)
                    val = float(vv) if pd.notna(vv) else None
                value_scores.append(val)
        else:
            for c in ["pe_inv", "pb_inv", "fcf_yield", "evebitda_inv", "gross_margin", "net_margin"]:
                val = None
                r3 = factor_ranks.get(c)
                if r3 is not None and sym_idx in r3.index:
                    vv = r3.get(sym_idx)
                    val = float(vv) if pd.notna(vv) else None
                value_scores.append(val)
        valid_value_scores = [v for v in value_scores if v is not None]
        value_score_out = float(np.mean(valid_value_scores)) if valid_value_scores else None

        quality_scores = []
        if s == FINANCIALS:
            for c in ["roe_norm", "yoy_earnings_growth"]:
                val = None
                r4 = factor_ranks.get(c)
                if r4 is not None and sym_idx in r4.index:
                    vv = r4.get(sym_idx)
                    val = float(vv) if pd.notna(vv) else None
                quality_scores.append(val)
        elif s == REAL_ESTATE:
            for c in ["roe_norm", "net_margin", "yoy_revenue_growth", "yoy_earnings_growth"]:
                val = None
                r5 = factor_ranks.get(c)
                if r5 is not None and sym_idx in r5.index:
                    vv = r5.get(sym_idx)
                    val = float(vv) if pd.notna(vv) else None
                quality_scores.append(val)
        else:
            for c in ["roe_norm", "yoy_revenue_growth", "yoy_earnings_growth", "net_margin"]:
                val = None
                r6 = factor_ranks.get(c)
                if r6 is not None and sym_idx in r6.index:
                    vv = r6.get(sym_idx)
                    val = float(vv) if pd.notna(vv) else None
                quality_scores.append(val)
        valid_quality_scores = [v for v in quality_scores if v is not None]
        quality_score_out = float(np.mean(valid_quality_scores)) if valid_quality_scores else None

        # Legacy momentum sub-scores
        mom1_col = factor_ranks.get("momentum_1m_raw")
        mom3_col = factor_ranks.get("momentum_3m_raw")
        mom6_col = factor_ranks.get("momentum_6m_raw")
        mom1 = float(mom1_col.get(sym_idx)) if mom1_col is not None and sym_idx in mom1_col.index and pd.notna(mom1_col.get(sym_idx)) else None
        mom3 = float(mom3_col.get(sym_idx)) if mom3_col is not None and sym_idx in mom3_col.index and pd.notna(mom3_col.get(sym_idx)) else None
        mom6 = float(mom6_col.get(sym_idx)) if mom6_col is not None and sym_idx in mom6_col.index and pd.notna(mom6_col.get(sym_idx)) else None

        # Legacy volatility score
        vol_scores = []
        for c in ["volatility_20d", "volatility_60d"]:
            r7 = factor_ranks.get(c)
            if r7 is not None and sym_idx in r7.index:
                vv = r7.get(sym_idx)
                vol_scores.append(float(vv) if pd.notna(vv) else None)
        valid_vol = [v for v in vol_scores if v is not None]
        volatility_score_out = float(np.mean(valid_vol)) if valid_vol else None

        # Legacy liquidity score
        liq_scores = []
        for c in ["amihud_illiq", "volume_ratio"]:
            r8 = factor_ranks.get(c)
            if r8 is not None and sym_idx in r8.index:
                vv = r8.get(sym_idx)
                liq_scores.append(float(vv) if pd.notna(vv) else None)
        valid_liq = [v for v in liq_scores if v is not None]
        liquidity_score_out = float(np.mean(valid_liq)) if valid_liq else None

        # ── NEW: Tier A score values (individual factor ranks) ────
        def _factor_val(raw_col: str) -> Optional[float]:
            rs = factor_ranks.get(raw_col)
            if rs is not None and sym_idx in rs.index:
                vv = rs.get(sym_idx)
                return float(vv) if pd.notna(vv) else None
            return None

        _earn = _factor_val("earnings_yield_raw")
        _acc = _factor_val("accrual_ratio_raw")
        _ff = _factor_val("foreign_net_5d_raw")
        _ins = _factor_val("insider_net_30d_raw")
        _cm = _factor_val("conditional_mom_raw")
        _es = _factor_val("earnings_surprise_raw")  # earnings_surprise_score
        _dz = _factor_val("altman_z_raw")            # distress_score
        _pf = _factor_val("piotroski_f_raw")          # piotroski_score

        sz_raw = factor_ranks.get("size_raw")
        _sz = float(sz_raw.get(sym_idx)) if sz_raw is not None and sym_idx in sz_raw.index and pd.notna(sz_raw.get(sym_idx)) else None

        # ── Build factor_details JSONB ─────────────────────────────
        details = {}
        for col_name, rank_series in factor_ranks.items():
            if sym_idx in rank_series.index:
                vv = rank_series.get(sym_idx)
                if pd.notna(vv):
                    details[col_name] = round(float(vv), 1)
        details["value_score"] = round(float(vs), 1) if pd.notna(vs) else None
        details["quality_score"] = round(float(qs), 1) if pd.notna(qs) else None
        details["momentum_score"] = round(float(ms), 1) if pd.notna(ms) else None
        details = {k: v for k, v in details.items() if v is not None}
        details_json = json.dumps(details, ensure_ascii=False)

        output_rows.append((
            sym_idx, score_date,
            float(value_score_out) if value_score_out is not None else (float(vs) if pd.notna(vs) else None),
            float(quality_score_out) if quality_score_out is not None else (float(qs) if pd.notna(qs) else None),
            float(mom1) if mom1 is not None else None,
            float(mom3) if mom3 is not None else None,
            float(mom6) if mom6 is not None else None,
            float(_sz) if _sz is not None else None,
            float(volatility_score_out) if volatility_score_out is not None else None,
            float(liquidity_score_out) if liquidity_score_out is not None else None,
            float(cs) if pd.notna(cs) else None,
            float(pr) if pd.notna(pr) else None,
            # NEW: Tier A columns
            _earn, _acc, _ff, _ins, _cm,
            # NEW: Tier B columns
            _es, _dz, _pf,
            details_json,
        ))

    return output_rows


def refresh_all(
    score_date: Optional[date] = None,
    orthogonalizer: Optional[FactorOrthogonalizer] = None,
) -> dict:
    """Full refresh: compute factor scores for all HOSE symbols."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        if score_date is None:
            cur.execute("SELECT MAX(calc_date) FROM technical_indicators")
            score_date = cur.fetchone()[0]

        # Liquid Universe Filter (Fix 2)
        # adtv_val >= 5,000,000 (representing 5 billion VND/day, since prices are divided by 1000 in DB)
        # trading_days >= 45 in the last 60 calendar days (approx 90 calendar days lookback)
        start_20d = score_date - timedelta(days=30)
        start_60d = score_date - timedelta(days=90)
        
        cur.execute("""
            WITH adtv AS (
                SELECT 
                    ticker,
                    AVG(close_adj * volume_total) as adtv_val
                FROM market_data_daily
                WHERE date >= %s AND date <= %s
                GROUP BY ticker
            ),
            trades AS (
                SELECT 
                    ticker,
                    COUNT(CASE WHEN volume_total > 0 THEN 1 END) as trading_days
                FROM market_data_daily
                WHERE date >= %s AND date <= %s
                GROUP BY ticker
            )
            SELECT s.symbol FROM stocks s
            JOIN adtv a ON s.symbol = a.ticker
            JOIN trades t ON s.symbol = t.ticker
            WHERE s.exchange IN ('HOSE','HSX')
              AND s.trading_status = 'NORMAL'
              AND a.adtv_val >= 5000000
              AND t.trading_days >= 45
            ORDER BY s.symbol
        """, (start_20d, score_date, start_60d, score_date))
        symbols = [r[0] for r in cur.fetchall()]
        
        if not symbols:
            logger.info("Liquid universe query returned empty, falling back to all HOSE/HSX symbols")
            cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
            symbols = [r[0] for r in cur.fetchall()]
            
        logger.info("Computing factor scores for %d symbols (liquid universe)", len(symbols))

        cur.execute("DELETE FROM factor_scores")
        logger.info("  Deleted %d old factor scores", cur.rowcount)
        conn.commit()

        rows = compute_factor_scores(symbols, score_date, cur, orthogonalizer=orthogonalizer)
        if not rows:
            logger.warning("No factor scores computed")
            return {"rows": 0, "symbols": 0}

        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO factor_scores
               (symbol, score_date,
                value_score, quality_score, momentum_1m, momentum_3m, momentum_12m,
                size_score, volatility_score, liquidity_score,
                composite_score, percentile,
                earnings_yield_score, accrual_score,
                foreign_flow_score, insider_score, conditional_mom_score,
                earnings_surprise_score, distress_score, piotroski_score,
                factor_details)
               VALUES %s
               ON CONFLICT (symbol, score_date)
               DO UPDATE SET
                   value_score = EXCLUDED.value_score,
                   quality_score = EXCLUDED.quality_score,
                   momentum_1m = EXCLUDED.momentum_1m,
                   momentum_3m = EXCLUDED.momentum_3m,
                   momentum_12m = EXCLUDED.momentum_12m,
                   size_score = EXCLUDED.size_score,
                   volatility_score = EXCLUDED.volatility_score,
                   liquidity_score = EXCLUDED.liquidity_score,
                   composite_score = EXCLUDED.composite_score,
                   percentile = EXCLUDED.percentile,
                   earnings_yield_score = EXCLUDED.earnings_yield_score,
                   accrual_score = EXCLUDED.accrual_score,
                   foreign_flow_score = EXCLUDED.foreign_flow_score,
                   insider_score = EXCLUDED.insider_score,
                   conditional_mom_score = EXCLUDED.conditional_mom_score,
                   earnings_surprise_score = EXCLUDED.earnings_surprise_score,
                   distress_score = EXCLUDED.distress_score,
                   piotroski_score = EXCLUDED.piotroski_score,
                   factor_details = EXCLUDED.factor_details,
                   updated_at = NOW()""",
            rows,
            page_size=500,
        )
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM factor_scores WHERE score_date = %s", (score_date,))
        final_count = cur.fetchone()[0]

        logger.info("Factor scores done: %d rows for date %s", final_count, score_date)
        return {
            "rows": final_count,
            "symbols": len(rows),
            "score_date": str(score_date),
            "factor_count": len(rows[0]) if rows else 0,
        }
    finally:
        cur.close()
        conn.close()
