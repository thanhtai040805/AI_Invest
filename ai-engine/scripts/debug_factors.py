#!/usr/bin/env python3
"""Diagnostic: why are certain VN factors "dead" in IC benchmark?

Checks:
  1. Data availability per table (rows, symbols, date range)
  2. NaN rates for each factor's raw inputs
  3. Sample values of raw inputs vs benchmark computation
  4. Cross-checks bench logic vs actual factor_scores.py
  5. Statistical distribution of factor values vs ranks
"""
import sys
from datetime import date, timedelta
from collections import defaultdict, Counter

import psycopg2
import psycopg2.extras
import pandas as pd
import numpy as np

sys.path.insert(0, ".")
from app.services.pg_pool import DB_URL

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# ── 1. Table health ─────────────────────────────────────────────────
print("=" * 70)
print("TABLE HEALTH CHECK")
print("=" * 70)

tables = [
    ("ohlcv",          "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(time::date), MAX(time::date) FROM ohlcv"),
    ("financial_ratios","SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(ratio_date), MAX(ratio_date) FROM financial_ratios"),
    ("foreign_flow",   "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(trade_date), MAX(trade_date) FROM foreign_flow"),
    ("insider_trades", "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(trade_date), MAX(trade_date) FROM insider_trades"),
    ("stocks",         "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM stocks"),
    ("financial_statements","SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(period_end), MAX(period_end) FROM financial_statements"),
    ("macro_indicators","SELECT COUNT(*), COUNT(DISTINCT indicator_name), MIN(indicator_date), MAX(indicator_date) FROM macro_indicators"),
]
for name, sql in tables:
    try:
        cur.execute(sql)
        row = cur.fetchone()
        print(f"  {name:>25s}: {row[0]:>8,d} rows  {row[1]:>4,d} symbols  [{str(row[2])} .. {str(row[3])}]")
    except Exception as e:
        print(f"  {name:>25s}: ERROR — {e}")

# ── 2. Stock metadata quality ──────────────────────────────────────
print("\n" + "=" * 70)
print("STOCK METADATA QUALITY")
print("=" * 70)
cur.execute("SELECT COUNT(*) FROM stocks WHERE market_cap IS NOT NULL")
print(f"  market_cap populated: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM stocks WHERE ceiling IS NOT NULL")
print(f"  ceiling populated: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM stocks WHERE floor IS NOT NULL")
print(f"  floor populated: {cur.fetchone()[0]}")
cur.execute("SELECT exchange, COUNT(*) FROM stocks GROUP BY exchange ORDER BY COUNT(*) DESC")
print("  exchange distribution:")
for e, c in cur.fetchall():
    print(f"    {e or 'NULL':>15s}: {c}")

# ── 3. Financial ratios column quality ─────────────────────────────
print("\n" + "=" * 70)
print("FINANCIAL RATIOS — COLUMN FILL RATES")
print("=" * 70)
cur.execute("SELECT COUNT(*) FROM financial_ratios")
total = cur.fetchone()[0]
for col in ["pe", "pb", "roe", "roa", "debt_equity", "gross_margin", "net_margin",
            "fcf_yield", "ev_ebitda", "yoy_revenue_growth", "yoy_earnings_growth"]:
    cur.execute(f"SELECT COUNT({col}) FROM financial_ratios WHERE {col} IS NOT NULL")
    filled = cur.fetchone()[0]
    pct = filled / total * 100 if total else 0
    print(f"  {col:>25s}: {filled:>8,d}/{total:,d} ({pct:.1f}%)")

# Distribution of PE values
cur.execute("SELECT symbol, ratio_date, pe FROM financial_ratios WHERE pe IS NOT NULL LIMIT 20")
pe_samples = cur.fetchall()
print("\n  Sample PE values (first 20):")
for sym, dt, pe in pe_samples:
    print(f"    {sym:>6s}  {dt}  PE={pe}")

# ── 4. Foreign flow column quality ─────────────────────────────────
print("\n" + "=" * 70)
print("FOREIGN FLOW — COLUMN FILL RATES")
print("=" * 70)
for col in ["net_value", "room_remaining", "room_limit", "ownership_pct"]:
    cur.execute(f"SELECT COUNT({col}) FROM foreign_flow WHERE {col} IS NOT NULL")
    filled = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM foreign_flow")
    total = cur.fetchone()[0]
    pct = filled / total * 100 if total else 0
    print(f"  {col:>25s}: {filled:>8,d}/{total:,d} ({pct:.1f}%)")

# ── 5. Cross-sectional sample for ONE date ─────────────────────────
print("\n" + "=" * 70)
print("CROSS-SECTIONAL SAMPLE FOR 2025-01-15")
print("=" * 70)
dt = date(2025, 1, 15)

# OHLCV sample
cur.execute("""
    SELECT symbol, adj_close, close, volume, open, high, low
    FROM ohlcv WHERE time::date = %s AND symbol IN (
        SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY RANDOM() LIMIT 10
    )
""", (dt,))
print(f"\n  OHLCV at {dt} (10 random HOSE symbols):")
for sym, ac, cl, vol, op, hi, lo in cur.fetchall():
    print(f"    {sym:>6s}  adj_close={ac:>10.2f}  close={cl:>10.2f}  vol={vol:>10,d}")
    if None in (ac, cl):
        print("      ⚠️  adj_close or close is NULL!")

