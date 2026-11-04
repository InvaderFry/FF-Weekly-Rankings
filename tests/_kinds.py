"""The board kind each real source sets, for fakes that stand in for one.

``ExpertBoard.header`` tags a column by kind ("Boone WW", "Boone ROS"), so a
fake that left ``kind`` at its default would render a trade chart as a weekly
list ("Richard Wk") — a header no real run produces.
"""

KINDS = {"richard_chart": "ros", "boone_trade_values": "ros", "boone_values": "ros",
         "boone_waivers": "waiver", "eisenberg_waivers": "waiver"}


def kind_of(source_id: str) -> str:
    return KINDS.get(source_id, "weekly")
