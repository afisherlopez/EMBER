"""Tests for the in-map multi-year burn-severity control and mosaic ordering."""

from __future__ import annotations

import re

import folium

from core.app.map_view import BurnSeverityControl, add_burn_severity_layer


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
