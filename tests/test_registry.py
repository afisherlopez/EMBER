"""Tests for metric/profile registry parsing and cross-reference validation."""

from pathlib import Path

import pytest

from core.registry import RegistryValidationError, load_metric_registry, load_profile_registry


def test_registry_loads_sample_config() -> None:
    """Repository config files should parse and cross-reference cleanly."""
    base = Path(__file__).resolve().parents[1] / "config"
    metrics = load_metric_registry(base / "metrics.yaml")
    profiles = load_profile_registry(base / "profiles.yaml", metrics)
    assert "total_econ_impact" in metrics
    assert "water_utility" in profiles


def test_registry_rejects_unknown_metric_reference(tmp_path: Path) -> None:
    """Profiles referencing unknown metrics should raise explicit validation errors."""
    metrics_file = tmp_path / "metrics.yaml"
    profiles_file = tmp_path / "profiles.yaml"
    metrics_file.write_text(
        "metrics:\n  known:\n    display_name: Known\n    kind: scalar\n    unit: USD\n",
        encoding="utf-8",
    )
    profiles_file.write_text(
        "profiles:\n  demo:\n    label: Demo\n    features: [known, unknown]\n",
        encoding="utf-8",
    )
    metrics = load_metric_registry(metrics_file)
    with pytest.raises(RegistryValidationError):
        load_profile_registry(profiles_file, metrics)
