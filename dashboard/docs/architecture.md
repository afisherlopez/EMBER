# EMBER Architecture

## Principles

- Precompute all overlap and metrics offline, render by lookup at runtime.
- Load only metadata on startup; load per `(utility_id, wildfire_id)` selection thereafter.
- Serve raster maps as dynamic XYZ tiles from COG via TiTiler.
- Keep metrics in tidy tables keyed by `metric_key`; extend by data/config rows only.
- Keep storage concerns in one seam (`core/storage.py`) and env-based settings.

## Data Flow

1. User selects profile, utility, wildfire in Streamlit.
2. App fetches pair summary and metric payloads from `core/catalog.py`.
3. For raster metrics, app requests tilejson from TiTiler and adds returned tile template.
4. Utility boundary and wildfire perimeter are returned as simplified GeoJSON.
5. Feature cards and export use one shared state resolver (`no_impact`, `pending`, `available`).

## Storage and Credentials

- Storage location: GCS bucket (`gs://...`) or local filesystem (`file://...`).
- Single credential model: one read-only service account authenticates every GCS reader.
- TiTiler/GDAL auth (COGs): service account, native `gs://`/`/vsigs/` (JSON locally, attached SA in Cloud Run).
- DuckDB auth for GCS Parquet: same service account via `gcsfs` (native `gs://`, Application Default Credentials). No HMAC keys.

## Partitioning

`scalar_metrics` and `raster_assets` should be written as Hive-partitioned datasets by `metric_key` (optionally year) to keep query scan cost stable as catalog grows.

## Production Seam

The recommended production setup is TiTiler on Cloud Run behind HTTPS load balancer + Cloud CDN. v1 keeps this as infrastructure documentation only.
