"""Dashboard-wide summaries across utilities and wildfires."""

from __future__ import annotations

import streamlit as st

from core.catalog import Catalog

ACRES_PER_SQUARE_KILOMETER = 247.105381467165


def _render_area_chart(
    *,
    title: str,
    value_field: str,
    yearly_totals: dict[int, float],
    year_bounds: tuple[int, int],
) -> None:
    st.subheader(title)
    chart_rows: list[dict[str, float | int | None]] = []
    values: list[float] = []
    for year in range(year_bounds[0], year_bounds[1] + 1):
        value = yearly_totals.get(year, 0.0)
        values.append(value)
        rolling_average = (
            sum(values[-5:]) / 5 if len(values) >= 5 else None
        )
        chart_rows.append(
            {
                "Year": year,
                value_field: value,
                "5-year rolling average (acres)": rolling_average,
            }
        )
    st.vega_lite_chart(
        {
            "data": {"values": chart_rows},
            "layer": [
                {
                    "mark": {
                        "type": "line",
                        "point": {"filled": True, "size": 45},
                        "strokeWidth": 2.5,
                    },
                    "encoding": {
                        "x": {
                            "field": "Year",
                            "type": "quantitative",
                            "axis": {
                                "format": "d",
                                "title": "Wildfire ignition year",
                            },
                        },
                        "y": {
                            "field": value_field,
                            "type": "quantitative",
                            "axis": {"title": value_field},
                            "scale": {"zero": True},
                        },
                        "color": {
                            "datum": "Annual area",
                            "type": "nominal",
                            "scale": {
                                "domain": [
                                    "Annual area",
                                    "5-year rolling average",
                                ],
                                "range": ["#111827", "#dc2626"],
                            },
                            "legend": {
                                "title": None,
                                "orient": "top",
                            },
                        },
                        "tooltip": [
                            {
                                "field": "Year",
                                "type": "quantitative",
                                "format": "d",
                            },
                            {
                                "field": value_field,
                                "type": "quantitative",
                                "format": ",.2f",
                            },
                            {
                                "field": "5-year rolling average (acres)",
                                "type": "quantitative",
                                "format": ",.2f",
                            },
                        ],
                    },
                },
                {
                    "mark": {
                        "type": "line",
                        "strokeWidth": 2.5,
                    },
                    "encoding": {
                        "x": {
                            "field": "Year",
                            "type": "quantitative",
                        },
                        "y": {
                            "field": "5-year rolling average (acres)",
                            "type": "quantitative",
                        },
                        "color": {
                            "datum": "5-year rolling average",
                            "type": "nominal",
                            "scale": {
                                "domain": [
                                    "Annual area",
                                    "5-year rolling average",
                                ],
                                "range": ["#111827", "#dc2626"],
                            },
                            "legend": {
                                "title": None,
                                "orient": "top",
                            },
                        },
                        "tooltip": [
                            {
                                "field": "Year",
                                "type": "quantitative",
                                "format": "d",
                            },
                            {
                                "field": "5-year rolling average (acres)",
                                "type": "quantitative",
                                "format": ",.2f",
                            },
                        ],
                    },
                },
            ],
            "height": 430,
        },
        width="stretch",
    )


def render_general_insights(catalog: Catalog) -> None:
    """Render aggregate wildfire and utility-intersection trends."""
    year_bounds = catalog.wildfire_year_bounds()
    if year_bounds is None:
        st.info("No wildfire data is available.")
        return
    st.caption("Red trendlines show the trailing five-year rolling average.")

    burned_totals = {
        year: area_km2 * ACRES_PER_SQUARE_KILOMETER
        for year, area_km2 in catalog.list_yearly_burned_area()
    }
    _render_area_chart(
        title="Burned area over time",
        value_field="Burned area (acres)",
        yearly_totals=burned_totals,
        year_bounds=year_bounds,
    )
    st.caption(
        "Annual totals include every wildfire in the catalog."
    )

    st.divider()
    intersected_totals = {
        year: area_km2 * ACRES_PER_SQUARE_KILOMETER
        for year, area_km2 in catalog.list_yearly_intersected_area()
    }
    if intersected_totals:
        _render_area_chart(
            title="Intersected area over time",
            value_field="Intersected area (acres)",
            yearly_totals=intersected_totals,
            year_bounds=year_bounds,
        )
    else:
        st.info("No source- or service-area wildfire intersections are available.")
    st.caption(
        "Annual totals sum the recorded overlap area for every wildfire–utility "
        "source- or service-area intersection. Point locations do not contribute area."
    )

    washington_col, oregon_col = st.columns(2, gap="large")
    state_charts = (
        (
            washington_col,
            "Washington intersected area over time",
            "WA",
        ),
        (
            oregon_col,
            "Oregon intersected area over time",
            "OR",
        ),
    )
    for column, title, state in state_charts:
        state_totals = {
            year: area_km2 * ACRES_PER_SQUARE_KILOMETER
            for year, area_km2 in catalog.list_yearly_intersected_area(state)
        }
        with column:
            if state_totals:
                _render_area_chart(
                    title=title,
                    value_field="Intersected area (acres)",
                    yearly_totals=state_totals,
                    year_bounds=year_bounds,
                )
            else:
                st.subheader(title)
                st.info("No recorded intersections are available.")
    st.caption(
        "State charts are grouped by the state of the intersected utility boundary."
    )
