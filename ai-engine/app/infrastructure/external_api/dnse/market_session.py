"""
Vietnam Stock Market Session Manager.

Handles trading hours awareness for HOSE and HNX exchanges.
Auto-connects/disconnects DNSE WebSocket based on market schedule.
Accounts for lunch break, weekends, and configurable holidays.

Trading hours (ICT, UTC+7):
  Morning session: 09:00 - 11:30
  Afternoon session: 13:00 - 14:45
"""

import time
from datetime import datetime, timedelta, time as dt_time
from enum import Enum
from typing import Optional


class MarketState(Enum):
    PRE_OPEN = "pre_open"
    OPENING_AUCTION = "opening_auction"
    CONTINUOUS_MORNING = "continuous_morning"
    LUNCH_BREAK = "lunch_break"
    CONTINUOUS_AFTERNOON = "continuous_afternoon"
    CLOSING_AUCTION = "closing_auction"
    CLOSED = "closed"


class MarketSessionManager:
    HOSE_SCHEDULE = {
        "pre_open_start": dt_time(8, 30),
        "opening_auction_start": dt_time(9, 0),
        "continuous_morning_start": dt_time(9, 15),
        "lunch_break_start": dt_time(11, 30),
        "continuous_afternoon_start": dt_time(13, 0),
        "closing_auction_start": dt_time(14, 30),
        "market_close": dt_time(14, 45),
    }

    HNX_SCHEDULE = {
        "pre_open_start": dt_time(8, 30),
        "opening_auction_start": dt_time(9, 0),
        "continuous_morning_start": dt_time(9, 15),
        "lunch_break_start": dt_time(11, 30),
        "continuous_afternoon_start": dt_time(13, 0),
        "closing_auction_start": dt_time(14, 15),
        "market_close": dt_time(14, 30),
    }

    def __init__(self, holidays: Optional[set] = None) -> None:
        self._holidays: set = holidays or set()
        self._last_state: Optional[MarketState] = None
        self._state_changed_at: Optional[float] = None

    def is_trading_day(self, dt: Optional[datetime] = None) -> bool:
        now = dt or datetime.now()
        if now.weekday() >= 5:
            return False
        date_str = now.strftime("%Y-%m-%d")
        if date_str in self._holidays:
            return False
        return True

    def get_market_state(self, dt: Optional[datetime] = None) -> MarketState:
        now = dt or datetime.now()
        if not self.is_trading_day(now):
            return MarketState.CLOSED

        t = now.time()
        sched = self.HOSE_SCHEDULE

        if t < sched["pre_open_start"]:
            return MarketState.CLOSED
        elif t < sched["opening_auction_start"]:
            return MarketState.PRE_OPEN
        elif t < sched["continuous_morning_start"]:
            return MarketState.OPENING_AUCTION
        elif t < sched["lunch_break_start"]:
            return MarketState.CONTINUOUS_MORNING
        elif t < sched["continuous_afternoon_start"]:
            return MarketState.LUNCH_BREAK
        elif t < sched["closing_auction_start"]:
            return MarketState.CONTINUOUS_AFTERNOON
        elif t < sched["market_close"]:
            return MarketState.CLOSING_AUCTION
        else:
            return MarketState.CLOSED

    def is_market_open(self, dt: Optional[datetime] = None) -> bool:
        state = self.get_market_state(dt)
        return state in (
            MarketState.OPENING_AUCTION,
            MarketState.CONTINUOUS_MORNING,
            MarketState.CONTINUOUS_AFTERNOON,
            MarketState.CLOSING_AUCTION,
        )

    def is_connected(self, dt: Optional[datetime] = None) -> bool:
        state = self.get_market_state(dt)
        return state in (
            MarketState.PRE_OPEN,
            MarketState.OPENING_AUCTION,
            MarketState.CONTINUOUS_MORNING,
            MarketState.LUNCH_BREAK,
            MarketState.CONTINUOUS_AFTERNOON,
            MarketState.CLOSING_AUCTION,
        )

    def next_state_change(self, dt: Optional[datetime] = None) -> tuple[MarketState, float]:
        now = dt or datetime.now()
        current = self.get_market_state(now)
        sched = self.HOSE_SCHEDULE

        transitions = [
            (MarketState.PRE_OPEN, sched["pre_open_start"]),
            (MarketState.OPENING_AUCTION, sched["opening_auction_start"]),
            (MarketState.CONTINUOUS_MORNING, sched["continuous_morning_start"]),
            (MarketState.LUNCH_BREAK, sched["lunch_break_start"]),
            (MarketState.CONTINUOUS_AFTERNOON, sched["continuous_afternoon_start"]),
            (MarketState.CLOSING_AUCTION, sched["closing_auction_start"]),
            (MarketState.CLOSED, sched["market_close"]),
        ]

        for state, t in transitions:
            if t > now.time():
                dt_change = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                return state, (dt_change - now).total_seconds()

        next_day = now + timedelta(days=1)
        while not self.is_trading_day(next_day):
            next_day += timedelta(days=1)
        first_t = transitions[0][1]
        dt_change = next_day.replace(hour=first_t.hour, minute=first_t.minute, second=0, microsecond=0)
        return transitions[0][0], (dt_change - now).total_seconds()

    def track_state(self, callback) -> None:
        while True:
            state = self.get_market_state()
            if state != self._last_state:
                self._last_state = state
                self._state_changed_at = time.time()
                callback(state)
            next_state, wait_secs = self.next_state_change()
            wait_secs = max(1, min(wait_secs, 60))
            time.sleep(wait_secs)
