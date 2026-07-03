"""Fire x utilities view: all water utilities whose source areas overlap one wildfire.

The symmetric counterpart to the utility x year-range view: pick a wildfire and see every
utility source area it burned into, with per-utility overlap stats (year, percent of the
source area, and overlap in both acres and km²) plus the fire's total burned acreage.
"""

from __future__ import annotations

import json
from datetime import date

import folium
import streamlit as st
from streamlit_folium import st_folium

from core.catalog import Catalog
from core.models import IntersectingUtility, WildfireSummary

# 1 km² = 247.105381 acres; used to report overlap in acres alongside km².
KM2_TO_ACRES = 247.105381
# Cap perimeter/source-area polygons drawn on the map to keep the browser responsive.
MAX_UTILITIES_ON_MAP = 400


def _utility_style(_: dict) -> dict:
    return {"color": "#1f77b4", "weight": 2, "fillColor": "#1f77b4", "fillOpacity": 0.12}


def _fire_style(_: dict) -> dict:
    return {"color": "#d62728", "weight": 2, "fillColor": "#d62728", "fillOpacity": 0.20}


def _collect_points(geometry: dict, sink: list[tuple[float, float]]) -> None:
    def visit(node: object) -> None:
        if isinstance(node, (list, tuple)) and len(node) == 2 and isinstance(node[0], (int, float)):
            sink.append((float(node[1]), float(node[0])))
            return
        if isinstance(node, list):
            for item in node:
                visit(item)

    visit(geometry.get("coordinates", []))


def _format_overlap_pct(pct: float | None) -> str | None:
    """Format overlap percent to one decimal, showing '< 0.1' for tiny positive values."""
    if pct is None:
        return None
    if 0 < pct < 0.1:
        return "< 0.1"
    return f"{pct:.1f}"


def _fire_label(wildfire) -> str:
    ignition = wildfire.ignition_date.isoformat() if isinstance(wildfire.ignition_date, date) else "unknown"
    return f"{wildfire.name} ({ignition}, {wildfire.state})"


def _render_map(
    fire_geojson: dict, utilities: list[IntersectingUtility]
) -> None:
    fmap = folium.Map(location=[44.0, -120.5], zoom_start=7, control_scale=True)
    bounds_points: list[tuple[float, float]] = []

    folium.GeoJson(fire_geojson, name="Wildfire perimeter", style_function=_fire_style).add_to(fmap)
    _collect_points(fire_geojson["geometry"], bounds_points)

    shown = utilities[:MAX_UTILITIES_ON_MAP]
    util_group = folium.FeatureGroup(name=f"Overlapping source areas ({len(shown)})")
    for utility in shown:
        geometry = json.loads(utility.geometry_geojson)
        pct = _format_overlap_pct(utility.overlap_pct_of_source)
        pct_text = f"{pct}%" if pct is not None else "n/a"
        tooltip = f"{utility.name} — {pct_text} of source area"
        folium.GeoJson(
            {"type": "Feature", "geometry": geometry, "properties": {}},
            style_function=_utility_style,
            tooltip=tooltip,
        ).add_to(util_group)
        _collect_points(geometry, bounds_points)
    util_group.add_to(fmap)
    folium.LayerControl(collapsed=True).add_to(fmap)

    if bounds_points:
        lats = [p[0] for p in bounds_points]
        lons = [p[1] for p in bounds_points]
        fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    st_folium(fmap, width=750, height=560, returned_objects=[])


def render_fire_view(catalog: Catalog, wildfires) -> None:
    """Render the 'select a wildfire -> overlapping utilities' view."""
    st.subheader("Water utilities a wildfire's perimeter overlapped")

    ordered = sorted(
        wildfires, key=lambda item: (item.ignition_date or date.min, item.name), reverse=True
    )
    fire_map = {_fire_label(w): w.wildfire_id for w in ordered}
    fire_label = st.selectbox(
        "Wildfire", options=list(fire_map.keys()), index=None, placeholder="Select wildfire"
    )
    wildfire_id = fire_map.get(fire_label) if fire_label else None

    if not wildfire_id:
        st.info("Select a wildfire to see the water utility source areas it overlapped.")
        return

    summary: WildfireSummary | None = catalog.get_wildfire_summary(wildfire_id)
    if summary is None:
        st.warning("No details found for the selected wildfire.")
        return

    header_col, acreage_col = st.columns([3, 2])
    with header_col:
        year_text = str(summary.ignition_year) if summary.ignition_year is not None else "unknown"
        st.markdown(f"### {summary.name}\nIgnition year **{year_text}** · {summary.state}")
    with acreage_col:
        acreage_text = f"{summary.acres:,.0f} acres" if summary.acres is not None else "Not available"
        st.metric("Total burned area", acreage_text)

    utilities = catalog.list_intersecting_utilities(wildfire_id)
    if not utilities:
        st.warning(f"No water utility source areas overlapped {summary.name}.")
        return

    st.markdown(
        f"**{len(utilities)}** water utility source area(s) overlapped **{summary.name}**."
    )

    fire_geojson = catalog.get_geojson("wildfires", wildfire_id, simplify_tolerance=0.0)
    map_col, table_col = st.columns([3, 2], gap="large")
    with map_col:
        _render_map(fire_geojson, utilities)
    with table_col:
        st.dataframe(
            [
                {
                    "Water utility": utility.name,
                    "State": utility.state,
                    "Source area": utility.source_area_name,
                    "Year": (
                        str(utility.ignition_year) if utility.ignition_year is not None else None
                    ),
                    "Overlap % of source": _format_overlap_pct(utility.overlap_pct_of_source),
                    "Overlap acres": (
                        round(utility.overlap_area_km2 * KM2_TO_ACRES)
                        if utility.overlap_area_km2 is not None
                        else None
                    ),
                    "Overlap km²": (
                        round(utility.overlap_area_km2, 2)
                        if utility.overlap_area_km2 is not None
                        else None
                    ),
                }
                for utility in utilities
            ],
            use_container_width=True,
            hide_index=True,
        )
