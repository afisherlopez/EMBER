"""Ingest Oregon source-water and MTBS wildfire perimeters into the EMBER tables.

Sources:
  - water source areas — raw GeoJSON (CRS84 / EPSG:4326), one feature per PWS source area.
  - wildfire perimeters — the MTBS national perimeter shapefile (``mtbs_perims_DD``),
    subset to one state (Oregon) and to true wildfires. MTBS carries a stable
    ``event_id``, the incident name/type, burned acres, and a real ignition date. The
    shapefile may live in GCS (``gs://`` path); its sidecar files are fetched alongside
    the ``.shp`` before reading.

and writes the catalog's Parquet tables under ``<data_root>/tables``:
  - ``utilities.parquet``      (PWS source areas dissolved to one row per utility)
  - ``wildfires.parquet``      (MTBS perimeters for the state, one row per fire)
  - ``pair_summary.parquet``   (overlapping utility x wildfire pairs, with area/pct)
  - ``scalar_metrics.parquet`` (empty schema placeholder until metrics exist)
  - ``raster_assets.parquet``  (empty schema placeholder until COGs exist)

All geometry math is done in DuckDB's ``spatial`` extension. Area/overlap is computed
in EPSG:5070 (CONUS Albers, meters); geometries for display are simplified and stored
as GeoJSON text in ``geometry_geojson`` (the column the app renders).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest_mtbs import (
    DEFAULT_CONUS_PERIMETERS,
    WILDFIRE_INCIDENT_TYPES,
    _resolve_shapefile,
    build_wildfires,
)

# Simplification tolerance (degrees) for stored display geometry. ~0.0005 deg ~= 50 m.
DISPLAY_SIMPLIFY_DEG = 0.0005
# Equal-area projection (meters) used only for area/overlap math.
AREA_CRS = "EPSG:5070"
# Two-letter state code used to subset the national MTBS file (matches the `event_id` prefix).
STATE_CODE = "OR"
DEFAULT_FIRES_SHP = DEFAULT_CONUS_PERIMETERS


def _connect() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(database=":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    return conn


def build_utilities(conn: duckdb.DuckDBPyConnection, source_areas_geojson: str) -> None:
    """Dissolve PWS source areas to one row per utility and register `util_diss`."""
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE util_diss AS
        SELECT
            lower(PWS_ID)                              AS utility_id,
            any_value(PWS_label)                       AS name,
            'OR'                                       AS state,
            string_agg(DISTINCT Src_label, '; ')       AS source_area_name,
            ST_MakeValid(ST_Union_Agg(geom))           AS geom
        FROM ST_Read('{source_areas_geojson}')
        WHERE PWS_ID IS NOT NULL AND trim(PWS_ID) <> ''
        GROUP BY lower(PWS_ID)
        """
    )


