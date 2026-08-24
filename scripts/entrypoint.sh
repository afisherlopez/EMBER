#!/usr/bin/env sh
# Burn-severity tiler entrypoint for Cloud Run.
set -e

PORT="${PORT:-8080}"

exec uvicorn core.tiler.main:app --host 0.0.0.0 --port "${PORT}"
