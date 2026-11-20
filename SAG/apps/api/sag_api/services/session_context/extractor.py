"""Zero-token Vietnamese & technical entity, sentence, and epistemic state extractor.

Built directly on top of `sag_api.services.knowledge_service.extract_e0_e1` and
`sag_api.services.search_index_service.COMMON_STOPWORDS` on `main` (0 local ML models).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from sag_api.services.knowledge_service import (
    KnowledgeConfig,
    extract_e0_e1,
)
from sag_api.services.search_index_service import COMMON_STOPWORDS
from sag_api.services.session_context.models import EntityMention, EpistemicStatus

_UPPER_CHAR = (
    r"A-ZÀ-ÖØ-ÞĀĂĄĆĈĊČĎĐĒĔĖĘĚĜĞĠĢĤĦĨĪĬĮİĲĴĶĹĻĽĿŁŃŅŇŊŌŎŐŒŔŖŘŚŜŞŠŢŤŦ"
    r"ŨŪŬŮŰŲŴŶŸŹŻŽƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ"
)
_LOWER_CHAR = (
    r"a-zß-öø-ÿāăąćĉċčďđēĕėęěĝğġģĥħĩīĭįıĳĵķĸĺļľŀłńņňŉŋōŏőœŕŗřśŝşšţťŧ"
    r"ũūŭůűųŵŷźżžơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
)
_WORD_CONT = _UPPER_CHAR + _LOWER_CHAR + r"0-9_-"
_SINGLE_WORD_STOP_ENTITIES = frozenset(
    {
        "the", "this", "that", "these", "those", "there", "their", "they", "them", "then",
        "when", "where", "which", "while", "what", "who", "whom", "whose", "why", "how",
        "here", "have", "has", "had", "will", "would", "could", "should", "must", "shall",
        "may", "might", "can", "each", "every", "some", "many", "most", "more", "other",
        "another", "such", "only", "also", "even", "still", "both", "either", "neither",
        "after", "before", "during", "since", "until", "under", "over", "between", "through",
        "within", "without", "against", "among", "around", "because", "although", "though",
        "however", "therefore", "moreover", "furthermore", "meanwhile", "otherwise", "instead",
        "ngoài", "trong", "trên", "dưới", "giữa", "trước", "sau", "khi", "nếu", "vì", "do",
        "để", "với", "từ", "về", "theo", "tại", "các", "những", "một", "mỗi", "mọi", "nhiều",
        "nhưng", "hoặc", "cùng", "cũng", "được", "không", "chỉ", "còn", "đang", "đã", "sẽ",
        "này", "đó", "đây", "ấy", "ông", "bà", "cô", "anh", "chị", "họ", "nó", "tôi", "chúng",
        "phim", "bộ", "cuộc", "chiếc", "người", "năm", "tháng", "ngày", "quý", "mùa", "tập",
        "phần", "loạt", "chương", "điều", "khoản", "mục", "luật", "nghị", "thông", "văn",
    }
)
_SENTENCE_SPLIT_RE = re.compile(r"(?:[^.!?\n]|\d+\.\d+)+(?:[.!?](?!\d)|$)")

_HONORIFIC_WORDS = (
    "anh|chị|em|bạn|thầy|cô|sếp|ông|bà|team|nhóm|bên|mảng|module|service|dịch vụ|hệ thống"
)
_ROLE_QUALIFIERS = "FE|BE|QA|QC|PM|PO|BA|AI|ML|DB|DevOps|Infra|Auth|Payment|Core|Web|Mobile|Marketing|Sale|HR"
_HONORIFIC_ENTITY_RE = re.compile(
    rf"(?i)\b({_HONORIFIC_WORDS})[ \t]+([{_UPPER_CHAR}{_LOWER_CHAR}][{_WORD_CONT}]{{1,24}}"
    rf"(?:[ \t]+(?:{_ROLE_QUALIFIERS}|[{_UPPER_CHAR}][{_WORD_CONT}]{{1,24}}))?)\b"
)
_HONORIFIC_STOP_TARGETS = frozenset(
    {
        "ấy", "đó", "này", "kia", "nào", "gì", "ai", "mình", "ta", "tôi", "em", "anh", "chị",
        "bạn", "họ", "nữa", "vừa", "đang", "đã", "sẽ", "cần", "phải", "xem", "làm", "giúp",
        "nhận", "chốt", "bảo", "nói", "hỏi", "gửi", "check", "review", "fix", "test",
    }
)
_TECH_ANCHOR_RE = re.compile(
    r"(?i)\b(?:"
    r"postgres(?:ql)?(?:\s+\d+)?(?:\s+listen/notify)?|listen/notify|mongodb|mongo|cockroachdb|"
    r"mysql|mariadb|sqlite|supabase|dynamodb|"
    r"redis(?:\s+streams|\s+cache)?|kafka|rabbitmq|celery|bullmq|nats|"
    r"qdrant|milvus|pgvector|pinecone|weaviate|faiss|elasticsearch|opensearch|"
    r"siliconflow|kev-4b|bge-m3|hipporag|leiden|mineru|fastapi|next\.?js|nestjs|prisma|sqlalchemy|"
    r"react(?:\.js|js)?|vue(?:\.js|js)?|angular|svelte|redux|zustand|tailwind|"
    r"docker|kubernetes|k8s|keycloak|oauth2?|jwt|sso|"
    r"(?:adr|rfc|pr|issue|task|bug|err)[-_ #]?\d+|"
    r"[a-z_][a-z0-9_]{2,}_[a-z0-9_]+|"
    r"[\w./-]+?\.(?:py|ts|tsx|sql|yaml|yml|json|md)"
    r")\b"
)

_CANONICAL_TECH_SYNONYMS: dict[str, str] = {
    "postgres": "postgresql",
    "postgresql 16": "postgresql",
    "postgresql 18": "postgresql",
    "mongo": "mongodb",
    "k8s": "kubernetes",
    "nextjs": "next.js",
    "reactjs": "react",
    "react.js": "react",
    "vuejs": "vue",
    "vue.js": "vue",
}

_CLAUSE_SPLIT_RE = re.compile(
    r"[,;:\n]|(?i:\b(?:nhưng|mà|tuy\s+nhiên|thay\s+vào\s+đó|ngược\s+lại|however|but|instead)\b)"
)
_PRE_CONFIRM_GUARD_RE = re.compile(
    r"(?i)\b(?:"
    r"không|chưa|chẳng|chả|đừng|chớ|khoan|tạm\s+thời\s+chưa|chưa\s+hề|"
    r"đang\s+cân\s+nhắc|cân\s+nhắc|phân\s+vân|thử|đề\s+xuất|gợi\s+ý|dự\s+kiến|có\s+thể|hay\s+là|"
    r"not|never|don't|do\s+not|didn't|did\s+not|haven't|hasn't|cannot|can't|considering|might|propose"
    r")\b"
)
_POST_CONFIRM_NEG_RE = re.compile(
    r"(?i)^\s*(?:không|chưa|bỏ|hủy|loại\s+bỏ|ngừng|từ\s+bỏ|not|never|drop|reject)\b"
)

_CONFIRMED_CUE_RE = re.compile(
    r"(?i)\b(?:"
    r"chốt(?:\s+dùng|\s+chọn|\s+phương\s+án|\s+lấy|\s+giao|\s+nhé|\s+luôn)?|"
    r"quyết\s+định\s+(?:chọn|dùng|lấy|giữ|chuyển\s+sang|quay\s+lại)|"
    r"thống\s+nhất|"
    r"chuyển\s+(?:[\wÀ-ỹ]+\s+){0,4}sang\s+(?:dùng\s+)?|"
    r"đổi\s+(?:[\wÀ-ỹ]+\s+){0,3}từ\s+[\wÀ-ỹ0-9._-]+\s+sang\b|"
    r"thay\s+[\wÀ-ỹ0-9._-]+\s+bằng\b|"
    r"đồng\s+ý\s+(?:chọn|dùng|phương\s+án|hướng|với)|"
    r"quay\s+lại\s+dùng|chuyển\s+hẳn\s+sang|vẫn\s+(?:giữ|dùng)\s+(?:nguyên\s+)?|"
    r"đã\s+duyệt|nhận\s+phụ\s+trách|sẽ\s+phụ\s+trách|đảm\s+nhiệm\s+phần|"
    r"finalized|decided\s+to\s+use|agreed\s+(?:on|to\s+use)|we\s+chose|reverted\s+back\s+to|"
    r"switched\s+to|replaced\s+[\w0-9._-]+\s+with"
    r")\b"
)
_CONSIDERING_CUE_RE = re.compile(
    r"(?i)\b(?:"
    r"đang\s+cân\s+nhắc|cân\s+nhắc\s+(?:thêm|dùng|giữa)|đang\s+nghiêng\s+về|"
    r"phân\s+vân|chưa\s+chốt|chưa\s+thống\s+nhất|chưa\s+quyết\s+định|tạm\s+thời\s+nghĩ|"
    r"considering|evaluating|leaning\s+toward|not\s+finalized"
    r")\b"
)
_PROPOSED_CUE_RE = re.compile(
    r"(?i)\b(?:"
    r"đề\s+xuất|gợi\s+ý|thử\s+(?:dùng|phương\s+án|nghiệm|chuyển\s+sang)|"
    r"có\s+thể\s+(?:dùng|chọn|thử)|hay\s+là\s+(?:dùng|mình\s+thử)|dự\s+kiến\s+dùng|"
    r"propose|suggest(?:ed)?|might\s+use|could\s+try"
    r")\b"
)
_NEGATED_CUE_RE = re.compile(
    r"(?i)\b(?:"
    r"bỏ\s+phương\s+án|loại\s+bỏ|không\s+(?:dùng|chọn|lấy)\b.*\bnữa|"
    r"không\s+(?:thống\s+nhất|đồng\s+ý|chốt|duyệt)|bác\s+bỏ|"
    r"hủy\s+phương\s+án|từ\s+bỏ|dropped|rejected|ruled\s+out|do\s+not\s+use\s+anymore"
    r")\b"
)
_ISSUE_CUE_RE = re.compile(
    r"(?i)\b(?:"
    r"đang\s+bị\s+lỗi|gặp\s+lỗi|mất\s+thứ\s+tự|quá\s+tải|tốn\s+ram|bị\s+timeout|"
    r"còn\s+vướng|chưa\s+xử\s+lý\s+xong|blocker|regression|out\s+of\s+memory|deadlock"
    r")\b"
)
_ANAPHORIC_SHORT_RE = re.compile(
    r"(?i)\b(?:"
    r"phương\s+án\s+(?:\d+|đó|này|trên|vừa\s+nêu|đầu|hai|cuối)|"
    r"hướng\s+(?:đó|này|trên)|cái\s+(?:đó|này|thứ\s+hai|đầu)|như\s+(?:anh|chị|bạn)\s+\w+\s+vừa|"
    r"đồng\s+ý\s+(?:nhé|vậy\s+nhé|luôn)|chốt\s+vậy\s+nhé|theo\s+ý\s+trên|"
    r"option\s+\d+|that\s+option|as\s+proposed\s+above|agreed\s+on\s+that"
    r")\b"
)

_EXPLICIT_REPLACED_SPAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:thay\s+cho|thay\s+vì|thay\s+thế(?:\s+cho)?|instead\s+of|rather\s+than)\s+([^,.;:\n]{1,48})"),
    re.compile(r"(?i)\b(?:thay|replace(?:d)?)\s+([^,.;:\n]{1,40}?)\s+(?:bằng|with)\b"),
    re.compile(
        r"(?i)\b(?:chuyển|đổi|migrate|switch(?:ed)?)\s+(?:[\wÀ-ỹ]+\s+){0,3}?từ\s+([^,.;:\n]{1,40}?)\s+(?:sang|qua|to)\b"
    ),
    re.compile(r"(?i)\bkhông\s+(?:dùng|chọn|lấy|xài)\s+([^,.;:\n]{1,40}?)\s+nữa\b"),
    re.compile(
        r"(?i)\b(?:bỏ(?:\s+phương\s+án|\s+hướng)?|loại\s+bỏ|hủy(?:\s+phương\s+án)?|từ\s+bỏ|dropped)\s+([^,.;:\n]{1,44})"
    ),
)

_SLOT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "slot:messaging_queue",
        re.compile(
            r"(?i)\b(?:listen/notify|redis\s+streams|kafka|rabbitmq|bullmq|celery|nats|"
            r"message\s+broker|event\s+bus|hàng\s+đợi|queue|stream\s+events?)\b"
        ),
    ),
    (
        "slot:vector_store",
        re.compile(
            r"(?i)\b(?:qdrant|milvus|pgvector|pinecone|weaviate|faiss|vector\s+(?:db|store|database)|kho\s+vector)\b"
        ),
    ),
    (
        "slot:cache",
        re.compile(r"(?i)\b(?:redis\s+cache|memcached|bộ\s+nhớ\s+đệm|caching\s+layer)\b"),
    ),
    (
        "slot:database",
        re.compile(
            r"(?i)\b(?:database|cơ\s+sở\s+dữ\s+liệu|csdl|\bdb\b|postgresql|postgres|mongodb|mongo|"
            r"cockroachdb|mysql|mariadb|sqlite|supabase|dynamodb)\b"
        ),
    ),
    (
        "slot:auth",
        re.compile(r"(?i)\b(?:auth(?:entication|orization)?|xác\s+thực|phân\s+quyền|sso|keycloak|jwt|oauth2?|iam)\b"),
    ),
    (
        "slot:budget",
        re.compile(r"(?i)\b(?:ngân\s+sách|kinh\s+phí|chi\s+phí\s+tối\s+đa|hạn\s+mức|budget|cost\s+cap)\b"),
    ),
    (
        "slot:deployment",
        re.compile(r"(?i)\b(?:deploy(?:ment)?|triển\s+khai|hạ\s+tầng|kubernetes|k8s|docker\s+compose|ci/cd)\b"),
    ),
)
_OWNER_SLOT_RE = re.compile(
    rf"(?i)\b(?:phụ\s+trách|nhận\s+làm|đảm\s+nhiệm|giao\s+cho\s+[\wÀ-ỹ\s]+?\s+phần|owner\s+of)[ \t]+"
    rf"((?:phần|mảng|module|gói|dịch\s+vụ)[ \t]+[{_UPPER_CHAR}{_LOWER_CHAR}0-9_-]+"
    rf"|[{_UPPER_CHAR}{_LOWER_CHAR}0-9_-]{{3,24}})"
)


@dataclass
class _TurnBlockAdapter:
    """Minimal duck-typed adapter matching CanonicalBlock attributes read by extract_e0_e1."""

    id: str
    ordinal: int
    normalized_text: str
    source_anchor: str
    block_type: str = "paragraph"
    section_path: str = ""


def normalize_session_text(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def normalize_entity_key(label: str) -> str:
    cleaned = " ".join(normalize_session_text(label).casefold().split())
    cleaned = re.sub(
        r"^(pr|adr|rfc|issue|task|bug)\s*#?\s*(\d+)$",
        r"\1-\2",
        cleaned,
    )
    cleaned = _CANONICAL_TECH_SYNONYMS.get(cleaned, cleaned)
    return f"entity:{cleaned}"


def extract_content_terms(text: str) -> set[str]:
    """Unicode-aware Vietnamese + English content term set (replaces Zero-Mem ASCII regex)."""
    normalized = normalize_session_text(text).casefold()
    raw_tokens = re.findall(r"\w+", normalized)
    result: set[str] = set()
    for tok in raw_tokens:
        if len(tok) < 2 and not tok.isdigit():
            continue
        if tok in COMMON_STOPWORDS or tok in _SINGLE_WORD_STOP_ENTITIES:
            continue
        result.add(tok)
    return result


def split_sentences_with_spans(text: str) -> list[tuple[int, int, str]]:
    """Split text into sentences preserving decimals using SAG main's _SENTENCE_SPLIT_RE."""
    normalized = normalize_session_text(text)
    if not normalized:
        return []
    spans: list[tuple[int, int, str]] = []
    for match in _SENTENCE_SPLIT_RE.finditer(normalized):
        s_start, s_end = match.span()
        seg = normalized[s_start:s_end].strip()
        if seg:
            spans.append((s_start, s_end, seg))
    return spans or [(0, len(normalized), normalized)]


