"""Build EMBER tables from all utility sources and one MTBS CONUS dataset.

The state-specific utility loaders remain responsible for their different raw
formats. Wildfire perimeters are read once from the national MTBS source,
filtered to the configured dashboard states, and joined against the combined
utility table.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.manual_utilities import (  # noqa: E402
    DENVER_WATER_ID,
    DENVER_WATER_LATITUDE,
    DENVER_WATER_LONGITUDE,
    DENVER_WATER_NAME,
    DENVER_WATER_STATE,
)
from scripts.ingest_mtbs import (  # noqa: E402
    DEFAULT_CONUS_PERIMETERS,
    DEFAULT_STATE_CODES,
    _resolve_shapefile,
    build_wildfires,
)
from scripts.ingest_california import (  # noqa: E402
    DEFAULT_CA_BOUNDARIES,
    DEFAULT_CA_CONNECTIONS,
    DEFAULT_CA_SOURCE_POINTS,
    DEFAULT_CA_SYSTEM_POINTS,
    build_california_data,
    build_california_fire_links,
    resolve_data_file as resolve_california_file,
)
from scripts.ingest_oregon import (  # noqa: E402
    _connect,
    build_utilities as build_oregon_utilities,
    write_tables,
)
from scripts.ingest_washington import (  # noqa: E402
    DEFAULT_DOH_GDB,
    _resolve_doh_gdb,
    build_utilities as build_washington_utilities,
)

DEFAULT_WATER_SOURCE_ROOT = "gs://data_main_gcs/EMBER/water_source_areas"
EXPECTED_OREGON_GEOJSONS = (
    "Oregon_Surface_Water_Drinking_Water_Source_Areas.geojson",
    "water_source_areas.geojson",
)
DEFAULT_BUCKET = "data_main_gcs"
DEFAULT_PREFIX = "EMBER"
PUBLISHED_TABLES = (
    "utilities",
    "wildfires",
    "pair_summary",
    "utility_sources",
    "source_fire_locations",
)


def _resolve_oregon_geojson(source: str) -> str:
    """Resolve the Oregon source-area GeoJSON from a local/GCS file or prefix."""
    if not source.startswith("gs://"):
        local = Path(source).expanduser()
        if local.is_dir():
            matches = sorted(local.rglob("*.geojson"))
            preferred = [
                path
                for expected_name in EXPECTED_OREGON_GEOJSONS
                for path in matches
                if path.name == expected_name
            ]
            choices = preferred or matches
            if len(choices) != 1:
                raise ValueError(f"Expected one Oregon GeoJSON under {source}, found {len(choices)}.")
            local = choices[0]
        if not local.is_file():
            raise FileNotFoundError(source)
        return local.as_posix()

    import gcsfs

    fs = gcsfs.GCSFileSystem()
    remote_source = source[len("gs://") :].rstrip("/")
    if remote_source.lower().endswith((".geojson", ".json")):
        remote = remote_source
    else:
        matches = [
            path
            for path in fs.find(remote_source)
            if path.lower().endswith((".geojson", ".json"))
        ]
        preferred = [
            path
            for expected_name in EXPECTED_OREGON_GEOJSONS
            for path in matches
            if Path(path).name == expected_name
        ]
        choices = preferred or matches
        if len(choices) != 1:
            raise ValueError(
                f"Expected one Oregon GeoJSON under {source}, found {len(choices)}."
            )
        remote = choices[0]

    destination = Path(tempfile.mkdtemp(prefix="oregon_sources_")) / Path(remote).name
    fs.get(remote, destination.as_posix())
    return destination.as_posix()


def _parse_states(value: str) -> tuple[str, ...]:
    states = tuple(code.strip().upper() for code in value.split(",") if code.strip())
    if not states:
        raise argparse.ArgumentTypeError("Provide at least one two-letter state code.")
    if any(len(code) != 2 or not code.isalpha() for code in states):
        raise argparse.ArgumentTypeError(f"Invalid state list: {value}")
    return tuple(dict.fromkeys(states))


def _publish_tables(data_root: Path, bucket_name: str, prefix: str) -> None:
    """Back up and publish the three rebuilt geometry/overlap tables to GCS."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    cleaned_prefix = prefix.strip("/")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    live_blobs = {}
    generations = {}
    for table in PUBLISHED_TABLES:
        key = "/".join(
            part for part in (cleaned_prefix, "tables", f"{table}.parquet") if part
        )
        blob = bucket.blob(key)
        live_blobs[table] = blob
        if blob.exists(client):
            blob.reload()
            generations[table] = int(blob.generation)
        else:
            generations[table] = 0

    for table in PUBLISHED_TABLES:
        if generations[table] == 0:
            continue
        live_blob = live_blobs[table]
        backup_key = "/".join(
            part
            for part in (
                cleaned_prefix,
                "tables",
                "backups",
                f"{table}.pre-conus-{timestamp}.parquet",
            )
            if part
        )
        bucket.copy_blob(
            live_blob,
            bucket,
            backup_key,
            if_source_generation_match=generations[table],
        )
        print(f"Backed up gs://{bucket_name}/{backup_key}", flush=True)

    for table in PUBLISHED_TABLES:
        source = data_root / "tables" / f"{table}.parquet"
        live_blob = live_blobs[table]
        live_blob.upload_from_filename(
            source.as_posix(),
            if_generation_match=generations[table],
        )
        print(f"Published gs://{bucket_name}/{live_blob.name}", flush=True)


