#!/usr/bin/env bash
# Stop running server, pull + sync, then start in a tmux session
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SESSION="tastebud"

cd "$PROJECT_DIR"

echo "==> Stopping existing server..."
pkill -f '.venv/bin/python main.py' 2>/dev/null && echo "    Stopped." || echo "    No running server found."
tmux kill-session -t "$SESSION" 2>/dev/null || true
sleep 1

echo "==> Pulling & syncing..."
bash "$SCRIPT_DIR/setup.sh"

echo "==> Starting server in tmux session '$SESSION'..."
tmux new-session -d -s "$SESSION" "cd $PROJECT_DIR && .venv/bin/python main.py"
echo "    Attach with: tmux attach -t $SESSION"