def extract_turn_entities_and_sentences(
    turn_id: str,
    ordinal: int,
    text: str,
    *,
    project_aliases: Sequence[tuple[str, str]] = (),
) -> tuple[list[EntityMention], list[tuple[str, tuple[str, ...]]]]:
    """Extract entities and per-sentence entity keys with 0 LLM or local model calls."""
    normalized = normalize_session_text(text)
    if not normalized:
        return [], []

    adapter = _TurnBlockAdapter(
        id=turn_id,
        ordinal=ordinal,
        normalized_text=normalized,
        source_anchor=f"msg-{turn_id[:8]}",
    )
    cfg = KnowledgeConfig(aliases=tuple(project_aliases)) if project_aliases else KnowledgeConfig()
    e0_facts, _ = extract_e0_e1(adapter, cfg)

    mentions_by_key: dict[str, EntityMention] = {}
    span_records: list[tuple[int, int, str]] = []

    def _register(start: int, end: int, surface: str, soft_aliases: tuple[str, ...] = (), conf: float = 0.92) -> None:
        clean_surface = " ".join(surface.strip().split())
        if len(clean_surface) < 2:
            return
        bare_lower = clean_surface.casefold()
        if bare_lower in COMMON_STOPWORDS or bare_lower in _SINGLE_WORD_STOP_ENTITIES:
            return
        key = normalize_entity_key(clean_surface)
        existing = mentions_by_key.get(key)
        merged_aliases = tuple(sorted(set((existing.soft_aliases if existing else ()) + soft_aliases) - {key}))
        mentions_by_key[key] = EntityMention(
            key=key,
            surface=existing.surface if existing else clean_surface,
            soft_aliases=merged_aliases,
            confidence=max(existing.confidence if existing else 0.0, conf),
        )
        span_records.append((start, end, key))

    for fact in e0_facts:
        if fact.get("kind") in {"entity", "alias"}:
            label = str(fact.get("label") or fact.get("quote") or "").strip()
            start = int(fact.get("start", 0))
            end = int(fact.get("end", len(label)))
            if label:
                _register(start, end, label, conf=float(fact.get("confidence", 0.92)))

    # Conversational Vietnamese kinship/role mentions (handles lowercase chat and preserves FE/BE qualifiers)
    for match in _HONORIFIC_ENTITY_RE.finditer(normalized):
        prefix = match.group(1).strip().casefold()
        target = " ".join(match.group(2).strip().split())
        target_tokens = target.casefold().split()
        if not target_tokens or target_tokens[0] in _HONORIFIC_STOP_TARGETS:
            continue
        soft: list[str] = []
        if prefix in {"anh", "chị", "em", "bạn", "thầy", "cô", "sếp", "ông", "bà"}:
            # Keep role-qualified person distinct ("Minh FE" != "Minh BE"), link bare name only via soft alias edge
            primary_surface = target
            if len(target_tokens) >= 2:
                soft.append(normalize_entity_key(target_tokens[0]))
            soft.append(normalize_entity_key(f"{prefix} {target}"))
        else:
            primary_surface = f"{prefix} {target}"
            soft.append(normalize_entity_key(target))
        _register(match.start(), match.end(), primary_surface, soft_aliases=tuple(soft), conf=0.90)

    # Technical stack, code symbols, and ticket/ADR anchors (case-insensitive)
    for match in _TECH_ANCHOR_RE.finditer(normalized):
        _register(match.start(), match.end(), match.group(0), conf=0.94)

    sentence_spans = split_sentences_with_spans(normalized)
    sentence_items: list[tuple[str, tuple[str, ...]]] = []
    for s_start, s_end, s_text in sentence_spans:
        s_keys: list[str] = []
        seen_s: set[str] = set()
        for m_start, m_end, m_key in span_records:
            if m_start < s_end and m_end > s_start and m_key not in seen_s:
                seen_s.add(m_key)
                s_keys.append(m_key)
        sentence_items.append((s_text, tuple(s_keys)))

    return list(mentions_by_key.values()), sentence_items


