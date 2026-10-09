"""Scrub optional SDK metadata before it enters business artifacts."""

from __future__ import annotations
from pydantic import TypeAdapter, JsonValue

_SENSITIVE = (
    "apikey",
    "authorization",
    "accesstoken",
    "refreshtoken",
    "secret",
    "password",
    "credential",
    "privatekey",
)
_TOKEN_COUNTS = {
    "inputtokens",
    "outputtokens",
    "totaltokens",
    "prompttokens",
    "completiontokens",
}


def safe_metadata(value):
    value = TypeAdapter(JsonValue).validate_python(value)

    def visit(item):
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                normalized = "".join(c for c in key.casefold() if c.isalnum())
                sensitive = normalized == "auth" or any(
                    term in normalized for term in _SENSITIVE
                )
                if "token" in normalized and not (
                    normalized in _TOKEN_COUNTS and type(child) == int
                ):
                    sensitive = True
                result[key] = "[REDACTED]" if sensitive else visit(child)
            return result
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item

    return visit(value)
