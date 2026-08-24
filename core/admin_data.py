"""Admin helpers for safely updating EMBER Parquet tables."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
from google.cloud import storage as gcs_storage

from core.case_study_costs import CaseStudyCostInput
from core.models import UTILITY_METRIC_SCOPE_ID
from core.settings import settings


@dataclass(frozen=True)
class AdminWriteResult:
    """Result metadata for an admin table write."""

    table: str
    table_uri: str
    backup_uri: str | None


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _object_key(key: str) -> str:
    prefix = settings.gcs_prefix.strip("/")
    return f"{prefix}/{key}" if prefix else key


def _local_path(key: str) -> Path:
    return settings.data_root_path / key


def _read_table_to_temp(table: str, tmp_dir: Path) -> Path:
    key = f"tables/{table}.parquet"
    local_source = tmp_dir / f"{table}.source.parquet"
    if settings.ember_storage_backend == "gcs":
        client = gcs_storage.Client(project=settings.gcs_project or None)
        blob = client.bucket(settings.gcs_bucket).blob(_object_key(key))
        blob.download_to_filename(local_source.as_posix())
        return local_source

    source_path = _local_path(key)
    if not source_path.exists():
        raise FileNotFoundError(f"Missing local table: {source_path}")
    shutil.copy2(source_path, local_source)
    return local_source


def _backup_and_publish(table: str, updated_file: Path) -> AdminWriteResult:
    key = f"tables/{table}.parquet"
    backup_key = f"tables/backups/{table}.{_timestamp()}.parquet"

    if settings.ember_storage_backend == "gcs":
        client = gcs_storage.Client(project=settings.gcs_project or None)
        bucket = client.bucket(settings.gcs_bucket)
        source_blob = bucket.blob(_object_key(key))
        backup_blob_name = None
        if source_blob.exists():
            backup_blob_name = _object_key(backup_key)
            bucket.copy_blob(source_blob, bucket, backup_blob_name)
        source_blob.upload_from_filename(updated_file.as_posix())
        return AdminWriteResult(
            table=table,
            table_uri=f"gs://{settings.gcs_bucket}/{_object_key(key)}",
            backup_uri=(
                f"gs://{settings.gcs_bucket}/{backup_blob_name}"
                if backup_blob_name
                else None
            ),
        )

    table_path = _local_path(key)
    backup_path = _local_path(backup_key)
    table_path.parent.mkdir(parents=True, exist_ok=True)
    backup_uri = None
    if table_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(table_path, backup_path)
        backup_uri = backup_path.as_posix()
    shutil.copy2(updated_file, table_path)
    return AdminWriteResult(
        table=table,
        table_uri=table_path.as_posix(),
        backup_uri=backup_uri,
    )


def _rewrite_table(table: str, statements: list[tuple[str, list[Any]]]) -> AdminWriteResult:
    with tempfile.TemporaryDirectory(prefix=f"ember_admin_{table}_") as tmp:
        tmp_dir = Path(tmp)
        source = _read_table_to_temp(table, tmp_dir)
        output = tmp_dir / f"{table}.updated.parquet"

        conn = duckdb.connect(database=":memory:")
        conn.execute(f"CREATE TABLE edited AS SELECT * FROM read_parquet('{source.as_posix()}')")
        for sql, params in statements:
            conn.execute(sql, params)
        conn.execute(f"COPY edited TO '{output.as_posix()}' (FORMAT PARQUET)")

        return _backup_and_publish(table, output)


def upsert_scalar_metric(
    *,
    utility_id: str,
    wildfire_id: str,
    metric_key: str,
    value: float | None,
    unit: str | None,
    method: str | None,
    source_note: str | None,
    as_of_date: date | None,
) -> AdminWriteResult:
    """Add or replace one scalar metric row."""
    return _rewrite_table(
        "scalar_metrics",
        [
            (
                """
                DELETE FROM edited
                WHERE utility_id = ? AND wildfire_id = ? AND metric_key = ?
                """,
                [utility_id, wildfire_id, metric_key],
            ),
            (
                """
                INSERT INTO edited (
                    utility_id, wildfire_id, metric_key, value, unit, method, source_note, as_of_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [utility_id, wildfire_id, metric_key, value, unit, method, source_note, as_of_date],
            ),
        ],
    )


