"""
News RAG Service — Lightweight semantic news retrieval using TF-IDF and Cosine Similarity.
Provides robust RAG capabilities for LLM prompts without requiring pgvector DB setup.
"""
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger("ai_engine.news_rag")

class NewsRAGService:
    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=1000)
        self.articles: List[Dict[str, Any]] = []
        self.tfidf_matrix = None

    def add_articles(self, news_list: List[Dict[str, Any]]):
        """Add new articles to the in-memory RAG database."""
        for item in news_list:
            # Avoid duplicates by newsId
            if not any(a.get("newsId") == item.get("newsId") for a in self.articles):
                self.articles.append(item)
        
        # Retrain TF-IDF model if we have articles
        if self.articles:
            texts = [self._format_article(a) for a in self.articles]
            try:
                self.tfidf_matrix = self.vectorizer.fit_transform(texts)
            except Exception as e:
                logger.error(f"Failed to fit TF-IDF vectorizer: {e}")

    def query(self, query_text: str, symbol: Optional[str] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        """Retrieve most semantically similar articles using Cosine Similarity."""
        if not self.articles or self.tfidf_matrix is None:
            return []

        # Filter by symbol first if specified
        filtered_indices = []
        if symbol:
            filtered_indices = [i for i, a in enumerate(self.articles) if a.get("symbol") == symbol]
        else:
            filtered_indices = list(range(len(self.articles)))

        if not filtered_indices:
            return []

        try:
            # Vectorize query
            query_vec = self.vectorizer.transform([query_text])
            
            # Compute similarity scores against filtered articles
            filtered_matrix = self.tfidf_matrix[filtered_indices]
            sim_scores = cosine_similarity(query_vec, filtered_matrix).flatten()
            
            # Sort and pick top K
            top_indices = np.argsort(sim_scores)[::-1][:top_k]
            
            results = []
            for idx in top_indices:
                score = sim_scores[idx]
                original_idx = filtered_indices[idx]
                article = self.articles[original_idx]
                results.append({
                    **article,
                    "similarityScore": float(score)
                })
            return results
        except Exception as e:
            logger.error(f"Error querying RAG vector store: {e}")
            return []

    def _format_article(self, article: Dict[str, Any]) -> str:
        """Format an article for vectorization."""
        return f"{article.get('symbol', '')} {article.get('title', '')} {article.get('friendlyKeyword', '') or ''} {article.get('content', '') or ''}"
    
    def has_article(self, news_id: str) -> bool:
        """Check if article exists in RAG by newsId."""
        return any(a.get("newsId") == news_id for a in self.articles)

    def get_all_articles(self) -> List[Dict[str, Any]]:
        """Get all stored articles."""
        return list(self.articles)

    def clear_database(self) -> None:
        """Completely resets the in-memory database and clears vectors."""
        try:
            # 1. Clear the reference lists to free memory
            self.articles.clear()
            
            # 2. Drop the existing TF-IDF sparse matrix
            self.tfidf_matrix = None
            
            # 3. Re-instantiate the vectorizer to wipe its internal vocabulary
            self.vectorizer = TfidfVectorizer(max_features=1000)
            
            logger.info("News RAG Service storage and vectorizer vocabulary successfully reset.")
        except Exception as e:
            logger.error(f"Failed to reset News RAG Service database: {e}")
            
# Singleton instance
news_rag_svc = NewsRAGService()
