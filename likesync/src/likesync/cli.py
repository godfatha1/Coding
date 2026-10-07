"""likesync command line."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .app import build_app
from .config import Config, load_config
from .errors import AuthError, ConfigError, LikeSyncError, QuotaExceeded
from .models import PROVIDERS, SPOTIFY, other_provider
from .report import to_json, to_text

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_PARTIAL = 3
EXIT_ABORTED = 4

log = logging.getLogger("likesync")


def setup_logging(verbose: int, quiet: bool, log_file: str | None) -> None:
    if quiet:
        level = logging.WARNING
    elif verbose >= 2:
        level = logging.DEBUG
    else:
        level = logging.INFO
    handlers: list[logging.Handler] = []
    if log_file:
        Path(log_file).expanduser().parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(Path(log_file).expanduser()))
    stream = logging.StreamHandler(sys.stderr)
    handlers.append(stream)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    # The library's own chatter is useful; third-party noise is not.
    logging.getLogger("likesync.http").setLevel(
        logging.DEBUG if verbose >= 2 else logging.WARNING
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="likesync",
        description="Two-way sync between Spotify and SoundCloud liked songs.",
    )
    parser.add_argument("--version", action="version", version=f"likesync {__version__}")
    parser.add_argument("-c", "--config", help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--log-file", help="also write logs here")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="connect a service")
    p.add_argument("provider", choices=PROVIDERS)
    p.add_argument("--manual", action="store_true",
                   help="print the URL and paste the redirect back (headless boxes)")
    p.add_argument("--no-browser", action="store_true",
                   help="do not try to open a browser")

    p = sub.add_parser("logout", help="forget stored tokens for a service")
    p.add_argument("provider", choices=PROVIDERS)

    sub.add_parser("status", help="show connection and sync state")

    for name, helptext in (
        ("sync", "reconcile both libraries"),
        ("plan", "show what a sync would do, without writing (alias for --dry-run)"),
    ):
        p = sub.add_parser(name, help=helptext)
        if name == "sync":
            p.add_argument("--dry-run", action="store_true",
                           help="plan only; write nothing")
        p.add_argument("--direction", choices=("both", "to-spotify", "to-soundcloud"),
                       help="override sync.direction")
        p.add_argument("--no-unlikes", action="store_true",
                       help="propagate likes only, never removals")
        p.add_argument("--force-unlikes", action="store_true",
                       help="apply unlikes even past max_unlikes_per_run")
        p.add_argument("--force-shrink", action="store_true",
                       help="proceed even if a library shrank suspiciously")
        p.add_argument("--json", action="store_true", help="emit the report as JSON")

    p = sub.add_parser("unmatched", help="tracks with no counterpart")
    p.add_argument("--review", action="store_true",
                   help="show near-misses awaiting confirmation instead")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("link", help="manually pair two tracks")
    p.add_argument("provider", choices=PROVIDERS, help="which service track_id is on")
    p.add_argument("track_id")
    p.add_argument("counterpart_id", help="the id on the other service")

    p = sub.add_parser("unlink", help="remove a pairing")
    p.add_argument("provider", choices=PROVIDERS)
    p.add_argument("track_id")

    p = sub.add_parser("ignore", help="never sync a track")
    p.add_argument("provider", choices=PROVIDERS)
    p.add_argument("track_id")
    p.add_argument("--reason", default="")

    p = sub.add_parser("unignore", help="stop ignoring a track")
    p.add_argument("provider", choices=PROVIDERS)
    p.add_argument("track_id")

    p = sub.add_parser("undo", help="reverse the writes of a run")
    p.add_argument("--run", type=int, help="run id (default: the most recent)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    p = sub.add_parser("runs", help="recent run history")
    p.add_argument("-n", type=int, default=10)

    p = sub.add_parser("tokens", help="move credentials between machines/CI")
    p.add_argument("action", choices=("export", "path"))

    return parser


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_login(args: argparse.Namespace, cfg: Config) -> int:
    cfg.validate(providers=(args.provider,))
    app = build_app(cfg)
    try:
        auth = app.auth[args.provider]
        tokens = auth.login(manual=args.manual, open_browser=not args.no_browser)
        scope = tokens.scope or "(none reported)"
        print(f"\nConnected to {args.provider}. Scopes: {scope}")
        print(f"Tokens stored in {cfg.token_path}")
        if args.provider == SPOTIFY:
            print(
                "Note: Spotify refresh tokens expire about six months after "
                "consent, so expect to re-run this roughly twice a year."
            )
    finally:
        app.close()
    return EXIT_OK


def cmd_logout(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        if app.tokens.forget(args.provider):
            print(f"Forgot {args.provider} tokens.")
        else:
            print(f"No stored tokens for {args.provider}.")
    finally:
        app.close()
    return EXIT_OK


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        print(f"likesync {__version__}")
        print(f"  state      {cfg.state_path}")
        print(f"  tokens     {cfg.token_path}")
        if cfg.loaded_from:
            print(f"  config     {cfg.loaded_from}")
        else:
            print("  config     (defaults; no config.toml found)")
        print(f"  direction  {cfg.sync.direction}"
              f"   unlikes: {'on' if cfg.sync.propagate_unlikes else 'off'}")
        print()
        for name in PROVIDERS:
            tokens = app.tokens.load(name)
            if not tokens.usable:
                print(f"  {name:<11} not connected — run: likesync login {name}")
                continue
            if tokens.expires_at:
                remaining = tokens.expires_at - time.time()
                when = (
                    f"expires in {remaining / 60:.0f} min"
                    if remaining > 0 else "expired (will refresh)"
                )
            else:
                when = "no expiry recorded"
            age = tokens.consent_age_days()
            extra = ""
            if age is not None:
                extra = f", consent {age:.0f}d old"
                if name == SPOTIFY and age > 150:
                    extra += " — nearing Spotify's ~180d refresh limit"
            print(f"  {name:<11} connected ({when}{extra})")
            print(f"              mirror: {len(app.store.mirror(name))} tracks")
        print()
        print(f"  links      {app.store.count_links()} paired tracks")
        print(f"  unmatched  {len(app.store.attempts_by_status('unmatched'))}")
        print(f"  review     {len(app.store.attempts_by_status('review'))}")

        last = app.store.last_run()
        if last:
            print()
            print(f"  last run   #{last['id']} {last['status']} at "
                  f"{last['finished_at'] or last['started_at']}")
            print(f"             {last['stats']}")
        else:
            print()
            print("  last run   never — try: likesync plan")
    finally:
        app.close()
    return EXIT_OK


def cmd_sync(args: argparse.Namespace, cfg: Config) -> int:
    dry_run = getattr(args, "dry_run", False) or args.command == "plan"
    if args.direction:
        cfg.sync.direction = args.direction
    if args.no_unlikes:
        cfg.sync.propagate_unlikes = False
    cfg.sync.force_unlikes = args.force_unlikes
    cfg.sync.force_shrink = args.force_shrink
    cfg.validate()

    app = build_app(cfg)
    try:
        report = app.engine().run(dry_run=dry_run)
    finally:
        app.close()

    if args.json:
        print(to_json(report))
    else:
        print(to_text(report, verbose=args.verbose > 0))

    if report.aborted:
        return EXIT_ABORTED
    if report.failed:
        return EXIT_PARTIAL
    return EXIT_OK


def cmd_unmatched(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        status = "review" if args.review else "unmatched"
        rows = app.store.attempts_by_status(status)
        if args.json:
            import json

            print(json.dumps([
                {
                    "provider": r["provider"],
                    "track_id": r["track_id"],
                    "label": app.store.describe(r["provider"], r["track_id"]),
                    "best_score": r["best_score"],
                    "best_candidate": r["best_candidate"],
                    "note": r["note"],
                    "tries": r["tries"],
                    "last_try": r["last_try"],
                }
                for r in rows
            ], indent=2, ensure_ascii=False))
            return EXIT_OK

        if not rows:
            print(f"Nothing in the {status} list.")
            return EXIT_OK
        print(f"{len(rows)} {status} track(s):\n")
        for r in rows:
            label = app.store.describe(r["provider"], r["track_id"])
            print(f"  {r['provider']:<11} {label}")
            if r["best_candidate"]:
                other = other_provider(r["provider"])
                print(f"              best guess [{r['best_score']:.2f}]: "
                      f"{app.store.describe(other, r['best_candidate'])}")
                print(f"              confirm: likesync link {r['provider']} "
                      f"{r['track_id']} {r['best_candidate']}")
            if r["note"]:
                print(f"              {r['note']}")
            print(f"              tries: {r['tries']}, last: {r['last_try']}")
    finally:
        app.close()
    return EXIT_OK


def cmd_link(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        if args.provider == SPOTIFY:
            spotify_id, soundcloud_id = args.track_id, args.counterpart_id
        else:
            spotify_id, soundcloud_id = args.counterpart_id, args.track_id
        app.store.put_link(spotify_id, soundcloud_id, score=1.0,
                           method="manual", manual=True)
        print(f"Linked spotify:{spotify_id} <-> soundcloud:{soundcloud_id}")
        print("The next sync will treat them as the same track.")
    finally:
        app.close()
    return EXIT_OK


def cmd_unlink(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        if app.store.delete_link(args.provider, args.track_id):
            print(f"Removed the link for {args.provider}:{args.track_id}")
        else:
            print("No such link.")
            return EXIT_ERROR
    finally:
        app.close()
    return EXIT_OK


def cmd_ignore(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        app.store.add_ignore(args.provider, args.track_id, args.reason)
        print(f"Ignoring {args.provider}:{args.track_id}. It will not be synced "
              "in either direction.")
    finally:
        app.close()
    return EXIT_OK


def cmd_unignore(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        if app.store.remove_ignore(args.provider, args.track_id):
            print(f"No longer ignoring {args.provider}:{args.track_id}")
        else:
            print("That track was not ignored.")
            return EXIT_ERROR
    finally:
        app.close()
    return EXIT_OK


def cmd_undo(args: argparse.Namespace, cfg: Config) -> int:
    cfg.validate()
    app = build_app(cfg)
    try:
        row = app.store.run(args.run) if args.run else app.store.last_run()
        if row is None:
            print("No run to undo.")
            return EXIT_ERROR
        if row["dry_run"]:
            print(f"Run #{row['id']} was a dry run; there is nothing to undo.")
            return EXIT_OK
        actions = app.store.actions(row["id"], only_applied=True)
        if not actions:
            print(f"Run #{row['id']} made no changes that are still un-reverted.")
            return EXIT_OK

        print(f"Run #{row['id']} will be reversed: {len(actions)} action(s).")
        for a in actions[:20]:
            back = "unlike" if a["action"] == "like" else "like"
            print(f"  {back} on {a['provider']}: "
                  f"{app.store.describe(a['provider'], a['track_id'])}")
        if len(actions) > 20:
            print(f"  ... and {len(actions) - 20} more")

        if not args.yes:
            answer = input("\nProceed? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Cancelled.")
                return EXIT_OK

        failures = 0
        for a in actions:
            provider = app.providers[a["provider"]]
            reverse = provider.unlike if a["action"] == "like" else provider.like
            try:
                done = reverse([a["track_id"]])
            except LikeSyncError as exc:
                log.error("undo failed for %s: %s", a["track_id"], exc)
                failures += 1
                continue
            if done:
                app.store.mark_reverted(a["id"])
            else:
                failures += 1

        # Re-baseline from what the services actually hold now, so the next
        # run does not try to re-apply what we just reversed.
        for name, provider in app.providers.items():
            app.store.set_mirror(name, [t.id for t in provider.liked()])
        print(f"\nReversed {len(actions) - failures} of {len(actions)} action(s).")
        if failures:
            print(f"{failures} could not be reversed; see the log.")
            return EXIT_PARTIAL
    finally:
        app.close()
    return EXIT_OK


def cmd_runs(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        rows = app.store.recent_runs(args.n)
        if not rows:
            print("No runs yet.")
            return EXIT_OK
        for r in rows:
            flag = " (dry)" if r["dry_run"] else ""
            print(f"#{r['id']:<4} {r['status']:<8}{flag:<6} "
                  f"{r['started_at']} -> {r['finished_at'] or '...'}")
            print(f"      {r['stats']}")
    finally:
        app.close()
    return EXIT_OK


def cmd_tokens(args: argparse.Namespace, cfg: Config) -> int:
    app = build_app(cfg)
    try:
        if args.action == "path":
            print(cfg.token_path)
            return EXIT_OK
        blob = app.tokens.export_b64()
        if not blob or blob == "e30=":
            print("No tokens stored yet.", file=sys.stderr)
            return EXIT_ERROR
        print(blob)
        print(
            "\nStore this as the LIKESYNC_TOKENS secret. It grants library "
            "access to both accounts — treat it like a password.",
            file=sys.stderr,
        )
    finally:
        app.close()
    return EXIT_OK


COMMANDS = {
    "login": cmd_login,
    "logout": cmd_logout,
    "status": cmd_status,
    "sync": cmd_sync,
    "plan": cmd_sync,
    "unmatched": cmd_unmatched,
    "link": cmd_link,
    "unlink": cmd_unlink,
    "ignore": cmd_ignore,
    "unignore": cmd_unignore,
    "undo": cmd_undo,
    "runs": cmd_runs,
    "tokens": cmd_tokens,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose, args.quiet, args.log_file)
    try:
        cfg = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return COMMANDS[args.command](args, cfg)
    except (ConfigError, AuthError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return EXIT_CONFIG
    except QuotaExceeded as exc:
        print(f"\n{exc}", file=sys.stderr)
        return EXIT_ERROR
    except LikeSyncError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
