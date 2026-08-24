"""Ingest Washington DOH drinking-water watersheds and MTBS fires into EMBER.

The DOH source dataset is an Esri File Geodatabase. This script keeps only
``AreaType = 'Full'`` watershed rows, dissolves them to one row per water system,
then computes utility x wildfire overlaps using Washington MTBS perimeters.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest_mtbs import (
    WILDFIRE_INCIDENT_TYPES,
    _resolve_shapefile,
    build_wildfires,
)

DISPLAY_SIMPLIFY_DEG = 0.0005
AREA_CRS = "EPSG:5070"
SOURCE_CRS = "EPSG:3857"
STATE_CODE = "WA"
DEFAULT_DOH_GDB = (
    "gs://data_main_gcs/EMBER/water_source_areas/"
    "DOH Drinking Water Full Watershed.gdb"
)


def _connect() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(database=":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    return conn


def _resolve_doh_gdb(path: str) -> Path:
    """Resolve a local or GCS DOH FileGDB to a local directory."""
    if path.startswith("gs://"):
        import gcsfs

        fs = gcsfs.GCSFileSystem()
        remote_root = path[len("gs://") :].rstrip("/")
        tmp_root = Path(tempfile.mkdtemp(prefix="doh_wa_"))
        local_gdb = tmp_root / Path(remote_root).name
        local_gdb.mkdir()
        remote_files = fs.find(remote_root)
        if not remote_files:
            raise FileNotFoundError(f"No FileGDB objects found under {path}")
        for remote in remote_files:
            relative = remote.removeprefix(f"{remote_root}/")
            local_file = local_gdb / relative
            local_file.parent.mkdir(parents=True, exist_ok=True)
            fs.get(remote, local_file.as_posix())
        return local_gdb

    candidates = [
        Path(path),
        Path("./DOH Drinking Water Full Watershed.gdb"),
        Path("./data/raw/DOH Drinking Water Full Watershed.gdb"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    searched = ", ".join(candidate.as_posix() for candidate in candidates)
    raise FileNotFoundError(f"Could not find DOH FileGDB. Checked: {searched}")


def build_utilities(conn: duckdb.DuckDBPyConnection, doh_gdb: str) -> None:
    """Dissolve full DOH watershed rows to one utility geometry per PWS ID."""
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE util_diss AS
        SELECT
            'wa' || lower(PwsId) AS utility_id,
            any_value(SystemName) AS name,
            '{STATE_CODE}' AS state,
            string_agg(DISTINCT nullif(trim(SrcName), ''), '; ') AS source_area_name,
            ST_MakeValid(
                ST_Union_Agg(
                    ST_Transform(Shape, '{SOURCE_CRS}', 'EPSG:4326', always_xy := true)
                )
            ) AS geom,
            CAST(NULL AS GEOMETRY) AS service_geom
        FROM ST_Read('{doh_gdb}')
        WHERE PwsId IS NOT NULL
          AND trim(PwsId) <> ''
          AND upper(trim(SrcStatusIndDesc)) = 'ACTIVE'
          AND upper(trim(AreaType)) = 'FULL'
        GROUP BY lower(PwsId)
        """
    )


def write_utilities(conn: duckdb.DuckDBPyConnection, tables_dir: Path) -> Path:
    """Write the Washington utilities table and return its path."""
    tables_dir.mkdir(parents=True, exist_ok=True)
    utilities_path = (tables_dir / "utilities.parquet").as_posix()
    conn.execute(
        f"""
        COPY (
            SELECT
                utility_id, name, state, source_area_name,
                CAST(ST_AsGeoJSON(ST_Simplify(geom, {DISPLAY_SIMPLIFY_DEG})) AS JSON) AS geometry_geojson,
                CAST(
                    ST_AsGeoJSON(ST_Simplify(service_geom, {DISPLAY_SIMPLIFY_DEG}))
                    AS JSON
                ) AS service_area_geojson,
                ST_X(ST_Centroid(coalesce(service_geom, geom))) AS centroid_lon,
                ST_Y(ST_Centroid(coalesce(service_geom, geom))) AS centroid_lat,
                now() AS updated_at
            FROM util_diss
        ) TO '{utilities_path}' (FORMAT PARQUET)
        """
    )
    return Path(utilities_path)


