"""From a fresh clone to a scheduled sync in one command.

Every step is skipped when it is already done, so the command is safe to run
again: after a sign-in expires, after a Client ID finally arrives, or just to
check that everything is still in place.

Two things cannot be automated by anyone but the account holder, and the
wizard stops at exactly those: signing in to SoundCloud in the window it
opens, and approving the Spotify consent screen.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import schedule
from .app import build_app
from .config import Config, load_config, set_config_value
from .engine import RunReport
from .errors import LikeSyncError
from .models import SOUNDCLOUD, SPOTIFY
from .report import to_text
from .tokens import TokenStore
from .websession import BrowserSession


@dataclass
class Options:
    spotify_client_id: str = ""
    playlist: str = ""
    frequency: str = "weekly"
    weekday: str = "sun"
    at: str = "10:17"
    schedule: bool = True
    interactive: bool = True
    manual: bool = False
    apply: bool = False


def _real(value: str) -> str:
    """Treat the example file's placeholders as "not set"."""
    value = (value or "").strip()
    return "" if value.upper().startswith("PUT_") else value


# -- the operations with side effects, replaceable in tests -----------------


def _soundcloud_ready(cfg: Config) -> bool:
    if cfg.soundcloud.mode == "web":
        return cfg.session_path.exists()
    return TokenStore(cfg.token_path).load(SOUNDCLOUD).usable


def _soundcloud_login(cfg: Config) -> None:
    if cfg.soundcloud.mode == "web":
        session = BrowserSession(
            state_path=cfg.session_path,
            executable_path=cfg.soundcloud.browser_executable or None,
            headless=False,
        )
        try:
            session.login()
        finally:
            session.close()
        return
    cfg.validate(providers=(SOUNDCLOUD,))
    app = build_app(cfg)
    try:
        app.auth[SOUNDCLOUD].login()
    finally:
        app.close()


def _soundcloud_probe(cfg: Config) -> str:
    app = build_app(cfg)
    try:
        probe = getattr(app.providers[SOUNDCLOUD], "probe", None)
        return str(probe().get("verdict", "")) if probe else "ready"
    finally:
        app.close()


def _spotify_ready(cfg: Config) -> bool:
    return TokenStore(cfg.token_path).load(SPOTIFY).usable


def _spotify_login(cfg: Config, manual: bool) -> None:
    cfg.validate(providers=(SPOTIFY,))
    app = build_app(cfg)
    try:
        app.auth[SPOTIFY].login(manual=manual)
    finally:
        app.close()


def _run_engine(cfg: Config, *, dry_run: bool) -> RunReport:
    cfg.validate()
    app = build_app(cfg)
    try:
        return app.engine().run(dry_run=dry_run)
    finally:
        app.close()


@dataclass
class Steps:
    soundcloud_ready: Callable[[Config], bool] = _soundcloud_ready
    soundcloud_login: Callable[[Config], None] = _soundcloud_login
    soundcloud_probe: Callable[[Config], str] = _soundcloud_probe
    spotify_ready: Callable[[Config], bool] = _spotify_ready
    spotify_login: Callable[[Config, bool], None] = _spotify_login
    plan: Callable[[Config], RunReport] = field(
        default=lambda cfg: _run_engine(cfg, dry_run=True)
    )
    sync: Callable[[Config], RunReport] = field(
        default=lambda cfg: _run_engine(cfg, dry_run=False)
    )
    install_schedule: Callable[[schedule.Plan], list[str]] = schedule.install
    ask: Callable[[str], str] = input
    say: Callable[[str], None] = print


def make_plan(cfg: Config, options: Options) -> schedule.Plan:
    hour, minute = schedule.parse_time(options.at)
    return schedule.Plan(
        python=sys.executable,
        home=Path(cfg.home).resolve(),
        # Passed explicitly so the job does not depend on its working directory.
        config=cfg.loaded_from.resolve() if cfg.loaded_from else None,
        frequency=options.frequency,
        weekday=options.weekday,
        hour=hour,
        minute=minute,
    )


