"""Merge Washington source data into EMBER's published GCS Parquet tables.

By default this publishes utilities only. Pass ``--fires-shp`` when a Washington-capable
MTBS perimeter source is available to also merge Washington wildfires and overlap pairs.
Existing scalar metrics and raster assets are never replaced by this workflow.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from google.cloud import storage

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest_washington import (
    DEFAULT_DOH_GDB,
    STATE_CODE,
    _connect,
    _resolve_doh_gdb,
    _resolve_shapefile,
    build_utilities,
    build_wildfires,
    write_tables,
    write_utilities,
)

DEFAULT_BUCKET = "data_main_gcs"
DEFAULT_PREFIX = "EMBER"


def _key(prefix: str, relative: str) -> str:
    cleaned = prefix.strip("/")
    return f"{cleaned}/{relative}" if cleaned else relative


def _download_live_table(
    bucket: storage.Bucket, prefix: str, table: str, destination: Path
) -> tuple[Path, int]:
    blob = bucket.blob(_key(prefix, f"tables/{table}.parquet"))
    blob.reload()
    generation = int(blob.generation)
    blob.download_to_filename(destination.as_posix())
    return destination, generation


def _merge_state_rows(
    existing: Path,
    incoming: Path,
    output: Path,
    *,
    state: str,
    id_column: str,
) -> tuple[int, int, int]:
    conn = duckdb.connect(database=":memory:")
    conn.execute(f"CREATE TABLE existing AS SELECT * FROM read_parquet('{existing.as_posix()}')")
    conn.execute(f"CREATE TABLE incoming AS SELECT * FROM read_parquet('{incoming.as_posix()}')")
    quoted_state = state.replace("'", "''")

    incoming_count = conn.execute("SELECT count(*) FROM incoming").fetchone()[0]
    if incoming_count == 0:
        raise ValueError(f"Incoming {state} table is empty.")
    invalid_states = conn.execute(
        f"SELECT count(*) FROM incoming WHERE state <> '{quoted_state}'"
    ).fetchone()[0]
    if invalid_states:
        raise ValueError(f"Incoming table contains {invalid_states} non-{state} rows.")
    duplicate_ids = conn.execute(
        f"""
        SELECT count(*) FROM (
            SELECT {id_column} FROM incoming GROUP BY {id_column} HAVING count(*) > 1
        )
        """
    ).fetchone()[0]
    if duplicate_ids:
        raise ValueError(f"Incoming table contains {duplicate_ids} duplicate {id_column} values.")

    retained_count = conn.execute(
        f"SELECT count(*) FROM existing WHERE coalesce(state, '') <> '{quoted_state}'"
    ).fetchone()[0]
    conn.execute(
        f"""
        CREATE TABLE merged AS
        SELECT * FROM existing WHERE coalesce(state, '') <> '{quoted_state}'
        UNION ALL BY NAME
        SELECT * FROM incoming
        """
    )
    merged_count = conn.execute("SELECT count(*) FROM merged").fetchone()[0]
    if merged_count != retained_count + incoming_count:
        raise ValueError("Merged row count failed validation.")
    conn.execute(f"COPY merged TO '{output.as_posix()}' (FORMAT PARQUET)")
    return retained_count, incoming_count, merged_count


def _merge_pair_rows(
    existing_pairs: Path,
    incoming_pairs: Path,
    existing_utilities: Path,
    existing_wildfires: Path,
    output: Path,
) -> tuple[int, int, int]:
    conn = duckdb.connect(database=":memory:")
    conn.execute(
        f"CREATE TABLE existing_pairs AS SELECT * FROM read_parquet('{existing_pairs.as_posix()}')"
    )
    conn.execute(
        f"CREATE TABLE incoming_pairs AS SELECT * FROM read_parquet('{incoming_pairs.as_posix()}')"
    )
    conn.execute(
        f"CREATE TABLE existing_utilities AS SELECT * FROM read_parquet('{existing_utilities.as_posix()}')"
    )
    conn.execute(
        f"CREATE TABLE existing_wildfires AS SELECT * FROM read_parquet('{existing_wildfires.as_posix()}')"
    )
    conn.execute(
        """
        CREATE TABLE retained AS
        SELECT p.*
        FROM existing_pairs p
        WHERE p.utility_id NOT IN (
            SELECT utility_id FROM existing_utilities WHERE state = 'WA'
        )
          AND p.wildfire_id NOT IN (
            SELECT wildfire_id FROM existing_wildfires WHERE state = 'WA'
        )
        """
    )
    retained_count = conn.execute("SELECT count(*) FROM retained").fetchone()[0]
    incoming_count = conn.execute("SELECT count(*) FROM incoming_pairs").fetchone()[0]
    conn.execute(
        """
        CREATE TABLE merged AS
        SELECT * FROM retained
        UNION ALL BY NAME
        SELECT * FROM incoming_pairs
        """
    )
    merged_count = conn.execute("SELECT count(*) FROM merged").fetchone()[0]
    if merged_count != retained_count + incoming_count:
        raise ValueError("Merged pair row count failed validation.")
    conn.execute(f"COPY merged TO '{output.as_posix()}' (FORMAT PARQUET)")
    return retained_count, incoming_count, merged_count


def _backup_and_publish(
    bucket: storage.Bucket,
    prefix: str,
    table: str,
    source: Path,
    expected_generation: int,
) -> tuple[str, str]:
    live_key = _key(prefix, f"tables/{table}.parquet")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_key = _key(prefix, f"tables/backups/{table}.pre-wa-{timestamp}.parquet")
    live_blob = bucket.blob(live_key)
    bucket.copy_blob(
        live_blob,
        bucket,
        backup_key,
        if_source_generation_match=expected_generation,
    )
    live_blob.upload_from_filename(
        source.as_posix(),
        if_generation_match=expected_generation,
    )
    return f"gs://{bucket.name}/{live_key}", f"gs://{bucket.name}/{backup_key}"


def publish_washington(
    *,
    doh_gdb: str,
    bucket_name: str,
    prefix: str,
    fires_shp: str | None,
    publish: bool,
) -> None:
    """Build, validate, optionally publish merged Washington tables."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    with tempfile.TemporaryDirectory(prefix="ember_publish_wa_") as tmp:
        root = Path(tmp)
        incoming_root = root / "incoming"
        live_root = root / "live"
        merged_root = root / "merged"
        live_root.mkdir()
        merged_root.mkdir()

        conn = _connect()
        local_gdb = _resolve_doh_gdb(doh_gdb)
        build_utilities(conn, local_gdb.as_posix())
        if fires_shp:
            local_shp = _resolve_shapefile(fires_shp)
            build_wildfires(conn, local_shp, state_codes=(STATE_CODE,))
            write_tables(conn, incoming_root / "tables")
            tables = ["utilities", "wildfires", "pair_summary"]
        else:
            write_utilities(conn, incoming_root / "tables")
            tables = ["utilities"]

        live_paths: dict[str, Path] = {}
        generations: dict[str, int] = {}
        download_tables = {"utilities", *tables}
        if fires_shp:
            download_tables.add("wildfires")
        for table in sorted(download_tables):
            path, generation = _download_live_table(
                bucket, prefix, table, live_root / f"{table}.parquet"
            )
            live_paths[table] = path
            generations[table] = generation

        summaries: dict[str, tuple[int, int, int]] = {}
        summaries["utilities"] = _merge_state_rows(
            live_paths["utilities"],
            incoming_root / "tables" / "utilities.parquet",
            merged_root / "utilities.parquet",
            state="WA",
            id_column="utility_id",
        )
        if fires_shp:
            summaries["wildfires"] = _merge_state_rows(
                live_paths["wildfires"],
                incoming_root / "tables" / "wildfires.parquet",
                merged_root / "wildfires.parquet",
                state="WA",
                id_column="wildfire_id",
            )
            summaries["pair_summary"] = _merge_pair_rows(
                live_paths["pair_summary"],
                incoming_root / "tables" / "pair_summary.parquet",
                live_paths["utilities"],
                live_paths["wildfires"],
                merged_root / "pair_summary.parquet",
            )

        for table, (retained, incoming, merged) in summaries.items():
            print(
                f"{table}: retained={retained}, washington={incoming}, merged={merged}",
                flush=True,
            )

        if not publish:
            print("Dry run complete. Re-run with --publish to update GCS.", flush=True)
            return

        for table in tables:
            live_uri, backup_uri = _backup_and_publish(
                bucket,
                prefix,
                table,
                merged_root / f"{table}.parquet",
                generations[table],
            )
            print(f"Published {live_uri}", flush=True)
            print(f"Backup    {backup_uri}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Merge Washington source data into EMBER's GCS Parquet tables."
    )
    parser.add_argument("--doh-gdb", default=DEFAULT_DOH_GDB)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument(
        "--fires-shp",
        default=None,
        help="Optional local or gs:// Washington-capable MTBS shapefile.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Back up and update live GCS tables. Without this flag, run validation only.",
    )
    args = parser.parse_args()
    publish_washington(
        doh_gdb=args.doh_gdb,
        bucket_name=args.bucket,
        prefix=args.prefix,
        fires_shp=args.fires_shp,
        publish=args.publish,
    )
