"""Case-study view for wildfires intersecting a utility source area over time."""

from __future__ import annotations

import json
from collections.abc import Callable

import folium
import matplotlib

matplotlib.use("Agg")  # Headless backend; Streamlit renders the figure via st.pyplot.
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import streamlit as st  # noqa: E402

from core.app.map_view import SharedMapSlot, ViewSlots, map_viewport  # noqa: E402
from core.app.utility_sources import render_utility_sources  # noqa: E402
from core.catalog import Catalog  # noqa: E402
from core.models import IntersectingWildfire, Utility, UtilitySource  # noqa: E402

# Rendering many perimeters is the browser's bottleneck; cap and warn beyond this.
MAX_PERIMETERS_ON_MAP = 400


def _source_area_style(_: dict) -> dict:
    return {"color": "#1f77b4", "weight": 2, "fillColor": "#1f77b4", "fillOpacity": 0.12}


def _service_area_style(_: dict) -> dict:
    return {"color": "#2ca02c", "weight": 2, "fillColor": "#2ca02c", "fillOpacity": 0.12}


def _fire_style(_: dict) -> dict:
    return {"color": "#d62728", "weight": 1, "fillColor": "#d62728", "fillOpacity": 0.25}


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


def _format_impact_basis(value: str | None) -> str:
    labels = {
        "service_area": "Service area",
        "source_location": "Connected source location",
        "source_area": "Source-water area",
    }
    return " and ".join(
        labels.get(item, item.replace("_", " ").title())
        for item in (value or "").split(",")
        if item
    )


def _render_overlap_chart(
    fires: list[IntersectingWildfire], year_min: int, year_max: int
) -> None:
    """Bar chart of yearly overlap (km²) with a linear trendline.

    Sums ``overlap_area_km2`` across all fires per ignition year and keeps every year in
    the selected range on the axis: a year with no intersecting fire (or zero overlap)
    shows as a zero-height bar rather than being dropped, so the trend reads correctly.
    """
    totals: dict[int, float] = {}
    for fire in fires:
        if fire.ignition_year is None:
            continue
        totals[fire.ignition_year] = totals.get(fire.ignition_year, 0.0) + (
            fire.overlap_area_km2 or 0.0
        )
    years = list(range(year_min, year_max + 1))
    values = [totals.get(year, 0.0) for year in years]

    x = np.asarray(years, dtype=float)
    y = np.asarray(values, dtype=float)
    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.bar(x, y, width=0.8, color="#d62728", alpha=0.75, label="Overlap area")
    if len(years) >= 2:
        slope, intercept = np.polyfit(x, y, 1)
        ax.plot(
            x,
            slope * x + intercept,
            color="#1f77b4",
            linewidth=2,
            label=f"Trend ({slope:+.2f} km²/yr)",
        )
    ax.set_xlabel("Ignition year")
    ax.set_ylabel("Overlap (km²)")
    ax.set_ylim(bottom=0)
    ax.margins(x=0.01)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(fontsize=8)
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


