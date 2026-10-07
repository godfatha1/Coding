"""Cross-platform track matching.

SoundCloud and Spotify describe the same recording very differently. SoundCloud
titles carry the artist ("Artist - Title (Remixer Remix) [FREE DOWNLOAD]") and
the uploader is often a label or promo channel; Spotify keeps artist and title
in separate fields and appends things like "- 2011 Remaster".

The job here is to decide when two descriptions mean the same recording, and --
far more important for an unattended job -- when to refuse to guess. A wrong
match propagates an unlike to the other service, so the gates below are
deliberately allowed to veto a high-scoring pair.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from .models import Track

# --------------------------------------------------------------------------
# Version markers
#
# DROP   segments that carry no information about which recording this is.
# SOFT   segments that distinguish recordings weakly; a mismatch costs points.
# HARD   segments that make it a genuinely different recording; a mismatch is a
#        veto no matter how well everything else lines up.
# --------------------------------------------------------------------------

_DROP_PATTERNS = (
    r"original\s+mix",
    r"original\s+version",
    r"original",
    r"\d{0,4}\s*re-?master(ed)?(\s*\d{4})?",
    r"explicit",
    r"clean",
    r"album\s+version",
    r"single\s+version",
    r"bonus\s+track",
    r"official\s*(music)?\s*(video|audio|visualizer|lyric\s*video)?",
    r"officiel",
    r"lyrics?",
    r"lyric\s*video",
    r"audio",
    r"video",
    r"visuali[sz]er",
    r"hq|hd|320|wav|flac",
    r"free\s*(dl|download)",
    r"free",
    r"out\s*now",
    r"buy\s*now",
    r"download",
    r"premiere",
    r"exclusive",
    r"forthcoming",
    r"snippet",
    r"teaser",
    r"preview",
    r"master",
    r"full\s*track",
    r"stream",
    r"support",
    r"tracklist",
    r"\d{4}",
)

_SOFT_TAGS = {
    "radio": r"radio\s*(edit|mix|version)?",
    "extended": r"extended\s*(mix|edit|version)?",
    "club": r"club\s*(mix|edit|version)",
    "short": r"short\s*(edit|version)",
    "sped": r"sped\s*up|speed\s*up",
    "slowed": r"slowed(\s*(down|reverb|\+\s*reverb))?",
}

# Remix-like markers. The capture group, where present, is the remixer name.
_HARD_TAGS = {
    "remix": r"(?:(.*?)\s+)?re-?mix(?:ed)?",
    "bootleg": r"(?:(.*?)\s+)?bootleg",
    "flip": r"(?:(.*?)\s+)?flip",
    "refix": r"(?:(.*?)\s+)?re-?fix",
    "rework": r"(?:(.*?)\s+)?re-?work",
    "edit": r"(.+?)\s+edit",  # a *named* edit only; bare "edit" is handled as soft
    "vip": r"(?:(.*?)\s+)?v\.?i\.?p\.?(?:\s*mix)?",
    "dub": r"(?:(.*?)\s+)?dub(?:\s*mix)?",
    "mashup": r"(?:(.*?)\s+)?mash-?up",
    "cover": r"(?:(.*?)\s+)?cover",
    "live": r"live(?:\s*(?:at|from|in)\s+.*)?",
    "acoustic": r"acoustic",
    "instrumental": r"instrumental",
    "karaoke": r"karaoke",
    "demo": r"demo",
    "nightcore": r"nightcore",
    "unplugged": r"unplugged",
    "orchestral": r"orchestral",
    "reprise": r"reprise",
    "intro": r"intro\s*(edit|mix)",
}

_BRACKET_RE = re.compile(r"[\(\[\{]\s*([^\(\)\[\]\{\}]*?)\s*[\)\]\}]")
_FEAT_RE = re.compile(
    r"\b(?:feat|ft|featuring|w/|with)\.?\s+(.+?)"
    r"(?=$|[\(\)\[\]\{\}]|\s[-–—|]\s|,\s*(?:feat|ft)\b)",
    re.IGNORECASE,
)
_DASH_SPLIT_RE = re.compile(r"\s+[-–—―|]\s+|\s+//\s+")
_DROP_RE = re.compile(
    "^(?:" + "|".join(_DROP_PATTERNS) + ")$", re.IGNORECASE
)
_BARE_EDIT_RE = re.compile(r"^edit$", re.IGNORECASE)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def norm_text(text: str) -> str:
    """Fold a title or artist name to comparable tokens."""
    if not text:
        return ""
    out = strip_accents(text).lower()
    out = out.replace("&", " and ").replace("+", " and ")
    out = re.sub(r"[’'`´]", "", out)
    out = re.sub(r"\bpt\.?\s*(\d+)\b", r"part \1", out)
    out = re.sub(r"\bvol\.?\s*(\d+)\b", r"volume \1", out)
    out = re.sub(r"[^a-z0-9]+", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _token_set_ratio(a: str, b: str) -> float:
    """rapidfuzz's token_set_ratio, implemented on difflib."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    rest_a = " ".join(sorted(ta - tb))
    rest_b = " ".join(sorted(tb - ta))
    whole_a = f"{inter} {rest_a}".strip()
    whole_b = f"{inter} {rest_b}".strip()
    scores = [_ratio(whole_a, whole_b)]
    if inter:
        scores.append(_ratio(inter, whole_a))
        scores.append(_ratio(inter, whole_b))
    return max(scores)


