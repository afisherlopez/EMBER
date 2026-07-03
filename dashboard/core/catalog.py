"""DuckDB catalog access layer for selectors, pair facts, metrics, and geometry."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from core.models import (
    IntersectingUtility,
    IntersectingWildfire,
    MetricValue,
    PairSummary,
    RasterAsset,
    Utility,
    Wildfire,
    WildfireSummary,
)
from core.settings import settings
from core.storage import Storage


class Catalog:
    """Encapsulates all DuckDB SQL access behind typed methods."""

    def __init__(self, storage: Storage) -> None:
        """Initialize in-memory DuckDB connection, registering GCS access when needed."""
        self._storage = storage
        self._materialized: set[str] = set()
        self._conn = duckdb.connect(database=":memory:")
        duckdb_home = Path(".duckdb").resolve()
        duckdb_home.mkdir(parents=True, exist_ok=True)
        self._conn.execute(f"SET home_directory='{duckdb_home.as_posix()}';")
        self._conn.execute(f"SET extension_directory='{(duckdb_home / 'extensions').as_posix()}';")
        if settings.ember_storage_backend == "gcs":
            self._register_gcs_filesystem()

    def _register_gcs_filesystem(self) -> None:
        """Register a native GCS filesystem so DuckDB reads ``gs://`` Parquet directly.

        DuckDB's built-in ``httpfs`` can only reach GCS through the S3-compatibility API,
        which requires separate, long-lived HMAC keys (an AWS-shaped credential that GCS
        org policy often disables). Registering ``gcsfs`` instead lets DuckDB authenticate
        with Google Application Default Credentials — the same service account used by
        GDAL/TiTiler for COGs and by ``core/storage.py`` for object reads. The result is a
        single credential for the whole app: ``GOOGLE_APPLICATION_CREDENTIALS`` (a JSON key)
        locally, or the attached service account on managed runtimes like Cloud Run.
        """
        import gcsfs

        token = settings.google_application_credentials or None
        self._conn.register_filesystem(gcsfs.GCSFileSystem(token=token))

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Expose the active DuckDB connection."""
        return self._conn

    def _dataset(self, name: str) -> str:
        return self._storage.dataset_uri(name)

    def _table(self, name: str) -> str:
        """Materialize a dataset into a local DuckDB table once, then reuse it.

        The published Parquet lives in GCS; reading ``gs://`` on every query re-opens the
        remote file (seconds of network latency per call, repeated on every Streamlit
        rerun). Loading each table into the in-memory DuckDB on first access pays that
        network cost a single time per process, after which all queries are local and
        return in milliseconds. The connection is cached for the app's lifetime, so new
        data published to GCS is picked up on the next app restart.
        """
        if name not in self._materialized:
            self._conn.execute(
                f'CREATE TABLE "{name}" AS SELECT * FROM read_parquet(\'{self._dataset(name)}\')'
            )
            self._materialized.add(name)
        return f'"{name}"'

    def list_utilities(self) -> list[Utility]:
        """List utility selector metadata without geometry payload."""
        rows = self._conn.execute(
            f"""
            SELECT utility_id, name, state, source_area_name, centroid_lon, centroid_lat
            FROM {self._table("utilities")}
            ORDER BY name
            """
        ).fetchall()
        return [Utility(*row) for row in rows]

    def list_wildfires(self, state: str | None = None, year: int | None = None) -> list[Wildfire]:
        """List wildfire selector rows with optional location/year filtering."""
        where_parts: list[str] = []
        params: list[object] = []
        if state:
            where_parts.append("state = ?")
            params.append(state)
        if year is not None:
            where_parts.append("EXTRACT(YEAR FROM ignition_date) = ?")
            params.append(year)
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = self._conn.execute(
            f"""
            SELECT wildfire_id, name, ignition_date, containment_date, state, county, centroid_lon, centroid_lat
            FROM {self._table("wildfires")}
            {where_clause}
            ORDER BY ignition_date DESC, name
            """,
            params,
        ).fetchall()
        return [Wildfire(*row) for row in rows]

    def get_pair_summary(self, utility_id: str, wildfire_id: str) -> PairSummary:
        """Return overlap summary for a pair, treating a missing row as no overlap.

        `pair_summary` only stores overlapping pairs, so an absent row means the fire
        perimeter does not intersect the source area (the app's "No direct impact" state).
        """
        row = self._conn.execute(
            f"""
            SELECT utility_id, wildfire_id, has_overlap, overlap_area_km2, overlap_pct_of_source
            FROM {self._table("pair_summary")}
            WHERE utility_id = ? AND wildfire_id = ?
            """,
            [utility_id, wildfire_id],
        ).fetchone()
        if row is None:
            return PairSummary(utility_id, wildfire_id, False, None, None)
        return PairSummary(*row)

    def wildfire_year_bounds(self) -> tuple[int, int] | None:
        """Return `(min_year, max_year)` of wildfire ignition years, or None if empty."""
        row = self._conn.execute(
            f"""
            SELECT MIN(EXTRACT(YEAR FROM ignition_date))::INTEGER,
                   MAX(EXTRACT(YEAR FROM ignition_date))::INTEGER
            FROM {self._table("wildfires")}
            WHERE ignition_date IS NOT NULL
            """
        ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return int(row[0]), int(row[1])

    def list_intersecting_wildfires(
        self, utility_id: str, year_min: int, year_max: int
    ) -> list[IntersectingWildfire]:
        """List wildfires overlapping a utility's source area within a year range.

        Returns overlap facts and display geometry in one query so the view can render
        both the fire table and the map perimeters without per-fire follow-up lookups.
        Ordered by overlap share (largest first).
        """
        rows = self._conn.execute(
            f"""
            SELECT
                w.wildfire_id,
                w.name,
                EXTRACT(YEAR FROM w.ignition_date)::INTEGER AS ignition_year,
                w.acres,
                p.overlap_area_km2,
                p.overlap_pct_of_source,
                w.geometry_geojson
            FROM {self._table("pair_summary")} p
            JOIN {self._table("wildfires")} w USING (wildfire_id)
            WHERE p.utility_id = ?
              AND p.has_overlap
              AND EXTRACT(YEAR FROM w.ignition_date) BETWEEN ? AND ?
            ORDER BY p.overlap_pct_of_source DESC NULLS LAST, w.acres DESC NULLS LAST
            """,
            [utility_id, year_min, year_max],
        ).fetchall()
        return [IntersectingWildfire(*row) for row in rows]

    def get_wildfire_summary(self, wildfire_id: str) -> WildfireSummary | None:
        """Return header facts (name, year, total burned acres, state) for one fire."""
        row = self._conn.execute(
            f"""
            SELECT
                wildfire_id,
                name,
                EXTRACT(YEAR FROM ignition_date)::INTEGER AS ignition_year,
                acres,
                state
            FROM {self._table("wildfires")}
            WHERE wildfire_id = ?
            LIMIT 1
            """,
            [wildfire_id],
        ).fetchone()
        if row is None:
            return None
        return WildfireSummary(*row)

    def list_intersecting_utilities(self, wildfire_id: str) -> list[IntersectingUtility]:
        """List utilities whose source area overlaps a given fire, with overlap facts.

        The inverse of ``list_intersecting_wildfires``: joins overlapping pairs to the
        utilities table and carries display geometry so the view can render both the table
        and the source-area outlines without per-utility follow-up lookups. Ordered by
        overlap share (largest first).
        """
        rows = self._conn.execute(
            f"""
            SELECT
                u.utility_id,
                u.name,
                u.state,
                u.source_area_name,
                EXTRACT(YEAR FROM w.ignition_date)::INTEGER AS ignition_year,
                p.overlap_area_km2,
                p.overlap_pct_of_source,
                u.geometry_geojson
            FROM {self._table("pair_summary")} p
            JOIN {self._table("utilities")} u USING (utility_id)
            JOIN {self._table("wildfires")} w USING (wildfire_id)
            WHERE p.wildfire_id = ?
              AND p.has_overlap
            ORDER BY p.overlap_pct_of_source DESC NULLS LAST, p.overlap_area_km2 DESC NULLS LAST
            """,
            [wildfire_id],
        ).fetchall()
        return [IntersectingUtility(*row) for row in rows]

    def get_scalar(self, utility_id: str, wildfire_id: str, metric_key: str) -> MetricValue | None:
        """Return scalar metric payload for a selected pair and metric."""
        row = self._conn.execute(
            f"""
            SELECT utility_id, wildfire_id, metric_key, value, unit, method, source_note, as_of_date
            FROM {self._table("scalar_metrics")}
            WHERE utility_id = ? AND wildfire_id = ? AND metric_key = ?
            LIMIT 1
            """,
            [utility_id, wildfire_id, metric_key],
        ).fetchone()
        if row is None:
            return None
        return MetricValue(*row)

    def get_raster_asset(self, utility_id: str, wildfire_id: str, metric_key: str) -> RasterAsset | None:
        """Return raster asset payload for a selected pair and metric."""
        row = self._conn.execute(
            f"""
            SELECT utility_id, wildfire_id, metric_key, cog_uri, units, colormap_name, rescale_min, rescale_max, nodata, as_of_date
            FROM {self._table("raster_assets")}
            WHERE utility_id = ? AND wildfire_id = ? AND metric_key = ?
            LIMIT 1
            """,
            [utility_id, wildfire_id, metric_key],
        ).fetchone()
        if row is None:
            return None
        return RasterAsset(*row)

    def get_geojson(self, table: str, row_id: str, simplify_tolerance: float) -> dict:
        """Return GeoJSON geometry for one utility or wildfire id."""
        del simplify_tolerance
        id_col = "utility_id" if table == "utilities" else "wildfire_id"
        row = self._conn.execute(
            f"""
            SELECT geometry_geojson
            FROM {self._table(table)}
            WHERE {id_col} = ?
            LIMIT 1
            """,
            [row_id],
        ).fetchone()
        if row is None or row[0] is None:
            raise KeyError(f"Missing geometry in {table} for id={row_id}.")
        geometry = json.loads(row[0])
        return {"type": "Feature", "geometry": geometry, "properties": {"id": row_id}}


def config_dir_from_module() -> Path:
    """Resolve the project config directory from module location."""
    return Path(__file__).resolve().parents[1] / "config"
