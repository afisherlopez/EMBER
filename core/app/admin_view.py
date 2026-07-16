"""Password-protected admin forms for updating EMBER Parquet fact tables."""

from __future__ import annotations

import hmac
from datetime import date

import streamlit as st

from core.admin_data import (
    AdminWriteResult,
    upsert_pair_summary,
    upsert_raster_asset,
    upsert_scalar_metric,
)
from core.catalog import Catalog
from core.models import MetricDefinition, Utility, Wildfire
from core.settings import settings


def _configured_password() -> str:
    if settings.ember_admin_password:
        return settings.ember_admin_password
    try:
        secret = st.secrets.get("EMBER_ADMIN_PASSWORD", "")
    except Exception:  # noqa: BLE001 - missing Streamlit secrets should just disable admin
        return ""
    return str(secret or "")


def admin_password_is_configured() -> bool:
    """Return whether admin editing has a configured password."""
    return bool(_configured_password())


def admin_password_matches(entered: str) -> bool:
    """Check a submitted admin password without leaking timing information."""
    configured = _configured_password()
    return bool(configured and hmac.compare_digest(entered, configured))


def _utility_options(utilities: list[Utility]) -> dict[str, Utility]:
    return {f"{utility.name} ({utility.state}) - {utility.utility_id}": utility for utility in utilities}


def _wildfire_options(wildfires: list[Wildfire]) -> dict[str, Wildfire]:
    return {
        f"{wildfire.name} ({wildfire.ignition_date or 'unknown'}, {wildfire.state}) - {wildfire.wildfire_id}": wildfire
        for wildfire in wildfires
    }


def _metric_options(
    metrics: dict[str, MetricDefinition], kind: str
) -> dict[str, MetricDefinition]:
    return {
        f"{metric.display_name} - {metric.key}": metric
        for metric in metrics.values()
        if metric.kind == kind
    }


def _show_write_result(result: AdminWriteResult) -> None:
    st.cache_data.clear()
    st.cache_resource.clear()
    st.success(f"Updated `{result.table}`.")
    st.caption(f"Backup: `{result.backup_uri}`")
    st.caption(f"Published table: `{result.table_uri}`")
    st.info("Cached Parquet reads were cleared. Return to the dashboard to reload the updated data.")
    if st.button("Return to dashboard with updated data", key=f"return_after_{result.table}"):
        st.session_state["admin_mode"] = False
        st.rerun()


def _selected_pair(
    utility_labels: dict[str, Utility], wildfire_labels: dict[str, Wildfire]
) -> tuple[Utility, Wildfire]:
    utility_label = st.selectbox("Water utility", list(utility_labels.keys()))
    wildfire_label = st.selectbox("Wildfire", list(wildfire_labels.keys()))
    return utility_labels[utility_label], wildfire_labels[wildfire_label]


def _render_scalar_metric_form(
    utilities: list[Utility],
    wildfires: list[Wildfire],
    metrics: dict[str, MetricDefinition],
) -> None:
    st.subheader("Scalar Metric")
    st.caption("Add or replace a row in `scalar_metrics.parquet`.")
    utility_labels = _utility_options(utilities)
    wildfire_labels = _wildfire_options(wildfires)
    metric_labels = _metric_options(metrics, "scalar")

    with st.form("admin_scalar_metric"):
        utility, wildfire = _selected_pair(utility_labels, wildfire_labels)
        metric_label = st.selectbox("Metric", list(metric_labels.keys()))
        metric = metric_labels[metric_label]
        value = st.number_input("Value", value=0.0, format="%.6f")
        unit = st.text_input("Unit", value=metric.unit or "")
        method = st.text_input("Method", value="manual admin update")
        source_note = st.text_area("Source note", value="")
        as_of_date = st.date_input("As-of date", value=date.today())
        submitted = st.form_submit_button("Save scalar metric")

    if submitted:
        result = upsert_scalar_metric(
            utility_id=utility.utility_id,
            wildfire_id=wildfire.wildfire_id,
            metric_key=metric.key,
            value=float(value),
            unit=unit or None,
            method=method or None,
            source_note=source_note or None,
            as_of_date=as_of_date,
        )
        _show_write_result(result)


