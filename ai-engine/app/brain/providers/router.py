"""
Intent Router - Phân loại intent và route đến pipeline phù hợp
"""
import re
from typing import Dict, Literal
from enum import Enum


class IntentType(str, Enum):
    """Các loại intent được hỗ trợ"""
    CHAT = "CHAT"
    RESEARCH = "RESEARCH"
    SIGNAL = "SIGNAL"


class PipelineType(str, Enum):
    """Các loại pipeline"""
    SIMPLE = "simple"
    GRAPH = "graph"


class ModelType(str, Enum):
    """Các loại model"""
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"


class IntentRouter:
    """Phân loại intent và route đến pipeline phù hợp"""
    
    INTENT_PATTERNS = {
        IntentType.CHAT: [
            r"^(hi|hello|hey|xin chào|chào)",
            r"làm sao|làm gì|how to|how do",
            r"giải thích|cho tôi biết|explain|tell me",
            r"bạn là ai|who are you",
            r"giúp tôi|help me",
        ],
        IntentType.RESEARCH: [
            r"phân tích|đánh giá|analyze|analysis",
            r"nên mua|nên bán|should i buy|should i sell",
            r"triển vọng|tăng trưởng|outlook|growth",
            r"báo cáo|report|financial",
            r"kết quả kinh doanh|earnings",
        ],
        IntentType.SIGNAL: [
            r"tín hiệu|signal",
            r"mua vào|bán ra|buy|sell",
            r"[A-Z]{4,6}",  # Stock symbol pattern
            r"giá|price",
            r"khuyến nghị|recommendation",
        ],
    }
    
    def __init__(self):
        """Initialize Intent Router"""
        self._compile_patterns()
    
    def _compile_patterns(self):
        """Pre-compile regex patterns for performance"""
        self.compiled_patterns = {}
        for intent, patterns in self.INTENT_PATTERNS.items():
            self.compiled_patterns[intent] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]
    
    def classify(self, user_input: str) -> IntentType:
        """
        Phân loại intent từ input của user
        
        Args:
            user_input: Input string từ user
            
        Returns:
            IntentType: Loại intent được phân loại
        """
        if not user_input or not user_input.strip():
            return IntentType.CHAT
        
        # Check each intent pattern
        for intent, patterns in self.compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(user_input):
                    return intent
        
        # Default to CHAT if no pattern matches
        return IntentType.CHAT
    
    def route(self, user_input: str) -> Dict:
        """
        Route input đến pipeline và model phù hợp
        
        Args:
            user_input: Input string từ user
            
        Returns:
            Dict: Routing configuration với keys:
                - pipeline: "simple" hoặc "graph"
                - model: "gemini", "groq", hoặc "openrouter"
                - stream: boolean
                - intent: IntentType
        """
        intent = self.classify(user_input)
        
        if intent == IntentType.CHAT:
            return {
                "pipeline": PipelineType.SIMPLE,
                "model": ModelType.GEMINI,
                "stream": False,
                "intent": intent,
            }
        elif intent in [IntentType.RESEARCH, IntentType.SIGNAL]:
            return {
                "pipeline": PipelineType.GRAPH,
                "model": ModelType.GROQ,
                "stream": True,
                "intent": intent,
            }
        
        # Default fallback
        return {
            "pipeline": PipelineType.SIMPLE,
            "model": ModelType.GEMINI,
            "stream": False,
            "intent": IntentType.CHAT,
        }
    
    def get_model_for_task(self, task_type: str) -> ModelType:
        """
        Get appropriate model for specific task type
        
        Args:
            task_type: Type of task (e.g., "realtime_signal", "deep_research")
            
        Returns:
            ModelType: Appropriate model for the task
        """
        task_model_mapping = {
            "realtime_signal": ModelType.GROQ,
            "quick_analysis": ModelType.GROQ,
            "headline_classification": ModelType.GROQ,
            "deep_research": ModelType.GEMINI,
            "chatbot": ModelType.GEMINI,
            "long_form_analysis": ModelType.GEMINI,
            "fallback": ModelType.OPENROUTER,
            "batch": ModelType.OPENROUTER,
            "experimentation": ModelType.OPENROUTER,
        }
        
        return task_model_mapping.get(task_type, ModelType.GEMINI)


# Singleton instance
intent_router = IntentRouter()
