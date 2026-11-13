"""The self-installing schedule: unit text, and what installing it runs."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from likesync import schedule
from likesync.cli import build_parser
from likesync.errors import ConfigError
from likesync.schedule import Plan


def plan(tmp_path, **kw) -> Plan:
    defaults = {"python": "/opt/venv/bin/python", "home": tmp_path / "ls home"}
    defaults.update(kw)
    return Plan(**defaults)


class FakeRun:
    """Stands in for subprocess.run and keeps a pretend crontab."""

    def __init__(self, crontab: str | None = None, fail: tuple[str, ...] = ()):
        self.calls: list[list[str]] = []
        self.crontab = crontab
        self.fail = fail

    def __call__(self, args, **kw):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["crontab", "-l"]:
            if self.crontab is None:
                return subprocess.CompletedProcess(args, 1, "", "no crontab for user")
            return subprocess.CompletedProcess(args, 0, self.crontab, "")
        if args[:2] == ["crontab", "-"]:
            self.crontab = kw["input"]
            return subprocess.CompletedProcess(args, 0, "", "")
        code = 1 if any(token in args for token in self.fail) else 0
        return subprocess.CompletedProcess(args, code, "", "boom" if code else "")


# --- the plan ---------------------------------------------------------------


def test_default_is_weekly(tmp_path):
    p = plan(tmp_path)
    assert p.frequency == "weekly"
    assert p.describe() == "every Sun at 10:17"
    assert plan(tmp_path, frequency="daily", hour=4, minute=5).describe() == (
        "every day at 04:05"
    )


def test_scheduled_command_is_one_the_real_cli_accepts(tmp_path):
    """Global flags must precede the subcommand, or the job fails every week."""
    p = plan(tmp_path, config=tmp_path / "my config.toml")
    assert p.command[:3] == ["/opt/venv/bin/python", "-m", "likesync"]
    args = build_parser().parse_args(p.command[3:])
    assert args.command == "sync"
    assert args.config == str(tmp_path / "my config.toml")
    assert args.log_file == str(p.log)


@pytest.mark.parametrize("bad", [
    {"frequency": "hourly"}, {"weekday": "funday"}, {"hour": 24}, {"minute": 60},
])
def test_bad_plans_are_rejected(tmp_path, bad):
    with pytest.raises(ConfigError):
        plan(tmp_path, **bad)


def test_parse_time():
    assert schedule.parse_time("10:17") == (10, 17)
    assert schedule.parse_time(" 4:05 ") == (4, 5)
    with pytest.raises(ConfigError):
        schedule.parse_time("teatime")


# --- unit text ---------------------------------------------------------------


@pytest.mark.parametrize("weekday,launchd_day", [("sun", 0), ("mon", 1), ("sat", 6)])
def test_launchd_plist_is_valid_and_uses_launchds_weekday_numbering(
    tmp_path, weekday, launchd_day
):
    p = plan(tmp_path, weekday=weekday, hour=9, minute=30)
    data = plistlib.loads(schedule.launchd_plist(p).encode())
    assert data["Label"] == "com.likesync.sync"
    assert data["ProgramArguments"] == p.command
    assert data["StartCalendarInterval"] == {
        "Weekday": launchd_day, "Hour": 9, "Minute": 30
    }
    assert data["EnvironmentVariables"]["LIKESYNC_HOME"] == str(p.home)
    assert data["StandardErrorPath"] == str(p.log)


def test_launchd_daily_has_no_weekday(tmp_path):
    data = plistlib.loads(
        schedule.launchd_plist(plan(tmp_path, frequency="daily")).encode()
    )
    assert "Weekday" not in data["StartCalendarInterval"]


def test_launchd_plist_escapes_awkward_paths(tmp_path):
    p = plan(tmp_path, home=tmp_path / "R&B <likes>")
    data = plistlib.loads(schedule.launchd_plist(p).encode())
    assert data["EnvironmentVariables"]["LIKESYNC_HOME"].endswith("R&B <likes>")


def test_systemd_units(tmp_path):
    service, timer = schedule.systemd_units(plan(tmp_path, weekday="wed", hour=7, minute=5))
    assert "OnCalendar=Wed *-*-* 07:05:00" in timer
    assert "Persistent=true" in timer
    assert "Type=oneshot" in service
    # The home directory contains a space, so it must be quoted for systemd.
    assert f'Environment="LIKESYNC_HOME={tmp_path / "ls home"}"' in service
    assert 'ExecStart=/opt/venv/bin/python -m likesync --log-file "' in service
    assert service.rstrip().endswith("SuccessExitStatus=3")


def test_systemd_daily_calendar(tmp_path):
    _, timer = schedule.systemd_units(plan(tmp_path, frequency="daily", hour=4, minute=0))
    assert "OnCalendar=*-*-* 04:00:00" in timer


@pytest.mark.parametrize("weekday,cron_day", [("sun", "0"), ("mon", "1"), ("sat", "6")])
def test_cron_line_uses_crons_weekday_numbering(tmp_path, weekday, cron_day):
    line = schedule.cron_line(plan(tmp_path, weekday=weekday, hour=10, minute=17))
    assert line.split()[:5] == ["17", "10", "*", "*", cron_day]
    assert line.endswith(schedule.CRON_MARK)
    assert f"LIKESYNC_HOME='{tmp_path / 'ls home'}'" in line


def test_cron_daily(tmp_path):
    line = schedule.cron_line(plan(tmp_path, frequency="daily"))
    assert line.split()[4] == "*"


def test_windows_task(tmp_path):
    p = plan(tmp_path, weekday="fri", hour=18, minute=0)
    batch = schedule.windows_batch(p)
    assert f'set "LIKESYNC_HOME={p.home}"' in batch
    assert '"-m" "likesync"' in batch
    args = schedule.schtasks_args(p, Path("C:/x/likesync-sync.cmd"))
    assert args[:2] == ["schtasks", "/Create"]
    assert args[args.index("/SC") + 1] == "WEEKLY"
    assert args[args.index("/D") + 1] == "FRI"
    assert args[args.index("/ST") + 1] == "18:00"


# --- installing ---------------------------------------------------------------


def test_cron_install_replaces_our_line_and_keeps_everyone_elses(tmp_path):
    run = FakeRun(crontab=f"0 3 * * * /usr/bin/backup\n1 1 * * 1 old {schedule.CRON_MARK}\n")
    notes = schedule.install(plan(tmp_path), kind="cron", run=run)

    lines = run.crontab.strip().splitlines()
    assert lines[0] == "0 3 * * * /usr/bin/backup"
    assert len(lines) == 2 and "old" not in lines[1]
    assert lines[1].startswith("17 10 * * 0 ")
    assert notes[0] == "likesync will sync every Sun at 10:17."


def test_cron_install_with_no_existing_crontab(tmp_path):
    run = FakeRun(crontab=None)
    schedule.install(plan(tmp_path), kind="cron", run=run)
    assert run.crontab.count(schedule.CRON_MARK) == 1


def test_cron_remove_keeps_other_lines(tmp_path):
    run = FakeRun(crontab=f"0 3 * * * /usr/bin/backup\n1 1 * * 1 x {schedule.CRON_MARK}\n")
    schedule.remove(kind="cron", user_home=tmp_path, run=run)
    assert run.crontab == "0 3 * * * /usr/bin/backup\n"
    assert schedule.status(kind="cron", run=run) == (False, "cron: not scheduled")


def test_systemd_install_writes_units_and_enables_the_timer(tmp_path):
    run = FakeRun()
    schedule.install(plan(tmp_path), kind="systemd", user_home=tmp_path, run=run)

    base = tmp_path / ".config" / "systemd" / "user"
    assert (base / "likesync.service").exists()
    assert "OnCalendar=Sun" in (base / "likesync.timer").read_text()
    assert ["systemctl", "--user", "daemon-reload"] in run.calls
    assert ["systemctl", "--user", "enable", "--now", "likesync.timer"] in run.calls
    assert schedule.status(kind="systemd", user_home=tmp_path, run=run)[0] is True

    schedule.remove(kind="systemd", user_home=tmp_path, run=run)
    assert not (base / "likesync.timer").exists()
    assert schedule.status(kind="systemd", user_home=tmp_path, run=run)[0] is False


def test_launchd_install_writes_the_agent_and_loads_it(tmp_path):
    run = FakeRun()
    notes = schedule.install(plan(tmp_path), kind="launchd", user_home=tmp_path, run=run)
    plist = tmp_path / "Library" / "LaunchAgents" / "com.likesync.sync.plist"
    assert plistlib.loads(plist.read_bytes())["Label"] == "com.likesync.sync"
    assert ["launchctl", "load", "-w", str(plist)] in run.calls
    assert any("wakes" in n for n in notes)


def test_a_failing_scheduler_command_is_reported(tmp_path):
    run = FakeRun(fail=("enable",))
    with pytest.raises(ConfigError, match="systemctl enable failed: boom"):
        schedule.install(plan(tmp_path), kind="systemd", user_home=tmp_path, run=run)


def test_install_creates_the_home_directory_for_the_log(tmp_path):
    p = plan(tmp_path)
    assert not p.home.exists()
    schedule.install(p, kind="cron", run=FakeRun())
    assert p.home.is_dir()
