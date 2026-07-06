"""EMBER tiler service exposing dynamic COG tile endpoints through TiTiler."""

from core.gcp_auth import bootstrap_gcp_credentials

# Apply the same GCP credentials/config the Streamlit app uses (from Streamlit secrets, or an
# already-set GOOGLE_APPLICATION_CREDENTIALS) before `core.settings` loads and before GDAL
# opens any `gs://` COG. On a host with an attached service account (e.g. Cloud Run) this is a
# no-op and GDAL falls back to Application Default Credentials.
bootstrap_gcp_credentials()

from fastapi import FastAPI, HTTPException, Query
from starlette.middleware.cors import CORSMiddleware
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers
from titiler.core.factory import TilerFactory

from core.settings import settings
from core.storage import get_storage

# Confine the tiler to EMBER's own data area. TiTiler's COG endpoints take the source
# raster as a `?url=` query parameter, so on a public, unauthenticated service this guard
# is what stops anyone from pointing it at an arbitrary raster the runtime credentials can
# reach (e.g. gs://data_main_gcs/<anything outside EMBER>) or an external URL (SSRF). The
# allowed prefix is derived from the active storage backend — `gs://<bucket>/<prefix>` in
# gcs mode, or the local data root in local mode — so the same check works in both.
_ALLOWED_DATASET_PREFIX = get_storage().uri_for("").rstrip("/")


def restricted_dataset_path(
    url: str = Query(..., description="COG dataset URL; must be inside the EMBER data area."),
) -> str:
    """TiTiler path dependency that rejects any dataset URL outside the EMBER data area."""
    if url == _ALLOWED_DATASET_PREFIX or url.startswith(f"{_ALLOWED_DATASET_PREFIX}/"):
        return url
    raise HTTPException(status_code=403, detail="Dataset URL is not permitted.")


app = FastAPI(title="EMBER Tiler", description="Dynamic COG tiling for EMBER.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET"],
    allow_headers=["*"],
)

cog = TilerFactory(path_dependency=restricted_dataset_path)
# Mount under /cog so endpoints match the URLs the app builds
# (see core/app/map_view.py: `{tiler_url}/cog/WebMercatorQuad/tilejson.json`).
app.include_router(cog.router, prefix="/cog", tags=["Cloud Optimized GeoTIFF"])
add_exception_handlers(app, DEFAULT_STATUS_CODES)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    """Liveness endpoint used by local compose and container health checks."""
    return {"status": "ok"}
