# EMBER — Implementation Spec (v1)

> **Audience:** the Cursor agent building the first working version, plus the Blue Forest engineers who will maintain it.
> **Read this top to bottom before writing code.** It defines the architecture, the data model, the performance bar, and the documentation standard. Build the smallest thing that satisfies every "Acceptance Criteria" item — no more — and document as you go.

---

## 1. What EMBER is

EMBER (*Environmental and economic Measurements of Burn Events on water Resources*) is a Streamlit dashboard that shows, for **one water utility × one wildfire at a time**, how the fire affected that utility's source-water area — both environmentally (sediment, turbidity) and economically. It serves Blue Forest's internal teams and external water-utility stakeholders.

The user picks three things at the top:

1. **"I am…"** — a profile (water utility / Project Development / Science / Finance / External Affairs). Changing this **only changes which feature panels are visible**. It does not re-fetch data.
2. **A water utility** (searchable dropdown).
3. **A wildfire** (searchable dropdown, also sortable/filterable by date and location).

Changing (2) or (3) re-fetches the data for that pair, updates the map, and updates every visible panel.

---

## 2. Architectural principles (the "why" behind every decision)

**EMBER is a read-only lookup-and-visualize app over precomputed data.** Nothing is modeled at request time. This single fact drives the whole design:

- **Precompute offline, serve static assets at runtime.** All geoprocessing (overlap tests, sediment/turbidity modeling, economic modeling) happens in the data pipeline *before* anything is published. The app only ever does a keyed lookup on `(utility_id, wildfire_id)` and renders. See `DATA_GUIDE.md` for the pipeline.
- **Lazy-load only the selected pair.** Never load the full catalog of geometries or rasters on startup. Load list metadata for the dropdowns; load heavy assets only for the current selection.
- **The map is dynamically tiled, not a static image.** Raster layers (sediment, turbidity) are **Cloud-Optimized GeoTIFFs (COGs)** served as XYZ map tiles by a **TiTiler** service. The browser fetches only the tiles in the current viewport at the current zoom — so pan/zoom is fast regardless of raster size.
- **Scale comes from a "tidy" (long) data model + registries, not from schema changes.** Adding a new metric (e.g. a nitrogen layer), a new utility, a new fire, or a new profile must require **only new data rows and a config edit — never a migration or a code change to the core app.** This is non-negotiable; the schema in §5 and the registries in §10 exist to guarantee it.
- **Storage is GCS, read natively, behind one module.** All data lives in **Google Cloud Storage** as `gs://` objects, and every reader authenticates with a **single read-only service account**: **TiTiler/GDAL** reads COGs *natively* (`/vsigs/`), and **DuckDB** reads Parquet *natively* via `gcsfs` (`gs://`, same credentials — no HMAC keys). Both paths are configured from env vars and isolated in `ember/storage.py` + the catalog init — no bucket names or credentials appear inline in application code. A `local` backend mirrors the same interface for dev and tests.
- **Readable over clever.** This is shared, team-maintained code. Prefer obvious code with docstrings and type hints over abstractions that save keystrokes.

---

## 3. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python ≥ 3.11 | TiTiler 2.x requires 3.11+. |
| Frontend / app | **Streamlit** | The dashboard and the export UI. |
| Interactive map | **Folium (Leaflet)** via `streamlit-folium` | Leaflet `TileLayer` consumes TiTiler tiles; GeoJSON overlays for vector boundaries. |
| Raster tiles | **TiTiler** (`titiler.core`) as a small custom FastAPI service | Dynamic COG tiling with server-side rescale + colormap. |
| Catalog / queries | **DuckDB** (+ `spatial` extension, `gcsfs` for `gs://`) | Queries Parquet/GeoParquet directly from object storage. No DB server to run. |
| Tabular + geo data | **Parquet / GeoParquet** in object storage | This *is* the database. Portable, columnar, partition-friendly. |
| Object storage | **Google Cloud Storage (GCS)** | `gs://` URIs. Abstracted behind `ember/storage.py`; a `local` backend exists for dev/tests. |
| Charts (later) | Plotly or Altair | Not required for v1. |
| Config | **YAML** for registries; **`.env`** for secrets/endpoints | Registries must be editable by non-Python teammates. |

