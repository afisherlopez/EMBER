"""Selector controls for utility-wide case studies."""

from __future__ import annotations

import streamlit as st

from core.models import CaseStudy


def _case_study_label(case_study: CaseStudy) -> str:
    return f"{case_study.utility_name} ({case_study.utility_state})"


def render_case_study_selector(case_studies: list[CaseStudy]) -> str | None:
    """Select one utility-wide case study and return its utility identifier."""
    options = {
        _case_study_label(case_study): case_study.utility_id
        for case_study in case_studies
    }
    selected_label = st.selectbox(
        "Choose a case study",
        options=list(options),
        index=None,
        placeholder="Select a utility case study",
    )
    return options.get(selected_label) if selected_label else None
