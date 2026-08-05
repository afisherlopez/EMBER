"""Validation and aggregation tests for case-study economic-impact data."""

from pathlib import Path

import pytest

from core.case_study_costs import (
    CaseStudyCSVError,
    parse_case_study_csv,
    source_pdf_key,
    source_pdf_references,
    yearly_cost_breakdown,
    yearly_cost_totals,
)
from core.models import CaseStudyCost


def _row(**overrides: object) -> CaseStudyCost:
    values = {
        "utility_id": "eweb",
        "wildfire_id": "holiday-farm",
        "item_type": "Cost",
        "start_year": 2020,
        "end_year": 2020,
        "description": "Recovery",
        "raw_cost": 100.0,
        "inflation_adjusted_cost": 125.0,
        "contributing_fires": "Holiday Farm Fire",
        "source": "EWEB_Report 2020",
        "method": "SoS Baseline",
        "description_and_notes": "",
    }
    values.update(overrides)
    return CaseStudyCost(**values)  # type: ignore[arg-type]


def test_repository_example_csv_is_accepted() -> None:
    csv_path = (
        Path(__file__).resolve().parents[1]
        / "EMBER Case-Study Datasheet - EWEB_W_Costs.csv"
    )
    rows = parse_case_study_csv(csv_path.read_bytes())

    assert len(rows) == 7
    assert rows[0].raw_cost == 783_000
    assert rows[0].description == "McKenzie River Watershed Recovery"
    assert rows[-1].item_type == "Aid"


def test_csv_requires_all_published_columns() -> None:
    with pytest.raises(CaseStudyCSVError, match="Missing required column"):
        parse_case_study_csv(b"Item Type,Start Year\nCost,2020\n")


def test_yearly_totals_use_only_inflation_adjusted_cost_rows() -> None:
    rows = [
        _row(inflation_adjusted_cost=125.0),
        _row(inflation_adjusted_cost=75.0),
        _row(item_type="Aid", inflation_adjusted_cost=500.0),
        _row(start_year=2021, end_year=2022, inflation_adjusted_cost=300.0),
    ]

    assert yearly_cost_totals(rows) == {2020: 200.0, 2021: 300.0}


def test_yearly_breakdown_contains_category_amounts_and_total() -> None:
    rows = [
        _row(description="Recovery", inflation_adjusted_cost=1_000.0),
        _row(
            item_type="Aid",
            description="Monitoring",
            inflation_adjusted_cost=100.0,
        ),
        _row(item_type="Aid", description="Grant", inflation_adjusted_cost=200.0),
    ]

    chart_rows = yearly_cost_breakdown(rows)

    assert chart_rows == [
        {
            "Year": "2020",
            "Category": "Grant",
            "Amount": 200.0,
            "Total": 1_000.0,
            "Breakdown": (
                "Grant: $200.00\nMonitoring: $100.00\nRecovery: $700.00"
            ),
        },
        {
            "Year": "2020",
            "Category": "Monitoring",
            "Amount": 100.0,
            "Total": 1_000.0,
            "Breakdown": (
                "Grant: $200.00\nMonitoring: $100.00\nRecovery: $700.00"
            ),
        },
        {
            "Year": "2020",
            "Category": "Recovery",
            "Amount": 700.0,
            "Total": 1_000.0,
            "Breakdown": (
                "Grant: $200.00\nMonitoring: $100.00\nRecovery: $700.00"
            ),
        },
    ]


def test_source_name_maps_to_case_studies_pdf() -> None:
    assert source_pdf_key("EWEB_Report 2020") == (
        "case_studies/EWEB/EWEB_Report_2020.pdf"
    )
    assert source_pdf_key("report.PDF") == "case_studies/report.PDF"
    assert source_pdf_references("EWEB_Report_2020, EWEB_Report_2021") == [
        ("EWEB_Report_2020", "case_studies/EWEB/EWEB_Report_2020.pdf"),
        ("EWEB_Report_2021", "case_studies/EWEB/EWEB_Report_2021.pdf"),
    ]
