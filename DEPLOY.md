# Deploying EMBER to Streamlit Community Cloud

This guide covers publishing the dashboard so anyone with the link can use it. The app reads
its geospatial data from **your** private GCS bucket using **one server-side service
account** — visitors never need their own Google credentials, `gcloud`, or bucket access.
They just use the app, and it reads the data on their behalf.

> **What visitors can see:** everything the app renders (all utilities, fires, and overlap
> stats). The service-account key stays server-side in Streamlit secrets and is never sent to
> browsers, so users can't extract it or hit the bucket directly.

---

## 1. Prerequisites

1. **A GitHub repo** containing this `dashboard/` app (Streamlit Cloud deploys from GitHub).
   Ensure `.env`, `secrets/`, and `data/` stay git-ignored (they already are) so no key is
   ever committed.
2. **A read-only service account** with **Storage Object Viewer** on the data bucket (or,
   preferably, scoped to just the `EMBER/` prefix / a dedicated bucket — see §5). Download a
   JSON key for it. This is the same single credential described in `../DATA_GUIDE.md` §6.

### Create the read-only service account + key (gcloud)

Replace `PROJECT_ID` and, if different, the bucket name.

```bash
# a. Create the service account
gcloud iam service-accounts create ember-reader \
  --project=EMBER \
  --display-name="EMBER read-only data reader"

# b. Grant read-only access to the bucket (Storage Object Viewer)
gcloud storage buckets add-iam-policy-binding gs://data_main_gcs \
  --member="serviceAccount:ember-reader@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"

# c. Create a JSON key (this file goes into Streamlit secrets — never commit it)
gcloud iam service-accounts keys create ember-sa.json \
  --iam-account=ember-reader@PROJECT_ID.iam.gserviceaccount.com
```

**Tighter (optional) — restrict the SA to just the `EMBER/` prefix** with an IAM condition,
so a leaked key can't read the bucket's other datasets:

```bash
gcloud storage buckets add-iam-policy-binding gs://data_main_gcs \
  --member="serviceAccount:ember-reader@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer" \
  --condition='title=ember-prefix-only,expression=resource.name.startsWith("projects/_/buckets/data_main_gcs/objects/EMBER/")'
```

> If your org blocks SA key creation (`iam.disableServiceAccountKeyCreation`), an admin must
> allow it for this account — Streamlit Community Cloud needs a key, since (unlike Cloud Run)
> you can't attach a service account to it.

Console equivalent: **IAM & Admin → Service Accounts → Create**, name it `ember-reader`; then
**Cloud Storage → your bucket → Permissions → Grant access**, principal = the SA, role =
*Storage Object Viewer*; then back in the SA, **Keys → Add key → Create new key → JSON**.

The downloaded `ember-sa.json` maps field-for-field into the `[gcp_service_account]` TOML
table in §2.

---

## 2. Configure Streamlit secrets

In the Streamlit Cloud app page: **Manage app → Settings → Secrets**, paste TOML like below.
`core/gcp_auth.py` reads this at startup, writes the key to a temp file, and points every GCS
reader at it via `GOOGLE_APPLICATION_CREDENTIALS`.

```toml
# --- app config (Streamlit Cloud has no .env, so set these here) ---
EMBER_STORAGE_BACKEND = "gcs"
GCS_BUCKET = "data_main_gcs"
GCS_PREFIX = "EMBER"
# TILER_URL only matters once you publish raster COGs (see §4). Safe to leave as-is otherwise.
TILER_URL = "http://localhost:8000"

# --- read-only service account key ---
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "abc123..."
# IMPORTANT: use a TOML triple-quoted string so the PEM keeps real newlines.
private_key = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcw...
...many lines...
-----END PRIVATE KEY-----
"""
client_email = "ember-reader@your-project-id.iam.gserviceaccount.com"
client_id = "1234567890"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/ember-reader%40your-project-id.iam.gserviceaccount.com"
universe_domain = "googleapis.com"
```

> **The `private_key` newlines matter.** Use a `"""triple-quoted"""` block and paste the key
> with its real line breaks. A single-line value with literal `\n` will produce an invalid
> PEM and authentication will fail.

The `[gcp_service_account]` table is simply the contents of the downloaded JSON key,
translated to TOML (each JSON field becomes a `key = "value"` line).

---

## 3. Deploy

1. Push the repo to GitHub.
2. On <https://share.streamlit.io>, **New app** → pick the repo/branch.
3. **Main file path:** `dashboard/core/app/streamlit_app.py`.
4. **Advanced settings → Python dependencies:** `dashboard/requirements.txt` (auto-detected if
   at the chosen root). Everything the runtime needs (DuckDB, gcsfs, matplotlib, folium, …) is
   pinned there; the app does **not** need the DuckDB spatial extension (only the offline
   ingest scripts do).
5. Paste the secrets from §2, then **Deploy**.

---

## 4. Raster layers (COGs) — later

The map's raster overlays (e.g. sediment/turbidity) are served by **TiTiler**, a separate
process at `TILER_URL`. Streamlit Community Cloud runs **only** the single Streamlit process,
so a `localhost` tiler is not available there.

- **Today this doesn't block you:** `raster_assets` is empty, so the app renders no raster
  layer regardless (those panels show "Data not yet available"). All vector data — boundaries,
  fire perimeters, and every overlap table/chart — works fully.
- **When you publish COGs:** host TiTiler separately (e.g. Google Cloud Run), set `TILER_URL`
  in the app's secrets to that HTTPS URL, and add the Streamlit app's origin to the tiler's
  `CORS_ORIGINS`.

The tiler entrypoint (`core/tiler/main.py`) runs the **same** `bootstrap_gcp_credentials()` as
the app, so it uses the identical read-only credential. Give the tiler host that credential in
any one of these ways (GDAL reads `gs://` COGs with it):

1. **Attach the service account to the service (recommended on Cloud Run).** No key file —
   GDAL falls back to Application Default Credentials from the metadata server. If GDAL doesn't
   auto-detect the environment, set `CPL_MACHINE_IS_GCE=YES` (see `../DATA_GUIDE.md` §6.3).
2. **Provide the key file** via a `GOOGLE_APPLICATION_CREDENTIALS` env var pointing at the
   mounted JSON.
3. **Mount the same secret** as `.streamlit/secrets.toml` (with the identical
   `[gcp_service_account]` table); `bootstrap_gcp_credentials()` materializes it and points
   GDAL at it automatically.

---

## 5. Security & cost

- **Public = world-readable data** through the app UI. Only publish data you're comfortable
  exposing.
- **GCS egress is billed to your project** per visitor query. The catalog caches tables in
  memory per process to limit repeat reads, but traffic still incurs cost.
- **Scope the service account tightly.** Granting Storage Object Viewer on the whole
  `data_main_gcs` bucket also exposes unrelated datasets (`CECS2021`, `LFmod`, …) *to anyone
  who obtains the key*. The app only reads `EMBER/…`, but for a public deployment prefer a
  dedicated bucket for EMBER data, or an IAM condition restricting the SA to the `EMBER/`
  object prefix.
- **Never commit the key.** It lives only in Streamlit secrets (and, locally, in the
  git-ignored `secrets/` or your ADC login).

---

## 6. Local development is unchanged

With no Streamlit secrets present, `bootstrap_gcp_credentials()` is a no-op and the app keeps
authenticating through Application Default Credentials:

```bash
gcloud auth application-default login   # once
cd dashboard
python -m streamlit run core/app/streamlit_app.py
```

Or set `GOOGLE_APPLICATION_CREDENTIALS` in `.env` to a local service-account JSON path.
