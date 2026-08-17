"""Load California utility service areas and surface-water source connections."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest_oregon import AREA_CRS, DISPLAY_SIMPLIFY_DEG, _connect  # noqa: E402

DEFAULT_CA_BOUNDARIES = (
    "gs://data_main_gcs/EMBER/water_source_areas/"
    "CA_Water_District_Boundaries.json"
)
DEFAULT_CA_CONNECTIONS = (
    "gs://data_main_gcs/EMBER/water_source_areas/"
    "CA_Source_Connections_Data/Final_dryad_data_V2.csv"
)
DEFAULT_CA_SOURCE_POINTS = (
    "https://services2.arcgis.com/iq8zYa0SRsvIFFKz/arcgis/rest/services/"
    "Surface_Water_Sources/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson"
)
DEFAULT_CA_SYSTEM_POINTS = (
    "https://services2.arcgis.com/iq8zYa0SRsvIFFKz/arcgis/rest/services/"
    "Community_Public_Water_Systems/FeatureServer/0/query"
    "?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson"
)


def resolve_data_file(source: str) -> Path:
    """Resolve an exact local, HTTP, or GCS object path to a local file."""
    if source.startswith(("http://", "https://")):
        import requests

        response = requests.get(source, timeout=60)
        response.raise_for_status()
        destination = (
            Path(tempfile.mkdtemp(prefix="california_sources_"))
            / "arcgis_points.geojson"
        )
        destination.write_bytes(response.content)
        return destination
    if not source.startswith("gs://"):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(source)
        return path

    import gcsfs

    remote = source[len("gs://") :]
    destination = Path(tempfile.mkdtemp(prefix="california_sources_")) / Path(remote).name
    gcsfs.GCSFileSystem().get(remote, destination.as_posix())
    return destination


def build_california_data(
    conn: duckdb.DuckDBPyConnection,
    boundaries_json: str,
    connections_csv: str,
    source_points_json: str | None = None,
    system_points_json: str | None = None,
) -> None:
    """Register matched California utilities and all direct source edges.

    The boundary file has agency names but no PWSIDs, while the connection file
    has PWSIDs and system names. Only unambiguous normalized-name matches become
    selectable utilities; all source edges remain available so supplier links can
    be followed transitively.
    """
    boundaries_sql = boundaries_json.replace("'", "''")
    connections_sql = connections_csv.replace("'", "''")
    if source_points_json:
        source_points_sql = source_points_json.replace("'", "''")
        conn.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE ca_source_points AS
            SELECT
                trim(sorc_nm) AS source_name,
                any_value(geom) AS source_geom
            FROM ST_Read('{source_points_sql}')
            WHERE sorc_nm IS NOT NULL
              AND trim(sorc_nm) <> ''
              AND geom IS NOT NULL
            GROUP BY trim(sorc_nm)
            """
        )
    else:
        conn.execute(
            """
            CREATE OR REPLACE TEMP TABLE ca_source_points AS
            SELECT
                CAST(NULL AS VARCHAR) AS source_name,
                CAST(NULL AS GEOMETRY) AS source_geom
            WHERE FALSE
            """
        )
    if system_points_json:
        system_points_sql = system_points_json.replace("'", "''")
        conn.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE ca_system_points AS
            SELECT
                lower(trim(pwsid)) AS utility_id,
                any_value(geom) AS system_geom
            FROM ST_Read('{system_points_sql}')
            WHERE pwsid IS NOT NULL
              AND trim(pwsid) <> ''
              AND geom IS NOT NULL
            GROUP BY lower(trim(pwsid))
            """
        )
    else:
        conn.execute(
            """
            CREATE OR REPLACE TEMP TABLE ca_system_points AS
            SELECT
                CAST(NULL AS VARCHAR) AS utility_id,
                CAST(NULL AS GEOMETRY) AS system_geom
            WHERE FALSE
            """
        )
    normalized_agency = (
        "lower(regexp_replace(replace(trim(AGENCYNAME), '&', ' and '), "
        "'[^a-zA-Z0-9]+', '', 'g'))"
    )
    normalized_system = (
        "lower(regexp_replace(replace(trim(system_name), '&', ' and '), "
        "'[^a-zA-Z0-9]+', '', 'g'))"
    )
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE ca_source_rows AS
        SELECT *
        FROM read_csv(
            '{connections_sql}',
            header = true,
            all_varchar = true,
            auto_detect = true
        );

        CREATE OR REPLACE TEMP TABLE ca_systems AS
        SELECT
            trim(pwsid) AS pwsid,
            any_value(trim(system_name)) AS system_name,
            {normalized_system} AS normalized_name
        FROM ca_source_rows
        WHERE pwsid IS NOT NULL
          AND trim(pwsid) <> ''
          AND system_name IS NOT NULL
          AND trim(system_name) <> ''
        GROUP BY trim(pwsid), {normalized_system};

        CREATE OR REPLACE TEMP TABLE ca_boundaries AS
        SELECT
            trim(AGENCYNAME) AS agency_name,
            {normalized_agency} AS normalized_name,
            ST_MakeValid(geom) AS service_geom
        FROM ST_Read('{boundaries_sql}')
        WHERE AGENCYNAME IS NOT NULL
          AND trim(AGENCYNAME) <> ''
          AND geom IS NOT NULL;

        CREATE OR REPLACE TEMP TABLE ca_unambiguous_systems AS
        SELECT any_value(pwsid) AS pwsid,
               any_value(system_name) AS system_name,
               normalized_name
        FROM ca_systems
        GROUP BY normalized_name
        HAVING count(DISTINCT pwsid) = 1;

        CREATE OR REPLACE TEMP TABLE utilities_california AS
        SELECT
            lower(system.pwsid) AS utility_id,
            any_value(boundary.agency_name) AS name,
            'CA' AS state,
            string_agg(
                DISTINCT nullif(trim(source.source_name), ''),
                '; '
                ORDER BY nullif(trim(source.source_name), '')
            ) AS source_area_name,
            CAST(NULL AS GEOMETRY) AS geom,
            ST_MakeValid(ST_Union_Agg(boundary.service_geom)) AS service_geom
        FROM ca_unambiguous_systems system
        JOIN ca_boundaries boundary USING (normalized_name)
        LEFT JOIN ca_source_rows source
          ON trim(source.pwsid) = system.pwsid
        GROUP BY system.pwsid;

        CREATE OR REPLACE TEMP TABLE utility_sources AS
        SELECT
            lower(trim(source.pwsid)) AS utility_id,
            trim(source.source_id) AS source_id,
            coalesce(
                nullif(trim(supplier.system_name), ''),
                nullif(trim(source.source_name), ''),
                trim(source.source_id)
            ) AS source_name,
            trim(source.source_type) AS source_type,
            CASE
                WHEN supplier.pwsid IS NOT NULL THEN lower(supplier.pwsid)
            END AS source_utility_id,
            coalesce(try_cast(source.purchased AS INTEGER), 0) = 1 AS purchased,
            try_cast(source.average_source_usage AS DOUBLE) AS average_source_usage,
            nullif(trim(source.average_source_method), '') AS average_source_method,
            coalesce(
                ST_X(source_point.source_geom),
                ST_X(system_point.system_geom)
            ) AS source_lon,
            coalesce(
                ST_Y(source_point.source_geom),
                ST_Y(system_point.system_geom)
            ) AS source_lat
        FROM ca_source_rows source
        LEFT JOIN ca_systems supplier
          ON trim(source.source_id) = supplier.pwsid
        LEFT JOIN ca_source_points source_point
          ON trim(source.source_name) = source_point.source_name
        LEFT JOIN ca_system_points system_point
          ON lower(trim(source.source_id)) = system_point.utility_id
        WHERE source.pwsid IS NOT NULL
          AND trim(source.pwsid) <> ''
          AND source.source_id IS NOT NULL
          AND trim(source.source_id) <> '';
        """
    )
    matched = conn.execute("SELECT count(*) FROM utilities_california").fetchone()[0]
    if not matched:
        raise ValueError(
            "California boundary and connection datasets produced no unambiguous "
            "utility-name matches."
        )


