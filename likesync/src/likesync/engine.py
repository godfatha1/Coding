"""Two-way reconciliation between two liked-songs libraries.

The core problem: if Spotify has a track and SoundCloud does not, that is
either an addition on Spotify or a removal on SoundCloud. Only the previous
run's snapshot (``Store.mirror``) distinguishes them, so every decision here is
a three-way comparison between "what Spotify has now", "what SoundCloud has
now" and "what both had last time".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .config import SyncConfig
from .errors import AbortRun
from .matching import score_pair, search_queries
from .models import SOUNDCLOUD, SPOTIFY, Track, other_provider
from .providers.base import Provider
from .state import Store, utcnow

log = logging.getLogger("likesync.engine")

LIKE = "like"
UNLIKE = "unlike"


@dataclass
class PlannedAction:
    provider: str          # where the write happens
    track_id: str          # id on that provider
    action: str            # LIKE | UNLIKE
    reason: str
    label: str = ""
    # The change that caused this action. If the write does not land, the
    # baseline for this source id is held back so the change is re-detected
    # next run instead of being silently forgotten.
    source_provider: str = ""
    source_id: str = ""

    def describe(self) -> str:
        verb = "like" if self.action == LIKE else "unlike"
        return f"{verb} on {self.provider}: {self.label or self.track_id} ({self.reason})"


@dataclass
class RunReport:
    run_id: int = 0
    dry_run: bool = False
    direction: str = "both"
    first_run: bool = False
    started_at: str = field(default_factory=utcnow)
    finished_at: str = ""
    aborted: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    planned: list[PlannedAction] = field(default_factory=list)
    applied: list[PlannedAction] = field(default_factory=list)
    failed: list[tuple[PlannedAction, str]] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    new_links: int = 0
    searches: int = 0
    # Planned work that did not get applied this run (rails, truncation).
    deferred: list[PlannedAction] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.aborted:
            return "aborted"
        if self.failed:
            return "partial"
        return "ok"

    def stats(self) -> dict:
        return {
            **self.counts,
            "planned": len(self.planned),
            "applied": len(self.applied),
            "failed": len(self.failed),
            "unmatched": len(self.unmatched),
            "review": len(self.review),
            "conflicts": len(self.conflicts),
            "new_links": self.new_links,
            "searches": self.searches,
            "dry_run": int(self.dry_run),
        }


class SyncEngine:
    def __init__(
        self,
        *,
        store: Store,
        spotify: Provider,
        soundcloud: Provider,
        cfg: SyncConfig,
    ) -> None:
        self.store = store
        self.cfg = cfg
        self.providers: dict[str, Provider] = {
            SPOTIFY: spotify,
            SOUNDCLOUD: soundcloud,
        }
        self._searches = 0
        self._tracks: dict[tuple[str, str], Track] = {}

    # -- helpers -----------------------------------------------------------

    def _can_write(self, provider: str) -> bool:
        if self.cfg.direction == "both":
            return True
        if self.cfg.direction == "to-spotify":
            return provider == SPOTIFY
        return provider == SOUNDCLOUD

    def _label(self, provider: str, track_id: str) -> str:
        track = self._tracks.get((provider, track_id))
        if track:
            return track.display()
        return self.store.describe(provider, track_id)

    def _search_budget_left(self) -> bool:
        return self._searches < self.cfg.max_searches_per_run

    def _stale_attempt(self, provider: str, track_id: str) -> bool:
        """True when this track is due another matching attempt."""
        row = self.store.attempt(provider, track_id)
        if row is None:
            return True
        if row["status"] == "matched":
            return False
        try:
            last = datetime.fromisoformat(row["last_try"])
        except (TypeError, ValueError):
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        due = last + timedelta(days=self.cfg.retry_unmatched_after_days)
        return datetime.now(UTC) >= due

    # -- matching ----------------------------------------------------------

    def resolve(self, track: Track, report: RunReport) -> str | None:
        """Find ``track``'s counterpart, searching the other service if needed."""
        target_name = other_provider(track.provider)
        existing = self.store.counterpart(track.provider, track.id)
        if existing:
            return existing
        if not self._stale_attempt(track.provider, track.id):
            return None
        if not self._search_budget_left():
            return None

        target = self.providers[target_name]
        best: tuple[float, Track, str] | None = None

        candidates: list[Track] = []
        if track.isrc and target_name == SPOTIFY:
            self._searches += 1
            candidates.extend(target.search_isrc(track.isrc, limit=5))

        queries = search_queries(track, target=target_name)
        query_index = 0
        while True:
            for candidate in candidates:
                if self.store.counterpart(target_name, candidate.id):
                    # Already spoken for by another track; a 1:1 link only.
                    continue
                score = score_pair(
                    track,
                    candidate,
                    duration_tolerance_ms=self.cfg.duration_tolerance_ms,
                )
                if score.blocked:
                    continue
                if best is None or score.value > best[0]:
                    best = (score.value, candidate, ", ".join(score.reasons))
            if best and best[0] >= self.cfg.accept_threshold:
                break
            if query_index >= len(queries) or not self._search_budget_left():
                break
            self._searches += 1
            candidates = target.search(queries[query_index], limit=self.cfg.search_limit)
            self._tracks.update({c.key: c for c in candidates})
            query_index += 1

        if best and best[0] >= self.cfg.accept_threshold:
            score, candidate, why = best
            spotify_id = track.id if track.provider == SPOTIFY else candidate.id
            soundcloud_id = candidate.id if track.provider == SPOTIFY else track.id
            self.store.cache_track(candidate)
            self.store.put_link(
                spotify_id, soundcloud_id, score=score, method="fuzzy"
            )
            report.new_links += 1
            log.info(
                "linked %s <-> %s (%.2f: %s)",
                track.display(), candidate.display(), score, why,
            )
            return candidate.id

        if best and best[0] >= self.cfg.review_threshold:
            score, candidate, why = best
            self.store.cache_track(candidate)
            self.store.record_attempt(
                track.provider, track.id, status="review",
                best_score=score, best_candidate=candidate.id, note=why,
            )
            report.review.append(
                {
                    "provider": track.provider,
                    "track_id": track.id,
                    "label": track.display(),
                    "score": round(score, 3),
                    "candidate": candidate.display(),
                    "candidate_id": candidate.id,
                    "note": why,
                }
            )
            return None

        score = best[0] if best else 0.0
        note = best[2] if best else "no candidate cleared the gates"
        self.store.record_attempt(
            track.provider, track.id, status="unmatched",
            best_score=score, best_candidate=best[1].id if best else "", note=note,
        )
        report.unmatched.append(
            {
                "provider": track.provider,
                "track_id": track.id,
                "label": track.display(),
                "score": round(score, 3),
                "note": note,
                "url": track.url or "",
            }
        )
        return None

    # -- the run -----------------------------------------------------------

    def run(self, *, dry_run: bool = False) -> RunReport:
        report = RunReport(dry_run=dry_run, direction=self.cfg.direction)
        report.run_id = self.store.start_run(dry_run=dry_run)
        try:
            self._run(report)
        except AbortRun as exc:
            report.aborted = str(exc)
            log.error("run aborted: %s", exc)
        finally:
            report.finished_at = utcnow()
            self.store.finish_run(
                report.run_id, status=report.status, stats=report.stats()
            )
        return report

    def _run(self, report: RunReport) -> None:
        cfg = self.cfg
        report.first_run = not self.store.has_mirror()

        # --- phase 1: read both libraries -------------------------------
        current: dict[str, list[Track]] = {}
        for name, provider in self.providers.items():
            log.info("reading %s likes", name)
            tracks = provider.liked()
            current[name] = tracks
            log.info("%s: %d liked tracks", name, len(tracks))

        ignored = {n: self.store.ignored(n) for n in self.providers}
        filtered: dict[str, dict[str, Track]] = {}
        for name, tracks in current.items():
            keep: dict[str, Track] = {}
            for track in tracks:
                if track.id in ignored[name]:
                    continue
                if (
                    cfg.skip_longer_than_ms
                    and track.duration_ms
                    and track.duration_ms > cfg.skip_longer_than_ms
                ):
                    continue
                keep[track.id] = track
            filtered[name] = keep
            self._tracks.update({t.key: t for t in keep.values()})
        self.store.cache_tracks([t for side in filtered.values() for t in side.values()])

        now_ids = {name: set(side) for name, side in filtered.items()}
        prev_ids = {name: self.store.mirror(name) for name in self.providers}
        report.counts = {
            "spotify_library": len(now_ids[SPOTIFY]),
            "soundcloud_library": len(now_ids[SOUNDCLOUD]),
        }

        # --- phase 2: safety rail on library shrinkage -------------------
        # A truncated read would look like a mass unlike, so refuse to act on
        # a library that lost a large fraction of its tracks since last run.
        for name in self.providers:
            before, after = len(prev_ids[name]), len(now_ids[name])
            if (
                not cfg.force_shrink
                and before >= 10
                and after < before * cfg.min_library_ratio
            ):
                raise AbortRun(
                    f"{name} reported {after} liked tracks but had {before} last "
                    f"run (below the {cfg.min_library_ratio:.0%} floor). Nothing "
                    "was changed. If the drop is real, re-run with "
                    "--force-shrink; otherwise check the API/account first."
                )

        added: dict[str, set[str]] = {}
        removed: dict[str, set[str]] = {}
        for name in self.providers:
            if report.first_run:
                added[name] = set(now_ids[name])
                removed[name] = set()
            else:
                added[name] = now_ids[name] - prev_ids[name]
                removed[name] = prev_ids[name] - now_ids[name]
        report.counts.update(
            {
                "spotify_added": len(added[SPOTIFY]),
                "spotify_removed": len(removed[SPOTIFY]),
                "soundcloud_added": len(added[SOUNDCLOUD]),
                "soundcloud_removed": len(removed[SOUNDCLOUD]),
            }
        )
        if report.first_run:
            report.warnings.append(
                "First run: treating both libraries as additions and merging "
                "them. No unlikes are possible until a baseline exists."
            )

        # --- phase 3: resolve links --------------------------------------
        # Fresh additions first, then the backlog of never-matched tracks, so a
        # tight search budget is spent on what changed today.
        plan: list[PlannedAction] = []
        consider: dict[str, list[str]] = {name: [] for name in self.providers}
        for name in self.providers:
            fresh = [i for i in added[name] if i in filtered[name]]
            backlog = [
                i
                for i in now_ids[name] - added[name]
                if not self.store.counterpart(name, i)
                and self._stale_attempt(name, i)
            ]
            consider[name] = fresh + backlog

        for name, track_ids in consider.items():
            target = other_provider(name)
            for track_id in track_ids:
                track = filtered[name].get(track_id)
                if track is None:
                    continue
                counterpart = self.resolve(track, report)
                if counterpart is None:
                    continue
                if counterpart in now_ids[target]:
                    continue  # both sides already have it
                if counterpart in removed[target]:
                    # Added here, removed there, in the same window.
                    message = (
                        f"{self._label(name, track_id)}: added on {name} but "
                        f"removed on {target}"
                    )
                    report.conflicts.append(message)
                    if cfg.conflict != "like_wins":
                        continue
                if not self._can_write(target):
                    continue
                plan.append(
                    PlannedAction(
                        provider=target,
                        track_id=counterpart,
                        action=LIKE,
                        reason=f"added on {name}",
                        label=self._label(target, counterpart),
                        source_provider=name,
                        source_id=track_id,
                    )
                )

        # --- phase 4: removals -------------------------------------------
        unlikes: list[PlannedAction] = []
        if cfg.propagate_unlikes:
            for name in self.providers:
                target = other_provider(name)
                for track_id in removed[name]:
                    counterpart = self.store.counterpart(name, track_id)
                    if not counterpart:
                        continue
                    if counterpart not in now_ids[target]:
                        continue  # already gone on the other side
                    if counterpart in added[target]:
                        message = (
                            f"{self._label(name, track_id)}: removed on {name} "
                            f"but added on {target}"
                        )
                        report.conflicts.append(message)
                        if cfg.conflict == "like_wins" and self._can_write(name):
                            plan.append(
                                PlannedAction(
                                    provider=name,
                                    track_id=track_id,
                                    action=LIKE,
                                    reason=f"re-added from {target} (conflict)",
                                    label=self._label(name, track_id),
                                )
                            )
                        continue
                    if not self._can_write(target):
                        continue
                    unlikes.append(
                        PlannedAction(
                            provider=target,
                            track_id=counterpart,
                            action=UNLIKE,
                            reason=f"unliked on {name}",
                            label=self._label(target, counterpart),
                            source_provider=name,
                            source_id=track_id,
                        )
                    )
        elif any(removed.values()):
            report.warnings.append(
                "sync.propagate_unlikes is off; removals were not mirrored."
            )

        # Blast-radius rail: an unexpected pile of unlikes is far more likely
        # to be a matching or API fault than a real intent, so drop them all
        # and let a human look rather than deleting a library.
        if len(unlikes) > cfg.max_unlikes_per_run and not cfg.force_unlikes:
            report.warnings.append(
                f"Planned {len(unlikes)} unlikes, over the "
                f"max_unlikes_per_run limit of {cfg.max_unlikes_per_run}. All "
                "unlikes were skipped. Review with `likesync plan`, then raise "
                "the limit or run with --force-unlikes if this is expected."
            )
            report.deferred.extend(unlikes)
            unlikes = []
        plan.extend(unlikes)

        # Likes are safer than unlikes, so keep them when truncating.
        plan.sort(key=lambda a: 0 if a.action == LIKE else 1)
        if len(plan) > cfg.max_writes_per_run:
            report.warnings.append(
                f"Planned {len(plan)} writes, truncated to "
                f"max_writes_per_run={cfg.max_writes_per_run}. The rest will "
                "be picked up by the next run."
            )
            report.deferred.extend(plan[cfg.max_writes_per_run :])
            plan = plan[: cfg.max_writes_per_run]

        # Deduplicate: the same track can be reached from both directions.
        seen: set[tuple[str, str, str]] = set()
        deduped: list[PlannedAction] = []
        for action in plan:
            key = (action.provider, action.track_id, action.action)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(action)
        report.planned = deduped
        report.searches = self._searches

        # --- phase 5: apply ----------------------------------------------
        if report.dry_run:
            report.warnings.append("Dry run: nothing was written.")
            return
        self._apply(report)

        # --- phase 6: record the new baseline ----------------------------
        # The baseline may only advance past a change whose propagation
        # actually landed. For anything still outstanding -- a failed write, an
        # unlike the rail dropped, a truncated plan -- the source side is
        # pinned to its previous value so the same diff is recomputed next run.
        # Without this, a single failed write diverges the two libraries
        # permanently, because neither side ever looks "changed" again.
        outstanding: dict[str, set[str]] = {name: set() for name in self.providers}
        for action in [*report.deferred, *(a for a, _ in report.failed)]:
            if action.source_provider in outstanding:
                outstanding[action.source_provider].add(action.source_id)

        for name in self.providers:
            expected = set(now_ids[name])
            for action in report.applied:
                if action.provider != name:
                    continue
                if action.action == LIKE:
                    expected.add(action.track_id)
                else:
                    expected.discard(action.track_id)
            for track_id in outstanding[name]:
                if track_id in prev_ids[name]:
                    expected.add(track_id)      # removal not yet mirrored
                else:
                    expected.discard(track_id)  # addition not yet mirrored
            self.store.set_mirror(name, expected)

    def _apply(self, report: RunReport) -> None:
        grouped: dict[tuple[str, str], list[PlannedAction]] = {}
        for action in report.planned:
            grouped.setdefault((action.provider, action.action), []).append(action)

        for (provider_name, action_name), actions in grouped.items():
            provider = self.providers[provider_name]
            ids = [a.track_id for a in actions]
            log.info("%s: %s %d track(s)", provider_name, action_name, len(ids))
            method = provider.like if action_name == LIKE else provider.unlike
            succeeded = set(method(ids))
            failures = getattr(provider, "failures", {}) or {}
            for action in actions:
                if action.track_id in succeeded:
                    report.applied.append(action)
                    self.store.record_action(
                        report.run_id, provider_name, action.track_id,
                        action_name, "ok", action.reason,
                    )
                else:
                    detail = failures.get(action.track_id, "not confirmed by provider")
                    report.failed.append((action, detail))
                    self.store.record_action(
                        report.run_id, provider_name, action.track_id,
                        action_name, "failed", detail,
                    )
