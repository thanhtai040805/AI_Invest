"""
ATC Anomaly Detector — HOSE Closing Auction
Detects volume spikes and price manipulation in the 14:30-14:45 ATC window.
Distinguishes legitimate volume spikes (ETF rebalance dates, derivatives expiry) from abnormal manipulation.
"""

import logging
from datetime import date, datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ATCAnomalyDetector:
    """
    Evaluates anomalies during the HOSE 14:30-14:45 ATC session.
    """

    @staticmethod
    def is_derivatives_expiry(target_date: date) -> bool:
        """
        Derivatives contracts expire on the 3rd Thursday of each month in Vietnam.
        """
        if target_date.weekday() != 3:  # 3 = Thursday
            return False
        
        # Count Thursdays in the month
        first_day = date(target_date.year, target_date.month, 1)
        # Find first Thursday
        first_thursday_day = 1 + (3 - first_day.weekday()) % 7
        third_thursday_day = first_thursday_day + 14
        
        return target_date.day == third_thursday_day

    def evaluate_atc_session(
        self,
        target_date: date,
        atc_volume: float,
        continuous_avg_volume: float,
        atc_price_change_pct: float,
        is_etf_rebalance: bool = False
    ) -> Dict[str, Any]:
        """
        Evaluates ATC auction data for anomalous volume spikes or price manipulation.

        Returns:
            Dict containing status (NORMAL, INFO, WARNING, CRITICAL), ratio, and rationale.
        """
        if continuous_avg_volume <= 0:
            return {
                "status": "NORMAL",
                "volume_ratio": 1.0,
                "reason": "INSUFFICIENT_CONTINUOUS_VOLUME"
            }

        volume_ratio = atc_volume / continuous_avg_volume
        is_expiry = self.is_derivatives_expiry(target_date)

        # Rule 1: ETF Rebalance Day (legitimate heavy volume)
        if is_etf_rebalance and volume_ratio > 2.5:
            return {
                "status": "INFO",
                "volume_ratio": round(volume_ratio, 2),
                "is_expiry": is_expiry,
                "is_etf_rebalance": True,
                "reason": "ETF_REBALANCE_SCHEDULED_HIGH_VOLUME"
            }

        # Rule 2: Derivatives Expiry Thursday (higher volatility expected)
        if is_expiry and volume_ratio > 2.0:
            status = "WARNING" if abs(atc_price_change_pct) > 0.03 else "INFO"
            return {
                "status": status,
                "volume_ratio": round(volume_ratio, 2),
                "is_expiry": True,
                "is_etf_rebalance": is_etf_rebalance,
                "reason": f"DERIVATIVES_EXPIRY_ATC_SPIKE (change: {atc_price_change_pct:.2%})"
            }

        # Rule 3: Extreme volume spike without known event (suspected ATC manipulation)
        if volume_ratio > 3.0 or (volume_ratio > 2.2 and abs(atc_price_change_pct) > 0.04):
            return {
                "status": "CRITICAL",
                "volume_ratio": round(volume_ratio, 2),
                "is_expiry": is_expiry,
                "is_etf_rebalance": is_etf_rebalance,
                "reason": f"SUSPECTED_ATC_MANIPULATION (ratio: {volume_ratio:.2f}x, price change: {atc_price_change_pct:.2%})"
            }

        # Rule 4: Moderate volume/price anomaly
        if volume_ratio > 2.0 or abs(atc_price_change_pct) > 0.03:
            return {
                "status": "WARNING",
                "volume_ratio": round(volume_ratio, 2),
                "is_expiry": is_expiry,
                "is_etf_rebalance": is_etf_rebalance,
                "reason": "ELEVATED_ATC_VOLATILITY"
            }

        return {
            "status": "NORMAL",
            "volume_ratio": round(volume_ratio, 2),
            "is_expiry": is_expiry,
            "is_etf_rebalance": is_etf_rebalance,
            "reason": "ATC_SESSION_NORMAL"
        }


atc_anomaly_detector = ATCAnomalyDetector()
