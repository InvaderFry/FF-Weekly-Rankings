"""Configuration loaded from environment / .env.

One place owns the blend weights and thresholds so a future #7 calibrator can
rewrite them programmatically without touching the engine.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Map of user-facing scoring choice -> FantasyPros scoring code.
SCORING_CODES = {"ppr": "PPR", "half": "HALF", "std": "STD"}
ROSTER_SOURCES = {"espn", "sleeper", "manual"}


@dataclass
class LeagueProfile:
    """One named league the user follows.

    A profile only carries what *selects a roster* — source + league/team ids
    (+ optional per-league scoring). Secrets (ESPN cookies, API keys) stay global
    on ``Settings`` because they're per-account, not per-league. ``name`` is the
    handle used by ``--league-name`` and printed as the output label.
    """

    name: str
    source: str = "espn"
    league_id: str = ""
    team_id: str = ""
    scoring: Optional[str] = None       # None -> fall back to the global FF_SCORING


@dataclass
class Settings:
    # Roster source: espn (default) | sleeper | manual
    roster_source: str = "espn"
    # Named leagues (multi-league support). Always non-empty after load_settings:
    # a single "default" profile is synthesized from the flat ESPN_*/SLEEPER_* vars
    # below when neither FF_LEAGUES nor a leagues.json file is configured, so
    # single-league setups keep working unchanged.
    leagues: list[LeagueProfile] = field(default_factory=list)
    default_league: str = ""            # name of the profile used when none is asked for
    # ESPN
    espn_league_id: str = ""
    espn_team_id: str = ""
    espn_s2: str = ""
    espn_swid: str = ""
    # Sleeper
    sleeper_username: str = ""
    sleeper_league_id: str = ""
    # Manual
    manual_roster_file: Path = field(default_factory=lambda: Path("manual_roster.csv"))
    # Signals
    odds_api_key: str = ""
    fantasypros_api_key: str = ""
    scoring: str = "ppr"
    weights: dict[str, float] = field(
        default_factory=lambda: {"ecr": 0.60, "vegas": 0.18, "injury": 0.12,
                                 "weather": 0.10})
    close_call_threshold: float = 5.0
    injury_enabled: bool = True
    weather_enabled: bool = True
    # Preferred journalists (display-only view): "id:Name,id:Name" FantasyPros
    # expert ids. Empty/0/off disables the section. Never part of the blend
    # weights — this is a side-by-side view, not a signal.
    preferred_experts: str = ""
    # Before Week 1 there is no live data; fill runs with bundled sample data
    # (clearly labeled) instead of an empty lineup. FF_PRESEASON_FILL=0 disables.
    preseason_fill: bool = True
    # Distribution
    discord_webhook_url: str = ""
    dashboard_url: str = ""
    # Repo web URL, used to point Discord readers at the issue-comment commands.
    # FF_REPO_URL, else derived from the GITHUB_* vars GitHub Actions sets.
    repo_url: str = ""
    data_dir: Path = field(default_factory=lambda: Path(".cache"))

    @property
    def scoring_code(self) -> str:
        return SCORING_CODES.get(self.scoring, "PPR")

    @property
    def results_log_path(self) -> Path:
        return self.data_dir / "results_log.jsonl"

    @property
    def learned_weights_path(self) -> Path:
        """Where ``calibrate --write`` persists learned blend weights (#7)."""
        return self.data_dir / "learned_weights.json"


def _f(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` on anything unusable.

    ``float()`` happily accepts "nan" and "inf", which then defeat every
    downstream comparison guard (``nan < 0`` and ``nan > 0`` are both False), so
    non-finite values are rejected here rather than being allowed to reach the
    blend.
    """
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        parsed = float(val)
    except ValueError:
        return default
    if not math.isfinite(parsed):
        _warn(f"{name} is not a finite number; using {default} instead.")
        return default
    return parsed


def _b(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() not in {"0", "false", "no", "off"}


def _validate_weights(weights: dict[str, float],
                      defaults: dict[str, float]) -> dict[str, float]:
    """Reject negative or all-zero weights, warning and falling back to defaults.

    A silently-invalid weight set (e.g. every weight 0) makes the blend score
    every player ``None`` — fail loud-but-graceful instead.

    Non-finite weights are checked first: NaN compares False against everything,
    so it would slip past both the sign and the sum guard below and then make
    ``weighted_final``'s ``wsum > 0`` test False for every player — exactly the
    all-``None`` blend this function exists to prevent.
    """
    if not all(math.isfinite(w) for w in weights.values()):
        _warn("Non-finite blend weight(s) configured; using defaults instead.")
        return dict(defaults)
    if any(w < 0 for w in weights.values()):
        _warn("Negative blend weight(s) configured; using defaults instead.")
        return dict(defaults)
    if sum(weights.values()) <= 0:
        _warn("Blend weights sum to 0; using defaults instead.")
        return dict(defaults)
    return weights


def _load_learned_weights(path: Path) -> dict[str, float]:
    """Read calibrated weights written by ``calibrate --write`` (empty if absent).

    A corrupt or non-numeric file is ignored with a warning rather than crashing
    load — the defaults then stand. ``json.loads`` accepts bare ``NaN``/
    ``Infinity``, so non-finite entries are dropped here too.
    """
    if not path.exists():
        return {}
    try:
        import json
        data = json.loads(path.read_text())
        parsed = {str(k): float(v) for k, v in data.items()}
    except Exception:
        _warn(f"Could not read learned weights at {path}; ignoring.")
        return {}
    finite = {k: v for k, v in parsed.items() if math.isfinite(v)}
    if len(finite) != len(parsed):
        _warn(f"Ignoring non-finite learned weight(s) in {path}.")
    return finite


def parse_leagues(raw: str) -> list[LeagueProfile]:
    """Parse the ``FF_LEAGUES`` env string into profiles (bad entries skipped).

    Format: comma-separated ``name=source:league_id:team_id[:scoring]``. Whitespace
    around every part is tolerated. A malformed or unknown-source entry is warned
    about and dropped rather than crashing load — never silently invalid.

    Example: ``work=espn:111111:3, dynasty=espn:222222:7:half``
    """
    profiles: list[LeagueProfile] = []
    seen: set[str] = set()
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            _warn(f"Ignoring malformed FF_LEAGUES entry {chunk!r} "
                  "(expected name=source:league_id:team_id).")
            continue
        name, spec = chunk.split("=", 1)
        name = name.strip()
        parts = [p.strip() for p in spec.split(":")]
        source = parts[0].lower() if parts else ""
        league_id = parts[1] if len(parts) >= 2 else ""
        team_id = parts[2] if len(parts) >= 3 else ""
        scoring = parts[3].lower() if len(parts) >= 4 and parts[3] else None
        if not name or source not in ROSTER_SOURCES:
            _warn(f"Ignoring malformed FF_LEAGUES entry {chunk!r} "
                  f"(source must be one of {sorted(ROSTER_SOURCES)}).")
            continue
        if source != "manual" and not league_id:
            _warn(f"Ignoring FF_LEAGUES entry {name!r}: {source} needs a league id.")
            continue
        if scoring is not None and scoring not in SCORING_CODES:
            _warn(f"League {name!r}: unknown scoring {scoring!r}; using the global default.")
            scoring = None
        key = name.lower()
        if key in seen:
            _warn(f"Duplicate league name {name!r} in FF_LEAGUES; keeping the first.")
            continue
        seen.add(key)
        profiles.append(LeagueProfile(name=name, source=source, league_id=league_id,
                                      team_id=team_id, scoring=scoring))
    return profiles


def _load_leagues_file(path: Path) -> list[LeagueProfile]:
    """Read leagues from a local (gitignored) JSON file, or [] if absent/bad.

    Shape: ``{"leagues": [{"name": ..., "source": ..., "id": ..., "team": ...,
    "scoring": ...}, ...]}``. A corrupt file is ignored with a warning.
    """
    if not path.exists():
        return []
    try:
        import json
        data = json.loads(path.read_text())
        rows = data.get("leagues", []) if isinstance(data, dict) else []
    except Exception:
        _warn(f"Could not read leagues file at {path}; ignoring.")
        return []
    # Re-use the string parser's validation by re-encoding each row.
    specs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        source = str(row.get("source", "espn")).strip()
        league_id = str(row.get("id", "")).strip()
        team_id = str(row.get("team", "")).strip()
        scoring = str(row.get("scoring", "")).strip()
        spec = f"{name}={source}:{league_id}:{team_id}"
        if scoring:
            spec += f":{scoring}"
        specs.append(spec)
    return parse_leagues(",".join(specs))


def _synthesized_default(roster_source: str, espn_league_id: str, espn_team_id: str,
                         sleeper_league_id: str) -> LeagueProfile:
    """Build the single fallback profile from the flat env vars (legacy setups)."""
    if roster_source == "sleeper":
        return LeagueProfile("default", "sleeper", sleeper_league_id, "")
    if roster_source == "manual":
        return LeagueProfile("default", "manual", "", "")
    return LeagueProfile("default", "espn", espn_league_id, espn_team_id)


def _warn(message: str) -> None:
    try:
        from rich import print as rprint
        rprint(f"[yellow]warning:[/yellow] {message}")
    except Exception:  # rich is a hard dep, but never let a warning crash load
        print(f"warning: {message}")


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    """Load settings from .env (if present) and the process environment."""
    load_dotenv(dotenv_path=env_file, override=False)

    scoring = (os.getenv("FF_SCORING") or "ppr").lower()
    if scoring not in SCORING_CODES:
        scoring = "ppr"

    roster_source = (os.getenv("FF_ROSTER_SOURCE") or "espn").lower()
    if roster_source not in {"espn", "sleeper", "manual"}:
        roster_source = "espn"

    data_dir = Path(os.getenv("FF_DATA_DIR", ".cache"))

    # Weight precedence: hardcoded defaults < learned file (calibrate --write) <
    # explicit FF_WEIGHT_* env overrides. Config stays the single owner of weights.
    default_weights = {"ecr": 0.60, "vegas": 0.18, "injury": 0.12, "weather": 0.10}
    base = dict(default_weights)
    base.update(_load_learned_weights(data_dir / "learned_weights.json"))
    weights = _validate_weights(
        {
            "ecr": _f("FF_WEIGHT_ECR", base["ecr"]),
            "vegas": _f("FF_WEIGHT_VEGAS", base["vegas"]),
            "injury": _f("FF_WEIGHT_INJURY", base["injury"]),
            "weather": _f("FF_WEIGHT_WEATHER", base["weather"]),
        },
        default_weights,
    )

    threshold = _f("FF_CLOSE_CALL_THRESHOLD", 5.0)
    if threshold < 0:
        _warn("FF_CLOSE_CALL_THRESHOLD is negative; using 5.0 instead.")
        threshold = 5.0

    espn_league_id = os.getenv("ESPN_LEAGUE_ID", "").strip()
    espn_team_id = os.getenv("ESPN_TEAM_ID", "").strip()
    sleeper_league_id = os.getenv("SLEEPER_LEAGUE_ID", "").strip()

    # League list precedence: FF_LEAGUES env > gitignored leagues.json file >
    # a single "default" profile synthesized from the flat env vars above (so
    # existing single-league setups keep working with no config change).
    leagues = parse_leagues(os.getenv("FF_LEAGUES", "").strip())
    if not leagues:
        leagues = _load_leagues_file(Path(os.getenv("FF_LEAGUES_FILE", "leagues.json")))
    if not leagues:
        leagues = [_synthesized_default(roster_source, espn_league_id, espn_team_id,
                                        sleeper_league_id)]
    default_league = os.getenv("FF_DEFAULT_LEAGUE", "").strip() or leagues[0].name

    return Settings(
        roster_source=roster_source,
        leagues=leagues,
        default_league=default_league,
        espn_league_id=espn_league_id,
        espn_team_id=espn_team_id,
        espn_s2=os.getenv("ESPN_S2", "").strip(),
        espn_swid=os.getenv("ESPN_SWID", "").strip(),
        manual_roster_file=Path(os.getenv("FF_MANUAL_ROSTER", "manual_roster.csv")),
        sleeper_username=os.getenv("SLEEPER_USERNAME", "").strip(),
        sleeper_league_id=sleeper_league_id,
        odds_api_key=os.getenv("ODDS_API_KEY", "").strip(),
        fantasypros_api_key=os.getenv("FANTASYPROS_API_KEY", "").strip(),
        scoring=scoring,
        weights=weights,
        close_call_threshold=threshold,
        preferred_experts=os.getenv("FF_PREFERRED_EXPERTS", "").strip(),
        injury_enabled=_b("FF_INJURY", True),
        weather_enabled=_b("FF_WEATHER", True),
        preseason_fill=_b("FF_PRESEASON_FILL", True),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        dashboard_url=os.getenv("FF_DASHBOARD_URL", "").strip(),
        repo_url=_repo_url(),
        data_dir=data_dir,
    )


def _repo_url() -> str:
    """FF_REPO_URL, else the GITHUB_* vars every Actions run exports."""
    explicit = os.getenv("FF_REPO_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if repo:
        server = (os.getenv("GITHUB_SERVER_URL", "").strip()
                  or "https://github.com").rstrip("/")
        return f"{server}/{repo}"
    return ""
