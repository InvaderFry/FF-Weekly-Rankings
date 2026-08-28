"""Durable on-disk cache primitives — write without a race, read without a crash.

Every cache in this package is an optimization: a miss costs one refetch, so no
cache operation may ever be the thing that fails a run. Two rules follow, and
they are here rather than repeated at each call site because both were already
gotten right in some places and missed in others.

**Writes are atomic through a per-process temp file.** The obvious spelling —
``path.with_name(path.name + ".tmp")`` — gives every process the *same*
temporary path, so two concurrent commands sharing ``FF_DATA_DIR`` race: one
``os.replace``\\ s the shared temp file away and the other raises
``FileNotFoundError`` from a command that was otherwise valid. ``mkstemp`` in
the destination's own directory gives each writer a name nobody else will
touch, and keeping it on the same filesystem is what makes the replace atomic.

**Reads treat a corrupt file as a miss.** A cache write interrupted by Ctrl-C,
a full disk, or a crash leaves a truncated file; an unguarded ``json.loads``
then raises on *every* subsequent run until the user finds and deletes it by
hand, turning a transient interruption into a persistent outage that outlives
its own TTL. Returning ``None`` costs one refetch instead.

Pure stdlib and no intra-package imports, so anything in the tree can use it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


def atomic_write_text(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` atomically, safe against concurrent writers.

    Readers see either the old contents or the new ones, never a partial file,
    and two processes writing the same path both succeed. Creates the parent
    directory if needed; the temp file is cleaned up if anything goes wrong.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)        # atomic: same directory, same filesystem
    except BaseException:
        tmp.unlink(missing_ok=True)  # our own temp file, so never anyone else's
        raise
    return path


def write_json(path: Path, data: Any, **dumps_kwargs) -> Path:
    """``atomic_write_text`` of ``json.dumps(data)`` — the common case."""
    return atomic_write_text(path, json.dumps(data, **dumps_kwargs))


def read_json_or_none(path: Path) -> Optional[Any]:
    """The decoded JSON at ``path``, or ``None`` if it is missing or unreadable.

    A miss and a corrupt file are deliberately the same answer: the caller's
    next move is to refetch either way, and distinguishing them would only
    invite someone to raise on the second.
    """
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None
