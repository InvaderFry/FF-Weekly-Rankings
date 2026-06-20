"""Put each signal's native units onto a common 0-100 scale.

Normalization is *relative to the candidate set being compared* — the right frame
for start/sit, where you care how your options rank against each other, not the
whole league. Min-max scaling keeps it deterministic and easy to test; a single
candidate (or a flat set) maps to the neutral midpoint, 50.

Pure functions only, so the blender and any future #7 optimizer can lean on them.
"""

from __future__ import annotations

from typing import Optional

NEUTRAL = 50.0


def to_0_100(values: dict[str, Optional[float]], higher_is_better: bool) -> dict[str, float]:
    """Scale available raw values to 0-100 within the candidate set.

    Keys whose value is None are omitted from the output (the signal had nothing
    for that player). With <2 usable values, or a zero-width range, everything
    present maps to NEUTRAL.
    """
    usable = {k: v for k, v in values.items() if v is not None}
    if not usable:
        return {}
    lo = min(usable.values())
    hi = max(usable.values())
    if hi == lo:
        return {k: NEUTRAL for k in usable}

    span = hi - lo
    out: dict[str, float] = {}
    for k, v in usable.items():
        frac = (v - lo) / span
        if not higher_is_better:
            frac = 1.0 - frac
        out[k] = round(100.0 * frac, 2)
    return out
