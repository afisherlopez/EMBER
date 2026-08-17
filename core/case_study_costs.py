"""Validation and CSV serialization for case-study economic impact inputs."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import PurePath

from core.models import CaseStudyCost

CSV_COLUMNS = (
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
)
YEAR_COLUMN_ALIASES = ("Years Incurred", "Year", "Start Year")
COST_COLUMN_ALIASES = (
    "Inflation-Adjusted Value",
    "Inflation-Adjusted Cost",
    "Cost",
    "Raw Value",
    "Raw Cost",
)


class CaseStudyCSVError(ValueError):
    """Raised when an uploaded case-study CSV does not match the expected schema."""


@dataclass(frozen=True)
class CaseStudyCostInput:
    """Validated CSV values before utility and wildfire identifiers are attached."""

    item_type: str
    start_year: int
    end_year: int
    description: str
    raw_cost: float
    inflation_adjusted_cost: float
    contributing_fires: str
    source: str
    method: str
    degree_of_causation: str
    description_and_notes: str
    extra_fields_json: str


def _required_text(row: dict[str, str | None], column: str, row_number: int) -> str:
    value = (row.get(column) or "").strip().strip('"')
    if not value:
        raise CaseStudyCSVError(f"Row {row_number}: `{column}` is required.")
    return value


def _optional_text(row: dict[str, str | None], column: str | None) -> str:
    if column is None:
        return ""
    return (row.get(column) or "").strip().strip('"')


def _find_column(columns: tuple[str, ...], aliases: tuple[str, ...]) -> str | None:
    normalized = {column.strip().casefold(): column for column in columns}
    for alias in aliases:
        if alias.casefold() in normalized:
            return normalized[alias.casefold()]
    return None


def _parse_year(value: str, column: str, row_number: int) -> int:
    try:
        year = int(value)
    except ValueError as exc:
        raise CaseStudyCSVError(
            f"Row {row_number}: `{column}` must be a four-digit year."
        ) from exc
    if year < 1000 or year > 9999:
        raise CaseStudyCSVError(f"Row {row_number}: `{column}` must be a four-digit year.")
    return year


def _parse_years_incurred(value: str, row_number: int) -> tuple[int, int]:
    year_values = [item.strip() for item in value.split(",")]
    if not year_values or any(
        not re.fullmatch(r"\d{4}", item) for item in year_values
    ):
        raise CaseStudyCSVError(
            f"Row {row_number}: `Years Incurred` must be one year or a "
            "comma-separated list of years (for example, `2020` or "
            "`2020, 2021, 2022`). Enclose a multi-year list in quotes in the CSV."
        )
    years = [
        _parse_year(item, "Years Incurred", row_number) for item in year_values
    ]
    if any(current <= previous for previous, current in zip(years, years[1:])):
        raise CaseStudyCSVError(
            f"Row {row_number}: years in `Years Incurred` must be unique and "
            "listed in ascending order."
        )
    return years[0], years[-1]


def _parse_money(value: str, column: str, row_number: int) -> float:
    normalized = value.strip().replace("$", "").replace(",", "")
    is_parenthesized = normalized.startswith("(") and normalized.endswith(")")
    if is_parenthesized:
        normalized = f"-{normalized[1:-1]}"
    try:
        return float(normalized)
    except ValueError as exc:
        raise CaseStudyCSVError(
            f"Row {row_number}: `{column}` must be a numeric currency value."
        ) from exc


def parse_case_study_csv(data: bytes) -> list[CaseStudyCostInput]:
    """Parse and validate one uploaded CSV using the published case-study schema."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CaseStudyCSVError("The CSV must be UTF-8 encoded.") from exc

    reader = csv.DictReader(io.StringIO(text))
    actual_columns = tuple(reader.fieldnames or ())
    if not actual_columns or any(not column.strip() for column in actual_columns):
        raise CaseStudyCSVError("The CSV must have a non-empty header row.")
    year_column = _find_column(actual_columns, YEAR_COLUMN_ALIASES)
    cost_column = _find_column(actual_columns, COST_COLUMN_ALIASES)
    if year_column is None:
        raise CaseStudyCSVError(
            "Could not identify a year column. Use `Years Incurred`, `Year`, "
            "or `Start Year`."
        )
    if cost_column is None:
        raise CaseStudyCSVError(
            "Could not identify a cost column. Use `Inflation-Adjusted Value`, "
            "`Inflation-Adjusted Cost`, `Cost`, `Raw Value`, or `Raw Cost`."
        )

    end_year_column = _find_column(actual_columns, ("End Year",))
    item_type_column = _find_column(actual_columns, ("Item Type",))
    summary_column = _find_column(actual_columns, ("Item Summary", "Description"))
    raw_value_column = _find_column(actual_columns, ("Raw Value", "Raw Cost"))
    adjusted_value_column = _find_column(
        actual_columns,
        ("Inflation-Adjusted Value", "Inflation-Adjusted Cost"),
    )
    fires_column = _find_column(actual_columns, ("Contributing Fire(s)",))
    source_column = _find_column(actual_columns, ("Source",))
    method_column = _find_column(actual_columns, ("Method",))
    causation_column = _find_column(actual_columns, ("Degree of Causation",))
    notes_column = _find_column(actual_columns, ("Description and Notes",))

    rows: list[CaseStudyCostInput] = []
    for row_number, row in enumerate(reader, start=2):
        if row.get(None):
            raise CaseStudyCSVError(
                f"Row {row_number}: found values beyond the declared CSV columns. "
                "If `Years Incurred` contains multiple years, enclose the entire "
                "comma-separated list in quotes."
            )
        if not any((row.get(column) or "").strip() for column in actual_columns):
            continue
        if year_column.strip().casefold() == "start year":
            start_year = _parse_year(
                _required_text(row, year_column, row_number),
                year_column,
                row_number,
            )
            end_year = (
                _parse_year(
                    _required_text(row, end_year_column, row_number),
                    end_year_column,
                    row_number,
                )
                if end_year_column
                else start_year
            )
            if end_year < start_year:
                raise CaseStudyCSVError(
                    f"Row {row_number}: `End Year` cannot be earlier than `Start Year`."
                )
        else:
            start_year, end_year = _parse_years_incurred(
                _required_text(row, year_column, row_number),
                row_number,
            )
        source = _optional_text(row, source_column)
        if source and (PurePath(source).name != source or source in {".", ".."}):
            raise CaseStudyCSVError(
                f"Row {row_number}: `Source` must be a PDF name, not a folder path."
            )
        raw_value = _optional_text(row, raw_value_column)
        adjusted_value = _optional_text(row, adjusted_value_column)
        selected_cost_value = (
            _optional_text(row, cost_column) or adjusted_value or raw_value
        )
        if not selected_cost_value:
            raise CaseStudyCSVError(
                f"Row {row_number}: a cost/value entry is required."
            )
        selected_cost = _parse_money(
            selected_cost_value,
            cost_column,
            row_number,
        )
        raw_cost = (
            _parse_money(raw_value, raw_value_column or cost_column, row_number)
            if raw_value
            else selected_cost
        )
        adjusted_cost = (
            _parse_money(
                adjusted_value,
                adjusted_value_column or cost_column,
                row_number,
            )
            if adjusted_value
            else selected_cost
        )
        rows.append(
            CaseStudyCostInput(
                item_type=_optional_text(row, item_type_column) or "Cost",
                start_year=start_year,
                end_year=end_year,
                description=_optional_text(row, summary_column),
                raw_cost=raw_cost,
                inflation_adjusted_cost=adjusted_cost,
                contributing_fires=_optional_text(row, fires_column),
                source=source,
                method=_optional_text(row, method_column),
                degree_of_causation=_optional_text(row, causation_column),
                description_and_notes=_optional_text(row, notes_column),
                extra_fields_json=json.dumps(
                    {
                        column: (row.get(column) or "").strip()
                        for column in actual_columns
                    },
                    ensure_ascii=False,
                ),
            )
        )
    if not rows:
        raise CaseStudyCSVError("The CSV contains no data rows.")
    return rows


