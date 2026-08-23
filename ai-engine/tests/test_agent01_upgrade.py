"""
Unit Test Suite for Agent 01 HOSE/KRX Upgrades
Located in dedicated tests/ directory.
Verifies SessionContextManager, ATCAnomalyDetector, Price Band ±7%, CSADCalculator,
VN30DistortionMonitor, RegimeService, and HMM 10-feature extraction.
"""

import sys
import os
import unittest
from datetime import datetime, date, time
import numpy as np
import pandas as pd

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.domain.rules.market.session_context_manager import (
    SessionContextManager, HOSEMarketSession
)
from app.domain.rules.market.atc_anomaly_detector import ATCAnomalyDetector
from app.domain.rules.market.csad_calculator import CSADCalculator
from app.domain.rules.market.vn30_distortion import VN30DistortionMonitor
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.rules.market.hmm_regime_engine import hmm_engine


class TestAgent01Upgrade(unittest.TestCase):

    def test_session_context_manager(self):
        """Test HOSE 6-session time boundaries."""
        # ATO 09:05
        t_ato = datetime(2026, 8, 24, 9, 5)  # Monday
        self.assertEqual(SessionContextManager.get_session(t_ato), HOSEMarketSession.ATO)
        
        # Morning Continuous 10:30
        t_am = datetime(2026, 8, 24, 10, 30)
        self.assertEqual(SessionContextManager.get_session(t_am), HOSEMarketSession.CONTINUOUS_MORNING)
        
        # Lunch Break 12:00
        t_lunch = datetime(2026, 8, 24, 12, 0)
        self.assertEqual(SessionContextManager.get_session(t_lunch), HOSEMarketSession.LUNCH_BREAK)
        self.assertTrue(SessionContextManager.should_pause_polling(HOSEMarketSession.LUNCH_BREAK))
        
        # Afternoon Continuous 13:45
        t_pm = datetime(2026, 8, 24, 13, 45)
        self.assertEqual(SessionContextManager.get_session(t_pm), HOSEMarketSession.CONTINUOUS_AFTERNOON)

        # ATC Auction 14:35
        t_atc = datetime(2026, 8, 24, 14, 35)
        self.assertEqual(SessionContextManager.get_session(t_atc), HOSEMarketSession.ATC)

        # Negotiated Block Trade 14:50
        t_neg = datetime(2026, 8, 24, 14, 50)
        self.assertEqual(SessionContextManager.get_session(t_neg), HOSEMarketSession.NEGOTIATED)

    def test_atc_anomaly_detector(self):
        """Test ATC closing auction volume anomaly rules."""
        detector = ATCAnomalyDetector()
        d_normal = date(2026, 8, 24)  # Monday

        # Normal volume
        res1 = detector.evaluate_atc_session(d_normal, atc_volume=1000, continuous_avg_volume=1000, atc_price_change_pct=0.005)
        self.assertEqual(res1["status"], "NORMAL")

        # Suspected manipulation (>3x continuous avg)
        res2 = detector.evaluate_atc_session(d_normal, atc_volume=3500, continuous_avg_volume=1000, atc_price_change_pct=-0.04)
        self.assertEqual(res2["status"], "CRITICAL")

        # ETF Rebalance (high volume expected)
        res3 = detector.evaluate_atc_session(d_normal, atc_volume=2800, continuous_avg_volume=1000, atc_price_change_pct=0.01, is_etf_rebalance=True)
        self.assertEqual(res3["status"], "INFO")

    def test_feature_forge_price_bands(self):
        """Test HOSE ±7% price limit streak calculations."""
        dates = pd.date_range("2026-01-01", periods=150, freq="B")
        prices = [40000.0] * 140
        # Add 3 consecutive ceiling hits
        prices.append(42800.0)  # +7.0%
        prices.append(45750.0)  # +6.9%
        prices.append(48950.0)  # +7.0%
        prices.extend([48950.0, 48950.0, 48950.0, 48950.0, 48950.0, 48950.0, 48950.0])

        df = pd.DataFrame({"close": prices, "high": prices, "low": prices, "volume": [100000] * 150}, index=dates)
        features = feature_forge.generate(df, ticker="TEST_TICKER")

        self.assertIn("ceiling_streak", features.columns)
        self.assertIn("floor_streak", features.columns)
        self.assertGreaterEqual(features["ceiling_streak"].max(), 1.0)

    def test_csad_calculator(self):
        """Test CSAD herding detection."""
        calculator = CSADCalculator()
        
        # Test calculation
        stock_rets = pd.DataFrame([[-0.03], [-0.031], [-0.029], [-0.0305]])
        csad = calculator.compute_csad(stock_rets, market_return=-0.03)
        self.assertLess(csad, 0.005)  # Very low dispersion = high herding

        # Test full analysis
        df_rets = pd.DataFrame(np.random.normal(0, 0.002, (60, 30)))
        mkt_rets = pd.Series([-0.03] * 60)
        res = calculator.analyze_herding(df_rets, mkt_rets)
        self.assertIn("herding_status", res)
        self.assertIn("alert_level", res)

    def test_vn30_distortion_monitor(self):
        """Test VN30 Index constituent concentration distortion."""
        monitor = VN30DistortionMonitor()
        
        # Top 3 stocks drive 80% of movement
        stock_returns = {"VIC": 0.06, "VHM": 0.05, "VCB": 0.04, "TCB": 0.001, "HPG": 0.001}
        stock_weights = {"VIC": 0.12, "VHM": 0.10, "VCB": 0.10, "TCB": 0.05, "HPG": 0.05}
        
        res = monitor.analyze_distortion(stock_returns, stock_weights)
        self.assertTrue(res["is_distorted"])
        self.assertGreater(res["concentration_ratio"], 0.70)

    def test_hmm_feature_extraction_count(self):
        """Test HMM feature extraction outputs 10 features."""
        dates = pd.date_range("2026-01-01", periods=50, freq="B")
        df = pd.DataFrame({
            "close": np.linspace(1000, 1200, 50),
            "ma50": np.linspace(950, 1150, 50),
            "ma200": np.linspace(900, 1100, 50),
            "breadth_ma50": np.full(50, 60.0),
            "volume": np.full(50, 1e7),
            "vol_ma20": np.full(50, 1e7),
            "net_prop_flow_bil": np.full(50, 20.0),
            "margin_debt_change_pct": np.full(50, 0.01),
            "usdvnd_change_pct": np.full(50, 0.001),
            "csad_score": np.full(50, 0.02),
            "sector_dispersion": np.full(50, 0.015)
        }, index=dates)

        X = hmm_engine._extract_features(df)
        self.assertEqual(X.shape[1], 10)  # Exactly 10 features


if __name__ == "__main__":
    unittest.main()
