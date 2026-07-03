"""Single-source data-state resolution for scalar and raster feature rendering."""

from __future__ import annotations

from typing import Literal


DataState = Literal["no_impact", "pending", "available"]


def resolve_state(has_overlap: bool, payload: object | None) -> DataState:
    """Resolve panel/map state from overlap fact and metric payload."""
    if not has_overlap:
        return "no_impact"
    if payload is None:
        return "pending"
    return "available"


def state_message(state: DataState) -> str | None:
    """Return required display text for non-available states."""
    if state == "no_impact":
        return "No direct impact"
    if state == "pending":
        return "Data not yet available"
    return None
