# EMBER v1

EMBER (Environmental and economic Measurements of Burn Events on water Resources) is a read-only Streamlit dashboard for viewing the impact of one wildfire on one utility source-water area at a time.

The app only performs lookup and visualization over precomputed assets (Parquet + COG), with dynamic raster tiles served through TiTiler.

## Architecture

```text
                 +---------------------------+
                 |      Streamlit app        |
                 |  core/app/streamlit_app   |
                 +------------+--------------+
                              |
                   (feature + map requests)
                              |
          +-------------------+---------------------+
          |                                         |
+---------v-----------------+          +------------v-------------+
| DuckDB catalog layer      |          | TiTiler FastAPI service  |
| core/catalog.py           |          | core/tiler/main.py       |
| spatial + gcsfs (gs://)   |          | /cog/* endpoints         |
+------------+--------------+          +------------+-------------+
             |                                      |
             | reads Parquet/GeoParquet             | reads COGs
             |                                      |
     +-------v--------------------------------------v-------+
     |         Storage backend (local | gcs)               |
     |         core/storage.py                            |
     +-----------------------------------------------------+
```

## Run Locally

EMBER runs as two processes: the TiTiler tiler and the Streamlit app. With your
Python environment active (e.g. a conda env):

1. Copy env template:
   - `cp .env.example .env`
2. Create the local sample dataset (no cloud credentials needed):
   - `python scripts/bootstrap_sample_data.py`
3. In one terminal, start the tiler:
   - `uvicorn core.tiler.main:app --host 0.0.0.0 --port 8000`
4. In a second terminal, start the app (pointing it at the tiler):
   - `TILER_URL=http://localhost:8000 streamlit run core/app/streamlit_app.py --server.port 8501`
5. Open:
   - App: `http://localhost:8501`
   - Tiler health: `http://localhost:8000/healthz`

## GCS Setup (Where to put credentials)

For GCS-backed runtime (`EMBER_STORAGE_BACKEND=gcs`), fill values in `./.env`:

- `GCS_BUCKET`
- `GOOGLE_APPLICATION_CREDENTIALS` (path inside container, usually `/secrets/ember-sa.json`)

For local runs, place the service account JSON file somewhere readable and point
`GOOGLE_APPLICATION_CREDENTIALS` at it, e.g.:

- `./secrets/ember-sa.json`

Single credential (GCS-native): one read-only service account authenticates every reader.

- TiTiler/GDAL (COGs): native `gs://` via the service account JSON, or the attached service account on Cloud Run.
- DuckDB (Parquet): native `gs://` via `gcsfs`, using the same service account (no HMAC keys).

On Cloud Run, leave `GOOGLE_APPLICATION_CREDENTIALS` blank and attach the service account; all readers pick it up through Application Default Credentials.

## Deploy to Cloud Run

EMBER deploys as two Cloud Run services built from one image (`Dockerfile`). The
container entrypoint (`scripts/entrypoint.sh`) selects which process to run from
the `SERVICE` env var (`app` or `tiler`) and binds to Cloud Run's `$PORT`.

One-time setup:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create ember --repository-format=docker --location=us-central1
```

Deploy both services (builds, deploys tiler + app, and wires `TILER_URL` and the
tiler's `CORS_ORIGINS` automatically):

```bash
PROJECT_ID=my-project \
GCS_BUCKET=my-bucket \
SERVICE_ACCOUNT=ember-sa@my-project.iam.gserviceaccount.com \
./scripts/deploy_cloudrun.sh
```

Optional overrides: `REGION` (default `us-central1`), `REPO`, `GCS_PREFIX`,
`TILER_SERVICE`, `APP_SERVICE`, `IMAGE_TAG`. The script prints the final App and
Tiler URLs when it finishes.

## Extensibility Contract

- New metric: add to `config/metrics.yaml` and insert rows into `scalar_metrics` or `raster_assets`.
- New profile: add to `config/profiles.yaml`.
- New utility/wildfire: add geometry rows + pair/metric rows.

Core code under `core/` should not change for any of these.

## Glossary

- COG: Cloud-Optimized GeoTIFF designed for ranged tile reads.
- Tiler: service that serves map tiles from source rasters on demand.
- Tidy schema: one row per measurement with `metric_key` instead of one column per metric.
- Source area: utility watershed/source-water geometry used in overlap analysis.
