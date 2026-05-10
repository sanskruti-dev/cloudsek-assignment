#!/usr/bin/env bash
# Stops the API + local mongod started outside Docker.
# Pairs with the manual `mongod --fork` + `uvicorn` flow used during development.
set -euo pipefail

cd "$(dirname "$0")/.."

if pid=$(lsof -nP -iTCP:8000 -sTCP:LISTEN -t 2>/dev/null); then
  echo "Stopping uvicorn (pid=$pid)…"
  kill "$pid" 2>/dev/null || true
fi

if pid=$(lsof -nP -iTCP:27017 -sTCP:LISTEN -t 2>/dev/null); then
  echo "Stopping mongod (pid=$pid)…"
  mongod --dbpath .run/mongo_data --shutdown 2>/dev/null || kill "$pid" 2>/dev/null || true
fi

echo "Done."
