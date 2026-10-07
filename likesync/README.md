# likesync

Keeps your **Spotify Liked Songs** and **SoundCloud Likes** in sync, both
directions, once a day.

Like something on either service and it shows up on the other. Unlike it on
either and it goes away on both. It runs unattended from cron, a systemd timer,
Docker, or GitHub Actions, and it is built to fail safe: a bad match or a
truncated API response should never wipe a library.

- [Read this before you start](#read-this-before-you-start)
- [How it works](#how-it-works)
- [Install](#install)
- [Register the two apps](#register-the-two-apps)
- [Connect your accounts](#connect-your-accounts)
- [First run](#first-run)
- [Schedule it](#schedule-it)
- [Everyday commands](#everyday-commands)
- [How matching works](#how-matching-works)
- [Safety rails](#safety-rails)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Read this before you start

Two things are outside this tool's control and will decide whether you can run
it at all. Please check both before investing time.

**1. SoundCloud API access is gated.** SoundCloud stopped self-serve API
registration years ago. Access is now granted case by case on request, some
sources report it requires an Artist Pro account, and the public API terms
generally exclude commercial use. Personal library sync is the kind of
non-commercial use that fits, but **approval is not guaranteed and can take
weeks**. Start at <https://developers.soundcloud.com> and check the current
process. If you already have a client id and secret, you are fine.

**2. Spotify's developer mode has conditions.** An app in Development Mode
requires the app owner's account to have **Spotify Premium** — if the
subscription lapses, API calls stop. Dev-mode apps are limited to a handful of
allow-listed users, which is plenty for syncing your own library. You add your
own account to the allow-list in the app's dashboard settings.

**3. You will need to re-authorise Spotify about twice a year.** Since July
2026, Spotify refresh tokens expire roughly six months after you first grant
consent. When that happens the run fails with `invalid_grant` and you re-run
`likesync login spotify`. `likesync status` warns you as the date approaches.

This tool talks only to the official, documented APIs. It does not scrape, and
it does not use SoundCloud's internal `api-v2` endpoints.

## How it works

Two-way sync needs memory. If Spotify has a track and SoundCloud does not, that
is either *an addition on Spotify* or *a removal on SoundCloud* — and nothing
in the current state of the two libraries can tell you which. So every run
compares three things:

```
   what Spotify has now  ─┐
                          ├─→  added / removed on each side  ─→  plan  ─→  apply
 what SoundCloud has now ─┤
   what both had last run ┘          (state.sqlite3)
```

The previous run's snapshot lives in a local SQLite file, along with the track
pairings it has worked out. A run then:

1. Reads both libraries in full (and aborts rather than acting on a partial read).
2. Diffs each against the snapshot to get real additions and removals.
3. For anything new, finds its counterpart on the other service — by ISRC when
   both sides publish one, otherwise by searching and scoring the results.
4. Builds a plan, checks it against the safety rails, and applies it.
5. Records the new snapshot — but only for changes that actually propagated, so
   a failed write is retried tomorrow instead of being silently forgotten.

**The first run is a merge, not a sync.** With no snapshot, everything looks
new, so both libraries end up with the union of the two. Nothing is ever
unliked on a first run. Use `likesync plan` first to see exactly what it would
do.

## Install

Python 3.11 or newer. No runtime dependencies — this runs from cron for years,
and a dependency resolution failure at 04:00 is worse than a bit more code.

```bash
git clone https://github.com/godfatha1/coding.git
cd coding/likesync
python3 -m venv venv
./venv/bin/pip install .

# optional: encrypt the stored tokens at rest (needed for the CI workflow)
./venv/bin/pip install '.[crypt]'
```

Then `./venv/bin/likesync --help`, or put the venv's `bin` on your `PATH`.

## Register the two apps

### Spotify

1. Go to <https://developer.spotify.com/dashboard> and create an app.
2. Set the redirect URI to exactly `http://127.0.0.1:8765/callback`.
   Spotify requires the loopback **IP**; `localhost` is rejected.
3. Request the scopes `user-library-read` and `user-library-modify`.
4. Under the app's settings, add your own Spotify account as a user.
5. Copy the **Client ID**. You do not need the client secret — likesync uses
   PKCE, which is the right choice for a personal app.

### SoundCloud

1. Request API access at <https://developers.soundcloud.com> (see the warning
   above).
2. Set the redirect URI to `http://127.0.0.1:8765/callback`.
3. Copy the **Client ID** and **Client Secret**. SoundCloud treats every client
   as confidential, so the secret is required even though we use PKCE.

### Configure

```bash
cp config.example.toml ~/.local/share/likesync/config.toml
# or keep it next to the code as ./likesync.toml
$EDITOR ~/.local/share/likesync/config.toml
```

Fill in `spotify.client_id`, `soundcloud.client_id` and
`soundcloud.client_secret`. Everything else has a working default, and
`config.example.toml` documents each setting. Any value can also come from the
environment as `LIKESYNC_<SECTION>_<KEY>`, e.g. `LIKESYNC_SPOTIFY_CLIENT_ID`,
which is the easier route for containers and CI.

## Connect your accounts

```bash
likesync login spotify
likesync login soundcloud
```

Each opens your browser, then catches the redirect on `127.0.0.1:8765`. On a
headless box, use `--manual`: it prints the URL, you approve it in any browser,
and paste the resulting address back.

```bash
likesync login spotify --manual
```

Tokens are written to `~/.local/share/likesync/tokens.json`, mode `0600`.
Set `LIKESYNC_SECRET_KEY` (32 random bytes, base64) to encrypt that file with
AES-GCM instead:

```bash
python3 -c 'import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
```

## First run

Always look before you leap:

```bash
likesync plan
```

That reads both libraries, works out the pairings, and prints exactly what it
would change — without writing anything. Typical first output:

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

Pick whichever fits. All of them just run `likesync sync` once a day.

### systemd (recommended on Linux)

```bash
sudo cp deploy/likesync.service deploy/likesync.timer /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/likesync.service   # fix paths and the user
sudo systemctl enable --now likesync.timer
systemctl list-timers likesync.timer                # confirm the next run
journalctl -u likesync.service -n 50                # read the last run
```

The timer uses `Persistent=true`, so a machine that was asleep at the scheduled
time catches up on wake, and a randomised delay keeps you off the hour.

### cron

See `deploy/crontab.example`. cron runs with almost no environment, so set
`LIKESYNC_HOME` and the credentials explicitly and use absolute paths.

### macOS

See `deploy/com.likesync.daily.plist`; copy it to `~/Library/LaunchAgents/` and
`launchctl load` it.

### Docker

```bash
docker build -t likesync ./likesync
docker run --rm -v likesync-data:/data \
  -e LIKESYNC_SPOTIFY_CLIENT_ID=... \
  -e LIKESYNC_SOUNDCLOUD_CLIENT_ID=... \
  -e LIKESYNC_SOUNDCLOUD_CLIENT_SECRET=... \
  likesync sync
```

Log in once with `docker run -it --rm -v likesync-data:/data ... likesync login
spotify --manual` so the tokens land on the volume.

### GitHub Actions

`.github/workflows/likesync.yml` runs it daily with no machine of your own.

**Understand the trade-off first.** Tokens have to survive between runs, and
SoundCloud rotates its refresh token on every refresh, so the workflow commits
the token file to a `likesync-state` branch of this repository. It is encrypted
with AES-GCM under `LIKESYNC_SECRET_KEY`, and the job refuses to run if that
key is missing or if the file is not encrypted. Even so, **if this repository
is public, that encrypted blob is publicly readable**. For a public repo,
prefer running it on your own machine, or move the workflow to a private repo.

Set these repository secrets:

| Secret | What it is |
| --- | --- |
| `LIKESYNC_SPOTIFY_CLIENT_ID` | from the Spotify dashboard |
| `LIKESYNC_SOUNDCLOUD_CLIENT_ID` | from SoundCloud |
| `LIKESYNC_SOUNDCLOUD_CLIENT_SECRET` | from SoundCloud |
| `LIKESYNC_SECRET_KEY` | 32 random bytes, base64 — required |
| `LIKESYNC_TOKENS` | `likesync tokens export` output, first run only |

Log in locally first, then:

```bash
likesync tokens export        # paste into the LIKESYNC_TOKENS secret
```

After the first successful run the encrypted file on the state branch takes
over and you can delete the `LIKESYNC_TOKENS` secret. Each run writes a summary
to the job page and uploads the full JSON report as an artifact. Trigger it by
hand from the Actions tab, with a dry-run checkbox, whenever you want.

## Everyday commands

```bash
likesync status            # connections, library sizes, last run
likesync plan              # what a sync would do, writing nothing
likesync sync              # do it
likesync sync -v           # ... and show the reasoning per track
likesync sync --json       # machine-readable report
likesync runs              # recent run history

likesync unmatched         # tracks with no counterpart found
likesync unmatched --review  # near-misses waiting on your call
likesync link soundcloud 5512 3aBc9   # confirm a pairing by hand
likesync unlink spotify 3aBc9         # forget a wrong pairing

likesync ignore soundcloud 912 --reason "90 minute DJ set"
likesync unignore soundcloud 912

likesync undo              # reverse the last run
likesync undo --run 7      # reverse a specific run
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
DOWNLOAD]"` uploaded by a promo channel. Bridging that is the hard part.

**ISRC first.** When both sides publish an ISRC, an exact match is accepted
immediately. SoundCloud exposes one in `publisher_metadata` for properly
distributed tracks, which covers most label releases.

**Otherwise, parse and score.** Titles are decomposed into:

- the actual title, after stripping promo noise (`Official Video`, `[FREE
  DOWNLOAD]`, `HQ`, `Out Now`, `Premiere`, `2016 Remaster`, `Original Mix`, …)
- credited names, including `feat.` artists pulled out of the title
- the artist, parsed from `"Artist - Title"` when the uploader is just a label
- **version markers**, which are the thing that matters most

The score weighs title similarity, artist similarity and duration. But the
score alone never decides — these **vetoes** come first, and any one of them
rejects a pair outright no matter how well it scores:

- **Version mismatch.** A remix never matches the original. Nor does a
  bootleg, flip, VIP, dub, mashup, cover, live take, acoustic, instrumental,
  karaoke, demo or nightcore edit.
- **Different remixer.** A Skrillex remix never matches a Noisia remix.
- **Duration too far apart.** Beyond roughly 15 seconds (scaling with track
  length, capped at 45), it is a different recording — this is what keeps
  hour-long DJ sets from matching the track they are named after.
- **Artist or title too weak** on their own.

Anything at or above `accept_threshold` (0.78) is linked automatically.
Between `review_threshold` (0.62) and that, it is parked for you to confirm
with `likesync link` — surfaced by `likesync unmatched --review`. Below that it
is reported as unmatched and retried every couple of weeks, since the track may
simply not be uploaded yet.

Pairings are remembered, so this work happens once per track, not daily. If you
ever see a wrong pairing, `likesync unlink` it and `likesync link` the right
one; manual links are never overwritten by the matcher.

## Safety rails

Delete propagation is the dangerous half of two-way sync, so it is fenced in.

- **Library shrinkage abort.** If a library comes back with less than half of
  what it had last run, the run aborts and writes nothing. A truncated API read
  is indistinguishable from a mass unlike, and this is what stops likesync
  acting on one. Override with `--force-shrink` once you have checked.
- **Unlike cap.** More than `max_unlikes_per_run` (50) planned removals and all
  of them are skipped with a warning rather than applied. They are *deferred*,
  not dropped: raise the limit or pass `--force-unlikes` and the next run
  applies them.
- **No unlikes on the first run.** There is no baseline to diff against, so
  removals are not even representable.
- **The snapshot only advances past changes that landed.** A failed write, a
  deferred unlike or a truncated plan leaves that track's baseline untouched,
  so the next run sees the same change again and retries it. Without this, one
  failed write would diverge the two libraries permanently.
- **Per-track failures are isolated.** One unlikeable track does not abort the
  run, and a rejected Spotify batch is retried one id at a time to find the
  culprit.
- **`likesync undo`** reverses any run, then re-reads both libraries so the
  next run starts from the truth.
- **Write and search budgets** keep a single run inside both services' rate
  limits; anything left over is picked up tomorrow.

Every run is recorded — the plan, what was applied, what failed and why — in
`state.sqlite3`, readable with `likesync runs` and `likesync sync --json`.

## Limitations

Worth knowing before you rely on it:

- **Liked/saved tracks only.** Playlists, albums and followed artists are out
  of scope.
- **Lots of SoundCloud has no Spotify counterpart.** Bootlegs, unreleased
  edits, DJ mixes and self-released demos simply do not exist on Spotify. They
  show up in `likesync unmatched` permanently, and that is the correct answer.
  Set `skip_longer_than_ms` to keep long mixes out of the matcher entirely.
- **SoundCloud does not expose when you liked something.** So a genuine
  conflict — the same track liked on one side and unliked on the other since
  the last run — cannot be resolved by recency. The default `like_wins` keeps
  the track and reports the conflict; `conflict = "skip"` leaves both sides
  alone.
- **A newly discovered pairing is treated as an addition.** If a track sat
  unmatched on Spotify for a month and then appears on SoundCloud, likesync
  links them and likes it on SoundCloud. It has no record of a counterpart
  having existed before, so it cannot tell that apart from a new like.
- **Spotify local files cannot sync.** They have no API id. They are skipped.
- **Region-blocked or deleted tracks** may fail to like; they are reported as
  failures and retried.
- **One account pair per state file.** Use separate `LIKESYNC_HOME` directories
  for separate pairs.

## Troubleshooting

**`403` on a Spotify like or unlike.** Your client id is subject to the
February 2026 endpoint changes, which replaced `PUT /me/tracks` with
`PUT /me/library`. likesync probes both automatically and remembers which works,
so this usually resolves itself; if it does not, pin
`spotify.write_mode = "library"`. A 403 can also mean the app owner's Premium
subscription lapsed.

**`401` from SoundCloud with a token you just created.** SoundCloud documents
the `OAuth` authorization scheme rather than `Bearer`. likesync defaults to
`OAuth`; set `soundcloud.auth_scheme = "Bearer"` if they have changed it.

**`invalid_grant` on refresh.** The refresh token is dead — expected for
Spotify roughly every six months. Run `likesync login <provider>` again.
If this happens repeatedly on SoundCloud, something is spending the refresh
token twice: SoundCloud invalidates it on every use. likesync serialises
refreshes behind a file lock, so check you are not running two instances
against the same `LIKESYNC_HOME` (and note the CI workflow sets a
`concurrency` group for exactly this reason).

**`429` / `QUOTA_EXCEEDED`.** likesync honours `Retry-After` and backs off. A
`QUOTA_EXCEEDED` response means Spotify's dev-mode quota is spent and retrying
today will not help; lower `max_searches_per_run` so a run does less work.

**A wrong pairing got made.** `likesync unlink <provider> <id>`, then
`likesync link` the right one, or `likesync ignore` the track. Raise
`accept_threshold` if it keeps happening.

**The run aborted on library shrinkage.** That is the rail doing its job.
Check the library really did shrink (open the app), then re-run with
`--force-shrink`.

## Development

```bash
cd likesync
python3 -m pip install -e '.[dev]'
python3 -m pytest              # 121 tests, no network
python3 -m pytest --cov=likesync
```

The tests run entirely offline. `FakeTransport` scripts HTTP at the transport
seam, so provider tests assert on real request shapes — paths, query strings,
batch sizes, fallback order — without touching the network, and the engine
tests drive full sync scenarios through in-memory providers.

```
src/likesync/
  matching.py    title parsing, version vetoes, scoring   <- read this first
  engine.py      three-way reconciliation and the rails
  state.py       SQLite: snapshot, pairings, run log
  providers/     spotify.py, soundcloud.py  (API quirks live here)
  oauth.py       PKCE, loopback redirect, refresh under lock
  tokens.py      token storage, optional AES-GCM at rest
  httpc.py       stdlib HTTP with retries + the test seam
  cli.py         commands
```

Both services change their APIs more often than you would like, so endpoint
behaviour is probed and remembered rather than hardcoded, and base URLs,
auth schemes and write modes are all configurable. If something moves, it
should be a config change rather than a patch.

## Licence

MIT.
