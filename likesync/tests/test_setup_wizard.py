"""The setup wizard: what it does, what it skips, and where it has to stop."""

from __future__ import annotations

import tomllib

from likesync.config import load_config
from likesync.engine import LIKE, PlannedAction, RunReport
from likesync.errors import AuthError, ConfigError
from likesync.setup_wizard import Options, Steps, run_setup


class World:
    """Records what the wizard did, and plays the parts it cannot do itself."""

    def __init__(self, *, soundcloud=False, spotify=False, answers=(),
                 soundcloud_login_fails=None, probe="ready", planned=1):
        self.soundcloud = soundcloud
        self.spotify = spotify
        self.answers = list(answers)
        self.soundcloud_login_fails = soundcloud_login_fails
        self.probe = probe
        self.planned = planned
        self.events: list[str] = []
        self.said: list[str] = []
        self.plans: list = []

    def report(self, dry_run: bool) -> RunReport:
        report = RunReport(run_id=1, dry_run=dry_run)
        report.counts = {"spotify_library": 3, "soundcloud_library": 2}
        actions = [
            PlannedAction("spotify", f"sp{i}", LIKE, "added on soundcloud", f"Track {i}")
            for i in range(self.planned)
        ]
        report.planned = actions
        if not dry_run:
            report.applied = actions
        return report

    def steps(self) -> Steps:
        def soundcloud_login(cfg):
            self.events.append("soundcloud_login")
            if self.soundcloud_login_fails:
                raise self.soundcloud_login_fails
            self.soundcloud = True

        def spotify_login(cfg, manual):
            self.events.append(f"spotify_login(manual={manual})")
            self.spotify = True

        def ask(prompt):
            self.events.append("ask")
            return self.answers.pop(0) if self.answers else ""

        def install(plan):
            self.events.append("schedule")
            self.plans.append(plan)
            return [f"likesync will sync {plan.describe()}."]

        return Steps(
            soundcloud_ready=lambda cfg: self.soundcloud,
            soundcloud_login=soundcloud_login,
            soundcloud_probe=lambda cfg: self.probe,
            spotify_ready=lambda cfg: self.spotify,
            spotify_login=spotify_login,
            plan=lambda cfg: (self.events.append("plan"), self.report(True))[1],
            sync=lambda cfg: (self.events.append("sync"), self.report(False))[1],
            install_schedule=install,
            ask=ask,
            say=self.said.append,
        )

    @property
    def output(self) -> str:
        return "\n".join(self.said)


def fresh_cfg(tmp_path, body: str = ""):
    path = tmp_path / "config.toml"
    path.write_text(f'home = "{tmp_path}"\n{body}')
    return load_config(path), path


def test_everything_from_scratch_ends_scheduled_weekly(tmp_path):
    cfg, path = fresh_cfg(tmp_path)
    world = World(answers=["client-abc", "https://soundcloud.com/me/sets/in", "n"])
    code = run_setup(cfg, Options(), world.steps())

    assert code == 0
    data = tomllib.loads(path.read_text())
    assert data["spotify"]["client_id"] == "client-abc"
    assert data["soundcloud"]["playlist_url"] == "https://soundcloud.com/me/sets/in"
    assert world.events == [
        "ask", "ask", "soundcloud_login", "spotify_login(manual=False)",
        "plan", "ask", "schedule",
    ]
    # Weekly is the default, and the job is pinned to this config and home.
    plan = world.plans[0]
    assert plan.frequency == "weekly" and plan.describe() == "every Sun at 10:17"
    assert plan.config == path.resolve()
    assert plan.home == tmp_path.resolve()
    assert "All set" in world.output
    # Declined to apply now, so nothing was written by the wizard itself.
    assert "sync" not in world.events
    assert "first scheduled run applies" in world.output


def test_rerun_skips_everything_already_done(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n[soundcloud]\n'
                                 'playlist_url = "https://soundcloud.com/me/sets/in"\n')
    world = World(soundcloud=True, spotify=True, planned=0)
    assert run_setup(cfg, Options(), world.steps()) == 0
    # No questions, no sign-ins: just the look and the (idempotent) schedule.
    assert world.events == ["plan", "schedule"]
    assert "Already signed in." in world.output
    assert "Already connected." in world.output


