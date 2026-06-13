"""
Intent Router - Phan loai intent va route den pipeline phu hop

Two-layer classification:
  Layer 1 — Regex (fast, <1ms): return immediately if confidence > 0.90
  Layer 2 — TF-IDF + Logistic Regression (slower, ~10ms): ML classifier as fallback
"""
import re
from dataclasses import dataclass
from typing import Dict, Optional
from enum import Enum

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression


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


@dataclass
class IntentResult:
    intent: IntentType
    confidence: float
    method: str  # "regex" | "ml"
    clarification_needed: bool = False


# ── Labeled training examples for TF-IDF + Logistic Regression ──────────────
_TRAINING_EXAMPLES: list[tuple[str, IntentType]] = [
    # CHAT
    ("hi", IntentType.CHAT),
    ("hello", IntentType.CHAT),
    ("xin chao", IntentType.CHAT),
    ("chao ban", IntentType.CHAT),
    ("ban la ai", IntentType.CHAT),
    ("lam sao de bat dau", IntentType.CHAT),
    ("giup toi", IntentType.CHAT),
    ("cam on", IntentType.CHAT),
    ("toi can tro giup", IntentType.CHAT),
    ("how are you", IntentType.CHAT),
    ("what can you do", IntentType.CHAT),
    ("tell me about yourself", IntentType.CHAT),
    ("thoi tiet hom nay", IntentType.CHAT),
    ("ban khoe khong", IntentType.CHAT),
    ("toi muon tro chuyen", IntentType.CHAT),
    ("hello ban oi", IntentType.CHAT),
    # RESEARCH
    ("phan tich co phieu VCB", IntentType.RESEARCH),
    ("danh gia thi truong hom nay", IntentType.RESEARCH),
    ("phan tich nganh ngan hang", IntentType.RESEARCH),
    ("bao cao tai chinh FPT", IntentType.RESEARCH),
    ("trien vong thi truong 2026", IntentType.RESEARCH),
    ("phân tích kết quả kinh doanh quý 1", IntentType.RESEARCH),
    ("analyze VN index", IntentType.RESEARCH),
    ("outlook for steel sector", IntentType.RESEARCH),
    ("financial analysis of HPG", IntentType.RESEARCH),
    ("earnings report VNM", IntentType.RESEARCH),
    ("đánh giá cổ phiếu MWG", IntentType.RESEARCH),
    ("phan tich co ban SSI", IntentType.RESEARCH),
    ("nhan dinh thi truong", IntentType.RESEARCH),
    ("phan tich vi mo", IntentType.RESEARCH),
    ("danh gia nganh thep", IntentType.RESEARCH),
    ("báo cáo ngành bán lẻ", IntentType.RESEARCH),
    ("co phieu nao dang mua", IntentType.RESEARCH),
    ("thi truong chung khoan hom nay", IntentType.RESEARCH),
    ("phân tích kỹ thuật VIC", IntentType.RESEARCH),
    # SIGNAL
    ("tin hieu mua VCB", IntentType.SIGNAL),
    ("co nen mua HPG hom nay", IntentType.SIGNAL),
    ("gia co phieu FPT", IntentType.SIGNAL),
    ("khuyen nghi mua ban", IntentType.SIGNAL),
    ("buy VNM", IntentType.SIGNAL),
    ("sell HPG", IntentType.SIGNAL),
    ("signal for VCB", IntentType.SIGNAL),
    ("gia mua toc uu", IntentType.SIGNAL),
    ("lenh mua SSI", IntentType.SIGNAL),
    ("recommendation for MWG", IntentType.SIGNAL),
    ("tin hieu giao dich", IntentType.SIGNAL),
    ("mua vao VIC", IntentType.SIGNAL),
    ("ban ra VRE", IntentType.SIGNAL),
    ("bán ra cổ phiếu HPG", IntentType.SIGNAL),
    ("mua vào VNM", IntentType.SIGNAL),
    ("lenh ban SSI", IntentType.SIGNAL),
    ("gia tran VCB hom nay", IntentType.SIGNAL),
    ("BBH HPG", IntentType.SIGNAL),
    ("muc tieu gia FPT", IntentType.SIGNAL),
    ("can mua MWG", IntentType.SIGNAL),
    ("dang co tin hieu mua", IntentType.SIGNAL),
    ("co nen ban VIC", IntentType.SIGNAL),
    ("gia san HPG", IntentType.SIGNAL),
    ("VNM mua duoc khong", IntentType.SIGNAL),
]


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
            r"trien vong|tang truong|outlook|growth",
            r"bao cao|report|financial",
            r"ket qua kinh doanh|earnings",
        ],
        IntentType.SIGNAL: [
            r"tin hieu|signal",
            r"mua vao|ban ra|buy|sell",
            r"khuyen nghi|recommendation",
            r"nen mua|nen ban|should i buy|should i sell",
        ],
    }

    CONFIDENCE_THRESHOLD = 0.70

    def __init__(self):
        self._compile_patterns()
        self._ml_ready = False
        self._vectorizer: Optional[TfidfVectorizer] = None
        self._classifier: Optional[LogisticRegression] = None
        self._fit_ml_classifier()

    def _compile_patterns(self):
        self.compiled_patterns: dict[IntentType, list[re.Pattern]] = {}
        for intent, patterns in self.INTENT_PATTERNS.items():
            self.compiled_patterns[intent] = [
                re.compile(p, re.IGNORECASE) for p in patterns
            ]

    def _fit_ml_classifier(self):
        try:
            texts = [ex[0] for ex in _TRAINING_EXAMPLES]
            labels = [ex[1].value for ex in _TRAINING_EXAMPLES]
            self._vectorizer = TfidfVectorizer(
                analyzer="word",
                ngram_range=(1, 2),
                max_features=2000,
                lowercase=True,
            )
            X = self._vectorizer.fit_transform(texts)
            self._classifier = LogisticRegression(
                C=1.0, max_iter=500, random_state=42, solver="lbfgs"
            )
            self._classifier.fit(X, labels)
            self._ml_ready = True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to fit ML classifier: %s", e)
            self._ml_ready = False

    def _regex_confidence(self, user_input: str) -> tuple[Optional[IntentType], int]:
        """Run regex layer. Returns (intent, matched_pattern_count).

        Only returns a result if one intent strictly out-matches all others.
        Ties fall through to the ML layer.
        """
        counts: dict[IntentType, int] = {}
        for intent, patterns in self.compiled_patterns.items():
            count = sum(1 for p in patterns if p.search(user_input))
            if count > 0:
                counts[intent] = count
        if not counts:
            return None, 0
        sorted_intents = sorted(counts.items(), key=lambda x: (-x[1], x[0].value))
        if len(sorted_intents) == 1 or sorted_intents[0][1] > sorted_intents[1][1]:
            return sorted_intents[0]
        return None, 0

    def _ml_classify(self, user_input: str) -> Optional[tuple[IntentType, float]]:
        """Run ML layer. Returns (intent, probability)."""
        if not self._ml_ready or not self._vectorizer or not self._classifier:
            return None
        try:
            X = self._vectorizer.transform([user_input])
            proba = self._classifier.predict_proba(X)[0]
            max_idx = proba.argmax()
            max_proba = float(proba[max_idx])
            pred_intent = IntentType(self._classifier.classes_[max_idx])
            return pred_intent, max_proba
        except Exception:
            return None

    def classify(self, user_input: str) -> IntentResult:
        if not user_input or not user_input.strip():
            return IntentResult(intent=IntentType.CHAT, confidence=1.0, method="regex")

        # Layer 1 — Regex (fast path, <1ms)
        regex_intent, regex_count = self._regex_confidence(user_input)
        if regex_intent is not None and regex_count >= 1:
            conf = min(0.85 + 0.05 * (regex_count - 1), 0.99)
            return IntentResult(intent=regex_intent, confidence=conf, method="regex")

        # Layer 2 — ML (slower path, ~10ms) — fallback when regex matches nothing
        ml_result = self._ml_classify(user_input)
        if ml_result is not None:
            ml_intent, ml_confidence = ml_result
            if ml_confidence >= self.CONFIDENCE_THRESHOLD:
                return IntentResult(intent=ml_intent, confidence=round(ml_confidence, 3), method="ml")
            return IntentResult(
                intent=ml_intent,
                confidence=round(ml_confidence, 3),
                method="ml",
                clarification_needed=True,
            )

        # Fallback: default to CHAT
        return IntentResult(intent=IntentType.CHAT, confidence=0.50, method="regex", clarification_needed=True)

    def route(self, user_input: str) -> Dict:
        result = self.classify(user_input)
        intent = result.intent
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
