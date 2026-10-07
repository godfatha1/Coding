from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from likesync.config import Config, SyncConfig
from likesync.models import SOUNDCLOUD, SPOTIFY, Track
from likesync.state import Store


class FakeProvider:
    """In-memory provider.

    ``library`` is what the account currently likes; ``catalog`` is everything
    searchable. Search returns the whole catalog so tests exercise the real
    scoring path rather than a stubbed matcher.
    """

    def __init__(self, name: str, library=(), catalog=()):
        self.name = name
        self.library = {t.id: t for t in library}
        self.catalog = list(catalog) + list(library)
        self.failures: dict[str, str] = {}
        self.search_calls: list[str] = []
        self.like_calls: list[list[str]] = []
        self.unlike_calls: list[list[str]] = []
        self.fail_ids: set[str] = set()
        self.truncate_to: int | None = None

    def liked(self):
        items = list(self.library.values())
        if self.truncate_to is not None:
            return items[: self.truncate_to]
        return items

    def _find(self, track_id):
        for t in self.catalog:
            if t.id == track_id:
                return t
        return None

    def like(self, track_ids):
        self.like_calls.append(list(track_ids))
        self.failures = {}
        done = []
        for tid in track_ids:
            if tid in self.fail_ids:
                self.failures[tid] = "simulated failure"
                continue
            track = self._find(tid)
            if track is None:
                self.failures[tid] = "unknown id"
                continue
            self.library[tid] = track
            done.append(tid)
        return done

    def unlike(self, track_ids):
        self.unlike_calls.append(list(track_ids))
        self.failures = {}
        done = []
        for tid in track_ids:
            if tid in self.fail_ids:
                self.failures[tid] = "simulated failure"
                continue
            self.library.pop(tid, None)
            done.append(tid)
        return done

    def search(self, query, limit=10):
        self.search_calls.append(query)
        return list(self.catalog)[:limit]

    def search_isrc(self, isrc, limit=5):
        return [t for t in self.catalog if t.isrc and t.isrc.upper() == isrc.upper()]


def sp(track_id, title, artists, duration_ms=200_000, isrc=None):
    return Track(provider=SPOTIFY, id=track_id, title=title,
                 artists=tuple(artists), duration_ms=duration_ms, isrc=isrc)


def sc(track_id, title, artists=("Uploader",), duration_ms=200_000, isrc=None,
       artist_source="uploader"):
    return Track(provider=SOUNDCLOUD, id=track_id, title=title,
                 artists=tuple(artists), duration_ms=duration_ms, isrc=isrc,
                 raw_title=title, extra={"artist_source": artist_source})


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state.sqlite3")
    yield s
    s.close()


@pytest.fixture
def sync_cfg():
    cfg = SyncConfig()
    cfg.max_searches_per_run = 100
    return cfg


@pytest.fixture
def config(tmp_path):
    cfg = Config()
    cfg.home = tmp_path
    cfg.spotify.client_id = "sp-client"
    cfg.soundcloud.client_id = "sc-client"
    cfg.soundcloud.client_secret = "sc-secret"
    return cfg
