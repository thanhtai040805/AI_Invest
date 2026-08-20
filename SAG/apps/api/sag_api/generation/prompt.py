"""Prompt sinh câu trả lời và cấu trúc trích dẫn."""

from __future__ import annotations

from typing import Any

from sag_api.branding import DEFAULT_AGENT_NAME
from sag_api.sag import RetrievedSection


def _citation_excerpt(content: str) -> str:
    """Return a bounded source excerpt without assigning it event semantics."""
    text = " ".join(content.split())
    if not text:
        return ""
    excerpt_limit = 720
    excerpt = text[:excerpt_limit].strip()
    if len(text) > excerpt_limit:
        excerpt = excerpt.rstrip("…") + "…"
    return excerpt


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _iso_datetime(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat()).strip()
    return str(value).strip()


def _event_refs_by_section(events: list[Any] | None) -> dict[tuple[str, str], list[dict[str, str]]]:
    """Index traceable extracted events by source config and chunk.

    Event order comes from ``graph_for_sections`` and is preserved.  The
    composite key is required because chunk identifiers are only source-local.
    """

    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    seen: dict[tuple[str, str], set[str]] = {}
    for event in events or []:
        source_config_id = str(_field(event, "source_config_id") or "").strip()
        chunk_id = str(_field(event, "chunk_id") or "").strip()
        event_id = str(_field(event, "id") or "").strip()
        title = " ".join(str(_field(event, "title") or "").split())
        if not source_config_id or not chunk_id or not event_id or not title:
            continue
        key = (source_config_id, chunk_id)
        if event_id in seen.setdefault(key, set()):
            continue
        seen[key].add(event_id)
        ref = {
            "id": event_id,
            "title": title[:500],
            "summary": " ".join(str(_field(event, "summary") or "").split())[:800],
            "category": " ".join(str(_field(event, "category") or "").split())[:100],
        }
        start_time = _iso_datetime(_field(event, "start_time"))
        if start_time:
            ref["start_time"] = start_time
        content = " ".join(str(_field(event, "content") or "").split())[:4000]
        if content:
            ref["content"] = content
        grouped.setdefault(key, []).append(ref)
    return grouped


_GUIDANCE = {
    "vi": (
        "【MỤC TIÊU PHÂN TÍCH TÀI CHÍNH】\n"
        "- Đóng vai trò là Chuyên gia Phân tích Đầu tư / Senior Broker chuyên nghiệp tại thị trường tài chính Việt Nam và Quốc tế.\n"
        "- Cung cấp kết quả phân tích sắc bén, khách quan, có căn cứ từ báo cáo tài chính (BCTC), báo cáo thường niên (BCTN), "
        "thuyết minh kế toán, tin tức vĩ mô, chuỗi giá trị ngành và các công bố thông tin chính thức.\n"
        "【QUY TẮC BẰNG CHỨNG & TRÍCH DẪN】\n"
        "- Ưu tiên tuyệt đối dữ liệu gốc (BCTC kiểm toán, Thuyết minh, Công bố thông tin HOSE/HNX/SSC, Nghị định/Thông tư chính thức).\n"
        "- Phải dẫn nguồn chính xác exact chunk/trang/thuyết minh được cung cấp. Đánh dấu trích dẫn bằng [n] ngay sau luận điểm tương ứng.\n"
        "- Phân biệt rõ ràng giữa: Bằng chứng thực tế (Facts), Biến động số liệu (Metrics), Giả định phân tích (Assumptions) và Rủi ro chưa xác minh (Risks).\n"
        "【SUY LUẬN ĐA CHẶNG (MULTI-HOP REASONING)】\n"
        "- Nối chuỗi logic giữa các sự kiện (Event) và thực thể (Entity): Biến động vĩ mô/Giá hàng hóa ➔ Chi phí đầu vào/Biên lợi nhuận ➔ "
        "Dòng tiền HĐKD/Nợ vay ➔ Định giá & Động lực tăng giá (Catalysts).\n"
        "- Phát hiện rủi ro nợ ẩn, giao dịch bên liên quan (RPT), sở hữu chéo, pha loãng cổ phiếu từ phần Thuyết minh BCTC.\n"
        "【SỬ DỤNG CÔNG CỤ】\n"
        "- Gọi search_context khi cần tra cứu tài liệu phi cấu trúc BCTC/BCTN/News. Dùng get_entity để xác định quan hệ thực thể/cổ đông/dự án.\n"
        "- Nếu thông tin chưa đủ để xác nhận rủi ro hoặc định giá, nêu rõ khoảng trống dữ liệu thay vì tự tạo thông tin giả định."
    ),
    "en": (
        "[Delivery objective]\n"
        "- Act as a Senior Financial Analyst & Institutional Broker for Vietnamese and International Markets.\n"
        "- Optimize for solving the user's real problem and delivering a directly usable result with precise citations.\n"
        "[Evidence strategy & Citations]\n"
        "- Prioritize audited financial statements, notes, regulatory disclosures, and official macro/industry data.\n"
        "- Only search_context numbers may be cited as [n], near the supported claim.\n"
        "- Distinguish facts, metrics, analytical assumptions, and evidence gaps.\n"
        "[Multi-hop Reasoning]\n"
        "- Connect macroeconomic events, commodity price cycles, capacity expansions, and financial metrics to valuation catalysts.\n"
        "- Uncover off-balance sheet liabilities, related party transactions (RPT), and dilution risks from financial notes."
    ),
}

