"""Tests for scalar metric registry parsing and validation."""

from pathlib import Path

import pytest

from core.registry import RegistryValidationError, load_metric_registry


def test_registry_loads_sample_config() -> None:
    """Repository scalar metric config should parse cleanly."""
    base = Path(__file__).resolve().parents[1] / "config"
    metrics = load_metric_registry(base / "metrics.yaml")
    assert "total_econ_impact" in metrics
    assert "pre_fire_annual_operating_revenue" in metrics
    assert all(metric.kind == "scalar" for metric in metrics.values())


def test_registry_rejects_non_scalar_metric(tmp_path: Path) -> None:
    """The retired generic raster metric kind should be rejected."""
    metrics_file = tmp_path / "metrics.yaml"
    metrics_file.write_text(
        "metrics:\n  old_raster:\n    display_name: Old raster\n    kind: raster\n",
        encoding="utf-8",
    )

    with pytest.raises(RegistryValidationError):
        load_metric_registry(metrics_file)
