"""
Temporal Fusion Transformer (TFT) - Edge Inference Engine
Handles fast CPU inference using ONNX Runtime for the TFT models trained on Cloud GPUs.
"""

import os
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any

try:
    # pyrefly: ignore [missing-import]
    import onnxruntime as ort
except ImportError:
    ort = None

logger = logging.getLogger(__name__)

class TFTEngine:
    def __init__(self, model_dir: str = "app/domain/services/ml/models"):
        self.model_dir = model_dir
        self.session = None
        self._load_model()

    def _load_model(self):
        """
        Loads the pre-trained TFT ONNX model into memory for fast CPU execution.
        """
        model_path = os.path.join(self.model_dir, "tft_v1.onnx")
        if not os.path.exists(model_path):
            logger.warning(f"TFT ONNX model not found at {model_path}. Awaiting Cloud Training sync.")
            return

        if ort is None:
            logger.error("onnxruntime is not installed. TFT CPU inference will fail. Run: pip install onnxruntime")
            return

        try:
            # Force CPU Execution Provider to comply with local infrastructure limits
            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            logger.info("TFT ONNX model loaded successfully for CPU Inference.")
        except Exception as e:
            logger.error(f"Failed to load TFT ONNX model: {e}")

    def format_tft_inputs(self, historical_data: pd.DataFrame, known_future_data: pd.DataFrame, static_data: Dict[str, Any]) -> dict:
        """
        Formats data into the strict tensor shapes required by TFT.
        - historical_data: past prices, volume, MACD, etc.
        - known_future_data: ETF rebalance dates, dividend dates, macro scheduled releases.
        - static_data: Sector classification, market cap bucket.
        """
        # Scaffolded for ONNX input shapes (Batch, Seq_Len, Features)
        # In a real scenario, this matches the exact PyTorch TFT tensor signature.
        hist_tensor = np.zeros((1, 30, 15), dtype=np.float32) # (1 batch, 30 days history, 15 features)
        future_tensor = np.zeros((1, 5, 2), dtype=np.float32) # (1 batch, 5 days horizon, 2 known features)
        static_tensor = np.zeros((1, 3), dtype=np.float32)    # (1 batch, 3 static features)
        
        return {
            "past_inputs": hist_tensor,
            "known_future_inputs": future_tensor,
            "static_inputs": static_tensor
        }

    def predict(self, historical_data: pd.DataFrame, known_future_data: pd.DataFrame, static_data: Dict[str, Any]) -> float:
        """
        Executes millisecond-latency prediction on CPU.
        """
        if self.session is None:
            logger.error("TFT session is offline. Cannot predict.")
            return 0.0

        try:
            ort_inputs = self.format_tft_inputs(historical_data, known_future_data, static_data)
            ort_outs = self.session.run(None, ort_inputs)
            
            # TFT typically outputs quantiles (10th, 50th, 90th). Return the median (50th).
            prediction = float(ort_outs[0][0][1]) 
            return prediction
        except Exception as e:
            logger.error(f"TFT prediction failed: {e}")
            return 0.0

tft_engine = TFTEngine()
