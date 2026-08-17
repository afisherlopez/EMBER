"""Presentation of direct and upstream utility-source connections."""

from __future__ import annotations

import streamlit as st

from core.models import UtilitySource


def render_utility_sources(
    sources: list[UtilitySource],
    *,
    compact: bool = False,
) -> None:
    """Render the source network supplied for a selected utility."""
    if not sources:
        return

    st.subheader("Surface water sources")
    st.caption(
        "Includes direct sources and upstream sources. Blue map points are approximate access locations "
        "from the original dataset (see below for source) "
        "and are not necessarily exhaustive of all sources for a given utility."
    )
    rows = [
        {
            "Source": source.source_name,
            "Type": source.source_type,
            "Connection": (
                "Direct" if source.depth == 1 else f"Upstream ({source.depth} links)"
            ),
            "Purchased": "Yes" if source.purchased else "No",
            "Reported usage": (
                f"{source.average_source_usage:.1f}%"
                if source.average_source_usage is not None
                else None
            ),
            "Usage method": source.average_source_method,
        }
        for source in sources
    ]
    if compact:
        rows = [
            {
                "Source": row["Source"],
                "Type": row["Type"],
                "Connection": row["Connection"],
            }
            for row in rows
        ]
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
    )
