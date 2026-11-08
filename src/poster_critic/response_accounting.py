"""Framework-free, sanitized response metadata snapshots for both executors."""
import re
from pydantic import ValidationError
from .contracts import TokenUsage
from .errors import ProviderError
from .ports import ModelResponse

_SECRET = re.compile(r"(?i)(?:\bsk-[a-z0-9_-]+|\bbearer\s+\S+|\bbasic\s+\S+|(?:api[_-]?key|password|secret)\s*[=:])")
_SENSITIVE_KEYS = ("apikey", "api_key", "authorization", "accesstoken", "refreshtoken", "secret", "password", "credential", "privatekey")


def safe_model_identifier(value: str) -> str:
    if (not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/ -]{0,127}', value) is None
            or _SECRET.search(value)):
        raise ProviderError('model must be a short non-secret provider/model identifier')
    return value

def response_accounting(response: ModelResponse) -> tuple[TokenUsage | None, dict[str, object], bool]:
    """Validate independent accounting fields without trusting the dataclass.

    Invalid fields are omitted, while independently valid usage/metadata survive.
    This snapshot, rather than the raw provider object, is used by ``finally``.
    Neither validation errors nor malformed values are included in diagnostics.
    """
    invalid = False
    usage = None
    if response.usage is not None:
        if isinstance(response.usage, TokenUsage):
            try:
                # model_construct/model_copy can bypass a frozen model's validators.
                usage = TokenUsage.model_validate({
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                })
            except (AttributeError, ValidationError):
                invalid = True
        else:
            invalid = True
    fields: dict[str, object] = {}
    for name in ("request_id", "response_model", "finish_reason"):
        value = getattr(response, name)
        if value is None:
            fields[name] = None
        elif (type(value) is str and 0 < len(value) <= 256 and value.strip()
              and not any(ord(character) < 32 for character in value)
              and not _SECRET.search(value)
              and not value.startswith(("data:", "iVBORw0KGgo", "/9j/"))):
            fields[name] = value
        else:
            fields[name] = None
            invalid = True
    http_status = response.http_status
    if http_status is None or type(http_status) is int and 100 <= http_status <= 599:
        fields["http_status"] = http_status
    else:
        fields["http_status"] = None
        invalid = True
    details: dict[str, int] = {}
    if type(response.usage_details) is dict and len(response.usage_details) <= 256:
        for key, value in response.usage_details.items():
            if (type(key) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,127}", key)
                    and not _SECRET.search(key) and not any(part in key.casefold() for part in _SENSITIVE_KEYS) and type(value) is int and value >= 0):
                details[key] = value
            else:
                invalid = True
    else:
        invalid = True
    fields["usage_details"] = details
    return usage, fields, invalid