Pin versions in `pyproject.toml` (preferred) or `requirements.txt`. Use one dependency group for the **app** and one for the **tiler** so the tiler image stays lean.

---

## 4. Repository structure

Create exactly this layout. Each file gets a module-level docstring explaining its one job.

```
ember/
├── README.md                  # how to run, how to extend, architecture diagram
├── pyproject.toml             # deps, pinned; [app] and [tiler] optional-dependency groups
├── .env.example               # every env var, documented, with safe defaults
├── docker-compose.yml         # local dev: `tiler` + `app` services
├── docs/
│   ├── architecture.md        # the diagram + data-flow narrative (copy §2 + §9)
│   └── adr/                    # Architecture Decision Records, one .md per decision
├── config/
│   ├── metrics.yaml           # metric registry (see §10) — editable by anyone
│   └── profiles.yaml          # profile → ordered feature list (see §10)
├── ember/
│   ├── __init__.py
│   ├── settings.py            # pydantic-settings; all config from env, typed + documented
│   ├── storage.py             # storage abstraction (local | gcs)
│   ├── catalog.py             # DuckDB access layer; all queries live here
│   ├── models.py              # typed records: Utility, Wildfire, PairSummary, MetricValue...
│   ├── registry.py            # loads + validates metrics.yaml / profiles.yaml
│   ├── states.py              # the three data-state decision logic (single source of truth)
│   ├── tiler/
│   │   ├── __init__.py
│   │   └── main.py            # FastAPI app mounting TiTiler TilerFactory
│   └── app/
│       ├── __init__.py
│       ├── streamlit_app.py   # entrypoint: header bar + layout + wiring
│       ├── selectors.py       # the "I am…" / utility / wildfire controls
│       ├── features.py        # renders a feature panel given (metric, state, data)
│       ├── map_view.py        # builds the Folium map: COG tile layer + GeoJSON overlays
│       └── export.py          # HTML summary generation (reuse ember_summary_draft.html)
├── scripts/
│   ├── build_manifest.py      # (re)builds pair_summary from geometries (overlap tests)
│   └── validate_data.py       # validates COGs + checks every asset URI resolves
└── tests/
    ├── test_states.py         # the data-state logic — must be exhaustively covered
    ├── test_catalog.py        # queries against a tiny fixture dataset
    └── test_registry.py       # config files parse + reference valid metrics
```

---

## 5. Data model — the scalability backbone

The "database" is a set of Parquet/GeoParquet files in object storage, queried by DuckDB. **Geometry tables hold geometry; metric data is stored "tidy" (one row per measurement) so new metrics never change the schema.**

### 5.1 Geometry tables (GeoParquet, EPSG:4326)

`utilities.parquet`
| column | type | notes |
|---|---|---|
| `utility_id` | TEXT (PK) | stable slug, e.g. `denver-water` |
| `name` | TEXT | display name |
| `state` | TEXT | 2-letter |
| `source_area_name` | TEXT | e.g. "Upper South Platte watershed" |
| `geometry` | GEOMETRY (Polygon/MultiPolygon) | source-area boundary |
| `centroid_lon`, `centroid_lat` | DOUBLE | for map centering / label placement |
| `updated_at` | TIMESTAMP | provenance |

`wildfires.parquet`
| column | type | notes |
|---|---|---|
| `wildfire_id` | TEXT (PK) | slug, e.g. `hayman-2002` |
| `name` | TEXT | display name |
| `ignition_date`, `containment_date` | DATE | **powers date sorting in the dropdown** |
| `acres` | DOUBLE | |
| `state`, `county` | TEXT | **powers location filtering in the dropdown** |
| `centroid_lon`, `centroid_lat` | DOUBLE | |
| `geometry` | GEOMETRY | burn perimeter |
| `source` | TEXT | `NIFC` / `MTBS` / … (provenance) |
| `updated_at` | TIMESTAMP | |

### 5.2 Pair + metric tables (Parquet, tidy)

