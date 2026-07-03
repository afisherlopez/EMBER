"""Folium map construction for utility/wildfire overlays and optional raster tiles."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import folium
import requests
from streamlit_folium import st_folium

from core.models import MetricDefinition, RasterAsset, Utility, Wildfire
from core.settings import settings
from core.states import DataState


def cog_tilejson_url(asset: RasterAsset, metric: MetricDefinition) -> str:
    """Build TiTiler tilejson endpoint URL for one raster asset."""
    colormap_name = asset.colormap_name or metric.default_colormap or "ylorbr"
    rescale_min = asset.rescale_min
    rescale_max = asset.rescale_max
    if rescale_min is None or rescale_max is None:
        default = metric.default_rescale or (0.0, 100.0)
        rescale_min, rescale_max = default
    query = urlencode(
        {
            "url": asset.cog_uri,
            "rescale": f"{rescale_min},{rescale_max}",
            "colormap_name": colormap_name,
        }
    )
    return f"{settings.tiler_url.rstrip('/')}/cog/WebMercatorQuad/tilejson.json?{query}"


def _geojson_style(color: str) -> dict[str, Any]:
    return {"color": color, "weight": 2, "fillOpacity": 0.08}


def _feature_bounds(feature: dict) -> list[tuple[float, float]]:
    coords = feature["geometry"]["coordinates"]
    flat: list[tuple[float, float]] = []

    def visit(node: Any) -> None:
        if isinstance(node, (list, tuple)) and len(node) == 2 and isinstance(node[0], (int, float)):
            flat.append((float(node[1]), float(node[0])))
            return
        if isinstance(node, list):
            for item in node:
                visit(item)

    visit(coords)
    return flat


def render_map(
    utility: Utility,
    wildfire: Wildfire,
    utility_geojson: dict,
    wildfire_geojson: dict,
    raster_metric: MetricDefinition | None,
    raster_asset: RasterAsset | None,
    raster_state: DataState,
) -> None:
    """Render display-only Folium map with optional raster tile layer."""
    m = folium.Map(location=[utility.centroid_lat, utility.centroid_lon], zoom_start=9, control_scale=True)

    if raster_metric and raster_asset and raster_state == "available":
        tilejson_resp = requests.get(cog_tilejson_url(raster_asset, raster_metric), timeout=5)
        tilejson_resp.raise_for_status()
        tile_url = tilejson_resp.json()["tiles"][0]
        folium.TileLayer(tiles=tile_url, name=raster_metric.display_name, attr="EMBER/TiTiler", overlay=True).add_to(m)

    folium.GeoJson(utility_geojson, name="Source area", style_function=lambda _: _geojson_style("blue")).add_to(m)
    folium.GeoJson(wildfire_geojson, name="Wildfire perimeter", style_function=lambda _: _geojson_style("red")).add_to(m)

    points = _feature_bounds(utility_geojson) + _feature_bounds(wildfire_geojson)
    if points:
        latitudes = [point[0] for point in points]
        longitudes = [point[1] for point in points]
        m.fit_bounds([[min(latitudes), min(longitudes)], [max(latitudes), max(longitudes)]])

    # Disable returning map interaction payloads so pan/zoom does not trigger app reruns.
    st_folium(m, width=700, height=500, returned_objects=[])
