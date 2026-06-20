"""Configuration helper for external APIs."""

def get_config():
    """Returns a dictionary of data vendor settings."""
    return {
        "data_vendors": {
            "core_stock_apis": "yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "yfinance",
            "news_data": "yfinance",
        },
        "tool_vendors": {}
    }