_TIME_RULE = {
    "vi": (
        "Bối cảnh hiện tại: Múi giờ hệ thống là «{timezone}». Thời gian và ngày tháng hiện tại là dữ liệu động, "
        "phải gọi get_time khi liên quan đến dữ liệu thời gian thực (giá cổ phiếu, tin tức mới nhất, kỳ BCTC)."
    ),
    "en": (
        "Current context: the configured system timezone is {timezone}. Database and API timestamps use UTC; "
        "convert them for the user. Call get_time for time-sensitive tasks."
    ),
}

_IDENTITY = {
    "vi": "Tên của bạn là «{name}». Bạn là Chuyên gia Phân tích Đầu tư AIInvest.",
    "en": "Your name is {name}. You are the AIInvest Senior Financial Analysis Agent.",
}

_USER_TEMPLATE = {
    "vi": "Tài liệu & Nguồn tri thức:\n{context}\n\nCâu hỏi phân tích: {query}\n\nHãy phân tích dựa trên nguồn tài liệu trên và trích dẫn [n] tại vị trí tương ứng.",
    "en": "Sources:\n{context}\n\nFinancial Analysis Query: {query}\n\nAnswer from the sources and cite with [index].",
}


def estimate_tokens(text: str) -> int:
    """Ước lượng token nhận biết CJK: CJK ≈1/ký tự, còn lại ≈1/4 ký tự (đồng bộ frontend)."""
    cjk = sum(1 for ch in text if "\u3000" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff")
    return cjk + max(0, (len(text) - cjk) + 3) // 4


def _format_context(sections: list[RetrievedSection]) -> str:
    if not sections:
        return "（Không có tài liệu liên quan）"
    blocks = []
    for i, s in enumerate(sections, start=1):
        heading = s.heading or "Đoạn trích"
        blocks.append(f"[{i}] {heading}\n{s.content}")
    return "\n\n".join(blocks)


def _identity_prompt(name: str, language: str) -> str:
    display_name = name.strip() or DEFAULT_AGENT_NAME
    return _IDENTITY[language].format(name=display_name)


def build_messages(
    query: str,
    sections: list[RetrievedSection],
    *,
    history: list[dict[str, str]] | None = None,
    language: str = "vi",
    name: str = DEFAULT_AGENT_NAME,
) -> list[dict[str, str]]:
    lang = language if language in _GUIDANCE else "vi"
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "\n\n".join((_identity_prompt(name, lang), _GUIDANCE[lang])),
        }
    ]
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": _USER_TEMPLATE[lang].format(context=_format_context(sections), query=query),
        }
    )
    return messages


