"""
Standalone Agent 01 (Market Surveillance Agent) Runner Script
Allows testing and demonstrating Agent 01 independently without needing downstream agents.

Usage:
    python scripts/run_agent01_standalone.py
"""

import sys
import os
from datetime import datetime, date
import json
import pandas as pd
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.domain.rules.market.session_context_manager import session_context_manager
from app.domain.rules.market.atc_anomaly_detector import atc_anomaly_detector
from app.domain.rules.market.csad_calculator import csad_calculator
from app.domain.rules.market.vn30_distortion import vn30_distortion_monitor
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.rules.market.hmm_regime_engine import hmm_engine
from app.domain.services.regime_service import RegimeService


def run_agent01_standalone():
    """Generates an independent Agent 01 Market Pulse & Surveillance Report."""
    now = datetime.now()
    today = now.date()

    print("=" * 70)
    print(f"[AGENT 01: MARKET SURVEILLANCE] -- STANDALONE EXECUTION RUN")
    print(f"Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. Session Context
    current_session = session_context_manager.get_session(now)
    is_active = session_context_manager.is_trading_active(current_session)
    pause_polling = session_context_manager.should_pause_polling(current_session)
    thresholds = session_context_manager.get_session_anomaly_thresholds(current_session)

    print("\n1. HOSE SESSION CONTEXT:")
    print(f"   * Current Session: {current_session.value}")
    print(f"   * Order Matching Active: {is_active}")
    print(f"   * Polling Paused (Lunch/Closed): {pause_polling}")
    print(f"   * Session Anomaly Drop Limit (30m): {thresholds.get('max_index_drop_30m', -0.03):.1%}")

    # 2. ATC Anomaly Detector
    atc_result = atc_anomaly_detector.evaluate_atc_session(
        target_date=today,
        atc_volume=1200000,
        continuous_avg_volume=500000,
        atc_price_change_pct=-0.015,
        is_etf_rebalance=False
    )
    print("\n2. ATC CLOSING AUCTION EVALUATION:")
    print(f"   * ATC Status: {atc_result['status']}")
    print(f"   * ATC Volume Ratio: {atc_result['volume_ratio']}x vs Continuous Avg")
    print(f"   * Expiry Thursday: {atc_result['is_expiry']}")
    print(f"   * Reason: {atc_result['reason']}")

    # 3. CSAD Herding Behavior
    sample_df = pd.DataFrame(np.random.normal(0, 0.015, (60, 30)))
    sample_mkt = pd.Series(np.random.normal(-0.005, 0.01, 60))
    csad_res = csad_calculator.analyze_herding(sample_df, sample_mkt)

    print("\n3. CSAD HERDING BEHAVIOR ANALYSIS:")
    print(f"   * CSAD Metric: {csad_res['csad']}")
    print(f"   * Non-linear Beta 2: {csad_res['beta_2']}")
    print(f"   * Herding Status: {csad_res['herding_status']}")
    print(f"   * Alert Level: {csad_res['alert_level']}")

    # 4. VN30 Index Distortion
    sample_rets = {"VIC": 0.068, "VHM": 0.052, "VCB": 0.041, "TCB": -0.005, "MBB": -0.01}
    sample_weights = {"VIC": 0.12, "VHM": 0.10, "VCB": 0.10, "TCB": 0.06, "MBB": 0.05}
    distortion_res = vn30_distortion_monitor.analyze_distortion(sample_rets, sample_weights)

    print("\n4. VN30 INDEX DISTORTION MONITOR:")
    print(f"   * Is Index Distorted: {distortion_res['is_distorted']}")
    print(f"   * Top 3 Concentration Ratio: {distortion_res['concentration_ratio']:.1%}")
    print(f"   * Reason: {distortion_res['reason']}")

    # 5. Market Pulse & Regime Signal
    regime_service = RegimeService()
    latest_regime = regime_service.get_latest_regime()

    market_pulse_report = {
        "agent": "AGENT_01_MARKET_SURVEILLANCE",
        "timestamp": now.isoformat(),
        "market_session": current_session.value,
        "regime_label": latest_regime.get("regime_label", "BULL_MOMENTUM"),
        "breadth_ma50": latest_regime.get("breadth_ma50", 55.0),
        "atc_surveillance": atc_result,
        "csad_herding": csad_res,
        "vn30_distortion": distortion_res,
        "t2_5_settlement": {
            "day_of_week": now.strftime("%A"),
            "settlement_days_remaining": 2.5
        }
    }

    print("\n5. FINAL AGENT 01 MARKET PULSE REPORT (JSON Output):")
    print(json.dumps(market_pulse_report, indent=2))
    print("=" * 70)


if __name__ == "__main__":
    run_agent01_standalone()