def extract_superseded_entity_keys(
    text: str,
    mentions: Sequence[EntityMention],
) -> tuple[str, ...]:
    """Identify entity keys explicitly dropped/replaced in a turn (e.g., 'Chuyển sang Vue thay cho React')."""
    normalized = normalize_session_text(text)
    if not normalized or not mentions:
        return ()

    replaced_keys: list[str] = []
    seen: set[str] = set()
    for pattern in _EXPLICIT_REPLACED_SPAN_PATTERNS:
        for match in pattern.finditer(normalized):
            span_lower = match.group(1).casefold()
            for mention in mentions:
                cand_surfaces = {mention.surface.casefold(), mention.key.removeprefix("entity:")}
                if any(
                    surf and re.search(rf"(?<!\w){re.escape(surf)}(?!\w)", span_lower)
                    for surf in cand_surfaces
                ):
                    if mention.key not in seen:
                        seen.add(mention.key)
                        replaced_keys.append(mention.key)
    return tuple(replaced_keys)


def infer_topic_slot(text: str) -> str | None:
    """Infer canonical topic slot key for decision superseding & conflict tracking."""
    normalized = normalize_session_text(text)
    if not normalized:
        return None
    owner_match = _OWNER_SLOT_RE.search(normalized)
    if owner_match:
        raw_target = " ".join(owner_match.group(1).casefold().split())
        raw_target = re.sub(r"^(?:phần|mảng|module|gói|dịch\s+vụ)\s+", "", raw_target)
        if raw_target and raw_target not in COMMON_STOPWORDS:
            return f"slot:owner:{raw_target}"
    for slot_key, pattern in _SLOT_PATTERNS:
        if pattern.search(normalized):
            return slot_key
    return None


