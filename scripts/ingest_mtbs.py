"""Shared MTBS CONUS wildfire-perimeter ingestion helpers.

EMBER uses one national MTBS perimeter source, then keeps only the configured
states used by the dashboard. State is derived from the first two
characters of MTBS ``event_id`` and stored on every wildfire row so the app can
filter and sort without state-specific source files.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

MIN_FIRE_YEAR = 1900
MAX_FIRE_YEAR = 2026
WILDFIRE_INCIDENT_TYPES = ("WILDFIRE", "WILDLAND FIRE USE")
WESTERN_STATE_CODES = (
    "AZ",
    "CA",
    "CO",
    "ID",
    "MT",
    "NV",
    "NM",
    "OR",
    "UT",
    "WA",
    "WY",
)
DEFAULT_STATE_CODES = ("WA", "OR", "CA", "CO")

DEFAULT_CONUS_PERIMETERS = (
    "gs://data_main_gcs/EMBER/fire_burn_perimeters/"
    "CONUS_perimeter_data/mtbs_perimeter_data.zip"
)
EXPECTED_MTBS_SHAPEFILE = "mtbs_perims_dd.shp"
EXPECTED_MTBS_ARCHIVE = "mtbs_perimeter_data.zip"


def _select_shapefile(candidates: list[str], source: str) -> str:
    """Choose the sole MTBS perimeter shapefile from discovered candidates."""
    shapefiles = sorted(path for path in candidates if path.lower().endswith(".shp"))
    preferred = [
        path
        for path in shapefiles
        if Path(path).name.lower() == EXPECTED_MTBS_SHAPEFILE
    ]
    matches = preferred or shapefiles
    if not matches:
        raise FileNotFoundError(f"No shapefile found under {source}")
    if len(matches) > 1:
        listed = "\n  ".join(matches)
        raise ValueError(
            f"Multiple MTBS shapefiles found under {source}; pass the CONUS .shp URI directly:\n"
            f"  {listed}"
        )
    return matches[0]


def _extract_shapefile_archive(archive_path: Path) -> str:
    """Extract the MTBS shapefile and same-stem sidecars from a ZIP archive."""
    output_dir = Path(tempfile.mkdtemp(prefix="mtbs_conus_"))
    with zipfile.ZipFile(archive_path) as archive:
        shp_member = _select_shapefile(archive.namelist(), archive_path.as_posix())
        stem = Path(shp_member).stem.lower()
        local_shp: Path | None = None
        for member in archive.namelist():
            member_path = Path(member)
            if member_path.stem.lower() != stem or member.endswith("/"):
                continue
            destination = output_dir / member_path.name
            with archive.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
            if destination.suffix.lower() == ".shp":
                local_shp = destination
    if local_shp is None:
        raise FileNotFoundError(f"No .shp found in {archive_path}")
    return local_shp.as_posix()


def _resolve_shapefile(source: str) -> str:
    """Resolve a local/GCS shapefile or directory to a local ``.shp`` path.

    GCS shapefiles are downloaded with all same-stem sidecars because GDAL must
    open the ``.shp``, ``.dbf``, ``.shx``, and projection files together.
    """
    if not source.startswith("gs://"):
        local = Path(source).expanduser()
        if local.is_dir():
            archives = [
                path
                for path in local.rglob("*.zip")
                if path.name.lower() == EXPECTED_MTBS_ARCHIVE
            ]
            if len(archives) == 1:
                return _extract_shapefile_archive(archives[0])
            return _select_shapefile([path.as_posix() for path in local.rglob("*.shp")], source)
        if not local.is_file():
            raise FileNotFoundError(source)
        if local.suffix.lower() == ".zip":
            return _extract_shapefile_archive(local)
        if local.suffix.lower() != ".shp":
            raise ValueError(f"Expected a .shp, .zip, or directory, got {source}")
        return local.as_posix()

    import gcsfs

    fs = gcsfs.GCSFileSystem()
    remote_source = source[len("gs://") :].rstrip("/")
    if remote_source.lower().endswith(".zip"):
        tmp_dir = Path(tempfile.mkdtemp(prefix="mtbs_archive_"))
        local_archive = tmp_dir / Path(remote_source).name
        fs.get(remote_source, local_archive.as_posix())
        return _extract_shapefile_archive(local_archive)
    if remote_source.lower().endswith(".shp"):
        remote_shp = remote_source
    else:
        candidates = fs.find(remote_source)
        archives = [
            path
            for path in candidates
            if Path(path).name.lower() == EXPECTED_MTBS_ARCHIVE
        ]
        if len(archives) == 1:
            tmp_dir = Path(tempfile.mkdtemp(prefix="mtbs_archive_"))
            local_archive = tmp_dir / Path(archives[0]).name
            fs.get(archives[0], local_archive.as_posix())
            return _extract_shapefile_archive(local_archive)
        remote_shp = _select_shapefile(candidates, source)

    directory, shp_name = remote_shp.rsplit("/", 1)
    stem = shp_name.rsplit(".", 1)[0]
    siblings = [
        path
        for path in fs.ls(directory)
        if Path(path).name.rsplit(".", 1)[0].lower() == stem.lower()
    ]
    tmp_dir = Path(tempfile.mkdtemp(prefix="mtbs_conus_"))
    local_shp: Path | None = None
    for remote in siblings:
        destination = tmp_dir / Path(remote).name
        fs.get(remote, destination.as_posix())
        if destination.suffix.lower() == ".shp":
            local_shp = destination
    if local_shp is None:
        raise FileNotFoundError(f"No .shp found alongside gs://{remote_shp}")
    return local_shp.as_posix()


def build_wildfires(
    conn: duckdb.DuckDBPyConnection,
    mtbs_shp: str,
    state_codes: tuple[str, ...] = DEFAULT_STATE_CODES,
    incident_types: tuple[str, ...] = WILDFIRE_INCIDENT_TYPES,
) -> None:
    """Build ``fires_raw`` from one MTBS CONUS shapefile.

    Oregon IDs retain their existing ``name-year`` form for compatibility with
    published metric rows. Other states are prefixed (for example ``wa-``) to
    prevent same-name/year collisions across states.
    """
    normalized_states = tuple(dict.fromkeys(code.strip().upper() for code in state_codes))
    if not normalized_states:
        raise ValueError("At least one state code is required.")
    if any(len(code) != 2 or not code.isalpha() for code in normalized_states):
        raise ValueError(f"Invalid two-letter state code in {state_codes!r}")

    states_sql = ", ".join(f"'{code}'" for code in normalized_states)
    types_sql = ", ".join(f"'{incident_type}'" for incident_type in incident_types)
    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fires_raw AS
        WITH src AS (
            SELECT
                substr(upper(trim(event_id)), 1, 2) AS state,
                nullif(trim(incid_name), '') AS incident,
                TRY_CAST(ig_date AS DATE) AS ignition_date,
                TRY_CAST(burnbndac AS DOUBLE) AS acres,
                ST_MakeValid(geom) AS geom
            FROM ST_Read('{mtbs_shp}')
            WHERE substr(upper(trim(event_id)), 1, 2) IN ({states_sql})
              AND upper(trim(incid_type)) IN ({types_sql})
        ),
        valid AS (
            SELECT
                state,
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
                state,
                coalesce(
                    nullif(regexp_replace(lower(incident), '[^a-z0-9]+', '-', 'g'), ''),
                    'fire'
                ) AS base_slug,
                coalesce(incident, 'Unnamed Fire') AS name,
                ignition_date,
                year,
                acres,
                geom
            FROM valid
        ),
        ranked AS (
            SELECT
                state,
                base_slug || '-' || CAST(year AS VARCHAR) AS base_id,
                row_number() OVER (
                    PARTITION BY state, base_slug, year
                    ORDER BY acres DESC NULLS LAST
                ) AS rn,
                count(*) OVER (
                    PARTITION BY state, base_slug, year
                ) AS grp_n,
                name,
                ignition_date,
                year,
                acres,
                geom
            FROM slugged
        )
        SELECT
            CASE WHEN state = 'OR' THEN '' ELSE lower(state) || '-' END ||
            CASE WHEN grp_n > 1 THEN base_id || '-' || CAST(rn AS VARCHAR) ELSE base_id END
                AS wildfire_id,
            name,
            ignition_date,
            year,
            acres,
            state,
            'MTBS' AS source,
            geom
        FROM ranked
        """
    )
