"""HTML export generation and download UI for selected EMBER view."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.models import MetricDefinition
from core.states import DataState, state_message


def _template_path() -> Path:
    return Path(__file__).resolve().parent / "ember_summary_draft.html"


def _feature_line(metric: MetricDefinition, state: DataState, rendered_value: str | None) -> str:
    if state != "available":
        return f"<li><strong>{metric.display_name}:</strong> {state_message(state)}</li>"
    return f"<li><strong>{metric.display_name}:</strong> {rendered_value or ''}</li>"


def build_export_html(
    utility_name: str,
    wildfire_name: str,
    profile_label: str,
    feature_rows: list[tuple[MetricDefinition, DataState, str | None]],
) -> str:
    """Fill the export template with selection metadata and panel outputs."""
    template = _template_path().read_text(encoding="utf-8")
    features_html = "\n".join(_feature_line(metric, state, rendered) for metric, state, rendered in feature_rows)
    return (
        template.replace("{{UTILITY_NAME}}", utility_name)
        .replace("{{WILDFIRE_NAME}}", wildfire_name)
        .replace("{{PROFILE_LABEL}}", profile_label)
        .replace("{{FEATURE_ITEMS}}", features_html)
    )


def render_export_button(html_payload: str, utility_id: str, wildfire_id: str) -> None:
    """Render HTML download button for current selection."""
    filename = f"ember-summary-{utility_id}-{wildfire_id}.html"
    st.download_button(
        label="Download HTML summary",
        data=html_payload,
        file_name=filename,
        mime="text/html",
    )
