#!/usr/bin/env bash
# Usage: bash pushscript.sh ["commit message"]
# Stages your code changes (never the bot-owned docs/data.json / seen.json),
# commits, pulls the bot's overnight scan, and pushes — all in one step.
set -e
cd "$(dirname "$0")"

MSG="${1:-update}"

# Discard any local test-run changes to the bot-owned files so they can't
# cause a conflict or get committed by accident.
git checkout -- docs/data.json seen.json 2>/dev/null || true

git add -A -- . ':!docs/data.json' ':!seen.json'

if git diff --cached --quiet; then
  echo "-> No code changes to commit."
else
  git commit -m "$MSG"
fi

echo "-> Pulling latest (rebase)..."
git pull --rebase origin main

echo "-> Pushing..."
git push origin main

echo "Done."
