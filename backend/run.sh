#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x ".venv/bin/uvicorn" ]; then
  UVICORN=".venv/bin/uvicorn"
elif command -v uvicorn >/dev/null 2>&1; then
  UVICORN="uvicorn"
else
  echo "uvicorn not found. Activate the venv or run: pip install -r requirements.txt" >&2
  exit 1
fi
exec "$UVICORN" app:app --host 127.0.0.1 --port 8765 "$@"
