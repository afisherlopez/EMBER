"""EMBER tiler service exposing dynamic COG tile endpoints through TiTiler."""

from core.gcp_auth import bootstrap_gcp_credentials

# Apply the same GCP credentials/config the Streamlit app uses (from Streamlit secrets, or an
# already-set GOOGLE_APPLICATION_CREDENTIALS) before `core.settings` loads and before GDAL
# opens any `gs://` COG. On a host with an attached service account (e.g. Cloud Run) this is a
# no-op and GDAL falls back to Application Default Credentials.
bootstrap_gcp_credentials()

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers
from titiler.core.factory import TilerFactory

from core.settings import settings

app = FastAPI(title="EMBER Tiler", description="Dynamic COG tiling for EMBER.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET"],
    allow_headers=["*"],
)

cog = TilerFactory()
app.include_router(cog.router, tags=["Cloud Optimized GeoTIFF"])
add_exception_handlers(app, DEFAULT_STATUS_CODES)


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    """Liveness endpoint used by local compose and container health checks."""
    return {"status": "ok"}