# Financial ratios sample around this date
cur.execute("""
    SELECT DISTINCT ON (symbol) symbol, pe, pb, roe, gross_margin, net_margin, fcf_yield, ev_ebitda
    FROM financial_ratios
    WHERE ratio_date <= %s AND symbol = ANY(ARRAY(SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') LIMIT 20))
    ORDER BY symbol, ratio_date DESC
""", (dt,))
samples = cur.fetchall()
print(f"\n  Financial ratios at (≤{dt}) for 20 symbols:")
pe_vals = [r[1] for r in samples if r[1] is not None]
print(f"    Symbols with PE: {len(pe_vals)}/20")
print(f"    PE  min={min(pe_vals) if pe_vals else 'N/A':>6s}  max={max(pe_vals) if pe_vals else 'N/A':>6s}")
for sym, pe, pb, roe, gm, nm, fcf, eveb in samples:
    missing = []
    if pe is None: missing.append("pe")
    if pb is None: missing.append("pb")
    if roe is None: missing.append("roe")
    if gm is None: missing.append("gm")
    if nm is None: missing.append("nm")
    if fcf is None: missing.append("fcf")
    label = f"  MISSING: {', '.join(missing)}" if missing else "  OK"
    print(f"    {sym:>6s}  pe={pe:>8.2f}  pb={pb:>8.2f}  roe={str(roe):>8s}  gm={str(gm):>8s}  nm={str(nm):>8s} {label}")

# Foreign flow sample
cur.execute("""
    SELECT DISTINCT ON (symbol) symbol, net_value, room_remaining, room_limit
    FROM foreign_flow
    WHERE trade_date <= %s AND symbol = ANY(ARRAY(SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') LIMIT 20))
    ORDER BY symbol, trade_date DESC
""", (dt,))
samples = cur.fetchall()
print(f"\n  Foreign flow at (≤{dt}) for 20 symbols:")
net_vals = [r[1] for r in samples if r[1] is not None]
room_vals = [r[2] for r in samples if r[2] is not None]
print(f"    Symbols with net_value: {len(net_vals)}/20")
print(f"    Symbols with room_remaining: {len(room_vals)}/20")
for sym, nv, rr, rl in samples[:10]:
    print(f"    {sym:>6s}  net_val={str(nv):>12s}  room_rem={str(rr):>8s}  room_lim={str(rl):>8s}")

# ── 6. VNINDEX macro data ─────────────────────────────────────────
print("\n" + "=" * 70)
print("MACRO INDICATORS (VNINDEX)")
print("=" * 70)
cur.execute("SELECT indicator_name, COUNT(*), MIN(indicator_date), MAX(indicator_date) FROM macro_indicators GROUP BY indicator_name")
for row in cur.fetchall():
    print(f"  {row[0]:>30s}: {row[1]:>6,d} rows  [{str(row[2])} .. {str(row[3])}]")

# ── 7. Insider trades quality ──────────────────────────────────────
print("\n" + "=" * 70)
print("INSIDER TRADES SAMPLE")
print("=" * 70)
cur.execute("SELECT trade_type, COUNT(*) FROM insider_trades GROUP BY trade_type ORDER BY COUNT(*) DESC LIMIT 10")
print("  Trade types:")
for tt, cnt in cur.fetchall():
    print(f"    {tt:>25s}: {cnt:>8,d}")
cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT symbol) FROM insider_trades")
row = cur.fetchone()
print(f"  Date range: [{row[0]} .. {row[1]}]  symbols: {row[2]}")

# ── 8. Size of the daily factor_scores table ───────────────────────
print("\n" + "=" * 70)
print("FACTOR SCORES TABLE")
print("=" * 70)
cur.execute("SELECT COUNT(*), MIN(score_date), MAX(score_date) FROM factor_scores")
row = cur.fetchone()
print(f"  Rows: {row[0]:>8,d}  Date range: [{row[1]} .. {row[2]}]")

# ── 9. Close price quality check (compare adj_close vs close) ──────
print("\n" + "=" * 70)
print("CLOSE vs ADJ_CLOSE COMPARISON (sample 5 stocks, 2025-01-02 onwards)")
print("=" * 70)
cur.execute("""
    SELECT symbol, time::date, adj_close, close
    FROM ohlcv
    WHERE time::date >= '2025-01-02' AND symbol IN (
        SELECT DISTINCT symbol FROM ohlcv ORDER BY RANDOM() LIMIT 5
    )
    ORDER BY symbol, time
    LIMIT 100
""")
diffs = []
for sym, td, ac, cl in cur.fetchall():
    if ac and cl:
        diff_pct = abs(ac - cl) / cl * 100
        diffs.append(diff_pct)
        if diff_pct > 1:
            print(f"    ⚠️  {sym} {td}: adj_close={ac:.2f} close={cl:.2f} diff={diff_pct:.2f}%")
if diffs:
    print(f"  Max diff: {max(diffs):.2f}%  Mean diff: {np.mean(diffs):.2f}%  (of {len(diffs)} rows)")

cur.close()
conn.close()
print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)
