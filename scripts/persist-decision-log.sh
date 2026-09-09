#!/usr/bin/env bash
set -euo pipefail
if [ ! -s .cache/results_log.jsonl ]; then
  echo "Nothing logged this run (preseason, or a signal served the wrong week) — leaving $LOG_BRANCH alone."
  exit 0
fi
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
WORKTREE="$(mktemp -d)"
trap 'git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rmdir "$WORKTREE" 2>/dev/null || true' EXIT
if git fetch --depth=1 origin "$LOG_BRANCH" 2>/dev/null; then
  git worktree add --detach "$WORKTREE" FETCH_HEAD
  git -C "$WORKTREE" checkout -B "$LOG_BRANCH"
else
  git worktree add --detach "$WORKTREE"
  git -C "$WORKTREE" checkout --orphan "$LOG_BRANCH"
  git -C "$WORKTREE" rm -rf --quiet . || true
fi
cp .cache/results_log.jsonl "$WORKTREE/$LOG_FILE"
# -f is required: results_log.jsonl is gitignored as cache everywhere
# else in this repo, so a plain `git add` would silently do nothing and
# the log would never actually persist.
git -C "$WORKTREE" add -f "$LOG_FILE"
if git -C "$WORKTREE" diff --cached --quiet; then
  echo "Log unchanged; nothing to push."
else
  git -C "$WORKTREE" commit -m "Log week $(date -u +%Y-%m-%d) decisions"
  git -C "$WORKTREE" push origin "$LOG_BRANCH"
  echo "Pushed $(wc -l < .cache/results_log.jsonl) decision(s) to $LOG_BRANCH."
fi
