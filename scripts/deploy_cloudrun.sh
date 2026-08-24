#!/usr/bin/env bash
# Deploy EMBER's burn-severity tiler to Google Cloud Run.
#
# Prereqs (one-time):
#   - gcloud CLI installed and authenticated:   gcloud auth login
#   - Required APIs enabled:
#       gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
#         artifactregistry.googleapis.com
#   - An Artifact Registry Docker repo (default name "ember"):
#       gcloud artifacts repositories create ember \
#         --repository-format=docker --location="$REGION"
#   - A GCS bucket containing the published burn-severity COGs and manifest.
#   - A Cloud Run service account with Storage Object Viewer on that data.
#
# Usage:
#   PROJECT_ID=my-proj GCS_BUCKET=my-bucket STREAMLIT_ORIGIN=https://example.streamlit.app \
#   SERVICE_ACCOUNT=ember-sa@my-proj.iam.gserviceaccount.com \
#   ./scripts/deploy_cloudrun.sh
#
# Optional overrides: REGION, REPO, GCS_PREFIX, TILER_SERVICE, IMAGE_TAG
set -euo pipefail

# Existing EMBER defaults can be overridden without editing this script.
PROJECT_ID="${PROJECT_ID:-data-gcp-main}"
GCS_BUCKET="${GCS_BUCKET:-data_main_gcs}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-ember-reader@data-gcp-main.iam.gserviceaccount.com}"
: "${STREAMLIT_ORIGIN:?Set STREAMLIT_ORIGIN to the deployed app origin, such as https://ember-dashboard.streamlit.app}"

REGION="${REGION:-us-central1}"
REPO="${REPO:-ember}"
GCS_PREFIX="${GCS_PREFIX:-EMBER}"
TILER_SERVICE="${TILER_SERVICE:-ember-tiler}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/ember-tiler:${IMAGE_TAG}"

echo "==> Building image with Cloud Build: ${IMAGE}"
gcloud builds submit --project "${PROJECT_ID}" --tag "${IMAGE}" .

# GOOGLE_APPLICATION_CREDENTIALS is intentionally left unset so the attached
# service account is used via Application Default Credentials.
echo "==> Deploying tiler service: ${TILER_SERVICE}"
gcloud run deploy "${TILER_SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${IMAGE}" \
  --service-account "${SERVICE_ACCOUNT}" \
  --allow-unauthenticated \
  --set-env-vars "EMBER_STORAGE_BACKEND=gcs,GCS_BUCKET=${GCS_BUCKET},GCS_PREFIX=${GCS_PREFIX},CORS_ORIGINS=${STREAMLIT_ORIGIN}"

TILER_URL="$(gcloud run services describe "${TILER_SERVICE}" \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format='value(status.url)')"
echo "==> Tiler URL: ${TILER_URL}"

echo ""
echo "Tiler deploy complete: ${TILER_URL}"
echo "Set TILER_URL=${TILER_URL} in Streamlit Community Cloud secrets."
echo "Any existing ember-app Cloud Run service is unchanged."
