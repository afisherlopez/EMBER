"""Streamlit entrypoint wiring selectors, map, feature panels, and export."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# `streamlit run core/app/streamlit_app.py` puts this file's directory on sys.path rather
# than the project root, which breaks the absolute `core.*` imports below. Add the dashboard
# root (three parents up: app -> core -> dashboard) so the app runs without PYTHONPATH or an
# editable install.
_DASHBOARD_ROOT = Path(__file__).resolve().parents[2]
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

# macOS python.org builds ship without CA certificates wired into OpenSSL, which makes the
# HTTPS calls gcsfs sends to storage.googleapis.com fail with CERTIFICATE_VERIFY_FAILED.
# Point OpenSSL at certifi's bundle when no cert file is already configured. This is a no-op
# on environments (conda, Linux, Docker) that already verify correctly.
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(certifi.where()))
except Exception:  # noqa: BLE001 - certs are best-effort; never block app startup on this
    pass

import streamlit as st

# Inject GCP credentials/config from Streamlit secrets before any core module imports
# `core.settings` (whose pydantic Settings reads env at import time). No-op locally.
from core.gcp_auth import bootstrap_gcp_credentials

bootstrap_gcp_credentials()

from core.app.export import build_export_html, render_export_button
from core.app.features import format_scalar_value, render_feature_panel
from core.app.fire_view import render_fire_view
from core.app.map_view import render_map
from core.app.range_view import render_range_view
from core.app.selector_controls import render_selectors
from core.catalog import Catalog
from core.models import MetricDefinition, MetricValue, RasterAsset, Utility, Wildfire
from core.registry import load_registries
from core.settings import settings
from core.states import resolve_state, state_message
from core.storage import get_storage

st.set_page_config(page_title="EMBER", layout="wide")


@st.cache_resource
def cached_storage():
    """Create storage backend once per Streamlit process."""
    return get_storage()


@st.cache_resource
def cached_catalog() -> Catalog:
    """Create and hold one DuckDB catalog connection per process."""
    return Catalog(cached_storage())


@st.cache_resource
def cached_registries():
    """Load and validate metric/profile registries once per process."""
    config_dir = Path(__file__).resolve().parents[2] / "config"
    return load_registries(config_dir)


@st.cache_data
def cached_pair_data(utility_id: str, wildfire_id: str, metric_key: str, kind: str):
    """Cache pair-scoped query payloads by utility, wildfire, and metric."""
    catalog = cached_catalog()
    pair = catalog.get_pair_summary(utility_id, wildfire_id)
    if kind == "scalar":
        payload = catalog.get_scalar(utility_id, wildfire_id, metric_key)
    else:
        payload = catalog.get_raster_asset(utility_id, wildfire_id, metric_key)
    return pair, payload


@st.cache_data
def cached_geojson(table: str, row_id: str) -> dict:
    """Cache simplified GeoJSON lookups for selected utility/fire rows."""
    return cached_catalog().get_geojson(table=table, row_id=row_id, simplify_tolerance=settings.geojson_simplify_tolerance)


def _index_by_id(utilities: list[Utility], wildfires: list[Wildfire]) -> tuple[dict[str, Utility], dict[str, Wildfire]]:
    return ({item.utility_id: item for item in utilities}, {item.wildfire_id: item for item in wildfires})


def _first_raster_metric(profile_metrics: list[MetricDefinition]) -> MetricDefinition | None:
    for metric in profile_metrics:
        if metric.kind == "raster":
            return metric
    return None


def main() -> None:
    """Render the EMBER dashboard."""
    st.title("EMBER")
    st.caption("Environmental and economic Measurements of Burn Events on water Resources")

    metrics_registry, profiles_registry = cached_registries()
    catalog = cached_catalog()
    utilities = catalog.list_utilities()

    view_mode = st.radio(
        "View",
        options=[
            "Single fire \u00d7 utility",
            "Fires by utility & year range",
            "Utilities by fire",
        ],
        horizontal=True,
    )
    if view_mode == "Fires by utility & year range":
        # The range view queries fires by overlap pair, so it never needs the full
        # 8,920-row wildfire list that the single-fire selector relies on.
        render_range_view(catalog, utilities)
        return

    if view_mode == "Utilities by fire":
        render_fire_view(catalog, catalog.list_wildfires())
        return

    wildfires = catalog.list_wildfires()
    utility_by_id, wildfire_by_id = _index_by_id(utilities, wildfires)

    selector_state = render_selectors(profiles_registry, utilities, wildfires)
    if not selector_state.utility_id or not selector_state.wildfire_id:
        st.info("Select both a water utility and a wildfire to load the dashboard.")
        return

    selected_utility = utility_by_id[selector_state.utility_id]
    selected_wildfire = wildfire_by_id[selector_state.wildfire_id]

    selected_profile = profiles_registry[selector_state.profile_key]
    profile_metrics = [metrics_registry[metric_key] for metric_key in selected_profile.features]

    feature_rows: list[tuple[MetricDefinition, str, str | None]] = []
    metric_state_payload: dict[str, tuple[str, MetricValue | None, RasterAsset | None]] = {}
    for metric in profile_metrics:
        pair_summary, payload = cached_pair_data(
            utility_id=selector_state.utility_id,
            wildfire_id=selector_state.wildfire_id,
            metric_key=metric.key,
            kind=metric.kind,
        )
        state = resolve_state(pair_summary.has_overlap, payload)
        scalar_payload = payload if isinstance(payload, MetricValue) else None
        raster_payload = payload if isinstance(payload, RasterAsset) else None
        metric_state_payload[metric.key] = (state, scalar_payload, raster_payload)
        rendered_value = (
            format_scalar_value(metric, scalar_payload.value)
            if scalar_payload is not None and state == "available"
            else state_message(state)
        )
        feature_rows.append((metric, state, rendered_value))

    raster_metric = _first_raster_metric(profile_metrics)
    raster_state = "pending"
    raster_payload: RasterAsset | None = None
    if raster_metric:
        raster_state, _, raster_payload = metric_state_payload[raster_metric.key]

    utility_geojson = cached_geojson("utilities", selector_state.utility_id)
    wildfire_geojson = cached_geojson("wildfires", selector_state.wildfire_id)

    map_col, panel_col = st.columns([3, 2], gap="large")
    with map_col:
        render_map(
            utility=selected_utility,
            wildfire=selected_wildfire,
            utility_geojson=utility_geojson,
            wildfire_geojson=wildfire_geojson,
            raster_metric=raster_metric,
            raster_asset=raster_payload,
            raster_state=raster_state,  # type: ignore[arg-type]
        )

    with panel_col:
        st.subheader("Profile Features")
        for metric in profile_metrics:
            state, scalar_payload, known_raster_payload = metric_state_payload[metric.key]
            render_feature_panel(metric, state, scalar_payload, known_raster_payload)  # type: ignore[arg-type]

        export_html = build_export_html(
            utility_name=selected_utility.name,
            wildfire_name=selected_wildfire.name,
            profile_label=selected_profile.label,
            feature_rows=feature_rows,  # type: ignore[arg-type]
        )
        render_export_button(export_html, selector_state.utility_id, selector_state.wildfire_id)


if __name__ == "__main__":
    main()
