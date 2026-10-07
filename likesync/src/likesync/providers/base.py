"""Provider interface."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from ..errors import AuthError, QuotaExceeded, RateLimited
from ..models import Track


class Memo(Protocol):
    """Somewhere to remember which endpoint variant worked."""

    def get_meta(self, key: str, default: str | None = None) -> str | None: ...
    def set_meta(self, key: str, value: str) -> None: ...


class DictMemo:
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data = data or {}

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        return self.data.get(key, default)

    def set_meta(self, key: str, value: str) -> None:
        self.data[key] = value


class Provider(Protocol):
    name: str

    def liked(self) -> list[Track]:
        """Every liked/saved track. Must raise rather than return a partial list."""
        ...

    def like(self, track_ids: Sequence[str]) -> list[str]:
        """Like each id; return the ids that succeeded."""
        ...

    def unlike(self, track_ids: Sequence[str]) -> list[str]:
        """Unlike each id; return the ids that succeeded."""
        ...

    def search(self, query: str, limit: int = 10) -> list[Track]:
        ...

    def search_isrc(self, isrc: str, limit: int = 5) -> list[Track]:
        ...


def chunked(items: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def systemic(exc: Exception) -> bool:
    """True when an error means "stop the run", not "skip this track".

    Auth failures, rate limits and quota exhaustion will hit every remaining
    item, so retrying per-track just burns the budget.
    """
    return isinstance(exc, (AuthError, RateLimited, QuotaExceeded))