def _build_map_groups(
    utility: Utility,
    source_area_geojson: dict | None,
    service_area_geojson: dict | None,
    sources: list[UtilitySource],
    fires: list[IntersectingWildfire],
) -> tuple[list[folium.FeatureGroup], list[tuple[float, float]]]:
    feature_groups: list[folium.FeatureGroup] = []
    bounds_points: list[tuple[float, float]] = []

    if service_area_geojson is not None:
        service_group = folium.FeatureGroup(name=f"{utility.name} service area")
        folium.GeoJson(
            service_area_geojson,
            marker=folium.CircleMarker(
                radius=7,
                color="#2ca02c",
                weight=2,
                fill=True,
                fill_color="#2ca02c",
                fill_opacity=0.9,
            ),
            style_function=_service_area_style,
        ).add_to(service_group)
        feature_groups.append(service_group)
        _collect_points(service_area_geojson["geometry"], bounds_points)
    if source_area_geojson is not None:
        source_area_group = folium.FeatureGroup(
            name=f"{utility.name} source water area"
        )
        folium.GeoJson(
            source_area_geojson,
            style_function=_source_area_style,
        ).add_to(source_area_group)
        feature_groups.append(source_area_group)
        _collect_points(source_area_geojson["geometry"], bounds_points)
    mapped_sources = [
        source
        for source in sources
        if source.latitude is not None and source.longitude is not None
    ]
    if mapped_sources:
        source_group = folium.FeatureGroup(
            name=f"Connected source locations ({len(mapped_sources)})"
        )
        for source in mapped_sources:
            folium.CircleMarker(
                location=[source.latitude, source.longitude],
                radius=6,
                color="#1f77b4",
                weight=2,
                fill=True,
                fill_color="#1f77b4",
                fill_opacity=0.8,
                tooltip=f"{source.source_name} — {source.source_type}",
            ).add_to(source_group)
            bounds_points.append((source.latitude, source.longitude))
        feature_groups.append(source_group)

    shown = fires[:MAX_PERIMETERS_ON_MAP]
    fire_group = folium.FeatureGroup(name=f"Intersecting wildfires ({len(shown)})")
    for fire in shown:
        geometry = json.loads(fire.geometry_geojson)
        if fire.impact_basis and fire.impact_basis != "source_area":
            impact_text = _format_impact_basis(fire.impact_basis)
        else:
            pct = _format_overlap_pct(fire.overlap_pct_of_source)
            impact_text = f"{pct}% of source area" if pct is not None else "Source area"
        tooltip = f"{fire.name} ({fire.ignition_year}) — {impact_text}"
        folium.GeoJson(
            {"type": "Feature", "geometry": geometry, "properties": {}},
            style_function=_fire_style,
            tooltip=tooltip,
        ).add_to(fire_group)
        _collect_points(geometry, bounds_points)
    feature_groups.append(fire_group)

    return feature_groups, bounds_points