`pair_summary.parquet` — one row per `(utility, wildfire)` pair, the **overlap fact**:
| column | type | notes |
|---|---|---|
| `utility_id`, `wildfire_id` | TEXT | composite PK |
| `has_overlap` | BOOLEAN | drives the "No direct impact" state |
| `overlap_area_km2` | DOUBLE | nullable |
| `overlap_pct_of_source` | DOUBLE | nullable |
| `updated_at` | TIMESTAMP | |

`scalar_metrics.parquet` — **tidy**, one row per `(pair, metric)`:
| column | type | notes |
|---|---|---|
| `utility_id`, `wildfire_id` | TEXT | |
| `metric_key` | TEXT | FK → `metrics.yaml`, e.g. `econ_impact_5yr` |
| `value` | DOUBLE | **nullable** → triggers "Data not yet available" |
| `unit` | TEXT | denormalized for safety |
| `method`, `source_note` | TEXT | provenance |
| `as_of_date` | DATE | |

`raster_assets.parquet` — **tidy**, one row per `(pair, raster-metric)`:
| column | type | notes |
|---|---|---|
| `utility_id`, `wildfire_id` | TEXT | |
| `metric_key` | TEXT | e.g. `sediment_yield_increase` |
| `cog_uri` | TEXT | `gs://bucket/key.tif` (see §6) |
| `units` | TEXT | |
| `colormap_name` | TEXT | TiTiler colormap; falls back to registry default |
| `rescale_min`, `rescale_max` | DOUBLE | server-side rescale range |
| `nodata` | DOUBLE | nullable |
| `as_of_date` | DATE | |

> **Why tidy matters:** adding a "nitrogen_increase" raster metric is `INSERT`-only — new rows in `raster_assets`, one entry in `metrics.yaml`, optionally referenced from a profile in `profiles.yaml`. **No table is altered. No app code changes.** Enforce this property; if a proposed feature would require a schema change, flag it instead of implementing it.

### 5.3 Partitioning for scale (do this from day one)

Write `scalar_metrics` and `raster_assets` as **Hive-partitioned** datasets keyed by `metric_key` (and optionally fire year), e.g. `raster_assets/metric_key=sediment_yield_increase/part-*.parquet`. DuckDB prunes partitions automatically, so query cost stays flat as metrics and fires accumulate. Document the partition scheme in `docs/architecture.md`.

---

## 6. Storage abstraction (`ember/storage.py`)

One small module. **No bucket name or credential appears anywhere else in the codebase.**

Requirements:
- A `Storage` protocol/ABC with: `uri_for(key) -> str`, `read_bytes(key) -> bytes`, `exists(key) -> bool`, and a `dataset_uri(name) -> str` helper for Parquet paths.
- Two implementations selected by `EMBER_STORAGE_BACKEND`:
  - `local` — `file://` URIs under a configurable root (for dev/tests).
  - `gcs` — uses `GCS_BUCKET`. `uri_for(key)` returns `gs://{bucket}/{key}`; `read_bytes`/`exists` use `google-cloud-storage` (or `gcsfs`).
- `uri_for` returns the `gs://` form that **both** GDAL/rasterio (for the tiler) and DuckDB understand directly, so the same URI works for COGs and Parquet. In `local` mode it returns `file://{path}`.

**One credential, two native readers (this is GCS-specific — call it out in the README):**

| Consumer | How it reads GCS | Credential | Set where |
|---|---|---|---|
| TiTiler / GDAL (COGs) | native `/vsigs/` | service account (JSON key file, or the attached SA on Cloud Run via the metadata server) | tiler container env (§7) |
| DuckDB (Parquet) | native `gs://` via `gcsfs` | **same service account** (Application Default Credentials) | catalog init (§8) |

Both readers use the **same read-only service account** granted access to the bucket — resolved through Application Default Credentials (`GOOGLE_APPLICATION_CREDENTIALS` JSON locally, or the attached SA on Cloud Run). No HMAC keys, so no `storage.restrictAuthTypes` org-policy exception is needed. Never hardcode bucket or credentials; read from env (§14).

---

## 7. The tiler service (`ember/tiler/main.py`)

