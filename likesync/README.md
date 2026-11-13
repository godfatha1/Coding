# likesync

Keeps your **Spotify Liked Songs** and **SoundCloud Likes** in sync, both
directions, once a week.

Like something on either service and it shows up on the other. Unlike it on
either and it goes from both. It installs its own weekly job, runs unattended,
and is built to fail safe: a bad match or a truncated
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
| **A machine to run it on** | Anything on for part of most weeks: a laptop is fine (a missed run is caught up on wake), or a Pi or small VPS. SoundCloud web mode needs a browser, so a one-time graphical sign-in is required somewhere — you can then copy the session to a headless box. |
| **Python** | 3.11 or newer. |

One ongoing chore: **Spotify refresh tokens expire about six months after you
first authorise**, so roughly twice a year a run fails and you re-run
`likesync login spotify`. `likesync status` warns as the date approaches.

## SoundCloud web mode: read this

Because SoundCloud will not issue credentials, likesync does on your behalf
what you would do by hand: it opens your likes page in a real browser and
scrolls it, and it opens a track's page and clicks the like button.

What that means, stated plainly:

- **It is contrary to SoundCloud's terms of service**, which prohibit
  automated access. It is your own account, your own library, nobody else's
  data, and the pace is far below ordinary human browsing — but the decision is
  yours, and account action is a real if unlikely risk.
- **How it reads.** As you scroll your likes, the site loads them in batches;
  likesync reads those batches as they arrive. They carry what the visible
  rows do not — each track's duration, its ISRC where there is one, the real
  artist — and they say outright when the list has ended. If that data is not
  seen, it falls back to reading the visible rows, which works but matches
  less well (rows show no duration at all; the site draws it on the waveform).
- **What it never does.** It does not handle your password: you sign in
  yourself. It does not read, extract or transmit any token or key. It never
  sends a request of its own to SoundCloud's servers — it navigates, scrolls
  and clicks, and reads what the page loaded. The only thing stored is the
  ordinary browser profile that keeps you signed in between runs, kept locally
  at mode `0600` and encrypted if you set `LIKESYNC_SECRET_KEY`.
- **It can break.** None of this is a published interface. There are three
  layers before it gives up (page data, visible rows, your public likes page),
  and `likesync probe soundcloud` reports which of them work. But a big enough
  redesign will need a fix.
- **It is slow on purpose.** Reading a library is one page and some scrolling
  (about 150 likes in 12 seconds). Each write is a page load plus a click,
  spaced `write_pause_s` apart.

### What has been checked against the real site

Run against live soundcloud.com in November 2026, signed out, on public pages:
reading a 148-track likes list both ways (the two agree exactly), the
end-of-list detection, search, a playlist, the sign-in redirect, and locating
the like button on a track page.

**Not checked, because it needs your sign-in:** your own library page
(`/you/likes`), and actually clicking like. Those are covered by tests against
pages modelled on the real ones, and `likesync probe soundcloud` plus
`likesync plan` are how you confirm them on your account before anything is
written.

That live run is also what caught two faults a markup-only reader has: a liked
*playlist* sits in the same list as liked tracks and lists its own tracks
inside its row, so they get counted as likes; and a track page has like buttons
for *related* tracks too, so "the like button" is ambiguous unless it is scoped
to the track itself. Both are handled, and there are tests named after them.

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
   a failed write is retried on the next run instead of silently forgotten.

**The first run is a merge, not a sync.** With no snapshot everything looks
new, so both libraries end up with the union of the two. Nothing is ever
unliked on a first run. Use `likesync plan` first to see exactly what it would do.

## Install

One command, on the computer that will run it (macOS or Linux; Python 3.11+):

```bash
git clone -b ccr-0fca9d99-eer2q5 https://github.com/godfatha1/Coding.git
cd Coding/likesync && ./setup.sh
```

`setup.sh` installs likesync into a private `./venv`, fetches the browser it
drives, and hands over to `likesync setup`, which walks through five steps and
skips any that are already done:

1. **Configuration** — creates the config file; asks for the Spotify Client ID
   and, optionally, an inbox playlist.
2. **SoundCloud** — opens a browser window for you to sign in, then checks it
   can read your likes page.
3. **Spotify** — opens the consent screen for you to approve.
4. **First look** — shows exactly what the first sync would change, and asks
   before writing anything.
5. **Schedule** — installs the weekly job (Sundays 10:17 by default).

The only things it cannot do for you are the two sign-ins. Run it again any
time — after a sign-in expires, or once the Client ID arrives — and it picks up
where it stopped:

```bash
./setup.sh --spotify-client-id abc123 --weekday sat --at 09:00
```

On Windows there is no `setup.sh`; do the same by hand:

```
py -m venv venv
venv\Scripts\python -m pip install -e ".[web,crypt]"
venv\Scripts\python -m playwright install chromium
venv\Scripts\python -m likesync setup
```

Windows scheduling uses Task Scheduler and has not been tested on a real
machine; macOS and Linux are the tested paths.

The rest of this page explains each step, for when you want to do one by hand.

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

`likesync setup` does this for you. To do it, change it or undo it by hand:

```bash
likesync schedule install                       # weekly: Sundays at 10:17
likesync schedule install --weekday wed --at 07:30
likesync schedule install --daily --at 04:05    # if weekly is not often enough
likesync schedule show                          # print what it would install
likesync schedule status
likesync schedule remove
```

It picks whatever the machine has, per-user, with no administrator rights:

