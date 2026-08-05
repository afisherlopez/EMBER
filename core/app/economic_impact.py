"""Case-study cost chart, linked raw-data table, and CSV download."""

from __future__ import annotations

from html import escape

import streamlit as st

from core.case_study_costs import (
    case_study_costs_to_csv,
    source_pdf_key,
    yearly_cost_breakdown,
)
from core.models import CaseStudyCost
from core.storage import Storage


def _render_cost_chart(rows: list[CaseStudyCost]) -> None:
    chart_rows = yearly_cost_breakdown(rows)
    if not chart_rows:
        st.info("This dataset has no economic-impact rows to chart.")
        return

    st.vega_lite_chart(
        {
            "data": {"values": chart_rows},
            "height": 360,
            "mark": {"type": "bar"},
            "encoding": {
                "x": {
                    "field": "Year",
                    "type": "ordinal",
                    "title": "Year",
                    "sort": "ascending",
                },
                "y": {
                    "field": "Amount",
                    "type": "quantitative",
                    "title": "Inflation-adjusted cost (USD)",
                    "stack": "zero",
                    "axis": {"format": "$~s"},
                },
                "color": {
                    "field": "Category",
                    "type": "nominal",
                    "title": "Description",
                    "scale": {"scheme": "tableau10"},
                    "legend": {"orient": "bottom", "columns": 2},
                },
                "order": {"field": "Category", "sort": "ascending"},
                "tooltip": [
                    {"field": "Year", "type": "ordinal", "title": "Year"},
                    {
                        "field": "Total",
                        "type": "quantitative",
                        "title": "Year total",
                        "format": "$,.2f",
                    },
                    {
                        "field": "Category",
                        "type": "nominal",
                        "title": "Category",
                    },
                    {
                        "field": "Amount",
                        "type": "quantitative",
                        "title": "Category amount",
                        "format": "$,.2f",
                    },
                    {
                        "field": "Breakdown",
                        "type": "nominal",
                        "title": "Full breakdown",
                    },
                ],
            },
        },
        width="stretch",
    )


def _render_linked_table(rows: list[CaseStudyCost], storage: Storage) -> None:
    headers = [
        "Item Type",
        "Start Year",
        "End Year",
        "Description",
        "Raw Cost",
        "Inflation-Adjusted Cost",
        "Contributing Fire(s)",
        "Source",
        "Method",
        "Description and Notes",
    ]
    body_rows = []
    for row in rows:
        source_url = storage.public_url_for(source_pdf_key(row.source))
        values = [
            escape(row.item_type),
            str(row.start_year),
            str(row.end_year),
            escape(row.description),
            f"${row.raw_cost:,.2f}",
            f"${row.inflation_adjusted_cost:,.2f}",
            escape(row.contributing_fires),
            (
                f'<a href="{escape(source_url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{escape(row.source)}</a>'
            ),
            escape(row.method),
            escape(row.description_and_notes),
        ]
        body_rows.append(
            "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
        )

    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    st.markdown(
        f"""
        <div class="ember-cost-table">
          <table>
            <thead><tr>{header_html}</tr></thead>
            <tbody>{''.join(body_rows)}</tbody>
          </table>
        </div>
        <style>
          .ember-cost-table {{ overflow-x: auto; width: 100%; }}
          .ember-cost-table table {{
            border-collapse: collapse;
            font-size: 0.85rem;
            width: max-content;
            min-width: 100%;
          }}
          .ember-cost-table th, .ember-cost-table td {{
            border: 1px solid rgba(128, 128, 128, 0.35);
            padding: 0.45rem 0.6rem;
            text-align: left;
            vertical-align: top;
          }}
          .ember-cost-table th {{ background: rgba(128, 128, 128, 0.12); }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_economic_impact_data(
    rows: list[CaseStudyCost],
    storage: Storage,
    utility_id: str,
    wildfire_id: str,
) -> None:
    """Render economic-impact evidence when a selected pair has uploaded rows."""
    if not rows:
        return

    st.divider()
    st.subheader("Economic impact over time")
    st.caption(
        "Bars stack all inflation-adjusted rows by Description. Hover for the yearly "
        "total and category breakdown. Rows spanning multiple years are assigned to "
        "their Start Year."
    )
    _render_cost_chart(rows)

    st.subheader("Economic impact source data")
    _render_linked_table(rows, storage)
    st.download_button(
        "Download source data as CSV",
        data=case_study_costs_to_csv(rows),
        file_name=f"{utility_id}_{wildfire_id}_economic_impact.csv",
        mime="text/csv",
    )