A deliberately tiny FastAPI app that mounts TiTiler's COG factory. Keep it legible — a teammate should understand the whole file in one read.

```python
"""EMBER tiler: a thin TiTiler service that serves XYZ tiles from our COGs.

It does ONE job: given a COG URI plus rescale/colormap query params, return
PNG map tiles on demand. All styling decisions (rescale range, colormap) come
from `raster_assets` rows, not from this file.
"""
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from titiler.core.factory import TilerFactory
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from ember.settings import settings

app = FastAPI(title="EMBER Tiler", description="Dynamic COG tiling for EMBER.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,   # the Streamlit app origin(s)
    allow_methods=["GET"],
    allow_headers=["*"],
)

cog = TilerFactory()                       # creates /cog/* endpoints
app.include_router(cog.router, tags=["Cloud Optimized GeoTIFF"])
add_exception_handlers(app, DEFAULT_STATUS_CODES)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    """Liveness probe for compose / orchestration."""
    return {"status": "ok"}
```

**Endpoints the app will use** (provided by `TilerFactory`):
- `GET /cog/WebMercatorQuad/tilejson.json?url=<cog>&rescale=<min>,<max>&colormap_name=<name>` → returns the tile-URL template.
- `GET /cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=<cog>&rescale=<min>,<max>&colormap_name=<name>` → the tiles themselves.

**GDAL tuning + GCS auth (set as env vars on the tiler container — critical for speed and access):**
```
# --- performance ---
GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR     # don't list the bucket on every open
CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif,.TIF,.tiff
GDAL_HTTP_MULTIPLEX=YES
GDAL_HTTP_VERSION=2
VSI_CACHE=TRUE
GDAL_CACHEMAX=200                          # MB

# --- GCS auth (native /vsigs/) ---
# Local / docker-compose: mount a service-account JSON and point GDAL at it.
GOOGLE_APPLICATION_CREDENTIALS=/secrets/ember-sa.json
# On Cloud Run / GCE: attach the service account to the service instead and omit
# the JSON file — GDAL picks up credentials from the metadata server automatically.
# If the container isn't auto-detected as GCE, force it:
# CPL_MACHINE_IS_GCE=YES
```
GDAL reads `gs://bucket/key.tif` natively via `/vsigs/`, so the `url` param the app sends is just the asset's `gs://` `cog_uri` — no HMAC, no endpoint config needed on the tiler side. The service account needs only **read** access to the COG prefix (`Storage Object Viewer`).

**Caching:** TiTiler sets `Cache-Control: public, max-age=3600` by default — keep it, and put a CDN in front of the tiler in production so repeat tiles are served from edge cache, not recomputed. On GCP the natural fit is to run the tiler on **Cloud Run** behind an external HTTPS load balancer with **Cloud CDN** enabled. Document this in `docs/architecture.md`; CDN/LB provisioning itself is out of scope for v1.

**Why COGs must be web-optimized:** if COGs are written in EPSG:3857 aligned to the web-mercator tile grid (see `DATA_GUIDE.md`), the tiler does **no reprojection at tile time** — the single biggest tiling speedup. The app must assume served COGs are web-optimized.

---

## 8. Catalog layer (`ember/catalog.py`)

All DuckDB access lives here behind named functions — no SQL anywhere else. On init, load the spatial extension and, for the `gcs` backend, register a native GCS filesystem so DuckDB reads `gs://` paths with the service account:
```python
conn.execute("INSTALL spatial; LOAD spatial;")
# gcs backend only: native gs:// reads via the same service account as GDAL — no HMAC keys.
import gcsfs
conn.register_filesystem(gcsfs.GCSFileSystem(token=settings.google_application_credentials or None))
```
`gcsfs` resolves credentials through Application Default Credentials (the `GOOGLE_APPLICATION_CREDENTIALS` JSON locally, or the attached SA on Cloud Run), so DuckDB reads our Parquet directly from `gs://{bucket}/...` paths. In `local` backend mode, skip registration and read plain filesystem paths. Hold **one** connection per process (the app wraps it in `@st.cache_resource`).

