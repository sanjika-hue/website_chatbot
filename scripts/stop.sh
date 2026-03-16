#!/usr/bin/env bash
# Stop the running server and kill tmux session
set -euo pipefail

SESSION="tastebud"

echo "==> Stopping server..."
pkill -f '.venv/bin/python main.py' 2>/dev/null && echo "    Process stopped." || echo "    No running process found."
tmux kill-session -t "$SESSION" 2>/dev/null && echo "    Session '$SESSION' killed." || echo "    No tmux session found."
