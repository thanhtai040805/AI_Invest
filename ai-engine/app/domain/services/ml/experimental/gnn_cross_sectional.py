"""
Graph Neural Network (GNN) - Cross-Sectional Momentum Engine
Models ecosystem contagion (e.g. Vingroup ecosystem) and lead-lag relationships in the VN30.
Executes purely on CPU via ONNX Runtime.
"""

import os
import logging
import numpy as np
from typing import List, Dict

try:
    # pyrefly: ignore [missing-import]
    import onnxruntime as ort
except ImportError:
    ort = None

logger = logging.getLogger(__name__)

class GNNEngine:
    def __init__(self, model_dir: str = "app/domain/services/ml/models"):
        self.model_dir = model_dir
        self.session = None
        self._load_model()

    def _load_model(self):
        model_path = os.path.join(self.model_dir, "gcn_cross_sectional_v1.onnx")
        if not os.path.exists(model_path):
            logger.warning(f"GNN ONNX model not found at {model_path}. Awaiting Cloud Training sync.")
            return

        if ort is None:
            logger.error("onnxruntime is not installed. GNN CPU inference will fail.")
            return

        try:
            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            logger.info("GNN ONNX model loaded successfully for CPU Inference.")
        except Exception as e:
            logger.error(f"Failed to load GNN ONNX model: {e}")

    def build_adjacency_matrix(self, universe: List[str]) -> np.ndarray:
        """
        Builds the adjacency matrix mapping structural relationships between VN stocks.
        E.g., VIC owns VHM & VRE. TCB owns TCBS. HPG tracks global steel.
        In a real implementation, this reads from an ecosystem config or correlation matrix.
        """
        N = len(universe)
        # Scaffolded: Identity matrix with slight random correlations
        adj_matrix = np.eye(N, dtype=np.float32)
        adj_matrix += np.random.uniform(0, 0.1, (N, N)).astype(np.float32)
        # Normalize
        row_sums = adj_matrix.sum(axis=1)
        adj_matrix = adj_matrix / row_sums[:, np.newaxis]
        return adj_matrix

    def predict_momentum_spillover(self, universe: List[str], recent_returns: np.ndarray) -> Dict[str, float]:
        """
        Predicts which stocks are about to experience momentum spillover based on the network graph.
        Returns a dictionary of {ticker: contagion_score}.
        """
        if self.session is None:
            logger.warning("GNN session offline. Returning 0.0 contagion scores.")
            return {ticker: 0.0 for ticker in universe}
            
        try:
            A = self.build_adjacency_matrix(universe)
            # Shape requirements for ONNX GCN typically: (Node_Features, Adjacency_Matrix)
            # recent_returns acts as the node features (N_nodes, N_features)
            
            ort_inputs = {
                "node_features": recent_returns.astype(np.float32),
                "adjacency_matrix": A
            }
            ort_outs = self.session.run(None, ort_inputs)
            
            # GNN outputs an expected return delta for each node
            contagion_scores = ort_outs[0].flatten()
            return {ticker: float(score) for ticker, score in zip(universe, contagion_scores)}
            
        except Exception as e:
            logger.error(f"GNN prediction failed: {e}")
            return {ticker: 0.0 for ticker in universe}

gnn_engine = GNNEngine()