Functions (typed, docstringed):
- `list_utilities() -> list[Utility]` — id + name + state for the dropdown. Cheap; no geometry.
- `list_wildfires(state: str | None = None, year: int | None = None) -> list[Wildfire]` — id, name, date, state, county for the searchable/sortable dropdown.
- `get_pair_summary(utility_id, wildfire_id) -> PairSummary` — the overlap fact.
- `get_scalar(utility_id, wildfire_id, metric_key) -> MetricValue | None`
- `get_raster_asset(utility_id, wildfire_id, metric_key) -> RasterAsset | None`
- `get_geojson(table, row_id, simplify_tolerance: float) -> dict` — returns simplified GeoJSON for one boundary/perimeter using DuckDB spatial: `ST_AsGeoJSON(ST_Simplify(geometry, ?))`. Simplifying server-side keeps map payloads small.

Every query targets a single id or pair — never `SELECT *` across the catalog.

---

## 9. The Streamlit app

### 9.1 Layout
- **Header bar** (always visible): the three selectors from §1, left to right. The wildfire selector supports text search and offers sort/filter by date and by state/county (use `list_wildfires` filters).
- **Upper-right hero:** the map (§9.3).
- **Rest of dashboard:** the feature panels for the selected profile (§9.2).

### 9.2 Feature panels (`features.py` + `registry.py` + `states.py`)
1. Read the selected profile from `profiles.yaml` → an ordered list of `metric_key`s.
2. For each metric, look up its definition in `metrics.yaml` (kind, unit, format, default colormap/rescale).
3. Fetch the data (`get_scalar` or `get_raster_asset`) and the pair's `has_overlap`.
4. Resolve the **data state** (§9.4) and render accordingly.

Changing the profile **only** re-runs steps 1–4 against already-cached data; it must not re-fetch geometry or rasters.

### 9.3 Map (`map_view.py`)
Build one Folium map per selection:
1. Add a neutral basemap.
2. Add the **source-area boundary** as a GeoJSON layer styled **blue**; add the **wildfire perimeter** as GeoJSON styled **red** (matches the spec and the export).
3. For a raster feature that has data: build the tile URL from the asset's `cog_uri` + `rescale` + `colormap_name`, request `…/tilejson.json` from the tiler, and add the returned template as a Leaflet `TileLayer` above the basemap and below the vector overlays.
4. `fit_bounds` to the combined extent of boundary + perimeter.
5. Render with `st_folium(..., returned_objects=[])` so map interactions don't trigger Streamlit reruns (big perf win); the map is display-only.

Helper: `cog_tilejson_url(asset) -> str` that URL-encodes `url`, `rescale=min,max`, `colormap_name` against `settings.tiler_url`.

### 9.4 The three data states (`states.py` — single source of truth)
This logic must exist in exactly one place and be unit-tested exhaustively.

```python
def resolve_state(has_overlap: bool, payload) -> str:
    """Return one of: 'no_impact' | 'pending' | 'available'.

    payload is the scalar MetricValue/value or the RasterAsset (or None).
    """
    if not has_overlap:
        return "no_impact"
    if payload is None:           # overlap exists but the metric isn't modeled yet
        return "pending"
    return "available"
```

Rendering rules:
| state | scalar feature | raster feature |
|---|---|---|
| `available` | formatted value (per `value_format`) | COG tile layer + boundary + legend |
| `no_impact` | text: **"No direct impact"** | boundary only + centered label **"No direct impact"** |
| `pending` | text: **"Data not yet available"** | boundary only + centered label **"Data not yet available"** |

> Use the exact strings **"No direct impact"** and **"Data not yet available"** (no brackets). Never fabricate or infer a value to fill a panel — a missing value is `NULL` and renders as `pending`.

For the centered label on a boundary-only map, add a Leaflet `DivIcon` marker at the boundary centroid (from `centroid_lon/lat`).

---

## 10. Registries — how new things get added without code changes

