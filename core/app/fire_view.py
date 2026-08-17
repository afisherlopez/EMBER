"""Fire x utilities view: all water utilities whose source areas overlap one wildfire.

The symmetric counterpart to the utility x year-range view: pick a wildfire and see every
utility source area it burned into, with per-utility overlap stats (year, percent of the
source area, and overlap in both acres and km²) plus the fire's total burned acreage.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import date

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from core.app.map_view import add_burn_severity_layer
from core.catalog import Catalog
from core.models import (
    IntersectingServiceArea,
    IntersectingSourceLocation,
    IntersectingUtility,
    Wildfire,
    WildfireSummary,
)

# 1 km² = 247.105381 acres; used to report overlap in acres alongside km².
KM2_TO_ACRES = 247.105381
# Cap perimeter/source-area polygons drawn on the map to keep the browser responsive.
MAX_UTILITIES_ON_MAP = 400


def _utility_style(_: dict) -> dict:
    return {"color": "#1f77b4", "weight": 2, "fillColor": "#1f77b4", "fillOpacity": 0.12}


def _selected_utility_style(_: dict) -> dict:
    return {"color": "#f2c94c", "weight": 3, "fillColor": "#f2c94c", "fillOpacity": 0.20}


def _service_area_style(_: dict) -> dict:
    return {"color": "#2ca02c", "weight": 2, "fillColor": "#2ca02c", "fillOpacity": 0.12}


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


def _estimated_source_area_km2(utility: IntersectingUtility) -> float:
    """Estimate total source area from overlap area and overlap percentage."""
    if (
        utility.overlap_area_km2 is None
        or utility.overlap_pct_of_source is None
        or utility.overlap_pct_of_source <= 0
    ):
        return math.inf
    return utility.overlap_area_km2 * 100.0 / utility.overlap_pct_of_source


def _render_map(
    fire_geojson: dict,
    utilities: list[IntersectingUtility],
    service_areas: list[IntersectingServiceArea],
    source_locations: list[IntersectingSourceLocation],
    selected_utility_id: str | None,
    map_key: str,
    burn_severity_year: int | None,
) -> str | None:
    fmap = folium.Map(location=[44.0, -120.5], zoom_start=7, control_scale=True)
    fmap.get_root().html.add_child(
        folium.Element(
            """
            <style>
            html:focus,
            body:focus,
            #root:focus,
            .leaflet-container:focus,
            .leaflet-container *:focus,
            .leaflet-interactive:focus,
            .leaflet-interactive:focus-visible {
                outline: none !important;
                box-shadow: none !important;
            }
            </style>
            """
        )
    )
    bounds_points: list[tuple[float, float]] = []

    if burn_severity_year is not None:
        add_burn_severity_layer(
            fmap,
            [burn_severity_year],
            show=False,
            year_control=False,
        )

    folium.GeoJson(fire_geojson, name="Wildfire perimeter", style_function=_fire_style).add_to(fmap)
    _collect_points(fire_geojson["geometry"], bounds_points)

    # Leaflet draws later layers on top. Draw largest source areas first so smaller
    # polygons remain hoverable, then draw the selected utility last for emphasis.
    ordered = sorted(utilities, key=_estimated_source_area_km2, reverse=True)
    shown = ordered[:MAX_UTILITIES_ON_MAP]
    selected_utility = next(
        (utility for utility in utilities if utility.utility_id == selected_utility_id),
        None,
    )
    if selected_utility is not None and selected_utility not in shown:
        shown[-1] = selected_utility
    shown = [utility for utility in shown if utility.utility_id != selected_utility_id]
    if selected_utility is not None:
        shown.append(selected_utility)

    tooltip_to_utility_id: dict[str, str] = {}
    if shown:
        util_group = folium.FeatureGroup(name=f"Overlapping source areas ({len(shown)})")
        for utility in shown:
            geometry = json.loads(utility.geometry_geojson)
            pct = _format_overlap_pct(utility.overlap_pct_of_source)
            pct_text = f"{pct}%" if pct is not None else "n/a"
            tooltip = (
                f"{utility.name} ({utility.state}) — {utility.source_area_name} — "
                f"{pct_text} of source area"
            )
            tooltip_to_utility_id[tooltip] = utility.utility_id
            folium.GeoJson(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {"utility_id": utility.utility_id},
                },
                style_function=(
                    _selected_utility_style
                    if utility.utility_id == selected_utility_id
                    else _utility_style
                ),
                tooltip=tooltip,
            ).add_to(util_group)
            _collect_points(geometry, bounds_points)
        util_group.add_to(fmap)

    if service_areas:
        service_group = folium.FeatureGroup(
            name=f"Overlapping service areas ({len(service_areas)})"
        )
        for service_area in service_areas[:MAX_UTILITIES_ON_MAP]:
            geometry = json.loads(service_area.geometry_geojson)
            folium.GeoJson(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {"utility_id": service_area.utility_id},
                },
                marker=folium.CircleMarker(
                    radius=7,
                    color="#2ca02c",
                    weight=2,
                    fill=True,
                    fill_color="#2ca02c",
                    fill_opacity=0.9,
                ),
                style_function=_service_area_style,
                tooltip=f"{service_area.name} ({service_area.state}) — service area",
            ).add_to(service_group)
            _collect_points(geometry, bounds_points)
        service_group.add_to(fmap)

    if source_locations:
        source_group = folium.FeatureGroup(
            name=f"Surface water points ({len(source_locations)})"
        )
        for source in source_locations:
            folium.CircleMarker(
                location=[source.latitude, source.longitude],
                radius=6,
                color="#1f77b4",
                weight=2,
                fill=True,
                fill_color="#1f77b4",
                fill_opacity=0.8,
                tooltip=f"{source.source_name} — {source.utility_name}",
            ).add_to(source_group)
            bounds_points.append((source.latitude, source.longitude))
        source_group.add_to(fmap)

    folium.LayerControl(collapsed=True).add_to(fmap)

    if bounds_points:
        lats = [p[0] for p in bounds_points]
        lons = [p[1] for p in bounds_points]
        fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    map_data = st_folium(
        fmap,
        key=map_key,
        width=750,
        height=560,
        returned_objects=["last_object_clicked_tooltip"],
    )
    clicked_tooltip = map_data.get("last_object_clicked_tooltip")
    return tooltip_to_utility_id.get(clicked_tooltip) if clicked_tooltip else None


@st.fragment
def _render_linked_utility_map_table(
    fire_geojson: dict,
    utilities: list[IntersectingUtility],
    service_areas: list[IntersectingServiceArea],
    source_locations: list[IntersectingSourceLocation],
    show_california_categories: bool,
    wildfire_id: str,
    utility_state_filter: str,
    burn_severity_year: int | None,
) -> None:
    """Render linked map/table selection without rerunning the full page."""
    selection_context = f"{wildfire_id}:{utility_state_filter}"
    if st.session_state.get("fire_view_selection_context") != selection_context:
        st.session_state["fire_view_selection_context"] = selection_context
        st.session_state["fire_view_selected_utility_id"] = None

    valid_utility_ids = {utility.utility_id for utility in utilities}
    selected_utility_id = st.session_state.get("fire_view_selected_utility_id")
    if selected_utility_id not in valid_utility_ids:
        selected_utility_id = None
        st.session_state["fire_view_selected_utility_id"] = None

    map_col, table_col = st.columns([3, 2], gap="large")
    with map_col:
        clicked_utility_id = _render_map(
            fire_geojson,
            utilities,
            service_areas,
            source_locations,
            selected_utility_id,
            map_key=(
                f"fire_utilities_map_{wildfire_id}_{utility_state_filter}_"
                f"{selected_utility_id or 'none'}"
            ),
            burn_severity_year=burn_severity_year,
        )
        if clicked_utility_id and clicked_utility_id != selected_utility_id:
            st.session_state["fire_view_selected_utility_id"] = clicked_utility_id
            st.rerun(scope="fragment")

    with table_col:
        if utilities:
            st.subheader("Source-water areas")
            table_rows = [
                {
                    "_utility_id": utility.utility_id,
                    "Water utility": utility.name,
                    "State": utility.state,
                    "Source area": utility.source_area_name,
                    "Year": (
                        str(utility.ignition_year)
                        if utility.ignition_year is not None
                        else None
                    ),
                    "Overlap % of source": _format_overlap_pct(
                        utility.overlap_pct_of_source
                    ),
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
            ]
            table_data = pd.DataFrame(table_rows)
            visible_columns = [
                column for column in table_data.columns if column != "_utility_id"
            ]

            def highlight_selected_row(row: pd.Series) -> list[str]:
                style = (
                    "background-color: #fff8cc;"
                    if row["_utility_id"] == selected_utility_id
                    else ""
                )
                return [style] * len(row)

            styled_table = table_data.style.apply(highlight_selected_row, axis=1)
            selected_row_index = next(
                (
                    index
                    for index, row in enumerate(table_rows)
                    if row["_utility_id"] == selected_utility_id
                ),
                None,
            )
            selection_default = (
                {
                    "selection": {
                        "rows": [],
                        "columns": [],
                        "cells": [(selected_row_index, visible_columns[0])],
                    }
                }
                if selected_row_index is not None
                else None
            )
            table_event = st.dataframe(
                styled_table,
                key=(
                    f"fire_utilities_table_{wildfire_id}_{utility_state_filter}_"
                    f"{selected_utility_id or 'none'}"
                ),
                width="stretch",
                hide_index=True,
                column_order=visible_columns,
                on_select="rerun",
                selection_mode="single-cell",
                selection_default=selection_default,
            )
            if table_event.selection.cells:
                selected_cell = table_event.selection.cells[0]
                selected_row = selected_cell[0]
                clicked_table_utility_id = table_rows[selected_row]["_utility_id"]
                if clicked_table_utility_id != selected_utility_id:
                    st.session_state["fire_view_selected_utility_id"] = (
                        clicked_table_utility_id
                    )
                    st.rerun(scope="fragment")
        if show_california_categories:
            st.subheader("Utility service areas")
            if service_areas:
                st.dataframe(
                    [
                        {"Water utility": area.name, "State": area.state}
                        for area in service_areas
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No utility service areas overlapped this wildfire.")

            st.subheader("Surface water points")
            if source_locations:
                st.dataframe(
                    [
                        {
                            "Surface water point": source.source_name,
                            "Type": source.source_type,
                            "Water utility": source.utility_name,
                            "Connection": (
                                "Direct"
                                if source.depth == 1
                                else f"Upstream ({source.depth} links)"
                            ),
                        }
                        for source in source_locations
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No connected surface water points fell within this wildfire.")


def render_fire_view(
    catalog: Catalog,
    wildfires: list[Wildfire],
    render_overview: Callable[[], None],
    available_burn_severity_years: set[int],
) -> None:
    """Render the 'select a wildfire -> overlapping utilities' view."""
    st.subheader("Water utility areas and sources overlapped by a wildfire")

    wildfire_states = sorted({wildfire.state for wildfire in wildfires if wildfire.state})
    known_acres = [wildfire.acres for wildfire in wildfires if wildfire.acres is not None]
    max_available_acres = math.ceil(max(known_acres)) if known_acres else 0

    state_col, min_acres_col, max_acres_col, unknown_col = st.columns([2, 2, 2, 1.5])
    with state_col:
        wildfire_state_filter = st.selectbox(
            "Wildfire state", ["All"] + wildfire_states, index=0
        )
    with min_acres_col:
        min_acres = st.number_input(
            "Minimum burned acres",
            min_value=0,
            max_value=max_available_acres,
            value=0,
            step=100,
        )
    with max_acres_col:
        max_acres = st.number_input(
            "Maximum burned acres",
            min_value=0,
            max_value=max_available_acres,
            value=max_available_acres,
            step=100,
        )
    with unknown_col:
        include_unknown_acres = st.checkbox("Include unknown acreage", value=True)

    if min_acres > max_acres:
        st.warning("Minimum burned acres must not exceed maximum burned acres.")
        return

    filtered_wildfires = [
        wildfire
        for wildfire in wildfires
        if (wildfire_state_filter == "All" or wildfire.state == wildfire_state_filter)
        and (
            include_unknown_acres
            if wildfire.acres is None
            else min_acres <= wildfire.acres <= max_acres
        )
    ]
    st.caption(f"Showing {len(filtered_wildfires):,} of {len(wildfires):,} wildfires.")
    if not filtered_wildfires:
        st.info("No wildfires match the selected state and burned-acreage filters.")
        return

    ordered = sorted(
        filtered_wildfires,
        key=lambda item: (item.ignition_date or date.min, item.name),
        reverse=True,
    )
    fire_map = {_fire_label(w): w.wildfire_id for w in ordered}
    fire_label = st.selectbox(
        "Wildfire", options=list(fire_map.keys()), index=None, placeholder="Select wildfire"
    )
    wildfire_id = fire_map.get(fire_label) if fire_label else None

    if not wildfire_id:
        render_overview()
        st.info(
            "Select a wildfire to see the source areas, service areas, and connected "
            "surface water points it overlapped."
        )
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

    fire_geojson = catalog.get_geojson("wildfires", wildfire_id, simplify_tolerance=0.0)
    utilities = catalog.list_intersecting_utilities(wildfire_id)
    if summary.state == "CA":
        service_areas = catalog.list_intersecting_service_areas(wildfire_id)
        source_locations = catalog.list_intersecting_source_locations(wildfire_id)
        utilities = []
    else:
        service_areas = []
        source_locations = []
    burn_severity_year = (
        summary.ignition_year
        if summary.ignition_year in available_burn_severity_years
        else None
    )
    if not utilities and not service_areas and not source_locations:
        _render_map(
            fire_geojson,
            [],
            [],
            [],
            selected_utility_id=None,
            map_key=f"fire_utilities_map_{wildfire_id}_none",
            burn_severity_year=burn_severity_year,
        )
        if summary.state == "CA":
            st.warning(
                f"No utility service areas or connected surface water points overlapped "
                f"{summary.name}."
            )
        else:
            st.warning(f"No water utility source areas overlapped {summary.name}.")
        return

    if summary.state == "CA":
        st.markdown(
            f"**{len(service_areas)}** service area(s) and "
            f"**{len(source_locations)}** connected surface water point(s) overlapped "
            f"**{summary.name}**."
        )
    else:
        st.markdown(
            f"**{len(utilities)}** water utility source area(s) overlapped "
            f"**{summary.name}**."
        )

    _render_linked_utility_map_table(
        fire_geojson,
        utilities,
        service_areas,
        source_locations,
        summary.state == "CA",
        wildfire_id,
        "All",
        burn_severity_year,
    )
