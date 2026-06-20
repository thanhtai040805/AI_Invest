"""Local PLM Client — Vietnamese Semantic Search Engine

Quản lý mô hình bge-vi-base và FAISS index cục bộ.
Sử dụng Singleton pattern để tránh load model nhiều lần vào RAM/VRAM.
"""

import logging
import os
import pickle
import numpy as np
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

class LocalPLMClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LocalPLMClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self.model_name = 'vibloai/bge-vi-base'
        self.vector_dim = 768 # Dimension of bge-vi-base
        self.index_path = "faiss_news_index.bin"
        self.metadata_path = "faiss_news_metadata.pkl"
        
        self.model = None
        self.index = None
        self.metadata = [] # Lưu trữ article_id tương ứng với từng vector
        
        self._initialized = True

    def initialize(self):
        """Lazy load model and index."""
        if self.model is None:
            logger.info(f"Loading SentenceTransformer: {self.model_name}...")
            try:
                from sentence_transformers import SentenceTransformer
                # Thêm prefix theo khuyến nghị của BAAI model để tăng hiệu suất truy xuất
                self.model = SentenceTransformer(self.model_name)
                logger.info("Model loaded successfully.")
            except ImportError:
                logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
                raise

        if self.index is None:
            try:
                import faiss
                if os.path.exists(self.index_path) and os.path.exists(self.metadata_path):
                    logger.info("Loading existing FAISS index...")
                    self.index = faiss.read_index(self.index_path)
                    with open(self.metadata_path, 'rb') as f:
                        self.metadata = pickle.load(f)
                else:
                    logger.info("Creating new FAISS IndexFlatIP (Cosine Similarity)...")
                    # IndexFlatIP calculates inner product. 
                    # If vectors are normalized, IP == Cosine Similarity.
                    self.index = faiss.IndexFlatIP(self.vector_dim)
            except ImportError:
                logger.error("faiss not installed. Run: pip install faiss-cpu")
                raise

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """Tạo embeddings và chuẩn hóa (Normalize) để dùng Inner Product làm Cosine."""
        self.initialize()
        # Thêm prefix "Đoạn văn:" cho documents (đặc thù của BAAI models)
        formatted_texts = [f"Đoạn văn: {text}" for text in texts]
        
        embeddings = self.model.encode(formatted_texts, normalize_embeddings=True)
        return np.array(embeddings).astype('float32')

    def embed_query(self, query: str) -> np.ndarray:
        """Tạo embedding cho câu hỏi truy vấn."""
        self.initialize()
        # Thêm prefix "Câu hỏi:" cho query
        formatted_query = f"Câu hỏi: {query}"
        embedding = self.model.encode([formatted_query], normalize_embeddings=True)
        return np.array(embedding).astype('float32')

    def add_to_index(self, article_ids: List[int], texts: List[str]):
        """Nhúng văn bản và thêm vào kho FAISS."""
        self.initialize()
        embeddings = self.embed_texts(texts)
        self.index.add(embeddings)
        self.metadata.extend(article_ids)
        
    def save_index(self):
        """Lưu index xuống ổ cứng."""
        import faiss
        if self.index is not None:
            faiss.write_index(self.index, self.index_path)
            with open(self.metadata_path, 'wb') as f:
                pickle.dump(self.metadata, f)
            logger.info(f"Saved FAISS index with {self.index.ntotal} vectors.")

    def search(self, query: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """Tìm kiếm các bài báo liên quan nhất."""
        self.initialize()
        if self.index.ntotal == 0:
            return []
            
        query_vector = self.embed_query(query)
        distances, indices = self.index.search(query_vector, top_k)
        
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx != -1 and idx < len(self.metadata):
                article_id = self.metadata[idx]
                results.append((article_id, float(dist)))
                
        return results

local_plm = LocalPLMClient()
