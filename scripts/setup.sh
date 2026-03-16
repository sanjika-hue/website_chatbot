#!/usr/bin/env bash
# Setup: install tooling, pull latest, sync deps, copy .env if missing
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
UV="${HOME}/.local/bin/uv"

cd "$PROJECT_DIR"

# --- Install uv if missing ---
if ! command -v uv &>/dev/null && [ ! -f "$UV" ]; then
  echo "==> Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# --- Install tmux if missing ---
if ! command -v tmux &>/dev/null; then
  echo "==> Installing tmux..."
  sudo apt-get update -qq && sudo apt-get install -y -qq tmux
fi

echo "==> Pulling latest changes..."
git pull

# --- Create venv if missing ---
if [ ! -d .venv ]; then
  echo "==> Creating virtual environment..."
  $UV venv
fi

echo "==> Syncing dependencies..."
$UV pip install -r requirements.txt

if [ ! -f .env ]; then
  echo "==> No .env found — copying from .env.example"
  cp .env.example .env
  echo "    Edit .env with your config before starting the server."
fi

echo "==> Setup complete."
