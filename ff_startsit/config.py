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
    scoring: str = "ppr"
    weights: dict[str, float] = field(
        default_factory=lambda: {"ecr": 0.65, "vegas": 0.20, "injury": 0.15})
    close_call_threshold: float = 5.0
    injury_enabled: bool = True
    # Distribution
    discord_webhook_url: str = ""
    dashboard_url: str = ""
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
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


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
    """
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
    load — the defaults then stand.
    """
    if not path.exists():
        return {}
    try:
        import json
        data = json.loads(path.read_text())
        return {str(k): float(v) for k, v in data.items()}
    except Exception:
        _warn(f"Could not read learned weights at {path}; ignoring.")
        return {}


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
    default_weights = {"ecr": 0.65, "vegas": 0.20, "injury": 0.15}
    base = dict(default_weights)
    base.update(_load_learned_weights(data_dir / "learned_weights.json"))
    weights = _validate_weights(
        {
            "ecr": _f("FF_WEIGHT_ECR", base["ecr"]),
            "vegas": _f("FF_WEIGHT_VEGAS", base["vegas"]),
            "injury": _f("FF_WEIGHT_INJURY", base["injury"]),
        },
        default_weights,
    )

    threshold = _f("FF_CLOSE_CALL_THRESHOLD", 5.0)
    if threshold < 0:
        _warn("FF_CLOSE_CALL_THRESHOLD is negative; using 5.0 instead.")
        threshold = 5.0

    return Settings(
        roster_source=roster_source,
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
        weights=weights,
        close_call_threshold=threshold,
        injury_enabled=_b("FF_INJURY", True),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        dashboard_url=os.getenv("FF_DASHBOARD_URL", "").strip(),
        data_dir=data_dir,
    )
