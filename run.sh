#!/bin/bash
# Run Torrent Saver standalone (dev / self-hosting).
# In the Claude dashboard the hub spawns it — see dashboard/services.py.
cd "$(dirname "$0")"

PORT="${PORT:-18780}"
ROOT_PATH="${ROOT_PATH:-/torrentsaver}"
HOST="${HOST:-127.0.0.1}"

# Prefer the local venv; fall back to whatever uvicorn is on PATH.
if [ -x .venv/bin/uvicorn ]; then
  UVICORN=.venv/bin/uvicorn
else
  UVICORN=uvicorn
fi

exec "$UVICORN" app.main:app \
  --host "$HOST" --port "$PORT" \
  --root-path "$ROOT_PATH" \
  --log-level info
