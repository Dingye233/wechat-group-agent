#!/usr/bin/env bash
# Start Group Agent on macOS / Linux. Real WeChat is Windows-only; this script
# is intended for the demo adapter or a custom adapter.
#
# The first run bootstraps the environment (venv, deps, .env, data/, sqlite
# schema) through scripts/bootstrap.py; later runs only verify it quickly.
# Extra args are forwarded, e.g.  ./start.sh --extras all --mirror tsinghua
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  for candidate in python3.12 python3.11 python3.13 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if [ -x ".venv/bin/python" ]; then
  .venv/bin/python scripts/bootstrap.py "$@"
elif [ -x ".venv312/bin/python" ]; then
  .venv312/bin/python scripts/bootstrap.py "$@"
elif base_python="$(find_python)"; then
  "$base_python" scripts/bootstrap.py "$@"
else
  echo "[setup] Python 3.11+ was not found. Install it and run ./start.sh again." >&2
  exit 1
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON=".venv312/bin/python"
fi

echo "Dashboard: http://127.0.0.1:8000"
if command -v open >/dev/null 2>&1; then
  (sleep 2; open http://127.0.0.1:8000) &
elif command -v xdg-open >/dev/null 2>&1; then
  (sleep 2; xdg-open http://127.0.0.1:8000) &
fi

exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
