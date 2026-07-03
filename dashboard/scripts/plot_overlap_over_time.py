"""Plot yearly wildfire overlap (km^2) for one utility's source area, with a trendline.

Mirrors the "fires by utility and year range" tool: for a chosen utility it sums the
burn area (km^2) that overlapped the utility's source area per ignition year, then draws
a bar per year plus a linear trendline over time.

Every year in the range is kept on the x-axis. Years with no intersecting fire (or zero
overlap) are shown as a zero-height bar rather than being dropped, so gaps read as "no
overlap that year" instead of vanishing from the axis.

Run from the ``dashboard/`` directory so ``core`` is importable, e.g.::

    python -m scripts.plot_overlap_over_time --utility-name "salem"
    python -m scripts.plot_overlap_over_time --utility-id or4100257 --year-min 1990
    python -m scripts.plot_overlap_over_time --list
"""

from __future__ import annotations

import argparse
import sys

import matplotlib

matplotlib.use("Agg")  # File-first backend; --show swaps to an interactive one below.
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from core.catalog import Catalog  # noqa: E402
from core.models import Utility  # noqa: E402
from core.storage import get_storage  # noqa: E402


def resolve_utility(
    utilities: list[Utility], utility_id: str | None, utility_name: str | None
) -> Utility:
    """Find a single utility by exact id or case-insensitive name substring.

    Exits with a helpful message when nothing matches or a name is ambiguous, so the
    caller never silently plots the wrong source area.
    """
    if utility_id:
        for utility in utilities:
            if utility.utility_id == utility_id:
                return utility
        sys.exit(f"No utility with id '{utility_id}'. Use --list to see available ids.")

    if utility_name:
        needle = utility_name.strip().lower()
        matches = [u for u in utilities if needle in u.name.lower()]
        if not matches:
            sys.exit(f"No utility name contains '{utility_name}'. Use --list to see options.")
        if len(matches) > 1:
            options = "\n".join(f"  {u.utility_id}  {u.name} ({u.state})" for u in matches)
            sys.exit(f"'{utility_name}' matches multiple utilities; pass --utility-id:\n{options}")
        return matches[0]

    sys.exit("Provide --utility-id or --utility-name (or --list to browse).")


def overlap_by_year(
    catalog: Catalog, utility_id: str, year_min: int, year_max: int
) -> tuple[list[int], list[float]]:
    """Return (years, overlap_km2) with every year in range present, zero-filled.

    Sums ``overlap_area_km2`` across all fires that ignited in each year, so a year with
    several intersecting fires reports their combined overlap with the source area.
    """
    fires = catalog.list_intersecting_wildfires(utility_id, year_min, year_max)
    totals: dict[int, float] = {}
    for fire in fires:
        if fire.ignition_year is None:
            continue
        totals[fire.ignition_year] = totals.get(fire.ignition_year, 0.0) + (
            fire.overlap_area_km2 or 0.0
        )
    years = list(range(year_min, year_max + 1))
    values = [totals.get(year, 0.0) for year in years]
    return years, values


def plot_overlap(
    utility: Utility, years: list[int], values: list[float], output: str, show: bool
) -> None:
    """Draw the yearly overlap bar chart with a linear trendline and save/show it."""
    x = np.asarray(years, dtype=float)
    y = np.asarray(values, dtype=float)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(x, y, width=0.8, color="#d62728", alpha=0.75, label="Overlap area")

    # Linear trendline over every year (zeros included) needs at least two distinct years.
    if len(years) >= 2:
        slope, intercept = np.polyfit(x, y, 1)
        ax.plot(
            x,
            slope * x + intercept,
            color="#1f77b4",
            linewidth=2,
            label=f"Trend ({slope:+.2f} km²/yr)",
        )

    ax.set_title(f"Wildfire overlap with {utility.name} source area by year")
    ax.set_xlabel("Ignition year")
    ax.set_ylabel("Overlap area (km²)")
    ax.set_ylim(bottom=0)
    ax.margins(x=0.01)
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend()
    fig.tight_layout()

    fig.savefig(output, dpi=150)
    print(f"Saved plot to {output}")
    if show:
        plt.show()


def main() -> None:
    """Parse arguments, aggregate overlap per year, and render the plot."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--utility-id", help="Exact utility id (e.g. or4100257).")
    parser.add_argument("--utility-name", help="Case-insensitive substring of the utility name.")
    parser.add_argument("--year-min", type=int, help="First year on the x-axis (default: data min).")
    parser.add_argument("--year-max", type=int, help="Last year on the x-axis (default: data max).")
    parser.add_argument("--output", help="Output image path (default: overlap_<utility_id>.png).")
    parser.add_argument("--show", action="store_true", help="Open an interactive window as well.")
    parser.add_argument("--list", action="store_true", help="List utilities and exit.")
    args = parser.parse_args()

    catalog = Catalog(get_storage())
    utilities = catalog.list_utilities()

    if args.list:
        for utility in utilities:
            print(f"{utility.utility_id}\t{utility.name} ({utility.state})")
        return

    utility = resolve_utility(utilities, args.utility_id, args.utility_name)

    bounds = catalog.wildfire_year_bounds()
    if bounds is None:
        sys.exit("No wildfire data is available to plot.")
    data_min, data_max = bounds
    year_min = args.year_min if args.year_min is not None else data_min
    year_max = args.year_max if args.year_max is not None else data_max
    if year_min > year_max:
        sys.exit(f"--year-min ({year_min}) cannot be greater than --year-max ({year_max}).")

    years, values = overlap_by_year(catalog, utility.utility_id, year_min, year_max)

    if args.show:
        matplotlib.use("TkAgg", force=True)
    output = args.output or f"overlap_{utility.utility_id}.png"
    plot_overlap(utility, years, values, output, args.show)


if __name__ == "__main__":
    main()