| Machine | What gets installed | If the machine is off at the time |
| --- | --- | --- |
| macOS | a launchd agent in `~/Library/LaunchAgents` | runs when it next wakes |
| Linux with systemd | a user timer in `~/.config/systemd/user` | caught up at next boot |
| Linux without | one marked line in your crontab | that week is skipped |
| Windows | a Task Scheduler task (untested) | that week is skipped |

Output goes to `likesync.log` in the likesync home directory. On Linux, a user
timer only runs while you are logged in unless you enable lingering:
`loginctl enable-linger`.

### A server, system-wide

`deploy/likesync.service` and `deploy/likesync.timer` are a hardened
system-level pair for a box that runs it under its own account:

```bash
sudo cp deploy/likesync.service deploy/likesync.timer /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/likesync.service   # fix paths and the user
sudo systemctl enable --now likesync.timer
```

### Docker

The image bundles Chromium for web mode. It has no scheduler of its own; run
it from the host's.

```bash
docker build -t likesync ./likesync
docker run --rm -v likesync-data:/data \
  -e LIKESYNC_SPOTIFY_CLIENT_ID=... \
  likesync sync
```

Sign in on your laptop and `likesync session import` into the volume, since the
container has no display.

### GitHub Actions

`.github/workflows/likesync.yml` runs it weekly with no machine of your own.
Two caveats. Tokens and the browser session have to survive between runs, so
the workflow commits them — AES-GCM encrypted under `LIKESYNC_SECRET_KEY`, and
the job refuses to run without that key — which means **on a public repository
that encrypted blob is publicly readable**. And SoundCloud web mode from a
data-centre address is far more likely to be challenged as a bot than the same
browser on your home connection. Your own machine is the better home for this.

## Everyday commands

```bash
likesync setup             # do, or redo, whatever is not done yet
likesync schedule status   # is the weekly job installed
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
retry on the next run), `4` a safety rail aborted the run.

## How matching works

Spotify gives you clean artist and title fields. SoundCloud gives you
`"Fred again.. - Delilah (pull me out of this) [Skrillex Remix] [FREE
DOWNLOAD]"` posted by a promo channel. Bridging that is the hard part.

**ISRC first.** When both sides publish an ISRC, an exact match is accepted
immediately. Roughly a fifth of a typical SoundCloud likes list carries one —
the properly distributed releases.

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

Pairings are remembered, so this happens once per track, not every run. If you see
a wrong pairing, `likesync unlink` it and `likesync link` the right one; manual
links are never overwritten by the matcher.

## Safety rails

Delete propagation is the dangerous half of two-way sync, so it is fenced in.

- **Library shrinkage abort.** If a library comes back with under half of what
  it had last run, the run aborts and writes nothing. A truncated read is
  indistinguishable from a mass unlike. Override with `--force-shrink` once
  you have checked.
- **Incomplete reads fail loudly.** In web mode the site itself says when a
  likes list has ended, and likesync waits for that. If the page promises more
  and then stalls, the run errors instead of treating the half-loaded list as
  your library. ("The page stopped growing" is not trusted as an ending unless
  there is nothing better to go on.)
- **A renamed track is not an unlike.** Web mode addresses a track by its URL,
  which an uploader can change. The site's own stable id is kept alongside, so
  a track that moved carries its pairing with it instead of reading as "unliked
  here" and being removed from Spotify.
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
  rather than a toggle. Only the track's own button is ever clicked — if which
  one that is cannot be told for certain, or the address has redirected
  somewhere else, nothing is clicked and the track is reported as failed.
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
- **Big libraries take a few runs to settle.** Each run has a search budget
  (`max_searches_per_run`), kept modest on purpose, so a first sync of many
  hundreds of tracks pairs them up over several runs — weeks, on a weekly
  schedule. Nothing is lost in the meantime. To hurry it, run `likesync sync`
  by hand a few times.
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

**Probe says the page could not be read.** SoundCloud changed enough to defeat
all three ways of reading your likes. Send the probe output. Meanwhile, setting
`soundcloud.likes_url` to your public likes page
(`https://soundcloud.com/<your-name>/likes`) may be enough on its own.

**Probe says "visible rows only".** It can read your likes but not the data
behind them, so matching runs without durations. It works; pairs are just more
likely to land in the review list.

**The run errors that your likes were still loading.** The page promised more
and then stalled, or the list is longer than `max_scrolls` rounds. Run it
again; raise `soundcloud.max_scrolls` for a very large library. It fails rather
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
python3 -m pytest              # 253 tests, no network
```

The tests run entirely offline. Provider tests script HTTP at the transport
seam and assert on real request shapes — paths, query strings, batch sizes,
fallback order — and the Spotify side was checked against Spotify's reference
and its February 2026 changelog. The SoundCloud web provider runs in **real
Chromium against pages modelled on the live site**: the fixtures were written
from the real markup and the real shape of the data a page loads, including
like buttons that genuinely toggle. The same provider code was run against live
soundcloud.com; the tests pin down what that run established.

```
src/likesync/
  matching.py           title parsing, version vetoes, scoring  <- read first
  engine.py             three-way reconciliation, inbox, rails
  state.py              SQLite: snapshot, pairings, inbox memory, run log
  providers/
    spotify.py          official API
    soundcloud.py       official API (for if approval ever arrives)
    soundcloud_web.py   reads what the page loads; click-to-like
  websession.py         signed-in Chromium, persisted profile
  schedule.py           installs the weekly job (launchd / systemd / cron)
  setup_wizard.py       `likesync setup`: the whole install in one command
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
