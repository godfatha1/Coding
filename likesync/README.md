# likesync

Keeps your **Spotify Liked Songs** and **SoundCloud Likes** in sync, both
directions, once a day.

Like something on either service and it shows up on the other. Unlike it on
either and it goes from both. Runs unattended from cron, a systemd timer,
Docker or GitHub Actions, and is built to fail safe: a bad match or a truncated
read should never wipe a library.

**You do not need Spotify Premium and you do not need SoundCloud API approval.**
How that works, and what it costs you, is the next two sections.

- [What you need](#what-you-need)
- [SoundCloud web mode: read this](#soundcloud-web-mode-read-this)
- [How it works](#how-it-works)
- [Install](#install)
- [Set up Spotify](#set-up-spotify)
- [Set up SoundCloud](#set-up-soundcloud)
- [The inbox playlist](#the-inbox-playlist)
- [First run](#first-run)
- [Schedule it](#schedule-it)
- [Everyday commands](#everyday-commands)
- [How matching works](#how-matching-works)
- [Safety rails](#safety-rails)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## What you need

| | |
| --- | --- |
| **Spotify Premium** | Not yours. A development-mode Spotify app needs Premium on the account that **owns the app**, and allows up to 5 allow-listed users who do **not** need it. So anyone you know with Premium creates the app once and allow-lists you. Two minutes of their time, no money, no further involvement. |
| **SoundCloud API approval** | Not needed. SoundCloud stopped granting API applications years ago, so likesync drives soundcloud.com in a browser you sign in to yourself. See the warning below. |
| **A machine to run it on** | Anything always-ish on: a laptop, a Pi, a small VPS. SoundCloud web mode needs a browser, so a one-time graphical sign-in is required somewhere — you can then copy the session to a headless box. |
| **Python** | 3.11 or newer. |

One ongoing chore: **Spotify refresh tokens expire about six months after you
first authorise**, so roughly twice a year a run fails and you re-run
`likesync login spotify`. `likesync status` warns as the date approaches.

## SoundCloud web mode: read this

Because SoundCloud will not issue credentials, likesync does on your behalf
what you would do by hand: it opens your likes page in a real browser, reads
what is on it, and clicks the like button on a track page.

What that means, stated plainly:

- **It is contrary to SoundCloud's terms of service**, which prohibit
  automated access. It is your own account, your own library, nobody else's
  data, and the request rate is far below ordinary human browsing — but the
  decision is yours, and account action is a real if unlikely risk.
- **It can break.** Page markup is not a published interface. The extractor is
  written as layered selectors with a structural fallback, and if SoundCloud
  renames every CSS class it still works by finding the track link and the
  uploader link inside each row. But a big enough redesign will need a fix.
  `likesync probe soundcloud` tells you in one command whether it can still
  read the page.
- **What it never does**: it does not handle your password (you sign in
  yourself), and it does not read, extract or transmit any token or key. The
  only thing stored is the ordinary browser profile that keeps you signed in
  between runs, kept locally at mode `0600` and encrypted if you set
  `LIKESYNC_SECRET_KEY`.
- **It is slow on purpose.** Reads are one page load; each write is a page load
  plus a click, spaced `write_pause_s` apart. A daily sync of a handful of
  changes takes seconds to a couple of minutes.

If you would rather not: set `soundcloud.mode = "api"` and the credentialed
path is fully implemented and tested, waiting for the day approval arrives.
Alternatively **Soundiiz** and **TuneMyMusic** do automated SoundCloud↔Spotify
sync with proper API access for a few pounds a month, with nothing to maintain.
That is a perfectly good answer and cheaper than Premium.

## How it works

Two-way sync needs memory. If Spotify has a track and SoundCloud does not,
that is either *an addition on Spotify* or *a removal on SoundCloud* — and
nothing in the current state of the two libraries can tell you which. So every
run compares three things:

```
   what Spotify has now  ─┐
                          ├─→  added / removed on each side  ─→  plan  ─→  apply
 what SoundCloud has now ─┤
   what both had last run ┘          (state.sqlite3)
```

The previous run's snapshot lives in a local SQLite file, along with the track
pairings it has worked out. A run then:

1. Reads both libraries in full, and aborts rather than acting on a partial read.
2. Takes in anything new in the [inbox playlist](#the-inbox-playlist).
3. Diffs each library against the snapshot to get real additions and removals.
4. For anything new, finds its counterpart on the other service — by ISRC when
   both sides publish one, otherwise by searching and scoring the results.
5. Builds a plan, checks it against the [safety rails](#safety-rails), applies it.
6. Records the new snapshot — but only for changes that actually propagated, so
   a failed write is retried tomorrow instead of silently forgotten.

**The first run is a merge, not a sync.** With no snapshot everything looks
new, so both libraries end up with the union of the two. Nothing is ever
unliked on a first run. Use `likesync plan` first to see exactly what it would do.

## Install

```bash
git clone https://github.com/godfatha1/coding.git
cd coding/likesync
python3 -m venv venv
./venv/bin/pip install '.[web,crypt]'
./venv/bin/python -m playwright install chromium
```

`[web]` pulls in Playwright for SoundCloud web mode; `[crypt]` encrypts the
stored session and tokens at rest. In `api` mode likesync has **no runtime
dependencies at all** — plain `pip install .` is enough.

Then `./venv/bin/likesync --help`, or put the venv's `bin` on your `PATH`.

## Set up Spotify

**Ask someone with Spotify Premium to do steps 1–4.** It costs them nothing and
they never have to think about it again.

1. Go to <https://developer.spotify.com/dashboard> and create an app.
2. Set the redirect URI to exactly `http://127.0.0.1:8765/callback`.
   Spotify requires the loopback **IP**; `localhost` is rejected.
3. Request the scopes `user-library-read` and `user-library-modify`.
4. In the app's settings, under user management, **add your Spotify account**
   (the email on it). Then send you the **Client ID** — not the secret;
   likesync uses PKCE and does not want one.
5. You: put that client id in your config, then

```bash
likesync login spotify
```

That opens your browser, you approve it on your own free account, and the
tokens land in `~/.local/share/likesync/tokens.json` at mode `0600`.

## Set up SoundCloud

```bash
cp config.example.toml ~/.local/share/likesync/config.toml
$EDITOR ~/.local/share/likesync/config.toml   # paste the Spotify client id
likesync login soundcloud --web
```

A browser window opens on soundcloud.com. Sign in exactly as you normally
would, including Google or Apple sign-in. The window closes itself once it sees
you are signed in, and the browser profile is saved. Then check it:

```bash
likesync probe soundcloud
```

That is read-only. It confirms you are signed in, reads the first screen of
your likes page, and reports how many tracks it could parse — so you know the
extractor works before any syncing happens. If it reports that the extractor
needs updating, send that output.

### On a headless server

Sign in on a machine with a screen, then move the session across:

```bash
# on your laptop
likesync login soundcloud --web
likesync session export            # prints a base64 blob

# on the server
export LIKESYNC_SESSION='<the blob>'
likesync session import
likesync probe soundcloud
```

That blob is a live sign-in to your SoundCloud account. Treat it like a
password, and set `LIKESYNC_SECRET_KEY` on the server so it is encrypted at
rest.

## The inbox playlist

Optional, and worth it. Make a SoundCloud playlist, put its URL in
`soundcloud.playlist_url`, and it becomes an intake queue: **drop a track in
and the next run likes it on SoundCloud and mirrors it to Spotify.**

Useful because your likes are probably full of things that will never exist on
Spotify — bootlegs, unreleased edits, DJ mixes — whereas a playlist is explicit
curation.

It is deliberately **one-way**:

- Adding a track to the playlist likes it. From then on the ordinary two-way
  rules govern it, exactly as if you had liked it yourself.
- **Removing** a track from the playlist does nothing.
- A track already taken in is never reconsidered.

That last point is the important one. If playlist membership were treated as
part of the mirrored set, a playlist-only track could never be deleted: you
unlike it on Spotify, the removal propagates to SoundCloud, the playlist still
lists it, and the next run puts it back — for ever. The one-way intake is what
avoids that, and there is a test named after the scenario.

If you do want the whole playlist taken in again:

```bash
likesync reseed      # warns you first: this re-likes tracks you may have removed
```

## First run

Always look before you leap:

```bash
likesync plan
```

That reads both libraries, works out the pairings and prints exactly what it
would change, writing nothing:

```
likesync run #1 (dry run) (first run — baseline merge)
=====================================================
Libraries: Spotify 842 liked, SoundCloud 611 liked

Planned: 94 likes, 0 unlikes
    + soundcloud  Burial — Archangel
    + spotify     M83 — Midnight City
    ...

Needs review (12) — close but below the accept threshold:
  • [0.71] Four Tet — Love Cry (Extended)
       maybe: Four Tet — Love Cry
       confirm: likesync link soundcloud 5512 3aBc9

No counterpart found (170) — will retry periodically:
  • soundcloud: Some Bootleg Nobody Uploaded
  ...

! First run: treating both libraries as additions and merging them.
```

Review that, then do it for real:

```bash
likesync sync
```

If you do not like the result, `likesync undo` reverses the whole run.

## Schedule it

All of these just run `likesync sync` once a day.

### systemd (recommended on Linux)

```bash
sudo cp deploy/likesync.service deploy/likesync.timer /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/likesync.service   # fix paths and the user
sudo systemctl enable --now likesync.timer
systemctl list-timers likesync.timer                # confirm the next run
journalctl -u likesync.service -n 50                # read the last run
```

`Persistent=true` means a machine asleep at the scheduled time catches up on
wake, and a randomised delay keeps you off the hour.

### cron

See `deploy/crontab.example`. cron runs with almost no environment, so set
`LIKESYNC_HOME` explicitly and use absolute paths.

### macOS

See `deploy/com.likesync.daily.plist`; copy it to `~/Library/LaunchAgents/`
and `launchctl load` it.

### Docker

The image bundles Chromium for web mode.

```bash
docker build -t likesync ./likesync
docker run --rm -v likesync-data:/data \
  -e LIKESYNC_SPOTIFY_CLIENT_ID=... \
  likesync sync
```

Sign in on your laptop and `likesync session import` into the volume, since the
container has no display.

### GitHub Actions

`.github/workflows/likesync.yml` runs it daily with no machine of your own.
Note the trade-off documented in the workflow: tokens and the browser session
have to survive between runs, so it commits them — AES-GCM encrypted under
`LIKESYNC_SECRET_KEY`, and the job refuses to run without that key. **If this
repository is public, that encrypted blob is publicly readable.** For a public
repo, prefer your own machine or a private repo.

## Everyday commands

```bash
likesync status            # connections, library sizes, inbox, last run
likesync probe soundcloud  # read-only: is the session good, can it read the page
likesync plan              # what a sync would do, writing nothing
likesync sync              # do it
likesync sync -v           # ... and show the reasoning per track
likesync sync --json       # machine-readable report
likesync runs              # recent run history

likesync unmatched            # tracks with no counterpart found
likesync unmatched --review   # near-misses waiting on your call
likesync link soundcloud 5512 3aBc9   # confirm a pairing by hand
likesync unlink spotify 3aBc9         # forget a wrong pairing

likesync ignore soundcloud 912 --reason "90 minute DJ set"
likesync unignore soundcloud 912

likesync reseed            # take the whole inbox playlist in again
likesync session export    # move the SoundCloud sign-in to another machine
likesync undo              # reverse the last run
```

Useful flags on `sync`:

| Flag | Effect |
| --- | --- |
| `--dry-run` | plan only |
| `--direction to-spotify` | one-way: SoundCloud is the source of truth |
| `--direction to-soundcloud` | one-way the other way |
| `--no-unlikes` | propagate likes only, never removals |
| `--force-unlikes` | apply unlikes past `max_unlikes_per_run` |
| `--force-shrink` | proceed even if a library shrank suspiciously |

Exit codes: `0` fine, `2` config or auth problem, `3` some writes failed (they
retry tomorrow), `4` a safety rail aborted the run.

## How matching works

Spotify gives you clean artist and title fields. SoundCloud gives you
`"Fred again.. - Delilah (pull me out of this) [Skrillex Remix] [FREE
DOWNLOAD]"` posted by a promo channel. Bridging that is the hard part.

**ISRC first.** When both sides publish an ISRC, an exact match is accepted
immediately. (Web mode cannot see ISRCs — the page does not show them — so this
only applies in `api` mode.)

**Otherwise, parse and score.** Titles are decomposed into:

- the actual title, after stripping promo noise (`Official Video`, `[FREE
  DOWNLOAD]`, `HQ`, `Out Now`, `Premiere`, `2016 Remaster`, `Original Mix`, …)
- credited names, including `feat.` artists pulled out of the title
- the artist, parsed from `"Artist - Title"` when the uploader is just a label
- **version markers**, which matter most

The score weighs title, artist and duration similarity. But the score alone
never decides — these **vetoes** come first, and any one of them rejects a pair
outright however well it scores:

- **Version mismatch.** A remix never matches the original. Nor does a
  bootleg, flip, VIP, dub, mashup, cover, live take, acoustic, instrumental,
  karaoke, demo or nightcore edit.
- **Different remixer.** A Skrillex remix never matches a Noisia remix.
- **Duration too far apart.** Beyond roughly 15 seconds (scaling with length,
  capped at 45), it is a different recording — this is what stops hour-long DJ
  sets matching the track they are named after.
- **Artist or title too weak** on their own.

At or above `accept_threshold` (0.78) a pair is linked automatically. Between
`review_threshold` (0.62) and that it is parked for you to confirm with
`likesync link`. Below, it is reported unmatched and retried every couple of
weeks, since the track may simply not be uploaded yet.

Pairings are remembered, so this happens once per track, not daily. If you see
a wrong pairing, `likesync unlink` it and `likesync link` the right one; manual
links are never overwritten by the matcher.

## Safety rails

Delete propagation is the dangerous half of two-way sync, so it is fenced in.

- **Library shrinkage abort.** If a library comes back with under half of what
  it had last run, the run aborts and writes nothing. A truncated read is
  indistinguishable from a mass unlike. Override with `--force-shrink` once
  you have checked.
- **Incomplete reads fail loudly.** In web mode, if the likes page is still
  loading more when the scroll cap is hit, the run errors instead of treating
  the half-loaded list as your library.
- **Unlike cap.** More than `max_unlikes_per_run` (50) planned removals and all
  of them are skipped with a warning rather than applied. They are *held back*,
  not dropped: raise the limit or pass `--force-unlikes` and the next run
  applies them.
- **No unlikes on the first run.** There is no baseline to diff against.
- **The snapshot only advances past changes that landed.** A failed write, a
  held-back unlike or a truncated plan leaves that track's baseline untouched,
  so the next run sees the change again and retries. Without this, one failed
  write would diverge the two libraries permanently.
- **Writes verify themselves.** In web mode a like is read back off the button
  before it counts as done, and liking something already liked is a no-op
  rather than a toggle.
- **Per-track failures are isolated.** One unlikeable track does not abort the
  run, and a rejected Spotify batch is retried one id at a time to find the
  culprit.
- **`likesync undo`** reverses any run, then re-reads both libraries so the
  next run starts from the truth.
- **Write and search budgets** keep a run inside both services' limits.

Every run is recorded — the plan, what was applied, what failed and why — in
`state.sqlite3`, readable with `likesync runs` and `likesync sync --json`.

## Limitations

- **Liked/saved tracks only.** Playlists, albums and followed artists are out
  of scope (the inbox playlist is an input, not a sync target).
- **Much of SoundCloud has no Spotify counterpart.** Bootlegs, unreleased
  edits, DJ mixes and self-released demos simply do not exist there. They stay
  in `likesync unmatched` permanently, and that is the correct answer. Set
  `skip_longer_than_ms` to keep long mixes out of the matcher.
- **Web mode has no ISRC**, so matching leans entirely on title, artist and
  duration. It is the same matcher, just without the shortcut.
- **SoundCloud does not expose when you liked something.** A genuine conflict —
  the same track liked on one side and unliked on the other since the last run
  — cannot be resolved by recency. The default `like_wins` keeps the track and
  reports the conflict.
- **A newly discovered pairing counts as an addition.** If a track sat
  unmatched on Spotify for a month and then appears on SoundCloud, likesync
  links them and likes it. It has no record of a counterpart existing before,
  so it cannot tell that from a new like.
- **Switching modes needs re-linking.** `api` mode identifies tracks by URN
  (`soundcloud:tracks:12345`), web mode by permalink
  (`soundcloud:permalink:burial/archangel`), because the page does not reliably
  expose numeric ids. A state file written in one mode cannot address tracks in
  the other; it will say so clearly rather than misbehaving. Start fresh with a
  new `LIKESYNC_HOME` if you switch.
- **Spotify local files cannot sync.** They have no API id, and are skipped.
- **One account pair per state file.** Use separate `LIKESYNC_HOME`
  directories for separate pairs.

## Troubleshooting

**`likesync probe soundcloud` says "not signed in".** The browser profile
expired, which happens every few months. Re-run `likesync login soundcloud
--web`.

**Probe says the extractor needs updating.** SoundCloud changed their markup
enough to defeat both the class selectors and the structural fallback. Send the
probe output; the fix is contained in one JavaScript block in
`providers/soundcloud_web.py`.

**The run errors about the scroll limit.** Your likes list is longer than
`max_scrolls` rounds can load. Raise `soundcloud.max_scrolls`. It fails rather
than syncing a partial library on purpose.

**`403` on a Spotify like or unlike.** Your client id is subject to the
February 2026 endpoint changes, which replaced `PUT /me/tracks` with
`PUT /me/library`. likesync probes both and remembers which works, so this
usually resolves itself; otherwise pin `spotify.write_mode = "library"`. A 403
can also mean the **app owner's** Premium lapsed.

**Spotify says `invalid_grant`.** The refresh token expired — expected roughly
every six months. Run `likesync login spotify` again.

**A wrong pairing got made.** `likesync unlink <provider> <id>`, then
`likesync link` the right one, or `likesync ignore` the track. Raise
`accept_threshold` if it recurs.

**The run aborted on library shrinkage.** That is the rail working. Check the
library really did shrink, then re-run with `--force-shrink`.

## Development

```bash
cd likesync
python3 -m pip install -e '.[dev,crypt]'
python3 -m pytest              # 177 tests, no network
```

The tests run entirely offline. Provider tests script HTTP at the transport
seam and assert on real request shapes — paths, query strings, batch sizes,
fallback order. The SoundCloud web provider is harder: soundcloud.com is not
reachable from CI, so its DOM extractor is exercised **against representative
markup rendered in real Chromium**, including a like button that genuinely
toggles. That proves the JavaScript works — selectors, structural fallback,
click-and-verify — not merely the Python around it.

```
src/likesync/
  matching.py           title parsing, version vetoes, scoring  <- read first
  engine.py             three-way reconciliation, inbox, rails
  state.py              SQLite: snapshot, pairings, inbox memory, run log
  providers/
    spotify.py          official API
    soundcloud.py       official API (for if approval ever arrives)
    soundcloud_web.py   the DOM extractor and click-to-like
  websession.py         signed-in Chromium, persisted profile
  oauth.py              PKCE, loopback redirect, refresh under lock
  tokens.py             token storage, optional AES-GCM at rest
  httpc.py              stdlib HTTP with retries + the test seam
  cli.py                commands
```

Both services change more often than you would like, so endpoint behaviour is
probed and remembered rather than hardcoded, and base URLs, auth schemes, write
modes and selectors are all replaceable. If something moves, it should be a
config change or one patch, not a rewrite.

## Licence

MIT.
