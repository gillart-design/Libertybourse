#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_FILE="$ROOT_DIR/portfolio_simulator_app.py"
TARGET_FILE="$ROOT_DIR/portfolio_simulator_app.md"

{
  printf '# portfolio_simulator_app.py\n\n'
  printf 'Export synchronise du fichier source `portfolio_simulator_app.py`.\n\n'
  printf '## Code\n\n'
  printf '```python\n'
  cat "$SOURCE_FILE"
  printf '\n```\n'
} > "$TARGET_FILE"

echo "Export generated: $TARGET_FILE"