def is_interrogative_turn(text: str) -> bool:
    """Return True when a turn is asking a question rather than asserting a fact or decision."""
    stripped = normalize_session_text(text).rstrip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    return bool(
        re.search(
            r"(?i)\b(?:bao\s+nhiêu|là\s+gì|như\s+thế\s+nào|ai\s+(?:phụ\s+trách|nhận|đảm\s+nhiệm)|"
            r"vì\s+sao|tại\s+sao|khi\s+nào|ngày\s+nào|còn\s+trong\s+lịch\s+sử\s+không)\b",
            stripped,
        )
    )


def _has_unnegated_confirmation(normalized: str) -> bool:
    """Return True iff at least one clause contains an affirmative confirmation unblocked by negation/tentative cues."""
    for clause in _CLAUSE_SPLIT_RE.split(normalized):
        clause_clean = clause.strip()
        if not clause_clean:
            continue
        for match in _CONFIRMED_CUE_RE.finditer(clause_clean):
            prefix_window = clause_clean[max(0, match.start() - 36) : match.start()]
            if _PRE_CONFIRM_GUARD_RE.search(prefix_window):
                continue
            suffix_window = clause_clean[match.end() : match.end() + 28]
            if _POST_CONFIRM_NEG_RE.search(suffix_window):
                continue
            return True
    return False