def case_study_costs_to_csv(rows: list[CaseStudyCost]) -> bytes:
    """Serialize stored rows back to the user-facing upload schema."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    uploaded_values: list[dict[str, str]] = []
    columns: list[str] = []
    for row in rows:
        try:
            values = json.loads(getattr(row, "extra_fields_json", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            values = {}
        if not isinstance(values, dict):
            values = {}
        string_values = {str(key): str(value or "") for key, value in values.items()}
        uploaded_values.append(string_values)
        for column in string_values:
            if column not in columns:
                columns.append(column)

    if columns:
        writer.writerow(columns)
        for values in uploaded_values:
            writer.writerow([values.get(column, "") for column in columns])
    else:
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row.item_type,
                    (
                        str(row.start_year)
                        if row.start_year == row.end_year
                        else ", ".join(
                            str(year)
                            for year in range(row.start_year, row.end_year + 1)
                        )
                    ),
                    row.description,
                    row.raw_cost,
                    row.inflation_adjusted_cost,
                    row.contributing_fires,
                    row.source,
                    row.method,
                    getattr(row, "degree_of_causation", ""),
                    row.description_and_notes,
                ]
            )
    return output.getvalue().encode("utf-8")


def yearly_cost_totals(rows: list[CaseStudyCost]) -> dict[int, float]:
    """Sum inflation-adjusted Cost rows by their start year."""
    totals: dict[int, float] = {}
    for row in rows:
        if row.item_type.strip().casefold() != "cost":
            continue
        totals[row.start_year] = (
            totals.get(row.start_year, 0.0) + row.inflation_adjusted_cost
        )
    return dict(sorted(totals.items()))


def yearly_cost_breakdown(rows: list[CaseStudyCost]) -> list[dict[str, object]]:
    """Build yearly stacks where summed Cost is the total and other rows are components."""
    rows_by_year: dict[int, list[CaseStudyCost]] = {}
    for row in rows:
        rows_by_year.setdefault(row.start_year, []).append(row)

    chart_rows: list[dict[str, object]] = []
    for year, year_rows in sorted(rows_by_year.items()):
        cost_rows = [
            row
            for row in year_rows
            if row.item_type.strip().casefold() == "cost"
        ]
        component_rows = [
            row
            for row in year_rows
            if row.item_type.strip().casefold() != "cost"
        ]
        categories: dict[str, float] = {}
        for row in component_rows:
            category = row.description.strip() or "Uncategorized"
            categories[category] = (
                categories.get(category, 0.0) + row.inflation_adjusted_cost
            )

        if cost_rows:
            total = sum(row.inflation_adjusted_cost for row in cost_rows)
            remainder = total - sum(categories.values())
            if remainder > 0:
                cost_descriptions = {
                    row.description.strip() for row in cost_rows if row.description.strip()
                }
                category = (
                    next(iter(cost_descriptions))
                    if len(cost_descriptions) == 1
                    else "Other cost"
                )
                categories[category] = categories.get(category, 0.0) + remainder
        else:
            total = sum(categories.values())

        breakdown = "\n".join(
            f"{category}: ${amount:,.2f}"
            for category, amount in sorted(categories.items())
        )
        for category, amount in sorted(categories.items()):
            chart_rows.append(
                {
                    "Year": str(year),
                    "Category": category,
                    "Amount": amount,
                    "Total": total,
                    "Breakdown": breakdown,
                }
            )
    return chart_rows


def split_contributing_fires(value: str) -> list[str]:
    """Split one row's fire labels, preserving order and removing duplicates."""
    fires: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[,;\n]+", value):
        fire = item.strip().strip('"')
        key = fire.casefold()
        if fire and key not in seen:
            fires.append(fire)
            seen.add(key)
    return fires or ["Unspecified wildfire"]