def build_agent_messages(
    name: str,
    persona: dict[str, Any],
    query: str,
    *,
    history: list[dict[str, str]] | None = None,
    language: str = "vi",
    timezone: str = "Asia/Ho_Chi_Minh",
    attachments: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Tiêm cấu hình Agent (agent-first: không có vùng tài liệu cố định, truy vấn do công cụ thực hiện theo nhu cầu)."""
    lang = language if language in _GUIDANCE else "vi"
    persona = persona or {}
    parts = [_identity_prompt(name, lang)]
    system_prompt = str(persona.get("system_prompt") or "").strip()
    if system_prompt:
        parts.append(system_prompt)
    parts.append(_GUIDANCE[lang])
    parts.append(_TIME_RULE[lang].format(timezone=timezone))
    guardrails = persona.get("guardrails") or []
    if guardrails:
        parts.append("Ràng buộc：" + "；".join(guardrails))
    empty_response = (persona.get("empty_response") or "").strip()
    if empty_response:
        parts.append(f"Nếu sau khi truy vấn vẫn không có tài liệu liên quan, hãy đáp bằng câu này: 「{empty_response}」")
    messages: list[dict[str, str]] = [{"role": "system", "content": "\n\n".join(parts)}]
    if history:
        messages.extend(history)
    user_text = query
    if attachments:
        # Đầu vào hình ảnh: content parts tương thích OpenAI (ảnh đọc đĩa chuyển data URL; vòng lịch sử chỉ giữ văn bản)
        import base64

        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for att in attachments:
            path, media_type = att.get("path"), att.get("media_type", "image/png")
            if not path:
                continue
            try:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
            except OSError:
                continue
            content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_text})
    return messages


def build_prompt_preview(messages: list[dict[str, Any]], *, limit: int = 6000) -> str:
    """Ghép ngữ cảnh đầu vào trước khi chạy thành bản xem trước dễ đọc.

    Tin nhắn đa phương thức (content là danh sách parts): phần văn bản giữ nguyên, hình ảnh
    hiển thị bằng placeholder (không xuất base64).
    """
    lines: list[str] = []
    current_user_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].get("role") == "user"),
        -1,
    )
    history_labels = {"user": "Lịch sử · Người dùng", "assistant": "Lịch sử · Trợ lý", "tool": "Lịch sử · Công cụ"}
    for index, m in enumerate(messages):
        role = m.get("role", "")
        if role == "system":
            label = "Hệ thống"
        elif role == "user" and index == current_user_index:
            label = "Câu hỏi hiện tại"
        else:
            label = history_labels.get(role, role)
        content = m.get("content", "")
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content if p.get("type") == "text"]
            images = sum(1 for p in content if p.get("type") == "image_url")
            content = "\n".join(texts) + (f"\n〔Hình đính kèm ×{images}〕" if images else "")
        lines.append(f"【{label}】\n{content}")
    text = "\n\n".join(lines)
    if len(text) > limit:
        # Giữ phần đầu của hệ thống và phần cuối chứa câu hỏi hiện tại; chỉ nén lịch sử ở giữa,
        # tránh bảng trong suốt cắt mất đúng đầu vào của lượt này.
        head = max(1, int(limit * 0.62))
        tail = max(1, limit - head)
        text = text[:head].rstrip() + "\n\n…（ngữ cảnh lịch sử ở giữa đã bị cắt）…\n\n" + text[-tail:].lstrip()
    return text


def build_citations(
    sections: list[RetrievedSection],
    source_refs: dict[str, dict[str, str]] | None = None,
    events: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Xây dựng danh sách trích dẫn một cách xác định từ các đoạn đã truy vấn (số đánh dấu khớp với prompt).

    `source_refs`：{sag_source_config_id: {"id": id nguồn sag, "name": tên nguồn}}。
    `events`：các sự kiện đã trích xuất thực tế do `graph_for_sections` trả về；liên kết theo
    `(source_config_id, chunk_id)`，mỗi trích dẫn kèm tối đa ba sự kiện.
    `source_id` trả ra bên ngoài luôn là **id nguồn sag** (có thể route trực tiếp / lấy nguyên văn)，không rò rỉ id nội bộ engine.
    `event_refs[].content` là nội dung sự kiện sau khi trích xuất；`snippet` chỉ dùng để định vị nguyên văn，
    không suy diễn hoặc bịa nội dung sự kiện từ thân chunk.
    """
    citations = []
    event_refs = _event_refs_by_section(events)
    for i, s in enumerate(sections, start=1):
        snippet = _citation_excerpt(s.content)
        ref = (source_refs or {}).get(s.source_config_id or "") or {}
        citation = {
            "kind": "internal",
            "n": i,
            "chunk_id": s.chunk_id,
            "heading": s.heading,
            "snippet": snippet,
            "score": round(s.score, 4),
            "source_id": ref.get("id"),
            "source_name": ref.get("name"),
        }
        event_key = ((s.source_config_id or "").strip(), (s.chunk_id or "").strip())
        matched_events = event_refs.get(event_key, [])[:3]
        if matched_events:
            citation["event_refs"] = matched_events
        citations.append(citation)
    return citations
