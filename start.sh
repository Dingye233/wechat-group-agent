#!/usr/bin/env bash
# Start Group Agent on macOS / Linux. Real WeChat (WCF) is Windows-only;
# this script is intended for the demo adapter or a custom adapter.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
fi

echo "Dashboard: http://127.0.0.1:8000"
if command -v open >/dev/null 2>&1; then
  (sleep 2; open http://127.0.0.1:8000) &
elif command -v xdg-open >/dev/null 2>&1; then
  (sleep 2; xdg-open http://127.0.0.1:8000) &
fi

exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
