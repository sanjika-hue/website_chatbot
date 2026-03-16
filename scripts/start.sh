#!/usr/bin/env bash
# Start the server in a tmux session
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SESSION="tastebud"

cd "$PROJECT_DIR"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' already running. Attach with: tmux attach -t $SESSION"
  exit 0
fi

tmux new-session -d -s "$SESSION" "cd $PROJECT_DIR && .venv/bin/python main.py"
echo "==> Server started in tmux session '$SESSION'"
echo "    Attach with: tmux attach -t $SESSION"
