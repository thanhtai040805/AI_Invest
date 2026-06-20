"""Test backfilling 2 years of VNINDEX via VietFin."""
from vietfin import vf
import pandas as pd
from datetime import date, timedelta

end_date = date(2026, 6, 19)
start_date = end_date - timedelta(days=730)

try:
    print(f"Fetching from {start_date} to {end_date}...")
    r = vf.index.price.historical(
        symbol="vnindex",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        interval="1d",
        provider="dnse",
    )
    df = r.to_df()
    print("Successfully fetched!")
    print("Total rows:", len(df))
except Exception as e:
    print("Failed:", e)
