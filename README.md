# Mat time — a BJJ training dashboard fed by Garmin Connect

Every morning a GitHub Action logs in to Garmin Connect, pulls your activities,
and republishes a single-page dashboard to GitHub Pages. No server, no database,
nothing running on your Mac.

```
Garmin Connect ──► scripts/sync.py ──► data/*.json ──► scripts/build.py ──► docs/index.html ──► Pages
        (daily, 05:15 Oslo, via GitHub Actions)
```

---

## Setup

```bash
cd bjj-dashboard
./setup.sh
```

That's it. The installer creates the GitHub repo, logs you in to Garmin, uploads
the secrets, switches on Pages, and starts the first sync. You'll be asked for:

- your Garmin email, password, and 2FA code if you use one — used once, here on
  your Mac, never stored or uploaded
- a repository name (press Enter for `bjj-dashboard`)
- GitHub sign-in, in a browser window

The only tools it needs are Python and git, both of which macOS already has. If
the GitHub CLI is missing it downloads the official binary into `.tools/` inside
this folder — no Homebrew, no `sudo`, nothing installed system-wide. Deleting the
folder removes every trace.

It also checks how your BJJ sessions are actually labelled in Garmin and offers
to fix `config.json` if they aren't `mixed_martial_arts`.

Re-running `./setup.sh` is safe — it skips anything already done. Use it to
refresh your Garmin token if the sync ever stops authenticating.

<details>
<summary><b>Doing it by hand instead</b></summary>

1. Create a **public** repo on GitHub and push these files to it. (Public is what
   makes Pages free.)
2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. `.venv/bin/python scripts/login_local.py` — logs in and prints two values.
   Garmin rate-limits password logins from datacenter IPs, so this only ever
   happens on your own machine; CI uses the resulting token.
4. **Settings → Secrets and variables → Actions**: add `GARMIN_TOKENS` and
   `TOKEN_KEY` from that output.
5. **Settings → Actions → General → Workflow permissions → Read and write.**
   Without this the sync runs but can't commit its results.
6. **Settings → Pages → Deploy from a branch → `main` / `/docs`.**
7. **Actions → Sync Garmin and publish dashboard → Run workflow.**

</details>

Nothing sensitive is ever committed. Garmin rotates your refresh token every time
it is used, so the workflow re-encrypts the current one into
`data/garmin_token.enc` after each run — sealed with AES under `TOKEN_KEY`
(PBKDF2-SHA256, 600k iterations). `GARMIN_TOKENS` is only the bootstrap for run one.

The first run backfills your whole history and takes a few minutes. After that it
runs itself every morning at 05:15 Oslo time, and your dashboard lives at
`https://<your-username>.github.io/<repo>/`.

---

## What you'll see

**All time** — lifetime mat hours as the headline, then sessions, your average
week, longest streak, and total energy burned.

**Right now** — this week against your 3-session target, this month against your
20-hour target with a pace marker showing where you should be today, plus days
since your last session and your recent session heart rate versus your own baseline.

**Consistency** — a 12-month calendar of every training day, sessions per week
against target, and mat hours per month against target.

**Intensity** — how session time splits across heart-rate zones month by month,
and average heart rate per session with a rolling average through it.

**The long game** — your last 7 days of mat time against your 4-week average week
(the ratio is the classic overreaching signal), your VO2 max trend, and how the
training year splits between BJJ and everything else.

**Sessions** — the raw table.

Three controls in the header: a range filter for the trend charts, a light/dark
toggle, and **Data tables**, which reveals the underlying numbers beneath every
chart.

---

## Tuning it

Everything adjustable lives in `config.json`:

| Key | Default | What it does |
|---|---|---|
| `bjj_type_keys` | `["mixed_martial_arts"]` | Garmin activity types that count as BJJ |
| `bjj_name_patterns` | `["bjj", "jiu", ...]` | activity-name matches, for sessions logged under a generic type |
| `weekly_session_target` | `3` | the weekly goal line |
| `monthly_hours_target` | `20` | the monthly goal line |
| `min_session_minutes` | `15` | anything shorter is treated as a misfire |
| `max_session_minutes` | `240` | caps a forgotten stop |
| `hr_zone_enrich_budget` | `30` | zone lookups per run |
| `vo2max_backfill_budget` | `25` | VO2 max lookups per run |

Heart-rate zones and VO2 max history need one API call each, so they backfill a
chunk at a time across successive daily runs rather than all at once. A few years
of history fills in over roughly a week. The dashboard says how many are still queued.

Push a config change and the next scheduled run picks it up, or trigger the
workflow by hand.

---

## Everyday operation

**See it locally before pushing**

```bash
python3 scripts/build.py && open docs/index.html
```

**Force a full re-scan** — Actions → Run workflow → tick "Re-scan the entire
Garmin history".

**A sync failed.** The workflow still rebuilds the dashboard from the last good
data and shows a red banner at the top, and GitHub emails you about the failed
run. Common causes:

- *`429 Too Many Requests`* — Garmin is rate-limiting. It clears by itself;
  usually the next morning's run succeeds. Don't retry in a loop, that extends it.
- *Authentication errors* — the token chain broke (usually because a run was
  interrupted between refreshing and committing). Re-run `scripts/login_local.py`
  and update both secrets.
- *`Could not decrypt the stored token`* — `TOKEN_KEY` no longer matches
  `data/garmin_token.enc`. Delete that file, re-run `login_local.py`, update
  both secrets.

**Try a design change without touching your real data**

```bash
python3 scripts/make_demo_data.py --out /tmp/demo   # synthetic history
```

---

## Files

```
setup.sh                       the one-command installer
config.json                    every setting you'd want to change
requirements.txt
scripts/login_local.py         one-time interactive login (run on your Mac)
scripts/garmin_client.py       auth: sealed token → bootstrap secret → password
scripts/vault.py               AES sealing of the rotating token
scripts/sync.py                Garmin → data/*.json
scripts/build.py               data/*.json → docs/index.html
scripts/make_demo_data.py      synthetic history for testing
web/template.html              the dashboard: markup, styles, charts
data/                          committed history (JSON) + the sealed token
docs/index.html                the published page
.github/workflows/sync.yml     the daily job
```

## A note on what's public

The repo is public so Pages is free, which means your training history — dates,
durations, heart rates — is readable by anyone with the URL. The Garmin token is
not: it is only ever stored encrypted. If you'd rather keep the data private,
GitHub Pages on a private repo needs a paid plan; the alternative is to keep the
repo private and open `docs/index.html` locally instead.
