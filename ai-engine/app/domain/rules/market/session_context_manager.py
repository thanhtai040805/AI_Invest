"""
HOSE Session Context Manager
Enforces awareness of 6 HOSE trading sessions (ATO, Continuous AM, Lunch, Continuous PM, ATC, Negotiated).
Dictates session-specific anomaly thresholds and lunch break polling pause.
"""

from datetime import datetime, time
from enum import Enum
from typing import Dict, Any, Tuple


class HOSEMarketSession(Enum):
    PRE_MARKET = "PRE_MARKET"            # 08:30 - 09:00
    ATO = "ATO"                          # 09:00 - 09:15 (Opening Auction)
    CONTINUOUS_MORNING = "CONTINUOUS_AM"  # 09:15 - 11:30 (Continuous Matching)
    LUNCH_BREAK = "LUNCH_BREAK"          # 11:30 - 13:00 (Trading Pause)
    CONTINUOUS_AFTERNOON = "CONTINUOUS_PM"# 13:00 - 14:30 (Continuous Matching)
    ATC = "ATC"                          # 14:30 - 14:45 (Closing Auction)
    NEGOTIATED = "NEGOTIATED"            # 14:45 - 15:00 (Block Trade)
    CLOSED = "CLOSED"                    # 15:00 - 08:30


class SessionContextManager:
    """
    Manages session context and thresholds tailored for HOSE trading schedule.
    """

    @staticmethod
    def get_session(current_time: datetime) -> HOSEMarketSession:
        """
        Determines the current HOSE market session based on time.
        """
        t = current_time.time() if isinstance(current_time, datetime) else current_time
        
        # Weekend check
        if isinstance(current_time, datetime) and current_time.weekday() in (5, 6):
            return HOSEMarketSession.CLOSED

        if time(8, 30) <= t < time(9, 0):
            return HOSEMarketSession.PRE_MARKET
        elif time(9, 0) <= t < time(9, 15):
            return HOSEMarketSession.ATO
        elif time(9, 15) <= t < time(11, 30):
            return HOSEMarketSession.CONTINUOUS_MORNING
        elif time(11, 30) <= t < time(13, 0):
            return HOSEMarketSession.LUNCH_BREAK
        elif time(13, 0) <= t < time(14, 30):
            return HOSEMarketSession.CONTINUOUS_AFTERNOON
        elif time(14, 30) <= t < time(14, 45):
            return HOSEMarketSession.ATC
        elif time(14, 45) <= t < time(15, 0):
            return HOSEMarketSession.NEGOTIATED
        else:
            return HOSEMarketSession.CLOSED

    @staticmethod
    def is_trading_active(session: HOSEMarketSession) -> bool:
        """Returns True if order matching is actively taking place."""
        return session in (
            HOSEMarketSession.ATO,
            HOSEMarketSession.CONTINUOUS_MORNING,
            HOSEMarketSession.CONTINUOUS_AFTERNOON,
            HOSEMarketSession.ATC
        )

    is_order_matching_active = is_trading_active

    @staticmethod
    def should_pause_polling(session: HOSEMarketSession) -> bool:
        """Returns True if surveillance polling should pause (e.g. during Lunch Break or Closed)."""
        return session in (HOSEMarketSession.LUNCH_BREAK, HOSEMarketSession.CLOSED)

    @staticmethod
    def get_session_anomaly_thresholds(session: HOSEMarketSession) -> Dict[str, Any]:
        """
        Returns session-adjusted anomaly thresholds.
        During ATO/ATC, price gaps are normal; continuous volume thresholds do not apply directly.
        """
        if session == HOSEMarketSession.ATO:
            return {
                "max_index_drop_30m": -0.05,  # Higher tolerance for opening gap
                "min_market_breadth": 0.05,
                "allow_opening_gap": True,
                "volume_spike_threshold": 4.0
            }
        elif session == HOSEMarketSession.ATC:
            return {
                "max_index_drop_30m": -0.03,
                "min_market_breadth": 0.10,
                "atc_volume_ratio_critical": 3.0,
                "allow_opening_gap": False
            }
        elif session in (HOSEMarketSession.CONTINUOUS_MORNING, HOSEMarketSession.CONTINUOUS_AFTERNOON):
            return {
                "max_index_drop_30m": -0.03,
                "min_market_breadth": 0.10,
                "volume_spike_threshold": 2.5,
                "allow_opening_gap": False
            }
        else:
            return {
                "max_index_drop_30m": -0.05,
                "min_market_breadth": 0.05,
                "allow_opening_gap": True
            }


session_context_manager = SessionContextManager()
