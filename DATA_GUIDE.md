# EMBER — Data Guide

> **Audience:** you (and whoever collects data for EMBER), starting from an empty repo.
> **Purpose:** how to find, process, name, and publish each piece of data so the app in `IMPLEMENTATION_SPEC.md` can read it. Follow the same steps every time new data arrives — the workflow is designed to scale without rework.
>
> **Storage:** EMBER uses **Google Cloud Storage (GCS)**. Everything you publish goes into one GCS bucket as `gs://` objects. §6 covers setting up the bucket and the single read-only service account EMBER uses (both the tiler and DuckDB authenticate with it natively).

---

## 0. The mental model

EMBER reads precomputed data. Your job is to turn raw downloads and model outputs into a small, consistent set of files and publish them. Three stages:

```
  raw/            processed/              published/  (-> object storage)
  ───────         ─────────────           ────────────────────────────────
  downloads  ──►  reprojected, clipped ──►  GeoParquet + COGs + tidy Parquet
  & model         simplified, validated     (exactly what the app queries)
  outputs
```

- **raw/** — untouched downloads and model outputs. Never edited. Keep provenance.
- **processed/** — intermediate working files while you reproject/clip/convert.
- **published/** — the final, validated files that get uploaded to object storage. These mirror the data model in the spec.

Keep `raw/` and `processed/` out of git (`.gitignore` them); they can be large. `published/` is what you upload.

---

## 1. IDs and naming — decide once, never improvise

Every utility and wildfire needs a **stable, human-readable slug ID**. The app keys everything on these, and asset filenames are built from them, so they must never change once published.

- **Utility id:** lowercase, hyphenated name. `denver-water`, `salt-lake-city-public-utilities`.
- **Wildfire id:** lowercase name + 4-digit ignition year. `hayman-2002`, `cameron-peak-2020`. The year disambiguates fires that reuse names.
- **Metric keys:** match `config/metrics.yaml` exactly. `econ_impact_5yr`, `sediment_yield_increase`, `turbidity_increase`.

Keep a simple `ids.csv` in the repo listing every assigned id and its full source name, so two people don't slugify the same thing differently.

---

## 2. Folder + object-storage layout

Local working tree:
```
data/
├── raw/
│   ├── perimeters/         # downloaded fire perimeters (shp/gpkg)
│   ├── boundaries/         # utility source-area boundaries (from utilities/WBD)
│   ├── rasters/            # model outputs (sediment, turbidity) as-delivered
│   └── economics/          # spreadsheets from the modeling teams
├── processed/              # scratch: reprojected/clipped/validated intermediates
└── published/              # final files to upload (mirror of the bucket)
    ├── utilities.parquet
    ├── wildfires.parquet
    ├── pair_summary.parquet
    ├── scalar_metrics/metric_key=.../part-*.parquet
    ├── raster_assets/metric_key=.../part-*.parquet
    └── cogs/
        └── {metric_key}/{utility_id}__{wildfire_id}.tif
```

GCS object layout (one bucket; the app's `GCS_BUCKET`). All paths are addressed as `gs://<bucket>/ember/...`:
```
gs://<bucket>/ember/
├── tables/utilities.parquet
├── tables/wildfires.parquet
├── tables/pair_summary.parquet
├── tables/scalar_metrics/metric_key=.../part-*.parquet
├── tables/raster_assets/metric_key=.../part-*.parquet
└── cogs/{metric_key}/{utility_id}__{wildfire_id}.tif
```
The COG key is fully determined by `(metric_key, utility_id, wildfire_id)`, so `raster_assets.cog_uri` is always derivable as `gs://<bucket>/ember/cogs/{metric_key}/{utility_id}__{wildfire_id}.tif` — no guesswork.

---

## 3. Where to get each dataset

### 3.1 Wildfire perimeters
- **NIFC Open Data** (`data-nifc.opendata.arcgis.com`) — authoritative, current and recent-year perimeters. Best for fires from roughly the last decade and for the freshest boundaries. Download as shapefile/GeoPackage/GeoJSON.
- **MTBS** (`mtbs.gov`, via the USGS Burn Severity portal `burnseverity.cr.usgs.gov`) — consistent perimeters **and** burn-severity rasters for fires ≥ 1,000 acres in the West, back to 1984. Best for historical fires and when you also want severity layers. The national perimeter file is distributed as a single zipped shapefile; per-fire bundles include the burn boundary and severity GeoTIFFs.

Pick NIFC for recent/authoritative perimeters, MTBS for historical coverage and severity. Record which one you used in the `source` column.

### 3.2 Utility source-area boundaries
- Preferred: the **utility's own** source-water / collection-area boundary (often shared directly, or in a watershed protection plan).
- Fallback / approximation: delineate from the **USGS Watershed Boundary Dataset (WBD)** HUC units that make up the source area. Note in `source_area_name` how it was derived.

### 3.3 Sediment & turbidity rasters
These are **Blue Forest model outputs** (e.g. your post-fire erosion/water-quality modeling). They arrive as GeoTIFFs (or model grids you export to GeoTIFF). One raster per `(metric, utility, wildfire)` representing the **increase vs. pre-fire baseline**, clipped to or covering the source area. Confirm with the modeling team: the value units, the no-data value, and the projection.

### 3.4 Economic figures
From the Finance / Project Development modeling spreadsheets. You need, per `(utility, wildfire)`: the 5-year economic impact to the utility and the total estimated economic impact from the fire, each with units and a method note. These become rows in `scalar_metrics`.

> **Rule:** if a number or raster doesn't exist yet, **leave it out** (no row, or a `NULL` value). The app renders that as "Data not yet available." Never estimate to fill a gap.

---

## 4. Processing — turn raw into published

Tooling: GDAL (`gdalwarp`, `gdal_translate`), `rio-cogeo` (the Rasterio COG plugin), and DuckDB with the `spatial` extension. Install once:
```bash
pip install rio-cogeo duckdb gdal   # or use a GDAL/conda environment
```

### 4.1 Vector boundaries & perimeters → GeoParquet (EPSG:4326)
For each boundary/perimeter:
1. Reproject to **EPSG:4326** (the app simplifies on read; canonical storage stays 4326).
2. Clean geometry if needed (fix invalid polygons).
3. Compute centroid lon/lat and write the attribute columns from the spec (`utility_id`/`wildfire_id`, name, dates, state, county, acres, source, `updated_at`).
4. Append to `utilities.parquet` / `wildfires.parquet` (GeoParquet with a `geometry` column).

DuckDB makes this straightforward — read the shapefile/GeoPackage with `ST_Read`, add columns, write Parquet. Keep one row per utility / per wildfire.

### 4.2 Model rasters → web-optimized COGs
This is the most important processing step for map speed. Convert each model GeoTIFF to a **web-optimized COG** — reprojected to **EPSG:3857** and aligned to the web-mercator tile grid, with internal tiling and overviews — so the tiler never reprojects at request time:

```bash
rio cogeo create \
  data/raw/rasters/sediment_hayman_denver.tif \
  data/published/cogs/sediment_yield_increase/denver-water__hayman-2002.tif \
  --web-optimized \
  --cog-profile deflate \
  --blocksize 512 \
  --overview-resampling average \
  --nodata <nodata_value>
```

Notes:
- `--web-optimized` reprojects to 3857 and aligns to the tile grid. This is the single biggest tiling speedup.
- `deflate` compression is safe and lossless for continuous data; `--blocksize 512` matches the tiler's default tile size; `average` overviews look right for continuous fields like sediment.
- Pick a sensible `rescale` range (the min/max you want mapped to the colormap) and a `colormap_name` now — you'll record them in `raster_assets`. Defaults can live in `metrics.yaml`.

**Always validate before publishing:**
```bash
rio cogeo validate data/published/cogs/sediment_yield_increase/denver-water__hayman-2002.tif
```

### 4.3 Scalars → tidy Parquet
For each economic number, append one row to `scalar_metrics` (partition folder `metric_key=<key>/`): `utility_id, wildfire_id, metric_key, value, unit, method, source_note, as_of_date`. Missing value → omit the row or write `value = NULL`.

### 4.4 Raster assets → tidy Parquet
For each published COG, append one row to `raster_assets` (partition `metric_key=<key>/`): `utility_id, wildfire_id, metric_key, cog_uri, units, colormap_name, rescale_min, rescale_max, nodata, as_of_date`. `cog_uri` is `gs://{bucket}/ember/cogs/{metric_key}/{utility_id}__{wildfire_id}.tif`.

---

## 5. Build the overlap manifest (`pair_summary`)

The app's "No direct impact" state depends on whether a fire perimeter actually intersects a source area. Compute this once per pair with `scripts/build_manifest.py` (uses DuckDB spatial):

For every `(utility, wildfire)` pair you care about:
1. `has_overlap = ST_Intersects(source_geom, perimeter_geom)`.
2. If overlapping: `overlap_area_km2` and `overlap_pct_of_source` via `ST_Area(ST_Intersection(...))` (project to an equal-area CRS for area math).
3. Write rows to `pair_summary.parquet`.

You only need pair rows for combinations a user might select; the app treats a missing pair row as "no data for this pair." Re-run this script whenever you add a utility or fire.

---

## 6. Set up Google Cloud Storage

Do this once. EMBER needs **one bucket** and **one credential** — a single read-only service account. Both readers (the tiler/GDAL for COGs and DuckDB for Parquet) reach GCS natively with it, so there are no HMAC keys to manage.

**6.1 Create the bucket**
- One bucket (this becomes `GCS_BUCKET`), e.g. `ember-data`.
- **Location type: Region**, not multi-region — pick a single region close to where the tiler runs and to your users (e.g. `us-west1` for the Western US). COG tiling does many small range reads, so co-locating the bucket and the tiler compute in the same region matters more than geographic redundancy, and a region costs less.
- Turn on **uniform bucket-level access** (IAM-only; simpler and safer than per-object ACLs).
- Use the key scheme from §2 (`ember/tables/...`, `ember/cogs/...`).

**6.2 Create the read-only service account**
- Create a service account, e.g. `ember-reader@<project>.iam.gserviceaccount.com`.
- Grant it **Storage Object Viewer** on the bucket (read-only — the app and tiler never write).
- This one account authenticates everything: GDAL/TiTiler reading `gs://` COGs, and DuckDB reading `gs://` Parquet through `gcsfs`.

**6.3 Provide the credential**
- For **local dev / docker-compose**: create a JSON key for the service account and point `GOOGLE_APPLICATION_CREDENTIALS` at the file. GDAL, `gcsfs`, and the storage client all read it via Application Default Credentials.
- For **production on Cloud Run**: don't use a key file — **attach this service account to the Cloud Run service**. All readers pick up credentials from the metadata server automatically (for GDAL, set `CPL_MACHINE_IS_GCE=YES` if it isn't auto-detected in the container).
- No HMAC keys, no interoperability/S3-compatibility setup, and no `storage.restrictAuthTypes` org-policy exception required.

**6.4 Wire it up**
Fill these in `.env` (see spec §14): `GCS_BUCKET`, and either `GOOGLE_APPLICATION_CREDENTIALS` (local) or the attached SA (Cloud Run). If the `tables/` and `cogs/` layout lives in a **folder inside the bucket** rather than at the bucket root, set `GCS_PREFIX` to that folder (e.g. `GCS_PREFIX=EMBER` to read `gs://<bucket>/EMBER/tables/...`).

**6.5 CDN (production, later)**
For fast repeat tiles, run the tiler on **Cloud Run** behind an external HTTPS load balancer with **Cloud CDN** enabled; TiTiler's `Cache-Control` headers let the CDN cache tiles at the edge. Not needed to get started.

---

## 7. Publish (upload)

Mirror `data/published/` to the bucket under `ember/`. Use the `gcloud` CLI (or `gsutil`). Keep local `published/` as the source of truth so you can re-sync or rebuild.

```bash
# uploads/refreshes everything under ember/, in parallel
gcloud storage rsync --recursive data/published/ gs://<bucket>/ember/
# (equivalent older form: gsutil -m rsync -r data/published/ gs://<bucket>/ember/)
```

After uploading, run `scripts/validate_data.py` pointed at the bucket: it confirms every COG is valid, every `cog_uri` resolves in GCS, and every `metric_key` exists in the registry.

---

## 8. Adding a brand-new metric (the scalable path)

When the science or finance team produces a new kind of output (say, a nitrogen layer), you do **not** change the app:

1. Add the metric to `config/metrics.yaml` (key, `display_name`, `kind`, unit, colormap/rescale or value format).
2. Produce the data: COGs (for a raster metric) or values (for a scalar), the same way as above.
3. Append rows to `raster_assets` or `scalar_metrics` under the new `metric_key=` partition.
4. If it should appear for a profile, add the key to that profile's `features:` list in `config/profiles.yaml`.
5. Re-run `validate_data.py`, then upload.

That's it — no schema migration, no core code edit.

---

## 9. The "new data arrived" checklist

Print this; run it every time.

- [ ] Assigned/confirmed `utility_id` and `wildfire_id` (recorded in `ids.csv`).
- [ ] Boundary/perimeter reprojected to 4326, cleaned, written to GeoParquet with all attribute columns.
- [ ] Rasters converted to **web-optimized COGs**, named `{utility_id}__{wildfire_id}.tif` under `cogs/{metric_key}/`.
- [ ] Every COG passes `rio cogeo validate`.
- [ ] `scalar_metrics` / `raster_assets` rows appended (correct units, colormap, rescale; missing data left as no-row/NULL).
- [ ] `build_manifest.py` re-run → `pair_summary` updated.
- [ ] `validate_data.py` passes.
- [ ] `published/` synced to the bucket.
- [ ] Spot-check in the app: select the new pair, confirm map + panels render, and that gaps show "Data not yet available" / non-overlaps show "No direct impact".

---

## 10. Quick reference — formats at a glance

| Data | Source | Stored as | CRS | GCS key (under `gs://<bucket>/ember/`) |
|---|---|---|---|---|
| Utility source areas | utility / USGS WBD | GeoParquet | 4326 | `tables/utilities.parquet` |
| Wildfire perimeters | NIFC / MTBS | GeoParquet | 4326 | `tables/wildfires.parquet` |
| Sediment / turbidity | Blue Forest models | **web-optimized COG** | 3857 | `cogs/{metric}/{u}__{w}.tif` |
| Economic figures | Finance/PD spreadsheets | tidy Parquet | — | `tables/scalar_metrics/...` |
| Raster metadata | you (this guide) | tidy Parquet | — | `tables/raster_assets/...` |
| Overlap facts | `build_manifest.py` | Parquet | — | `tables/pair_summary.parquet` |

> COGs are read by the tiler natively (`gs://`, service account); Parquet is read by DuckDB natively via `gcsfs` (`gs://`, same service account). Same `gs://` paths, one credential — see §6.