def classify_epistemic_state(
    text: str,
    *,
    role: str = "user",
) -> tuple[EpistemicStatus, str | None, bool]:
    """Classify turn epistemic status, topic slot, and anaphoric/short dependency."""
    normalized = normalize_session_text(text)
    if not normalized:
        return "FACT", None, True

    word_count = len(normalized.split())
    is_anaphoric = bool(_ANAPHORIC_SHORT_RE.search(normalized)) or word_count <= 9
    slot_key = infer_topic_slot(normalized)

    # Interrogative turns ask about decisions/facts; they never assert or supersede decisions!
    if is_interrogative_turn(normalized):
        return "FACT", slot_key, is_anaphoric

    # Order matters: a sentence that drops A AND confirms B ("bỏ Mongo, chốt Postgres") is CONFIRMED on the slot,
    # Negated confirmations ("chưa chốt dùng Mongo", "không thống nhất") are blocked by _has_unnegated_confirmation.
    if _has_unnegated_confirmation(normalized):
        return "CONFIRMED", slot_key, is_anaphoric
    if _NEGATED_CUE_RE.search(normalized):
        return "NEGATED", slot_key, is_anaphoric
    if _CONSIDERING_CUE_RE.search(normalized):
        return "CONSIDERING", slot_key, is_anaphoric
    if _PROPOSED_CUE_RE.search(normalized):
        return "PROPOSED", slot_key, is_anaphoric
    if _ISSUE_CUE_RE.search(normalized):
        return "OPEN_ISSUE", slot_key, is_anaphoric
    return "FACT", slot_key, is_anaphoric
