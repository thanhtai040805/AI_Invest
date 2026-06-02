"""Academic Sentiment Score Factor: Quantitative sentiment score trand trượt 5 ngày.

Directly bridges the live NLP CafeF scraper/RAG database with the quantitative alpha registry.
Extracts articles from news_rag_svc, averages daily sentiment scores per symbol, reindexes
them to match the pricing close panel, and applies a 5-day rolling mean.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd

from app.brain.quant.factors.base import ts_mean

logger = logging.getLogger(__name__)

__alpha_meta__ = {
    'id': 'academic_sentiment_score',
    'nickname': '[SENTIMENT] CaféF NLP news sentiment score trượt 5 ngày',
    'theme': ['sentiment'],
    'formula_latex': r'\mathrm{ts\_mean}(\mathrm{news\_sentiment},\,5)',
    'columns_required': ['close'],
    'extras_required': [],
    'requires_sector': False,
    'universe': ["equity_vn"],
    'frequency': ['1d'],
    'decay_horizon': 5,
    'min_warmup_bars': 5,
    'notes': (
        'Extracts live NLP sentiment scores from the news_rag_svc in-memory vector database, '
        'maps and aligns them to the pricing close panel daily cross-section, and computes a '
        '5-day rolling average to serve as a crowd sentiment factor.'
    ),
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return 5-day rolling average of news sentiment score per stock."""
    close = panel['close']
    
    # Initialize a NaN DataFrame matching the pricing panel's structure
    sentiment_df = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    
    # Try to fetch crawled articles from news_rag_svc
    try:
        from app.services.news_rag import news_rag_svc
        articles = news_rag_svc.get_all_articles()
    except Exception as exc:
        logger.debug("Failed to import or read from news_rag_svc: %s", exc)
        articles = []

    if articles:
        data = []
        for a in articles:
            sym = str(a.get("symbol", "")).upper().strip()
            pdate = a.get("publishDate")
            score = a.get("sentimentScore")
            
            if sym in close.columns and pdate and score is not None:
                try:
                    # Parse YYYY-MM-DD from ISO publish date
                    date_str = pdate.split("T")[0] if "T" in pdate else pdate[:10]
                    dt = pd.to_datetime(date_str)
                    data.append({
                        "date": dt,
                        "symbol": sym,
                        "score": float(score)
                    })
                except Exception:
                    continue
                    
        if data:
            df = pd.DataFrame(data)
            # Take average sentiment score per date per ticker
            pivot_df = df.groupby(["date", "symbol"])["score"].mean().unstack()
            # Align and reindex
            pivot_aligned = pivot_df.reindex(index=close.index, columns=close.columns)
            sentiment_df.update(pivot_aligned)

    # Impute missing: forward fill, then fill remaining starting NaNs with 0
    # to avoid propagation issues in rolling operators
    sentiment_df = sentiment_df.ffill().fillna(0.0)
    
    # Apply 5-day rolling average
    return ts_mean(sentiment_df, 5)
