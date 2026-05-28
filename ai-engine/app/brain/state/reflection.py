"""
Reflection - Reflect on trading decisions to learn from outcomes
"""
import logging
from typing import Dict, Any, Optional
from ..providers.orchestrator import graph_orchestrator

logger = logging.getLogger(__name__)


class Reflector:
    """
    Handles reflection on trading decisions to learn from past outcomes
    """
    
    def __init__(self):
        """Initialize Reflector"""
        logger.info("Reflector initialized")
    
    def _get_reflection_prompt(self) -> str:
        """
        Get the reflection prompt for learning from past decisions
        
        Returns:
            str: Reflection prompt
        """
        return (
            "You are a trading analyst reviewing your own past decision now that the outcome is known.\n"
            "Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).\n\n"
            "Cover in order:\n"
            "1. Was the directional call correct? (cite the alpha figure)\n"
            "2. Which part of the investment thesis held or failed?\n"
            "3. One concrete lesson to apply to the next similar analysis.\n\n"
            "Be specific and terse. Your output will be stored verbatim in a decision log "
            "and re-read by future analysts, so every word must earn its place."
        )
    
    async def reflect_on_decision(
        self,
        decision: str,
        thesis: str,
        actual_return: float,
        benchmark_return: float,
        symbol: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Reflect on a trading decision after knowing the outcome
        
        Args:
            decision: The decision made (BUY/SELL/HOLD)
            thesis: The investment thesis
            actual_return: Actual return achieved
            benchmark_return: Benchmark return (e.g., VN-Index)
            symbol: Stock symbol
            **kwargs: Additional parameters
            
        Returns:
            Dict containing reflection and metadata
        """
        try:
            alpha = actual_return - benchmark_return
            
            prompt = f"""Decision: {decision}
Thesis: {thesis}
Actual Return: {actual_return:+.1%}
Benchmark Return: {benchmark_return:+.1%}
Alpha: {alpha:+.1%}

Reflect on this decision:"""
            
            # Use OpenAI for reflection (reasoning/judge role)
            result = await graph_orchestrator.execute_node(
                node_name="reflection",
                state={"symbol": symbol, "context": prompt},
                temperature=0.3,
            )
            
            reflection = result.get("content", "")
            
            logger.info(f"Reflection generated for {symbol}: {reflection[:100]}...")
            
            return {
                "reflection": reflection,
                "decision": decision,
                "symbol": symbol,
                "actual_return": actual_return,
                "benchmark_return": benchmark_return,
                "alpha": alpha,
                "timestamp": kwargs.get("timestamp"),
            }
            
        except Exception as e:
            logger.error(f"Reflection failed for {symbol}: {str(e)}")
            return {
                "reflection": f"Reflection failed: {str(e)}",
                "decision": decision,
                "symbol": symbol,
                "error": str(e),
            }
    
    def get_past_lessons(
        self,
        past_decisions: list,
        symbol: Optional[str] = None
    ) -> str:
        """
        Extract lessons from past decisions for context
        
        Args:
            past_decisions: List of past decisions with reflections
            symbol: Optional symbol to filter by
            
        Returns:
            str: Compiled lessons from past decisions
        """
        if not past_decisions:
            return ""
        
        lessons = []
        for past_decision in past_decisions:
            if symbol and past_decision.get("symbol") != symbol:
                continue
            
            reflection = past_decision.get("reflection", "")
            if reflection and "failed" not in reflection.lower():
                lessons.append(reflection)
        
        if not lessons:
            return ""
        
        return "Past lessons:\n" + "\n".join([f"- {lesson}" for lesson in lessons[-5:]])


# Singleton instance
reflector = Reflector()
