"""
Fundamentals Tool - Vietnam stock fundamentals data adapter
Wraps DNSE data for P/E, ROE, EPS, and other financial metrics
"""
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class FundamentalsTool:
    """
    Fundamentals data tool for Vietnam market
    Wraps DNSE financial data
    """
    
    def __init__(self):
        """Initialize Fundamentals Tool"""
        # Import here to avoid circular imports
        from app.infrastructure.external_api.market_data_service import MarketDataService
        self.market_service = MarketDataService()
        logger.info("Fundamentals Tool initialized")
    
    async def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Get fundamental data for a symbol
        
        Args:
            symbol: Stock symbol (e.g., "VIC", "FPT")
            
        Returns:
            Dict containing fundamental data
        """
        try:
            # Call DNSE service for fundamental data
            data = await self.market_service.get_fundamental_data(symbol)
            
            return {
                "symbol": symbol,
                "pe_ratio": data.get("pe_ratio"),
                "pb_ratio": data.get("pb_ratio"),
                "eps": data.get("eps"),
                "roe": data.get("roe"),
                "roa": data.get("roa"),
                "debt_to_equity": data.get("debt_to_equity"),
                "market_cap": data.get("market_cap"),
                "dividend_yield": data.get("dividend_yield"),
                "book_value_per_share": data.get("book_value_per_share"),
                "revenue": data.get("revenue"),
                "net_income": data.get("net_income"),
                "total_assets": data.get("total_assets"),
                "total_liabilities": data.get("total_liabilities"),
                "updated_at": data.get("updated_at"),
            }
            
        except Exception as e:
            logger.error(f"Failed to get fundamentals for {symbol}: {str(e)}")
            # Return empty dict with symbol on error
            return {"symbol": symbol, "error": str(e)}
    
    async def get_financial_ratios(self, symbol: str) -> Dict[str, Any]:
        """
        Get key financial ratios for analysis
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dict containing calculated ratios
        """
        try:
            fundamentals = await self.get_fundamentals(symbol)
            
            if "error" in fundamentals:
                return fundamentals
            
            # Calculate additional ratios
            pe = fundamentals.get("pe_ratio")
            pb = fundamentals.get("pb_ratio")
            roe = fundamentals.get("roe")
            
            # PEG ratio (assuming 10% growth rate if not available)
            growth_rate = 0.10  # Default assumption
            peg = pe / growth_rate if pe else None
            
            return {
                "symbol": symbol,
                "pe_ratio": pe,
                "pb_ratio": pb,
                "roe": roe,
                "peg_ratio": peg,
                "valuation": self._classify_valuation(pe, pb),
                "profitability": self._classify_profitability(roe),
                "overall_score": self._calculate_overall_score(pe, pb, roe),
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate ratios for {symbol}: {str(e)}")
            raise
    
    def _classify_valuation(self, pe: Optional[float], pb: Optional[float]) -> str:
        """
        Classify valuation based on P/E and P/B ratios
        
        Args:
            pe: P/E ratio
            pb: P/B ratio
            
        Returns:
            str: Valuation classification (UNDervalued, FAIR, OVERvalued)
        """
        if not pe or not pb:
            return "UNKNOWN"
        
        # Vietnam market typical ranges (may need adjustment)
        if pe < 10 and pb < 1.0:
            return "UNDervalued"
        elif pe > 25 or pb > 3.0:
            return "OVERvalued"
        else:
            return "FAIR"
    
    def _classify_profitability(self, roe: Optional[float]) -> str:
        """
        Classify profitability based on ROE
        
        Args:
            roe: Return on Equity
            
        Returns:
            str: Profitability classification
        """
        if not roe:
            return "UNKNOWN"
        
        if roe > 20:
            return "EXCELLENT"
        elif roe > 15:
            return "GOOD"
        elif roe > 10:
            return "AVERAGE"
        else:
            return "POOR"
    
    def _calculate_overall_score(
        self,
        pe: Optional[float],
        pb: Optional[float],
        roe: Optional[float]
    ) -> float:
        """
        Calculate overall fundamental score (0-100)
        
        Args:
            pe: P/E ratio
            pb: P/B ratio
            roe: Return on Equity
            
        Returns:
            float: Overall score
        """
        score = 50.0  # Base score
        
        # Adjust based on valuation
        if pe and pe < 15:
            score += 10
        elif pe and pe > 25:
            score -= 10
        
        # Adjust based on profitability
        if roe and roe > 20:
            score += 15
        elif roe and roe < 10:
            score -= 15
        
        # Adjust based on P/B
        if pb and pb < 1.0:
            score += 5
        elif pb and pb > 2.5:
            score -= 5
        
        # Cap at 0-100
        return max(0, min(100, score))
    
    async def get_industry_comparison(
        self,
        symbol: str,
        industry: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Compare symbol with industry averages
        
        Args:
            symbol: Stock symbol
            industry: Industry name (optional)
            
        Returns:
            Dict containing comparison data
        """
        try:
            # Get symbol fundamentals
            symbol_fundamentals = await self.get_fundamentals(symbol)
            
            # TODO: Implement industry comparison with DNSE data
            # For now, return placeholder
            return {
                "symbol": symbol,
                "industry": industry or "Unknown",
                "symbol_pe": symbol_fundamentals.get("pe_ratio"),
                "industry_pe": None,  # To be implemented
                "symbol_roe": symbol_fundamentals.get("roe"),
                "industry_roe": None,  # To be implemented
                "comparison": "PENDING",
            }
            
        except Exception as e:
            logger.error(f"Failed to get industry comparison for {symbol}: {str(e)}")
            raise
