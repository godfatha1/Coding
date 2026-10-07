"""Provider-agnostic track representation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

SPOTIFY = "spotify"
SOUNDCLOUD = "soundcloud"
PROVIDERS = (SPOTIFY, SOUNDCLOUD)


def other_provider(name: str) -> str:
    if name == SPOTIFY:
        return SOUNDCLOUD
    if name == SOUNDCLOUD:
        return SPOTIFY
    raise ValueError(f"unknown provider: {name!r}")


@dataclass(frozen=True)
class Track:
    """One track as a single provider describes it.

    ``id`` is always a string. SoundCloud is mid-migration from numeric ids to
    URN strings, and Spotify ids are base62, so nothing here is ever an int.
    """

    provider: str
    id: str
    title: str
    artists: tuple[str, ...] = ()
    duration_ms: int | None = None
    isrc: str | None = None
    url: str | None = None
    album: str | None = None
    # The uploader-supplied title before any "Artist - Title" splitting. Kept so
    # matching can fall back to the raw string when parsing guesses wrong.
    raw_title: str | None = None
    added_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.id)

    def display(self) -> str:
        who = self.artist or "unknown artist"
        return f"{who} — {self.title}"

    def to_json(self) -> str:
        data = asdict(self)
        data["artists"] = list(self.artists)
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, blob: str) -> Track:
        data = json.loads(blob)
        data["artists"] = tuple(data.get("artists") or ())
        data.setdefault("extra", {})
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
