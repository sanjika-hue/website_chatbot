#!/usr/bin/env bash
# Point git to use tracked hooks from this directory
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
git config core.hooksPath "$SCRIPT_DIR"
echo "✅ Git hooks installed from hooks/"
