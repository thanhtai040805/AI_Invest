"""
OpenAI Client - OpenAI API integration for reasoning and synthesis
"""
import logging
from typing import Dict, Any, Optional
from openai import OpenAI
from .base import BaseAgent, LLMError, APIError, RateLimitError

logger = logging.getLogger(__name__)


class OpenAIAgent(BaseAgent):
    """
    OpenAI API client for stable reasoning and final synthesis
    Used as the final judge for decision-making and explanation generation
    """
    
    INPUT_COST_PER_1M = 2.50  # GPT-4 pricing
    OUTPUT_COST_PER_1M = 10.00
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        max_retries: int = 3,
        timeout: int = 60,
        enable_fallback: bool = True,
    ):
        """
        Initialize OpenAI Agent
        
        Args:
            api_key: OpenAI API key
            model: OpenAI model name (gpt-4o-mini for cost-effective, gpt-4o for quality)
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds
            enable_fallback: Enable fallback logic
        """
        super().__init__(api_key, model, max_retries, timeout, enable_fallback)
        
        # Initialize OpenAI client
        try:
            self.client = OpenAI(api_key=api_key)
            logger.info(f"OpenAI client initialized with model: {model}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {str(e)}")
            raise LLMError(f"OpenAI initialization failed: {str(e)}")
    
    async def _call_llm(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Call OpenAI API
        
        Args:
            prompt: The prompt to send
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            Dict containing response and metadata
        """
        try:
            # Extract parameters
            temperature = kwargs.get("temperature", 0.3)  # Lower temp for reasoning
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
                raise RateLimitError(f"OpenAI rate limit exceeded: {str(e)}")
            elif "api key" in error_msg or "authentication" in error_msg:
                raise LLMError(f"OpenAI authentication failed: {str(e)}")
            elif "timeout" in error_msg:
                raise LLMError(f"OpenAI request timeout: {str(e)}")
            elif "quota" in error_msg or "limit" in error_msg:
                raise LLMError(f"OpenAI quota exceeded: {str(e)}")
            else:
                raise APIError(f"OpenAI API error: {str(e)}")
    
    def _calculate_cost(self, response: Dict[str, Any]) -> float:
        """
        Calculate cost of the API call
        
        Args:
            response: Response from OpenAI
            
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
        Chat with OpenAI using message history
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        try:
            # Convert messages to OpenAI format
            openai_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                # Map role to OpenAI format
                if role == "user":
                    openai_messages.append({"role": "user", "content": content})
                elif role == "assistant":
                    openai_messages.append({"role": "assistant", "content": content})
                elif role == "system":
                    openai_messages.append({"role": "system", "content": content})
            
            # Extract parameters
            temperature = kwargs.get("temperature", 0.3)
            max_tokens = kwargs.get("max_tokens", 2048)
            
            # Make API call
            response = self.client.chat.completions.create(
                model=self.model,
                messages=openai_messages,
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
            raise APIError(f"OpenAI chat failed: {str(e)}")
    
    async def synthesize_decision(
        self,
        bull_case: str,
        bear_case: str,
        risk_factors: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Synthesize final trading decision from multiple perspectives
        
        Args:
            bull_case: Bullish arguments
            bear_case: Bearish arguments
            risk_factors: Risk factors
            **kwargs: Additional parameters
            
        Returns:
            Dict containing synthesized decision and metadata
        """
        prompt = f"""As a financial analyst, synthesize the following information into a final trading recommendation:

BULL CASE:
{bull_case}

BEAR CASE:
{bear_case}

RISK FACTORS:
{risk_factors}

Provide:
1. Final recommendation (BUY/SELL/HOLD)
2. Confidence level (0-100)
3. Key reasoning (2-3 sentences)
4. Primary risk to monitor
5. Suggested position size (as percentage of portfolio)

Format as structured JSON."""
        
        response = await self.call_with_retry(prompt, temperature=0.3, **kwargs)
        
        return {
            "content": response["content"],
            "metadata": response.get("metadata", {}),
        }
    
    async def judge_signals(
        self,
        signals: list,
        context: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Act as a judge to evaluate conflicting signals
        
        Args:
            signals: List of trading signals with sources
            context: Market context and additional information
            **kwargs: Additional parameters
            
        Returns:
            Dict containing judgment and metadata
        """
        prompt = f"""As an experienced trading judge, evaluate the following conflicting signals and provide a balanced judgment:

SIGNALS:
{chr(10).join([f"- {s}" for s in signals])}

CONTEXT:
{context}

Provide:
1. Overall assessment (BULLISH/BEARISH/NEUTRAL)
2. Weight of each signal (0-100)
3. Which signals to trust and why
4. What additional information is needed
5. Final action recommendation

Format as structured analysis."""
        
        response = await self.call_with_retry(prompt, temperature=0.2, **kwargs)
        
        return {
            "content": response["content"],
            "metadata": response.get("metadata", {}),
        }
