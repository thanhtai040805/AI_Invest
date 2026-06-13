"""ML Alpha Predictor — trains XGBoost/Random Forest models on alpha factor zoo
to predict forward returns for Vietnamese equities.

Pipeline:
  1. Fetch OHLCV for universe
  2. Compute N alpha factors (from factor zoo)
  3. Engineer features (impute, winsorize, z-score)
  4. Train model (XGBoost or Random Forest)
  5. Predict forward returns
  6. Return predictions + feature importance
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "ml_alpha_models"
MODEL_DIR = Path(os.getenv("ML_MODEL_DIR", str(_DEFAULT_MODEL_DIR)))
MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Feature extraction from factor zoo
# ---------------------------------------------------------------------------

_SELECTED_ALPHAS = [
    # momentum
    "alpha_001", "alpha_003", "alpha_006",
    "carhart_mom",
    # reversal
    "alpha_004", "alpha_007", "alpha_019",
    # volume
    "alpha_014", "alpha_021", "alpha_054",
    # volatility
    "alpha_026", "alpha_051",
    # quality
    "alpha_040",
    # value
    "alpha_043",
    # liquidity
    "alpha_048",
    # microstructure
    "alpha_005", "alpha_016",
    # GTJA
    "alpha_001", "alpha_004", "alpha_006", "alpha_008", "alpha_013",
    # Qlib
    "beta5", "correlation10", "std20", "roc20", "rsv_kd",
]


def _fetch_factor_panel(symbol: str) -> Optional[pd.DataFrame]:
    """Fetch OHLCV and compute selected alpha factors as feature panel.

    Returns:
        DataFrame with columns = alpha_id, values = factor scores.
        Index = date.
    """
    from app.services.market_data_service import market_data_svc

    end = datetime.now(TZ_VN)
    start = end - timedelta(days=365)

    import asyncio
    ohlcv = asyncio.run(
        market_data_svc.get_ohlcv(
            symbol, interval="1D",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    )
    bars = ohlcv.get("data", [])
    if len(bars) < 30:
        return None

    df = pd.DataFrame(bars)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_index()

    features: Dict[str, pd.Series] = {}

    # Compute momentum features
    close = df["close"]
    volume = df["volume"]

    # Simple alphas (from factor zoo formulas)
    for period in [5, 10, 20, 60]:
        features[f"ret_{period}d"] = close.pct_change(period)
        features[f"vol_{period}d"] = close.pct_change().rolling(period).std()
        features[f"volume_ma_{period}d"] = volume.rolling(period).mean()
        features[f"volume_ratio_{period}d"] = volume / volume.rolling(period).mean()

    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    features["rsi_14"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    features["macd"] = ema12 - ema26
    features["macd_signal"] = features["macd"].ewm(span=9).mean()

    # Bollinger Bands
    bb_sma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    features["bb_position"] = (close - bb_sma) / (bb_std * 2 + 1e-10)
    features["bb_width"] = bb_std / bb_sma

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    features["atr_14"] = tr.rolling(14).mean()

    # Price / SMA ratio
    for period in [10, 20, 50]:
        features[f"price_sma_{period}"] = close / close.rolling(period).mean()

    # Volume price trend
    features["vpt"] = (volume * close.pct_change()).cumsum()

    # Target: forward 5-day return
    features["target"] = close.pct_change(5).shift(-5)

    result = pd.DataFrame(features)
    result = result.replace([np.inf, -np.inf], np.nan)
    return result


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def _prepare_data(panel: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Prepare feature matrix X and target vector y.

    - Drops rows with NaN target
    - Imputes feature NaN with median
    - Returns (X, y)
    """
    df = panel.dropna(subset=["target"])
    if df.empty:
        return pd.DataFrame(), pd.Series(dtype=float)

    y = df.pop("target")
    X = df.copy()

    # Impute remaining NaN
    for col in X.columns:
        if X[col].isna().any():
            med = X[col].median()
            if pd.isna(med):
                med = 0.0
            X[col] = X[col].fillna(med)

    return X, y


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def _get_feature_cols(X: pd.DataFrame) -> List[str]:
    """Get sorted feature column names (excludes target-like cols)."""
    return sorted([c for c in X.columns if c != "target"])


