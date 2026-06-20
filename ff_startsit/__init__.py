"""Weekly fantasy football start/sit tool.

v1 blends two signals:
  - ECR  (#4): FantasyPros expert-consensus rankings as the robust backbone.
  - Vegas (#5): The Odds API implied team totals as a scoring-environment nudge.

Designed to grow into #7 (ensemble + self-calibration): signals are pluggable
(``ff_startsit.sources.base.Signal``), blend weights are configurable, and every
recommendation is logged (``ff_startsit.results_log``) so a learner can later
re-weight inputs from real outcomes.
"""

__version__ = "0.1.0"
