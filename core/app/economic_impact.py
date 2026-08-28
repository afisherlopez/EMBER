"""Case-study cost chart, linked raw-data table, and CSV download."""

from __future__ import annotations

import json
from html import escape

import streamlit as st

from core.case_study_costs import (
    case_study_costs_to_csv,
    source_pdf_references,
    wildfire_cost_totals,
    yearly_wildfire_amounts,
)
from core.models import CaseStudyCost
from core.storage import Storage


WILDFIRE_COLORS = [
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ab",
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]
CAUSATION_HELP = (
    "A value of 1 means that 100% of the cost can be attributed to wildfires. "
    "A value of 2 means that there is a non-wildfire driver of the cost that "
    "cannot be fully separated from the wildfire-induced costs, e.g. costs are "
    "for a full fiscal year in the middle of which the wildfire occurred."
)


def _color_scale(wildfires: list[str]) -> dict[str, list[str]]:
    return {
        "domain": wildfires,
        "range": [
            WILDFIRE_COLORS[index % len(WILDFIRE_COLORS)]
            for index in range(len(wildfires))
        ],
    }


def _render_yearly_wildfire_chart(
    chart_rows: list[dict[str, object]],
    *,
    title: str,
    wildfires: list[str],
    height: int,
) -> None:
    st.subheader(title)
    if not chart_rows:
        st.info("No rows meet this chart's criteria.")
        return
    st.vega_lite_chart(
        {
            "data": {"values": chart_rows},
            "height": height,
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
                    "title": "Inflation-adjusted value (USD)",
                    "stack": "zero",
                    "axis": {"format": "$~s"},
                },
                "color": {
                    "field": "Wildfire",
                    "type": "nominal",
                    "title": "Wildfire",
                    "scale": _color_scale(wildfires),
                    "legend": {"orient": "bottom", "columns": 2},
                },
                "order": {"field": "Wildfire", "sort": "ascending"},
                "tooltip": [
                    {"field": "Year", "type": "ordinal", "title": "Year"},
                    {
                        "field": "Year total",
                        "type": "quantitative",
                        "title": "Year total",
                        "format": "$,.2f",
                    },
                    {
                        "field": "Wildfire",
                        "type": "nominal",
                        "title": "Wildfire",
                    },
                    {
                        "field": "Amount",
                        "type": "quantitative",
                        "title": "Attributed amount",
                        "format": "$,.2f",
                    },
                ],
            },
        },
        width="stretch",
    )


def _render_wildfire_pie_chart(
    chart_rows: list[dict[str, object]],
    *,
    wildfires: list[str],
) -> None:
    st.subheader("Cost per wildfire")
    if not chart_rows:
        st.info("No rows meet this chart's criteria.")
        return
    st.vega_lite_chart(
        {
            "data": {"values": chart_rows},
            "height": 320,
            "mark": {"type": "arc", "innerRadius": 35},
            "encoding": {
                "theta": {
                    "field": "Amount",
                    "type": "quantitative",
                    "stack": True,
                },
                "color": {
                    "field": "Wildfire",
                    "type": "nominal",
                    "title": "Wildfire",
                    "scale": _color_scale(wildfires),
                    "legend": {"orient": "bottom", "columns": 2},
                },
                "tooltip": [
                    {"field": "Wildfire", "type": "nominal", "title": "Wildfire"},
                    {
                        "field": "Amount",
                        "type": "quantitative",
                        "title": "Attributed cost",
                        "format": "$,.2f",
                    },
                    {
                        "field": "Total",
                        "type": "quantitative",
                        "title": "Total cost",
                        "format": "$,.2f",
                    },
                ],
            },
        },
        width="stretch",
    )


