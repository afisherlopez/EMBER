"""Validation and aggregation tests for case-study economic-impact data."""

import json
from pathlib import Path

import pytest

from core.case_study_costs import (
    CaseStudyCSVError,
    case_study_wildfire_names,
    case_study_costs_to_csv,
    parse_case_study_csv,
    source_pdf_key,
    source_pdf_references,
    yearly_cost_breakdown,
    yearly_cost_totals,
    yearly_wildfire_amounts,
    wildfire_cost_totals,
)
from core.models import CaseStudyCost


def _row(**overrides: object) -> CaseStudyCost:
    values = {
        "utility_id": "eweb",
        "item_type": "Cost",
        "start_year": 2020,
        "end_year": 2020,
        "description": "Recovery",
        "raw_cost": 100.0,
        "inflation_adjusted_cost": 125.0,
        "contributing_fires": "Holiday Farm Fire",
        "source": "EWEB_Report 2020",
        "method": "SoS Baseline",
        "degree_of_causation": "1",
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


def test_csv_only_requires_recognizable_year_and_cost_columns() -> None:
    rows = parse_case_study_csv(
        b"Year,Cost,Vendor,Internal note\n2020,125,Acme,Keep this column\n"
    )

    assert rows[0].start_year == 2020
    assert rows[0].inflation_adjusted_cost == 125
    assert json.loads(rows[0].extra_fields_json) == {
        "Year": "2020",
        "Cost": "125",
        "Vendor": "Acme",
        "Internal note": "Keep this column",
    }

    with pytest.raises(CaseStudyCSVError, match="identify a year column"):
        parse_case_study_csv(b"Cost,Vendor\n125,Acme\n")
    with pytest.raises(CaseStudyCSVError, match="identify a cost column"):
        parse_case_study_csv(b"Year,Vendor\n2020,Acme\n")


def test_csv_download_preserves_arbitrary_uploaded_columns() -> None:
    stored = _row(
        extra_fields_json=json.dumps(
            {"Year": "2020", "Cost": "$125", "Vendor": "Acme"}
        )
    )

    exported = case_study_costs_to_csv([stored]).decode()

    assert exported.splitlines() == ["Year,Cost,Vendor", '2020,$125,Acme']


def test_csv_allows_multiple_cost_and_aid_rows_per_year() -> None:
    data = b"""Item Type,Years Incurred,Item Summary,Raw Value,Inflation-Adjusted Value,Contributing Fire(s),Source,Method,Degree of Causation,Description and Notes
Cost,2020,Recovery,400,400,Fire,report.pdf,Direct,Direct,
Cost,2020,Operations,600,600,Fire,report.pdf,Direct,Direct,
Aid,2020,Grant,100,100,Fire,report.pdf,Direct,Partial,
Aid,2020,Grant,200,200,Fire,report.pdf,Direct,Partial,
"""

    rows = parse_case_study_csv(data)

    assert len(rows) == 4


def test_csv_allows_aid_to_exceed_cost_rows() -> None:
    data = b"""Item Type,Years Incurred,Item Summary,Raw Value,Inflation-Adjusted Value,Contributing Fire(s),Source,Method,Degree of Causation,Description and Notes
Cost,2020,Recovery,100,100,Fire,report.pdf,Direct,Direct,
Cost,2020,Operations,200,200,Fire,report.pdf,Direct,Direct,
Aid,2020,Grant,301,301,Fire,report.pdf,Direct,Partial,
"""

    rows = parse_case_study_csv(data)

    assert len(rows) == 3


def test_csv_parses_and_serializes_year_lists() -> None:
    data = b"""Item Type,Years Incurred,Item Summary,Raw Value,Inflation-Adjusted Value,Contributing Fire(s),Source,Method,Degree of Causation,Description and Notes
Cost,"2020, 2021, 2022",Recovery,100,125,Fire,report.pdf,Direct,Direct,Three-year program
"""

    parsed = parse_case_study_csv(data)

    assert (parsed[0].start_year, parsed[0].end_year) == (2020, 2022)
    stored = _row(start_year=2020, end_year=2022)
    assert b'Cost,"2020, 2021, 2022",Recovery' in case_study_costs_to_csv([stored])


def test_csv_rejects_year_ranges() -> None:
    data = b"""Item Type,Years Incurred,Inflation-Adjusted Value
Cost,2020-2022,125
"""

    with pytest.raises(CaseStudyCSVError, match="comma-separated list"):
        parse_case_study_csv(data)


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
        _row(description="Recovery", inflation_adjusted_cost=600.0),
        _row(description="Recovery", inflation_adjusted_cost=400.0),
        _row(
            item_type="Aid",
            description="Monitoring",
            inflation_adjusted_cost=100.0,
        ),
        _row(item_type="Aid", description="Grant", inflation_adjusted_cost=125.0),
        _row(item_type="Aid", description="Grant", inflation_adjusted_cost=75.0),
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


def test_wildfire_allocations_filter_causation_and_split_multi_fire_rows() -> None:
    rows = [
        _row(
            start_year=2021,
            end_year=2023,
            contributing_fires="Fire A, Fire B",
            inflation_adjusted_cost=120.0,
        ),
        _row(
            contributing_fires="Fire A",
            inflation_adjusted_cost=500.0,
            degree_of_causation="2",
        ),
        _row(
            item_type="Aid",
            contributing_fires="Fire A",
            inflation_adjusted_cost=30.0,
        ),
        _row(
            item_type="Fee",
            contributing_fires="Fire B",
            inflation_adjusted_cost=20.0,
        ),
    ]

    assert yearly_wildfire_amounts(rows, item_type="Cost") == [
        {"Year": "2021", "Wildfire": "Fire A", "Amount": 60.0, "Year total": 120.0},
        {"Year": "2021", "Wildfire": "Fire B", "Amount": 60.0, "Year total": 120.0},
    ]
    assert yearly_wildfire_amounts(rows, item_type="Aid") == [
        {"Year": "2020", "Wildfire": "Fire A", "Amount": 30.0, "Year total": 30.0}
    ]
    assert wildfire_cost_totals(rows) == [
        {"Wildfire": "Fire A", "Amount": 60.0, "Total": 140.0},
        {"Wildfire": "Fire B", "Amount": 80.0, "Total": 140.0},
    ]
    assert case_study_wildfire_names(rows) == ["Fire A", "Fire B"]


def test_source_name_maps_to_case_studies_pdf() -> None:
    assert source_pdf_key("EWEB_Report 2020") == (
        "case_studies/EWEB/EWEB_Report_2020.pdf"
    )
    assert source_pdf_key("report.PDF") == "case_studies/report.PDF"
    assert source_pdf_references("EWEB_Report_2020, EWEB_Report_2021") == [
        ("EWEB_Report_2020", "case_studies/EWEB/EWEB_Report_2020.pdf"),
        ("EWEB_Report_2021", "case_studies/EWEB/EWEB_Report_2021.pdf"),
    ]
