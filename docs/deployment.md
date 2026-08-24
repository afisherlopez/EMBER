# EMBER Deployment

The supported production setup is:

- Streamlit Community Cloud hosts `core/app/streamlit_app.py`.
- Cloud Run hosts the public burn-severity tile endpoint.
- Private GCS stores catalog tables, case-study files, the severity manifest, and COGs.

## 1. Prepare GCS permissions

Use the existing `ember-reader@data-gcp-main.iam.gserviceaccount.com` service account.
It needs `storage.objects.get` for objects below `EMBER/`. The tile service does not
need write access.

If the in-app admin editor will publish changes, grant that service account object
create, delete, and get permissions only below `EMBER/tables/` and `EMBER/backups/`.
`roles/storage.objectAdmin` with IAM conditions on those two prefixes is the simplest
supported configuration. Do not grant bucket-wide admin access.

The deployment script attaches this service account to Cloud Run. Streamlit Cloud uses
a downloaded JSON key for the same account.

## 2. Deploy the burn-severity tiler

Enable Cloud Run, Cloud Build, and Artifact Registry once:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create ember \
  --repository-format=docker \
  --location=us-central1
```

Deploy the tiler and explicitly allow the Streamlit origin:

```bash
STREAMLIT_ORIGIN=https://ember-dashboard.streamlit.app \
PROJECT_ID=data-gcp-main \
GCS_BUCKET=data_main_gcs \
GCS_PREFIX=EMBER \
SERVICE_ACCOUNT=ember-reader@data-gcp-main.iam.gserviceaccount.com \
./scripts/deploy_cloudrun.sh
```

The script prints the tile service URL. If the deployer cannot set public IAM, a
project owner must run:

```bash
gcloud run services add-iam-policy-binding ember-tiler \
  --region=us-central1 \
  --project=data-gcp-main \
  --member=allUsers \
  --role=roles/run.invoker
```

Verify `https://TILER_URL/healthz` returns `{"status":"ok"}`.

## 3. Configure Streamlit Community Cloud

Create the app from this repository with:

- Main file: `core/app/streamlit_app.py`
- Python version: **3.12**

The requirements file intentionally lives beside the main file at
`core/app/requirements.txt`. This keeps native tiler dependencies out of the
Streamlit runtime.

Add these values in the app's Secrets editor:

```toml
EMBER_STORAGE_BACKEND = "gcs"
GCS_BUCKET = "data_main_gcs"
GCS_PREFIX = "EMBER"
GCS_PROJECT = "data-gcp-main"
TILER_URL = "https://YOUR-EMBER-TILER-URL"
EMBER_WILDFIRE_STATES = "WA,OR,CA,CO"
EMBER_ADMIN_PASSWORD = "choose-a-shared-admin-password"

[gcp_service_account]
type = "service_account"
project_id = "data-gcp-main"
private_key_id = "value from the downloaded JSON key"
private_key = """-----BEGIN PRIVATE KEY-----
the full private key, preserving its line breaks
-----END PRIVATE KEY-----
"""
client_email = "ember-reader@data-gcp-main.iam.gserviceaccount.com"
client_id = "value from the downloaded JSON key"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "value from the downloaded JSON key"
universe_domain = "googleapis.com"
```

The `client_email`, `client_id`, certificate URL, and all other fields are copied
verbatim from the downloaded service-account JSON. Do not add braces around values.

## 4. Verify before retiring the old app

Open the Streamlit URL and check:

1. All four views load.
2. Utility and wildfire maps render.
3. Enabling Burn Severity loads colored raster tiles.
4. The admin password opens the editor.
5. A test admin write succeeds only if scoped write permission was intentionally granted.

The deployment script does not delete the existing `ember-app` Cloud Run service.
After the Streamlit deployment is verified, remove the old service manually if desired:

```bash
gcloud run services delete ember-app \
  --region=us-central1 \
  --project=data-gcp-main
```
