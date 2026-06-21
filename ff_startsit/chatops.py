"""Map an issue-comment slash command to CLI argv (for the ChatOps workflow).

Pure and safe: only a fixed set of commands is recognized, each maps to a specific
CLI subcommand with validated arguments — there is no arbitrary shell. Anything
that isn't a known command returns None so the bot stays silent.

Examples:
    /lineup                          -> ["lineup", "--md"]
    /report                          -> ["report"]
    /rank RB                         -> ["rank", "--pos", "RB", "--md"]
    /rank RB week 5                  -> ["rank", "--pos", "RB", "--md", "--week", "5"]
    /compare Josh Allen | Jalen Hurts-> ["compare", "Josh Allen", "Jalen Hurts", "--md"]
    /lineup source manual            -> ["lineup", "--md", "--source", "manual"]
"""

from __future__ import annotations

from typing import Optional

POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF", "DST"}
SOURCES = {"espn", "sleeper", "manual"}
RANKINGS = {"fantasypros", "journalists"}
# Inline option keywords usable on any command: `week N`, `source X`, `league ID`,
# `team ID`, `ranking fantasypros|journalists`.
_OPTION_KEYS = {"week", "source", "league", "team", "ranking"}


def _first_line(body: str) -> str:
    for line in (body or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _split_options(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Peel trailing `key value` option pairs off the end of the token list.

    Returns (remaining_tokens, option_flags). Scans from the left so option pairs
    must come after the command's positional args.
    """
    flags: list[str] = []
    head: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i].lower()
        if tok in _OPTION_KEYS and i + 1 < len(tokens):
            value = tokens[i + 1]
            if tok == "source":
                if value.lower() not in SOURCES:
                    return tokens, []  # invalid -> ignore options, let caller decide
                value = value.lower()
            elif tok == "ranking":
                if value.lower() not in RANKINGS:
                    return tokens, []
                value = value.lower()
            flags += [f"--{tok}", value]
            i += 2
        else:
            head.append(tokens[i])
            i += 1
    return head, flags


def parse_command(body: str) -> Optional[list[str]]:
    """Return CLI argv for a recognized slash command, else None."""
    line = _first_line(body)
    if not line.startswith("/"):
        return None
    parts = line[1:].split()
    if not parts:
        return None
    cmd, rest = parts[0].lower(), parts[1:]

    if cmd == "lineup":
        _, flags = _split_options(rest)
        return ["lineup", "--md", *flags]

    if cmd == "report":
        _, flags = _split_options(rest)
        return ["report", *flags]

    if cmd == "rank":
        head, flags = _split_options(rest)
        if not head:
            return None
        pos = head[0].upper()
        if pos not in POSITIONS:
            return None
        return ["rank", "--pos", pos, "--md", *flags]

    if cmd == "compare":
        # Everything after "/compare"; names separated by "|", multi-word kept.
        after = line[1:].split(None, 1)
        if len(after) < 2 or "|" not in after[1]:
            return None
        names = [n.strip() for n in after[1].split("|")]
        # Trailing options (e.g. "week 5") live on the final name segment.
        head_tokens, flags = _split_options(names[-1].split())
        names[-1] = " ".join(head_tokens).strip()
        names = [n for n in names if n]
        if len(names) < 2:
            return None
        return ["compare", *names, "--md", *flags]

    return None
