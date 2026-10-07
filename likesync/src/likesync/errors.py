"""Exception hierarchy shared by every provider."""

from __future__ import annotations


class LikeSyncError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(LikeSyncError):
    """Configuration is missing or malformed."""


class AuthError(LikeSyncError):
    """Credentials are missing, rejected, or no longer refreshable.

    Raised when the only remedy is for a human to re-run ``likesync login``.
    """


class ProviderError(LikeSyncError):
    """A provider returned an unexpected response."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class RateLimited(ProviderError):
    """A 429 that survived the client's own retry budget."""

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: object) -> None:
        super().__init__(message, **kw)  # type: ignore[arg-type]
        self.retry_after = retry_after


class QuotaExceeded(ProviderError):
    """Spotify development-mode quota is spent; retrying today will not help."""


class EndpointGone(ProviderError):
    """An endpoint answered 403/404/405 in a way that means "use the other variant"."""


class AbortRun(LikeSyncError):
    """A safety rail tripped and the run stopped before touching anything."""
