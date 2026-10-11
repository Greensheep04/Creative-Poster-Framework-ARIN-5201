"""Errors distinguish invalid inputs and execution failures from poster quality."""


class InputError(ValueError):
    """The caller must correct the input before an audit can run."""


class ResponseError(ValueError):
    """A model response is malformed or does not cover the requested checks."""


class ProviderError(RuntimeError):
    """Sanitized provider/configuration error; never include response bodies or keys."""

    def __init__(self, message: str, *, http_status: int | None = None,
                 request_id: str | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.request_id = request_id
        self.retry_after = retry_after


class RetryableProviderError(ProviderError):
    """A transient request failure that may consume the single retry."""


class BudgetExceededError(RuntimeError):
    """No request budget remains; do not bypass the executor."""
