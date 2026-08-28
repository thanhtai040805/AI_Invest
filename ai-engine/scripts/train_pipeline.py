"""Production Training Pipeline
Executes Weekly T+2.5 Hybrid Stacking Alpha Retraining and Monthly HMM Retraining.
Designed to be run via cron on weekends/off-market hours.
"""

import os
import sys
import logging
import argparse

# Setup paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.domain.rules.market.hmm_classifier import hmm_classifier
from scripts.train_hybrid_stacking import train_and_export

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TrainPipeline")


def train_monthly_hmm():
    """Run monthly HMM Regime retraining."""
    logger.info("Starting Monthly HMM Training...")
    success = hmm_classifier.train_hmm_model(days_history=1500)
    if success:
        logger.info("Monthly HMM Training Completed Successfully.")
    else:
        logger.error("Monthly HMM Training Failed.")


def train_weekly_alpha():
    """Run weekly Hybrid Stacking Alpha Ranker retraining."""
    logger.info("Starting Weekly Hybrid Stacking Alpha Ranker Training...")
    try:
        train_and_export()
        logger.info("Weekly Alpha Training Completed Successfully.")
    except Exception as e:
        logger.error("Weekly Alpha Training Failed: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Run Production ML Training Pipeline")
    parser.add_argument("--task", type=str, choices=["all", "hmm", "alpha"], default="all",
                        help="Choose training task: hmm (monthly), alpha (weekly), all (both)")
    args = parser.parse_args()

    logger.info("==================================================")
    logger.info("Starting ML Training Pipeline for Task: %s", args.task.upper())
    logger.info("==================================================")

    if args.task in ["all", "hmm"]:
        train_monthly_hmm()

    if args.task in ["all", "alpha"]:
        train_weekly_alpha()

    logger.info("==================================================")
    logger.info("ML Training Pipeline Completed.")
    logger.info("==================================================")


if __name__ == "__main__":
    main()
