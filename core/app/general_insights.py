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
    color: str,
) -> None:
    st.subheader(title)
    chart_rows = [
        {
            "Year": year,
            value_field: yearly_totals.get(year, 0.0),
        }
        for year in range(year_bounds[0], year_bounds[1] + 1)
    ]
    st.vega_lite_chart(
        {
            "data": {"values": chart_rows},
            "mark": {
                "type": "line",
                "color": color,
                "point": {"filled": True, "size": 45},
                "strokeWidth": 2.5,
            },
            "encoding": {
                "x": {
                    "field": "Year",
                    "type": "quantitative",
                    "axis": {"format": "d", "title": "Wildfire ignition year"},
                },
                "y": {
                    "field": value_field,
                    "type": "quantitative",
                    "axis": {"title": value_field},
                    "scale": {"zero": True},
                },
                "tooltip": [
                    {"field": "Year", "type": "quantitative", "format": "d"},
                    {
                        "field": value_field,
                        "type": "quantitative",
                        "format": ",.2f",
                    },
                ],
            },
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

    burned_totals = {
        year: area_km2 * ACRES_PER_SQUARE_KILOMETER
        for year, area_km2 in catalog.list_yearly_burned_area()
    }
    _render_area_chart(
        title="Burned area over time",
        value_field="Burned area (acres)",
        yearly_totals=burned_totals,
        year_bounds=year_bounds,
        color="#7f1d1d",
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
            color="#d65f2e",
        )
    else:
        st.info("No source- or service-area wildfire intersections are available.")
    st.caption(
        "Annual totals sum the recorded overlap area for every wildfire–utility "
        "source- or service-area intersection. Point locations do not contribute area."
    )
