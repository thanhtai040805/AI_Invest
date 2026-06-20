"""Check actual data in PostgreSQL tables."""
import os
import psycopg2

conn = psycopg2.connect("dbname=aiinvest user=postgres password=123 host=localhost port=5432")
cur = conn.cursor()

print("--- Querying ohlcv ---")
cur.execute("SELECT COUNT(1) FROM ohlcv")
print("Total rows in ohlcv:", cur.fetchone()[0])

cur.execute("SELECT symbol, COUNT(1) FROM ohlcv GROUP BY symbol ORDER BY COUNT(1) DESC LIMIT 10")
print("Top 10 symbols in ohlcv:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} rows")

print("\n--- Querying market_data_daily ---")
cur.execute("SELECT COUNT(1) FROM market_data_daily")
print("Total rows in market_data_daily:", cur.fetchone()[0])

cur.execute("SELECT ticker, COUNT(1) FROM market_data_daily GROUP BY ticker ORDER BY COUNT(1) DESC LIMIT 10")
print("Top 10 tickers in market_data_daily:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} rows")

print("\n--- Check if VNINDEX exists in ohlcv or market_data_daily ---")
cur.execute("SELECT COUNT(1) FROM ohlcv WHERE symbol = 'VNINDEX'")
print("VNINDEX rows in ohlcv:", cur.fetchone()[0])

cur.execute("SELECT COUNT(1) FROM market_data_daily WHERE ticker = 'VNINDEX'")
print("VNINDEX rows in market_data_daily:", cur.fetchone()[0])

cur.execute("SELECT symbol, COUNT(1) FROM ohlcv WHERE symbol LIKE '%INDEX%' OR symbol LIKE '%VNI%' GROUP BY symbol")
print("INDEX-like symbols in ohlcv:", cur.fetchall())

cur.execute("SELECT ticker, COUNT(1) FROM market_data_daily WHERE ticker LIKE '%INDEX%' OR ticker LIKE '%VNI%' GROUP BY ticker")
print("INDEX-like tickers in market_data_daily:", cur.fetchall())

print("\n--- Check market_regime table ---")
try:
    cur.execute("SELECT COUNT(1) FROM market_regime")
    print("Total rows in market_regime:", cur.fetchone()[0])
    cur.execute("SELECT * FROM market_regime ORDER BY date DESC LIMIT 5")
    print("Latest rows in market_regime:")
    for row in cur.fetchall():
        print("  ", row)
except Exception as e:
    print("Failed to query market_regime:", e)

conn.close()
