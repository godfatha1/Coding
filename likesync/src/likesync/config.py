"""Configuration loading: TOML file, overridden by environment variables."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from .errors import ConfigError

ENV_PREFIX = "LIKESYNC_"


def default_home() -> Path:
    env = os.environ.get(f"{ENV_PREFIX}HOME")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "likesync"
    return Path.home() / ".local" / "share" / "likesync"


@dataclass
class SpotifyConfig:
    client_id: str = ""
    # Spotify's PKCE flow needs no secret. Set it only if you registered a
    # confidential app and want the classic authorization-code flow.
    client_secret: str = ""
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    api_base: str = "https://api.spotify.com/v1"
    auth_base: str = "https://accounts.spotify.com"
    scopes: str = "user-library-read user-library-modify"
    auth_scheme: str = "Bearer"
    # "auto" probes the current unified library endpoint and falls back to the
    # pre-February-2026 per-type routes. Force one with "library" or "tracks".
    write_mode: str = "auto"
    market: str = ""


@dataclass
class SoundCloudConfig:
    # "api" uses the official API and needs approved credentials, which
    # SoundCloud has not been granting. "web" drives the site in a browser you
    # signed in to yourself: no credentials, but read the README first.
    mode: str = "web"

    # --- "web" mode -------------------------------------------------------
    # A playlist used as an intake queue: drop a track in and it gets liked,
    # after which the normal two-way rules apply. Leave empty to disable.
    playlist_url: str = ""
    browser_executable: str = ""
    headless: bool = True
    # Infinite scroll: enough rounds to reach the bottom of your likes. A run
    # that hits this limit fails rather than syncing a partial library.
    max_scrolls: int = 400
    settle_ms: int = 450
    # Pause between write actions. Slow on purpose.
    write_pause_s: float = 1.5

    # --- "api" mode -------------------------------------------------------
    client_id: str = ""
    # SoundCloud treats every client as confidential, so the secret is required
    # even when using PKCE.
    client_secret: str = ""
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    api_base: str = "https://api.soundcloud.com"
    auth_base: str = "https://secure.soundcloud.com"
    # SoundCloud's docs use the "OAuth" authorization scheme rather than
    # "Bearer". Switch to "Bearer" if you get a 401 with a known-good token.
    auth_scheme: str = "OAuth"
    scopes: str = ""
    # "auto" tries POST/DELETE /likes/tracks/{id} then PUT/DELETE
    # /me/favorites/{id}. Force one with "likes" or "favorites".
    write_mode: str = "auto"


@dataclass
class SyncConfig:
    direction: str = "both"           # both | to-spotify | to-soundcloud
    propagate_unlikes: bool = True
    accept_threshold: float = 0.78
    review_threshold: float = 0.62
    duration_tolerance_ms: int = 10_000
    search_limit: int = 10
    # Rate-limit and blast-radius rails. Exceeding a write rail truncates the
    # plan; exceeding an unlike rail skips unlikes entirely for that run.
    max_searches_per_run: int = 400
    max_writes_per_run: int = 300
    max_unlikes_per_run: int = 50
    # If a library comes back smaller than this fraction of what we last saw,
    # assume a truncated API response rather than a mass unlike, and bail.
    min_library_ratio: float = 0.5
    retry_unmatched_after_days: int = 14
    conflict: str = "like_wins"       # like_wins | skip
    # Tracks longer than this are usually DJ sets or podcasts that have no
    # counterpart; 0 disables the filter.
    skip_longer_than_ms: int = 0
    # Per-run overrides for the two safety rails, set by CLI flags rather than
    # config in normal use.
    force_shrink: bool = False
    force_unlikes: bool = False


@dataclass
class HttpConfig:
    timeout: float = 30.0
    max_retries: int = 5
    user_agent: str = "likesync/1.0 (+https://github.com/godfatha1/coding)"


@dataclass
class Config:
    spotify: SpotifyConfig = field(default_factory=SpotifyConfig)
    soundcloud: SoundCloudConfig = field(default_factory=SoundCloudConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    home: Path = field(default_factory=default_home)
    loaded_from: Path | None = None

    @property
    def state_path(self) -> Path:
        return self.home / "state.sqlite3"

    @property
    def token_path(self) -> Path:
        return self.home / "tokens.json"

    @property
    def session_path(self) -> Path:
        """Browser profile that keeps the SoundCloud web session signed in."""
        return self.home / "soundcloud-session.json"

    def validate(self, *, providers: tuple[str, ...] = ("spotify", "soundcloud")) -> None:
        if self.soundcloud.mode not in ("api", "web"):
            raise ConfigError(
                f"soundcloud.mode invalid: {self.soundcloud.mode!r} "
                '(expected "api" or "web")'
            )
        missing = []
        if "spotify" in providers and not self.spotify.client_id:
            missing.append("spotify.client_id")
        # Web mode needs no credentials at all; it uses a browser session.
        if "soundcloud" in providers and self.soundcloud.mode == "api":
            if not self.soundcloud.client_id:
                missing.append("soundcloud.client_id")
            if not self.soundcloud.client_secret:
                missing.append("soundcloud.client_secret")
        if missing:
            raise ConfigError(
                "missing required config: "
                + ", ".join(missing)
                + "\nSet them in config.toml or via "
                + ", ".join(f"{ENV_PREFIX}{m.replace('.', '_').upper()}" for m in missing)
            )
        if self.sync.direction not in ("both", "to-spotify", "to-soundcloud"):
            raise ConfigError(f"sync.direction invalid: {self.sync.direction!r}")
        if self.sync.conflict not in ("like_wins", "skip"):
            raise ConfigError(f"sync.conflict invalid: {self.sync.conflict!r}")
        if not 0 < self.sync.review_threshold <= self.sync.accept_threshold <= 1:
            raise ConfigError(
                "need 0 < sync.review_threshold <= sync.accept_threshold <= 1"
            )


_TOML_ALIASES: dict[str, str] = {}

_HINT_CACHE: dict[type, dict[str, Any]] = {}


def _hints(obj: Any) -> dict[str, Any]:
    """Resolved field types.

    ``from __future__ import annotations`` makes ``dataclasses.fields()[i].type``
    a string, which silently breaks int/bool coercion, so resolve the real
    annotations once per class and cache them.
    """
    cls = type(obj)
    cached = _HINT_CACHE.get(cls)
    if cached is None:
        cached = get_type_hints(cls)
        _HINT_CACHE[cls] = cached
    return cached


def _unwrap_optional(target: Any) -> Any:
    if get_origin(target) is Union:
        args = [a for a in get_args(target) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return target


def _coerce(value: Any, target_type: Any) -> Any:
    target_type = _unwrap_optional(target_type)
    if target_type is bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ConfigError(f"expected a boolean, got {value!r}")
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    if target_type is Path:
        return Path(str(value)).expanduser()
    if target_type is str:
        return str(value)
    return value


def _apply_mapping(obj: Any, data: dict[str, Any], where: str) -> None:
    hints = _hints(obj)
    known = {f.name for f in fields(obj)}
    for raw_key, value in data.items():
        key = _TOML_ALIASES.get(raw_key, raw_key)
        if key not in known:
            raise ConfigError(f"unknown config key: {where}.{raw_key}")
        try:
            setattr(obj, key, _coerce(value, hints[key]))
        except (TypeError, ValueError, ConfigError) as exc:
            raise ConfigError(f"bad value for {where}.{raw_key}: {exc}") from exc


def load_config(path: Path | None = None) -> Config:
    """Load configuration from ``path``, then apply environment overrides."""
    cfg = Config()

    candidates = []
    if path:
        candidates.append(Path(path).expanduser())
    else:
        env_path = os.environ.get(f"{ENV_PREFIX}CONFIG")
        if env_path:
            candidates.append(Path(env_path).expanduser())
        candidates.append(Path("likesync.toml"))
        candidates.append(cfg.home / "config.toml")

    chosen = next((p for p in candidates if p.is_file()), None)
    if path and chosen is None:
        raise ConfigError(f"config file not found: {path}")

    if chosen is not None:
        with chosen.open("rb") as fh:
            data = tomllib.load(fh)
        for section, obj in (
            ("spotify", cfg.spotify),
            ("soundcloud", cfg.soundcloud),
            ("sync", cfg.sync),
            ("http", cfg.http),
        ):
            if section in data:
                if not isinstance(data[section], dict):
                    raise ConfigError(f"[{section}] must be a table")
                _apply_mapping(obj, data[section], section)
        if "home" in data:
            cfg.home = Path(str(data["home"])).expanduser()
        cfg.loaded_from = chosen

    _apply_env(cfg)
    cfg.home = Path(cfg.home).expanduser()
    return cfg


def _apply_env(cfg: Config) -> None:
    """Environment overrides: LIKESYNC_<SECTION>_<FIELD>, plus LIKESYNC_HOME."""
    sections = {
        "SPOTIFY": cfg.spotify,
        "SOUNDCLOUD": cfg.soundcloud,
        "SYNC": cfg.sync,
        "HTTP": cfg.http,
    }
    for name, value in os.environ.items():
        if not name.startswith(ENV_PREFIX) or not value:
            continue
        rest = name[len(ENV_PREFIX) :]
        if rest == "HOME":
            cfg.home = Path(value).expanduser()
            continue
        section, _, fieldname = rest.partition("_")
        obj = sections.get(section)
        if obj is None or not fieldname:
            continue
        hints = _hints(obj)
        key = fieldname.lower()
        if key in {f.name for f in fields(obj)}:
            try:
                setattr(obj, key, _coerce(value, hints[key]))
            except (TypeError, ValueError, ConfigError) as exc:
                raise ConfigError(f"bad value for {name}: {exc}") from exc
