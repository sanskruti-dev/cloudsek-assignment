#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p .run/mongo_data .run/logs

if ! command -v mongod >/dev/null; then
  echo "mongod not found on PATH. Install MongoDB Community Edition first." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "Creating Python virtualenv (.venv)…"
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
  .venv/bin/pip install -r requirements.txt >/dev/null
fi

if ! lsof -nP -iTCP:27017 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Starting mongod on 127.0.0.1:27017 (logs: .run/logs/mongod.log)…"
  mongod --dbpath .run/mongo_data --port 27017 --bind_ip 127.0.0.1 \
    --logpath .run/logs/mongod.log --quiet --fork
else
  echo "mongod already listening on 27017 — reusing it."
fi

export APP_ENV=local
export LOG_LEVEL=INFO
export API_HOST=127.0.0.1
export API_PORT=8000
export API_PREFIX=/api/v1
export MONGO_URI=mongodb://127.0.0.1:27017
export MONGO_DB=metadata_inventory
export MONGO_COLLECTION=url_metadata
export MONGO_STARTUP_RETRY_ATTEMPTS=10
export MONGO_STARTUP_RETRY_DELAY_S=1.0
export FETCH_TIMEOUT_S=15.0
export FETCH_MAX_REDIRECTS=5
export FETCH_MAX_BYTES=5242880
export FETCH_USER_AGENT='HTTPMetadataInventory/1.0 (+https://example.com/bot)'
export BLOCK_PRIVATE_NETWORKS=false
export ALLOWED_SCHEMES=http,https

echo "Starting FastAPI on http://${API_HOST}:${API_PORT} (logs: .run/logs/api.log)…"
exec .venv/bin/uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" --log-level info
