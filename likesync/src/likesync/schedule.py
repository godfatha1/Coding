"""Install a recurring ``likesync sync`` on this machine, with no manual steps.

Picks whatever the platform offers: a launchd agent on macOS, a systemd user
timer on Linux (cron where systemd is not available), Task Scheduler on
Windows. Everything is per-user, so nothing here needs administrator rights.

The text of each unit is produced by a pure function, which is what the tests
check; installing it is a thin layer of file writes and one or two commands.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from .errors import ConfigError

LABEL = "com.likesync.sync"
UNIT = "likesync"
CRON_MARK = "# likesync-schedule"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _run(args: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), capture_output=True, text=True, check=False, **kwargs  # type: ignore[call-overload]
    )


@dataclass(frozen=True)
class Plan:
    """When to run, and exactly what to run."""

    python: str                  # absolute path of the interpreter to use
    home: Path                   # LIKESYNC_HOME
    config: Path | None = None
    frequency: str = "weekly"    # weekly | daily
    weekday: str = "sun"
    hour: int = 10
    minute: int = 17

    def __post_init__(self) -> None:
        if self.frequency not in ("weekly", "daily"):
            raise ConfigError(f"frequency must be weekly or daily, not {self.frequency!r}")
        if self.weekday not in WEEKDAYS:
            raise ConfigError(f"weekday must be one of {', '.join(WEEKDAYS)}")
        if not (0 <= self.hour <= 23 and 0 <= self.minute <= 59):
            raise ConfigError("time must be HH:MM in 24-hour form")

    @property
    def log(self) -> Path:
        return self.home / "likesync.log"

    @property
    def command(self) -> list[str]:
        # Absolute interpreter and explicit paths: schedulers start jobs with
        # almost no environment, so nothing here may depend on PATH or cwd.
        args = [self.python, "-m", "likesync"]
        if self.config:
            args += ["--config", str(self.config)]
        args += ["--log-file", str(self.log), "sync"]
        return args

    def describe(self) -> str:
        at = f"{self.hour:02d}:{self.minute:02d}"
        if self.frequency == "daily":
            return f"every day at {at}"
        return f"every {self.weekday.capitalize()} at {at}"


def parse_time(text: str) -> tuple[int, int]:
    try:
        hour, minute = text.strip().split(":")
        return int(hour), int(minute)
    except ValueError as exc:
        raise ConfigError(f"time must look like 10:17, not {text!r}") from exc


# --------------------------------------------------------------------------
# unit text
# --------------------------------------------------------------------------


def launchd_plist(plan: Plan) -> str:
    args = "\n".join(f"    <string>{escape(a)}</string>" for a in plan.command)
    # launchd counts Sunday as 0 and Monday as 1.
    weekday = (WEEKDAYS.index(plan.weekday) + 1) % 7
    when = [f"    <key>Hour</key><integer>{plan.hour}</integer>",
            f"    <key>Minute</key><integer>{plan.minute}</integer>"]
    if plan.frequency == "weekly":
        when.insert(0, f"    <key>Weekday</key><integer>{weekday}</integer>")
    log = escape(str(plan.log))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LIKESYNC_HOME</key>
    <string>{escape(str(plan.home))}</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
{chr(10).join(when)}
  </dict>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def _systemd_quote(arg: str) -> str:
    if not arg or any(c in arg for c in ' \t"\\$%'):
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    return arg


def systemd_units(plan: Plan) -> tuple[str, str]:
    exec_start = " ".join(_systemd_quote(a) for a in plan.command)
    clock = f"{plan.hour:02d}:{plan.minute:02d}:00"
    calendar = (
        f"*-*-* {clock}"
        if plan.frequency == "daily"
        else f"{plan.weekday.capitalize()} *-*-* {clock}"
    )
    service = f"""[Unit]
Description=Sync Spotify and SoundCloud liked songs
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment={_systemd_quote(f"LIKESYNC_HOME={plan.home}")}
ExecStart={exec_start}
# Exit 3 means some writes failed; they are retried on the next run.
SuccessExitStatus=3
"""
    timer = f"""[Unit]
Description=Sync Spotify and SoundCloud liked songs ({plan.describe()})

