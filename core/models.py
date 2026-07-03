"""Typed records used by the catalog and app modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class Utility:
    """Utility list row for selector metadata and map labeling."""

    utility_id: str
    name: str
    state: str
    source_area_name: str
    centroid_lon: float
    centroid_lat: float


@dataclass(frozen=True)
class Wildfire:
    """Wildfire list row for selector metadata and map labeling."""

    wildfire_id: str
    name: str
    ignition_date: date | None
    containment_date: date | None
    state: str
    county: str
    centroid_lon: float
    centroid_lat: float


@dataclass(frozen=True)
class PairSummary:
    """Overlap summary for one utility-wildfire pair."""

    utility_id: str
    wildfire_id: str
    has_overlap: bool
    overlap_area_km2: float | None
    overlap_pct_of_source: float | None


@dataclass(frozen=True)
class MetricValue:
    """Scalar metric payload for one utility-wildfire pair."""

    utility_id: str
    wildfire_id: str
    metric_key: str
    value: float | None
    unit: str | None
    method: str | None
    source_note: str | None
    as_of_date: date | None


@dataclass(frozen=True)
class RasterAsset:
    """Raster metric asset descriptor used to build tile layer requests."""

    utility_id: str
    wildfire_id: str
    metric_key: str
    cog_uri: str
    units: str | None
    colormap_name: str | None
    rescale_min: float | None
    rescale_max: float | None
    nodata: float | None
    as_of_date: date | None


@dataclass(frozen=True)
class IntersectingWildfire:
    """A wildfire that overlaps a selected utility's source area, with overlap facts.

    Used by the utility x year-range view to both list fires and draw their perimeters,
    so ``geometry_geojson`` is included to avoid a second per-fire geometry lookup.
    """

    wildfire_id: str
    name: str
    ignition_year: int | None
    acres: float | None
    overlap_area_km2: float | None
    overlap_pct_of_source: float | None
    geometry_geojson: str


@dataclass(frozen=True)
class WildfireSummary:
    """Header facts for one wildfire, including total burned acreage."""

    wildfire_id: str
    name: str
    ignition_year: int | None
    acres: float | None
    state: str


@dataclass(frozen=True)
class IntersectingUtility:
    """A utility whose source area overlaps a selected wildfire, with overlap facts.

    Mirror of ``IntersectingWildfire`` for the "utilities by fire" view. ``ignition_year``
    is the selected fire's year (constant across rows) and is carried per row so the table
    can show it alongside the overlap stats.
    """

    utility_id: str
    name: str
    state: str
    source_area_name: str
    ignition_year: int | None
    overlap_area_km2: float | None
    overlap_pct_of_source: float | None
    geometry_geojson: str


@dataclass(frozen=True)
class MetricDefinition:
    """Metric registry entry loaded from `config/metrics.yaml`."""

    key: str
    display_name: str
    kind: Literal["scalar", "raster"]
    unit: str | None = None
    value_format: str | None = None
    default_colormap: str | None = None
    default_rescale: tuple[float, float] | None = None


@dataclass(frozen=True)
class ProfileDefinition:
    """Profile registry entry loaded from `config/profiles.yaml`."""

    key: str
    label: str
    features: list[str]
