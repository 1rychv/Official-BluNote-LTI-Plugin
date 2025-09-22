#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/server-py"
FRONTEND_DIR="$ROOT/web"
UVICORN_BIN="$BACKEND_DIR/.venv/bin/uvicorn"

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]] && ps -p "${BACKEND_PID}" > /dev/null 2>&1; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

if [[ ! -x "$UVICORN_BIN" ]]; then
  echo "Backend venv not ready. Run setup in server-py first." >&2
  exit 1
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "Frontend deps missing. Run npm install in web first." >&2
  exit 1
fi

(
  cd "$BACKEND_DIR"
  source .venv/bin/activate
  uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload
) &
BACKEND_PID=$!

cd "$FRONTEND_DIR"
npm run dev
