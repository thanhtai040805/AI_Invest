"""
OpenRouter Client - OpenRouter API integration for fallback and experimentation
"""
import logging
from typing import Dict, Any, Optional
from openai import OpenAI
from .base import BaseAgent, LLMError, APIError, RateLimitError

logger = logging.getLogger(__name__)


class OpenRouterAgent(BaseAgent):
    """
    OpenRouter API client for fallback and model experimentation
    """
    
    # Pricing (varies by model, using Claude 3.5 Sonnet as baseline)
    INPUT_COST_PER_1M = 3.0  # $3.00 per 1M input tokens (Claude 3.5 Sonnet)
    OUTPUT_COST_PER_1M = 15.0  # $15.00 per 1M output tokens (Claude 3.5 Sonnet)
    
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek/deepseek-v4-flash:free",
        max_retries: int = 3,
        timeout: int = 60,
        enable_fallback: bool = True,
    ):
        """
        Initialize OpenRouter Agent
        
        Args:
            api_key: OpenRouter API key
            model: Model identifier (e.g., "anthropic/claude-3.5-sonnet")
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds
            enable_fallback: Enable fallback logic
        """
        super().__init__(api_key, model, max_retries, timeout, enable_fallback)
        
        # Initialize OpenRouter client (using OpenAI-compatible API)
        try:
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=api_key,
            )
            logger.info(f"OpenRouter client initialized with model: {model}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenRouter client: {str(e)}")
            raise LLMError(f"OpenRouter initialization failed: {str(e)}")
    
    async def _call_llm(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Call OpenRouter API
        
        Args:
            prompt: The prompt to send
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            Dict containing response and metadata
        """
        try:
            # Extract parameters
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens", 2048)
            
            # Make API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://aiinvest.vn",  # Your app URL
                    "X-Title": "AIInvest Trading System",
                },
            )
            
            # Extract response
            content = response.choices[0].message.content
            usage = response.usage
            
            return {
                "content": content,
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "finish_reason": response.choices[0].finish_reason,
                "model": self.model,
                "provider": getattr(response, "model", "").split("/")[0] if "/" in str(getattr(response, "model", "")) else "unknown",
            }
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if "rate limit" in error_msg or "429" in error_msg:
                raise RateLimitError(f"OpenRouter rate limit exceeded: {str(e)}")
            elif "api key" in error_msg or "authentication" in error_msg:
                raise LLMError(f"OpenRouter authentication failed: {str(e)}")
            elif "timeout" in error_msg:
                raise LLMError(f"OpenRouter request timeout: {str(e)}")
            elif "credit" in error_msg or "balance" in error_msg:
                raise LLMError(f"OpenRouter insufficient credits: {str(e)}")
            else:
                raise APIError(f"OpenRouter API error: {str(e)}")
    
    def _calculate_cost(self, response: Dict[str, Any]) -> float:
        """
        Calculate cost of the API call
        Note: Actual cost varies by model, this is an estimate
        
        Args:
            response: Response from OpenRouter
            
        Returns:
            float: Cost in USD (estimated)
        """
        input_tokens = response.get("input_tokens", 0)
        output_tokens = response.get("output_tokens", 0)
        
        # Use baseline pricing (Claude 3.5 Sonnet)
        input_cost = (input_tokens / 1_000_000) * self.INPUT_COST_PER_1M
        output_cost = (output_tokens / 1_000_000) * self.OUTPUT_COST_PER_1M
        
        return input_cost + output_cost
    
    async def chat(self, messages: list, **kwargs) -> Dict[str, Any]:
        """
        Chat with OpenRouter using message history
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        try:
            # Convert messages to OpenRouter format
            openrouter_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                # Map role to OpenRouter format
                if role == "user":
                    openrouter_messages.append({"role": "user", "content": content})
                elif role == "assistant":
                    openrouter_messages.append({"role": "assistant", "content": content})
                elif role == "system":
                    openrouter_messages.append({"role": "system", "content": content})
            
            # Extract parameters
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens", 2048)
            
            # Make API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=openrouter_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://aiinvest.vn",
                    "X-Title": "AIInvest Trading System",
                },
            )
            
            # Extract response
            content = response.choices[0].message.content
            usage = response.usage
            
            return {
                "content": content,
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "finish_reason": response.choices[0].finish_reason,
                "model": self.model,
                "provider": getattr(response, "model", "").split("/")[0] if "/" in str(getattr(response, "model", "")) else "unknown",
            }
            
        except Exception as e:
            raise APIError(f"OpenRouter chat failed: {str(e)}")
    
    async def test_model(self, model: str, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Test a different model without changing the default
        
        Args:
            model: Model identifier to test
            prompt: Test prompt
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        try:
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens", 2048)
            
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={
                    "HTTP-Referer": "https://aiinvest.vn",
                    "X-Title": "AIInvest Trading System",
                },
            )
            
            content = response.choices[0].message.content
            usage = response.usage
            
            return {
                "content": content,
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "finish_reason": response.choices[0].finish_reason,
                "model": model,
            }
            
        except Exception as e:
            raise APIError(f"OpenRouter model test failed: {str(e)}")
