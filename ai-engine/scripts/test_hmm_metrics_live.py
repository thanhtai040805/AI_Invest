"""Test live get_market_metrics using current hmm_classifier."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATABASE_URL"] = "postgresql://postgres:123@localhost:5432/aiinvest"

from app.domain.rules.market.hmm_classifier import hmm_classifier
from datetime import date

target_date = date(2026, 6, 18)
metrics = hmm_classifier.get_market_metrics(target_date)
print("Metrics for 2026-06-18 from hmm_classifier:")
print(f"  vni_vs_ma50: {metrics[0]:.4f}")
print(f"  breadth:     {metrics[1]:.4f}")
print(f"  vol_trend:   {metrics[2]:.4f}")

posterior = hmm_classifier.calculate_posterior(*metrics)
print("Posterior probabilities:")
for k, v in posterior.items():
    print(f"  {k.value}: {v:.4f}")

state = hmm_classifier.classify(posterior)
print("Classified state:", state.value)
