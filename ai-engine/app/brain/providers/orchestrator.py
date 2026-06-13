"""
Graph Orchestrator - LangGraph Pipeline with Multi-Model Routing
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from enum import Enum

from .router import IntentRouter, ModelType, IntentType
from .groq_client import GroqAgent
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
    STRUCTURED_OUTPUT = "structured_output"
    CROSS_CHECK = "cross_check"
    FALLBACK = "fallback"


class GraphOrchestrator:
    """
    LangGraph Pipeline voi Multi-Model Routing

    Kien truc 3 model:
      - Groq-0 (llama-3.3-70b-versatile): reasoning, chat, synthesis
      - Groq-1 (qwen/qwen3-32b): structured output, classification, cross-check
      - NVIDIA (minimaxai/minimax-m2.7): document/news analysis (instantiated on demand)
    """

    MODEL_TIERS = {
        "groq0": {
            "use_cases": [
                TaskType.REALTIME_SIGNAL,
                TaskType.QUICK_ANALYSIS,
                TaskType.CHATBOT,
            ],
            "model": "llama-3.3-70b-versatile",
            "priority": 1,
        },
        "groq1": {
            "use_cases": [
                TaskType.HEADLINE_CLASSIFICATION,
                TaskType.STRUCTURED_OUTPUT,
                TaskType.CROSS_CHECK,
            ],
            "model": "qwen/qwen3-32b",
            "priority": 2,
        },
        "nvidia": {
            "use_cases": [
                TaskType.DEEP_RESEARCH,
                TaskType.LONG_FORM_ANALYSIS,
            ],
            "model": "minimaxai/minimax-m2.7",
            "priority": 3,
        },
    }

    CONFIDENCE_THRESHOLDS = {
        "low": 0.6,
        "medium": 0.75,
        "high": 0.85,
    }

    def __init__(self):
        """Initialize Graph Orchestrator"""
        self.settings = get_settings()
        self.intent_router = IntentRouter()

        # Groq-0: reasoning / chat (llama-3.3-70b-versatile)
        self.groq0_client = GroqAgent(
            api_key=self.settings.llm_groq_key0,
            model=self.settings.llm_groq_model0,
            enable_fallback=self.settings.enable_fallback,
        )

        # Groq-1: structured output / classification (qwen/qwen3-32b)
        self.groq1_client = GroqAgent(
            api_key=self.settings.llm_groq_key1,
            model=self.settings.llm_groq_model1,
            enable_fallback=self.settings.enable_fallback,
        )

        # NVIDIA client created on-demand via _get_nvidia_client()

        # Map model types to client instances
        self.model_clients = {
            ModelType.GROQ0: self.groq0_client,
            ModelType.GROQ1: self.groq1_client,
        }

        logger.info("Graph Orchestrator initialized: Groq-0 (reasoning), Groq-1 (structured)")

    def _get_nvidia_client(self) -> GroqAgent:
        """Create NVIDIA client on demand (OpenAI-compatible API)."""
        from openai import OpenAI
        api_key = self.settings.llm_nvidia_key
        model = self.settings.llm_nvidia_model
        if not api_key:
            raise ValueError("NVIDIA API key not configured")
        # Use a lightweight wrapper for NVIDIA OpenAI-compatible endpoint
        from .base import BaseAgent, LLMError

        class NvidiaAgent(BaseAgent):
            INPUT_COST_PER_1M = 0.0
            OUTPUT_COST_PER_1M = 0.0

            def __init__(self, api_key, model, max_retries=3, timeout=120, enable_fallback=False):
                super().__init__(api_key, model, max_retries, timeout, enable_fallback)
                self.client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)

            async def _call_llm(self, prompt, **kwargs):
                try:
                    temperature = kwargs.get("temperature", 0.7)
                    max_tokens = kwargs.get("max_tokens", 2048)
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    content = response.choices[0].message.content
                    usage = response.usage
                    return {
                        "content": content,
                        "input_tokens": usage.prompt_tokens,
                        "output_tokens": usage.completion_tokens,
                        "total_tokens": usage.total_tokens,
                        "finish_reason": response.choices[0].finish_reason,
                        "model": self.model,
                    }
                except Exception as e:
                    raise LLMError(f"NVIDIA API error: {str(e)}")

            def _calculate_cost(self, response):
                return 0.0

        return NvidiaAgent(api_key=api_key, model=model)

    def _classify_task(self, node_name: str) -> TaskType:
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
        elif "structured" in node_lower or "json" in node_lower or "extract" in node_lower:
            return TaskType.STRUCTURED_OUTPUT
        elif "cross" in node_lower or "verify" in node_lower or "check" in node_lower:
            return TaskType.CROSS_CHECK
        else:
            return TaskType.QUICK_ANALYSIS

    def _select_model(self, task_type: TaskType) -> ModelType:
        for provider, config in self.MODEL_TIERS.items():
            if task_type in config["use_cases"]:
                return ModelType(provider)
        return ModelType.GROQ0

    async def _call_model(
        self,
        model: ModelType,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        if model == ModelType.NVIDIA:
            client = self._get_nvidia_client()
        else:
            client = self.model_clients.get(model)
        if not client:
            raise ValueError(f"Model client not found: {model}")

        response = await client.call_with_retry(prompt, **kwargs)
        response["confidence"] = self._calculate_confidence(response)
        return response

    def _calculate_confidence(self, response: Dict[str, Any]) -> float:
        return 0.0  # KHÔNG dùng confidence giả. Dùng CalibratedConfidence từ ml/calibration.py

    async def _parallel_execution(
        self,
        prompt: str,
        **kwargs
    ) -> Dict[str, Any]:
        logger.info("Starting parallel execution with Groq-0 and Groq-1")
        results = await asyncio.gather(
            self.groq0_client.call_with_retry(prompt, **kwargs),
            self.groq1_client.call_with_retry(prompt, **kwargs),
            return_exceptions=True,
        )
        valid_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Model {i} failed: {str(result)}")
            else:
                valid_results.append(result)
        if not valid_results:
            from .base import LLMError
            raise LLMError("All models failed in parallel execution")
        return self._consensus_vote(valid_results)

    def _consensus_vote(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        best_result = max(results, key=lambda r: r.get("confidence", 0))
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
        task_type = self._classify_task(node_name)
        model = self._select_model(task_type)

        decision_type = state.get("decision_type")
        if decision_type == "auto_trade" and self.settings.max_parallel_calls >= 2:
            logger.info(f"High-risk decision detected, using parallel Groq execution")
            prompt = self._state_to_prompt(state, node_name)
            return await self._parallel_execution(prompt, **kwargs)

        prompt = self._state_to_prompt(state, node_name)
        result = await self._call_model(model, prompt, **kwargs)

        confidence = result.get("confidence", 0)
        if confidence < self.CONFIDENCE_THRESHOLDS["low"] and self.settings.enable_fallback:
            logger.info(f"Low confidence ({confidence}), calling fallback model")
            fallback_model = self._get_fallback_model(model)
            fallback_result = await self._call_model(fallback_model, prompt, **kwargs)
            return self._merge_results(result, fallback_result)

        return result

    def _get_fallback_model(self, current_model: ModelType) -> ModelType:
        fallback_chain = {
            ModelType.GROQ0: ModelType.GROQ1,
            ModelType.GROQ1: ModelType.GROQ0,
            ModelType.NVIDIA: ModelType.GROQ0,
        }
        return fallback_chain.get(current_model, ModelType.GROQ0)

    def _merge_results(
        self,
        primary: Dict[str, Any],
        fallback: Dict[str, Any]
    ) -> Dict[str, Any]:
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
        return await self._call_model(model, user_input, **kwargs)

    async def execute_graph_pipeline(
        self,
        user_input: str,
        model: ModelType,
        **kwargs
    ) -> Dict[str, Any]:
        state = {
            "symbol": kwargs.get("symbol", ""),
            "context": user_input,
            "data": kwargs.get("data", {}),
            "decision_type": kwargs.get("decision_type", "normal"),
        }
        return await self.execute_node("analysis", state, **kwargs)

    def get_model_statistics(self) -> Dict[str, Any]:
        return {
            "groq0": self.groq0_client.get_statistics(),
            "groq1": self.groq1_client.get_statistics(),
        }


# Singleton instance
graph_orchestrator = GraphOrchestrator()
