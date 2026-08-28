"""Password-protected admin forms for updating EMBER Parquet fact tables."""

from __future__ import annotations

import hmac
from datetime import date

import streamlit as st

from core.admin_data import (
    AdminWriteResult,
    replace_case_study_costs,
    upsert_case_study_point_utility,
    upsert_pair_summary,
    upsert_scalar_metric,
    upsert_utility_scalar_metric,
)
from core.case_study_costs import CaseStudyCSVError, parse_case_study_csv
from core.catalog import Catalog
from core.models import CaseStudy, MetricDefinition, Utility, Wildfire
from core.settings import settings

# Postal abbreviations for the 50 states plus DC; searchable in the admin selectbox.
US_STATE_ABBREVIATIONS = (
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DC",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
)


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


def _exit_admin_mode() -> None:
    st.session_state["admin_mode"] = False
    st.session_state.pop("admin_last_write", None)
    st.session_state.pop("admin_last_write_detail", None)


def _show_write_result(result: AdminWriteResult, detail: str | None = None) -> None:
    """Clear cached reads, then rerun so the return button is not tied to the form submit."""
    st.cache_data.clear()
    st.cache_resource.clear()
    st.session_state["admin_last_write"] = result
    if detail:
        st.session_state["admin_last_write_detail"] = detail
    else:
        st.session_state.pop("admin_last_write_detail", None)
    st.rerun()


def _render_pending_write_result() -> None:
    result = st.session_state.get("admin_last_write")
    if result is None:
        return
    detail = st.session_state.get("admin_last_write_detail")
    if detail:
        st.success(detail)
    st.success(f"Updated `{result.table}`.")
    if result.backup_uri:
        st.caption(f"Backup: `{result.backup_uri}`")
    else:
        st.caption("Created a new table; there was no previous version to back up.")
    st.caption(f"Published table: `{result.table_uri}`")
    st.info("Cached Parquet reads were cleared. Return to the dashboard to reload the updated data.")
    if st.button("Return to dashboard with updated data", key="return_after_admin_write"):
        _exit_admin_mode()
        st.rerun()
    st.divider()


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
    metric_label = st.selectbox(
        "Metric",
        list(metric_labels.keys()),
        key="admin_scalar_metric_selection",
    )
    metric = metric_labels[metric_label]
    is_utility_metric = metric.scope == "utility"

    utility_label = st.selectbox(
        "Water utility",
        list(utility_labels.keys()),
        key="admin_scalar_utility",
    )
    utility = utility_labels[utility_label]
    wildfire = None
    if is_utility_metric:
        st.caption("This metric applies to the selected utility as a whole.")
    else:
        wildfire_label = st.selectbox(
            "Wildfire",
            list(wildfire_labels.keys()),
            key="admin_scalar_wildfire",
        )
        wildfire = wildfire_labels[wildfire_label]

    with st.form("admin_scalar_metric"):
        value = st.number_input("Value", value=0.0, format="%.6f")
        unit = st.text_input("Unit", value=metric.unit or "")
        method = st.text_input("Method", value="manual admin update")
        source_note = st.text_area("Source note", value="")
        as_of_date = st.date_input("As-of date", value=date.today())
        submitted = st.form_submit_button("Save scalar metric")

    if submitted:
        if is_utility_metric:
            result = upsert_utility_scalar_metric(
                utility_id=utility.utility_id,
                metric_key=metric.key,
                value=float(value),
                unit=unit or None,
                method=method or None,
                source_note=source_note or None,
                as_of_date=as_of_date,
            )
        else:
            assert wildfire is not None
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


