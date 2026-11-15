#!/usr/bin/env bash
# Append this run's new decision-log rows to the calibration-data branch.
#
# Two workflows write this branch (weekly-report and chatops) from different
# concurrency groups, so the branch can move between this run's restore and its
# push. Copying the local file over the branch would then erase the other run's
# rows. So each attempt fetches the branch, appends only the local rows it does
# not already hold (the log is append-only and every row carries its own
# timestamp, so a row is identified by its exact text), and pushes; a rejected
# push is retried from a fresh fetch.
set -euo pipefail
if [ ! -s .cache/results_log.jsonl ]; then
  echo "Nothing logged this run (preseason, or a signal served the wrong week) — leaving $LOG_BRANCH alone."
  exit 0
fi
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
LOCAL="$(pwd)/.cache/results_log.jsonl"
ATTEMPTS="${PERSIST_ATTEMPTS:-4}"
BACKOFF="${PERSIST_BACKOFF:-2}"
WORKTREE="$(mktemp -d)"
NEW_ROWS="$(mktemp)"
trap 'git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || rm -rf "$WORKTREE"; rm -f "$NEW_ROWS"' EXIT

for attempt in $(seq 1 "$ATTEMPTS"); do
  git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$WORKTREE"
  if git fetch --depth=1 origin "$LOG_BRANCH" 2>/dev/null; then
    git worktree add --detach "$WORKTREE" FETCH_HEAD
    git -C "$WORKTREE" checkout -B "$LOG_BRANCH"
  else
    git worktree add --detach "$WORKTREE"
    git -C "$WORKTREE" checkout --orphan "$LOG_BRANCH"
    git -C "$WORKTREE" rm -rf --quiet . || true
  fi
  touch "$WORKTREE/$LOG_FILE"
  # Rows in the local log the branch does not hold yet, in local order.
  awk 'FILENAME == ARGV[1] { seen[$0] = 1; next } !($0 in seen)' \
    "$WORKTREE/$LOG_FILE" "$LOCAL" > "$NEW_ROWS"
  if [ ! -s "$NEW_ROWS" ]; then
    echo "Log unchanged; nothing to push."
    exit 0
  fi
  cat "$NEW_ROWS" >> "$WORKTREE/$LOG_FILE"
  # -f is required: results_log.jsonl is gitignored as cache everywhere
  # else in this repo, so a plain `git add` would silently do nothing and
  # the log would never actually persist.
  git -C "$WORKTREE" add -f "$LOG_FILE"
  git -C "$WORKTREE" commit -q -m "Log week $(date -u +%Y-%m-%d) decisions"
  if git -C "$WORKTREE" push origin "$LOG_BRANCH"; then
    echo "Pushed $(wc -l < "$NEW_ROWS") new decision(s) to $LOG_BRANCH."
    exit 0
  fi
  echo "Push to $LOG_BRANCH rejected (attempt $attempt of $ATTEMPTS); refetching and re-applying."
  sleep $((attempt * BACKOFF))
done
echo "error: could not push the decision log to $LOG_BRANCH after $ATTEMPTS attempts." >&2
exit 1
