"""Test fetching VNINDEX via VietFin."""
from vietfin import vf
import pandas as pd
from datetime import date, timedelta

end_date = date.today()
start_date = end_date - timedelta(days=120)

try:
    print("Calling VietFin historical for vnindex...")
    r = vf.index.price.historical(
        symbol="vnindex",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        interval="1d",
        provider="dnse",
    )
    df = r.to_df()
    print("Successfully fetched!")
    print("Columns:", df.columns)
    print("Head:")
    print(df.head())
    print("Tail:")
    print(df.tail())
except Exception as e:
    print("Failed:", e)