def upsert_utility_scalar_metric(
    *,
    utility_id: str,
    metric_key: str,
    value: float | None,
    unit: str | None,
    method: str | None,
    source_note: str | None,
    as_of_date: date | None,
) -> AdminWriteResult:
    """Add or replace one utility-wide case-study scalar metric."""
    return upsert_scalar_metric(
        utility_id=utility_id,
        wildfire_id=UTILITY_METRIC_SCOPE_ID,
        metric_key=metric_key,
        value=value,
        unit=unit,
        method=method,
        source_note=source_note,
        as_of_date=as_of_date,
    )


def upsert_pair_summary(
    *,
    utility_id: str,
    wildfire_id: str,
    has_overlap: bool,
    overlap_area_km2: float | None,
    overlap_pct_of_source: float | None,
) -> AdminWriteResult:
    """Add or replace one utility x wildfire overlap summary row."""
    return _rewrite_table(
        "pair_summary",
        [
            (
                """
                DELETE FROM edited
                WHERE utility_id = ? AND wildfire_id = ?
                """,
                [utility_id, wildfire_id],
            ),
            (
                """
                INSERT INTO edited (
                    utility_id, wildfire_id, has_overlap, overlap_area_km2,
                    overlap_pct_of_source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, now())
                """,
                [utility_id, wildfire_id, has_overlap, overlap_area_km2, overlap_pct_of_source],
            ),
        ],
    )


def replace_case_study_costs(
    *,
    utility_id: str,
    rows: list[CaseStudyCostInput],
) -> AdminWriteResult:
    """Replace all raw economic-impact rows for one utility."""
    with tempfile.TemporaryDirectory(prefix="ember_admin_case_study_costs_") as tmp:
        output = Path(tmp) / "case_study_costs.updated.parquet"
        conn = duckdb.connect(database=":memory:")
        conn.execute(
            """
            CREATE TABLE edited (
                utility_id VARCHAR,
                item_type VARCHAR,
                start_year INTEGER,
                end_year INTEGER,
                description VARCHAR,
                raw_cost DOUBLE,
                inflation_adjusted_cost DOUBLE,
                contributing_fires VARCHAR,
                source VARCHAR,
                method VARCHAR,
                degree_of_causation VARCHAR,
                description_and_notes VARCHAR,
                extra_fields_json VARCHAR
            )
            """
        )

        key = "tables/case_study_costs.parquet"
        existing_path: Path | None = None
        if settings.ember_storage_backend == "gcs":
            client = gcs_storage.Client(project=settings.gcs_project or None)
            blob = client.bucket(settings.gcs_bucket).blob(_object_key(key))
            if blob.exists():
                existing_path = Path(tmp) / "case_study_costs.source.parquet"
                blob.download_to_filename(existing_path.as_posix())
        else:
            local_path = _local_path(key)
            if local_path.exists():
                existing_path = local_path

        if existing_path is not None:
            existing_columns = {
                column[0]
                for column in conn.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)",
                    [existing_path.as_posix()],
                ).fetchall()
            }
            degree_of_causation = (
                "degree_of_causation"
                if "degree_of_causation" in existing_columns
                else "CAST(NULL AS VARCHAR)"
            )
            extra_fields_json = (
                "extra_fields_json"
                if "extra_fields_json" in existing_columns
                else "CAST(NULL AS VARCHAR)"
            )
            conn.execute(
                f"""
                INSERT INTO edited
                SELECT
                    utility_id, item_type, start_year, end_year,
                    description, raw_cost, inflation_adjusted_cost,
                    contributing_fires, source, method, {degree_of_causation},
                    description_and_notes, {extra_fields_json}
                FROM read_parquet(?)
                WHERE utility_id <> ?
                """,
                [existing_path.as_posix(), utility_id],
            )

        conn.executemany(
            """
            INSERT INTO edited VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                [
                    utility_id,
                    row.item_type,
                    row.start_year,
                    row.end_year,
                    row.description,
                    row.raw_cost,
                    row.inflation_adjusted_cost,
                    row.contributing_fires,
                    row.source,
                    row.method,
                    row.degree_of_causation,
                    row.description_and_notes,
                    row.extra_fields_json,
                ]
                for row in rows
            ],
        )
        conn.execute(f"COPY edited TO '{output.as_posix()}' (FORMAT PARQUET)")
        return _backup_and_publish("case_study_costs", output)
