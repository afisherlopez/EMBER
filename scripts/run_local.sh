#!/usr/bin/env bash
# Run EMBER locally with a single command: starts the TiTiler tiler and the Streamlit
# app together, wired to each other, and shuts the tiler down when you stop the app.
#
# Usage:
#   ./scripts/run_local.sh
#
# By default this runs against the bundled local sample data (no cloud credentials
# needed). To preview the real GCS data locally instead, set these in ./.env before
# running:
#   EMBER_STORAGE_BACKEND=gcs
#   GCS_BUCKET=data_main_gcs
#   GCS_PREFIX=EMBER
#   GOOGLE_APPLICATION_CREDENTIALS=./secrets/ember-sa.json
#
# Optional overrides: APP_PORT (default 8501), TILER_PORT (default 8000).
set -euo pipefail

cd "$(dirname "$0")/.."

APP_PORT="${APP_PORT:-8501}"
TILER_PORT="${TILER_PORT:-8000}"

# Ensure the local sample dataset exists (no-op download; regenerates the small tables so
# their cog_uri paths match this checkout). Harmless when running against GCS.
python scripts/bootstrap_sample_data.py

# Start the tiler in the background and make sure it is torn down on exit.
uvicorn core.tiler.main:app --host 0.0.0.0 --port "${TILER_PORT}" &
TILER_PID=$!
trap 'kill "${TILER_PID}" 2>/dev/null || true' EXIT INT TERM

# Wait for the tiler to answer its health check before launching the app.
echo "Waiting for tiler on http://localhost:${TILER_PORT} ..."
for _ in $(seq 1 40); do
  if curl -sf "http://localhost:${TILER_PORT}/healthz" >/dev/null 2>&1; then
    echo "Tiler is up."
    break
  fi
  sleep 0.5
done

# Run the app in the foreground. Ctrl-C stops it and (via the trap) the tiler too.
TILER_URL="http://localhost:${TILER_PORT}" \
  streamlit run core/app/streamlit_app.py \
  --server.port "${APP_PORT}"