def run_setup(cfg: Config, options: Options, steps: Steps | None = None) -> int:
    """Run the wizard. Returns 0 when everything is in place, 2 otherwise."""
    steps = steps or Steps()
    say = steps.say
    todo: list[str] = []
    path = cfg.loaded_from or (cfg.home / "config.toml")

    # ---- 1. configuration --------------------------------------------------
    say("\n[1/5] Configuration")
    if not path.is_file():
        set_config_value(path, "soundcloud", "mode", cfg.soundcloud.mode)
        say(f"  Created {path}")

    client_id = _real(options.spotify_client_id) or _real(cfg.spotify.client_id)
    if not client_id and options.interactive:
        say("  Spotify needs the Client ID of an app owned by an account with "
            "Premium\n  (not necessarily yours - see 'Set up Spotify' in the README).")
        client_id = _real(steps.ask("  Paste the Client ID, or press Enter to skip: "))
    if client_id and client_id != cfg.spotify.client_id:
        set_config_value(path, "spotify", "client_id", client_id)
        say("  Saved the Spotify Client ID.")

    playlist = options.playlist.strip()
    if (
        not playlist
        and not cfg.soundcloud.playlist_url
        and options.interactive
        and cfg.soundcloud.mode == "web"
    ):
        playlist = steps.ask(
            "  Optional: URL of a SoundCloud playlist to use as an inbox "
            "(Enter to skip): "
        ).strip()
    if playlist:
        set_config_value(path, "soundcloud", "playlist_url", playlist)
        say("  Saved the inbox playlist.")

    cfg = load_config(path)
    say(f"  Using {path}")

    # ---- 2. SoundCloud -------------------------------------------------------
    say("\n[2/5] SoundCloud")
    soundcloud_ok = steps.soundcloud_ready(cfg)
    if soundcloud_ok:
        say("  Already signed in.")
    else:
        try:
            steps.soundcloud_login(cfg)
            soundcloud_ok = steps.soundcloud_ready(cfg)
        except LikeSyncError as exc:
            say(f"  Could not sign in here: {exc}")
    if soundcloud_ok:
        try:
            verdict = steps.soundcloud_probe(cfg)
        except LikeSyncError as exc:
            verdict = str(exc)
        if verdict.startswith("ready"):
            say("  Checked: signed in, and your likes page is readable.")
        else:
            soundcloud_ok = False
            say(f"  Check failed: {verdict}")
    if not soundcloud_ok:
        todo.append(
            "Sign in to SoundCloud: run `likesync setup` on a machine with a "
            "screen and sign in when the window opens. (For a server: sign in "
            "on your laptop, then `likesync session export` / `session import`.)"
        )

    # ---- 3. Spotify ------------------------------------------------------------
    say("\n[3/5] Spotify")
    spotify_ok = False
    if not _real(cfg.spotify.client_id):
        say("  No Client ID yet, so this step is skipped.")
        todo.append(
            "Get a Spotify Client ID (README, 'Set up Spotify'), then run: "
            "likesync setup --spotify-client-id <the id>"
        )
    elif steps.spotify_ready(cfg):
        spotify_ok = True
        say("  Already connected.")
    else:
        try:
            steps.spotify_login(cfg, options.manual)
            spotify_ok = steps.spotify_ready(cfg)
        except LikeSyncError as exc:
            say(f"  Could not connect: {exc}")
        if not spotify_ok:
            todo.append("Approve the Spotify consent screen: run `likesync setup` again.")

    ready = soundcloud_ok and spotify_ok

    # ---- 4. first look ---------------------------------------------------------
    say("\n[4/5] First look")
    if not ready:
        say("  Skipped until both services are connected.")
    else:
        try:
            report = steps.plan(cfg)
            say(to_text(report))
            if report.aborted:
                todo.append(f"The trial run stopped: {report.aborted}")
            else:
                apply = options.apply
                if not apply and options.interactive and report.planned:
                    answer = steps.ask("\n  Apply this now? [y/N] ").strip().lower()
                    apply = answer in ("y", "yes")
                if apply and report.planned:
                    say(to_text(steps.sync(cfg)))
                elif report.planned:
                    say("\n  Nothing written yet. The first scheduled run applies "
                        "this plan;\n  `likesync undo` reverses any run.")
        except LikeSyncError as exc:
            say(f"  The trial run failed: {exc}")
            todo.append(f"Fix the trial run: {exc}")

    # ---- 5. schedule -------------------------------------------------------------
    say("\n[5/5] Schedule")
    if not options.schedule:
        say("  Skipped (--no-schedule).")
    elif not ready:
        say("  Not scheduled yet: there is nothing to run until both services "
            "are connected.")
    else:
        try:
            for line in steps.install_schedule(make_plan(cfg, options)):
                say(f"  {line}")
        except LikeSyncError as exc:
            say(f"  Could not install the schedule: {exc}")
            todo.append("Install the schedule: likesync schedule install")

    # ---- summary -------------------------------------------------------------------
    if todo:
        say("\nStill needed:")
        for item in todo:
            say(f"  - {item}")
        say("\nRun `likesync setup` again afterwards; finished steps are skipped.")
        return 2
    say("\nAll set. Check on it any time with: likesync status")
    return 0
