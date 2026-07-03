#!/usr/bin/env bash
# Deploy EMBER to Google Cloud Run as two services (tiler + app) from one image.
#
# Prereqs (one-time):
#   - gcloud CLI installed and authenticated:   gcloud auth login
#   - Required APIs enabled:
#       gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
#         artifactregistry.googleapis.com
#   - An Artifact Registry Docker repo (default name "ember"):
#       gcloud artifacts repositories create ember \
#         --repository-format=docker --location="$REGION"
#   - A GCS bucket with tables/ and cogs/, and a service account that can read it,
#     granted to both Cloud Run services (see SERVICE_ACCOUNT below).
#
# Usage:
#   PROJECT_ID=my-proj GCS_BUCKET=my-bucket \
#   SERVICE_ACCOUNT=ember-sa@my-proj.iam.gserviceaccount.com \
#   ./scripts/deploy_cloudrun.sh
#
# Optional overrides: REGION, REPO, GCS_PREFIX, TILER_SERVICE, APP_SERVICE, IMAGE_TAG
set -euo pipefail

# --- required config -------------------------------------------------------
PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your GCP project id}"
GCS_BUCKET="${GCS_BUCKET:?Set GCS_BUCKET to the bucket holding tables/ and cogs/}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:?Set SERVICE_ACCOUNT to the runtime service-account email}"

# --- optional config -------------------------------------------------------
REGION="${REGION:-us-central1}"
REPO="${REPO:-ember}"
GCS_PREFIX="${GCS_PREFIX:-}"
TILER_SERVICE="${TILER_SERVICE:-ember-tiler}"
APP_SERVICE="${APP_SERVICE:-ember-app}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/ember:${IMAGE_TAG}"

echo "==> Building image with Cloud Build: ${IMAGE}"
gcloud builds submit --project "${PROJECT_ID}" --tag "${IMAGE}" .

# --- 1) Tiler --------------------------------------------------------------
# GOOGLE_APPLICATION_CREDENTIALS is intentionally left unset so the attached
# service account is used via Application Default Credentials (per README).
echo "==> Deploying tiler service: ${TILER_SERVICE}"
gcloud run deploy "${TILER_SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "${SERVICE_ACCOUNT}" \
  --allow-unauthenticated \
  --set-env-vars "SERVICE=tiler,EMBER_STORAGE_BACKEND=gcs,GCS_BUCKET=${GCS_BUCKET},GCS_PREFIX=${GCS_PREFIX}"

TILER_URL="$(gcloud run services describe "${TILER_SERVICE}" \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format='value(status.url)')"
echo "==> Tiler URL: ${TILER_URL}"

# --- 2) App ----------------------------------------------------------------
echo "==> Deploying app service: ${APP_SERVICE}"
gcloud run deploy "${APP_SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "${SERVICE_ACCOUNT}" \
  --allow-unauthenticated \
  --set-env-vars "SERVICE=app,EMBER_STORAGE_BACKEND=gcs,GCS_BUCKET=${GCS_BUCKET},GCS_PREFIX=${GCS_PREFIX},TILER_URL=${TILER_URL}"

APP_URL="$(gcloud run services describe "${APP_SERVICE}" \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format='value(status.url)')"
echo "==> App URL: ${APP_URL}"

# --- 3) Wire CORS ----------------------------------------------------------
# The browser loads tiles from the tiler using the app's origin, so the tiler
# must allow the app URL. This is done after the app URL is known.
echo "==> Updating tiler CORS_ORIGINS to allow ${APP_URL}"
gcloud run services update "${TILER_SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --update-env-vars "CORS_ORIGINS=${APP_URL}"

echo ""
echo "Deploy complete."
echo "  App:   ${APP_URL}"
echo "  Tiler: ${TILER_URL}"
