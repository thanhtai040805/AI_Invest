"""Test calculating market metrics using VietFin for HMM classifier."""
from vietfin import vf
import pandas as pd
from datetime import date, timedelta

def get_market_metrics_vietfin(target_date: date):
    # Fetch 120 days of data to have enough for MA50
    start = target_date - timedelta(days=120)
    try:
        r = vf.index.price.historical(
            symbol="vnindex",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=target_date.strftime("%Y-%m-%d"),
            interval="1d",
            provider="dnse",
        )
        df = r.to_df()
        if df is None or df.empty or "close" not in df.columns:
            print("Empty dataframe from VietFin")
            return 0.0, 50.0, 0.0

        # Ensure index is datetime
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # Compute indicators
        df['ma50'] = df['close'].rolling(window=50).mean()
        df['vol_ma20'] = df['volume'].rolling(window=20).mean()

        # Get the row closest to target_date
        target_ts = pd.Timestamp(target_date)
        df_filtered = df[df.index <= target_ts]
        if df_filtered.empty:
            print("No data up to target date")
            return 0.0, 50.0, 0.0

        latest = df_filtered.iloc[-1]
        
        close = float(latest['close'])
        ma50 = float(latest['ma50'])
        vol = float(latest['volume'])
        vol_ma20 = float(latest['vol_ma20'])

        vni_vs_ma50 = (close / ma50 - 1) if ma50 and not pd.isna(ma50) else 0.0
        vol_trend = (vol / vol_ma20 - 1) if vol_ma20 and not pd.isna(vol_ma20) else 0.0

        # Breadth
        # Let's query PG database for breadth_ma50
        import psycopg2
        conn = psycopg2.connect("dbname=aiinvest user=postgres password=123 host=localhost port=5432")
        cur = conn.cursor()
        cur.execute("SELECT breadth_ma50 FROM market_regime WHERE date <= %s ORDER BY date DESC LIMIT 1", (target_date,))
        row = cur.fetchone()
        breadth = float(row[0]) if row else 50.0
        conn.close()

        return vni_vs_ma50, breadth, vol_trend
    except Exception as e:
        print("Error in get_market_metrics_vietfin:", e)
        return 0.0, 50.0, 0.0

# Test for date 2026-06-18
metrics = get_market_metrics_vietfin(date(2026, 6, 18))
print("Computed metrics for 2026-06-18:")
print(f"  vni_vs_ma50: {metrics[0]:.4f}")
print(f"  breadth:     {metrics[1]:.4f}")
print(f"  vol_trend:   {metrics[2]:.4f}")