def ingest_all(
    *,
    oregon_geojson: str,
    washington_gdb: str,
    california_boundaries: str,
    california_connections: str,
    california_source_points: str,
    california_system_points: str,
    conus_perimeters: str,
    data_root: Path,
    states: tuple[str, ...] = DEFAULT_STATE_CODES,
    publish: bool = False,
    bucket: str = DEFAULT_BUCKET,
    prefix: str = DEFAULT_PREFIX,
) -> None:
    """Build combined utilities, Western wildfires, and all overlap tables."""
    conn = _connect()

    resolved_oregon = _resolve_oregon_geojson(oregon_geojson)
    print(f"Reading Oregon source areas: {resolved_oregon}", flush=True)
    build_oregon_utilities(conn, resolved_oregon)
    conn.execute("CREATE TEMP TABLE utilities_oregon AS SELECT * FROM util_diss")
    oregon_count = conn.execute("SELECT count(*) FROM utilities_oregon").fetchone()[0]

    resolved_washington = _resolve_doh_gdb(washington_gdb)
    print(f"Reading Washington source areas: {resolved_washington}", flush=True)
    build_washington_utilities(conn, resolved_washington.as_posix())
    conn.execute("CREATE TEMP TABLE utilities_washington AS SELECT * FROM util_diss")
    washington_count = conn.execute(
        "SELECT count(*) FROM utilities_washington"
    ).fetchone()[0]

    resolved_ca_boundaries = resolve_california_file(california_boundaries)
    resolved_ca_connections = resolve_california_file(california_connections)
    resolved_ca_source_points = resolve_california_file(california_source_points)
    resolved_ca_system_points = resolve_california_file(california_system_points)
    print(f"Reading California service areas: {resolved_ca_boundaries}", flush=True)
    print(f"Reading California source connections: {resolved_ca_connections}", flush=True)
    build_california_data(
        conn,
        resolved_ca_boundaries.as_posix(),
        resolved_ca_connections.as_posix(),
        resolved_ca_source_points.as_posix(),
        resolved_ca_system_points.as_posix(),
    )
    california_count = conn.execute(
        "SELECT count(*) FROM utilities_california"
    ).fetchone()[0]

    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE util_diss AS
        SELECT * FROM utilities_oregon
        UNION ALL BY NAME
        SELECT * FROM utilities_washington
        UNION ALL BY NAME
        SELECT * FROM utilities_california
        """
    )
    conn.execute(
        """
        INSERT INTO util_diss BY NAME
        SELECT
            ? AS utility_id,
            ? AS name,
            ? AS state,
            'Service location only' AS source_area_name,
            CAST(NULL AS GEOMETRY) AS geom,
            ST_Point(?, ?) AS service_geom
        """,
        [
            DENVER_WATER_ID,
            DENVER_WATER_NAME,
            DENVER_WATER_STATE,
            DENVER_WATER_LONGITUDE,
            DENVER_WATER_LATITUDE,
        ],
    )
    duplicate_utilities = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT utility_id FROM util_diss GROUP BY utility_id HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_utilities:
        raise ValueError(f"Combined utilities contain {duplicate_utilities} duplicate IDs.")
    print(
        f"Utilities: Oregon={oregon_count:,}, Washington={washington_count:,}, "
        f"California={california_count:,}, manual=1, "
        f"total={oregon_count + washington_count + california_count + 1:,}",
        flush=True,
    )

    print(f"Resolving MTBS CONUS perimeters: {conus_perimeters}", flush=True)
    local_shp = _resolve_shapefile(conus_perimeters)
    print(f"Loading Western wildfire states: {', '.join(states)}", flush=True)
    build_wildfires(conn, local_shp, state_codes=states)
    build_california_fire_links(conn)
    wildfire_count = conn.execute("SELECT count(*) FROM fires_raw").fetchone()[0]
    wildfire_states = [
        row[0]
        for row in conn.execute("SELECT DISTINCT state FROM fires_raw ORDER BY state").fetchall()
    ]
    print(
        f"Wildfires: {wildfire_count:,} across {', '.join(wildfire_states)}",
        flush=True,
    )

    print("Writing tables and computing utility/wildfire overlaps...", flush=True)
    write_tables(conn, data_root / "tables")
    pair_count = conn.execute(
        f"""
        SELECT count(*)
        FROM read_parquet('{(data_root / "tables" / "pair_summary.parquet").as_posix()}')
        """
    ).fetchone()[0]
    print(f"Overlapping pairs: {pair_count:,}", flush=True)
    if publish:
        _publish_tables(data_root, bucket, prefix)
    else:
        print("Local build complete; pass --publish to update GCS.", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest all EMBER utilities and scoped MTBS CONUS perimeters."
    )
    parser.add_argument(
        "--oregon-geojson",
        default=DEFAULT_WATER_SOURCE_ROOT,
        help="Oregon GeoJSON file or containing directory/prefix; local or gs://.",
    )
    parser.add_argument(
        "--washington-gdb",
        default=DEFAULT_DOH_GDB,
        help="Washington DOH FileGDB folder; local or gs://.",
    )
    parser.add_argument(
        "--california-boundaries",
        default=DEFAULT_CA_BOUNDARIES,
        help="California utility service-area GeoJSON/JSON file; local or gs://.",
    )
    parser.add_argument(
        "--california-connections",
        default=DEFAULT_CA_CONNECTIONS,
        help="California utility-source connection CSV file; local or gs://.",
    )
    parser.add_argument(
        "--california-source-points",
        default=DEFAULT_CA_SOURCE_POINTS,
        help="California natural-source point GeoJSON or ArcGIS query URL.",
    )
    parser.add_argument(
        "--california-system-points",
        default=DEFAULT_CA_SYSTEM_POINTS,
        help="California water-system point GeoJSON or ArcGIS query URL.",
    )
    parser.add_argument(
        "--conus-perimeters",
        default=DEFAULT_CONUS_PERIMETERS,
        help="MTBS CONUS .shp file or containing directory/prefix; local or gs://.",
    )
    parser.add_argument(
        "--states",
        type=_parse_states,
        default=DEFAULT_STATE_CODES,
        help="Comma-separated states to retain (default: WA, OR, CA, and CO).",
    )
    parser.add_argument(
        "--data-root",
        default="./data/published",
        help="Output root (writes tables/).",
    )
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Published GCS bucket.")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="Published GCS prefix.")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Back up and replace the live utilities, wildfires, and pair tables in GCS.",
    )
    args = parser.parse_args()
    ingest_all(
        oregon_geojson=args.oregon_geojson,
        washington_gdb=args.washington_gdb,
        california_boundaries=args.california_boundaries,
        california_connections=args.california_connections,
        california_source_points=args.california_source_points,
        california_system_points=args.california_system_points,
        conus_perimeters=args.conus_perimeters,
        data_root=Path(args.data_root),
        states=args.states,
        publish=args.publish,
        bucket=args.bucket,
        prefix=args.prefix,
    )
