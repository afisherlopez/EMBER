"""California service-area and source-connection ingest tests."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from core.catalog import Catalog
from core.storage import LocalStorage
from scripts.ingest_california import (
    build_california_data,
    build_california_fire_links,
    write_california_tables,
)
from scripts.ingest_oregon import _connect


def _write_fixture_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    boundaries = tmp_path / "ca_boundaries.json"
    boundaries.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "AGENCYNAME": "Paradise Irrigation District",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-121.66, 39.72],
                                    [-121.56, 39.72],
                                    [-121.56, 39.80],
                                    [-121.66, 39.80],
                                    [-121.66, 39.72],
                                ]
                            ],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {"AGENCYNAME": "Small Water Company"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-121.70, 39.70],
                                    [-121.68, 39.70],
                                    [-121.68, 39.72],
                                    [-121.70, 39.72],
                                    [-121.70, 39.70],
                                ]
                            ],
                        },
                    },
                    {
                        "type": "Feature",
                        "properties": {"AGENCYNAME": "Unmatched District"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-120.0, 38.0],
                                    [-119.9, 38.0],
                                    [-119.9, 38.1],
                                    [-120.0, 38.1],
                                    [-120.0, 38.0],
                                ]
                            ],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    connections = tmp_path / "ca_connections.csv"
    with connections.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "system_name",
                "pwsid",
                "source_name",
                "source_id",
                "source_type",
                "purchased",
                "average_source_usage",
                "average_source_method",
            ],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "system_name": "PARADISE IRRIGATION DISTRICT",
                    "pwsid": "CA0410007",
                    "source_name": "Magalia Reservoir",
                    "source_id": "source_337",
                    "source_type": "Lake",
                    "purchased": "0",
                    "average_source_usage": "100",
                    "average_source_method": "calculated",
                },
                {
                    "system_name": "SMALL WATER COMPANY",
                    "pwsid": "CA0410999",
                    "source_name": "CA0410007",
                    "source_id": "CA0410007",
                    "source_type": "PWS",
                    "purchased": "1",
                    "average_source_usage": "75",
                    "average_source_method": "calculated",
                },
            ]
        )
    source_points = tmp_path / "source_points.geojson"
    source_points.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"sorc_nm": "Magalia Reservoir"},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [-121.5888, 39.8219],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    system_points = tmp_path / "system_points.geojson"
    system_points.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"pwsid": "CA0410007"},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [-121.61, 39.76],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return boundaries, connections, source_points, system_points


def test_california_ingest_matches_service_areas_and_follows_sources(
    tmp_path: Path,
) -> None:
    boundaries, connections, source_points, system_points = _write_fixture_sources(
        tmp_path
    )
    conn = _connect()

    build_california_data(
        conn,
        boundaries.as_posix(),
        connections.as_posix(),
        source_points.as_posix(),
        system_points.as_posix(),
    )

    utilities = conn.execute(
        """
        SELECT utility_id, name, geom IS NULL, service_geom IS NOT NULL
        FROM utilities_california
        ORDER BY utility_id
        """
    ).fetchall()
    assert utilities == [
        ("ca0410007", "Paradise Irrigation District", True, True),
        ("ca0410999", "Small Water Company", True, True),
    ]
    supplier = conn.execute(
        """
        SELECT source_name, source_utility_id
        FROM utility_sources
        WHERE utility_id = 'ca0410999'
        """
    ).fetchone()
    assert supplier == ("PARADISE IRRIGATION DISTRICT", "ca0410007")

    conn.execute(
        """
        CREATE TEMP TABLE fires_raw AS
        SELECT * FROM (
            VALUES
                (
                    'service-fire',
                    ST_GeomFromText(
                        'POLYGON((-121.70 39.70,-121.68 39.70,-121.68 39.72,'
                        || '-121.70 39.72,-121.70 39.70))'
                    )
                ),
                (
                    'source-fire',
                    ST_GeomFromText(
                        'POLYGON((-121.60 39.81,-121.58 39.81,-121.58 39.83,'
                        || '-121.60 39.83,-121.60 39.81))'
                    )
                )
        ) AS t(wildfire_id, geom)
        """
    )
    build_california_fire_links(conn)
    fire_links = conn.execute(
        """
        SELECT wildfire_id, impact_basis
        FROM ca_fire_pairs
        WHERE utility_id = 'ca0410999'
        ORDER BY wildfire_id
        """
    ).fetchall()
    assert fire_links == [
        ("service-fire", "service_area"),
        ("source-fire", "source_location"),
    ]
    source_fire_locations = conn.execute(
        """
        SELECT source_name, depth, source_lon, source_lat
        FROM ca_source_fire_matches
        WHERE utility_id = 'ca0410999'
          AND wildfire_id = 'source-fire'
        """
    ).fetchall()
    assert source_fire_locations == [
        ("Magalia Reservoir", 2, -121.5888, 39.8219)
    ]

    write_california_tables(conn, tmp_path / "tables")
    catalog = Catalog(LocalStorage(tmp_path))
    sources = catalog.list_utility_sources("ca0410999")
    assert [(source.source_name, source.depth) for source in sources] == [
        ("PARADISE IRRIGATION DISTRICT", 1),
        ("Magalia Reservoir", 2),
    ]
    assert [(source.longitude, source.latitude) for source in sources] == [
        (-121.61, 39.76),
        (-121.5888, 39.8219),
    ]
