"""Feature panel rendering for scalar and raster metrics across data states."""

from __future__ import annotations

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
    metric: MetricDefinition, state: DataState, scalar_payload: MetricValue | None, raster_payload: RasterAsset | None
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
            if scalar_payload.as_of_date:
                st.caption(f"As of {scalar_payload.as_of_date.isoformat()}")
            if scalar_payload.source_note:
                st.caption(scalar_payload.source_note)
            return

        assert raster_payload is not None
        units = raster_payload.units or metric.unit or ""
        st.write(f"Raster layer available ({units})")
        if raster_payload.as_of_date:
            st.caption(f"As of {raster_payload.as_of_date.isoformat()}")
