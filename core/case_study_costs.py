"""Validation and CSV serialization for case-study economic impact inputs."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import PurePath

from core.models import CaseStudyCost

CSV_COLUMNS = (
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
    description_and_notes: str


def _required_text(row: dict[str, str | None], column: str, row_number: int) -> str:
    value = (row.get(column) or "").strip().strip('"')
    if not value:
        raise CaseStudyCSVError(f"Row {row_number}: `{column}` is required.")
    return value


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
    missing = [column for column in CSV_COLUMNS if column not in actual_columns]
    if missing:
        raise CaseStudyCSVError(f"Missing required column(s): {', '.join(missing)}.")

    rows: list[CaseStudyCostInput] = []
    for row_number, row in enumerate(reader, start=2):
        if row.get(None):
            raise CaseStudyCSVError(
                f"Row {row_number}: found values beyond the declared CSV columns."
            )
        if not any((row.get(column) or "").strip() for column in CSV_COLUMNS):
            continue
        start_year = _parse_year(
            _required_text(row, "Start Year", row_number), "Start Year", row_number
        )
        end_year = _parse_year(
            _required_text(row, "End Year", row_number), "End Year", row_number
        )
        if end_year < start_year:
            raise CaseStudyCSVError(
                f"Row {row_number}: `End Year` cannot be earlier than `Start Year`."
            )
        source = _required_text(row, "Source", row_number)
        if PurePath(source).name != source or source in {".", ".."}:
            raise CaseStudyCSVError(
                f"Row {row_number}: `Source` must be a PDF name, not a folder path."
            )
        rows.append(
            CaseStudyCostInput(
                item_type=_required_text(row, "Item Type", row_number),
                start_year=start_year,
                end_year=end_year,
                description=_required_text(row, "Description", row_number),
                raw_cost=_parse_money(
                    _required_text(row, "Raw Cost", row_number), "Raw Cost", row_number
                ),
                inflation_adjusted_cost=_parse_money(
                    _required_text(row, "Inflation-Adjusted Cost", row_number),
                    "Inflation-Adjusted Cost",
                    row_number,
                ),
                contributing_fires=_required_text(
                    row, "Contributing Fire(s)", row_number
                ),
                source=source,
                method=_required_text(row, "Method", row_number),
                description_and_notes=(row.get("Description and Notes") or "").strip(),
            )
        )
    if not rows:
        raise CaseStudyCSVError("The CSV contains no data rows.")
    return rows


def case_study_costs_to_csv(rows: list[CaseStudyCost]) -> bytes:
    """Serialize stored rows back to the user-facing upload schema."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                row.item_type,
                row.start_year,
                row.end_year,
                row.description,
                row.raw_cost,
                row.inflation_adjusted_cost,
                row.contributing_fires,
                row.source,
                row.method,
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
    """Build stacked-chart rows for every item type, grouped by Description."""
    by_year: dict[int, dict[str, float]] = {}
    for row in rows:
        category = row.description.strip() or "Uncategorized"
        categories = by_year.setdefault(row.start_year, {})
        categories[category] = (
            categories.get(category, 0.0) + row.inflation_adjusted_cost
        )

    chart_rows: list[dict[str, object]] = []
    for year, categories in sorted(by_year.items()):
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


def source_pdf_key(source: str) -> str:
    """Map a validated Source value to its PDF under the case_studies folder."""
    filename = PurePath(source).name
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    return f"case_studies/{filename}"
