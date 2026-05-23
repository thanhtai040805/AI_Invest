"""
BaseAgent - Base class for all LLM agents with retry logic and error handling
"""
import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from abc import ABC, abstractmethod
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
import time

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base exception for LLM-related errors"""
    pass


class RateLimitError(LLMError):
    """Rate limit exceeded"""
    pass


class APIError(LLMError):
    """API error"""
    pass


class BaseAgent(ABC):
    """
    Base class for all LLM agents with retry logic
    """
    
    def __init__(
        self,
        api_key: str,
        model: str,
        max_retries: int = 3,
        timeout: int = 60,
        enable_fallback: bool = True,
    ):
        """
        Initialize BaseAgent
        
        Args:
            api_key: API key for the LLM provider
            model: Model name to use
            max_retries: Maximum number of retry attempts
            timeout: Request timeout in seconds
            enable_fallback: Whether to enable fallback logic
        """
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout
        self.enable_fallback = enable_fallback
        
        # Statistics
        self.call_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.total_tokens = 0
        self.total_cost = 0.0
        
    @abstractmethod
    async def _call_llm(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Abstract method to call the LLM API
        Must be implemented by subclasses
        
        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        pass
    
    @abstractmethod
    def _calculate_cost(self, response: Dict[str, Any]) -> float:
        """
        Calculate cost of the API call
        Must be implemented by subclasses
        
        Args:
            response: Response from the LLM
            
        Returns:
            float: Cost in USD
        """
        pass
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, APIError)),
    )
    async def call_with_retry(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Call LLM with retry logic
        
        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        self.call_count += 1
        
        try:
            start_time = time.time()
            response = await asyncio.wait_for(
                self._call_llm(prompt, **kwargs),
                timeout=self.timeout
            )
            elapsed = time.time() - start_time
            
            # Update statistics
            self.success_count += 1
            cost = self._calculate_cost(response)
            self.total_cost += cost
            
            # Add metadata
            response["metadata"] = {
                "model": self.model,
                "elapsed_time": elapsed,
                "cost": cost,
                "attempt": 1,
            }
            
            logger.info(f"LLM call successful: {self.model}, elapsed: {elapsed:.2f}s, cost: ${cost:.6f}")
            return response
            
        except asyncio.TimeoutError:
            self.failure_count += 1
            logger.error(f"LLM call timeout: {self.model}")
            raise LLMError(f"Request timeout after {self.timeout}s")
            
        except Exception as e:
            self.failure_count += 1
            logger.error(f"LLM call failed: {self.model}, error: {str(e)}")
            
            # Classify error type
            if "rate limit" in str(e).lower():
                raise RateLimitError(f"Rate limit exceeded: {str(e)}")
            elif "api" in str(e).lower() or "http" in str(e).lower():
                raise APIError(f"API error: {str(e)}")
            else:
                raise LLMError(f"LLM call failed: {str(e)}")
    
    async def analyze(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Analyze prompt using the LLM
        
        Args:
            prompt: The prompt to analyze
            **kwargs: Additional parameters
            
        Returns:
            Dict containing analysis result and metadata
        """
        return await self.call_with_retry(prompt, **kwargs)
    
    async def chat(self, messages: list, **kwargs) -> Dict[str, Any]:
        """
        Chat with the LLM using message history
        
        Args:
            messages: List of message dictionaries
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        # Convert messages to prompt format
        prompt = self._messages_to_prompt(messages)
        return await self.call_with_retry(prompt, **kwargs)
    
    def _messages_to_prompt(self, messages: list) -> str:
        """
        Convert message list to prompt string
        
        Args:
            messages: List of message dictionaries
            
        Returns:
            str: Prompt string
        """
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")
        
        return "\n".join(prompt_parts)
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get usage statistics
        
        Returns:
            Dict containing statistics
        """
        success_rate = (
            self.success_count / self.call_count * 100
            if self.call_count > 0 else 0
        )
        
        return {
            "model": self.model,
            "call_count": self.call_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": f"{success_rate:.2f}%",
            "total_cost": f"${self.total_cost:.6f}",
            "avg_cost_per_call": f"${self.total_cost / self.call_count:.6f}" if self.call_count > 0 else "$0.00",
        }
    
    def reset_statistics(self):
        """Reset usage statistics"""
        self.call_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.total_tokens = 0
        self.total_cost = 0.0
