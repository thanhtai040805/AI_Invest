"""
Groq Client - Groq API integration for fast inference
"""
import logging
from typing import Dict, Any, Optional
from groq import Groq
from .base import BaseAgent, LLMError, APIError, RateLimitError

logger = logging.getLogger(__name__)


class GroqAgent(BaseAgent):
    """
    Groq API client for fast inference and real-time signals
    """
    
    INPUT_COST_PER_1M = 0.05
    OUTPUT_COST_PER_1M = 0.08
    
    def __init__(
        self,
        api_key: str,
        model: str = "llama3-70b-8192",
        max_retries: int = 3,
        timeout: int = 60,
        enable_fallback: bool = True,
    ):
        """
        Initialize Groq Agent
        
        Args:
            api_key: Groq API key
            model: Groq model name
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds
            enable_fallback: Enable fallback logic
        """
        super().__init__(api_key, model, max_retries, timeout, enable_fallback)
        
        # Initialize Groq client
        try:
            self.client = Groq(api_key=api_key)
            logger.info(f"Groq client initialized with model: {model}")
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {str(e)}")
            raise LLMError(f"Groq initialization failed: {str(e)}")
    
    async def _call_llm(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Call Groq API
        
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
            }
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if "rate limit" in error_msg or "429" in error_msg:
                raise RateLimitError(f"Groq rate limit exceeded: {str(e)}")
            elif "api key" in error_msg or "authentication" in error_msg:
                raise LLMError(f"Groq authentication failed: {str(e)}")
            elif "timeout" in error_msg:
                raise LLMError(f"Groq request timeout: {str(e)}")
            else:
                raise APIError(f"Groq API error: {str(e)}")
    
    def _calculate_cost(self, response: Dict[str, Any]) -> float:
        """
        Calculate cost of the API call
        
        Args:
            response: Response from Groq
            
        Returns:
            float: Cost in USD
        """
        input_tokens = response.get("input_tokens", 0)
        output_tokens = response.get("output_tokens", 0)
        
        input_cost = (input_tokens / 1_000_000) * self.INPUT_COST_PER_1M
        output_cost = (output_tokens / 1_000_000) * self.OUTPUT_COST_PER_1M
        
        return input_cost + output_cost
    
    async def chat(self, messages: list, **kwargs) -> Dict[str, Any]:
        """
        Chat with Groq using message history
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        try:
            # Convert messages to Groq format
            groq_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                # Map role to Groq format
                if role == "user":
                    groq_messages.append({"role": "user", "content": content})
                elif role == "assistant":
                    groq_messages.append({"role": "assistant", "content": content})
                elif role == "system":
                    groq_messages.append({"role": "system", "content": content})
            
            # Extract parameters
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens", 2048)
            
            # Make API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=groq_messages,
                temperature=temperature,
                max_tokens=max_tokens,
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
            }
            
        except Exception as e:
            raise APIError(f"Groq chat failed: {str(e)}")
    
    async def realtime_signal(self, symbol: str, data: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """
        Generate real-time trading signal for a symbol
        
        Args:
            symbol: Stock symbol
            data: Market data (price, volume, indicators)
            **kwargs: Additional parameters
            
        Returns:
            Dict containing signal and metadata
        """
        # Construct prompt for signal generation
        prompt = f"""Analyze the following market data for {symbol} and provide a trading signal:

Current Price: {data.get('price', 'N/A')}
Volume: {data.get('volume', 'N/A')}
Change: {data.get('change', 'N/A')}%
RSI: {data.get('rsi', 'N/A')}
MACD: {data.get('macd', 'N/A')}

Provide:
1. Signal (BUY/SELL/HOLD)
2. Confidence score (0-100)
3. Brief reasoning (1-2 sentences)
4. Risk level (LOW/MEDIUM/HIGH)

Format as JSON."""
        
        response = await self.call_with_retry(prompt, temperature=0.3, **kwargs)
        
        return {
            "content": response["content"],
            "symbol": symbol,
            "metadata": response.get("metadata", {}),
        }
