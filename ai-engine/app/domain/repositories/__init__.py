"""Domain Repositories Module (IOS v5.1)
Tập hợp toàn bộ các lớp Repository quản lý truy xuất và lưu trữ CSDL chuẩn hóa:
1. PortfolioRepository: Quản lý users, positions, orders, portfolio_account, order_executions
2. MarketDataRepository: Quản lý ohlcv, market_data_daily, indicators, foreign_flow, market_regime
3. FinancialRepository: Quản lý financial_statements, financial_ratios, corporate_actions, insider_trades
4. UniverseRepository: Quản lý stocks, instrument_master, universe_securities, beneish_results
5. IntelligenceRepository: Quản lý factor_scores, moat_profiles, knowledge_documents, investment_theses
"""

from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.repositories.market_data_repository import MarketDataRepository
from app.domain.repositories.financial_repository import FinancialRepository
from app.domain.repositories.universe_repository import UniverseRepository
from app.domain.repositories.intelligence_repository import IntelligenceRepository

__all__ = [
    "PortfolioRepository",
    "MarketDataRepository",
    "FinancialRepository",
    "UniverseRepository",
    "IntelligenceRepository",
]
