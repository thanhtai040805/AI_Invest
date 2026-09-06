"""Standalone Agent 01 (Market Surveillance Agent) Production Runner Script

Cho phép chạy và kiểm thử độc lập Agent 01 với CSDL thực tế, kiểm tra toàn bộ 6 tầng radar:
1. HOSE Session Context
2. ATC Closing Auction Anomaly
3. CSAD Herding Behavior (Panic vs Rotation vs FOMO)
4. VN30 Index Distortion ("Xanh vỏ đỏ lòng")
5. Sticky HMM 3-State Regime & GJR-GARCH(1,1) VIX VN Analog
6. State Tables Persistence (market_regimes & market_anomalies)

Usage:
    python scripts/run_agent01_standalone.py
"""

import sys
import os
import asyncio
from datetime import datetime, date
import json
import pandas as pd
import numpy as np

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.registry import AgentRegistry
import app.domain.agents
from app.domain.rules.market.session_context_manager import SessionContextManager
from app.domain.rules.market.atc_anomaly_detector import ATCAnomalyDetector
from app.domain.rules.market.csad_calculator import CSADCalculator
from app.domain.rules.market.vn30_distortion import VN30DistortionMonitor


async def run_agent01_standalone():
    """Generates an independent Agent 01 Market Pulse & Surveillance Report."""
    now = datetime.now()
    today = now.date()

    print("=" * 75)
    print(f"[AGENT 01: MARKET SURVEILLANCE] -- STANDALONE PRODUCTION RUN")
    print(f"Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)

    session_mgr = SessionContextManager()
    atc_det = ATCAnomalyDetector()
    csad_calc = CSADCalculator()
    dist_mon = VN30DistortionMonitor()

    # 1. Session Context
    current_session = session_mgr.get_session(now)
    is_active = session_mgr.is_trading_active(current_session)
    pause_polling = session_mgr.should_pause_polling(current_session)
    thresholds = session_mgr.get_session_anomaly_thresholds(current_session)

    print("\n1. HOSE SESSION CONTEXT:")
    print(f"   * Current Session: {current_session.value}")
    print(f"   * Order Matching Active: {is_active}")
    print(f"   * Polling Paused (Lunch/Closed): {pause_polling}")
    print(f"   * Session Anomaly Drop Limit (30m): {thresholds.get('max_index_drop_30m', -0.03):.1%}")

    # 2. Dispatch Live MarketSurveillanceAgent with Autonomous Data Hydration
    print("\n2. EXECUTING LIVE AGENT-01 SURVEILLANCE PIPELINE...")
    res = await AgentRegistry.dispatch("market_surveillance", {"date": str(today)})
    
    if res["status"] != "SUCCESS":
        print(f"   [ERROR] Agent 01 execution failed: {res}")
        return

    data = res["result"]["data"]
    trace = res["result"]["trace"]

    print("\n3. RADAR HOSE & MARKET REGIME SYNTHESIS:")
    print(f"   * Effective Trading Date: {data.get('effective_date')}")
    print(f"   * Market Regime (Sticky HMM): {data.get('current_regime')}")
    print(f"   * Session Context: {data.get('session_context')}")
    print(f"   * System Alert Level: {data.get('alert_level')}")
    print(f"   * VIX VN Analog (GJR-GARCH): {data.get('vix_vn_analog')}")
    print(f"   * GARCH Recommended Cash Target: {data.get('garch_cash_target_pct')}%")
    print(f"   * Market Breadth (% > MA50): {data.get('breadth_above_ma50_pct')}%")
    print(f"   * Advance/Decline Ratio: {data.get('adv_decl_ratio')}")
    print(f"   * Floor Locked Stocks (Múa bên trăng): {data.get('floor_locked_count')}")
    print(f"   * Ceiling Stocks: {data.get('ceiling_count')}")
    print(f"   * CSAD Herding Status: {data.get('herding_status')} (CSAD: {data.get('csad_score')})")
    print(f"   * VN30 Index Distortion: {data.get('vn30_distortion')} ({data.get('vn30_distortion_type') or 'NORMAL'})")
    print(f"   * ATC Anomalies Count: {data.get('atc_anomalies_count')}")
    print(f"   * Halted Tickers Count: {len(data.get('halted_tickers', []))}")

    print("\n4. ANOMALIES DETECTED FOR PERSISTENCE:")
    anomalies = trace.get("anomalies_detected", [])
    if anomalies:
        for a in anomalies:
            print(f"   - [{a['severity']}] {a['type']}: {a['description']}")
    else:
        print("   (No critical market anomalies detected for this session)")

    print("\n5. FINAL AGENT 01 MARKET PULSE PAYLOAD (O(1) Memory & Downstream Delivery):")
    print(json.dumps(data, indent=2, default=str))
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_agent01_standalone())