def similarity(a: str, b: str) -> float:
    """Order-insensitive similarity of two already-normalised strings."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = a.split(), b.split()
    sorted_a, sorted_b = " ".join(sorted(ta)), " ".join(sorted(tb))
    score = max(_ratio(a, b), _ratio(sorted_a, sorted_b), _token_set_ratio(a, b))

    # token_set_ratio scores "love" against "love story" as a perfect match,
    # because the intersection is one side in full. Damp that case: a short
    # title that is a strict subset of a much longer one is suspicious.
    small, large = sorted((len(set(ta)), len(set(tb))))
    if small <= 2 and large - small >= 2:
        score *= 0.85
    return min(score, 1.0)


@dataclass(frozen=True)
class TitleParts:
    """A title decomposed into the parts worth comparing."""

    base: str                      # normalised title, version markers removed
    hard: frozenset[str] = frozenset()
    soft: frozenset[str] = frozenset()
    remixers: tuple[str, ...] = ()
    featured: tuple[str, ...] = ()
    leading_artist: str | None = None   # from an "Artist - Title" split


def _classify_segment(segment: str) -> tuple[str | None, str | None, str | None]:
    """Return (hard_tag, soft_tag, remixer) for one bracketed/dashed segment."""
    seg = segment.strip(" -–—|")
    if not seg:
        return None, None, None
    if _DROP_RE.match(seg):
        return None, None, None
    if _BARE_EDIT_RE.match(seg):
        return None, "radio", None
    for tag, pattern in _HARD_TAGS.items():
        m = re.fullmatch(pattern, seg, re.IGNORECASE)
        if m:
            remixer = None
            if m.groups():
                remixer = (m.group(1) or "").strip() or None
            return tag, None, remixer
    for tag, pattern in _SOFT_TAGS.items():
        if re.fullmatch(pattern, seg, re.IGNORECASE):
            return None, tag, None
    return None, None, None


def parse_title(
    title: str, *, split_leading_artist: bool = False
) -> TitleParts:
    """Decompose a title into base text, version markers and credited names.

    ``split_leading_artist`` turns "Artist - Title" into an artist candidate and
    a bare title. Use it for SoundCloud, where the uploader field is unreliable.
    """
    if not title:
        return TitleParts(base="")

    work = title
    hard: set[str] = set()
    soft: set[str] = set()
    remixers: list[str] = []
    featured: list[str] = []

    # "feat. X" can sit anywhere; pull it out before anything else so it does
    # not get mistaken for part of the title or a version marker.
    def _take_feat(text: str) -> str:
        nonlocal featured
        while True:
            m = _FEAT_RE.search(text)
            if not m:
                return text
            names = re.split(r"\s*(?:,|&|\band\b|\bx\b|\+)\s*", m.group(1))
            featured.extend(n.strip() for n in names if n.strip())
            text = (text[: m.start()] + " " + text[m.end() :]).strip()

    work = _take_feat(work)

    # Bracketed segments.
    def _eat_bracket(m: re.Match[str]) -> str:
        inner = _take_feat(m.group(1))
        h, s, remixer = _classify_segment(inner)
        if h:
            hard.add(h)
            if remixer:
                remixers.append(remixer)
            return " "
        if s:
            soft.add(s)
            return " "
        if _DROP_RE.match(inner.strip()) or not inner.strip():
            return " "
        # Unrecognised bracket content is part of the title ("(I Can't Get No)").
        return f" {inner} "

    work = _BRACKET_RE.sub(_eat_bracket, work)

    # Dash-delimited segments: "Title - Remixer Remix", and for SoundCloud
    # "Artist - Title". Walk from the right while segments look like markers.
    pieces = [p for p in _DASH_SPLIT_RE.split(work) if p.strip()]
    while len(pieces) > 1:
        h, s, remixer = _classify_segment(pieces[-1])
        if h:
            hard.add(h)
            if remixer:
                remixers.append(remixer)
            pieces.pop()
            continue
        if s:
            soft.add(s)
            pieces.pop()
            continue
        if _DROP_RE.match(pieces[-1].strip()):
            pieces.pop()
            continue
        break

    leading_artist = None
    if split_leading_artist and len(pieces) > 1:
        leading_artist = pieces[0].strip() or None
        pieces = pieces[1:]

    base = norm_text(" ".join(pieces))
    # A named edit whose "remixer" is really the whole title means the segment
    # was not a version marker at all; keep the tag but drop the bogus name.
    remixers = [r for r in (norm_text(x) for x in remixers) if r]
    return TitleParts(
        base=base,
        hard=frozenset(hard),
        soft=frozenset(soft),
        remixers=tuple(remixers),
        featured=tuple(norm_text(f) for f in featured if norm_text(f)),
        leading_artist=norm_text(leading_artist) if leading_artist else None,
    )


def artist_candidates(track: Track, parts: TitleParts) -> list[str]:
    """Every plausible normalised artist string for a track, best guess first."""
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        n = norm_text(value or "")
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    # When the only name we have is an uploader handle, the artist parsed from
    # "Artist - Title" is the better first guess.
    if track.extra.get("artist_source") == "uploader":
        add(parts.leading_artist)
    for a in track.artists:
        add(a)
    if len(track.artists) > 1:
        add(", ".join(track.artists))
    add(parts.leading_artist)
    for f in parts.featured:
        add(f)
    if track.artists and parts.featured:
        add(" ".join([*track.artists, *parts.featured]))
    return out


def duration_limits(a_ms: int, b_ms: int) -> int:
    """Hard rejection threshold for a duration gap, in milliseconds."""
    longer = max(a_ms, b_ms)
    return int(min(max(15_000, longer * 0.06), 45_000))


def _should_split(track: Track) -> bool:
    return track.provider == "soundcloud" or not track.artists


@dataclass
class MatchScore:
    value: float
    method: str = "fuzzy"
    blocked: bool = False
    reasons: list[str] = field(default_factory=list)

    def note(self, reason: str) -> None:
        self.reasons.append(reason)

    def block(self, reason: str) -> MatchScore:
        self.blocked = True
        self.note(reason)
        return self


def score_pair(
    a: Track,
    b: Track,
    *,
    duration_tolerance_ms: int = 10_000,
    title_floor: float = 0.50,
    artist_floor: float = 0.35,
) -> MatchScore:
    """Score two tracks as the same recording.

    Returns a score in 0..1. ``blocked`` means "never accept this pair", which
    takes precedence over the numeric score.
    """
    if a.isrc and b.isrc and a.isrc.upper() == b.isrc.upper():
        return MatchScore(1.0, method="isrc", reasons=[f"ISRC {a.isrc.upper()}"])

    # SoundCloud keeps the artist inside the title even when an uploader name
    # exists, so always split there; elsewhere only split when no artist field
    # came through.
    pa = parse_title(a.raw_title or a.title, split_leading_artist=_should_split(a))
    pb = parse_title(b.raw_title or b.title, split_leading_artist=_should_split(b))

    score = MatchScore(0.0)

    # --- veto gates -------------------------------------------------------
    if pa.hard != pb.hard:
        only = sorted(pa.hard ^ pb.hard)
        return score.block(f"version mismatch ({', '.join(only)})")

    # A remix only matches the same remixer's version of it.
    remix_like = {"remix", "bootleg", "edit"} & pa.hard
    if remix_like and pa.remixers and pb.remixers:
        best_remixer = max(
            similarity(x, y) for x in pa.remixers for y in pb.remixers
        )
        if best_remixer < 0.60:
            return score.block(
                f"different remixer ({'/'.join(pa.remixers)} vs "
                f"{'/'.join(pb.remixers)})"
            )
        score.note(f"remixer match {best_remixer:.2f}")

    title_sim = similarity(pa.base, pb.base)
    if title_sim < title_floor:
        return score.block(f"title {title_sim:.2f} below floor")

    cands_a = artist_candidates(a, pa)
    cands_b = artist_candidates(b, pb)
    if cands_a and cands_b:
        artist_sim = max(similarity(x, y) for x in cands_a for y in cands_b)
    else:
        # One side gave us nothing to compare; neutral rather than free points.
        artist_sim = 0.5
        score.note("no artist on one side")
    if cands_a and cands_b and artist_sim < artist_floor:
        return score.block(f"artist {artist_sim:.2f} below floor")

    if a.duration_ms and b.duration_ms:
        gap = abs(a.duration_ms - b.duration_ms)
        hard_gap = duration_limits(a.duration_ms, b.duration_ms)
        if gap > hard_gap:
            return score.block(f"duration differs by {gap / 1000:.0f}s")
        if gap <= 2_000:
            dur_sim = 1.0
        elif gap <= duration_tolerance_ms:
            span = max(duration_tolerance_ms - 2_000, 1)
            dur_sim = 1.0 - 0.4 * (gap - 2_000) / span
        else:
            span = max(hard_gap - duration_tolerance_ms, 1)
            dur_sim = 0.6 - 0.6 * (gap - duration_tolerance_ms) / span
        score.note(f"duration Δ{gap / 1000:.1f}s")
    else:
        dur_sim = 0.55
        score.note("duration unknown")

    value = 0.50 * title_sim + 0.33 * artist_sim + 0.17 * dur_sim

    soft_diff = pa.soft ^ pb.soft
    if soft_diff:
        value -= 0.06 * len(soft_diff)
        score.note(f"soft version diff ({', '.join(sorted(soft_diff))})")

    score.value = max(0.0, min(1.0, value))
    score.note(f"title {title_sim:.2f}, artist {artist_sim:.2f}")
    return score


def search_queries(track: Track, *, target: str) -> list[str]:
    """Search strings to try against ``target``, most precise first."""
    parts = parse_title(
        track.raw_title or track.title, split_leading_artist=_should_split(track)
    )
    title = parts.base
    artists = artist_candidates(track, parts)
    primary = artists[0] if artists else ""

    version = " ".join(sorted(parts.hard))
    remixer = parts.remixers[0] if parts.remixers else ""
    titled = " ".join(x for x in (title, remixer, version) if x).strip()

    queries: list[str] = []

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in queries:
            queries.append(q)

    if target == "spotify":
        if primary and title:
            add(f'track:"{titled}" artist:"{primary}"')
            add(f"{primary} {titled}")
        add(titled)
        if parts.leading_artist and parts.leading_artist != primary:
            add(f"{parts.leading_artist} {titled}")
    else:
        if primary and titled:
            add(f"{primary} {titled}")
        add(titled)
        if title != titled:
            add(f"{primary} {title}" if primary else title)
    return queries[:4]
