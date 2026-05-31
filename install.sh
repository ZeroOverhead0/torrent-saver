#!/bin/bash
# One-shot setup: create the venv, install dependencies, init the DB.
set -e
cd "$(dirname "$0")"

# /usr/bin/python3 (macOS CommandLineTools) avoids the launchd+TCC pyvenv.cfg
# read failures seen with Homebrew Python when spawned by a LaunchAgent.
PY="${PYTHON:-/usr/bin/python3}"

if [ ! -d .venv ]; then
  echo "Creating virtualenv with $PY …"
  "$PY" -m venv .venv
fi

echo "Installing dependencies…"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "Initialising database…"
.venv/bin/python -m app.db

echo "Done. Start with ./run.sh (standalone) or let the dashboard hub spawn it."
echo "Next: open the app and set your qBittorrent URL + password under Settings."