def write_tables(conn: duckdb.DuckDBPyConnection, tables_dir: Path) -> None:
    """Write utilities, wildfires, pair_summary, and empty metric tables to Parquet."""
    tables_dir.mkdir(parents=True, exist_ok=True)
    utilities_path = (tables_dir / "utilities.parquet").as_posix()
    wildfires_path = (tables_dir / "wildfires.parquet").as_posix()
    pair_path = (tables_dir / "pair_summary.parquet").as_posix()

    conn.execute(
        f"""
        COPY (
            SELECT
                utility_id, name, state, source_area_name,
                CAST(ST_AsGeoJSON(ST_Simplify(geom, {DISPLAY_SIMPLIFY_DEG})) AS JSON) AS geometry_geojson,
                ST_X(ST_Centroid(geom)) AS centroid_lon,
                ST_Y(ST_Centroid(geom)) AS centroid_lat,
                now() AS updated_at
            FROM util_diss
        ) TO '{utilities_path}' (FORMAT PARQUET)
        """
    )
    conn.execute(
        f"""
        COPY (
            SELECT
                wildfire_id,
                name,
                ignition_date,
                CAST(NULL AS DATE)    AS containment_date,
                acres,
                state,
                ''                    AS county,
                ST_X(ST_Centroid(geom)) AS centroid_lon,
                ST_Y(ST_Centroid(geom)) AS centroid_lat,
                CAST(ST_AsGeoJSON(ST_Simplify(geom, {DISPLAY_SIMPLIFY_DEG})) AS JSON) AS geometry_geojson,
                source,
                now() AS updated_at
            FROM fires_raw
        ) TO '{wildfires_path}' (FORMAT PARQUET)
        """
    )

    # Project once into an equal-area CRS, then spatial-join. ST_Intersects short-circuits
    # on bounding boxes, so the 180 x ~9k candidate space resolves quickly.
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE util_proj AS
        SELECT utility_id,
               ST_Transform(geom, 'EPSG:4326', '{AREA_CRS}', always_xy := true) AS g
        FROM util_diss;
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
                       ST_Area(u.g)                        AS util_m2
                FROM util_proj u
                JOIN fire_proj f ON ST_Intersects(u.g, f.g)
            )
            SELECT
                utility_id, wildfire_id,
                TRUE                              AS has_overlap,
                inter_m2 / 1.0e6                  AS overlap_area_km2,
                CASE WHEN util_m2 > 0 THEN inter_m2 / util_m2 * 100.0 END AS overlap_pct_of_source,
                now()                            AS updated_at
            FROM pairs
            WHERE inter_m2 > 0
        ) TO '{pair_path}' (FORMAT PARQUET)
        """
    )

    # Empty placeholders so the catalog's read_parquet calls succeed before metrics exist.
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
    conn.execute(
        f"""
        COPY (
            SELECT
                CAST(NULL AS VARCHAR) AS utility_id, CAST(NULL AS VARCHAR) AS wildfire_id,
                CAST(NULL AS VARCHAR) AS metric_key, CAST(NULL AS VARCHAR) AS cog_uri,
                CAST(NULL AS VARCHAR) AS units, CAST(NULL AS VARCHAR) AS colormap_name,
                CAST(NULL AS DOUBLE) AS rescale_min, CAST(NULL AS DOUBLE) AS rescale_max,
                CAST(NULL AS DOUBLE) AS nodata, CAST(NULL AS DATE) AS as_of_date
            WHERE FALSE
        ) TO '{(tables_dir / "raster_assets.parquet").as_posix()}' (FORMAT PARQUET)
        """
    )


def ingest(raw_dir: Path, data_root: Path, fires_shp: str = DEFAULT_FIRES_SHP) -> None:
    """Run the full ingest from raw sources to published Parquet tables.

    Utility source areas come from the local GeoJSON in ``raw_dir``; wildfire perimeters
    come from the MTBS shapefile at ``fires_shp`` (a local path or a ``gs://`` URI).
    """
    conn = _connect()
    source_areas = (raw_dir / "water_source_areas.geojson").as_posix()

    print(f"Resolving MTBS shapefile: {fires_shp}", flush=True)
    local_shp = _resolve_shapefile(fires_shp)

    print("Dissolving utility source areas...", flush=True)
    build_utilities(conn, source_areas)
    print("  utilities:", conn.execute("SELECT count(*) FROM util_diss").fetchone()[0], flush=True)

    print("Reading MTBS wildfire perimeters...", flush=True)
    build_wildfires(
        conn,
        local_shp,
        state_codes=(STATE_CODE,),
        incident_types=WILDFIRE_INCIDENT_TYPES,
    )
    print("  wildfires:", conn.execute("SELECT count(*) FROM fires_raw").fetchone()[0], flush=True)

    print("Writing tables + computing overlap pairs (this is the slow step)...", flush=True)
    write_tables(conn, data_root / "tables")
    pairs = conn.execute(
        f"SELECT count(*) FROM read_parquet('{(data_root / 'tables' / 'pair_summary.parquet').as_posix()}')"
    ).fetchone()[0]
    print("  overlapping pairs:", pairs, flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Oregon EMBER sources to Parquet tables.")
    parser.add_argument("--raw-dir", default="./data/raw", help="Directory with the raw GeoJSON files.")
    parser.add_argument("--data-root", default="./data/published", help="Output root (writes tables/).")
    parser.add_argument(
        "--fires-shp",
        default=DEFAULT_FIRES_SHP,
        help="MTBS perimeter .shp/.zip source or containing prefix; local or gs://.",
    )
    args = parser.parse_args()
    ingest(Path(args.raw_dir), Path(args.data_root), args.fires_shp)
