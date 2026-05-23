"""
Concurrency - Support for parallel analyst execution
"""
import logging
import asyncio
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AnalystType(str, Enum):
    """Types of analysts"""
    MARKET = "market"
    FUNDAMENTAL = "fundamental"
    NEWS = "news"
    SOCIAL = "social"


@dataclass
class AnalystSpec:
    """Specification for an analyst execution"""
    key: str
    agent_node: str
    tool_node: str
    clear_node: str


@dataclass
class ExecutionPlan:
    """Plan for analyst execution"""
    specs: List[AnalystSpec]
    concurrency_limit: int


class ConcurrencyManager:
    """
    Manages concurrent execution of analysts
    """
    
    def __init__(self, concurrency_limit: int = 2):
        """
        Initialize Concurrency Manager
        
        Args:
            concurrency_limit: Maximum number of analysts to run concurrently
        """
        self.concurrency_limit = concurrency_limit
        logger.info(f"Concurrency Manager initialized with limit: {concurrency_limit}")
    
    def build_execution_plan(
        self,
        selected_analysts: List[str],
        concurrency_limit: Optional[int] = None,
    ) -> ExecutionPlan:
        """
        Build execution plan for selected analysts
        
        Args:
            selected_analysts: List of analyst types to include
            concurrency_limit: Override default concurrency limit
            
        Returns:
            ExecutionPlan: Execution plan
        """
        if concurrency_limit is None:
            concurrency_limit = self.concurrency_limit
        
        # Map analyst types to node names
        analyst_map = {
            AnalystType.MARKET: AnalystSpec(
                key="market",
                agent_node="market_analyst",
                tool_node="tools_market",
                clear_node="clear_market"
            ),
            AnalystType.FUNDAMENTAL: AnalystSpec(
                key="fundamental",
                agent_node="fund_analyst",
                tool_node="tools_fundamental",
                clear_node="clear_fundamental"
            ),
            AnalystType.NEWS: AnalystSpec(
                key="news",
                agent_node="news_analyst",
                tool_node="tools_news",
                clear_node="clear_news"
            ),
            AnalystType.SOCIAL: AnalystSpec(
                key="social",
                agent_node="social_analyst",
                tool_node="tools_social",
                clear_node="clear_social"
            ),
        }
        
        # Build specs for selected analysts
        specs = []
        for analyst_type in selected_analysts:
            if analyst_type in [a.value for a in AnalystType]:
                spec = analyst_map[AnalystType(analyst_type)]
                specs.append(spec)
        
        return ExecutionPlan(
            specs=specs,
            concurrency_limit=concurrency_limit,
        )
    
    async def execute_analysts_parallel(
        self,
        specs: List[AnalystSpec],
        state: Dict[str, Any],
        execute_func: callable,
    ) -> Dict[str, Any]:
        """
        Execute analysts in parallel with concurrency limit
        
        Args:
            specs: List of analyst specifications
            state: Current graph state
            execute_func: Function to execute a single analyst
            
        Returns:
            Dict containing results from all analysts
        """
        results = {}
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.concurrency_limit)
        
        async def execute_with_limit(spec: AnalystSpec):
            async with semaphore:
                try:
                    result = await execute_func(spec, state)
                    results[spec.key] = result
                    logger.info(f"Analyst {spec.key} completed")
                except Exception as e:
                    logger.error(f"Analyst {spec.key} failed: {str(e)}")
                    results[spec.key] = {"error": str(e)}
        
        # Execute all analysts with concurrency limit
        tasks = [execute_with_limit(spec) for spec in specs]
        await asyncio.gather(*tasks)
        
        return results
    
    async def execute_analysts_sequential(
        self,
        specs: List[AnalystSpec],
        state: Dict[str, Any],
        execute_func: callable,
    ) -> Dict[str, Any]:
        """
        Execute analysts sequentially (for debugging or when order matters)
        
        Args:
            specs: List of analyst specifications
            state: Current graph state
            execute_func: Function to execute a single analyst
            
        Returns:
            Dict containing results from all analysts
        """
        results = {}
        
        for spec in specs:
            try:
                result = await execute_func(spec, state)
                results[spec.key] = result
                logger.info(f"Analyst {spec.key} completed (sequential)")
            except Exception as e:
                logger.error(f"Analyst {spec.key} failed: {str(e)}")
                results[spec.key] = {"error": str(e)}
        
        return results
    
    def get_execution_summary(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get summary of execution results
        
        Args:
            results: Results from analyst execution
            
        Returns:
            Dict containing execution summary
        """
        total = len(results)
        successful = sum(1 for r in results.values() if "error" not in r)
        failed = total - successful
        
        return {
            "total_analysts": total,
            "successful": successful,
            "failed": failed,
            "success_rate": f"{(successful / total * 100):.1f}%" if total > 0 else "0%",
        }


# Singleton instance
concurrency_manager = ConcurrencyManager()
