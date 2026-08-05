"""Discovery helpers for published annual MTBS burn-severity COGs."""

from __future__ import annotations

import json
import re

from core.storage import Storage

BURN_SEVERITY_PREFIX = "fire_burn_severity/cogs"
BURN_SEVERITY_MANIFEST = f"{BURN_SEVERITY_PREFIX}/manifest.json"
BURN_SEVERITY_COLORMAP = {
    1: (0, 104, 55, 190),
    2: (102, 189, 99, 190),
    3: (255, 255, 178, 205),
    4: (253, 174, 97, 215),
    5: (215, 25, 28, 225),
    6: (120, 120, 120, 180),
}
_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")


def _year_from_uri(uri: str) -> int | None:
    """Extract the final four-digit year from a raster filename."""
    matches = _YEAR_PATTERN.findall(uri.rsplit("/", 1)[-1])
    return int(matches[-1]) if matches else None


def load_burn_severity_assets(storage: Storage) -> dict[int, str]:
    """Return annual burn-severity COG URIs keyed by year.

    GCS deployments use a small manifest so the runtime service account only
    needs object-read access, not bucket-list access. Local development falls
    back to discovering TIFFs below the same prefix.
    """
    if storage.exists(BURN_SEVERITY_MANIFEST):
        payload = json.loads(storage.read_bytes(BURN_SEVERITY_MANIFEST))
        assets = payload.get("assets", [])
        return {
            int(asset["year"]): str(asset["cog_uri"])
            for asset in assets
            if asset.get("year") is not None and asset.get("cog_uri")
        }

    assets: dict[int, str] = {}
    for uri in storage.list_uris(BURN_SEVERITY_PREFIX, suffixes=(".tif", ".tiff")):
        year = _year_from_uri(uri)
        if year is not None:
            assets[year] = uri
    return assets


def newest_first_asset_uris(assets: dict[int, str], years: set[int]) -> list[str]:
    """Return selected raster URIs ordered so newest valid pixels win a mosaic."""
    return [assets[year] for year in sorted(years, reverse=True)]
