"""Selector controls for profile, utility, and wildfire choices.

Named ``selector_controls`` (not ``selectors``) to avoid shadowing Python's standard-library
``selectors`` module: ``streamlit run`` puts this package directory on ``sys.path``, and a
local ``selectors.py`` would override the stdlib module that asyncio/tornado depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import streamlit as st

from core.models import ProfileDefinition, Utility, Wildfire


@dataclass(frozen=True)
class SelectorState:
    """Resolved selector values from the header controls."""

    profile_key: str
    utility_id: str | None
    wildfire_id: str | None


def _wildfire_label(wildfire: Wildfire) -> str:
    ignition = wildfire.ignition_date.isoformat() if isinstance(wildfire.ignition_date, date) else "unknown"
    return f"{wildfire.name} ({ignition}, {wildfire.state}/{wildfire.county})"


def render_selectors(
    profiles: dict[str, ProfileDefinition], utilities: list[Utility], wildfires: list[Wildfire]
) -> SelectorState:
    """Render selector row and return selected profile, utility, and wildfire ids."""
    profile_col, utility_state_col, utility_col, wildfire_col = st.columns([2, 1, 2, 3])

    profile_options = list(profiles.keys())
    with profile_col:
        profile_key = st.selectbox(
            "I am…",
            options=profile_options,
            index=0,
            format_func=lambda key: profiles[key].label,
        )

    utility_states = sorted({u.state for u in utilities})
    with utility_state_col:
        utility_state_filter = st.selectbox("Utility state filter", ["All"] + utility_states, index=0)

    filtered_utilities = utilities
    if utility_state_filter != "All":
        filtered_utilities = [u for u in utilities if u.state == utility_state_filter]

    utility_map = {f"{u.name} ({u.state})": u.utility_id for u in filtered_utilities}
    with utility_col:
        utility_label = st.selectbox("Water utility", options=list(utility_map.keys()), index=None, placeholder="Select utility")
    utility_id = utility_map.get(utility_label) if utility_label else None

    wildfire_states = sorted({w.state for w in wildfires})
    state_filter_col, year_filter_col, sort_col = st.columns([1, 1, 1])
    with state_filter_col:
        wildfire_state_filter = st.selectbox("Fire state filter", ["All"] + wildfire_states, index=0)
    with year_filter_col:
        year_options = sorted({w.ignition_date.year for w in wildfires if w.ignition_date is not None}, reverse=True)
        wildfire_year_filter = st.selectbox("Fire year filter", ["All"] + year_options, index=0)
    with sort_col:
        wildfire_sort = st.selectbox("Fire sort", ["Newest first", "Oldest first", "Name"], index=0)

    filtered = wildfires
    if wildfire_state_filter != "All":
        filtered = [w for w in filtered if w.state == wildfire_state_filter]
    if wildfire_year_filter != "All":
        filtered = [w for w in filtered if w.ignition_date and w.ignition_date.year == wildfire_year_filter]

    if wildfire_sort == "Oldest first":
        filtered = sorted(filtered, key=lambda item: (item.ignition_date or date.min, item.name))
    elif wildfire_sort == "Name":
        filtered = sorted(filtered, key=lambda item: item.name)

    wildfire_options = {_wildfire_label(w): w.wildfire_id for w in filtered}
    with wildfire_col:
        wildfire_label = st.selectbox(
            "Wildfire",
            options=list(wildfire_options.keys()),
            index=None,
            placeholder="Select wildfire",
        )
    wildfire_id = wildfire_options.get(wildfire_label) if wildfire_label else None

    return SelectorState(profile_key=profile_key, utility_id=utility_id, wildfire_id=wildfire_id)
