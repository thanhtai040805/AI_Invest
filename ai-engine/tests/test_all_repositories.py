"""Test Full Domain Repositories Suite (IOS v5.1).
Kiểm thử toàn diện 5 Repository chuẩn hóa:
- PortfolioRepository
- MarketDataRepository
- FinancialRepository
- UniverseRepository
- IntelligenceRepository
"""

import pytest
from app.domain.repositories import (
    PortfolioRepository,
    MarketDataRepository,
    FinancialRepository,
    UniverseRepository,
    IntelligenceRepository,
)


def test_portfolio_repository():
    """Kiểm thử PortfolioRepository."""
    repo = PortfolioRepository()
    state = repo.get_account_state()
    assert "cash_balance" in state
    assert "total_nav" in state

    positions = repo.get_open_positions()
    assert isinstance(positions, list)


def test_market_data_repository():
    """Kiểm thử MarketDataRepository."""
    repo = MarketDataRepository()
    regime = repo.get_latest_market_regime()
    assert "regime_label" in regime

    ohlcv = repo.get_ohlcv("FPT", limit=5)
    assert isinstance(ohlcv, list)

    daily = repo.get_market_data_daily("FPT", limit=5)
    assert isinstance(daily, list)


def test_financial_repository():
    """Kiểm thử FinancialRepository."""
    repo = FinancialRepository()
    ratios = repo.get_latest_ratios("FPT")
    assert ratios is not None
    assert "pe" in ratios

    stmts = repo.get_financial_statements("FPT", limit=2)
    assert isinstance(stmts, list)


def test_universe_repository():
    """Kiểm thử UniverseRepository."""
    repo = UniverseRepository()
    stocks = repo.get_all_stocks(exchange="HOSE")
    assert isinstance(stocks, list)


def test_intelligence_repository():
    """Kiểm thử IntelligenceRepository."""
    repo = IntelligenceRepository()
    score = repo.get_factor_score("FPT")
    # Should safely return dict or None
    assert score is None or "composite_score" in score
