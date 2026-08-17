"""DuckDB catalog access layer for selectors, pair facts, metrics, and geometry."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import duckdb

from core.manual_utilities import (
    DENVER_WATER_ID,
    DENVER_WATER_LATITUDE,
    DENVER_WATER_LONGITUDE,
    DENVER_WATER_NAME,
    DENVER_WATER_SERVICE_POINT,
    DENVER_WATER_STATE,
)
from core.models import (
    CaseStudy,
    CaseStudyCost,
    IntersectingServiceArea,
    IntersectingSourceLocation,
    IntersectingUtility,
    IntersectingWildfire,
    MetricValue,
    PairSummary,
    RasterAsset,
    Utility,
    UtilitySource,
    UTILITY_METRIC_SCOPE_ID,
    Wildfire,
    WildfireSummary,
)
from core.settings import settings
from core.storage import Storage


class Catalog:
    """Encapsulates all DuckDB SQL access behind typed methods."""

    def __init__(self, storage: Storage) -> None:
        """Initialize the in-memory database and process-local dataset cache."""
        self._storage = storage
        self._materialized: set[str] = set()
        self._dataset_cache: TemporaryDirectory[str] | None = None
        self._dataset_download_lock = Lock()
        self._materialization_lock = Lock()
        self._spatial_loaded = False
        self._conn = duckdb.connect(database=":memory:")
        duckdb_home = Path(".duckdb").resolve()
        duckdb_home.mkdir(parents=True, exist_ok=True)
        self._conn.execute(f"SET home_directory='{duckdb_home.as_posix()}';")
        self._conn.execute(f"SET extension_directory='{(duckdb_home / 'extensions').as_posix()}';")
        if settings.ember_storage_backend == "gcs":
            # DuckDB's Python-backed gcsfs adapter can deadlock while its worker waits for
            # the GIL. Stream each Parquet object to a private local cache instead, then let
            # DuckDB use its native filesystem without any callbacks into Python.
            self._dataset_cache = TemporaryDirectory(prefix="ember-catalog-")

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        """Expose the active DuckDB connection."""
        return self._conn

    def _dataset(self, name: str) -> str:
        if self._dataset_cache is not None:
            destination = Path(self._dataset_cache.name) / f"{name}.parquet"
            with self._dataset_download_lock:
                if not destination.exists():
                    partial = destination.with_suffix(".parquet.part")
                    try:
                        self._storage.download_to_path(
                            f"tables/{name}.parquet",
                            partial,
                        )
                        partial.replace(destination)
                    finally:
                        partial.unlink(missing_ok=True)
            return destination.as_posix()
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
        with self._materialization_lock:
            if name not in self._materialized:
                dataset = self._dataset(name).replace("'", "''")
                query = f'CREATE TABLE "{name}" AS SELECT * FROM read_parquet(\'{dataset}\')'
                params: list[object] = []
                if name == "wildfires" and settings.wildfire_state_list:
                    placeholders = ", ".join("?" for _ in settings.wildfire_state_list)
                    query += f" WHERE upper(state) IN ({placeholders})"
                    params.extend(settings.wildfire_state_list)
                self._conn.execute(query, params)
                self._materialized.add(name)
        return f'"{name}"'

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        """Return whether a materialized dataset contains a named column."""
        self._table(table_name)
        row = self._conn.execute(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_name = ? AND column_name = ?
            """,
            [table_name, column_name],
        ).fetchone()
        return bool(row and row[0])

    def list_utilities(self) -> list[Utility]:
        """List utility selector metadata without geometry payload."""
        utilities = self._table("utilities")
        has_service_area = (
            "service_area_geojson IS NOT NULL"
            if self._column_exists("utilities", "service_area_geojson")
            else "FALSE"
        )
        rows = self._conn.execute(
            f"""
            SELECT
                utility_id, name, state, source_area_name, centroid_lon, centroid_lat,
                geometry_geojson IS NOT NULL AS has_source_area,
                {has_service_area} AS has_service_area
            FROM {utilities}
            ORDER BY name
            """
        ).fetchall()
        utilities = [Utility(*row) for row in rows]
        existing_index = next(
            (
                index
                for index, utility in enumerate(utilities)
                if utility.utility_id == DENVER_WATER_ID
            ),
            None,
        )
        if existing_index is None:
            utilities.append(
                Utility(
                    utility_id=DENVER_WATER_ID,
                    name=DENVER_WATER_NAME,
                    state=DENVER_WATER_STATE,
                    source_area_name="Service location only",
                    centroid_lon=DENVER_WATER_LONGITUDE,
                    centroid_lat=DENVER_WATER_LATITUDE,
                    has_source_area=False,
                    has_service_area=True,
                )
            )
        elif not utilities[existing_index].has_service_area:
            utilities[existing_index] = replace(
                utilities[existing_index],
                has_service_area=True,
            )
        return sorted(utilities, key=lambda utility: utility.name)

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
            SELECT
                wildfire_id, name, ignition_date, containment_date, acres,
                state, county, centroid_lon, centroid_lat
            FROM {self._table("wildfires")}
            {where_clause}
            ORDER BY ignition_date DESC, name
            """,
            params,
        ).fetchall()
        return [Wildfire(*row) for row in rows]

    def get_overview_geojson(self, table: str) -> dict:
        """Return all source areas or wildfire perimeters as one GeoJSON layer."""
        if table == "utilities":
            source_table = "utilities"
            id_column = "utility_id"
            property_columns = ["name", "state", "source_area_name"]
            geometry_column = "geometry_geojson"
        elif table == "service_areas":
            source_table = "utilities"
            id_column = "utility_id"
            property_columns = ["name", "state"]
            geometry_column = "service_area_geojson"
            if not self._column_exists(source_table, geometry_column):
                return {"type": "FeatureCollection", "features": []}
        elif table == "wildfires":
            source_table = "wildfires"
            id_column = "wildfire_id"
            property_columns = ["name", "state", "acres"]
            geometry_column = "geometry_geojson"
        else:
            raise ValueError(f"Unsupported overview geometry table: {table}")

        selected_columns = ", ".join([id_column, *property_columns, geometry_column])
        rows = self._conn.execute(
            f"SELECT {selected_columns} FROM {self._table(source_table)}"
        ).fetchall()
        features = []
        for row in rows:
            geometry_value = row[-1]
            if geometry_value is None:
                continue
            geometry = (
                json.loads(geometry_value)
                if isinstance(geometry_value, str)
                else geometry_value
            )
            if geometry.get("type") == "GeometryCollection":
                polygon_parts = []
                for part in geometry.get("geometries", []):
                    if part.get("type") == "Polygon":
                        polygon_parts.append(part["coordinates"])
                    elif part.get("type") == "MultiPolygon":
                        polygon_parts.extend(part["coordinates"])
                if polygon_parts:
                    geometry = {
                        "type": "MultiPolygon",
                        "coordinates": polygon_parts,
                    }
            properties = {id_column: row[0]}
            properties.update(
                {
                    column: value
                    for column, value in zip(property_columns, row[1:-1], strict=True)
                }
            )
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties,
                }
            )
        if table == "service_areas" and not any(
            feature["properties"].get("utility_id") == DENVER_WATER_ID
            for feature in features
        ):
            features.append(deepcopy(DENVER_WATER_SERVICE_POINT))
        return {"type": "FeatureCollection", "features": features}

    def get_pair_summary(self, utility_id: str, wildfire_id: str) -> PairSummary:
        """Return overlap summary for a pair, treating a missing row as no overlap.

        `pair_summary` only stores overlapping pairs, so an absent row means the fire
        perimeter does not intersect the source area (the app's "No direct impact" state).
        """
        impact_basis = (
            "impact_basis"
            if self._column_exists("pair_summary", "impact_basis")
            else "CAST(NULL AS VARCHAR)"
        )
        row = self._conn.execute(
            f"""
            SELECT
                utility_id, wildfire_id, has_overlap, overlap_area_km2,
                overlap_pct_of_source, {impact_basis}
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

    def list_yearly_burned_area(self) -> list[tuple[int, float]]:
        """Sum recorded wildfire acres by ignition year and convert to square kilometers."""
        rows = self._conn.execute(
            f"""
            SELECT
                EXTRACT(YEAR FROM ignition_date)::INTEGER AS ignition_year,
                SUM(acres) * 0.0040468564224 AS burned_area_km2
            FROM {self._table("wildfires")}
            WHERE ignition_date IS NOT NULL
              AND acres IS NOT NULL
            GROUP BY ignition_year
            ORDER BY ignition_year
            """
        ).fetchall()
        return [(int(year), float(area)) for year, area in rows]

    def list_yearly_intersected_area(self) -> list[tuple[int, float]]:
        """Sum utility source/service-area overlap with wildfires by ignition year."""
        has_service_geometry = self._column_exists(
            "utilities", "service_area_geojson"
        )
        if has_service_geometry and not self._spatial_loaded:
            self._conn.execute("LOAD spatial")
            self._spatial_loaded = True
        impact_filter = (
            """
            AND (
                p.impact_basis LIKE '%source_area%'
                OR p.impact_basis LIKE '%service_area%'
                OR p.impact_basis IS NULL
            )
            """
            if self._column_exists("pair_summary", "impact_basis")
            else ""
        )
        service_area_expression = (
            """
            WHEN p.overlap_area_km2 IS NULL
             AND p.impact_basis LIKE '%service_area%'
             AND u.service_area_geojson IS NOT NULL
            THEN ST_Area(
                ST_Intersection(
                    ST_Transform(
                        ST_GeomFromGeoJSON(CAST(u.service_area_geojson AS VARCHAR)),
                        'EPSG:4326',
                        'EPSG:5070',
                        always_xy := true
                    ),
                    ST_Transform(
                        ST_GeomFromGeoJSON(CAST(w.geometry_geojson AS VARCHAR)),
                        'EPSG:4326',
                        'EPSG:5070',
                        always_xy := true
                    )
                )
            ) / 1.0e6
            """
            if has_service_geometry
            and self._column_exists("pair_summary", "impact_basis")
            else ""
        )
        rows = self._conn.execute(
            f"""
            WITH intersections AS (
                SELECT
                    EXTRACT(YEAR FROM w.ignition_date)::INTEGER AS ignition_year,
                    CASE
                        WHEN p.overlap_area_km2 IS NOT NULL
                        THEN p.overlap_area_km2
                        {service_area_expression}
                        ELSE 0.0
                    END AS intersected_area_km2
                FROM {self._table("pair_summary")} p
                JOIN {self._table("wildfires")} w USING (wildfire_id)
                JOIN {self._table("utilities")} u USING (utility_id)
                WHERE p.has_overlap
                  AND w.ignition_date IS NOT NULL
                  {impact_filter}
            )
            SELECT
                ignition_year,
                SUM(intersected_area_km2)::DOUBLE AS intersected_area_km2
            FROM intersections
            GROUP BY ignition_year
            ORDER BY ignition_year
            """
        ).fetchall()
        return [(int(year), float(area)) for year, area in rows]

    def list_intersecting_wildfires(
        self, utility_id: str, year_min: int, year_max: int
    ) -> list[IntersectingWildfire]:
        """List wildfires overlapping a utility's source area within a year range.

        Returns overlap facts and display geometry in one query so the view can render
        both the fire table and the map perimeters without per-fire follow-up lookups.
        Ordered by overlap share (largest first).
        """
        impact_basis = (
            "p.impact_basis"
            if self._column_exists("pair_summary", "impact_basis")
            else "CAST(NULL AS VARCHAR)"
        )
        rows = self._conn.execute(
            f"""
            SELECT
                w.wildfire_id,
                w.name,
                EXTRACT(YEAR FROM w.ignition_date)::INTEGER AS ignition_year,
                w.acres,
                p.overlap_area_km2,
                p.overlap_pct_of_source,
                w.geometry_geojson,
                {impact_basis}
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
        source_area_filter = (
            "AND p.impact_basis LIKE '%source_area%'"
            if self._column_exists("pair_summary", "impact_basis")
            else ""
        )
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
              {source_area_filter}
            ORDER BY p.overlap_pct_of_source DESC NULLS LAST, p.overlap_area_km2 DESC NULLS LAST
            """,
            [wildfire_id],
        ).fetchall()
        return [IntersectingUtility(*row) for row in rows]

    def list_intersecting_service_areas(
        self, wildfire_id: str
    ) -> list[IntersectingServiceArea]:
        """List utility service areas overlapped by a selected wildfire."""
        if not self._column_exists("pair_summary", "impact_basis"):
            return []
        if not self._column_exists("utilities", "service_area_geojson"):
            return []
        rows = self._conn.execute(
            f"""
            SELECT
                u.utility_id,
                u.name,
                u.state,
                u.service_area_geojson
            FROM {self._table("pair_summary")} p
            JOIN {self._table("utilities")} u USING (utility_id)
            WHERE p.wildfire_id = ?
              AND p.has_overlap
              AND p.impact_basis LIKE '%service_area%'
              AND u.service_area_geojson IS NOT NULL
            ORDER BY u.name, u.state
            """,
            [wildfire_id],
        ).fetchall()
        return [IntersectingServiceArea(*row) for row in rows]

    def list_intersecting_source_locations(
        self, wildfire_id: str
    ) -> list[IntersectingSourceLocation]:
        """List connected surface-water points contained by a selected wildfire."""
        if not self._storage.exists("tables/source_fire_locations.parquet"):
            return []
        rows = self._conn.execute(
            f"""
            SELECT
                location.utility_id,
                utility.name,
                location.source_id,
                location.source_name,
                location.source_type,
                location.depth,
                location.source_lon,
                location.source_lat
            FROM {self._table("source_fire_locations")} location
            JOIN {self._table("utilities")} utility USING (utility_id)
            WHERE location.wildfire_id = ?
            ORDER BY location.source_name, utility.name, location.depth
            """,
            [wildfire_id],
        ).fetchall()
        return [IntersectingSourceLocation(*row) for row in rows]

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

    def get_utility_scalar(
        self, utility_id: str, metric_key: str
    ) -> MetricValue | None:
        """Return a utility-scoped metric, falling back to a legacy pair value."""
        row = self._conn.execute(
            f"""
            SELECT
                utility_id, wildfire_id, metric_key, value, unit, method,
                source_note, as_of_date
            FROM {self._table("scalar_metrics")}
            WHERE utility_id = ?
              AND metric_key = ?
            ORDER BY
                CASE WHEN wildfire_id = ? THEN 0 ELSE 1 END,
                as_of_date DESC NULLS LAST
            LIMIT 1
            """,
            [utility_id, metric_key, UTILITY_METRIC_SCOPE_ID],
        ).fetchone()
        return MetricValue(*row) if row is not None else None

    def list_case_study_costs(self, utility_id: str) -> list[CaseStudyCost]:
        """Return all raw economic-impact inputs for one utility case study."""
        if not self._storage.exists("tables/case_study_costs.parquet"):
            return []
        degree_of_causation = (
            "coalesce(degree_of_causation, '')"
            if self._column_exists("case_study_costs", "degree_of_causation")
            else "''"
        )
        extra_fields_json = (
            "coalesce(CAST(extra_fields_json AS VARCHAR), '')"
            if self._column_exists("case_study_costs", "extra_fields_json")
            else "''"
        )
        rows = self._conn.execute(
            f"""
            SELECT
                utility_id, item_type, start_year, end_year,
                description, raw_cost, inflation_adjusted_cost,
                contributing_fires, source, method, {degree_of_causation},
                description_and_notes, {extra_fields_json}
            FROM {self._table("case_study_costs")}
            WHERE utility_id = ?
            ORDER BY start_year, end_year, item_type, description
            """,
            [utility_id],
        ).fetchall()
        return [CaseStudyCost(*row) for row in rows]

    def list_case_studies(self) -> list[CaseStudy]:
        """List utilities that have uploaded case-study CSV rows."""
        if not self._storage.exists("tables/case_study_costs.parquet"):
            return []
        utility_ids = {
            row[0]
            for row in self._conn.execute(
            f"""
            SELECT DISTINCT utility_id
            FROM {self._table("case_study_costs")}
            """
            ).fetchall()
        }
        case_studies = [
            CaseStudy(utility.utility_id, utility.name, utility.state)
            for utility in self.list_utilities()
            if utility.utility_id in utility_ids
        ]
        return sorted(
            case_studies,
            key=lambda case_study: (
                case_study.utility_name,
                case_study.utility_state,
            ),
        )

    def list_utility_sources(self, utility_id: str) -> list[UtilitySource]:
        """List direct and transitively connected surface-water sources.

        California's source-connection data records a utility as the source of
        another utility. The recursive query follows those supplier links until
        it reaches natural sources, while retaining supplier rows and preventing
        cycles. A source reachable through multiple paths is shown at its shortest
        connection depth.
        """
        if not self._storage.exists("tables/utility_sources.parquet"):
            return []
        sources_table = self._table("utility_sources")
        has_locations = self._column_exists("utility_sources", "source_lon")
        direct_lon = "us.source_lon" if has_locations else "CAST(NULL AS DOUBLE)"
        direct_lat = "us.source_lat" if has_locations else "CAST(NULL AS DOUBLE)"
        upstream_lon = (
            "upstream.source_lon" if has_locations else "CAST(NULL AS DOUBLE)"
        )
        upstream_lat = (
            "upstream.source_lat" if has_locations else "CAST(NULL AS DOUBLE)"
        )
        rows = self._conn.execute(
            f"""
            WITH RECURSIVE source_network AS (
                SELECT
                    us.utility_id AS root_utility_id,
                    us.source_id,
                    us.source_name,
                    us.source_type,
                    us.source_utility_id,
                    1 AS depth,
                    us.purchased,
                    us.average_source_usage,
                    us.average_source_method,
                    {direct_lon} AS source_lon,
                    {direct_lat} AS source_lat,
                    [us.utility_id, coalesce(us.source_utility_id, us.source_id)] AS path
                FROM {sources_table} us
                WHERE us.utility_id = ?

                UNION ALL

                SELECT
                    network.root_utility_id,
                    upstream.source_id,
                    upstream.source_name,
                    upstream.source_type,
                    upstream.source_utility_id,
                    network.depth + 1,
                    upstream.purchased,
                    upstream.average_source_usage,
                    upstream.average_source_method,
                    {upstream_lon},
                    {upstream_lat},
                    list_append(
                        network.path,
                        coalesce(upstream.source_utility_id, upstream.source_id)
                    )
                FROM source_network network
                JOIN {sources_table} upstream
                  ON upstream.utility_id = network.source_utility_id
                WHERE network.source_utility_id IS NOT NULL
                  AND network.depth < 20
                  AND NOT list_contains(
                      network.path,
                      coalesce(upstream.source_utility_id, upstream.source_id)
                  )
            ),
            ranked AS (
                SELECT *,
                       row_number() OVER (
                           PARTITION BY root_utility_id, source_id
                           ORDER BY depth, source_name
                       ) AS source_rank
                FROM source_network
            )
            SELECT
                root_utility_id,
                source_id,
                source_name,
                source_type,
                depth,
                purchased,
                average_source_usage,
                average_source_method,
                source_lon,
                source_lat
            FROM ranked
            WHERE source_rank = 1
            ORDER BY depth, source_type, source_name
            """,
            [utility_id],
        ).fetchall()
        return [UtilitySource(*row) for row in rows]

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

    def get_utility_geojson(self, utility_id: str, area_type: str) -> dict | None:
        """Return a utility's source or service area, when that geometry exists."""
        if area_type == "source":
            geometry_column = "geometry_geojson"
        elif area_type == "service":
            geometry_column = "service_area_geojson"
        else:
            raise ValueError(f"Unsupported utility area type: {area_type}")
        manual_service_point = (
            area_type == "service" and utility_id == DENVER_WATER_ID
        )
        if not self._column_exists("utilities", geometry_column):
            if manual_service_point:
                return deepcopy(DENVER_WATER_SERVICE_POINT)
            return None
        row = self._conn.execute(
            f"""
            SELECT {geometry_column}
            FROM {self._table("utilities")}
            WHERE utility_id = ?
            LIMIT 1
            """,
            [utility_id],
        ).fetchone()
        if row is None or row[0] is None:
            if manual_service_point:
                return deepcopy(DENVER_WATER_SERVICE_POINT)
            return None
        geometry = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {"id": utility_id, "area_type": area_type},
        }

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