def render_case_study_view(
    catalog: Catalog,
    utilities: list[Utility],
    slots: ViewSlots,
    shared_map: SharedMapSlot,
    show_overview: Callable[[], None],
) -> None:
    """Render the 'select a utility + year range -> intersecting wildfires' view."""
    with slots.controls:
        st.subheader("Wildfires intersecting a utility's source or service area")

        utility_states = sorted({u.state for u in utilities})
        state_col, control_col, year_col = st.columns([1, 2, 3])
        with state_col:
            utility_state_filter = st.selectbox(
                "Utility state filter", ["All"] + utility_states, index=0
            )

        filtered_utilities = utilities
        if utility_state_filter != "All":
            filtered_utilities = [u for u in utilities if u.state == utility_state_filter]

        utility_map = {f"{u.name} ({u.state})": u.utility_id for u in filtered_utilities}
        with control_col:
            utility_label = st.selectbox(
                "Water utility",
                options=list(utility_map.keys()),
                index=None,
                placeholder="Select utility",
            )
        utility_id = utility_map.get(utility_label) if utility_label else None

        bounds = catalog.wildfire_year_bounds()
        if bounds is None:
            st.info("No wildfire data is available.")
            return
        min_year, max_year = bounds
        with year_col:
            if min_year == max_year:
                year_range = (min_year, max_year)
                st.caption(f"Only year {min_year} is available in the data.")
            else:
                year_range = st.slider(
                    "Ignition year range",
                    min_value=min_year,
                    max_value=max_year,
                    value=(max(min_year, max_year - 25), max_year),
                    format="%d",
                )

    if not utility_id:
        show_overview()
        with slots.panel:
            st.info(
                "Select a water utility to see the wildfires that intersected "
                "its source area."
            )
        return

    utility = next(u for u in filtered_utilities if u.utility_id == utility_id)
    fires = catalog.list_intersecting_wildfires(utility_id, year_range[0], year_range[1])
    source_area_geojson = catalog.get_utility_geojson(utility_id, "source")
    service_area_geojson = catalog.get_utility_geojson(utility_id, "service")
    sources = catalog.list_utility_sources(utility_id)

    overlay_id = f"utility:{utility_id}:{year_range[0]}:{year_range[1]}"
    cached_viewport = shared_map.overlay_viewport(overlay_id)
    if cached_viewport is not None:
        center, zoom = cached_viewport
        feature_groups = []
    else:
        feature_groups, bounds_points = _build_map_groups(
            utility,
            source_area_geojson,
            service_area_geojson,
            sources,
            fires,
        )
        center, zoom = map_viewport(
            bounds_points,
            default_center=(utility.centroid_lat, utility.centroid_lon),
            default_zoom=9,
        )
    shared_map.show(
        feature_groups,
        overlay_id=overlay_id,
        center=center,
        zoom=zoom,
        height=560,
    )

    with slots.panel:
        if sources:
            render_utility_sources(sources, compact=True)
        st.subheader("Intersecting wildfires")

        if not fires:
            if utility.state == "CA":
                st.warning(
                    f"No wildfires overlapped {utility.name}'s service area or connected "
                    f"source locations between {year_range[0]} and {year_range[1]}."
                )
            elif source_area_geojson is None and service_area_geojson is not None:
                st.warning(
                    f"No wildfires intersected {utility.name}'s mapped service location "
                    f"between {year_range[0]} and {year_range[1]}."
                )
            elif source_area_geojson is None:
                st.warning(
                    f"No mapped source-water area is available for {utility.name}, "
                    "so wildfire intersections cannot be calculated yet."
                )
            else:
                st.warning(
                    f"No wildfires intersected {utility.name}'s source area between "
                    f"{year_range[0]} and {year_range[1]}."
                )
            return

        if utility.state == "CA":
            st.markdown(
                f"**{len(fires)}** wildfire(s) overlapped **{utility.name}**'s service "
                f"area or connected source locations between **{year_range[0]}** and "
                f"**{year_range[1]}**."
            )
        elif source_area_geojson is None and service_area_geojson is not None:
            st.markdown(
                f"**{len(fires)}** wildfire(s) intersected **{utility.name}**'s mapped "
                f"service location between **{year_range[0]}** and **{year_range[1]}**."
            )
        else:
            st.markdown(
                f"**{len(fires)}** wildfire(s) intersected **{utility.name}**'s source area "
                f"between **{year_range[0]}** and **{year_range[1]}**."
            )
        if len(fires) > MAX_PERIMETERS_ON_MAP:
            st.caption(
                f"Showing the {MAX_PERIMETERS_ON_MAP} largest-overlap perimeters on the map; "
                "the table below lists all of them."
            )

        if utility.state == "CA" or source_area_geojson is None:
            table_rows = [
                {
                    "Wildfire": fire.name,
                    "Year": (
                        str(fire.ignition_year)
                        if fire.ignition_year is not None
                        else None
                    ),
                    "Acres": round(fire.acres) if fire.acres is not None else None,
                    "Affected feature": _format_impact_basis(fire.impact_basis),
                }
                for fire in fires
            ]
        else:
            table_rows = [
                {
                    "Wildfire": fire.name,
                    "Year": (
                        str(fire.ignition_year)
                        if fire.ignition_year is not None
                        else None
                    ),
                    "Acres": round(fire.acres) if fire.acres is not None else None,
                    "Overlap % of source": _format_overlap_pct(
                        fire.overlap_pct_of_source
                    ),
                    "Overlap km²": (
                        round(fire.overlap_area_km2, 2)
                        if fire.overlap_area_km2 is not None
                        else None
                    ),
                }
                for fire in fires
            ]
        st.dataframe(
            table_rows,
            width="stretch",
            hide_index=True,
        )
        if utility.state != "CA" and source_area_geojson is not None:
            st.markdown("**Overlap area over time**")
            _render_overlap_chart(fires, year_range[0], year_range[1])
