"""Feature panel rendering for scalar and raster metrics across data states."""

from __future__ import annotations

from html import escape

import streamlit as st

from core.models import MetricDefinition, MetricValue, RasterAsset
from core.states import DataState, state_message


def format_scalar_value(metric: MetricDefinition, value: float | None) -> str:
    """Format scalar value using metric registry format string."""
    if value is None:
        return "Data not yet available"
    if metric.value_format:
        return metric.value_format.format(value)
    return str(value)


def render_feature_panel(
    metric: MetricDefinition,
    state: DataState,
    scalar_payload: MetricValue | None,
    raster_payload: RasterAsset | None,
    helper_text: str | None = None,
) -> None:
    """Render one metric feature card according to its data state and kind."""
    with st.container(border=True):
        st.subheader(metric.display_name)
        if state != "available":
            message = state_message(state)
            if message:
                st.write(message)
            return

        if metric.kind == "scalar":
            assert scalar_payload is not None
            st.metric(label=metric.unit or "Value", value=format_scalar_value(metric, scalar_payload.value))
            if helper_text:
                st.markdown(
                    (
                        "<style>"
                        ".metric-helper-tooltip {"
                        "position: relative;"
                        "display: inline-block;"
                        "color: #777;"
                        "font-size: 0.85rem;"
                        "border-bottom: 1px dotted #aaa;"
                        "text-decoration: underline;"
                        "text-underline-offset: 2px;"
                        "cursor: help;"
                        "}"
                        ".metric-helper-tooltip .metric-helper-tooltip-text {"
                        "visibility: hidden;"
                        "opacity: 0;"
                        "position: absolute;"
                        "z-index: 9999;"
                        "top: 1.6rem;"
                        "left: 0;"
                        "width: 320px;"
                        "padding: 0.75rem;"
                        "border-radius: 0.5rem;"
                        "background: #f7f7f7;"
                        "border: 1px solid #d0d0d0;"
                        "box-shadow: 0 4px 14px rgba(0, 0, 0, 0.16);"
                        "color: #333;"
                        "font-size: 0.85rem;"
                        "line-height: 1.35;"
                        "transition: opacity 120ms ease-in-out;"
                        "}"
                        ".metric-helper-tooltip:hover .metric-helper-tooltip-text {"
                        "visibility: visible;"
                        "opacity: 1;"
                        "}"
                        "</style>"
                        '<span class="metric-helper-tooltip">'
                        "How did we calculate this number?"
                        f'<span class="metric-helper-tooltip-text">{escape(helper_text)}</span>'
                        "</span>"
                    ),
                    unsafe_allow_html=True,
                )
            if scalar_payload.as_of_date:
                st.caption(f"As of {scalar_payload.as_of_date.isoformat()}")
            return

        assert raster_payload is not None
        units = raster_payload.units or metric.unit or ""
        st.write(f"Raster layer available ({units})")
        if raster_payload.as_of_date:
            st.caption(f"As of {raster_payload.as_of_date.isoformat()}")