def _render_case_study_cost_form(
    utilities: list[Utility],
    case_studies: list[CaseStudy],
) -> None:
    st.subheader("Case Study Cost Data")
    st.caption(
        "Upload a CSV to replace the raw economic-impact rows for one utility. "
        "Source PDFs must be stored in `case_studies/` with names matching the Source column."
    )
    st.caption(
        "All uploaded columns are preserved. EMBER only needs a recognizable year "
        "column and cost/value column for the economic-impact chart. In `Years "
        "Incurred`, enter multiple years as a quoted, comma-separated list such as "
        "`\"2021, 2022, 2023\"`."
    )
    utility_labels = _utility_options(utilities)
    existing_utility_ids = {case_study.utility_id for case_study in case_studies}
    location_mode = st.radio(
        "Utility location",
        (
            "Choose from the utilities listed in EMBER",
            "Enter coordinates (utility not on file)",
        ),
        key="admin_case_study_location_mode",
        help=(
            "If the case-study utility is not in the dropdown, enter a name and "
            "map coordinates. EMBER will place a point at that location."
        ),
    )
    using_coordinates = location_mode.startswith("Enter coordinates")

    custom_name = ""
    custom_state = None
    latitude = None
    longitude = None
    utility = None
    if using_coordinates:
        st.caption("This utility is not in the EMBER catalog. Enter a map point instead.")
        custom_name = st.text_input("Utility name", key="admin_case_study_custom_name")
        custom_state = st.selectbox(
            "State",
            US_STATE_ABBREVIATIONS,
            index=None,
            placeholder="Select a state",
            key="admin_case_study_custom_state",
        )
        latitude = st.number_input(
            "Latitude",
            min_value=-90.0,
            max_value=90.0,
            value=None,
            placeholder="e.g. 44.0521",
            format="%.6f",
            key="admin_case_study_latitude",
        )
        longitude = st.number_input(
            "Longitude",
            min_value=-180.0,
            max_value=180.0,
            value=None,
            placeholder="e.g. -123.0868",
            format="%.6f",
            key="admin_case_study_longitude",
        )
    else:
        utility_label = st.selectbox(
            "Water utility",
            list(utility_labels),
            key="admin_case_study_utility",
        )
        utility = utility_labels[utility_label]
        if utility.utility_id in existing_utility_ids:
            st.write(f"Replacing all existing case-study rows for **{utility.name}**.")
        else:
            st.write(f"Creating a case study for **{utility.name}**.")

    with st.form("admin_case_study_costs"):
        uploaded_file = st.file_uploader("Case-study CSV", type=["csv"])
        submitted = st.form_submit_button("Replace case-study cost data")

    if not submitted:
        return
    if uploaded_file is None:
        st.error("Choose a CSV file to upload.")
        return
    try:
        rows = parse_case_study_csv(uploaded_file.getvalue())
    except CaseStudyCSVError as exc:
        st.error(str(exc))
        return

    if using_coordinates:
        if not custom_name.strip():
            st.error("Enter a utility name for the map point.")
            return
        if not custom_state:
            st.error("Select a state.")
            return
        if latitude is None or longitude is None:
            st.error("Enter both latitude and longitude.")
            return
        try:
            utility_id, _location_result = upsert_case_study_point_utility(
                name=custom_name.strip(),
                state=custom_state,
                latitude=float(latitude),
                longitude=float(longitude),
            )
        except ValueError as exc:
            st.error(str(exc))
            return
        st.caption(
            f"Mapped `{utility_id}` to a point at {float(latitude):.5f}, "
            f"{float(longitude):.5f}."
        )
        result = replace_case_study_costs(utility_id=utility_id, rows=rows)
        _show_write_result(
            result,
            detail=(
                f"Saved {len(rows)} uploaded row(s) for `{utility_id}` and placed a "
                "point on the case-study map."
            ),
        )
        return

    utility_id = utility.utility_id
    result = replace_case_study_costs(
        utility_id=utility_id,
        rows=rows,
    )
    _show_write_result(
        result,
        detail=(
            f"Replaced the existing data with {len(rows)} uploaded row(s) for "
            f"`{utility_id}`."
        ),
    )


def render_admin_view(catalog: Catalog, metrics: dict[str, MetricDefinition]) -> None:
    """Render the admin data editor."""
    st.subheader("Admin Data Editor")
    _render_pending_write_result()

    st.warning(
        "These forms rewrite Parquet tables and create a timestamped backup first. "
        "Use ingest scripts for utility/wildfire geometry changes."
    )

    utilities = catalog.list_utilities()
    wildfires = catalog.list_wildfires()
    case_studies = catalog.list_case_studies()
    scalar_tab, costs_tab, pair_tab = st.tabs(
        ["Scalar metrics", "Case study costs", "Pair summaries"]
    )
    with scalar_tab:
        _render_scalar_metric_form(utilities, wildfires, metrics)
    with costs_tab:
        _render_case_study_cost_form(utilities, case_studies)
    with pair_tab:
        _render_pair_summary_form(utilities, wildfires)
