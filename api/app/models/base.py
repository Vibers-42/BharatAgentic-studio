"""What every model provider shares: the errors it raises and how generate() should treat them.
A provider file only has to stream {"text": ...} pieces, then one {"usage": {...} or None}."""


class ProviderError(RuntimeError):
    """A provider could not answer.
    retryable: worth one more try (rate limit, network, server error).
    fallback:  worth trying the local model instead (False only when the content itself was refused)."""

    code = "model_error"
    retryable = False
    fallback = True

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None,
                 retry_after: float | None = None, fallback: bool | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        if fallback is not None:
            self.fallback = fallback
        self.retry_after = retry_after


class QuotaError(ProviderError):
    """HTTP 429. kind "daily" is not retried today; kind "rate" (per minute) is retried once."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message, code=f"quota_{kind}", retryable=(kind == "rate"), retry_after=retry_after)
        self.kind = kind


def http_error(provider: str, status: int, detail: str) -> ProviderError:
    """Map an HTTP status to the right error: server trouble is retried, bad requests and auth are not."""
    if status in (401, 403):
        return ProviderError(f"{provider} rejected the API key (HTTP {status}). Check the key in .env.", code="auth_error")
    if status == 404:
        return ProviderError(f"{provider} does not know this model (HTTP 404): {detail}", code="model_not_found")
    if status >= 500:
        return ProviderError(f"{provider} server error (HTTP {status}): {detail}", code="server_error", retryable=True)
    return ProviderError(f"{provider} returned HTTP {status}: {detail}", code="bad_request")


def network_error(provider: str, where: str, exc: Exception) -> ProviderError:
    return ProviderError(f"Cannot reach {provider} at {where}: {type(exc).__name__}: {exc}",
                         code="network_error", retryable=True)
