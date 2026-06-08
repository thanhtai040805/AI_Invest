#!/usr/bin/env python3
"""Check if docker is providing real data now vs old fallback data."""
import sys; sys.path.insert(0, ".")
import psycopg2
from datetime import date
from app.services.pg_pool import DB_URL

conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# 1. Compare row counts vs before
print("=== FINANCIAL RATIOS (ROE NOW?) ===")
cur.execute("SELECT COUNT(*), COUNT(roe), COUNT(pe), COUNT(gross_margin), COUNT(net_margin) FROM financial_ratios")
r = cur.fetchone()
print(f"  Total rows: {r[0]}")
print(f"  ROE non-null: {r[1]}")
print(f"  PE non-null: {r[2]}")
print(f"  GM non-null: {r[3]}")
print(f"  NM non-null: {r[4]}")

cur.execute("SELECT COUNT(DISTINCT symbol) FROM financial_ratios")
print(f"  Distinct symbols: {cur.fetchone()[0]}")

# Sample ROE values if any
cur.execute("SELECT symbol, ratio_date, roe FROM financial_ratios WHERE roe IS NOT NULL LIMIT 10")
rows = cur.fetchall()
print(f"  Sample ROE values ({len(rows)}):")
for r in rows:
    print(f"    {r[0]:>6s}  {r[1]}  roe={r[2]}")

print("\n=== FOREIGN FLOW ===")
cur.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(trade_date), MAX(trade_date) FROM foreign_flow")
r = cur.fetchone()
print(f"  {r[0]} rows, {r[1]} symbols, [{r[2]} .. {r[3]}]")

print("\n=== INSIDER TRADES ===")
cur.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(trade_date), MAX(trade_date) FROM insider_trades")
r = cur.fetchone()
print(f"  {r[0]} rows, {r[1]} symbols, [{r[2]} .. {r[3]}]")

print("\n=== STOCKS MARKET CAP ===")
cur.execute("SELECT COUNT(*) FROM stocks WHERE market_cap IS NOT NULL")
print(f"  Market cap populated: {cur.fetchone()[0]}")

print("\n=== FINANCIAL STATEMENTS ===")
cur.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(period_end), MAX(period_end) FROM financial_statements")
r = cur.fetchone()
print(f"  {r[0]} rows, {r[1]} symbols, [{r[2]} .. {r[3]}]")

conn.close()
