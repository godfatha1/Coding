"""SQLite-backed sync state.

Two-way sync is impossible without memory. If Spotify has a track and
SoundCloud does not, that is either an addition on Spotify or a removal on
SoundCloud, and only the previous run's snapshot can tell the two apart. That
snapshot lives in ``mirror``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import Track

SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mirror (
    provider   TEXT NOT NULL,
    track_id   TEXT NOT NULL,
    PRIMARY KEY (provider, track_id)
);

CREATE TABLE IF NOT EXISTS links (
    spotify_id     TEXT NOT NULL UNIQUE,
    soundcloud_id  TEXT NOT NULL UNIQUE,
    score          REAL NOT NULL DEFAULT 0,
    method         TEXT NOT NULL DEFAULT 'fuzzy',
    manual         INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ignores (
    provider   TEXT NOT NULL,
    track_id   TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (provider, track_id)
);

CREATE TABLE IF NOT EXISTS attempts (
    provider       TEXT NOT NULL,
    track_id       TEXT NOT NULL,
    tries          INTEGER NOT NULL DEFAULT 0,
    last_try       TEXT NOT NULL,
    status         TEXT NOT NULL,
    best_score     REAL NOT NULL DEFAULT 0,
    best_candidate TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (provider, track_id)
);

CREATE TABLE IF NOT EXISTS tracks (
    provider   TEXT NOT NULL,
    track_id   TEXT NOT NULL,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, track_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    dry_run     INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'running',
    stats       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL,
    provider   TEXT NOT NULL,
    track_id   TEXT NOT NULL,
    action     TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    reverted   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_actions_run ON actions(run_id);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.db.executescript(_SCHEMA)
        current = self.db.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.db
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    # -- meta --------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()

    # -- mirror (previous run's liked set) ---------------------------------

    def mirror(self, provider: str) -> set[str]:
        rows = self.db.execute(
            "SELECT track_id FROM mirror WHERE provider=?", (provider,)
        ).fetchall()
        return {r["track_id"] for r in rows}

    def has_mirror(self) -> bool:
        return bool(self.db.execute("SELECT 1 FROM mirror LIMIT 1").fetchone())

    def set_mirror(self, provider: str, ids: Iterable[str]) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM mirror WHERE provider=?", (provider,))
            db.executemany(
                "INSERT OR IGNORE INTO mirror(provider, track_id) VALUES(?,?)",
                [(provider, i) for i in ids],
            )

    # -- links -------------------------------------------------------------

    def counterpart(self, provider: str, track_id: str) -> str | None:
        col = "spotify_id" if provider == "spotify" else "soundcloud_id"
        want = "soundcloud_id" if provider == "spotify" else "spotify_id"
        row = self.db.execute(
            f"SELECT {want} AS other FROM links WHERE {col}=?", (track_id,)
        ).fetchone()
        return row["other"] if row else None

    def link_row(self, provider: str, track_id: str) -> sqlite3.Row | None:
        col = "spotify_id" if provider == "spotify" else "soundcloud_id"
        return self.db.execute(
            f"SELECT * FROM links WHERE {col}=?", (track_id,)
        ).fetchone()

    def put_link(
        self,
        spotify_id: str,
        soundcloud_id: str,
        *,
        score: float = 0.0,
        method: str = "fuzzy",
        manual: bool = False,
    ) -> None:
        with self.transaction() as db:
            # A link is 1:1 on both sides; drop anything either id already had.
            db.execute("DELETE FROM links WHERE spotify_id=? OR soundcloud_id=?",
                       (spotify_id, soundcloud_id))
            db.execute(
                "INSERT INTO links(spotify_id, soundcloud_id, score, method, manual, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (spotify_id, soundcloud_id, score, method, int(manual), utcnow()),
            )
            for provider, tid in (("spotify", spotify_id), ("soundcloud", soundcloud_id)):
                db.execute(
                    "INSERT INTO attempts(provider, track_id, tries, last_try, status,"
                    " best_score, best_candidate, note) VALUES(?,?,0,?,'matched',?, '', '')"
                    " ON CONFLICT(provider, track_id) DO UPDATE SET status='matched',"
                    " best_score=excluded.best_score, last_try=excluded.last_try",
                    (provider, tid, utcnow(), score),
                )

    def delete_link(self, provider: str, track_id: str) -> bool:
        col = "spotify_id" if provider == "spotify" else "soundcloud_id"
        with self.transaction() as db:
            cur = db.execute(f"DELETE FROM links WHERE {col}=?", (track_id,))
        return cur.rowcount > 0

    def all_links(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM links ORDER BY created_at DESC"
        ).fetchall()

    def count_links(self) -> int:
        return self.db.execute("SELECT COUNT(*) AS n FROM links").fetchone()["n"]

    # -- ignores -----------------------------------------------------------

    def ignored(self, provider: str) -> set[str]:
        rows = self.db.execute(
            "SELECT track_id FROM ignores WHERE provider=?", (provider,)
        ).fetchall()
        return {r["track_id"] for r in rows}

    def add_ignore(self, provider: str, track_id: str, reason: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO ignores(provider, track_id, reason, created_at)"
            " VALUES(?,?,?,?)",
            (provider, track_id, reason, utcnow()),
        )
        self.db.commit()

    def remove_ignore(self, provider: str, track_id: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM ignores WHERE provider=? AND track_id=?", (provider, track_id)
        )
        self.db.commit()
        return cur.rowcount > 0

    # -- search attempts ---------------------------------------------------

    def attempt(self, provider: str, track_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM attempts WHERE provider=? AND track_id=?",
            (provider, track_id),
        ).fetchone()

    def record_attempt(
        self,
        provider: str,
        track_id: str,
        *,
        status: str,
        best_score: float = 0.0,
        best_candidate: str = "",
        note: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO attempts(provider, track_id, tries, last_try, status,"
            " best_score, best_candidate, note) VALUES(?,?,1,?,?,?,?,?)"
            " ON CONFLICT(provider, track_id) DO UPDATE SET"
            " tries=attempts.tries+1, last_try=excluded.last_try,"
            " status=excluded.status, best_score=excluded.best_score,"
            " best_candidate=excluded.best_candidate, note=excluded.note",
            (provider, track_id, utcnow(), status, best_score, best_candidate, note),
        )
        self.db.commit()

    def attempts_by_status(self, status: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM attempts WHERE status=? ORDER BY last_try DESC", (status,)
        ).fetchall()

    # -- track cache -------------------------------------------------------

    def cache_track(self, track: Track) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO tracks(provider, track_id, payload, updated_at)"
            " VALUES(?,?,?,?)",
            (track.provider, track.id, track.to_json(), utcnow()),
        )

    def cache_tracks(self, tracks: Iterable[Track]) -> None:
        with self.transaction() as db:
            db.executemany(
                "INSERT OR REPLACE INTO tracks(provider, track_id, payload, updated_at)"
                " VALUES(?,?,?,?)",
                [(t.provider, t.id, t.to_json(), utcnow()) for t in tracks],
            )

    def cached_track(self, provider: str, track_id: str) -> Track | None:
        row = self.db.execute(
            "SELECT payload FROM tracks WHERE provider=? AND track_id=?",
            (provider, track_id),
        ).fetchone()
        if not row:
            return None
        try:
            return Track.from_json(row["payload"])
        except Exception:  # noqa: BLE001
            # The cache is only used for display; a row written by an older
            # version must never break a run.
            return None

    def describe(self, provider: str, track_id: str) -> str:
        track = self.cached_track(provider, track_id)
        return track.display() if track else f"{provider}:{track_id}"

    # -- runs and actions --------------------------------------------------

    def start_run(self, *, dry_run: bool) -> int:
        cur = self.db.execute(
            "INSERT INTO runs(started_at, dry_run, status) VALUES(?,?,'running')",
            (utcnow(), int(dry_run)),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, status: str, stats: dict) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at=?, status=?, stats=? WHERE id=?",
            (utcnow(), status, json.dumps(stats, sort_keys=True), run_id),
        )
        self.db.commit()

    def record_action(
        self,
        run_id: int,
        provider: str,
        track_id: str,
        action: str,
        status: str,
        detail: str = "",
    ) -> None:
        self.db.execute(
            "INSERT INTO actions(run_id, provider, track_id, action, status, detail,"
            " created_at) VALUES(?,?,?,?,?,?,?)",
            (run_id, provider, track_id, action, status, detail, utcnow()),
        )
        self.db.commit()

    def last_run(self) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def run(self, run_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

    def recent_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def actions(self, run_id: int, *, only_applied: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM actions WHERE run_id=?"
        if only_applied:
            sql += " AND status='ok' AND reverted=0"
        sql += " ORDER BY id"
        return self.db.execute(sql, (run_id,)).fetchall()

    def mark_reverted(self, action_id: int) -> None:
        self.db.execute("UPDATE actions SET reverted=1 WHERE id=?", (action_id,))
        self.db.commit()

    def vacuum(self) -> None:
        self.db.commit()
        self.db.execute("VACUUM")
