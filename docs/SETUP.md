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
| `FF_PREFERRED_EXPERTS` | Preferred journalists view (optional) | FantasyPros expert ids ([steps](#3-preferred-journalists-ff_preferred_experts)) |
| `ODDS_API_KEY` | Vegas signal (optional) | Free key from [the-odds-api.com](https://the-odds-api.com/) |
| `FANTASYPROS_API_KEY` | ECR via API (optional) | FantasyPros; without it the app scrapes the public page |
| `DISCORD_WEBHOOK_URL` | Discord notifications (optional) | Discord webhook ([steps](#5-discord-optional)) |
| `SLEEPER_USERNAME`, `SLEEPER_LEAGUE_ID` | Sleeper roster (alternative to ESPN) | Your Sleeper account |
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

## 3. Preferred journalists (`FF_PREFERRED_EXPERTS`)

This powers the optional **"Preferred journalists"** section (each journalist's
weekly rank per player + their average). It's display-only — it never changes
the blended score. You need each journalist's FantasyPros **expert id**:

1. Open a FantasyPros **weekly** rankings page, e.g.
   <https://www.fantasypros.com/nfl/rankings/ppr-rb.php>.
   *(Weekly pages only exist once the season is near — before that, come back
   later; the app just omits the section until then.)*
2. Click **Pick Experts** and deselect everything except your journalists —
   e.g. **Justin Boone**, **Jamey Eisenberg**, **Dave Richard**.
3. Apply. The page URL now ends with `...&filters=`**`1234:5678:9012`** — one
   number per selected expert. Those numbers are the expert ids.
4. Pair each id with a display name (check the picker to see which id belongs
   to whom — select one expert at a time if unsure) and set:

   ```bash
   FF_PREFERRED_EXPERTS=1234:Justin Boone,5678:Jamey Eisenberg,9012:Dave Richard
   ```

5. Verify:

   ```bash
   ffstartsit journalists
   ```

   You should see one table per position with each journalist's column. Two
   warnings to know about:
   - *"no rankings from preferred journalist X"* — that id returned nothing
     (wrong id, or no weekly data yet).
   - *"all preferred journalists returned identical ranks"* — FantasyPros
     ignored the filter, which almost always means a wrong id. Re-check step 3.

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

## 6. GitHub Actions setup (the twice-weekly runs)

The repo ships two workflows that run this code **on GitHub's servers**, where
your local `.env` doesn't exist:

- **Weekly start/sit report** (`weekly-report.yml`) — Thursday and Sunday
  (cron, UTC), posts the digest as an issue, deploys the dashboard to GitHub
  Pages, pings Discord.
- **ChatOps** (`chatops.yml`) — replies to `/rank RB`-style comments on issues.

They read their configuration from your repository's Actions settings:

### Secrets (sensitive values)

1. On GitHub, open your repo → **Settings** → **Secrets and variables** →
   **Actions**.
2. On the **Secrets** tab, click **New repository secret** for each of these,
   using the exact names (same values as your local `.env`):

   | Secret name | When |
   |---|---|
   | `ESPN_LEAGUE_ID` | always |
   | `ESPN_S2` | private league |
   | `ESPN_SWID` | private league |
   | `ESPN_TEAM_ID` | public league |
   | `ODDS_API_KEY` | if you use the Vegas signal |
   | `FANTASYPROS_API_KEY` | if you have one |
   | `DISCORD_WEBHOOK_URL` | if you want Discord pings |

   Secrets you skip just disable that feature — nothing breaks.

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
   summary for the digest and any warnings.
3. When ESPN cookies expire, update the `ESPN_S2`/`ESPN_SWID` **secrets** here
   too, not just your local `.env`.

> Cron times are UTC and drift an hour with daylight saving; edit the `cron:`
> lines in `weekly-report.yml` to taste.

## Quick reference: where does each value go?

| Variable | Local `.env` | GitHub **secret** | GitHub **variable** |
|---|:---:|:---:|:---:|
| `ESPN_LEAGUE_ID`, `ESPN_TEAM_ID`, `ESPN_S2`, `ESPN_SWID` | ✅ | ✅ | |
| `ODDS_API_KEY`, `FANTASYPROS_API_KEY`, `DISCORD_WEBHOOK_URL` | ✅ | ✅ | |
| `FF_PREFERRED_EXPERTS` | ✅ | | ✅ |
| Tuning (`FF_SCORING`, `FF_WEIGHT_*`, …) | ✅ | | *not wired to Actions — defaults apply there* |

*(The tuning knobs aren't currently passed to the workflows; the scheduled runs
use the defaults plus any learned weights committed to the repo. Add them to
the `env:` block of `weekly-report.yml` if you want to override them there.)*