`config/metrics.yaml`:
```yaml
metrics:
  econ_impact_5yr:
    display_name: "Economic impact to utility over 5 years"
    kind: scalar
    unit: USD
    value_format: "${:,.0f}"        # python format spec applied to `value`
  total_econ_impact:
    display_name: "Total estimated economic impact from fire"
    kind: scalar
    unit: USD
    value_format: "${:,.0f}"
  sediment_yield_increase:
    display_name: "Increase in sediment yield"
    kind: raster
    unit: "tonnes/km^2/yr"
    default_colormap: ylorbr
    default_rescale: [0, 100]
  turbidity_increase:
    display_name: "Increase in turbidity"
    kind: raster
    unit: "NTU"
    default_colormap: ylorbr            # pick a perceptually-uniform ramp
    default_rescale: [0, 50]
```

`config/profiles.yaml`:
```yaml
profiles:
  water_utility:
    label: "A water utility owner, employee, or partner"
    features: [econ_impact_5yr, sediment_yield_increase]
  project_development:
    label: "A Project Development team member"
    features: [econ_impact_5yr, total_econ_impact]
  science:
    label: "A Science team member"
    features: [sediment_yield_increase, turbidity_increase]
  finance:
    label: "A Finance team member"
    features: [econ_impact_5yr, total_econ_impact]
  external_affairs:
    label: "An External Affairs team member"
    features: [econ_impact_5yr, total_econ_impact]
```

`ember/registry.py` loads both files at startup and **validates** them: every `metric_key` referenced in `profiles.yaml` must exist in `metrics.yaml`; each metric's `kind` must be `scalar` or `raster`. Fail loudly with a clear message if not. `test_registry.py` covers this.

**The extensibility contract (state it in the README):**
- *New metric* → add to `metrics.yaml` + insert rows in `scalar_metrics`/`raster_assets`. Reference it from a profile if it should display.
- *New profile* → add a block to `profiles.yaml`.
- *New utility / wildfire* → add a geometry row + its metric rows.
None of these touch `ember/` core code.

---

## 11. Export (`app/export.py`)
Reuse the existing `ember_summary_draft.html` template. Given the selection + the user's chosen features (grouped by profile), fill the template's placeholders and offer it via `st.download_button` as HTML. Keep PDF conversion out of v1 (note the WeasyPrint/Playwright path in a TODO). Use the same `states.py` logic so the export shows identical "No direct impact" / "Data not yet available" wording.

---

## 12. Caching strategy
- `@st.cache_resource`: the DuckDB connection, the storage client, the loaded registries. Created once per process.
- `@st.cache_data`: per-selection results — keyed by `(utility_id, wildfire_id)` for geometry GeoJSON, pair summary, and metric lookups; keyed by `(utility_id, wildfire_id, metric_key)` for raster assets and tilejson. Re-selecting a previously viewed pair is then instant.
- Tiles: rely on the tiler's `Cache-Control` + a CDN in front (production).

---

## 13. Performance bar (treat as acceptance criteria)
- Cold dashboard load (dropdowns populated, no selection): **< 2 s**.
- After a selection: map visible with first tiles and all panels rendered: **< 2 s** on a previously-served COG.
- Pan/zoom on the map: smooth; new tiles appear in **< ~500 ms** each (warm cache).
- Re-selecting a previously viewed pair: **< 500 ms** (served from `st.cache_data`).
- No operation loads more than the selected pair's assets. No reprojection at tile time (web-optimized COGs).

---

## 14. Configuration (`settings.py` + `.env.example`)
Use `pydantic-settings`. Every variable typed, documented inline, with a safe default where possible. At minimum:
```
EMBER_STORAGE_BACKEND=local            # local | gcs
EMBER_DATA_ROOT=./data                 # used when backend=local

# --- GCS (backend=gcs) ---
GCS_BUCKET=ember-data                  # the single bucket holding tables/ and cogs/
GCS_PREFIX=                            # optional folder within the bucket (e.g. EMBER); blank = bucket root

# One service account for every reader (DuckDB via gcsfs, TiTiler/GDAL, storage client):
GOOGLE_APPLICATION_CREDENTIALS=        # path to SA JSON locally; leave blank on Cloud Run (attached SA)

# --- service wiring ---
TILER_URL=http://localhost:8000        # base URL the app uses to reach the tiler
CORS_ORIGINS=http://localhost:8501     # the Streamlit origin(s)
GEOJSON_SIMPLIFY_TOLERANCE=0.0005      # degrees; tune for payload vs. fidelity
```
`.env.example` is committed; real `.env` (and any service-account JSON) is git-ignored. No secret ever appears in code or in the repo.

