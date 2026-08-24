"""Folium map construction for utility/wildfire overlays and optional raster tiles."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import folium
import streamlit as st
from branca.element import MacroElement, Template
from streamlit_folium import st_folium

from core.models import Utility, UtilitySource
from core.settings import settings

# Dashboard scope: California/Colorado through Oregon/Washington.
OVERVIEW_BOUNDS = [[32.3, -125.0], [49.2, -102.0]]
class BurnSeverityControl(MacroElement):
    """Leaflet control for selecting years on an existing severity tile layer."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        (function() {
            var map = {{ this._parent.get_name() }};
            var years = {{ this.years_json }};
            var tileBase = {{ this.tile_url_json }};
            var layer = {{ this.layer_name }};
            var control = L.control({position: "topright"});

            function selectedYears(container) {
                return Array.from(container.querySelectorAll(".ember-severity-year:checked"))
                    .map(function(input) { return Number(input.value); })
                    .sort(function(a, b) { return b - a; });
            }

            function update(container) {
                var selected = selectedYears(container);
                container.querySelector(".ember-severity-summary").textContent =
                    "Years (" + selected.length + ")";
                if (selected.length) {
                    layer.setUrl(tileBase + "?years=" + encodeURIComponent(selected.join(",")));
                }
            }

            control.onAdd = function() {
                var container = L.DomUtil.create(
                    "div",
                    "leaflet-control-layers ember-severity-control"
                );
                container.style.background = "white";
                container.style.padding = "8px 10px";
                container.style.maxHeight = "310px";
                container.style.overflow = "auto";
                var options = years.map(function(year) {
                    return '<label style="display:block;white-space:nowrap;">' +
                        '<input class="ember-severity-year" type="checkbox" value="' +
                        year + '" checked> ' + year + '</label>';
                }).join("");
                container.innerHTML =
                    '<details>' +
                    '<summary class="ember-severity-summary" style="cursor:pointer;">Years (' +
                    years.length + ')</summary>' +
                    '<div style="margin:5px 0;">' +
                    '<button type="button" class="ember-severity-all">Select all</button>' +
                    '</div><div>' + options + '</div></details>' +
                    '<div style="font-size:10px;color:#555;margin-top:5px;">' +
                    'Most recent selected burn wins</div>';
                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);
                container.addEventListener("change", function() { update(container); });
                container.querySelector(".ember-severity-all").addEventListener(
                    "click",
                    function() {
                        container.querySelectorAll(".ember-severity-year").forEach(
                            function(input) { input.checked = true; }
                        );
                        update(container);
                    }
                );
                return container;
            };
            control.addTo(map);
        })();
        {% endmacro %}
        """
    )

    def __init__(self, years: list[int], layer_name: str) -> None:
        super().__init__()
        self._name = "BurnSeverityControl"
        import json

        self.years_json = json.dumps(sorted(years, reverse=True))
        self.layer_name = layer_name
        self.tile_url_json = json.dumps(
            f"{settings.tiler_url.rstrip('/')}/burn-severity/tiles/"
            "{z}/{x}/{y}.png"
        )


class BurnSeverityLegend(MacroElement):
    """Leaflet legend that follows the visibility of a severity tile layer."""

    _template = Template(
        """
        {% macro script(this, kwargs) %}
        (function() {
            var map = {{ this._parent.get_name() }};
            var layer = {{ this.layer_name }};
            var legend = L.control({position: "bottomright"});
            var container;
            legend.onAdd = function() {
                container = L.DomUtil.create(
                    "div",
                    "leaflet-control-layers ember-severity-legend"
                );
                container.style.background = "rgba(255,255,255,0.95)";
                container.style.padding = "8px 10px";
                container.style.lineHeight = "18px";
                container.style.display = map.hasLayer(layer) ? "block" : "none";
                container.innerHTML =
                    '<strong>Burn severity</strong><br>' +
                    '<span style="color:#006837;">■</span> Increased greenness<br>' +
                    '<span style="color:#66bd63;">■</span> Unburned to low<br>' +
                    '<span style="color:#ffffb2;">■</span> Low<br>' +
                    '<span style="color:#fdae61;">■</span> Moderate<br>' +
                    '<span style="color:#d7191c;">■</span> High<br>' +
                    '<span style="color:#787878;">■</span> Non-processing area';
                L.DomEvent.disableClickPropagation(container);
                return container;
            };
            legend.addTo(map);
            map.on("overlayadd", function(event) {
                if (event.layer === layer && container) {
                    container.style.display = "block";
                }
            });
            map.on("overlayremove", function(event) {
                if (event.layer === layer && container) {
                    container.style.display = "none";
                }
            });
        })();
        {% endmacro %}
        """
    )

    def __init__(self, layer_name: str) -> None:
        super().__init__()
        self._name = "BurnSeverityLegend"
        self.layer_name = layer_name


def add_burn_severity_layer(
    fmap: folium.Map,
    years: list[int],
    *,
    show: bool,
    year_control: bool,
) -> None:
    """Add severity tiles for one focused year or an overview year selection."""
    if not years:
        return
    ordered_years = sorted(set(years), reverse=True)
    year_query = ",".join(str(year) for year in ordered_years)
    severity_url = (
        f"{settings.tiler_url.rstrip('/')}/burn-severity/tiles/"
        f"{{z}}/{{x}}/{{y}}.png?{urlencode({'years': year_query})}"
    )
    layer_name = (
        "Burn severity"
        if len(ordered_years) > 1
        else f"Burn severity ({ordered_years[0]})"
    )
    folium.map.CustomPane(
        "burn_severity",
        z_index=450,
        pointer_events=False,
    ).add_to(fmap)
    severity_layer = folium.TileLayer(
        tiles=severity_url,
        name=layer_name,
        attr="Burn severity / EMBER",
        overlay=True,
        control=True,
        show=show,
        opacity=0.9,
        update_when_idle=True,
        keep_buffer=0,
        pane="burn_severity",
    )
    severity_layer.add_to(fmap)
    BurnSeverityLegend(severity_layer.get_name()).add_to(fmap)
    if year_control and len(ordered_years) > 1:
        BurnSeverityControl(ordered_years, severity_layer.get_name()).add_to(fmap)


def _geojson_style(color: str) -> dict[str, Any]:
    return {"color": color, "weight": 2, "fillOpacity": 0.08}


def _feature_bounds(feature: dict) -> list[tuple[float, float]]:
    flat: list[tuple[float, float]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if "geometry" in node:
                visit(node["geometry"])
            if "coordinates" in node:
                visit(node["coordinates"])
            if "geometries" in node:
                visit(node["geometries"])
            return
        if isinstance(node, (list, tuple)) and len(node) == 2 and isinstance(node[0], (int, float)):
            flat.append((float(node[1]), float(node[0])))
            return
        if isinstance(node, list):
            for item in node:
                visit(item)

    visit(feature)
    return flat


def _folium_compatible_feature_collection(feature_collection: dict) -> dict:
    """Reduce mixed GeometryCollections to their polygon components for Folium."""
    features = []
    for feature in feature_collection["features"]:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "GeometryCollection":
            features.append(feature)
            continue

        polygon_parts = []
        for part in geometry.get("geometries", []):
            if part.get("type") == "Polygon":
                polygon_parts.append(part["coordinates"])
            elif part.get("type") == "MultiPolygon":
                polygon_parts.extend(part["coordinates"])
        if polygon_parts:
            features.append(
                {
                    **feature,
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": polygon_parts,
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def render_overview_map(
    source_watersheds: dict,
    service_areas: dict,
    burn_perimeters: dict,
    burn_severity_years: list[int],
    *,
    map_key: str,
) -> None:
    """Render statewide service, source, fire, and optional severity layers."""
    source_watersheds = _folium_compatible_feature_collection(source_watersheds)
    service_areas = _folium_compatible_feature_collection(service_areas)
    burn_perimeters = _folium_compatible_feature_collection(burn_perimeters)
    m = folium.Map(location=[44.5, -121.0], zoom_start=6, control_scale=True)

    if service_areas["features"]:
        service_group = folium.FeatureGroup(
            name=f"Utility service areas ({len(service_areas['features']):,})",
            show=True,
        )
        folium.GeoJson(
            service_areas,
            marker=folium.CircleMarker(
                radius=7,
                color="#2ca02c",
                weight=2,
                fill=True,
                fill_color="#2ca02c",
                fill_opacity=0.9,
            ),
            style_function=lambda _: {
                "color": "#2ca02c",
                "weight": 1.5,
                "fillColor": "#2ca02c",
                "fillOpacity": 0.10,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "state"],
                aliases=["Utility:", "State:"],
                sticky=False,
            ),
            smooth_factor=0.5,
        ).add_to(service_group)
        service_group.add_to(m)

    watershed_group = folium.FeatureGroup(
        name=f"Source watersheds ({len(source_watersheds['features']):,})",
        show=True,
    )
    folium.GeoJson(
        source_watersheds,
        style_function=lambda _: {
            "color": "#1f77b4",
            "weight": 1.5,
            "fillColor": "#1f77b4",
            "fillOpacity": 0.08,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["name", "state", "source_area_name"],
            aliases=["Utility:", "State:", "Source watershed:"],
            sticky=False,
        ),
        smooth_factor=0.5,
    ).add_to(watershed_group)
    watershed_group.add_to(m)

    perimeter_group = folium.FeatureGroup(
        name=f"Burn perimeters ({len(burn_perimeters['features']):,})",
        show=True,
    )
    folium.GeoJson(
        burn_perimeters,
        style_function=lambda _: {
            "color": "#d62728",
            "weight": 1,
            "fillColor": "#d62728",
            "fillOpacity": 0.12,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["name", "state", "acres"],
            aliases=["Wildfire:", "State:", "Burned acres:"],
            sticky=False,
        ),
        smooth_factor=0.5,
    ).add_to(perimeter_group)
    perimeter_group.add_to(m)

    add_burn_severity_layer(
        m,
        burn_severity_years,
        show=False,
        year_control=True,
    )

    # Fixed four-state bounds prevent one malformed/outlying polygon coordinate
    # from zooming the overview out to the whole country and requesting dozens
    # of expensive empty raster tiles.
    m.fit_bounds(OVERVIEW_BOUNDS)

    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    st_folium(
        m,
        key=map_key,
        height=540,
        use_container_width=True,
        returned_objects=[],
    )


def render_utility_case_study_map(
    utility: Utility,
    source_area_geojson: dict | None,
    service_area_geojson: dict | None,
    utility_sources: list[UtilitySource],
    wildfire_geojson: dict,
) -> None:
    """Render utility context and its spatially intersecting wildfire perimeters."""
    m = folium.Map(
        location=[utility.centroid_lat, utility.centroid_lon],
        zoom_start=9,
        control_scale=True,
    )
    points: list[tuple[float, float]] = []

    if service_area_geojson is not None:
        folium.GeoJson(
            service_area_geojson,
            name="Utility service area",
            marker=folium.CircleMarker(
                radius=7,
                color="#2ca02c",
                weight=2,
                fill=True,
                fill_color="#2ca02c",
                fill_opacity=0.9,
            ),
            style_function=lambda _: _geojson_style("#2ca02c"),
        ).add_to(m)
        points += _feature_bounds(service_area_geojson)
    if source_area_geojson is not None:
        folium.GeoJson(
            source_area_geojson,
            name="Source water area",
            style_function=lambda _: _geojson_style("#1f77b4"),
        ).add_to(m)
        points += _feature_bounds(source_area_geojson)

    mapped_sources = [
        source
        for source in utility_sources
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
            points.append((source.latitude, source.longitude))
        source_group.add_to(m)

    compatible_wildfires = _folium_compatible_feature_collection(wildfire_geojson)
    if compatible_wildfires["features"]:
        wildfire_group = folium.FeatureGroup(
            name=f"Wildfire boundaries ({len(compatible_wildfires['features'])})"
        )
        folium.GeoJson(
            compatible_wildfires,
            style_function=lambda _: {
                "color": "#d62728",
                "weight": 2,
                "fillColor": "#d62728",
                "fillOpacity": 0.18,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "year", "acres"],
                aliases=["Wildfire:", "Year:", "Burned acres:"],
                sticky=False,
            ),
        ).add_to(wildfire_group)
        wildfire_group.add_to(m)
        points += _feature_bounds(compatible_wildfires)

    if points:
        latitudes = [point[0] for point in points]
        longitudes = [point[1] for point in points]
        m.fit_bounds(
            [[min(latitudes), min(longitudes)], [max(latitudes), max(longitudes)]]
        )

    folium.LayerControl(collapsed=False).add_to(m)
    st_folium(
        m,
        key=f"case_study_map_{utility.utility_id}",
        height=360,
        use_container_width=True,
        returned_objects=[],
    )
