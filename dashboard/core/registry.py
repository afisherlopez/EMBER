"""Registry loader and validator for metrics and profile YAML configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.models import MetricDefinition, ProfileDefinition


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
        if kind not in {"scalar", "raster"}:
            raise RegistryValidationError(f"Metric `{key}` has invalid `kind`: {kind!r}.")
        default_rescale = row.get("default_rescale")
        parsed_rescale: tuple[float, float] | None = None
        if default_rescale is not None:
            if not isinstance(default_rescale, list) or len(default_rescale) != 2:
                raise RegistryValidationError(
                    f"Metric `{key}` has invalid `default_rescale`; expected [min, max]."
                )
            parsed_rescale = (float(default_rescale[0]), float(default_rescale[1]))
        metrics[key] = MetricDefinition(
            key=key,
            display_name=str(row.get("display_name", key)),
            kind=kind,
            unit=row.get("unit"),
            value_format=row.get("value_format"),
            default_colormap=row.get("default_colormap"),
            default_rescale=parsed_rescale,
        )
    return metrics


def load_profile_registry(path: Path, metrics: dict[str, MetricDefinition]) -> dict[str, ProfileDefinition]:
    """Load and validate profile definitions against known metrics."""
    payload = _read_yaml(path)
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise RegistryValidationError("`profiles.yaml` must define a non-empty `profiles` map.")

    profiles: dict[str, ProfileDefinition] = {}
    for key, row in raw_profiles.items():
        if not isinstance(row, dict):
            raise RegistryValidationError(f"Profile `{key}` must map to an object.")
        features = row.get("features", [])
        if not isinstance(features, list):
            raise RegistryValidationError(f"Profile `{key}` must define `features` as a list.")
        missing = [feature for feature in features if feature not in metrics]
        if missing:
            raise RegistryValidationError(
                f"Profile `{key}` references unknown metric(s): {', '.join(missing)}."
            )
        profiles[key] = ProfileDefinition(
            key=key,
            label=str(row.get("label", key)),
            features=[str(item) for item in features],
        )
    return profiles


def load_registries(config_dir: Path) -> tuple[dict[str, MetricDefinition], dict[str, ProfileDefinition]]:
    """Load both metrics and profiles registry files from a directory."""
    metrics = load_metric_registry(config_dir / "metrics.yaml")
    profiles = load_profile_registry(config_dir / "profiles.yaml", metrics)
    return metrics, profiles