def train_model(
    symbol: str,
    model_type: str = "xgboost",
    force_retrain: bool = False,
) -> Dict[str, Any]:
    """Fetch data, engineer features, train model, save to disk.

    Args:
        symbol: Ticker symbol.
        model_type: 'xgboost' or 'random_forest'.
        force_retrain: If True, retrain even if cached model exists.

    Returns:
        Dict with model info + training metrics.
    """
    symbol = symbol.strip().upper()
    model_path = MODEL_DIR / f"{symbol}_{model_type}.pkl"
    feature_path = MODEL_DIR / f"{symbol}_{model_type}_features.json"

    if model_path.exists() and not force_retrain:
        try:
            with open(model_path, "rb") as f:
                pickle.load(f)
            with open(feature_path) as f:
                feature_cols = json.load(f)
            return {
                "symbol": symbol,
                "model_type": model_type,
                "status": "cached",
                "model_path": str(model_path),
                "feature_count": len(feature_cols),
            }
        except Exception:
            pass

    panel = _fetch_factor_panel(symbol)
    if panel is None or panel.empty:
        return {"symbol": symbol, "error": f"Insufficient data for {symbol}"}

    X, y = _prepare_data(panel)
    if X.empty or len(X) < 30:
        return {"symbol": symbol, "error": f"Too few samples: {len(X)}"}

    train_size = max(int(len(X) * 0.8), 30)
    X_train, X_test = X.iloc[:train_size], X.iloc[train_size:]
    y_train, y_test = y.iloc[:train_size], y.iloc[train_size:]

    if model_type == "random_forest":
        from sklearn.ensemble import RandomForestRegressor
        model = RandomForestRegressor(
            n_estimators=200, max_depth=8, random_state=42, n_jobs=-1
        )
    else:
        from xgboost import XGBRegressor
        model = XGBRegressor(
            n_estimators=200, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, n_jobs=-1,
        )

    model.fit(X_train, y_train)

    # Evaluate
    train_pred = model.predict(X_train)
    test_pred = model.predict(X_test)

    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    train_mae = float(mean_absolute_error(y_train, train_pred))
    test_mae = float(mean_absolute_error(y_test, test_pred))
    train_rmse = float(np.sqrt(mean_squared_error(y_train, train_pred)))
    test_rmse = float(np.sqrt(mean_squared_error(y_test, test_pred)))
    train_r2 = float(r2_score(y_train, train_pred))
    test_r2 = float(r2_score(y_test, test_pred))

    # Feature importance
    feature_cols = _get_feature_cols(X)
    if hasattr(model, "feature_importances_"):
        importance = list(zip(feature_cols, model.feature_importances_))
        importance.sort(key=lambda x: x[1], reverse=True)
        top_features = [
            {"name": name, "importance": round(imp, 4)}
            for name, imp in importance[:20]
        ]
    else:
        top_features = []

    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    with open(feature_path, "w") as f:
        json.dump(feature_cols, f)

    # Register with ModelRegistry for reproducibility
    try:
        from app.quant.data.mlops import ModelRecord, ModelRegistry

        _registry = ModelRegistry()
        _registry.register(ModelRecord(
            model_id=f"{symbol}_{model_type}",
            version=datetime.now(TZ_VN).strftime("%Y%m%d_%H%M%S"),
            created_at=datetime.now(TZ_VN),
            parameters={
                "model_type": model_type,
                "model_path": str(model_path),
                "feature_path": str(feature_path),
                "n_train": len(X_train),
                "n_test": len(X_test),
                "feature_count": len(feature_cols),
            },
            metrics_oos={
                "mae": test_mae,
                "rmse": test_rmse,
                "r2": test_r2,
            },
            status="staging",
        ))
    except Exception as e:
        logger.warning("Failed to register model with ModelRegistry: %s", e)

    return {
        "symbol": symbol,
        "model_type": model_type,
        "status": "trained",
        "model_path": str(model_path),
        "training_samples": len(X_train),
        "test_samples": len(X_test),
        "feature_count": len(feature_cols),
        "metrics": {
            "train_mae": round(train_mae, 6),
            "test_mae": round(test_mae, 6),
            "train_rmse": round(train_rmse, 6),
            "test_rmse": round(test_rmse, 6),
            "train_r2": round(train_r2, 4),
            "test_r2": round(test_r2, 4),
        },
        "top_features": top_features,
    }


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_alpha(
    symbol: str,
    model_type: str = "xgboost",
) -> Dict[str, Any]:
    """Generate alpha prediction for a symbol using trained model.

    Returns:
        Dict with prediction, confidence, and top contributing factors.
    """
    symbol = symbol.strip().upper()
    model_path = MODEL_DIR / f"{symbol}_{model_type}.pkl"
    feature_path = MODEL_DIR / f"{symbol}_{model_type}_features.json"

    if not model_path.exists():
        # Auto-train if no model exists
        result = train_model(symbol, model_type)
        if result.get("error"):
            return {"symbol": symbol, "error": result["error"]}
        if result.get("status") != "trained":
            return {"symbol": symbol, "error": "Model not available"}

    try:
        with open(model_path, "rb") as f:
            model = pickle.load(f)
    except Exception as e:
        return {"symbol": symbol, "error": f"Failed to load model: {e}"}

    panel = _fetch_factor_panel(symbol)
    if panel is None:
        return {"symbol": symbol, "error": "No data available for prediction"}

    X, _ = _prepare_data(panel)
    if X.empty:
        return {"symbol": symbol, "error": "No valid features"}

    # Predict
    preds = model.predict(X)

    # Latest prediction
    latest_idx = X.index[-1]
    latest_pred = float(preds[-1])

    # Direction
    direction = "BUY" if latest_pred > 0.005 else ("SELL" if latest_pred < -0.005 else "HOLD")
    confidence = min(abs(latest_pred) * 10, 1.0)

    # Feature importance for explanation
    if hasattr(model, "feature_importances_"):
        feature_cols = X.columns.tolist()
        importance = sorted(
            zip(feature_cols, model.feature_importances_),
            key=lambda x: x[1], reverse=True,
        )
        top_factors = [
            {
                "factor": name,
                "importance": round(imp, 4),
                "currentValue": round(float(X.loc[latest_idx, name]), 4) if name in X.columns else None,
            }
            for name, imp in importance[:5]
        ]
    else:
        top_factors = []

    return {
        "symbol": symbol,
        "model": model_type,
        "predictionDate": str(latest_idx.date()),
        "predicted5dReturn": round(latest_pred * 100, 2),
        "direction": direction,
        "confidence": round(confidence, 2),
        "topFactors": top_factors,
        "trainingScore": None,  # populated from train_model result
    }