def write_tables(conn: duckdb.DuckDBPyConnection, tables_dir: Path) -> None:
    """Write standalone EMBER tables for utilities, fires, overlaps, and placeholders."""
    write_utilities(conn, tables_dir)
    wildfires_path = (tables_dir / "wildfires.parquet").as_posix()
    pair_path = (tables_dir / "pair_summary.parquet").as_posix()

    conn.execute(
        f"""
        COPY (
            SELECT
                wildfire_id,
                name,
                ignition_date,
                CAST(NULL AS DATE) AS containment_date,
                acres,
                state,
                '' AS county,
                ST_X(ST_Centroid(geom)) AS centroid_lon,
                ST_Y(ST_Centroid(geom)) AS centroid_lat,
                CAST(ST_AsGeoJSON(ST_Simplify(geom, {DISPLAY_SIMPLIFY_DEG})) AS JSON) AS geometry_geojson,
                source,
                now() AS updated_at
            FROM fires_raw
        ) TO '{wildfires_path}' (FORMAT PARQUET)
        """
    )

    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE util_proj AS
        SELECT utility_id,
               ST_Transform(geom, 'EPSG:4326', '{AREA_CRS}', always_xy := true) AS g
        FROM util_diss
        WHERE geom IS NOT NULL;
        CREATE OR REPLACE TEMP TABLE fire_proj AS
        SELECT wildfire_id,
               ST_Transform(geom, 'EPSG:4326', '{AREA_CRS}', always_xy := true) AS g
        FROM fires_raw;
        """
    )
    conn.execute(
        f"""
        COPY (
            WITH pairs AS (
                SELECT u.utility_id, f.wildfire_id,
                       ST_Area(ST_Intersection(u.g, f.g)) AS inter_m2,
                       ST_Area(u.g) AS util_m2
                FROM util_proj u
                JOIN fire_proj f ON ST_Intersects(u.g, f.g)
            )
            SELECT
                utility_id, wildfire_id,
                TRUE AS has_overlap,
                inter_m2 / 1.0e6 AS overlap_area_km2,
                CASE WHEN util_m2 > 0 THEN inter_m2 / util_m2 * 100.0 END AS overlap_pct_of_source,
                now() AS updated_at
            FROM pairs
            WHERE inter_m2 > 0
        ) TO '{pair_path}' (FORMAT PARQUET)
        """
    )

    conn.execute(
        f"""
        COPY (
            SELECT
                CAST(NULL AS VARCHAR) AS utility_id, CAST(NULL AS VARCHAR) AS wildfire_id,
                CAST(NULL AS VARCHAR) AS metric_key, CAST(NULL AS DOUBLE) AS value,
                CAST(NULL AS VARCHAR) AS unit, CAST(NULL AS VARCHAR) AS method,
                CAST(NULL AS VARCHAR) AS source_note, CAST(NULL AS DATE) AS as_of_date
            WHERE FALSE
        ) TO '{(tables_dir / "scalar_metrics.parquet").as_posix()}' (FORMAT PARQUET)
        """
    )
def ingest(doh_gdb: str, data_root: Path, fires_shp: str | None = None) -> None:
    """Run the Washington ingest from DOH/MTBS sources to EMBER Parquet tables."""
    conn = _connect()
    resolved_doh_gdb = _resolve_doh_gdb(doh_gdb)
    print(f"Reading DOH geodatabase: {resolved_doh_gdb}", flush=True)

    print("Dissolving full DOH watersheds...", flush=True)
    build_utilities(conn, resolved_doh_gdb.as_posix())
    print("  utilities:", conn.execute("SELECT count(*) FROM util_diss").fetchone()[0], flush=True)

    if not fires_shp:
        output = write_utilities(conn, data_root / "tables")
        print(f"No Washington fire perimeter supplied; wrote utilities only: {output}", flush=True)
        print("DONE", flush=True)
        return

    print(f"Resolving MTBS shapefile: {fires_shp}", flush=True)
    local_shp = _resolve_shapefile(fires_shp)
    print("Reading Washington MTBS wildfire perimeters...", flush=True)
    build_wildfires(
        conn,
        local_shp,
        state_codes=(STATE_CODE,),
        incident_types=WILDFIRE_INCIDENT_TYPES,
    )
    print("  wildfires:", conn.execute("SELECT count(*) FROM fires_raw").fetchone()[0], flush=True)

    print("Writing tables + computing overlap pairs...", flush=True)
    write_tables(conn, data_root / "tables")
    pairs = conn.execute(
        f"SELECT count(*) FROM read_parquet('{(data_root / 'tables' / 'pair_summary.parquet').as_posix()}')"
    ).fetchone()[0]
    print("  overlapping pairs:", pairs, flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Washington EMBER sources to Parquet tables.")
    parser.add_argument("--doh-gdb", default=DEFAULT_DOH_GDB, help="Path to DOH FileGDB folder.")
    parser.add_argument("--data-root", default="./data/published-wa", help="Output root (writes tables/).")
    parser.add_argument(
        "--fires-shp",
        default=None,
        help=(
            "Optional Washington-capable MTBS perimeter shapefile (.shp), local or gs://. "
            "When omitted, only utilities.parquet is generated."
        ),
    )
    args = parser.parse_args()
    ingest(args.doh_gdb, Path(args.data_root), args.fires_shp)
