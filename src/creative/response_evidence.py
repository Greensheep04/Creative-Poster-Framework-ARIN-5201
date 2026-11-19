"""Safe pre-validation evidence and immutable revision input binding."""
from __future__ import annotations
import json
import re


def redact_response_text(text: str, token: str = "") -> str:
    """Preserve response formatting except credential spans, including broken JSON."""
    if token:
        for value in (token, json.dumps(token, ensure_ascii=False)[1:-1], json.dumps(token)[1:-1]):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"\bhf_[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>,;]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r'(?i)("[^"\n]*(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|credential|secret|token|private[_-]?key)[^"\n]*"\s*:\s*)"(?:\\.|[^"\\])*"',
        lambda match: match[1] + '"[REDACTED]"', text,
    )
    return text


def bind_revision_keywords(content: object, expected: list[str]) -> list[str]:
    """Recover malformed immutable input, while rejecting valid replacement text."""
    if not isinstance(content, dict):
        return []
    keywords = content.get("keywords")
    if isinstance(keywords, list) and keywords and all(isinstance(v, str) and v.strip() for v in keywords):
        return []  # The regular validator checks exact equality to the request.
    content["keywords"] = list(expected)
    return ["keywords"]


def bind_matching_keyword_objects(content: object, expected: list[str]) -> list[str]:
    """Accept request-shaped metadata only when every literal text matches in order."""
    if not isinstance(content, dict):
        return []
    value = content.get("keywords")
    if (isinstance(value, list) and len(value) == len(expected)
            and all(isinstance(item, dict) and item.get("text") == text
                    and set(item) <= {"keyword_id", "position", "text"}
                    for item, text in zip(value, expected))):
        content["keywords"] = list(expected)
        return ["keywords"]
    return []
