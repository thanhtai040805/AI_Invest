"""ML Alpha Prediction Tool — trains and predicts using XGBoost/Random Forest
on alpha factor zoo features.

Agent-facing interface around :mod:`app.services.ml_alpha_predictor`.
"""

from __future__ import annotations

import json
import logging

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


class MLAlphaTool(BaseTool):
    """Train ML models and generate alpha predictions for stocks.

    Uses the alpha factor zoo (WorldQuant, GTJA, Qlib, academic) as features
    to train XGBoost or Random Forest models that predict 5-day forward returns.

    Features include: momentum, reversal, volume, volatility, quality, value,
    liquidity, microstructure, technical indicators (RSI, MACD, Bollinger, ATR).

    Use 'train' action to train a model, 'predict' to generate latest prediction.
    """

    name = "ml_alpha"
    description = (
        "Train ML models (XGBoost/Random Forest) on alpha factor zoo features "
        "to predict 5-day forward returns for a stock symbol. "
        "Actions: 'train' (train new model), 'predict' (generate prediction). "
        "Returns predicted directional signal (BUY/SELL/HOLD) with confidence score."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["train", "predict"],
                "description": "'train' to train a new model, 'predict' to generate prediction.",
            },
            "symbol": {
                "type": "string",
                "description": "Vietnamese ticker symbol (e.g., FPT, HPG, VNM).",
            },
            "model_type": {
                "type": "string",
                "enum": ["xgboost", "random_forest"],
                "description": "Model type. Default: xgboost.",
            },
        },
        "required": ["action", "symbol"],
    }
    repeatable = True

    def execute(self, **kwargs: str) -> str:
        from app.services.ml_alpha_predictor import train_model, predict_alpha

        action = kwargs.get("action", "predict")
        symbol = kwargs.get("symbol", "")
        model_type = kwargs.get("model_type", "xgboost")

        try:
            if action == "train":
                result = train_model(symbol, model_type, force_retrain=True)
            else:
                result = predict_alpha(symbol, model_type)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.exception("ml_alpha failed for %s", symbol)
            return json.dumps({"symbol": symbol, "error": str(e)}, ensure_ascii=False)
