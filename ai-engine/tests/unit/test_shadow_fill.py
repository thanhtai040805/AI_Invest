from datetime import datetime, timedelta, timezone

import pytest

from app.domain.rules.execution.shadow_fill import shadow_fill


def test_shadow_fill_requires_fresh_executable_depth():
    now = datetime.now(timezone.utc)
    book = {
        "marketState": "continuous_morning",
        "lastUpdate": (now - timedelta(seconds=1)).isoformat(),
        "bids": [{"price": 24.95, "volume": 300}],
        "asks": [{"price": 25.05, "volume": 100}, {"price": 25, "volume": 200}],
    }
    assert shadow_fill(book, "BUY", 300, 25100, now) == (200 * 25000 + 100 * 25050) / 300
    with pytest.raises(ValueError, match="Insufficient executable depth"):
        shadow_fill(book, "BUY", 300, 25000, now)
    with pytest.raises(ValueError, match="stale"):
        shadow_fill({**book, "lastUpdate": (now - timedelta(seconds=11)).isoformat()}, "SELL", 100, 24000, now)
    with pytest.raises(ValueError, match="no executable depth"):
        shadow_fill({**book, "bids": []}, "SELL", 100, 24000, now)
    with pytest.raises(ValueError, match="continuous trading"):
        shadow_fill({**book, "marketState": "closing_auction"}, "BUY", 100, 25100, now)
