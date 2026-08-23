"""
Reinforcement Learning (RL) - Execution Agent
Uses a Deep Deterministic Policy Gradient (DDPG) model trained on HOSE Level-2 order book data.
Executes algorithmic trades (TWAP/VWAP/Iceberg) via ONNX Runtime on CPU to minimize ATC manipulation slippage.
"""

import os
import logging
import numpy as np

try:
    # pyrefly: ignore [missing-import]
    import onnxruntime as ort
except ImportError:
    ort = None

logger = logging.getLogger(__name__)

class RLExecutionAgent:
    def __init__(self, model_dir: str = "app/domain/services/ml/models"):
        self.model_dir = model_dir
        self.session = None
        self._load_model()

    def _load_model(self):
        model_path = os.path.join(self.model_dir, "ddpg_execution_v1.onnx")
        if not os.path.exists(model_path):
            logger.warning(f"RL Execution ONNX model not found at {model_path}. Awaiting Cloud Training sync.")
            return

        if ort is None:
            logger.error("onnxruntime is not installed. RL Execution will fallback to naive VWAP.")
            return

        try:
            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            logger.info("RL Execution ONNX model loaded successfully for CPU Inference.")
        except Exception as e:
            logger.error(f"Failed to load RL Execution ONNX model: {e}")

    def compute_optimal_split(self, target_volume: int, current_bid_ask_spread: float, time_to_close_mins: float, is_atc_phase: bool) -> int:
        """
        Determines the optimal chunk size to execute right now, based on the RL policy.
        """
        if self.session is None or target_volume <= 0:
            # Fallback to naive TWAP (Time-Weighted Average Price) logic
            return int(target_volume / max(1, time_to_close_mins))

        try:
            # RL State Vector: [remaining_volume, spread, time_left, is_atc, volume_imbalance]
            # volume_imbalance is mocked here; in prod it reads real-time Level-2 data
            state_vector = np.array([[
                float(target_volume),
                float(current_bid_ask_spread),
                float(time_to_close_mins),
                1.0 if is_atc_phase else 0.0,
                0.0 # Mocked imbalance
            ]], dtype=np.float32)

            ort_inputs = {"state": state_vector}
            ort_outs = self.session.run(None, ort_inputs)
            
            # DDPG outputs an action in [-1, 1] representing the percentage of target_volume to execute
            action_pct = float(ort_outs[0][0][0])
            # Scale to [0, 1] bounds (assuming tanh output in DDPG)
            action_pct = max(0.0, min(1.0, (action_pct + 1.0) / 2.0))
            
            chunk_size = int(target_volume * action_pct)
            return chunk_size

        except Exception as e:
            logger.error(f"RL optimal split calculation failed: {e}")
            return int(target_volume / max(1, time_to_close_mins))

rl_execution_agent = RLExecutionAgent()
