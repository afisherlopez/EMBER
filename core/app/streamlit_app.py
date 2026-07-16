"""Streamlit entrypoint wiring selectors, map, feature panels, and export."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# `streamlit run core/app/streamlit_app.py` puts this file's directory on sys.path rather
# than the project root, which breaks the absolute `core.*` imports below. Add the project
# root (three parents up: app -> core -> project root) so the app runs without PYTHONPATH or
# an editable install.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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

from core.app.admin_view import admin_password_is_configured, admin_password_matches, render_admin_view
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


DEMO_TOTAL_ECON_IMPACT = 22_146_000.0
DEMO_TOTAL_ECON_IMPACT_HELPER = (
    "This number was calculated using sources of supply (SoS) costs, which is "
    "the amount of money a water utility spends on maintaining their water supply. "
    "To find the economic impact of the Holiday Farm Fire on the Eugene Water & "
    "Electric Board, we took the difference between the baseline SoS from 2019 and "
    "the SoS costs for 2020 - 2025, which were the years following the fire."
)
BREITENBUSH_TOTAL_ECON_IMPACT_HELPER = (
    "This number was calculated using sources of supply (SoS) costs, which is "
    "the amount of money a water utility spends on maintaining their water supply. "
    "For this Breitenbush case study, we compare the baseline SoS costs before "
    "the fire with the SoS costs in the years following the fire."
)


def _is_holiday_farm_eweb_case_study(utility: Utility, wildfire: Wildfire) -> bool:
    """Temporary demo match until the case-study value is published in scalar_metrics."""
    utility_text = f"{utility.name} {utility.source_area_name}".lower()
    wildfire_text = wildfire.name.lower()
    is_eweb = "eweb" in utility_text or ("eugene" in utility_text and "electric" in utility_text)
    return is_eweb and "holiday farm" in wildfire_text


def _is_breitenbush_case_study(utility: Utility, wildfire: Wildfire) -> bool:
    """Match the Breitenbush demo case by selected utility or wildfire text."""
    selected_text = f"{utility.name} {utility.source_area_name} {wildfire.name}".lower()
    return "breitenbush" in selected_text


def _clear_admin_login_query_param() -> None:
    if "admin_login" in st.query_params:
        del st.query_params["admin_login"]


@st.dialog("Admin sign in")
def _render_admin_login_dialog() -> None:
    if not admin_password_is_configured():
        st.warning("Admin editing is disabled. Set `EMBER_ADMIN_PASSWORD` to enable it.")
        if st.button("Close"):
            st.session_state["show_admin_login"] = False
            st.rerun()
        return

    with st.form("admin_login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Open admin editor")

    if submitted:
        if admin_password_matches(password):
            st.session_state["admin_mode"] = True
            st.session_state["show_admin_login"] = False
            _clear_admin_login_query_param()
            st.rerun()
        st.error("Incorrect admin password.")

    if st.button("Cancel"):
        st.session_state["show_admin_login"] = False
        _clear_admin_login_query_param()
        st.rerun()


def _render_admin_launcher(current_view_mode: str) -> None:
    st.markdown(
        """
        <style>
        .ember-admin-launcher {
            position: fixed;
            right: 1rem;
            bottom: 0.75rem;
            z-index: 9999;
            color: #777;
            font-size: 0.8rem;
            text-decoration: underline;
            text-underline-offset: 2px;
            background: rgba(255, 255, 255, 0.85);
            padding: 0.2rem 0.35rem;
            border-radius: 0.25rem;
        }
        .ember-admin-launcher:hover {
            color: #333;
        }
        </style>
        <a class="ember-admin-launcher" href="?admin_login=1" target="_self">Admin</a>
        """,
        unsafe_allow_html=True,
    )

    if st.query_params.get("admin_login") == "1":
        _clear_admin_login_query_param()
        if not st.session_state.get("admin_mode"):
            st.session_state["show_admin_login"] = True
            st.session_state["admin_login_view_mode"] = current_view_mode

    if (
        st.session_state.get("show_admin_login")
        and st.session_state.get("admin_login_view_mode") != current_view_mode
    ):
        st.session_state["show_admin_login"] = False

    if st.session_state.get("show_admin_login") and not st.session_state.get("admin_mode"):
        _render_admin_login_dialog()


def main() -> None:
    """Render the EMBER dashboard."""
    st.title("EMBER")
    st.caption("Environmental and economic Measurements of Burn Events on water Resources")

    metrics_registry, profiles_registry = cached_registries()
    catalog = cached_catalog()
    utilities = catalog.list_utilities()

    if st.session_state.get("admin_mode"):
        if st.button("Exit admin mode"):
            st.session_state["admin_mode"] = False
            st.rerun()
        render_admin_view(catalog, metrics_registry)
        return

    view_mode = st.radio(
        "View",
        options=[
            "Search by Case Study",
            "Search by utility",
            "Search by wildfire",
        ],
        horizontal=True,
    )
    _render_admin_launcher(view_mode)

    if view_mode == "Search by utility":
        # The range view queries fires by overlap pair, so it never needs the full
        # 8,920-row wildfire list that the single-fire selector relies on.
        render_range_view(catalog, utilities)
        return

    if view_mode == "Search by wildfire":
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
    metric_helper_text: dict[str, str] = {}
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
        if metric.key == "total_econ_impact" and _is_holiday_farm_eweb_case_study(
            selected_utility, selected_wildfire
        ):
            scalar_payload = MetricValue(
                utility_id=selector_state.utility_id,
                wildfire_id=selector_state.wildfire_id,
                metric_key=metric.key,
                value=DEMO_TOTAL_ECON_IMPACT,
                unit=metric.unit or "USD",
                method="demo override",
                source_note=None,
                as_of_date=None,
            )
            raster_payload = None
            state = "available"
            metric_helper_text[metric.key] = DEMO_TOTAL_ECON_IMPACT_HELPER
        elif metric.key == "total_econ_impact" and _is_breitenbush_case_study(
            selected_utility, selected_wildfire
        ):
            metric_helper_text[metric.key] = BREITENBUSH_TOTAL_ECON_IMPACT_HELPER
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
            render_feature_panel(
                metric,
                state,
                scalar_payload,
                known_raster_payload,
                helper_text=metric_helper_text.get(metric.key),
            )  # type: ignore[arg-type]

        export_html = build_export_html(
            utility_name=selected_utility.name,
            wildfire_name=selected_wildfire.name,
            profile_label=selected_profile.label,
            feature_rows=feature_rows,  # type: ignore[arg-type]
        )
        render_export_button(export_html, selector_state.utility_id, selector_state.wildfire_id)


if __name__ == "__main__":
    main()
