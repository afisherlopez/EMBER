"""Tests for shared MTBS CONUS perimeter ingestion."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from scripts.ingest_mtbs import _resolve_shapefile, _select_shapefile, build_wildfires
from scripts.ingest_oregon import _connect


def _feature(
    event_id: str,
    *,
    incident_type: str = "Wildfire",
    name: str = "Shared Name",
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "event_id": event_id,
            "incid_name": name,
            "ig_date": "2020-08-15",
            "burnbndac": 1234.5,
            "incid_type": incident_type,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[-121.0, 44.0], [-120.0, 44.0], [-120.0, 45.0], [-121.0, 44.0]]],
        },
    }


def test_build_wildfires_filters_conus_rows_and_preserves_state(tmp_path: Path) -> None:
    """One national source should produce only requested true-wildfire states."""
    source = tmp_path / "mtbs.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    _feature("OR123"),
                    _feature("WA456"),
                    _feature("NY789"),
                    _feature("CA012", incident_type="Prescribed Fire"),
                ],
            }
        )
    )
    conn = _connect()

    build_wildfires(conn, source.as_posix(), state_codes=("OR", "WA", "CA"))

    rows = conn.execute(
        "SELECT wildfire_id, state FROM fires_raw ORDER BY state"
    ).fetchall()
    assert rows == [("shared-name-2020", "OR"), ("wa-shared-name-2020", "WA")]


def test_select_shapefile_prefers_standard_mtbs_name() -> None:
    """A versioned folder may contain ancillary shapefiles beside the MTBS source."""
    selected = _select_shapefile(
        [
            "bucket/upload/index_grid.shp",
            "bucket/upload/mtbs_perims_DD.shp",
        ],
        "gs://bucket/upload",
    )
    assert selected.endswith("mtbs_perims_DD.shp")


def test_resolve_shapefile_extracts_conus_zip_sidecars(tmp_path: Path) -> None:
    """The uploaded national vector ZIP should be usable without manual extraction."""
    archive_path = tmp_path / "mtbs_perimeter_data.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("mtbs_perims_DD.shp", b"shape")
        archive.writestr("mtbs_perims_DD.dbf", b"attributes")
        archive.writestr("mtbs_perims_DD.shx", b"index")

    resolved = Path(_resolve_shapefile(archive_path.as_posix()))

    assert resolved.name == "mtbs_perims_DD.shp"
    assert resolved.read_bytes() == b"shape"
    assert (resolved.parent / "mtbs_perims_DD.dbf").read_bytes() == b"attributes"
