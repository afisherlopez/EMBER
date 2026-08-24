"""Streamlit entrypoint wiring selectors, map, feature panels, and export."""

from __future__ import annotations

import json
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
from PIL import Image

# Inject GCP credentials/config from Streamlit secrets before any core module imports
# `core.settings` (whose pydantic Settings reads env at import time). No-op locally.
from core.gcp_auth import bootstrap_gcp_credentials

bootstrap_gcp_credentials()

from core.app.admin_view import admin_password_is_configured, admin_password_matches, render_admin_view
from core.app.case_study_view import render_case_study_view
from core.app.economic_impact import render_economic_impact_data
from core.app.fire_view import render_fire_view
from core.app.general_insights import render_general_insights
from core.app.map_view import render_overview_map, render_utility_case_study_map
from core.app.selector_controls import render_case_study_selector
from core.burn_severity import load_burn_severity_assets
from core.catalog import Catalog
from core.registry import load_metric_registry
from core.storage import get_storage

st.set_page_config(page_title="EMBER", layout="wide")

# Increment when the cached Catalog interface changes so Streamlit does not reuse
# an instance created from an older hot-reloaded class definition.
CATALOG_CACHE_VERSION = 2


@st.cache_resource
def cached_storage():
    """Create storage backend once per Streamlit process."""
    return get_storage()


@st.cache_resource
def cached_catalog(cache_version: int) -> Catalog:
    """Create and hold one DuckDB catalog connection per process."""
    del cache_version
    return Catalog(cached_storage())


@st.cache_resource
def cached_metric_registry():
    """Load and validate scalar metric definitions once per process."""
    config_dir = Path(__file__).resolve().parents[2] / "config"
    return load_metric_registry(config_dir / "metrics.yaml")


@st.cache_data
def cached_case_study_costs(utility_id: str):
    """Cache utility-scoped economic-impact source rows."""
    return cached_catalog(CATALOG_CACHE_VERSION).list_case_study_costs(utility_id)


@st.cache_data
def cached_utility_geojson(utility_id: str, area_type: str) -> dict | None:
    """Cache optional source-water or service-area geometry."""
    return cached_catalog(CATALOG_CACHE_VERSION).get_utility_geojson(
        utility_id, area_type
    )


@st.cache_data
def cached_utility_sources(utility_id: str):
    """Cache direct and upstream source connections for a utility."""
    return cached_catalog(CATALOG_CACHE_VERSION).list_utility_sources(utility_id)


@st.cache_data
def cached_case_study_wildfires(utility_id: str) -> dict:
    """Cache the same spatially intersecting wildfires used by Utility View."""
    catalog = cached_catalog(CATALOG_CACHE_VERSION)
    bounds = catalog.wildfire_year_bounds()
    if bounds is None:
        return {"type": "FeatureCollection", "features": []}
    start_year = max(bounds[0], bounds[1] - 25)
    fires = catalog.list_intersecting_wildfires(utility_id, start_year, bounds[1])
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(fire.geometry_geojson),
                "properties": {
                    "wildfire_id": fire.wildfire_id,
                    "name": fire.name,
                    "year": fire.ignition_year,
                    "acres": fire.acres,
                },
            }
            for fire in fires[:400]
        ],
    }


@st.cache_data
def cached_utility_metric(utility_id: str, metric_key: str):
    """Cache a utility-scoped metric with legacy pair fallback."""
    return cached_catalog(CATALOG_CACHE_VERSION).get_utility_scalar(
        utility_id, metric_key
    )


@st.cache_data
def cached_overview_geojson_v2(table: str) -> dict:
    """Cache all geometries used by the shared map layers."""
    return cached_catalog(CATALOG_CACHE_VERSION).get_overview_geojson(table)


@st.cache_data(ttl=300)
def cached_burn_severity_assets() -> dict[int, str]:
    """Load annual burn-severity COGs, refreshing periodically after publication."""
    return load_burn_severity_assets(cached_storage())


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
    if not admin_password_is_configured():
        _clear_admin_login_query_param()
        st.session_state["show_admin_login"] = False
        return

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


def _render_data_sources(*, compact: bool = False) -> None:
    """Render the user-editable data-source copy at the bottom of each view."""
    source_path = _PROJECT_ROOT / "config" / "data_sources.md"
    if not compact:
        st.divider()
    st.markdown(
        '<h2 style="font-size: 1rem; margin: 0 0 0.5rem 0;">DATA SOURCES</h2>',
        unsafe_allow_html=True,
    )
    st.markdown(source_path.read_text(encoding="utf-8"))


