"""Tests for the in-map multi-year burn-severity control and mosaic ordering."""

from __future__ import annotations

import re

import folium
import streamlit_folium

from core.app.map_view import (
    BurnSeverityControl,
    add_burn_severity_layer,
    render_shared_map,
)


def test_leaflet_control_contains_all_years_and_multi_select_actions() -> None:
    fmap = folium.Map()
    layer = folium.TileLayer(
        tiles="http://localhost/tiles/{z}/{x}/{y}.png",
        attr="test",
    ).add_to(fmap)
    BurnSeverityControl([2022, 2023, 2024], layer.get_name()).add_to(fmap)

    html = fmap.get_root().render()

    assert "ember-severity-summary" in html
    assert "Most recent selected burn wins" in html
    assert "ember-severity-all" in html
    assert "[2024, 2023, 2022]" in html
    assert f"var layer = {layer.get_name()};" in html
    assert f"var map = {fmap.get_name()};" in html


def test_focused_fire_layer_loads_only_its_ignition_year() -> None:
    fmap = folium.Map()

    add_burn_severity_layer(
        fmap,
        [2020],
        show=False,
        year_control=False,
    )
    folium.LayerControl().add_to(fmap)
    html = fmap.get_root().render()

    assert "Burn severity (2020)" in html
    assert "?years=2020" in html
    assert "ember-severity-control" not in html
    assert "High" in html
    assert "Non-processing area" in html
    assert '"opacity": 0.9' in html
    assert "style.zIndex = 450" in html
    assert f"var map = {fmap.get_name()};" in html
    severity_layer = re.search(
        r"var (tile_layer_[a-z0-9]+) = L\.tileLayer\(\s*"
        r'"http://localhost:8000/burn-severity',
        html,
    )
    assert severity_layer is not None
    assert f"{severity_layer.group(1)}.addTo(" not in html


def test_shared_map_component_key_stays_stable_when_overlays_change(
    monkeypatch,
) -> None:
    component_calls: list[dict] = []

    def capture_component(**kwargs):
        component_calls.append(kwargs)
        return {}

    monkeypatch.setattr(streamlit_folium, "_component_func", capture_component)

    first_group = folium.FeatureGroup(name="First view")
    folium.Marker([44.0, -121.0]).add_to(first_group)
    second_group = folium.FeatureGroup(name="Second view")
    folium.Marker([39.0, -105.0]).add_to(second_group)

    render_shared_map(
        [first_group],
        [2023, 2024],
        center=(44.0, -121.0),
        zoom=7,
        height=540,
        layer_control_collapsed=False,
    )
    render_shared_map(
        [second_group],
        [2023, 2024],
        center=(39.0, -105.0),
        zoom=9,
        height=560,
        layer_control_collapsed=True,
    )

    assert len(component_calls) == 2
    assert component_calls[0]["key"] == component_calls[1]["key"]
    assert component_calls[0]["feature_group"] != component_calls[1]["feature_group"]


def test_parked_map_does_not_serialize_overlays(monkeypatch) -> None:
    from core.app import map_view

    map_view._FALLBACK_MAP_CACHE.clear()
    map_view._FALLBACK_MAP_CACHE["overlays"] = {}

    component_calls: list[dict] = []
    monkeypatch.setattr(
        streamlit_folium, "_component_func", lambda **kwargs: component_calls.append(kwargs) or {}
    )
    serialize_calls: list[int] = []
    original = streamlit_folium._get_feature_group_string

    def counting(feature_group, map, idx: int = 0):
        serialize_calls.append(idx)
        return original(feature_group, map, idx)

    monkeypatch.setattr(streamlit_folium, "_get_feature_group_string", counting)

    group = folium.FeatureGroup(name="Parked")
    folium.Marker([44.0, -121.0]).add_to(group)
    render_shared_map(
        [group],
        [2024],
        center=(44.0, -121.0),
        zoom=7,
        height=540,
        layer_control_collapsed=True,
        overlay_id="__parked__",
    )

    assert serialize_calls == []
    assert component_calls[0]["feature_group"] is None


def test_cached_overlay_skips_folium_serialization_on_reuse(monkeypatch) -> None:
    from core.app import map_view

    map_view._FALLBACK_MAP_CACHE.clear()
    map_view._FALLBACK_MAP_CACHE["overlays"] = {}

    component_calls: list[dict] = []
    monkeypatch.setattr(
        streamlit_folium, "_component_func", lambda **kwargs: component_calls.append(kwargs) or {}
    )
    serialize_calls: list[int] = []
    original = streamlit_folium._get_feature_group_string

    def counting(feature_group, map, idx: int = 0):
        serialize_calls.append(idx)
        return original(feature_group, map, idx)

    monkeypatch.setattr(streamlit_folium, "_get_feature_group_string", counting)

    group = folium.FeatureGroup(name="Overview")
    folium.Marker([44.0, -121.0]).add_to(group)
    kwargs = dict(
        center=(44.0, -121.0),
        zoom=5,
        height=540,
        layer_control_collapsed=False,
        overlay_id="overview",
    )
    render_shared_map([group], [2024], **kwargs)
    assert serialize_calls == [0]
    first_js = component_calls[0]["feature_group"]

    serialize_calls.clear()
    render_shared_map([group], [2024], **kwargs)
    assert serialize_calls == []
    assert component_calls[1]["feature_group"] == first_js
