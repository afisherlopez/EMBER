"""Registry loader and validator for scalar metric YAML configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.models import MetricDefinition


class RegistryValidationError(ValueError):
    """Raised when registry files are malformed or cross references are invalid."""


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise RegistryValidationError(f"Missing registry file: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RegistryValidationError(f"Registry file must contain a map: {path}")
    return payload


def load_metric_registry(path: Path) -> dict[str, MetricDefinition]:
    """Load and validate metric definitions from YAML."""
    payload = _read_yaml(path)
    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, dict) or not raw_metrics:
        raise RegistryValidationError("`metrics.yaml` must define a non-empty `metrics` map.")

    metrics: dict[str, MetricDefinition] = {}
    for key, row in raw_metrics.items():
        if not isinstance(row, dict):
            raise RegistryValidationError(f"Metric `{key}` must map to an object.")
        kind = row.get("kind")
        if kind != "scalar":
            raise RegistryValidationError(f"Metric `{key}` has invalid `kind`: {kind!r}.")
        metrics[key] = MetricDefinition(
            key=key,
            display_name=str(row.get("display_name", key)),
            kind=kind,
            unit=row.get("unit"),
            value_format=row.get("value_format"),
        )
    return metrics
