"""Ingest Washington DOH drinking-water watersheds and MTBS fires into EMBER.

The DOH source dataset is an Esri File Geodatabase. This script keeps only
``AreaType = 'Full'`` watershed rows, dissolves them to one row per water system,
then computes utility x wildfire overlaps using Washington MTBS perimeters.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import duckdb

DISPLAY_SIMPLIFY_DEG = 0.0005
AREA_CRS = "EPSG:5070"
SOURCE_CRS = "EPSG:3857"
MIN_FIRE_YEAR = 1900
MAX_FIRE_YEAR = 2026
STATE_CODE = "WA"
WILDFIRE_INCIDENT_TYPES = ("WILDFIRE", "WILDLAND FIRE USE")
DEFAULT_DOH_GDB = "./water_source_areas/DOH Drinking Water Full Watershed.gdb"
DEFAULT_FIRES_SHP = (
    "gs://data_main_gcs/EMBER/fire_burn_perimeters/"
    "Washington_MTBS_perimeter_data/mtbs_perims_DD.shp"
)


def _resolve_shapefile(path: str) -> str:
    """Return a local shapefile path, downloading sidecars first for ``gs://`` paths."""
    if not path.startswith("gs://"):
        return path

    import gcsfs

    fs = gcsfs.GCSFileSystem()
    bucket_path = path[len("gs://") :]
    directory, shp_name = bucket_path.rsplit("/", 1)
    stem = shp_name.rsplit(".", 1)[0]
    tmp_dir = Path(tempfile.mkdtemp(prefix="mtbs_wa_"))
    local_shp: str | None = None
    for remote in fs.ls(directory):
        name = remote.rsplit("/", 1)[-1]
        if name.rsplit(".", 1)[0] != stem:
            continue
        local = (tmp_dir / name).as_posix()
        fs.get(remote, local)
        if name.lower().endswith(".shp"):
            local_shp = local
    if local_shp is None:
        raise FileNotFoundError(f"No .shp found alongside {path}")
    return local_shp


def _connect() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(database=":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    return conn


def _resolve_doh_gdb(path: Path) -> Path:
    """Resolve the DOH FileGDB from the expected raw-data locations."""
    candidates = [
        path,
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
            lower(PwsId) AS utility_id,
            any_value(SystemName) AS name,
            '{STATE_CODE}' AS state,
            string_agg(DISTINCT nullif(trim(SrcName), ''), '; ') AS source_area_name,
            ST_MakeValid(
                ST_Union_Agg(
                    ST_Transform(Shape, '{SOURCE_CRS}', 'EPSG:4326', always_xy := true)
                )
            ) AS geom
        FROM ST_Read('{doh_gdb}')
        WHERE PwsId IS NOT NULL
          AND trim(PwsId) <> ''
          AND upper(trim(SrcStatusIndDesc)) = 'ACTIVE'
          AND upper(trim(AreaType)) = 'FULL'
        GROUP BY lower(PwsId)
        """
    )


def build_wildfires(
    conn: duckdb.DuckDBPyConnection,
    mtbs_shp: str,
    state_code: str = STATE_CODE,
    incident_types: tuple[str, ...] = WILDFIRE_INCIDENT_TYPES,
) -> None:
    """Read MTBS perimeters for Washington into one row per wildfire."""
    types_sql = ", ".join(f"'{incident_type}'" for incident_type in incident_types)
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fires_raw AS
        WITH src AS (
            SELECT
                nullif(trim(incid_name), '') AS incident,
                TRY_CAST(ig_date AS DATE) AS ignition_date,
                TRY_CAST(burnbndac AS DOUBLE) AS acres,
                ST_MakeValid(geom) AS geom
            FROM ST_Read('{mtbs_shp}')
            WHERE substr(upper(event_id), 1, 2) = '{state_code}'
              AND upper(trim(incid_type)) IN ({types_sql})
        ),
        valid AS (
            SELECT
                incident,
                ignition_date,
                EXTRACT(YEAR FROM ignition_date)::INTEGER AS year,
                acres,
                geom
            FROM src
            WHERE ignition_date IS NOT NULL
              AND EXTRACT(YEAR FROM ignition_date) BETWEEN {MIN_FIRE_YEAR} AND {MAX_FIRE_YEAR}
        ),
        slugged AS (
            SELECT
                coalesce(
                    nullif(regexp_replace(lower(incident), '[^a-z0-9]+', '-', 'g'), ''),
                    'fire'
                ) AS base_slug,
                coalesce(incident, 'Unnamed Fire') AS name,
                ignition_date, year, acres, geom
            FROM valid
        ),
        ranked AS (
            SELECT
                base_slug || '-' || CAST(year AS VARCHAR) AS base_id,
                row_number() OVER (
                    PARTITION BY base_slug || '-' || CAST(year AS VARCHAR)
                    ORDER BY acres DESC NULLS LAST
                ) AS rn,
                count(*) OVER (
                    PARTITION BY base_slug || '-' || CAST(year AS VARCHAR)
                ) AS grp_n,
                name, ignition_date, year, acres, geom
            FROM slugged
        )
        SELECT
            CASE WHEN grp_n > 1 THEN base_id || '-' || CAST(rn AS VARCHAR) ELSE base_id END
                AS wildfire_id,
            name, ignition_date, year, acres, 'MTBS' AS source, geom
        FROM ranked
        """
    )


def write_tables(conn: duckdb.DuckDBPyConnection, tables_dir: Path) -> None:
    """Write EMBER Parquet tables for utilities, fires, overlaps, and placeholders."""
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
                CAST(NULL AS DATE) AS containment_date,
                acres,
                '{STATE_CODE}' AS state,
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


def ingest(doh_gdb: Path, data_root: Path, fires_shp: str = DEFAULT_FIRES_SHP) -> None:
    """Run the Washington ingest from DOH/MTBS sources to EMBER Parquet tables."""
    conn = _connect()
    resolved_doh_gdb = _resolve_doh_gdb(doh_gdb)
    print(f"Reading DOH geodatabase: {resolved_doh_gdb}", flush=True)
    print(f"Resolving MTBS shapefile: {fires_shp}", flush=True)
    local_shp = _resolve_shapefile(fires_shp)

    print("Dissolving full DOH watersheds...", flush=True)
    build_utilities(conn, resolved_doh_gdb.as_posix())
    print("  utilities:", conn.execute("SELECT count(*) FROM util_diss").fetchone()[0], flush=True)

    print("Reading Washington MTBS wildfire perimeters...", flush=True)
    build_wildfires(conn, local_shp)
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
        default=DEFAULT_FIRES_SHP,
        help="MTBS perimeter shapefile (.shp) path; local or gs:// URI.",
    )
    args = parser.parse_args()
    ingest(Path(args.doh_gdb), Path(args.data_root), args.fires_shp)
