"""Rebuild `pair_summary` overlap facts from utility and wildfire geometries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb


def _bbox_from_geojson(geometry_geojson: str) -> tuple[float, float, float, float]:
    """Extract bbox tuple `(min_lon, min_lat, max_lon, max_lat)` from polygon GeoJSON."""
    geometry = json.loads(geometry_geojson)
    points = geometry["coordinates"][0]
    lons = [float(point[0]) for point in points]
    lats = [float(point[1]) for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def _bbox_overlap_area_km2(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    """Approximate intersection area for axis-aligned bboxes in km^2."""
    min_lon = max(a[0], b[0])
    min_lat = max(a[1], b[1])
    max_lon = min(a[2], b[2])
    max_lat = min(a[3], b[3])
    if max_lon <= min_lon or max_lat <= min_lat:
        return 0.0
    # Lightweight approximation for manifest generation; full geodesic overlap is offline pipeline scope.
    km_per_degree_lat = 111.0
    km_per_degree_lon = 85.0
    return (max_lon - min_lon) * km_per_degree_lon * (max_lat - min_lat) * km_per_degree_lat


def build_pair_summary(data_root: Path) -> None:
    """Compute utility x wildfire overlap table and write `pair_summary.parquet`.

    This implementation uses bbox overlap from GeoJSON polygons, sufficient for local app fixtures.
    """
    conn = duckdb.connect(database=":memory:")
    utilities = (data_root / "tables" / "utilities.parquet").as_posix()
    wildfires = (data_root / "tables" / "wildfires.parquet").as_posix()
    out_path = (data_root / "tables" / "pair_summary.parquet").as_posix()
    utility_rows = conn.execute(
        f"SELECT utility_id, geometry_geojson FROM read_parquet('{utilities}')"
    ).fetchall()
    wildfire_rows = conn.execute(
        f"SELECT wildfire_id, geometry_geojson FROM read_parquet('{wildfires}')"
    ).fetchall()

    records: list[tuple[str, str, bool, float | None, float | None, str]] = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for utility_id, utility_geojson in utility_rows:
        utility_bbox = _bbox_from_geojson(utility_geojson)
        utility_area = _bbox_overlap_area_km2(utility_bbox, utility_bbox)
        for wildfire_id, wildfire_geojson in wildfire_rows:
            wildfire_bbox = _bbox_from_geojson(wildfire_geojson)
            overlap_area = _bbox_overlap_area_km2(utility_bbox, wildfire_bbox)
            has_overlap = overlap_area > 0.0
            overlap_pct = (overlap_area / utility_area * 100.0) if has_overlap and utility_area > 0 else None
            records.append(
                (
                    utility_id,
                    wildfire_id,
                    has_overlap,
                    overlap_area if has_overlap else None,
                    overlap_pct,
                    now_iso,
                )
            )

    conn.execute(
        "CREATE TABLE pair_summary (utility_id TEXT, wildfire_id TEXT, has_overlap BOOLEAN, overlap_area_km2 DOUBLE, overlap_pct_of_source DOUBLE, updated_at TIMESTAMP)"
    )
    conn.executemany("INSERT INTO pair_summary VALUES (?, ?, ?, ?, ?, ?)", records)
    conn.execute(f"COPY pair_summary TO '{out_path}' (FORMAT PARQUET)")


if __name__ == "__main__":
    build_pair_summary(Path("./data"))
