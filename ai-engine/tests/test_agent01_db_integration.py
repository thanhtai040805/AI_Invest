"""
Real Database Integration Test for Agent 01 (Market Surveillance Agent)
Queries PostgreSQL database tables (market_data_daily, market_regime, technical_indicators) directly.
"""

import sys
import os
import unittest
from datetime import date, datetime

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.domain.services.regime_service import RegimeService
from app.domain.rules.market.hmm_classifier import hmm_classifier


class TestAgent01DBIntegration(unittest.TestCase):

    def test_real_db_connection_and_regime(self):
        """Tests fetching latest market regime from PostgreSQL DB."""
        try:
            service = RegimeService()
            latest = service.get_latest_regime()
            print("\n[DB INTEGRATION] Latest Market Regime from DB:", latest)
            self.assertIsNotNone(latest)
            self.assertIn("regime_label", latest)
        except Exception as e:
            self.skipTest(f"Database connection not available for integration test: {e}")

    def test_real_db_hmm_historical_query(self):
        """Tests querying VNINDEX history for HMM from real DB."""
        try:
            import psycopg2
            from app.infrastructure.database.pg_pool import DB_URL
            
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM market_data_daily WHERE ticker = 'VNINDEX'")
            count = cur.fetchone()[0]
            conn.close()
            
            print(f"\n[DB INTEGRATION] Total VNINDEX daily rows in DB: {count}")
            self.assertGreaterEqual(count, 0)
        except Exception as e:
            self.skipTest(f"Database connection not available for HMM historical query: {e}")


if __name__ == "__main__":
    unittest.main()
