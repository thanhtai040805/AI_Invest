"""
Gemini Client - Google Gemini API integration
"""
import logging
from typing import Dict, Any, Optional
from google import genai
from google.genai import types
from .base import BaseAgent, LLMError, APIError

logger = logging.getLogger(__name__)


class GeminiAgent(BaseAgent):
    """
    Gemini API client for deep analysis and Vietnamese language support
    """
    
    INPUT_COST_PER_1M = 0.075
    OUTPUT_COST_PER_1M = 0.30
    
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
        max_retries: int = 3,
        timeout: int = 60,
        enable_fallback: bool = True,
    ):
        """
        Initialize Gemini Agent
        
        Args:
            api_key: Google API key
            model: Gemini model name
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds
            enable_fallback: Enable fallback logic
        """
        super().__init__(api_key, model, max_retries, timeout, enable_fallback)
        
        # Initialize Gemini client
        try:
            genai.configure(api_key=api_key)
            self.client = genai.GenerativeModel(model)
            logger.info(f"Gemini client initialized with model: {model}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {str(e)}")
            raise LLMError(f"Gemini initialization failed: {str(e)}")
    
    async def _call_llm(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Call Gemini API
        
        Args:
            prompt: The prompt to send
            **kwargs: Additional parameters (temperature, max_tokens, etc.)
            
        Returns:
            Dict containing response and metadata
        """
        try:
            # Extract parameters
            temperature = kwargs.get("temperature", 0.7)
            max_output_tokens = kwargs.get("max_tokens", 2048)
            
            # Configure generation config
            generation_config = types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            
            # Generate content
            response = self.client.generate_content(
                prompt,
                generation_config=generation_config,
            )
            
            # Extract response
            text = response.text
            usage_metadata = getattr(response, "usage_metadata", None)
            
            # Calculate tokens
            input_tokens = getattr(usage_metadata, "prompt_token_count", 0) if usage_metadata else 0
            output_tokens = getattr(usage_metadata, "candidates_token_count", 0) if usage_metadata else 0
            
            return {
                "content": text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "finish_reason": getattr(response, "finish_reason", "stop"),
                "model": self.model,
            }
            
        except Exception as e:
            error_msg = str(e).lower()
            
            if "quota" in error_msg or "limit" in error_msg:
                raise APIError(f"Gemini quota exceeded: {str(e)}")
            elif "api key" in error_msg or "authentication" in error_msg:
                raise LLMError(f"Gemini authentication failed: {str(e)}")
            else:
                raise APIError(f"Gemini API error: {str(e)}")
    
    def _calculate_cost(self, response: Dict[str, Any]) -> float:
        """
        Calculate cost of the API call
        
        Args:
            response: Response from Gemini
            
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
        Chat with Gemini using message history
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        try:
            # Convert messages to Gemini format
            gemini_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                # Map role to Gemini format
                if role == "user":
                    gemini_messages.append(types.HumanPart(content))
                elif role == "assistant":
                    gemini_messages.append(types.ModelPart(content))
                elif role == "system":
                    gemini_messages.append(types.HumanPart(f"System: {content}"))
            
            # Generate content with history
            temperature = kwargs.get("temperature", 0.7)
            max_output_tokens = kwargs.get("max_tokens", 2048)
            
            generation_config = types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            
            response = self.client.generate_content(
                gemini_messages,
                generation_config=generation_config,
            )
            
            # Extract response
            text = response.text
            usage_metadata = getattr(response, "usage_metadata", None)
            
            input_tokens = getattr(usage_metadata, "prompt_token_count", 0) if usage_metadata else 0
            output_tokens = getattr(usage_metadata, "candidates_token_count", 0) if usage_metadata else 0
            
            return {
                "content": text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "finish_reason": getattr(response, "finish_reason", "stop"),
                "model": self.model,
            }
            
        except Exception as e:
            raise APIError(f"Gemini chat failed: {str(e)}")
    
    async def analyze_vietnamese(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Analyze Vietnamese text with Gemini (optimized for Vietnamese)
        
        Args:
            prompt: Vietnamese prompt
            **kwargs: Additional parameters
            
        Returns:
            Dict containing response and metadata
        """
        # Add Vietnamese-specific system instruction
        system_instruction = "Bạn là một chuyên gia phân tích thị trường chứng khoán Việt Nam. Hãy trả lời bằng tiếng Việt."
        
        full_prompt = f"{system_instruction}\n\n{prompt}"
        
        return await self.call_with_retry(full_prompt, **kwargs)
