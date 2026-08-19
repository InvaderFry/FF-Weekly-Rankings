# Configuration guide — step by step

Everything `ffstartsit` needs is passed through environment variables. This
guide walks through where each value comes from and where to put it, for both
ways the code runs:

1. **On your computer** (the `ffstartsit` CLI) — variables live in a local
   `.env` file.
2. **On GitHub** (the twice-weekly Action + the issue-comment bot) — variables
   live in your repository's **Actions secrets and variables**.

These are two separate copies. Setting a value in `.env` does nothing for the
scheduled GitHub runs, and vice versa — if you want a setting in both places,
set it in both places.

## The variables at a glance

| Variable | Needed for | Where to get it |
|---|---|---|
| `ESPN_LEAGUE_ID` | ESPN roster (required) | Your league URL ([steps](#2-espn-league-info)) |
| `ESPN_TEAM_ID` | ESPN **public** league | Your team page URL |
| `ESPN_S2`, `ESPN_SWID` | ESPN **private** league | Browser cookies ([steps](#private-league-cookies)) |
| `FF_LEAGUES` | Multiple leagues (optional) | `name=source:id:team[:scoring]`, comma-separated ([steps](#multiple-leagues-ff_leagues)) |
| `FF_PREFERRED_EXPERTS` | Journalist ranks in the digest **and** the waiver report | `ffstartsit experts "Justin Boone" ...` ([steps](#3-preferred-journalists-ff_preferred_experts)) |
| `ODDS_API_KEY` | Vegas signal (optional) | Free key from [the-odds-api.com](https://the-odds-api.com/) |
| `FANTASYPROS_API_KEY` | ECR via API (optional) | FantasyPros; without it the app scrapes the public page |
| `DISCORD_WEBHOOK_URL` | Discord notifications (optional) | Discord webhook ([steps](#5-discord-optional)) |
| `SLEEPER_USERNAME`, `SLEEPER_LEAGUE_ID` | Sleeper roster (alternative to ESPN) | Your Sleeper account |
| `FF_WAIVER_LIMIT`, `FF_WAIVER_MAX_ADDS`, `FF_TRADE_SUGGESTIONS`, `FF_MAX_TRADE_IDEAS`, `FF_COLUMN_SCRAPE` | Waiver/trade report (optional) | Sensible defaults baked in ([steps](#7-waiver-wire--trades)) |
| `FF_SCORING`, `FF_WEIGHT_*`, `FF_INJURY`, `FF_WEATHER`, `FF_CLOSE_CALL_THRESHOLD`, `FF_PRESEASON_FILL` | Tuning (all optional) | Sensible defaults baked in — see `.env.example` |

## 1. Local setup (`.env`)

1. From the repo root, copy the template:

   ```bash
   cp .env.example .env
   ```

2. Open `.env` in any editor. Every variable is documented inline; fill in the
   ones you need (at minimum your roster source — usually the ESPN block).
   Lines you leave blank simply disable that feature.
3. `.env` is listed in `.gitignore` and must **never be committed** — it holds
   your private cookies and keys.
4. Verify:

   ```bash
   source .venv/bin/activate      # if not already active
   ffstartsit sync                # should list your roster
   ffstartsit report              # full digest
   ```

## 2. ESPN league info

### League id (always required)

1. Open your league in a browser and look at the URL:
   `https://fantasy.espn.com/football/league?leagueId=`**`123456`**
2. That number is your `ESPN_LEAGUE_ID`.

### Public league: team id

1. Open **your team's** page in the league. The URL contains
   `...teamId=`**`4`**... (or `.../teams/4`).
2. Set that number as `ESPN_TEAM_ID`. No cookies needed.

### Private league: cookies

Private leagues need two cookies from a logged-in browser session. With them,
the app auto-detects your team — you can skip `ESPN_TEAM_ID`.

1. Log in to [fantasy.espn.com](https://fantasy.espn.com) and open your league.
2. Open your browser's developer tools (`F12` or right-click → *Inspect*).
3. Go to the **Application** tab (Chrome/Edge) or **Storage** tab (Firefox) →
   **Cookies** → `https://fantasy.espn.com`.
4. Find the cookie named **`espn_s2`** and copy its (long) value into `ESPN_S2`.
5. Find the cookie named **`SWID`** and copy its value into `ESPN_SWID`. It
   looks like `{XXXXXXXX-XXXX-...}` — paste it **braces and all**; the app
   normalizes it.
6. Both cookies are required together — setting only one is the same as setting
   neither.

> **Cookies expire** every few weeks. When a run starts failing with a
> 401/403 "cookies have likely expired" message, repeat these steps and update
> the values (in `.env` locally, and in the GitHub secrets if you use the
> Actions — see below).

## Multiple leagues (`FF_LEAGUES`)

Follow every league you're in from a single setup. Step by step:

**1. Grab your ESPN cookies once.** Logged in to ESPN, open DevTools →
**Application → Cookies → espn.com** and copy `espn_s2` and `SWID` (keep SWID's
braces). One pair covers **every** ESPN league on your account — there are no
per-league credentials.

**2. Collect each league's id** from its URL — the number after `leagueId=` (or
`/leagues/`), e.g. `111111`.

**3. You almost certainly do NOT need team ids.** With `ESPN_SWID` set, the app
finds *your* team in each league by matching the SWID against each team's owners.
So leave the third field empty:

```bash
ESPN_S2=<your espn_s2 cookie>
ESPN_SWID={your-swid-cookie}

FF_LEAGUES=work=espn:111111:,dynasty=espn:222222::half,friends=espn:333333:
FF_DEFAULT_LEAGUE=work
#          name=source:league_id:team_id[:scoring], comma-separated
```

Note `dynasty=espn:222222::half` — the empty third field is the skipped team id,
and `half` is that league's scoring.

**4. Add non-ESPN leagues to the same list.** A Sleeper league is
`name=sleeper:<league_id>:`, and needs `SLEEPER_USERNAME` set (Sleeper resolves
rosters by username). A `manual` CSV league needs no id.

**5. Check each one resolves** before relying on it:

```bash
ffstartsit sync --league-name work
ffstartsit sync --league-name dynasty
ffstartsit publish --all-leagues       # or: ffstartsit waivers --all-leagues
```

A malformed entry is warned about on stderr and **dropped** — it doesn't fail the
run. So a league that silently goes missing is a typo in its entry; read the
warnings.

### The fields

- **`name`** is your handle for the league — pick anything (`work`, `dynasty`).
  Use it with `--league-name <name>` on any command, or `/report league-name work`
  as an issue comment. Names must be unique (a duplicate keeps the first, with a
  warning) and match case-insensitively.
- **`team_id`** is only needed when the app can't identify you from a cookie —
  a **public** league (no cookies at all), or when you deliberately want someone
  *else's* team. An explicit team id **overrides** SWID auto-detection.
- **`scoring`** (optional, `ppr|half|std`) overrides `FF_SCORING` for just that
  league.
- ESPN cookies are per-account, so your one `ESPN_S2`/`ESPN_SWID` pair covers
  every ESPN league listed — no per-league secrets.

`ffstartsit publish --all-leagues` renders all of them into one digest, one
dashboard (a collapsible section per league), and one Discord message (an embed
per league) — this is what the weekly Action runs. `FF_DEFAULT_LEAGUE=<name>`
sets which league commands use when you omit `--league-name`.

Prefer a file locally? Copy `leagues.example.json` to `leagues.json` (gitignored)
instead. `FF_LEAGUES` wins if both are present. With neither set, the flat
`ESPN_LEAGUE_ID`/`ESPN_TEAM_ID` drive a single league exactly as before — nothing
to change if you only have one.

> **Public repo?** Put `FF_LEAGUES` in a **GitHub Secret**, not `leagues.json`.
> League ids aren't credentials, but a committed file would expose your league
> list. See the CI secrets table below.

## 3. Preferred journalists (`FF_PREFERRED_EXPERTS`)

This is the most valuable optional variable, because it feeds **two** reports:
the **"Preferred journalists"** section of the weekly digest (each analyst's rank
per player, plus their average) *and* the Tuesday waiver report, where those
ranks annotate every suggested add. It's display-only — it never changes the
blended score.

You need each analyst's FantasyPros **expert id**.

### The quick way

```bash
ffstartsit experts "Justin Boone" "Jamey Eisenberg" "Dave Richard"
```

It looks each analyst up and prints a paste-ready line:

```
FF_PREFERRED_EXPERTS=1234:Justin Boone,120:Jamey Eisenberg,125:Dave Richard
```

Put that in `.env`. `ffstartsit experts --list` dumps every expert it can find,
if you want somebody not named above.

### The manual way (always works)

Do this if the lookup can't resolve a name — FantasyPros changes its markup from
time to time, and the browser never lies. **One analyst at a time**, which is
what makes the id unambiguous:

1. Open <https://www.fantasypros.com/nfl/rankings/ppr-rb.php>.
   *(Weekly pages only exist once the season is near — before that, come back
   later; the app just omits the section until then.)*
2. Click **Pick Experts**, **deselect everyone**, then select **only** Justin
   Boone. Apply.
3. The URL now ends with `&filters=`**`1234`** — that number is his id.
4. Repeat for Jamey Eisenberg and Dave Richard, then write the pairs out:

   ```bash
   FF_PREFERRED_EXPERTS=1234:Justin Boone,120:Jamey Eisenberg,125:Dave Richard
   ```

Selecting all three at once gives `filters=A:B:C`, which is faster but doesn't
tell you which number is whose. One at a time is worth the extra minute — see the
warning below about what a mislabeled id costs you.

### Check them

```bash
ffstartsit experts --verify     # are these ids live, and distinct?
ffstartsit journalists          # do I get three different columns?
```

`--verify` re-fetches each id's ranking and compares them. It catches three
things:

| Verdict | What it means |
|---|---|
| *returned no rankings* | Dead or malformed id, or no weekly data yet. |
| *identical to unfiltered consensus* | The id is almost certainly not that analyst. |
| *identical to expert id N* | FantasyPros ignored the filter — one of the ids is wrong. |

> ⚠️ **Why the middle row matters.** A wrong-but-*valid* id returns a real
> ranking, so the report renders somebody else's numbers under your journalist's
> name and nothing looks broken. `--verify` is what catches that; the older
> "all journalists returned identical ranks" warning only catches the filter
> being ignored for *every* expert.

`--verify` proves an id is live and distinct — **not** that it belongs to the
person you named it after. Only the per-analyst lookup ties a number to a name.

For the scheduled Actions runs, set the same value as the `FF_PREFERRED_EXPERTS`
repo **variable** (not a secret — expert ids aren't sensitive); see
[section 6](#6-github-actions-setup-the-scheduled-runs).

Leave the variable unset (or set it to `0`) to hide the section.

## 4. Signal API keys (optional)

- **`ODDS_API_KEY`** — free tier at [the-odds-api.com](https://the-odds-api.com/)
  (~500 requests/month is plenty). Without it the Vegas signal is marked
  unavailable and the blend runs on the other signals.
- **`FANTASYPROS_API_KEY`** — used first for ECR and the journalists view if
  set; otherwise the app scrapes the public rankings pages, which works fine.

## 5. Discord (optional)

1. In your Discord server: **Server Settings → Integrations → Webhooks →
   New Webhook**, pick the channel, **Copy Webhook URL**.
2. Set it as `DISCORD_WEBHOOK_URL`. Leave unset to skip Discord.

## 6. GitHub Actions setup (the scheduled runs)

The repo ships three workflows that run this code **on GitHub's servers**, where
your local `.env` doesn't exist:

- **Weekly start/sit report** (`weekly-report.yml`) — Thursday and Sunday
  (cron, UTC), posts the digest as an issue, deploys the dashboard to GitHub
  Pages, pings Discord.
- **Tuesday waiver wire & trades** (`waivers.yml`) — Tuesday evening, posts the
  waiver report as an issue, deploys `waivers.html` alongside the dashboard,
  pings Discord. Reads the same secrets; needs no extra ones.
- **ChatOps** (`chatops.yml`) — replies to `/rank RB`-style comments on issues.

They read their configuration from your repository's Actions settings:

### Secrets (sensitive values)

1. On GitHub, open your repo → **Settings** → **Secrets and variables** →
   **Actions**.
2. On the **Secrets** tab, click **New repository secret** for each of these,
   using the exact names (same values as your local `.env`):

   | Secret name | When |
   |---|---|
   | `ESPN_LEAGUE_ID` | single league (skip if you set `FF_LEAGUES`) |
   | `FF_LEAGUES` | multiple leagues — `name=espn:id:team[:scoring],…` |
   | `ESPN_S2` | private league |
   | `ESPN_SWID` | private league |
   | `ESPN_TEAM_ID` | public, single league |
   | `ODDS_API_KEY` | if you use the Vegas signal |
   | `FANTASYPROS_API_KEY` | if you have one |
   | `DISCORD_WEBHOOK_URL` | if you want Discord pings |
   | `SLEEPER_USERNAME` | if any `FF_LEAGUES` entry uses `source: sleeper` |

   Secrets you skip just disable that feature — nothing breaks. Set **either**
   `FF_LEAGUES` (multiple leagues, and the weekly Action publishes all of them)
   **or** the flat `ESPN_LEAGUE_ID`/`ESPN_TEAM_ID` (one league). Keeping
   `FF_LEAGUES` as a secret is why your league list never lands in a public repo.

### Variables (non-sensitive values)

`FF_PREFERRED_EXPERTS` isn't secret, so it goes on the **Variables** tab
(easier to view and edit later):

1. Same page (**Settings → Secrets and variables → Actions**), switch to the
   **Variables** tab.
2. Click **New repository variable**, name it `FF_PREFERRED_EXPERTS`, and paste
   the same `id:Name,id:Name` value you tested locally in
   [section 3](#3-preferred-journalists-ff_preferred_experts).

### Finish and test

1. **Enable GitHub Pages once:** repo → **Settings → Pages → Build and
   deployment → Source = GitHub Actions**. The dashboard then deploys to
   `https://<owner>.github.io/<repo>/`.
2. Trigger a run by hand: **Actions** tab → **Weekly start/sit report** →
   **Run workflow** (works from the GitHub mobile app too). Check the run's
   summary for the digest and any warnings. Do the same for **Tuesday waiver
   wire & trades** to see that report.
3. When ESPN cookies expire, update the `ESPN_S2`/`ESPN_SWID` **secrets** here
   too, not just your local `.env`.

> Cron times are UTC and drift an hour with daylight saving; edit the `cron:`
> lines in `weekly-report.yml` / `waivers.yml` to taste. The waiver run is set
> for 7pm Tuesday US Central (6pm once standard time starts), which is ahead of
> Wednesday-morning waiver processing either way.

> Both workflows deploy to the same GitHub Pages site, and a Pages deploy
> replaces the site wholesale. That's why each one rebuilds **both** pages and
> they share a `concurrency: group: ff-startsit-pages` — otherwise the later run
> would silently delete the earlier run's page.

## 7. Waiver wire & trades

`ffstartsit waivers` needs no new credentials — it reuses your ESPN cookies or
Sleeper username to read the league's free-agent pool and every team's roster.
The knobs are all optional:

| Variable | Default | What it does |
|---|---|---|
| `FF_WAIVER_LIMIT` | `150` | Free agents considered per league, in the platform's own relevance order |
| `FF_WAIVER_MAX_ADDS` | `8` | Most adds (and drop candidates) listed per league |
| `FF_TRADE_SUGGESTIONS` | `1` | `0` removes the trade section |
| `FF_MAX_TRADE_IDEAS` | `5` | Cap on trade ideas per league |
| `FF_COLUMN_SCRAPE` | `1` | `0` skips fetching the CBS/Yahoo waiver columns |

Two things worth knowing:

- **Set `FF_PREFERRED_EXPERTS`** ([section 3](#3-preferred-journalists-ff_preferred_experts))
  — `ffstartsit experts "Justin Boone" "Jamey Eisenberg" "Dave Richard"` prints
  the line to paste. Their *rankings* reach the waiver report through it, and
  that's the reliable half. The column quotes are a bonus that disappears quietly
  if a site changes layout or paywalls the piece.
- **A `manual` CSV league is skipped** with a warning. A hand-typed roster has no
  free-agent pool and no trade partners behind it, so there's nothing to read.

Try it locally before wiring the workflow:

```bash
ffstartsit waivers --all-leagues
```

## Quick reference: where does each value go?

| Variable | Local `.env` | GitHub **secret** | GitHub **variable** |
|---|:---:|:---:|:---:|
| `ESPN_LEAGUE_ID`, `ESPN_TEAM_ID`, `ESPN_S2`, `ESPN_SWID` | ✅ | ✅ | |
| `FF_LEAGUES` (multiple leagues) | ✅ | ✅ | |
| `ODDS_API_KEY`, `FANTASYPROS_API_KEY`, `DISCORD_WEBHOOK_URL` | ✅ | ✅ | |
| `SLEEPER_USERNAME` (Sleeper leagues) | ✅ | ✅ | |
| `FF_PREFERRED_EXPERTS` | ✅ | | ✅ |
| Waiver knobs (`FF_WAIVER_*`, `FF_TRADE_*`, `FF_COLUMN_SCRAPE`) | ✅ | | *not wired to Actions — defaults apply there* |
| Tuning (`FF_SCORING`, `FF_WEIGHT_*`, …) | ✅ | | *not wired to Actions — defaults apply there* |

*(The tuning knobs aren't currently passed to the workflows; the scheduled runs
use the defaults plus any learned weights committed to the repo. Add them to
the `env:` block of `weekly-report.yml` if you want to override them there.)*
