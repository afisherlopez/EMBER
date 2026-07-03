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

1. Copy env template:
   - `cp .env.example .env`
2. Start with local sample data (no cloud credentials needed):
   - `docker compose up --build`
3. Open:
   - App: `http://localhost:8501`
   - Tiler health: `http://localhost:8000/healthz`

The compose startup runs `scripts/bootstrap_sample_data.py` so the sample dataset is created automatically under `./data`.

## GCS Setup (Where to put credentials)

For GCS-backed runtime (`EMBER_STORAGE_BACKEND=gcs`), fill values in `./.env`:

- `GCS_BUCKET`
- `GOOGLE_APPLICATION_CREDENTIALS` (path inside container, usually `/secrets/ember-sa.json`)

Place the service account JSON file at:

- `./secrets/ember-sa.json`

And keep this mount in `docker-compose.yml`:

- `./secrets:/secrets:ro`

Single credential (GCS-native): one read-only service account authenticates every reader.

- TiTiler/GDAL (COGs): native `gs://` via the service account JSON, or the attached service account on Cloud Run.
- DuckDB (Parquet): native `gs://` via `gcsfs`, using the same service account (no HMAC keys).

On Cloud Run, leave `GOOGLE_APPLICATION_CREDENTIALS` blank and attach the service account; all readers pick it up through Application Default Credentials.

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
