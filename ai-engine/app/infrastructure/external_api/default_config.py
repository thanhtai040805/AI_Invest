"""Default configuration for trading dataflows."""

DEFAULT_CONFIG = {
    "data_vendors": {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
    },
    "tool_vendors": {},
    "output_language": "English",
    "news_article_limit": 10,
    "global_news_lookback_days": 7,
    "global_news_article_limit": 20,
    "global_news_queries": ["stock market", "economy", "finance"],
    "data_cache_dir": "./cache/trading",
}