def case_study_wildfire_names(rows: list[CaseStudyCost]) -> list[str]:
    """Return distinct named wildfires referenced anywhere in a utility case study."""
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for fire in split_contributing_fires(row.contributing_fires):
            key = fire.casefold()
            if fire != "Unspecified wildfire" and key not in seen:
                names.append(fire)
                seen.add(key)
    return names


def _has_full_wildfire_causation(row: CaseStudyCost) -> bool:
    try:
        return float(row.degree_of_causation.strip()) == 1.0
    except (AttributeError, ValueError):
        return False


def _allocated_row_amounts(row: CaseStudyCost) -> list[tuple[str, float]]:
    fires = split_contributing_fires(row.contributing_fires)
    amount = row.inflation_adjusted_cost / len(fires)
    return [(fire, amount) for fire in fires]


def yearly_wildfire_amounts(
    rows: list[CaseStudyCost],
    *,
    item_type: str,
) -> list[dict[str, object]]:
    """Aggregate fully attributable Cost or Aid rows by first year and wildfire."""
    totals: dict[tuple[int, str], float] = {}
    expected_type = item_type.strip().casefold()
    for row in rows:
        if not _has_full_wildfire_causation(row):
            continue
        if row.item_type.strip().casefold() != expected_type:
            continue
        for fire, amount in _allocated_row_amounts(row):
            key = (row.start_year, fire)
            totals[key] = totals.get(key, 0.0) + amount

    year_totals: dict[int, float] = {}
    for (year, _), amount in totals.items():
        year_totals[year] = year_totals.get(year, 0.0) + amount
    return [
        {
            "Year": str(year),
            "Wildfire": fire,
            "Amount": amount,
            "Year total": year_totals[year],
        }
        for (year, fire), amount in sorted(totals.items())
    ]


def wildfire_cost_totals(rows: list[CaseStudyCost]) -> list[dict[str, object]]:
    """Aggregate fully attributable non-Aid rows across all years by wildfire."""
    totals: dict[str, float] = {}
    for row in rows:
        if not _has_full_wildfire_causation(row):
            continue
        if row.item_type.strip().casefold() == "aid":
            continue
        for fire, amount in _allocated_row_amounts(row):
            totals[fire] = totals.get(fire, 0.0) + amount
    overall_total = sum(totals.values())
    return [
        {
            "Wildfire": fire,
            "Amount": amount,
            "Total": overall_total,
        }
        for fire, amount in sorted(totals.items())
    ]


def source_pdf_key(source: str) -> str:
    """Map a validated Source value to its PDF under the case_studies folder."""
    filename = re.sub(r"\s+", "_", PurePath(source.strip()).name)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    folder = filename.split("_", 1)[0] if "_" in filename else ""
    return f"case_studies/{folder}/{filename}" if folder else f"case_studies/{filename}"


def source_pdf_references(source: str) -> list[tuple[str, str]]:
    """Split a Source cell into display labels and corresponding PDF object keys."""
    references = []
    for item in source.split(","):
        label = item.strip()
        if label:
            references.append((label, source_pdf_key(label)))
    return references
