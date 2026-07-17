"""Self-calibration (#7): learn blend weights from logged decisions vs outcomes."""

from .backtest import BacktestResult, backtest
from .learner import CalibrationResult, calibrate
from .log_reader import Decision, load_decisions
from .outcomes import OutcomeIndex, SleeperStatsClient, build_outcome_lookup

__all__ = [
    "CalibrationResult",
    "calibrate",
    "BacktestResult",
    "backtest",
    "Decision",
    "load_decisions",
    "OutcomeIndex",
    "SleeperStatsClient",
    "build_outcome_lookup",
]
