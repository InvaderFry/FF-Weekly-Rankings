"""Configuration loaded from environment / .env.

One place owns the blend weights and thresholds so a future #7 calibrator can
rewrite them programmatically without touching the engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Map of user-facing scoring choice -> FantasyPros scoring code.
SCORING_CODES = {"ppr": "PPR", "half": "HALF", "std": "STD"}


@dataclass
class Settings:
    # Roster source: espn (default) | sleeper | manual
    roster_source: str = "espn"
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
    # Ranking backbone: fantasypros (default) | journalists
    ranking_source: str = "fantasypros"
    journalists_file: Path = field(default_factory=lambda: Path("journalists_rankings.csv"))
    cbs_rankings_url: str = ""   # optional scraper override
    boone_article_url: str = ""  # optional scraper override
    scoring: str = "ppr"
    weights: dict[str, float] = field(default_factory=lambda: {"ecr": 0.75, "vegas": 0.25})
    close_call_threshold: float = 5.0
    data_dir: Path = field(default_factory=lambda: Path(".cache"))

    @property
    def scoring_code(self) -> str:
        return SCORING_CODES.get(self.scoring, "PPR")

    @property
    def results_log_path(self) -> Path:
        return self.data_dir / "results_log.jsonl"


def _f(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    """Load settings from .env (if present) and the process environment."""
    load_dotenv(dotenv_path=env_file, override=False)

    scoring = (os.getenv("FF_SCORING") or "ppr").lower()
    if scoring not in SCORING_CODES:
        scoring = "ppr"

    roster_source = (os.getenv("FF_ROSTER_SOURCE") or "espn").lower()
    if roster_source not in {"espn", "sleeper", "manual"}:
        roster_source = "espn"

    ranking_source = (os.getenv("FF_RANKING_SOURCE") or "fantasypros").lower()
    if ranking_source not in {"fantasypros", "journalists"}:
        ranking_source = "fantasypros"

    return Settings(
        roster_source=roster_source,
        ranking_source=ranking_source,
        journalists_file=Path(os.getenv("FF_JOURNALISTS_FILE", "journalists_rankings.csv")),
        cbs_rankings_url=os.getenv("CBS_RANKINGS_URL", "").strip(),
        boone_article_url=os.getenv("BOONE_ARTICLE_URL", "").strip(),
        espn_league_id=os.getenv("ESPN_LEAGUE_ID", "").strip(),
        espn_team_id=os.getenv("ESPN_TEAM_ID", "").strip(),
        espn_s2=os.getenv("ESPN_S2", "").strip(),
        espn_swid=os.getenv("ESPN_SWID", "").strip(),
        manual_roster_file=Path(os.getenv("FF_MANUAL_ROSTER", "manual_roster.csv")),
        sleeper_username=os.getenv("SLEEPER_USERNAME", "").strip(),
        sleeper_league_id=os.getenv("SLEEPER_LEAGUE_ID", "").strip(),
        odds_api_key=os.getenv("ODDS_API_KEY", "").strip(),
        fantasypros_api_key=os.getenv("FANTASYPROS_API_KEY", "").strip(),
        scoring=scoring,
        weights={
            "ecr": _f("FF_WEIGHT_ECR", 0.75),
            "vegas": _f("FF_WEIGHT_VEGAS", 0.25),
        },
        close_call_threshold=_f("FF_CLOSE_CALL_THRESHOLD", 5.0),
        data_dir=Path(os.getenv("FF_DATA_DIR", ".cache")),
    )