def _render_linked_table(rows: list[CaseStudyCost], storage: Storage) -> None:
    fallback_headers = [
        "Item Type",
        "Years Incurred",
        "Item Summary",
        "Raw Value",
        "Inflation-Adjusted Value",
        "Contributing Fire(s)",
        "Source",
        "Method",
        "Degree of Causation",
        "Description and Notes",
    ]
    display_rows: list[dict[str, str]] = []
    headers: list[str] = []
    for row in rows:
        try:
            uploaded_values = json.loads(getattr(row, "extra_fields_json", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            uploaded_values = {}
        if not isinstance(uploaded_values, dict) or not uploaded_values:
            uploaded_values = {
                "Item Type": row.item_type,
                "Years Incurred": (
                    str(row.start_year)
                    if row.start_year == row.end_year
                    else f"{row.start_year}-{row.end_year}"
                ),
                "Item Summary": row.description,
                "Raw Value": f"${row.raw_cost:,.2f}",
                "Inflation-Adjusted Value": (
                    f"${row.inflation_adjusted_cost:,.2f}"
                ),
                "Contributing Fire(s)": row.contributing_fires,
                "Source": row.source,
                "Method": row.method,
                "Degree of Causation": getattr(row, "degree_of_causation", ""),
                "Description and Notes": row.description_and_notes,
            }
        values = {
            str(column): str(value or "")
            for column, value in uploaded_values.items()
        }
        display_rows.append(values)
        for column in values:
            if column not in headers:
                headers.append(column)
    if not headers:
        headers = fallback_headers

    body_rows = []
    for row_values in display_rows:
        values = []
        for header in headers:
            raw_value = row_values.get(header, "")
            if header.strip().casefold() == "source":
                source_links = []
                for source_label, source_key in source_pdf_references(raw_value):
                    source_url = storage.public_url_for(source_key)
                    source_links.append(
                        f'<a href="{escape(source_url, quote=True)}" target="_blank" '
                        f'rel="noopener noreferrer">{escape(source_label)}</a>'
                    )
                values.append(", ".join(source_links))
            elif header.strip().casefold() == "description and notes":
                escaped_notes = escape(raw_value)
                values.append(
                    '<span class="ember-description-tooltip" tabindex="0">'
                    f"{escaped_notes}"
                    '<span class="ember-description-tooltip-content" role="tooltip">'
                    f"{escaped_notes}"
                    "</span></span>"
                )
            else:
                values.append(escape(raw_value))
        body_rows.append(
            "<tr>" + "".join(f"<td>{value}</td>" for value in values) + "</tr>"
        )

    header_cells = []
    for header in headers:
        escaped_header = escape(header)
        if header.strip().casefold() == "degree of causation":
            header_cells.append(
                '<th><span class="ember-header-tooltip" tabindex="0">'
                f"{escaped_header}"
                '<span class="ember-header-tooltip-content" role="tooltip">'
                f"{escape(CAUSATION_HELP)}"
                "</span></span></th>"
            )
        else:
            header_cells.append(f"<th>{escaped_header}</th>")
    header_html = "".join(header_cells)
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
          .ember-header-tooltip {{
            position: relative;
            display: inline-block;
            cursor: help;
            text-decoration: underline dotted;
            text-underline-offset: 3px;
          }}
          .ember-header-tooltip-content {{
            visibility: hidden;
            opacity: 0;
            position: absolute;
            z-index: 10000;
            top: calc(100% + 0.4rem);
            left: 0;
            width: min(420px, 75vw);
            padding: 0.75rem;
            border: 1px solid #d0d0d0;
            border-radius: 0.5rem;
            background: #fff;
            color: #222;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18);
            font-size: 0.9rem;
            font-weight: normal;
            line-height: 1.45;
            text-align: left;
            white-space: normal;
            pointer-events: none;
            transition: opacity 120ms ease-in-out;
          }}
          .ember-header-tooltip:hover .ember-header-tooltip-content,
          .ember-header-tooltip:focus .ember-header-tooltip-content {{
            visibility: visible;
            opacity: 1;
          }}
          .ember-description-tooltip {{
            position: relative;
            display: inline-block;
            cursor: help;
            text-decoration: underline dotted;
            text-underline-offset: 3px;
          }}
          .ember-description-tooltip-content {{
            visibility: hidden;
            opacity: 0;
            position: absolute;
            z-index: 10000;
            top: calc(100% + 0.4rem);
            left: 0;
            width: min(360px, 70vw);
            padding: 0.75rem;
            border: 1px solid #d0d0d0;
            border-radius: 0.5rem;
            background: #fff;
            color: #222;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.18);
            font-size: 0.9rem;
            font-weight: normal;
            line-height: 1.45;
            text-align: left;
            white-space: normal;
            pointer-events: none;
            transition: opacity 120ms ease-in-out;
          }}
          .ember-description-tooltip:hover .ember-description-tooltip-content,
          .ember-description-tooltip:focus .ember-description-tooltip-content {{
            visibility: visible;
            opacity: 1;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_economic_impact_data(
    rows: list[CaseStudyCost],
    storage: Storage,
    utility_id: str,
) -> None:
    """Render utility-wide wildfire-attributed economic-impact evidence."""
    if not rows:
        return

    cost_rows = yearly_wildfire_amounts(rows, item_type="Cost")
    cost_per_fire = wildfire_cost_totals(rows)
    aid_rows = yearly_wildfire_amounts(rows, item_type="Aid")
    wildfires = sorted(
        {
            str(row["Wildfire"])
            for chart_rows in (cost_rows, cost_per_fire, aid_rows)
            for row in chart_rows
        }
    )
    _render_yearly_wildfire_chart(
        cost_rows,
        title="Wildfire-induced costs per year",
        wildfires=wildfires,
        height=360,
    )

    pie_col, aid_col = st.columns(2, gap="large")
    with pie_col:
        _render_wildfire_pie_chart(cost_per_fire, wildfires=wildfires)
    with aid_col:
        _render_yearly_wildfire_chart(
            aid_rows,
            title="Aid received per year",
            wildfires=wildfires,
            height=320,
        )

    st.subheader("Economic impact source data")
    _render_linked_table(rows, storage)
    st.download_button(
        "Download source data as CSV",
        data=case_study_costs_to_csv(rows),
        file_name=f"{utility_id}_economic_impact.csv",
        mime="text/csv",
    )
