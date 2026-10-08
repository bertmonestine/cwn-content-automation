#!/bin/bash
# Wrapper called by n8n: activates the virtual environment, logs output,
# and passes the exit code back so n8n can show failures.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
LOG="/var/log/content-pipeline.log"
exec >>"$LOG" 2>&1
echo "=== $(date -Iseconds) pipeline start ==="
if [[ ! -x .venv/bin/python ]]; then
  echo "ERROR: virtual environment missing. Create .venv and install requirements."
  exit 1
fi
exec .venv/bin/python scripts/pipeline_outline.py "$@"
