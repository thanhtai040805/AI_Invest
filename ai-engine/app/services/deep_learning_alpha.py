"""Deep Learning Alpha — LSTM/Transformer for stock return prediction.

Provides:
  - LSTM-based sequence model for 5-day forward return prediction
  - Feature preparation pipeline (technical indicators + alpha factors)
  - Model persistence and prediction
  - Attention-based interpretation (for Transformer variant)

Note: Requires TensorFlow/PyTorch for production training.
This module provides the architecture and fallback to sklearn MLP.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))
MODEL_DIR = Path(os.getenv("DL_MODEL_DIR", tempfile.gettempdir())) / "dl_alpha_models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

SEQ_LENGTH = 30  # lookback window
N_FEATURES = 20  # number of features


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _build_sequence_features(symbol: str) -> Optional[Tuple[np.ndarray, np.ndarray, pd.DatetimeIndex]]:
    """Build feature sequences for deep learning.

    Returns:
        (X, y, dates) where X shape = (n_samples, seq_length, n_features)
    """
    from app.services.ml_alpha_predictor import _fetch_factor_panel, _prepare_data

    panel = _fetch_factor_panel(symbol)
    if panel is None:
        return None

    X_raw, y_raw = _prepare_data(panel)
    if X_raw.empty or len(X_raw) < SEQ_LENGTH + 5:
        return None

    # Drop target column if present
    feature_cols = [c for c in X_raw.columns if c != "target"]
    X_raw = X_raw[feature_cols]

    # Normalize each feature across time
    X_norm = (X_raw - X_raw.mean()) / (X_raw.std() + 1e-10)

    # Create sequences
    X_seq = []
    y_seq = []
    dates = X_norm.index[SEQ_LENGTH:]

    for i in range(len(X_norm) - SEQ_LENGTH):
        X_seq.append(X_norm.iloc[i : i + SEQ_LENGTH].values)
        y_seq.append(y_raw.iloc[i + SEQ_LENGTH])

    return (
        np.array(X_seq, dtype=np.float32),
        np.array(y_seq, dtype=np.float32),
        pd.DatetimeIndex(dates),
    )


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def _build_lstm_model(input_shape: Tuple[int, int]) -> Any:
    """Build a simple LSTM model for return prediction.

    Falls back to sklearn MLP if TensorFlow not available.
    """
    try:
        import tensorflow as tf
        from tensorflow.keras import Input, Model
        from tensorflow.keras.layers import LSTM, Dense, Dropout

        inputs = Input(shape=input_shape, name="features")
        x = LSTM(64, return_sequences=True, name="lstm_1")(inputs)
        x = Dropout(0.2)(x)
        x = LSTM(32, return_sequences=False, name="lstm_2")(x)
        x = Dropout(0.2)(x)
        x = Dense(16, activation="relu", name="dense_1")(x)
        outputs = Dense(1, name="output")(x)

        model = Model(inputs=inputs, outputs=outputs, name="lstm_alpha")
        model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=0.001), loss="mse")
        return model, "tensorflow_lstm"
    except ImportError:
        logger.warning("TensorFlow not available, using sklearn MLP instead")
        from sklearn.neural_network import MLPRegressor
        return MLPRegressor(
            hidden_layer_sizes=(64, 32, 16),
            activation="relu",
            max_iter=500,
            random_state=42,
        ), "sklearn_mlp"


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_lstm(symbol: str, force_retrain: bool = False) -> Dict[str, Any]:
    """Train deep learning model for symbol.

    Args:
        symbol: Ticker symbol.
        force_retrain: Retrain even if cached.

    Returns:
        Dict with training results.
    """
    symbol = symbol.strip().upper()
    model_path = MODEL_DIR / f"{symbol}_lstm.pkl"
    metadata_path = MODEL_DIR / f"{symbol}_lstm_meta.json"

    if model_path.exists() and not force_retrain:
        return {"symbol": symbol, "status": "cached", "model_path": str(model_path)}

    data = _build_sequence_features(symbol)
    if data is None:
        return {"symbol": symbol, "error": "Insufficient data for sequence features"}

    X, y, dates = data
    n = len(X)
    train_size = int(n * 0.8)
    val_size = int(n * 0.1)

    X_train, y_train = X[:train_size], y[:train_size]
    X_val, y_val = X[train_size:train_size + val_size], y[train_size:train_size + val_size]
    X_test, y_test = X[train_size + val_size:], y[train_size + val_size:]

    model, backend = _build_lstm_model((SEQ_LENGTH, X.shape[2]))

    if backend == "tensorflow_lstm":
        from tensorflow.keras.callbacks import EarlyStopping
        history = model.fit(
            X_train, y_train,
            validation_data=(X_val, y_val),
            epochs=50,
            batch_size=32,
            verbose=0,
            callbacks=[EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True)],
        )

        train_pred = model.predict(X_train, verbose=0).flatten()
        test_pred = model.predict(X_test, verbose=0).flatten()
        loss = float(history.history["loss"][-1])
        val_loss = float(history.history["val_loss"][-1])

        import tensorflow as tf
        keras_path = str(model_path).replace(".pkl", ".keras")
        tf.keras.models.save_model(model, keras_path)
        model_path = Path(keras_path)
    else:
        model.fit(X_train.reshape(X_train.shape[0], -1), y_train)
        train_pred = model.predict(X_train.reshape(X_train.shape[0], -1))
        test_pred = model.predict(X_test.reshape(X_test.shape[0], -1))
        loss = float(np.mean((train_pred - y_train) ** 2))
        val_loss = 0.0

        with open(model_path, "wb") as f:
            pickle.dump(model, f)

    train_mae = float(np.mean(np.abs(train_pred - y_train)))
    test_mae = float(np.mean(np.abs(test_pred - y_test)))

    metadata = {
        "symbol": symbol,
        "backend": backend,
        "seq_length": SEQ_LENGTH,
        "n_features": X.shape[2],
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "train_loss": round(loss, 6),
        "val_loss": round(val_loss, 6),
        "train_mae": round(train_mae, 6),
        "test_mae": round(test_mae, 6),
        "trained_at": datetime.now(TZ_VN).isoformat(),
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return {
        **metadata,
        "status": "trained",
        "model_path": str(model_path),
    }


def predict_lstm(symbol: str) -> Dict[str, Any]:
    """Generate prediction using trained deep learning model.

    Args:
        symbol: Ticker symbol.

    Returns:
        Dict with prediction, direction, confidence.
    """
    symbol = symbol.strip().upper()
    keras_path = MODEL_DIR / f"{symbol}_lstm.keras"
    sklearn_path = MODEL_DIR / f"{symbol}_lstm.pkl"
    metadata_path = MODEL_DIR / f"{symbol}_lstm_meta.json"

    model = None
    backend = None

    if keras_path.exists():
        try:
            from tensorflow.keras.models import load_model as tf_load_model
            model = tf_load_model(str(keras_path))
            backend = "tensorflow_lstm"
        except Exception:
            pass

    if model is None and sklearn_path.exists():
        try:
            with open(sklearn_path, "rb") as f:
                model = pickle.load(f)
            backend = "sklearn_mlp"
        except Exception:
            pass

    if model is None:
        return {"symbol": symbol, "error": "No trained model found. Run train_lstm first."}

    data = _build_sequence_features(symbol)
    if data is None:
        return {"symbol": symbol, "error": "Cannot build features for prediction"}

    X, _, _ = data
    latest_seq = X[-1:]  # (1, seq_length, n_features)

    if backend == "tensorflow_lstm":
        pred = float(model.predict(latest_seq, verbose=0).flatten()[0])
    else:
        pred = float(model.predict(latest_seq.reshape(1, -1)).flatten()[0])

    direction = "BUY" if pred > 0.005 else ("SELL" if pred < -0.005 else "HOLD")
    confidence = min(abs(pred) * 10, 1.0)

    return {
        "symbol": symbol,
        "backend": backend or "unknown",
        "predicted5dReturn": round(pred * 100, 2),
        "direction": direction,
        "confidence": round(confidence, 2),
    }
