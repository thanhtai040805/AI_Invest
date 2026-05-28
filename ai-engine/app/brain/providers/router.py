"""
Intent Router - Phan loai intent va route den pipeline phu hop
"""
import re
from typing import Dict
from enum import Enum


class IntentType(str, Enum):
    CHAT = "CHAT"
    RESEARCH = "RESEARCH"
    SIGNAL = "SIGNAL"


class PipelineType(str, Enum):
    SIMPLE = "simple"
    GRAPH = "graph"


class ModelType(str, Enum):
    GROQ0 = "groq0"       # qwen/qwen3-32b (API key 0): reasoning, chat, synthesis
    GROQ1 = "groq1"       # qwen/qwen3-32b (API key 1): structured output, classification, cross-check
    NVIDIA = "nvidia"     # minimaxai/minimax-m2.7: document/news analysis


class IntentRouter:
    INTENT_PATTERNS = {
        IntentType.CHAT: [
            r"^(hi|hello|hey|xin chao|chao)",
            r"lam sao|lam gi|how to|how do",
            r"giai thich|cho toi biet|explain|tell me",
            r"ban la ai|who are you",
            r"giup toi|help me",
        ],
        IntentType.RESEARCH: [
            r"phan tich|danh gia|analyze|analysis",
            r"nen mua|nen ban|should i buy|should i sell",
            r"trien vong|tang truong|outlook|growth",
            r"bao cao|report|financial",
            r"ket qua kinh doanh|earnings",
        ],
        IntentType.SIGNAL: [
            r"tin hieu|signal",
            r"mua vao|ban ra|buy|sell",
            r"[A-Z]{4,6}",
            r"gia|price",
            r"khuyen nghi|recommendation",
        ],
    }

    def __init__(self):
        self._compile_patterns()

    def _compile_patterns(self):
        self.compiled_patterns = {}
        for intent, patterns in self.INTENT_PATTERNS.items():
            self.compiled_patterns[intent] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]

    def classify(self, user_input: str) -> IntentType:
        if not user_input or not user_input.strip():
            return IntentType.CHAT
        for intent, patterns in self.compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(user_input):
                    return intent
        return IntentType.CHAT

    def route(self, user_input: str) -> Dict:
        intent = self.classify(user_input)
        if intent == IntentType.CHAT:
            return {"pipeline": PipelineType.SIMPLE, "model": ModelType.GROQ0, "stream": False, "intent": intent}
        elif intent == IntentType.SIGNAL:
            return {"pipeline": PipelineType.GRAPH, "model": ModelType.GROQ1, "stream": True, "intent": intent}
        elif intent == IntentType.RESEARCH:
            return {"pipeline": PipelineType.GRAPH, "model": ModelType.GROQ0, "stream": True, "intent": intent}
        return {"pipeline": PipelineType.SIMPLE, "model": ModelType.GROQ0, "stream": False, "intent": IntentType.CHAT}

    def get_model_for_task(self, task_type: str) -> ModelType:
        task_model_mapping = {
            "realtime_signal": ModelType.GROQ0,
            "quick_analysis": ModelType.GROQ0,
            "headline_classification": ModelType.GROQ1,
            "deep_research": ModelType.NVIDIA,
            "chatbot": ModelType.GROQ0,
            "long_form_analysis": ModelType.NVIDIA,
            "structured_output": ModelType.GROQ1,
            "cross_check": ModelType.GROQ1,
        }
        return task_model_mapping.get(task_type, ModelType.GROQ0)


intent_router = IntentRouter()
