"""Folium overlay builders and the single persistent Leaflet component."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import folium
import streamlit as st
import streamlit_folium
from branca.element import MacroElement, Template
from streamlit_folium import st_folium

from core.models import UtilitySource
from core.settings import settings

# Dashboard scope: California/Colorado through Oregon/Washington.
OVERVIEW_BOUNDS = [[32.3, -125.0], [49.2, -102.0]]
OVERVIEW_CENTER = (
    (OVERVIEW_BOUNDS[0][0] + OVERVIEW_BOUNDS[1][0]) / 2,
    (OVERVIEW_BOUNDS[0][1] + OVERVIEW_BOUNDS[1][1]) / 2,
)
OVERVIEW_ZOOM = 5
SHARED_MAP_KEY = "ember_shared_map"
_PARKED_OVERLAY_ID = "__parked__"
_MAP_CACHE_KEY = "_ember_shared_map_cache"
_FALLBACK_MAP_CACHE: dict[str, Any] = {"overlays": {}}


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


def map_viewport(
    points: list[tuple[float, float]],
    *,
    default_center: tuple[float, float] = (44.5, -121.0),
    default_zoom: int = 6,
    width_px: int = 760,
    height_px: int = 520,
) -> tuple[tuple[float, float], int]:
    """Compute the Leaflet center/zoom that fits all points, like fit_bounds would."""
    if not points:
        return default_center, default_zoom
    latitudes = [point[0] for point in points]
    longitudes = [point[1] for point in points]
    center = (
        (min(latitudes) + max(latitudes)) / 2,
        (min(longitudes) + max(longitudes)) / 2,
    )
    lat_span = max(latitudes) - min(latitudes)
    lon_span = max(longitudes) - min(longitudes)
    if lat_span < 1e-6 and lon_span < 1e-6:
        return center, default_zoom

    # Web mercator stretches latitude spans by ~1/cos(latitude) relative to longitude.
    stretch = max(0.2, math.cos(math.radians(center[0])))

    def fit(span_degrees: float, pixels: int) -> float:
        return math.log2((pixels * 360.0) / (256.0 * max(span_degrees, 1e-4)))

    zoom = math.floor(min(fit(lon_span, width_px), fit(lat_span / stretch, height_px)))
    return center, int(max(3, min(12, zoom)))


def _map_cache() -> dict[str, Any]:
    """Session cache for serialized Leaflet overlays. Empty outside Streamlit."""
    try:
        return st.session_state.setdefault(_MAP_CACHE_KEY, {"overlays": {}})
    except Exception:  # noqa: BLE001 - tests run without a ScriptRunContext
        return _FALLBACK_MAP_CACHE


def render_shared_map(
    feature_groups: list[folium.FeatureGroup],
    burn_severity_years: list[int],
    *,
    center: tuple[float, float],
    zoom: int,
    height: int,
    layer_control_collapsed: bool,
    overlay_id: str | None = None,
) -> dict:
    """Update overlays on one stable Leaflet component without remounting it.

    When ``overlay_id`` matches a previously serialized overlay, the expensive
    Folium-to-Leaflet conversion is skipped and the cached JS is reused. Parked
    (hidden) views send no overlay payload so tab switches stay cheap.
    """
    cache = _map_cache()
    overlay_cache: dict[str, dict[str, Any]] = cache.setdefault("overlays", {})
    reuse_js: str | None = None
    reuse_control: str | None = None
    serialize_groups = feature_groups or None

    if overlay_id == _PARKED_OVERLAY_ID:
        serialize_groups = None
    elif overlay_id and overlay_id in overlay_cache:
        reuse_js = overlay_cache[overlay_id]["feature_group"]
        reuse_control = overlay_cache[overlay_id]["layer_control"]
        serialize_groups = None

    base_map = folium.Map(
        location=list(OVERVIEW_CENTER),
        zoom_start=OVERVIEW_ZOOM,
        control_scale=True,
    )
    base_map.get_root().html.add_child(
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
    add_burn_severity_layer(
        base_map,
        burn_severity_years,
        show=False,
        year_control=True,
    )
    layer_control = folium.LayerControl(
        collapsed=layer_control_collapsed,
        position="topright",
    )

    original_component = streamlit_folium._component_func

    def _component_func(**kwargs):
        if reuse_js is not None:
            kwargs["feature_group"] = reuse_js
            kwargs["layer_control"] = reuse_control
        elif overlay_id and overlay_id != _PARKED_OVERLAY_ID:
            overlay_cache[overlay_id] = {
                "feature_group": kwargs.get("feature_group"),
                "layer_control": kwargs.get("layer_control"),
                "center": center,
                "zoom": zoom,
                "meta": cache.get("pending_meta"),
            }
            cache["pending_meta"] = None
        cache["center"] = center
        cache["zoom"] = zoom
        cache["height"] = height
        return original_component(**kwargs)

    streamlit_folium._component_func = _component_func
    try:
        return st_folium(
            base_map,
            key=SHARED_MAP_KEY,
            height=height,
            use_container_width=True,
            # Kept identical on every view so the component args stay uniform; views
            # that do not care about clicks simply ignore the returned value.
            returned_objects=["last_object_clicked_tooltip"],
            center=center,
            zoom=zoom,
            feature_group_to_add=serialize_groups,
            layer_control=layer_control if serialize_groups is not None else None,
        )
    finally:
        streamlit_folium._component_func = original_component


@dataclass
class ViewSlots:
    """Pre-created layout containers that each view renders into.

    All views share these containers, created once at a fixed point in the page
    script. That keeps every element - most importantly the shared Leaflet
    component - at the same position in Streamlit's element tree on every rerun,
    which is what prevents the map iframe from being unmounted and reloaded when
    the user switches views.
    """

    controls: Any
    panel: Any
    below: Any


class SharedMapSlot:
    """Feeds each view's overlays into the single persistent Leaflet component."""

    def __init__(self, map_container: Any, burn_severity_years: list[int]) -> None:
        self._container = map_container
        self._years = burn_severity_years
        self.shown = False

    def has_overlay(self, overlay_id: str) -> bool:
        return overlay_id in _map_cache().setdefault("overlays", {})

    def overlay_viewport(
        self, overlay_id: str
    ) -> tuple[tuple[float, float], int] | None:
        overlay = _map_cache().setdefault("overlays", {}).get(overlay_id)
        if overlay is None:
            return None
        return overlay["center"], overlay["zoom"]

    def overlay_meta(self, overlay_id: str) -> dict[str, Any]:
        overlay = _map_cache().setdefault("overlays", {}).get(overlay_id) or {}
        return overlay.get("meta") or {}

    def show(
        self,
        feature_groups: list[folium.FeatureGroup] | None,
        *,
        overlay_id: str,
        center: tuple[float, float],
        zoom: int,
        height: int = 540,
        layer_control_collapsed: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> dict:
        self.shown = True
        cache = _map_cache()
        cache["pending_meta"] = meta
        with self._container:
            return render_shared_map(
                feature_groups or [],
                self._years,
                center=center,
                zoom=zoom,
                height=height,
                layer_control_collapsed=layer_control_collapsed,
                overlay_id=overlay_id,
            )

    def park(self) -> None:
        """Keep the Leaflet iframe mounted without reserializing overlays."""
        cache = _map_cache()
        self.show(
            [],
            overlay_id=_PARKED_OVERLAY_ID,
            center=cache.get("center", OVERVIEW_CENTER),
            zoom=cache.get("zoom", OVERVIEW_ZOOM),
            height=cache.get("height", 540),
            layer_control_collapsed=True,
        )


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


def build_overview_feature_groups(
    source_watersheds: dict,
    service_areas: dict,
    burn_perimeters: dict,
) -> list[folium.FeatureGroup]:
    """Build statewide service, source, and fire overlays for the shared map."""
    source_watersheds = _folium_compatible_feature_collection(source_watersheds)
    service_areas = _folium_compatible_feature_collection(service_areas)
    burn_perimeters = _folium_compatible_feature_collection(burn_perimeters)
    feature_groups: list[folium.FeatureGroup] = []

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
        feature_groups.append(service_group)

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
    feature_groups.append(watershed_group)

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
    feature_groups.append(perimeter_group)

    return feature_groups


def build_utility_case_study_groups(
    source_area_geojson: dict | None,
    service_area_geojson: dict | None,
    utility_sources: list[UtilitySource],
    wildfire_geojson: dict,
) -> tuple[list[folium.FeatureGroup], list[tuple[float, float]]]:
    """Build utility-context overlays plus the points that define their extent."""
    feature_groups: list[folium.FeatureGroup] = []
    points: list[tuple[float, float]] = []

    if service_area_geojson is not None:
        service_group = folium.FeatureGroup(name="Utility service area")
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
            style_function=lambda _: _geojson_style("#2ca02c"),
        ).add_to(service_group)
        feature_groups.append(service_group)
        points += _feature_bounds(service_area_geojson)
    if source_area_geojson is not None:
        source_area_group = folium.FeatureGroup(name="Source water area")
        folium.GeoJson(
            source_area_geojson,
            style_function=lambda _: _geojson_style("#1f77b4"),
        ).add_to(source_area_group)
        feature_groups.append(source_area_group)
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
        feature_groups.append(source_group)

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
        feature_groups.append(wildfire_group)
        points += _feature_bounds(compatible_wildfires)

    return feature_groups, points
