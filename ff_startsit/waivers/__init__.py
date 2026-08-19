"""Waiver-wire adds/drops and trade suggestions — the Tuesday-evening companion.

Where the start/sit half of the app asks "who do I *start*?", this half asks
"who do I *acquire*?". It is deliberately a separate pass with a separate seam:

- **Nothing here is a blend signal.** No ``FF_WEIGHT_*`` entry, no change to
  ``_validate_weights``, no effect on any existing start/sit ranking. The
  preferred journalists' ranks and the writers' column mentions are annotation,
  exactly as ``sources/journalists.py`` is.
- **Nothing here is ever written to the #7 results log.** A waiver row is not a
  start/sit decision: it would double-count players inside one decision, count
  free agents the user never started, and inflate the ``calibrate --write``
  floors with evidence nobody acted on. Every ``recommend`` call in this package
  passes ``log=False`` and the ``waivers`` command has no ``--log`` flag.
"""
