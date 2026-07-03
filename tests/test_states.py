"""Tests for the three-state rendering contract used by app and export."""

from core.states import resolve_state, state_message


def test_resolve_state_truth_table() -> None:
    """Resolve state should follow no_impact > pending > available precedence."""
    assert resolve_state(has_overlap=False, payload=None) == "no_impact"
    assert resolve_state(has_overlap=False, payload={"value": 3}) == "no_impact"
    assert resolve_state(has_overlap=True, payload=None) == "pending"
    assert resolve_state(has_overlap=True, payload={"value": 3}) == "available"


def test_state_messages_exact_strings() -> None:
    """Display text for no impact and pending states must match spec wording."""
    assert state_message("no_impact") == "No direct impact"
    assert state_message("pending") == "Data not yet available"
    assert state_message("available") is None
