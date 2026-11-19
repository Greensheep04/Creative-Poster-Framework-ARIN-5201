"""Scrub optional SDK metadata before it enters business artifacts."""

from __future__ import annotations
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
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
    "signature",
    "accesskey",
    "cookie",
)
_TOKEN_COUNTS = {
    "inputtokens",
    "outputtokens",
    "totaltokens",
    "prompttokens",
    "completiontokens",
}
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _normalize(key):
    return "".join(c for c in key.casefold() if c.isalnum())


def _credential(key, value=None):
    key = _normalize(key)
    return (
        key in {"auth", "key", "sig", "sas"}
        or any(term in key for term in _SENSITIVE)
        or ("token" in key and not (key in _TOKEN_COUNTS and type(value) == int))
    )


def _scrub_string(value):
    def url(match):
        parts = urlsplit(match.group(0))
        query = [
            (key, "[REDACTED]" if _credential(key) else val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
        ]
        netloc = (
            ("[REDACTED]@" + parts.netloc.rsplit("@", 1)[1])
            if "@" in parts.netloc
            else parts.netloc
        )
        fragment = parts.fragment
        if "=" in fragment:
            fragment = urlencode(
                [
                    (key, "[REDACTED]" if _credential(key) else val)
                    for key, val in parse_qsl(fragment, keep_blank_values=True)
                ]
            )
        return urlunsplit(
            (parts.scheme, netloc, parts.path, urlencode(query), fragment)
        )

    value = _URL.sub(url, value)
    return re.sub(
        r"\b(Bearer|Basic)\s+[^\s,;]+",
        lambda match: match.group(1) + " [REDACTED]",
        value,
        flags=re.IGNORECASE,
    )


def safe_metadata(value):
    value = TypeAdapter(JsonValue).validate_python(value)

    def visit(item):
        if isinstance(item, dict):
            pair_secret = any(
                isinstance(item.get(label), str) and _credential(item[label])
                for label in ("name", "key", "header")
            )
            result = {}
            for key, child in item.items():
                secret = _credential(key, child) or (
                    pair_secret
                    and _normalize(key)
                    in {"value", "values", "content", "text", "data"}
                )
                result[key] = "[REDACTED]" if secret else visit(child)
            return result
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, str):
            return _scrub_string(item)
        return item

    return visit(value)
