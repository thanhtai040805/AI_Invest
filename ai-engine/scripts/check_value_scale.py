"""Print sample values from market_data_daily."""
import psycopg2

conn = psycopg2.connect("dbname=aiinvest user=postgres password=123 host=localhost port=5432")
cur = conn.cursor()

cur.execute("""
    SELECT ticker, date, close_adj, volume_total, close_adj * volume_total as value
    FROM market_data_daily
    WHERE ticker = 'VCB'
    ORDER BY date DESC LIMIT 5
""")
print("VCB sample:")
for row in cur.fetchall():
    print(f"  {row[0]} on {row[1]}: close={row[2]:,.0f}, vol={row[3]:,}, value={row[4]:,.0f}")

cur.close()
conn.close()