def main() -> None:
    """Render the EMBER dashboard."""
    with Image.open(_PROJECT_ROOT / "EMBER_logo.png") as logo_source:
        logo = logo_source.convert("RGBA")
    visible_pixels = logo.getchannel("A").point(
        lambda alpha: 255 if alpha >= 8 else 0
    )
    visible_bounds = visible_pixels.getbbox()
    if visible_bounds is not None:
        logo = logo.crop(visible_bounds)
    st.image(logo, width=320)
    st.markdown(
        """
        <style>
        div[data-testid="stCustomComponentV1"]:focus,
        div[data-testid="stCustomComponentV1"]:focus-within,
        div[data-testid="stCustomComponentV1"] iframe:focus,
        div[data-testid="stCustomComponentV1"] iframe:focus-visible {
            outline: none !important;
            box-shadow: none !important;
        }
        .st-key-view_tabs [role="radiogroup"] {
            display: flex;
            gap: 0;
            border-bottom: 1px solid #d9d9d9;
        }
        .st-key-view_tabs [role="radiogroup"] label {
            margin: 0;
            padding: 0.65rem 1.25rem;
            border-bottom: 3px solid transparent;
            border-radius: 0;
            cursor: pointer;
        }
        .st-key-view_tabs [role="radiogroup"] label:hover {
            background: rgba(31, 119, 180, 0.06);
        }
        .st-key-view_tabs [role="radiogroup"] label:has(input:checked) {
            color: #0b4f8a;
            border-bottom-color: #0b4f8a;
            font-weight: 600;
        }
        .st-key-view_tabs [role="radiogroup"] label > div:first-child {
            display: none;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    metrics_registry = cached_metric_registry()
    catalog = cached_catalog(CATALOG_CACHE_VERSION)
    utilities = catalog.list_utilities()

    if st.session_state.get("admin_mode"):
        if st.button("Exit admin mode"):
            st.session_state["admin_mode"] = False
            st.rerun()
        render_admin_view(catalog, metrics_registry)
        return

    with st.container(key="view_tabs"):
        view_mode = st.radio(
            "View",
            options=[
                "Search by Case Study",
                "Search by utility",
                "Search by wildfire",
                "General Insights",
            ],
            horizontal=True,
            label_visibility="collapsed",
        )
    _render_admin_launcher(view_mode)

    def render_overview() -> None:
        st.subheader("Map layers")
        severity_assets = cached_burn_severity_assets()
        render_overview_map(
            cached_overview_geojson_v2("utilities"),
            cached_overview_geojson_v2("service_areas"),
            cached_overview_geojson_v2("wildfires"),
            sorted(severity_assets, reverse=True),
            map_key=f"overview_map_{view_mode}",
        )

    if view_mode == "General Insights":
        render_general_insights(catalog)
        _render_data_sources()
        return

    if view_mode == "Search by utility":
        # This view queries fires by overlap pair, so it never needs the full
        # 8,920-row wildfire list that the single-fire selector relies on.
        render_case_study_view(
            catalog,
            utilities,
            render_overview,
            set(cached_burn_severity_assets()),
        )
        _render_data_sources()
        return

    if view_mode == "Search by wildfire":
        render_fire_view(
            catalog,
            catalog.list_wildfires(),
            render_overview,
            set(cached_burn_severity_assets()),
        )
        _render_data_sources()
        return

    available_case_studies = catalog.list_case_studies()
    utility_id = render_case_study_selector(available_case_studies)
    if not utility_id:
        st.info("Choose an uploaded utility case study.")
        render_overview()
        _render_data_sources(compact=True)
        return

    selected_utility = next(
        utility for utility in utilities if utility.utility_id == utility_id
    )
    st.markdown(f"## {selected_utility.name} ({selected_utility.state})")
    case_study_costs = cached_case_study_costs(utility_id)
    map_col, metric_col = st.columns([3, 2], gap="large")
    with map_col:
        st.subheader("Utility and wildfire map")
        render_utility_case_study_map(
            selected_utility,
            cached_utility_geojson(utility_id, "source"),
            cached_utility_geojson(utility_id, "service"),
            cached_utility_sources(utility_id),
            cached_case_study_wildfires(utility_id),
        )
    with metric_col:
        first_year = min(row.start_year for row in case_study_costs)
        last_year = max(row.end_year for row in case_study_costs)
        total_impact = cached_utility_metric(utility_id, "total_econ_impact")
        total_impact_value = total_impact.value if total_impact is not None else None
        utility_text = (
            f"{selected_utility.name} {selected_utility.source_area_name}".lower()
        )
        if total_impact_value is None and (
            "eweb" in utility_text
            or ("eugene" in utility_text and "electric" in utility_text)
        ):
            total_impact_value = 22_146_000.0
        st.metric(
            (
                "Total economic impact from wildfires "
                f"(data range {first_year}-{last_year})"
            ),
            (
                f"${total_impact_value:,.0f}"
                if total_impact_value is not None
                else "Data not yet available"
            ),
        )

        pre_fire_revenue = cached_utility_metric(
            utility_id,
            "pre_fire_annual_operating_revenue",
        )
        st.metric(
            "Pre-Fire Annual Operating Revenue",
            (
                f"${pre_fire_revenue.value:,.0f}"
                if pre_fire_revenue is not None
                and pre_fire_revenue.value is not None
                else "Data not yet available"
            ),
        )
    render_economic_impact_data(
        case_study_costs,
        cached_storage(),
        utility_id,
    )
    _render_data_sources(compact=True)


if __name__ == "__main__":
    main()
