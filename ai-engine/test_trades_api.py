"""
Test DNSE market.trades API with historical data.
Tests fetching trades from long ago and aggregating into OHLCV.
"""
import sys
sys.path.insert(0, "ai-engine")

from datetime import datetime, timezone
from dnse import DnseClient
from dnse.models.market import BoardId
from app.config.settings import get_settings

settings = get_settings()
client = DnseClient(
    api_key=settings.dnse_api_key,
    api_secret=settings.dnse_api_secret,
    base_url=settings.dnse_base_url,
)

# Test 1: Basic connectivity - fetch today's trades for HPG
print("=== Test 1: Today's trades (limited to 10) ===")
try:
    trades = client.market.trades(
        symbol="HPG",
        board_id=BoardId.ROUND_LOT,
        from_ts="2026-05-22T00:00:00Z",
        to_ts="2026-05-22T23:59:59Z",
        limit=10,
    )
    print(f"  Got {len(trades)} trades")
    if trades:
        t = trades[0]
        print(f"  First trade: time={t.time} price={t.price} volume={t.volume}")
    else:
        print("  No trades returned")
except Exception as e:
    print(f"  ERROR: {e}")

# Test 2: Historical data - HPG trades from 2020
print("\n=== Test 2: Historical trades 2020 (limited to 100) ===")
try:
    trades = client.market.trades(
        symbol="HPG",
        board_id=BoardId.ROUND_LOT,
        from_ts="2020-01-01T00:00:00Z",
        to_ts="2020-12-31T23:59:59Z",
        limit=100,
    )
    print(f"  Got {len(trades)} trades")
    if trades:
        t = trades[0]
        print(f"  First trade: time={t.time} price={t.price} volume={t.volume}")
        t = trades[-1]
        print(f"  Last trade:  time={t.time} price={t.price} volume={t.volume}")
    else:
        print("  No trades returned")
except Exception as e:
    print(f"  ERROR: {e}")

# Test 3: Historical data different symbol + check max limit
print("\n=== Test 3: VIC trades 2022 (limit=500) ===")
try:
    trades = client.market.trades(
        symbol="VIC",
        board_id=BoardId.ROUND_LOT,
        from_ts="2022-01-01T00:00:00Z",
        to_ts="2022-06-30T23:59:59Z",
        limit=500,
    )
    print(f"  Got {len(trades)} trades")
    if trades:
        t = trades[0]
        print(f"  First trade: time={t.time} price={t.price} volume={t.volume}")
        t = trades[-1]
        print(f"  Last trade:  time={t.time} price={t.price} volume={t.volume}")
    else:
        print("  No trades returned")
except Exception as e:
    print(f"  ERROR: {e}")

# Test 4: Test different limit sizes
print("\n=== Test 4: VIC trades 2023-01 (limit=10, limit=100, limit=1000) ===")
for limit in [10, 100, 1000]:
    try:
        trades = client.market.trades(
            symbol="VIC",
            board_id=BoardId.ROUND_LOT,
            from_ts="2023-01-01T00:00:00Z",
            to_ts="2023-01-31T23:59:59Z",
            limit=limit,
        )
        print(f"  limit={limit}: got {len(trades)} trades")
    except Exception as e:
        print(f"  limit={limit}: ERROR: {e}")

# Test 5: ALL board (no filter)
print("\n=== Test 5: ALL board (BoardId.ALL) ===")
try:
    trades = client.market.trades(
        symbol="HPG",
        board_id=BoardId.ALL,
        from_ts="2026-05-22T00:00:00Z",
        to_ts="2026-05-22T23:59:59Z",
        limit=10,
    )
    print(f"  Got {len(trades)} trades")
    if trades:
        t = trades[0]
        print(f"  First trade: time={t.price} price={t.price} volume={t.volume}")
except Exception as e:
    print(f"  ERROR: {e}")

# Test 6: VIX derivative
print("\n=== Test 6: VIX trades ===")
try:
    trades = client.market.trades(
        symbol="VIX",
        board_id=BoardId.ROUND_LOT,
        from_ts="2026-05-22T00:00:00Z",
        to_ts="2026-05-22T23:59:59Z",
        limit=10,
    )
    print(f"  Got {len(trades)} trades")
    if trades:
        t = trades[0]
        print(f"  First trade: time={t.price} price={t.price} volume={t.volume}")
    else:
        print("  No trades returned")
except Exception as e:
    print(f"  ERROR: {e}")

# Test 7: BID trades
print("\n=== Test 7: BID trades ===")
try:
    trades = client.market.trades(
        symbol="BID",
        board_id=BoardId.ROUND_LOT,
        from_ts="2026-05-22T00:00:00Z",
        to_ts="2026-05-22T23:59:59Z",
        limit=10,
    )
    print(f"  Got {len(trades)} trades")
    if trades:
        t = trades[0]
        print(f"  First trade: time={t.price} price={t.price} volume={t.volume}")
    else:
        print("  No trades returned")
except Exception as e:
    print(f"  ERROR: {e}")

client.close()
print("\nDone!")
