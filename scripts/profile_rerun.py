"""One-off profiler for per-rerun costs in the dashboard. Safe to delete."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.catalog import Catalog  # noqa: E402
from core.storage import get_storage  # noqa: E402


def timed(label: str, fn):
    start = time.perf_counter()
    result = fn()
    print(f"{label}: {time.perf_counter() - start:.3f}s")
    return result


storage = timed("get_storage", get_storage)
catalog = timed("Catalog()", lambda: Catalog(storage))

timed("list_utilities", catalog.list_utilities)
timed("list_wildfires", catalog.list_wildfires)
timed("list_case_studies", catalog.list_case_studies)
timed("wildfire_year_bounds", catalog.wildfire_year_bounds)
timed("list_yearly_burned_area", catalog.list_yearly_burned_area)
timed("list_yearly_intersected_area(all)", catalog.list_yearly_intersected_area)
timed("list_yearly_intersected_area(WA)", lambda: catalog.list_yearly_intersected_area("WA"))
timed("list_yearly_intersected_area(OR)", lambda: catalog.list_yearly_intersected_area("OR"))

watersheds = timed("get_overview_geojson(utilities)", lambda: catalog.get_overview_geojson("utilities"))
services = timed("get_overview_geojson(service_areas)", lambda: catalog.get_overview_geojson("service_areas"))
fires = timed("get_overview_geojson(wildfires)", lambda: catalog.get_overview_geojson("wildfires"))
for name, fc in (("watersheds", watersheds), ("services", services), ("fires", fires)):
    print(
        f"  {name}: {len(fc['features'])} features, "
        f"{len(json.dumps(fc)) / 1e6:.1f} MB as JSON"
    )

import streamlit_folium  # noqa: E402

from core.app import map_view  # noqa: E402

groups = timed(
    "build_overview_feature_groups",
    lambda: map_view.build_overview_feature_groups(watersheds, services, fires),
)

captured: list[dict] = []
streamlit_folium._component_func = lambda **kwargs: captured.append(kwargs) or {}

timed(
    "st_folium serialization (render_shared_map)",
    lambda: map_view.render_shared_map(
        groups,
        [2020, 2021, 2022, 2023, 2024],
        center=map_view.OVERVIEW_CENTER,
        zoom=map_view.OVERVIEW_ZOOM,
        height=540,
        layer_control_collapsed=False,
    ),
)
if captured:
    fg = captured[0].get("feature_group") or ""
    print(f"  feature_group arg size: {len(fg) / 1e6:.1f} MB")

groups2 = map_view.build_overview_feature_groups(watersheds, services, fires)
timed(
    "st_folium serialization (2nd call)",
    lambda: map_view.render_shared_map(
        groups2,
        [2020, 2021, 2022, 2023, 2024],
        center=map_view.OVERVIEW_CENTER,
        zoom=map_view.OVERVIEW_ZOOM,
        height=540,
        layer_control_collapsed=False,
    ),
)
