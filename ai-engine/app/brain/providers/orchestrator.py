"""
Graph Orchestrator - LangGraph Pipeline with Multi-Model Routing
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from enum import Enum

from .router import IntentRouter, ModelType, IntentType
from .gemini_client import GeminiAgent
from .groq_client import GroqAgent
from .openrouter_client import OpenRouterAgent
from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """Task types for model selection"""
    REALTIME_SIGNAL = "realtime_signal"
    QUICK_ANALYSIS = "quick_analysis"
    HEADLINE_CLASSIFICATION = "headline_classification"
    DEEP_RESEARCH = "deep_research"
    CHATBOT = "chatbot"
    LONG_FORM_ANALYSIS = "long_form_analysis"
    FALLBACK = "fallback"
    BATCH = "batch"
    EXPERIMENTATION = "experimentation"


class GraphOrchestrator:
    """
    LangGraph Pipeline với Multi-Model Routing
    """
    
    MODEL_TIERS = {
        "groq": {
            "use_cases": [
                TaskType.REALTIME_SIGNAL,
                TaskType.QUICK_ANALYSIS,
                TaskType.HEADLINE_CLASSIFICATION,
            ],
            "model": "llama-3.3-70b-versatile",
            "priority": 1,
        },
        "gemini": {
            "use_cases": [
                TaskType.DEEP_RESEARCH,
                TaskType.CHATBOT,
                TaskType.LONG_FORM_ANALYSIS,
            ],
            "model": "gemini-2.0-flash-exp",
            "priority": 2,
        },
        "openrouter": {
            "use_cases": [
                TaskType.FALLBACK,
                TaskType.BATCH,
                TaskType.EXPERIMENTATION,
            ],
            "model": "anthropic/claude-3.5-sonnet",
            "priority": 3,
        },
    }
    
    CONFIDENCE_THRESHOLDS = {
        "low": 0.6,      # Call secondary model
        "medium": 0.75,  # Accept primary result
        "high": 0.85,     # Accept without verification
    }
    
    def __init__(self):
        """Initialize Graph Orchestrator"""
        self.settings = get_settings()
        self.intent_router = IntentRouter()
        
        # Initialize agents
        self.groq_client = GroqAgent(
            api_key=self.settings.llm_groq_key,
            model=self.settings.llm_groq_model,
            enable_fallback=self.settings.enable_fallback,
        )
        
        self.gemini_client = GeminiAgent(
            api_key=self.settings.llm_gemini_key,
            model=self.settings.llm_gemini_model,
            enable_fallback=self.settings.enable_fallback,
        )
        
        self.openrouter_client = OpenRouterAgent(
            api_key=self.settings.llm_openrouter_key,
            model=self.settings.llm_openrouter_model,
            enable_fallback=self.settings.enable_fallback,
        )
        
        # Map model types to client instances
        self.model_clients = {
            ModelType.GROQ: self.groq_client,
            ModelType.GEMINI: self.gemini_client,
            ModelType.OPENROUTER: self.openrouter_client,
        }
        
        logger.info("Graph Orchestrator initialized with multi-model routing")
    
    def _classify_task(self, node_name: str) -> TaskType:
        """
        Classify task type based on node name or context
        
        Args:
            node_name: Name of the node being executed
            
        Returns:
            TaskType: Classified task type
        """
        node_lower = node_name.lower()
        
        if "signal" in node_lower or "realtime" in node_lower:
            return TaskType.REALTIME_SIGNAL
        elif "quick" in node_lower or "fast" in node_lower:
            return TaskType.QUICK_ANALYSIS
        elif "headline" in node_lower or "news" in node_lower:
            return TaskType.HEADLINE_CLASSIFICATION
        elif "research" in node_lower or "deep" in node_lower:
            return TaskType.DEEP_RESEARCH
        elif "chat" in node_lower:
            return TaskType.CHATBOT
        elif "long" in node_lower or "report" in node_lower:
            return TaskType.LONG_FORM_ANALYSIS
        else:
            return TaskType.QUICK_ANALYSIS  # Default
    
    def _select_model(self, task_type: TaskType) -> ModelType:
        """
        Select appropriate model for task type
        
        Args:
            task_type: Type of task
            
        Returns:
            ModelType: Selected model
        """
        # Find which provider supports this task
        for provider, config in self.MODEL_TIERS.items():
            if task_type in config["use_cases"]:
                return ModelType(provider)
        
        # Default to Groq for unknown tasks
        return ModelType.GROQ
    
    async def _call_model(
        self,
        model: ModelType,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Call specific model
        
        Args:
            model: Model type to use
            prompt: Prompt to send
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        client = self.model_clients.get(model)
        if not client:
            raise ValueError(f"Model client not found: {model}")
        
        response = await client.call_with_retry(prompt, **kwargs)
        
        # Add confidence score (simplified for now)
        # In production, this would be based on model-specific metrics
        response["confidence"] = self._calculate_confidence(response)
        
        return response
    
    def _calculate_confidence(self, response: Dict[str, Any]) -> float:
        """
        Calculate confidence score for response
        Simplified version - in production use more sophisticated methods
        
        Args:
            response: Response from model
            
        Returns:
            float: Confidence score (0-1)
        """
        # Simplified confidence calculation
        # In production, use:
        # - Log probabilities if available
        # - Response length and structure
        # - Model-specific calibration
        
        content = response.get("content", "")
        
        # Base confidence
        confidence = 0.75
        
        # Increase if response is structured
        if any(char in content for char in ["{", "}", "[", "]"]):
            confidence += 0.10
        
        # Increase if response is substantial
        if len(content) > 100:
            confidence += 0.05
        
        # Cap at 1.0
        return min(confidence, 1.0)
    
    async def _parallel_execution(
        self,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute parallel analysis with all models for high-risk decisions
        
        Args:
            prompt: Prompt to analyze
            **kwargs: Additional parameters
            
        Returns:
            Dict containing consensus result
        """
        logger.info("Starting parallel execution with all models")
        
        # Run all models in parallel
        results = await asyncio.gather(
            self.groq_client.call_with_retry(prompt, **kwargs),
            self.gemini_client.call_with_retry(prompt, **kwargs),
            self.openrouter_client.call_with_retry(prompt, **kwargs),
            return_exceptions=True,
        )
        
        # Filter out exceptions
        valid_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Model {i} failed: {str(result)}")
            else:
                valid_results.append(result)
        
        if not valid_results:
            raise LLMError("All models failed in parallel execution")
        
        # Consensus voting
        return self._consensus_vote(valid_results)
    
    def _consensus_vote(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Vote/consensus mechanism for parallel results
        
        Args:
            results: List of results from different models
            
        Returns:
            Dict containing consensus result
        """
        # For now, use the result with highest confidence
        # In production, implement more sophisticated voting
        
        best_result = max(results, key=lambda r: r.get("confidence", 0))
        
        # Add metadata about consensus
        best_result["consensus_metadata"] = {
            "total_models": len(results),
            "successful_models": len(results),
            "selected_model": best_result.get("model"),
            "all_confidences": [r.get("confidence", 0) for r in results],
        }
        
        return best_result
    
    async def execute_node(
        self,
        node_name: str,
        state: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute a graph node with multi-model routing
        
        Args:
            node_name: Name of the node
            state: Graph state containing context
            **kwargs: Additional parameters
            
        Returns:
            Dict containing execution result
        """
        task_type = self._classify_task(node_name)
        model = self._select_model(task_type)
        
        # Check if this is a high-risk decision requiring parallel execution
        decision_type = state.get("decision_type")
        if decision_type == "auto_trade" and self.settings.max_parallel_calls >= 3:
            logger.info(f"High-risk decision detected, using parallel execution")
            prompt = self._state_to_prompt(state, node_name)
            return await self._parallel_execution(prompt, **kwargs)
        
        # Single model execution
        prompt = self._state_to_prompt(state, node_name)
        result = await self._call_model(model, prompt, **kwargs)
        
        # Check confidence and fallback if needed
        confidence = result.get("confidence", 0)
        if confidence < self.CONFIDENCE_THRESHOLDS["low"] and self.settings.enable_fallback:
            logger.info(f"Low confidence ({confidence}), calling fallback model")
            
            # Get fallback model
            fallback_model = self._get_fallback_model(model)
            fallback_result = await self._call_model(fallback_model, prompt, **kwargs)
            
            # Merge results
            return self._merge_results(result, fallback_result)
        
        return result
    
    def _get_fallback_model(self, current_model: ModelType) -> ModelType:
        """
        Get fallback model for given model
        
        Args:
            current_model: Current model that failed or had low confidence
            
        Returns:
            ModelType: Fallback model
        """
        fallback_chain = {
            ModelType.GROQ: ModelType.GEMINI,
            ModelType.GEMINI: ModelType.OPENROUTER,
            ModelType.OPENROUTER: ModelType.GROQ,
        }
        return fallback_chain.get(current_model, ModelType.GEMINI)
    
    def _merge_results(
        self,
        primary: Dict[str, Any],
        fallback: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge results from primary and fallback models
        
        Args:
            primary: Primary model result
            fallback: Fallback model result
            
        Returns:
            Dict containing merged result
        """
        # Use the result with higher confidence
        if fallback.get("confidence", 0) > primary.get("confidence", 0):
            merged = fallback.copy()
            merged["fallback_used"] = True
            merged["primary_confidence"] = primary.get("confidence", 0)
        else:
            merged = primary.copy()
            merged["fallback_used"] = False
            merged["fallback_confidence"] = fallback.get("confidence", 0)
        
        return merged
    
    def _state_to_prompt(self, state: Dict[str, Any], node_name: str) -> str:
        """
        Convert graph state to prompt
        
        Args:
            state: Graph state
            node_name: Name of the node
            
        Returns:
            str: Prompt string
        """
        # Extract relevant information from state
        symbol = state.get("symbol", "")
        context = state.get("context", "")
        data = state.get("data", {})
        
        prompt = f"Node: {node_name}\n"
        
        if symbol:
            prompt += f"Symbol: {symbol}\n"
        
        if context:
            prompt += f"Context: {context}\n"
        
        if data:
            prompt += f"Data: {data}\n"
        
        return prompt
    
    async def execute_simple_pipeline(
        self,
        user_input: str,
        model: ModelType,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute simple pipeline (for CHAT intent)
        
        Args:
            user_input: User input
            model: Model to use
            **kwargs: Additional parameters
            
        Returns:
            Dict containing result
        """
        return await self._call_model(model, user_input, **kwargs)
    
    async def execute_graph_pipeline(
        self,
        user_input: str,
        model: ModelType,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute graph pipeline (for RESEARCH/SIGNAL intent)
        
        Args:
            user_input: User input
            model: Model to use
            **kwargs: Additional parameters
            
        Returns:
            Dict containing result
        """
        # For now, this is a simplified version
        # In production, this would execute the full LangGraph
        
        state = {
            "symbol": kwargs.get("symbol", ""),
            "context": user_input,
            "data": kwargs.get("data", {}),
            "decision_type": kwargs.get("decision_type", "normal"),
        }
        
        return await self.execute_node("analysis", state, **kwargs)
    
    def get_model_statistics(self) -> Dict[str, Any]:
        """
        Get statistics for all models
        
        Returns:
            Dict containing statistics for each model
        """
        return {
            "groq": self.groq_client.get_statistics(),
            "gemini": self.gemini_client.get_statistics(),
            "openrouter": self.openrouter_client.get_statistics(),
        }


# Singleton instance
graph_orchestrator = GraphOrchestrator()
