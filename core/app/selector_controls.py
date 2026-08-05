"""Selector controls for profile, case-study, utility, and wildfire choices.

Named ``selector_controls`` (not ``selectors``) to avoid shadowing Python's standard-library
``selectors`` module: ``streamlit run`` puts this package directory on ``sys.path``, and a
local ``selectors.py`` would override the stdlib module that asyncio/tornado depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import streamlit as st

from core.models import CaseStudy, ProfileDefinition, Utility, Wildfire


@dataclass(frozen=True)
class SelectorState:
    """Resolved selector values from the header controls."""

    profile_key: str
    utility_id: str | None
    wildfire_id: str | None


def _case_study_label(case_study: CaseStudy) -> str:
    year = str(case_study.ignition_year) if case_study.ignition_year is not None else "year unknown"
    return (
        f"{case_study.utility_name} ({case_study.utility_state}) × "
        f"{case_study.wildfire_name} ({year})"
    )


def _wildfire_label(wildfire: Wildfire) -> str:
    ignition = (
        wildfire.ignition_date.isoformat()
        if isinstance(wildfire.ignition_date, date)
        else "unknown"
    )
    return f"{wildfire.name} ({ignition}, {wildfire.state}/{wildfire.county})"


def render_selectors(
    profiles: dict[str, ProfileDefinition],
    utilities: list[Utility],
    wildfires: list[Wildfire],
    case_studies: list[CaseStudy],
) -> SelectorState:
    """Render selectors, with a case-study choice that preselects its pair."""
    profile_col, case_study_col = st.columns([1, 3])

    profile_options = list(profiles.keys())
    with profile_col:
        profile_key = st.selectbox(
            "I am…",
            options=profile_options,
            index=0,
            format_func=lambda key: profiles[key].label,
        )

    case_study_options = {
        _case_study_label(case_study): case_study for case_study in case_studies
    }
    with case_study_col:
        selected_label = st.selectbox(
            "Choose a case study",
            options=list(case_study_options),
            index=None,
            placeholder="Select a case study",
        )
    selected = case_study_options.get(selected_label) if selected_label else None

    utility_options = {
        f"{utility.name} ({utility.state})": utility.utility_id for utility in utilities
    }
    wildfire_options = {
        _wildfire_label(wildfire): wildfire.wildfire_id for wildfire in wildfires
    }
    utility_default = (
        next(
            (
                label
                for label, utility_id in utility_options.items()
                if selected and utility_id == selected.utility_id
            ),
            None,
        )
        if selected
        else None
    )
    wildfire_default = (
        next(
            (
                label
                for label, wildfire_id in wildfire_options.items()
                if selected and wildfire_id == selected.wildfire_id
            ),
            None,
        )
        if selected
        else None
    )
    selector_key = (
        f"{selected.utility_id}_{selected.wildfire_id}" if selected else "manual"
    )
    utility_col, wildfire_col = st.columns([2, 3])
    with utility_col:
        utility_label = st.selectbox(
            "Water utility",
            options=list(utility_options),
            index=(
                list(utility_options).index(utility_default)
                if utility_default is not None
                else None
            ),
            placeholder="Select utility",
            key=f"case_study_utility_{selector_key}",
        )
    with wildfire_col:
        wildfire_label = st.selectbox(
            "Wildfire",
            options=list(wildfire_options),
            index=(
                list(wildfire_options).index(wildfire_default)
                if wildfire_default is not None
                else None
            ),
            placeholder="Select wildfire",
            key=f"case_study_wildfire_{selector_key}",
        )

    return SelectorState(
        profile_key=profile_key,
        utility_id=utility_options.get(utility_label) if utility_label else None,
        wildfire_id=wildfire_options.get(wildfire_label) if wildfire_label else None,
    )
