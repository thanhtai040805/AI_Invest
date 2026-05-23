"""
Signal Processing - Extract portfolio ratings from decisions
"""
import logging
import re
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class SignalProcessor:
    """
    Extract 5-tier portfolio rating from Portfolio Manager's decision
    """
    
    RATING_PATTERN = r'\*\*Rating\*\*:\s*(Buy|Overweight|Hold|Underweight|Sell)'
    
    def __init__(self):
        """Initialize Signal Processor"""
        logger.info("Signal Processor initialized")
    
    def parse_rating(self, text: str) -> str:
        """
        Parse rating from markdown text
        
        Args:
            text: Markdown text containing rating
            
        Returns:
            str: Rating (Buy/Overweight/Hold/Underweight/Sell) or "Hold" as default
        """
        match = re.search(self.RATING_PATTERN, text, re.IGNORECASE)
        if match:
            return match.group(1).capitalize()
        
        # Fallback: look for rating keywords
        text_lower = text.lower()
        if "buy" in text_lower and "strong buy" in text_lower:
            return "Buy"
        elif "overweight" in text_lower:
            return "Overweight"
        elif "sell" in text_lower and "strong sell" in text_lower:
            return "Sell"
        elif "underweight" in text_lower:
            return "Underweight"
        else:
            return "Hold"
    
    def process_signal(self, decision: str, thesis: str, confidence: float) -> Dict[str, Any]:
        """
        Process trading signal and extract portfolio rating
        
        Args:
            decision: Decision (BUY/SELL/HOLD)
            thesis: Investment thesis
            confidence: Confidence score
            
        Returns:
            Dict containing processed signal with rating
        """
        try:
            # Combine decision and thesis for rating extraction
            full_signal = f"Decision: {decision}\nThesis: {thesis}"
            
            # Extract rating
            rating = self.parse_rating(full_signal)
            
            # Map decision to rating if not found
            if rating == "Hold":
                if decision == "BUY":
                    rating = "Overweight" if confidence > 0.7 else "Hold"
                elif decision == "SELL":
                    rating = "Underweight" if confidence > 0.7 else "Hold"
                else:
                    rating = "Hold"
            
            logger.info(f"Signal processed: {decision} -> {rating} (confidence: {confidence})")
            
            return {
                "decision": decision,
                "rating": rating,
                "confidence": confidence,
                "thesis": thesis,
            }
            
        except Exception as e:
            logger.error(f"Signal processing failed: {str(e)}")
            return {
                "decision": decision,
                "rating": "Hold",
                "confidence": confidence,
                "thesis": thesis,
                "error": str(e),
            }
    
    def get_rating_strength(self, rating: str) -> int:
        """
        Get numeric strength for rating (1-5 scale)
        
        Args:
            rating: Rating string
            
        Returns:
            int: Strength (1=Sell, 2=Underweight, 3=Hold, 4=Overweight, 5=Buy)
        """
        rating_map = {
            "Sell": 1,
            "Underweight": 2,
            "Hold": 3,
            "Overweight": 4,
            "Buy": 5,
        }
        return rating_map.get(rating, 3)


# Singleton instance
signal_processor = SignalProcessor()