---

## 15. Local development (`docker-compose.yml`)
Two services:
- `tiler` — runs `uvicorn ember.tiler.main:app` on `:8000` with the GDAL env from §7.
- `app` — runs `streamlit run ember/app/streamlit_app.py` on `:8501`, with `TILER_URL=http://tiler:8000`.
`docker compose up` brings the whole stack up against `EMBER_STORAGE_BACKEND=local` and the bundled sample dataset — **no GCS credentials needed for local dev** (the tiler reads `file://` COGs). To run compose against the real GCS bucket instead, set `EMBER_STORAGE_BACKEND=gcs`, mount the service-account JSON into both services (`./secrets/ember-sa.json:/secrets/ember-sa.json:ro`), and point `GOOGLE_APPLICATION_CREDENTIALS` at it — the same credential serves DuckDB (`gcsfs`) and the tiler (GDAL). Document this as the first thing in the README.

---

## 16. Testing & validation
- `tests/test_states.py` — exhaustive truth table for `resolve_state` and rendering selection. This is the most important test; the three states are the heart of correctness.
- `tests/test_catalog.py` — run queries against a tiny committed fixture dataset (2 utilities, 2 fires, mixed availability incl. a no-overlap pair and a pending metric).
- `tests/test_registry.py` — config parses and cross-references validate.
- `scripts/validate_data.py` — for a given dataset: every `cog_uri` is a valid COG (`rio cogeo validate`), every referenced asset resolves in storage, every `metric_key` exists in the registry. Run this in the data pipeline before publishing.

---

## 17. Documentation standard (required, not optional)
This code is shared and edited by the whole team. The agent must:
- Give every module a top-of-file docstring stating its single responsibility.
- Give every public function a docstring (what it does, args, returns) and full type hints.
- Comment the *why* on any non-obvious decision (especially the GDAL env vars, the tidy-schema rationale, and the `st_folium(returned_objects=[])` choice).
- Write `README.md` with: a one-paragraph overview, an architecture diagram (ASCII is fine), "run locally" steps, the **extensibility contract** from §10, and a glossary (COG, tiler, tidy schema, source area).
- Record meaningful decisions as short ADRs in `docs/adr/` (e.g. "Why DuckDB+Parquet over PostGIS", "Why dynamic tiling over pre-rendered tiles").
- Keep functions small and named for what they do. Favor clarity over abstraction.

---

## 18. Definition of done (v1 acceptance criteria)
1. `docker compose up` launches tiler + app against the bundled sample dataset.
2. The header bar has all three working selectors; the wildfire selector supports search and sort/filter by date and location.
3. Selecting a utility + wildfire renders the upper-right map with the **blue source-area boundary** and **red wildfire perimeter**, fit to bounds.
4. Raster features with data render as **interactive, zoomable/pannable COG tile layers** (served by the tiler) with a legend; pan/zoom fetches tiles on the fly.
5. All five profiles work; switching profile only changes visible panels (no re-fetch).
6. All three data states are reachable in the sample data and render the exact strings **"No direct impact"** and **"Data not yet available"**; no value is ever fabricated.
7. Adding a new metric is demonstrably config-only: include a short README walkthrough proving it (add metric to YAML + rows, it appears — no core code edited).
8. The export button produces the filled HTML summary using the same state logic.
9. Performance bar (§13) met on the sample dataset.
10. Tests pass; every module and public function is documented per §17.

---

## 19. Out of scope for v1 (note as TODOs, don't build)
- PDF export (HTML now; WeasyPrint/Playwright later).
- Vector tiles / PMTiles for boundaries (simplified GeoJSON per selection is enough at current scale; revisit if many layers must show at once).
- Auth / multi-tenant access control on the tiler beyond CORS.
- CDN provisioning (document the seam; don't implement).
- Mosaic/STAC endpoints in the tiler (only `/cog` is needed).