def build_california_fire_links(conn: duckdb.DuckDBPyConnection) -> None:
    """Relate California utilities to fires affecting service or source locations."""
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE ca_source_network AS
        WITH RECURSIVE source_network AS (
            SELECT
                root.utility_id AS root_utility_id,
                source.source_id,
                source.source_name,
                source.source_type,
                source.source_utility_id,
                source.source_lon,
                source.source_lat,
                1 AS depth,
                [
                    source.utility_id,
                    coalesce(source.source_utility_id, source.source_id)
                ] AS path
            FROM utilities_california root
            JOIN utility_sources source
              ON source.utility_id = root.utility_id

            UNION ALL

            SELECT
                network.root_utility_id,
                upstream.source_id,
                upstream.source_name,
                upstream.source_type,
                upstream.source_utility_id,
                upstream.source_lon,
                upstream.source_lat,
                network.depth + 1,
                list_append(
                    network.path,
                    coalesce(upstream.source_utility_id, upstream.source_id)
                )
            FROM source_network network
            JOIN utility_sources upstream
              ON upstream.utility_id = network.source_utility_id
            WHERE network.source_utility_id IS NOT NULL
              AND network.depth < 20
              AND NOT list_contains(
                  network.path,
                  coalesce(upstream.source_utility_id, upstream.source_id)
              )
        )
        SELECT * FROM source_network;

        CREATE OR REPLACE TEMP TABLE ca_source_fire_matches AS
        SELECT
            network.root_utility_id AS utility_id,
            fire.wildfire_id,
            network.source_id,
            network.source_name,
            network.source_type,
            network.depth,
            network.source_lon,
            network.source_lat
        FROM ca_source_network network
        JOIN fires_raw fire
          ON ST_Intersects(
              fire.geom,
              ST_Point(network.source_lon, network.source_lat)
          )
        WHERE network.source_lon IS NOT NULL
          AND network.source_lat IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY
                network.root_utility_id,
                fire.wildfire_id,
                network.source_id
            ORDER BY network.depth
        ) = 1;

        CREATE OR REPLACE TEMP TABLE ca_fire_pairs AS
        WITH
        service_matches AS (
            SELECT
                utility.utility_id,
                fire.wildfire_id,
                'service_area' AS impact_basis,
                ST_Area(
                    ST_Intersection(
                        ST_Transform(
                            utility.service_geom,
                            'EPSG:4326',
                            '{AREA_CRS}',
                            always_xy := true
                        ),
                        ST_Transform(
                            fire.geom,
                            'EPSG:4326',
                            '{AREA_CRS}',
                            always_xy := true
                        )
                    )
                ) / 1.0e6 AS overlap_area_km2
            FROM utilities_california utility
            JOIN fires_raw fire
              ON ST_Intersects(utility.service_geom, fire.geom)
        ),
        source_matches AS (
            SELECT DISTINCT
                utility_id,
                wildfire_id,
                'source_location' AS impact_basis,
                CAST(NULL AS DOUBLE) AS overlap_area_km2
            FROM ca_source_fire_matches
        ),
        impacts AS (
            SELECT * FROM service_matches
            UNION ALL
            SELECT * FROM source_matches
        )
        SELECT
            utility_id,
            wildfire_id,
            TRUE AS has_overlap,
            max(overlap_area_km2) AS overlap_area_km2,
            CAST(NULL AS DOUBLE) AS overlap_pct_of_source,
            string_agg(
                DISTINCT impact_basis,
                ','
                ORDER BY impact_basis
            ) AS impact_basis,
            now() AS updated_at
        FROM impacts
        GROUP BY utility_id, wildfire_id
        """
    )


def write_california_tables(
    conn: duckdb.DuckDBPyConnection,
    tables_dir: Path,
) -> None:
    """Write standalone California utility and source-connection tables."""
    tables_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(
        f"""
        COPY (
            SELECT
                utility_id,
                name,
                state,
                source_area_name,
                CAST(NULL AS JSON) AS geometry_geojson,
                CAST(
                    ST_AsGeoJSON(ST_Simplify(service_geom, {DISPLAY_SIMPLIFY_DEG}))
                    AS JSON
                ) AS service_area_geojson,
                ST_X(ST_Centroid(service_geom)) AS centroid_lon,
                ST_Y(ST_Centroid(service_geom)) AS centroid_lat,
                now() AS updated_at
            FROM utilities_california
        ) TO '{(tables_dir / "utilities.parquet").as_posix()}' (FORMAT PARQUET);

        COPY (
            SELECT *
            FROM utility_sources
        ) TO '{(tables_dir / "utility_sources.parquet").as_posix()}' (FORMAT PARQUET);
        """
    )


def ingest(
    boundaries: str,
    connections: str,
    source_points: str,
    system_points: str,
    data_root: Path,
) -> None:
    """Build standalone California utility and source tables."""
    resolved_boundaries = resolve_data_file(boundaries)
    resolved_connections = resolve_data_file(connections)
    resolved_source_points = resolve_data_file(source_points)
    resolved_system_points = resolve_data_file(system_points)
    conn = _connect()
    build_california_data(
        conn,
        resolved_boundaries.as_posix(),
        resolved_connections.as_posix(),
        resolved_source_points.as_posix(),
        resolved_system_points.as_posix(),
    )
    write_california_tables(conn, data_root / "tables")
    utility_count = conn.execute(
        "SELECT count(*) FROM utilities_california"
    ).fetchone()[0]
    source_count = conn.execute("SELECT count(*) FROM utility_sources").fetchone()[0]
    print(
        f"Wrote {utility_count:,} matched California service areas and "
        f"{source_count:,} source connections.",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest California service areas and utility-source connections."
    )
    parser.add_argument("--boundaries", default=DEFAULT_CA_BOUNDARIES)
    parser.add_argument("--connections", default=DEFAULT_CA_CONNECTIONS)
    parser.add_argument("--source-points", default=DEFAULT_CA_SOURCE_POINTS)
    parser.add_argument("--system-points", default=DEFAULT_CA_SYSTEM_POINTS)
    parser.add_argument("--data-root", default="./data/published-ca")
    args = parser.parse_args()
    ingest(
        args.boundaries,
        args.connections,
        args.source_points,
        args.system_points,
        Path(args.data_root),
    )
