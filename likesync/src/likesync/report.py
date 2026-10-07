"""Human- and machine-readable run reports."""

from __future__ import annotations

import json
from typing import Any

from .engine import LIKE, RunReport

BULLET = "  • "


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def to_dict(report: RunReport) -> dict[str, Any]:
    return {
        "run_id": report.run_id,
        "status": report.status,
        "dry_run": report.dry_run,
        "direction": report.direction,
        "first_run": report.first_run,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "aborted": report.aborted,
        "counts": report.counts,
        "planned": [
            {
                "provider": a.provider,
                "track_id": a.track_id,
                "action": a.action,
                "reason": a.reason,
                "label": a.label,
            }
            for a in report.planned
        ],
        "applied": [
            {"provider": a.provider, "track_id": a.track_id, "action": a.action,
             "label": a.label}
            for a in report.applied
        ],
        "failed": [
            {"provider": a.provider, "track_id": a.track_id, "action": a.action,
             "label": a.label, "error": err}
            for a, err in report.failed
        ],
        "unmatched": report.unmatched,
        "review": report.review,
        "conflicts": report.conflicts,
        "warnings": report.warnings,
        "deferred": [
            {"provider": a.provider, "track_id": a.track_id, "action": a.action,
             "label": a.label, "reason": a.reason}
            for a in report.deferred
        ],
        "new_links": report.new_links,
        "searches": report.searches,
    }


def to_json(report: RunReport) -> str:
    return json.dumps(to_dict(report), indent=2, ensure_ascii=False)


def to_text(report: RunReport, *, verbose: bool = False) -> str:
    out: list[str] = []
    head = f"likesync run #{report.run_id}"
    if report.dry_run:
        head += " (dry run)"
    if report.first_run:
        head += " (first run — baseline merge)"
    out.append(head)
    out.append("=" * len(head))

    counts = report.counts
    out.append(
        f"Libraries: Spotify {counts.get('spotify_library', 0)} liked, "
        f"SoundCloud {counts.get('soundcloud_library', 0)} liked"
    )
    if not report.first_run:
        out.append(
            f"Since last run: Spotify +{counts.get('spotify_added', 0)}/"
            f"-{counts.get('spotify_removed', 0)}, "
            f"SoundCloud +{counts.get('soundcloud_added', 0)}/"
            f"-{counts.get('soundcloud_removed', 0)}"
        )

    if report.aborted:
        out.append("")
        out.append(f"ABORTED: {report.aborted}")
        return "\n".join(out)

    # Count what actually happened. Reporting planned totals under an
    # "Applied" heading would hide failures.
    counted = report.planned if report.dry_run else report.applied
    likes = [a for a in counted if a.action == LIKE]
    unlikes = [a for a in counted if a.action != LIKE]
    verb = "Planned" if report.dry_run else "Applied"
    applied_ids = {(a.provider, a.track_id, a.action) for a in report.applied}

    out.append("")
    out.append(
        f"{verb}: {_plural(len(likes), 'like')}, {_plural(len(unlikes), 'unlike')}"
        + (f", {_plural(len(report.failed), 'failure')}" if report.failed else "")
    )

    show = report.planned if (verbose or report.dry_run) else report.applied
    limit = None if verbose else 40
    for action in show[:limit]:
        mark = " " if report.dry_run else (
            "✓" if (action.provider, action.track_id, action.action) in applied_ids
            else "✗"
        )
        arrow = "+" if action.action == LIKE else "-"
        out.append(f"  {mark} {arrow} {action.provider:<11} {action.label}")
        if verbose:
            out.append(f"       {action.reason}")
    if limit and len(show) > limit:
        out.append(f"  ... and {len(show) - limit} more")

    if report.failed:
        out.append("")
        out.append(f"Failures ({len(report.failed)}):")
        for action, err in report.failed[: None if verbose else 10]:
            out.append(f"{BULLET}{action.provider} {action.action} {action.label}: {err}")

    if report.conflicts:
        out.append("")
        out.append(f"Conflicts ({len(report.conflicts)}) — changed on both sides:")
        for line in report.conflicts[: None if verbose else 10]:
            out.append(f"{BULLET}{line}")

    if report.review:
        out.append("")
        out.append(
            f"Needs review ({len(report.review)}) — close but below the "
            "accept threshold:"
        )
        for item in report.review[: None if verbose else 10]:
            out.append(
                f"{BULLET}[{item['score']:.2f}] {item['label']}"
                f"\n       maybe: {item['candidate']}"
                f"\n       confirm: likesync link {item['provider']} "
                f"{item['track_id']} {item['candidate_id']}"
            )

    if report.unmatched:
        out.append("")
        out.append(
            f"No counterpart found ({len(report.unmatched)}) — will retry "
            "periodically:"
        )
        for item in report.unmatched[: None if verbose else 10]:
            out.append(f"{BULLET}{item['provider']}: {item['label']}")
        if not verbose and len(report.unmatched) > 10:
            out.append("       ... see `likesync unmatched` for the full list")

    if report.new_links:
        out.append("")
        out.append(f"New track links: {report.new_links} ({report.searches} searches)")

    if report.deferred:
        out.append("")
        out.append(
            f"Deferred ({len(report.deferred)}) — held back this run, "
            "will be retried:"
        )
        for action in report.deferred[: None if verbose else 10]:
            arrow = "+" if action.action == LIKE else "-"
            out.append(f"{BULLET}{arrow} {action.provider}: {action.label}")

    if report.warnings:
        out.append("")
        for line in report.warnings:
            out.append(f"! {line}")

    return "\n".join(out)
