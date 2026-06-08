"""Deep Learning Alpha Tool — LSTM stock prediction.

Agent-facing interface around :mod:`app.services.deep_learning_alpha`.
"""

from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


class DeepLearningTool(BaseTool):
    """Train and predict with LSTM deep learning models for stocks.

    Uses sequence models (LSTM or sklearn MLP fallback) to predict
    5-day forward returns from technical features.

    Actions: 'train' to train a new model, 'predict' to generate signal.
    """

    name = "deep_learning"
    description = (
        "Train and predict with deep learning (LSTM) models for stock "
        "returns. Uses 30-day sequence of technical features to predict "
        "5-day forward return. Actions: 'train' (train new model), "
        "'predict' (generate prediction signal). "
        "Returns directional signal (BUY/SELL/HOLD) with confidence score."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["train", "predict"],
                "description": "'train' to train model, 'predict' to generate prediction.",
            },
            "symbol": {
                "type": "string",
                "description": "Ticker symbol",
            },
        },
        "required": ["action", "symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        from app.services.deep_learning_alpha import train_lstm, predict_lstm

        action = kwargs.get("action", "predict")
        symbol = kwargs.get("symbol", "")

        try:
            if action == "train":
                result = train_lstm(symbol, force_retrain=True)
            else:
                result = predict_lstm(symbol)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("deep_learning failed for %s", symbol)
            return json.dumps({"symbol": symbol, "error": str(e)}, ensure_ascii=False)