def _render_pair_summary_form(utilities: list[Utility], wildfires: list[Wildfire]) -> None:
    st.subheader("Pair Summary")
    st.caption("Add or replace a row in `pair_summary.parquet`.")
    utility_labels = _utility_options(utilities)
    wildfire_labels = _wildfire_options(wildfires)

    with st.form("admin_pair_summary"):
        utility, wildfire = _selected_pair(utility_labels, wildfire_labels)
        has_overlap = st.checkbox("Has overlap", value=True)
        overlap_area_km2 = st.number_input("Overlap area (km²)", value=0.0, format="%.6f")
        overlap_pct_of_source = st.number_input(
            "Overlap percent of source area", value=0.0, min_value=0.0, format="%.6f"
        )
        submitted = st.form_submit_button("Save pair summary")

    if submitted:
        result = upsert_pair_summary(
            utility_id=utility.utility_id,
            wildfire_id=wildfire.wildfire_id,
            has_overlap=has_overlap,
            overlap_area_km2=float(overlap_area_km2) if has_overlap else None,
            overlap_pct_of_source=float(overlap_pct_of_source) if has_overlap else None,
        )
        _show_write_result(result)


def _render_raster_asset_form(
    utilities: list[Utility],
    wildfires: list[Wildfire],
    metrics: dict[str, MetricDefinition],
) -> None:
    st.subheader("Raster Asset")
    st.caption("Add or replace a row in `raster_assets.parquet`.")
    utility_labels = _utility_options(utilities)
    wildfire_labels = _wildfire_options(wildfires)
    metric_labels = _metric_options(metrics, "raster")

    if not metric_labels:
        st.info("No raster metrics are configured.")
        return

    with st.form("admin_raster_asset"):
        utility, wildfire = _selected_pair(utility_labels, wildfire_labels)
        metric_label = st.selectbox("Metric", list(metric_labels.keys()))
        metric = metric_labels[metric_label]
        cog_uri = st.text_input("COG URI")
        units = st.text_input("Units", value=metric.unit or "")
        colormap_name = st.text_input("Colormap", value=metric.default_colormap or "")
        default_rescale = metric.default_rescale or (0.0, 1.0)
        rescale_min = st.number_input("Rescale min", value=float(default_rescale[0]), format="%.6f")
        rescale_max = st.number_input("Rescale max", value=float(default_rescale[1]), format="%.6f")
        nodata = st.number_input("NoData value", value=-9999.0, format="%.6f")
        as_of_date = st.date_input("As-of date", value=date.today(), key="raster_as_of_date")
        submitted = st.form_submit_button("Save raster asset")

    if submitted:
        if not cog_uri:
            st.error("COG URI is required.")
            return
        result = upsert_raster_asset(
            utility_id=utility.utility_id,
            wildfire_id=wildfire.wildfire_id,
            metric_key=metric.key,
            cog_uri=cog_uri,
            units=units or None,
            colormap_name=colormap_name or None,
            rescale_min=float(rescale_min),
            rescale_max=float(rescale_max),
            nodata=float(nodata),
            as_of_date=as_of_date,
        )
        _show_write_result(result)


def render_admin_view(catalog: Catalog, metrics: dict[str, MetricDefinition]) -> None:
    """Render the admin data editor."""
    st.subheader("Admin Data Editor")

    st.warning(
        "These forms rewrite Parquet tables and create a timestamped backup first. "
        "Use ingest scripts for utility/wildfire geometry changes."
    )

    utilities = catalog.list_utilities()
    wildfires = catalog.list_wildfires()
    scalar_tab, pair_tab, raster_tab = st.tabs(["Scalar metrics", "Pair summaries", "Raster assets"])
    with scalar_tab:
        _render_scalar_metric_form(utilities, wildfires, metrics)
    with pair_tab:
        _render_pair_summary_form(utilities, wildfires)
    with raster_tab:
        _render_raster_asset_form(utilities, wildfires, metrics)
