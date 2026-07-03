#!/usr/bin/env sh
# Container entrypoint for EMBER on Cloud Run.
# Selects which service to run via SERVICE (app|tiler) and binds to Cloud Run's $PORT.
set -e

PORT="${PORT:-8080}"

case "${SERVICE:-app}" in
  tiler)
    exec uvicorn core.tiler.main:app --host 0.0.0.0 --port "${PORT}"
    ;;
  app)
    exec streamlit run core/app/streamlit_app.py \
      --server.port "${PORT}" \
      --server.address 0.0.0.0 \
      --server.headless true
    ;;
  *)
    echo "Unknown SERVICE='${SERVICE}'. Set SERVICE=app or SERVICE=tiler." >&2
    exit 1
    ;;
esac