def test_without_a_client_id_it_stops_short_and_says_what_is_needed(tmp_path):
    cfg, _ = fresh_cfg(tmp_path)
    world = World()
    code = run_setup(cfg, Options(interactive=False), world.steps())

    assert code == 2
    assert world.events == ["soundcloud_login"]      # never asks, never schedules
    assert "Still needed:" in world.output
    assert "--spotify-client-id" in world.output
    assert "Not scheduled yet" in world.output


def test_the_example_placeholder_counts_as_no_client_id(tmp_path):
    cfg, _ = fresh_cfg(
        tmp_path, '[spotify]\nclient_id = "PUT_THE_SPOTIFY_CLIENT_ID_HERE"\n'
    )
    world = World(soundcloud=True)
    assert run_setup(cfg, Options(interactive=False), world.steps()) == 2
    assert "spotify_login(manual=False)" not in world.events


def test_client_id_can_be_passed_as_a_flag(tmp_path):
    cfg, path = fresh_cfg(tmp_path)
    world = World(soundcloud=True)
    code = run_setup(
        cfg, Options(spotify_client_id="from-flag", interactive=False), world.steps()
    )
    assert code == 0
    assert tomllib.loads(path.read_text())["spotify"]["client_id"] == "from-flag"
    assert "ask" not in world.events


def test_a_machine_with_no_screen_reports_the_sign_in_as_outstanding(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n')
    world = World(
        spotify=True,
        soundcloud_login_fails=ConfigError("could not launch Chromium: no display"),
    )
    code = run_setup(cfg, Options(interactive=False), world.steps())
    assert code == 2
    assert "no display" in world.output
    assert "session export" in world.output
    assert "schedule" not in world.events


def test_an_expired_sign_in_is_caught_by_the_check(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n')
    world = World(soundcloud=True, spotify=True,
                  probe="not signed in - run: likesync login soundcloud --web")
    code = run_setup(cfg, Options(interactive=False), world.steps())
    assert code == 2
    assert "Check failed: not signed in" in world.output
    assert "plan" not in world.events and "schedule" not in world.events


def test_apply_flag_runs_the_first_sync(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n')
    world = World(soundcloud=True, spotify=True)
    assert run_setup(cfg, Options(interactive=False, apply=True), world.steps()) == 0
    assert world.events == ["plan", "sync", "schedule"]


def test_answering_yes_applies_the_plan(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n[soundcloud]\n'
                                 'playlist_url = "https://soundcloud.com/me/sets/in"\n')
    world = World(soundcloud=True, spotify=True, answers=["y"])
    assert run_setup(cfg, Options(), world.steps()) == 0
    assert world.events == ["plan", "ask", "sync", "schedule"]


def test_no_schedule_flag(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n')
    world = World(soundcloud=True, spotify=True)
    run_setup(cfg, Options(interactive=False, schedule=False), world.steps())
    assert "schedule" not in world.events


def test_daily_and_custom_time_reach_the_schedule(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n')
    world = World(soundcloud=True, spotify=True)
    run_setup(
        cfg,
        Options(interactive=False, frequency="daily", at="04:05"),
        world.steps(),
    )
    assert world.plans[0].describe() == "every day at 04:05"


def test_spotify_failure_does_not_stop_the_rest_being_reported(tmp_path):
    cfg, _ = fresh_cfg(tmp_path, '[spotify]\nclient_id = "abc"\n')
    world = World(soundcloud=True)
    steps = world.steps()

    def refuse(cfg, manual):
        raise AuthError("spotify refused authorization: access_denied")

    steps.spotify_login = refuse
    assert run_setup(cfg, Options(interactive=False), steps) == 2
    assert "access_denied" in world.output
    assert "Approve the Spotify consent screen" in world.output


def test_creates_a_config_file_when_there_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("LIKESYNC_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    world = World()
    run_setup(cfg, Options(interactive=False), world.steps())
    created = tmp_path / "home" / "config.toml"
    assert tomllib.loads(created.read_text())["soundcloud"]["mode"] == "web"
