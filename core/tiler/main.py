"""EMBER tile service for dynamic multi-year burn-severity mosaics."""

import base64
from functools import lru_cache
from threading import BoundedSemaphore

from core.gcp_auth import bootstrap_gcp_credentials

# Apply the same GCP credentials/config the Streamlit app uses (from Streamlit secrets, or an
# already-set GOOGLE_APPLICATION_CREDENTIALS) before `core.settings` loads and before GDAL
# opens any `gs://` COG. On a host with an attached service account (e.g. Cloud Run) this is a
# no-op and GDAL falls back to Application Default Credentials.
bootstrap_gcp_credentials()

from fastapi import FastAPI, HTTPException, Query, Response
from rio_tiler.errors import EmptyMosaicError
from rio_tiler.io import COGReader
from rio_tiler.mosaic import mosaic_reader
from rio_tiler.mosaic.methods.defaults import FirstMethod
from starlette.middleware.cors import CORSMiddleware

from core.burn_severity import (
    BURN_SEVERITY_COLORMAP,
    load_burn_severity_assets,
    newest_first_asset_uris,
)
from core.settings import settings
from core.storage import get_storage

_STORAGE = get_storage()
_BURN_SEVERITY_TILE_SLOTS = BoundedSemaphore(2)
_TRANSPARENT_TILE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVQImWNgYAAAAAMAAaCmo9QAAAAASUVORK5CYII="
)
app = FastAPI(
    title="EMBER Burn-Severity Tiler",
    description="Dynamic map tiles for EMBER's published burn-severity COGs.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET"],
    allow_headers=["*"],
)

@lru_cache(maxsize=1)
def _burn_severity_assets() -> dict[int, str]:
    """Load the annual COG manifest once per tiler process."""
    return load_burn_severity_assets(_STORAGE)


def _read_burn_severity_tile(
    asset: str,
    x: int,
    y: int,
    z: int,
):
    """Read one annual COG tile for rio-tiler's mosaic function."""
    with COGReader(asset) as source:
        return source.tile(x, y, z, nodata=0)


@lru_cache(maxsize=512)
def _render_burn_severity_tile(
    z: int,
    x: int,
    y: int,
    selected_years: tuple[int, ...],
) -> bytes:
    """Render and cache one newest-first multi-year tile."""
    available = _burn_severity_assets()
    ordered_assets = newest_first_asset_uris(available, set(selected_years))
    try:
        with _BURN_SEVERITY_TILE_SLOTS:
            image, _ = mosaic_reader(
                ordered_assets,
                _read_burn_severity_tile,
                x,
                y,
                z,
                pixel_selection=FirstMethod,
                threads=min(4, len(ordered_assets)),
                chunk_size=min(4, len(ordered_assets)),
            )
    except EmptyMosaicError:
        return _TRANSPARENT_TILE
    return image.render(img_format="PNG", colormap=BURN_SEVERITY_COLORMAP)


@app.get("/burn-severity/tiles/{z}/{x}/{y}.png", tags=["Burn Severity"])
def burn_severity_tile(
    z: int,
    x: int,
    y: int,
    years: str = Query(
        default="",
        description="Comma-separated years. Newest selected year wins at overlapping pixels.",
    ),
) -> Response:
    """Render selected annual rasters as one newest-valid-pixel mosaic."""
    available = _burn_severity_assets()
    try:
        selected_years = (
            {int(value.strip()) for value in years.split(",") if value.strip()}
            if years
            else set(available)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Years must be comma-separated integers.") from exc

    unknown = sorted(selected_years - set(available))
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unavailable burn-severity years: {unknown}")
    if not selected_years:
        raise HTTPException(status_code=422, detail="Select at least one burn-severity year.")

    png = _render_burn_severity_tile(
        z,
        x,
        y,
        tuple(sorted(selected_years, reverse=True)),
    )
    return Response(
        png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    """Liveness endpoint used by local compose and container health checks."""
    return {"status": "ok"}