[Timer]
OnCalendar={calendar}
# Catch up after the machine was asleep or off at the scheduled time.
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
"""
    return service, timer


def _sh_quote(arg: str) -> str:
    return "'" + arg.replace("'", "'\"'\"'") + "'"


def cron_line(plan: Plan) -> str:
    # cron counts Sunday as 0 and Monday as 1.
    dow = "*" if plan.frequency == "daily" else str((WEEKDAYS.index(plan.weekday) + 1) % 7)
    command = " ".join(_sh_quote(a) for a in plan.command)
    return (
        f"{plan.minute} {plan.hour} * * {dow} "
        f"LIKESYNC_HOME={_sh_quote(str(plan.home))} {command} "
        f">/dev/null 2>&1 {CRON_MARK}"
    )


def windows_batch(plan: Plan) -> str:
    command = " ".join(f'"{a}"' for a in plan.command)
    return f'@echo off\r\nset "LIKESYNC_HOME={plan.home}"\r\n{command}\r\n'


def schtasks_args(plan: Plan, batch: Path) -> list[str]:
    args = ["schtasks", "/Create", "/F", "/TN", UNIT, "/TR", f'"{batch}"',
            "/ST", f"{plan.hour:02d}:{plan.minute:02d}"]
    if plan.frequency == "daily":
        args += ["/SC", "DAILY"]
    else:
        args += ["/SC", "WEEKLY", "/D", plan.weekday.upper()]
    return args


# --------------------------------------------------------------------------
# install / remove / status
# --------------------------------------------------------------------------


def detect(run: Runner = _run) -> str:
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("win"):
        return "schtasks"
    # A user manager is not always running (containers, some servers), so
    # having systemctl on the PATH is not enough.
    if (
        shutil.which("systemctl")
        and run(["systemctl", "--user", "show-environment"]).returncode == 0
    ):
        return "systemd"
    if shutil.which("crontab"):
        return "cron"
    raise ConfigError(
        "No scheduler found on this machine (looked for systemd and cron). "
        "Run `likesync sync` from whatever scheduler you do have."
    )


def _paths(kind: str, user_home: Path, plan_home: Path) -> list[Path]:
    if kind == "launchd":
        return [user_home / "Library" / "LaunchAgents" / f"{LABEL}.plist"]
    if kind == "systemd":
        base = user_home / ".config" / "systemd" / "user"
        return [base / f"{UNIT}.service", base / f"{UNIT}.timer"]
    if kind == "schtasks":
        return [plan_home / "likesync-sync.cmd"]
    return []


def _check(result: subprocess.CompletedProcess[str], what: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ConfigError(f"{what} failed: {detail or f'exit {result.returncode}'}")


def _crontab_without_ours(run: Runner) -> list[str]:
    current = run(["crontab", "-l"])
    if current.returncode != 0:
        return []  # "no crontab for user" is the normal empty case
    return [ln for ln in current.stdout.splitlines() if CRON_MARK not in ln]


def install(
    plan: Plan,
    *,
    kind: str | None = None,
    user_home: Path | None = None,
    run: Runner = _run,
) -> list[str]:
    """Install or replace the schedule. Returns lines describing what was done."""
    kind = kind or detect(run)
    user_home = user_home or Path.home()
    plan.home.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    if kind == "launchd":
        (plist,) = _paths(kind, user_home, plan.home)
        plist.parent.mkdir(parents=True, exist_ok=True)
        run(["launchctl", "unload", str(plist)])  # fine if it was not loaded
        plist.write_text(launchd_plist(plan))
        _check(run(["launchctl", "load", "-w", str(plist)]), "launchctl load")
        notes.append(f"Installed launchd agent {plist}")
        notes.append("If the Mac is asleep at that time, it runs when it next wakes.")

    elif kind == "systemd":
        service, timer = _paths(kind, user_home, plan.home)
        service.parent.mkdir(parents=True, exist_ok=True)
        service_text, timer_text = systemd_units(plan)
        service.write_text(service_text)
        timer.write_text(timer_text)
        _check(run(["systemctl", "--user", "daemon-reload"]), "systemctl daemon-reload")
        _check(run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"]),
               "systemctl enable")
        notes.append(f"Installed systemd user timer {timer}")
        notes.append("A missed run is caught up at next boot or wake. To run while "
                     "logged out as well: loginctl enable-linger")

    elif kind == "cron":
        lines = [*_crontab_without_ours(run), cron_line(plan)]
        _check(run(["crontab", "-"], input="\n".join(lines) + "\n"), "crontab")
        notes.append("Added a line to your crontab")
        notes.append("cron does not catch up a run missed while the machine was off.")

    elif kind == "schtasks":
        (batch,) = _paths(kind, user_home, plan.home)
        batch.write_text(windows_batch(plan))
        _check(run(schtasks_args(plan, batch)), "schtasks")
        notes.append(f"Created the scheduled task '{UNIT}' running {batch}")

    else:
        raise ConfigError(f"unknown scheduler: {kind}")

    notes.insert(0, f"likesync will sync {plan.describe()}.")
    notes.append(f"Log: {plan.log}")
    return notes


def remove(
    *,
    kind: str | None = None,
    user_home: Path | None = None,
    plan_home: Path | None = None,
    run: Runner = _run,
) -> list[str]:
    kind = kind or detect(run)
    user_home = user_home or Path.home()
    plan_home = plan_home or user_home
    if kind == "launchd":
        (plist,) = _paths(kind, user_home, plan_home)
        run(["launchctl", "unload", "-w", str(plist)])
    elif kind == "systemd":
        run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
    elif kind == "cron":
        current = run(["crontab", "-l"])
        if current.returncode == 0 and CRON_MARK in current.stdout:
            kept = [ln for ln in current.stdout.splitlines() if CRON_MARK not in ln]
            _check(run(["crontab", "-"], input="\n".join(kept) + ("\n" if kept else "")),
                   "crontab")
    elif kind == "schtasks":
        run(["schtasks", "/Delete", "/F", "/TN", UNIT])

    removed = []
    for path in _paths(kind, user_home, plan_home):
        if path.exists():
            path.unlink()
            removed.append(str(path))
    if kind == "systemd":
        run(["systemctl", "--user", "daemon-reload"])
    return [f"Removed the {kind} schedule.", *[f"Deleted {p}" for p in removed]]


def status(
    *,
    kind: str | None = None,
    user_home: Path | None = None,
    plan_home: Path | None = None,
    run: Runner = _run,
) -> tuple[bool, str]:
    """(installed?, one-line description)."""
    try:
        kind = kind or detect(run)
    except ConfigError as exc:
        return False, str(exc)
    user_home = user_home or Path.home()
    plan_home = plan_home or user_home
    if kind == "cron":
        current = run(["crontab", "-l"])
        for line in current.stdout.splitlines() if current.returncode == 0 else []:
            if CRON_MARK in line:
                return True, f"cron: {' '.join(line.split()[:5])}"
        return False, "cron: not scheduled"
    if kind == "schtasks":
        found = run(["schtasks", "/Query", "/TN", UNIT]).returncode == 0
        return found, f"Task Scheduler: {'task present' if found else 'not scheduled'}"
    paths = _paths(kind, user_home, plan_home)
    if all(p.exists() for p in paths):
        return True, f"{kind}: {paths[-1]}"
    return False, f"{kind}: not scheduled"
