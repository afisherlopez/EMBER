"""Create a tiny local sample EMBER dataset for compose and tests."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import rasterio
from rasterio.transform import from_origin


def _write_sample_raster(path: Path, value_scale: float) -> None:
    """Write a small web-mercator raster usable by TiTiler in local mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    data = np.array(
        [[10, 12, 14, 16], [8, 10, 11, 13], [5, 6, 8, 9], [2, 3, 4, 5]],
        dtype=np.float32,
    )
    scaled = data * value_scale
    transform = from_origin(-11688500.0, 4866000.0, 500.0, 500.0)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=scaled.shape[0],
        width=scaled.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(scaled, 1)


def bootstrap_sample_data(data_root: Path) -> None:
    """Build sample tables and rasters if they do not already exist."""
    tables_dir = data_root / "tables"
    cogs_dir = data_root / "cogs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    cogs_dir.mkdir(parents=True, exist_ok=True)

    sediment_path = (cogs_dir / "sediment_yield_increase_example.tif").resolve()
    turbidity_path = (cogs_dir / "turbidity_increase_example.tif").resolve()
    _write_sample_raster(sediment_path, value_scale=1.0)
    _write_sample_raster(turbidity_path, value_scale=0.35)

    if (tables_dir / "utilities.parquet").exists() and (tables_dir / "wildfires.parquet").exists():
        return

    conn = duckdb.connect(database=":memory:")
    duckdb_home = (data_root / ".duckdb").resolve()
    duckdb_home.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET home_directory='{duckdb_home.as_posix()}';")
    conn.execute(f"SET extension_directory='{(duckdb_home / 'extensions').as_posix()}';")
    conn.execute(
        """
        CREATE TABLE utilities AS
        SELECT * FROM (
            VALUES
                (
                    'denver-water',
                    'Denver Water',
                    'CO',
                    'Upper South Platte',
                    '{"type":"Polygon","coordinates":[[[-105.75,39.30],[-105.45,39.30],[-105.45,39.55],[-105.75,39.55],[-105.75,39.30]]]}',
                    -105.60,
                    39.43,
                    NOW()
                ),
                (
                    'foothills-utility',
                    'Foothills Utility',
                    'CA',
                    'Foothills Basin',
                    '{"type":"Polygon","coordinates":[[[-121.55,39.65],[-121.25,39.65],[-121.25,39.90],[-121.55,39.90],[-121.55,39.65]]]}',
                    -121.40,
                    39.78,
                    NOW()
                )
        ) AS t(utility_id, name, state, source_area_name, geometry_geojson, centroid_lon, centroid_lat, updated_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE wildfires AS
        SELECT * FROM (
            VALUES
                (
                    'hayman-2002',
                    'Hayman Fire',
                    DATE '2002-06-08',
                    DATE '2002-07-02',
                    137760.0,
                    'CO',
                    'Douglas',
                    -105.63,
                    39.50,
                    '{"type":"Polygon","coordinates":[[[-105.70,39.35],[-105.40,39.35],[-105.40,39.60],[-105.70,39.60],[-105.70,39.35]]]}',
                    'NIFC',
                    NOW()
                ),
                (
                    'camp-2018',
                    'Camp Fire',
                    DATE '2018-11-08',
                    DATE '2018-11-25',
                    153336.0,
                    'CA',
                    'Butte',
                    -121.45,
                    39.75,
                    '{"type":"Polygon","coordinates":[[[-121.52,39.70],[-121.35,39.70],[-121.35,39.88],[-121.52,39.88],[-121.52,39.70]]]}',
                    'NIFC',
                    NOW()
                )
        ) AS t(
            wildfire_id, name, ignition_date, containment_date, acres, state, county,
            centroid_lon, centroid_lat, geometry_geojson, source, updated_at
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE pair_summary AS
        SELECT * FROM (
            VALUES
                ('denver-water', 'hayman-2002', TRUE, 580.2, 56.4, NOW()),
                ('denver-water', 'camp-2018', FALSE, NULL, NULL, NOW()),
                ('foothills-utility', 'hayman-2002', FALSE, NULL, NULL, NOW()),
                ('foothills-utility', 'camp-2018', TRUE, 210.1, 33.7, NOW())
        ) AS t(utility_id, wildfire_id, has_overlap, overlap_area_km2, overlap_pct_of_source, updated_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE scalar_metrics AS
        SELECT * FROM (
            VALUES
                ('denver-water', 'hayman-2002', 'econ_impact_5yr', 25000000.0, 'USD', 'model-v1', 'sample estimate', DATE '2026-01-01'),
                ('denver-water', 'hayman-2002', 'total_econ_impact', 68000000.0, 'USD', 'model-v1', 'sample estimate', DATE '2026-01-01'),
                ('denver-water', 'camp-2018', 'econ_impact_5yr', NULL, 'USD', 'model-v1', 'pending metric', DATE '2026-01-01'),
                ('foothills-utility', 'camp-2018', 'econ_impact_5yr', 12000000.0, 'USD', 'model-v1', 'sample estimate', DATE '2026-01-01'),
                ('foothills-utility', 'camp-2018', 'total_econ_impact', 33000000.0, 'USD', 'model-v1', 'sample estimate', DATE '2026-01-01')
        ) AS t(utility_id, wildfire_id, metric_key, value, unit, method, source_note, as_of_date)
        """
    )
    conn.execute(
        """
        CREATE TABLE raster_assets AS
        SELECT * FROM (
            VALUES
                (
                    'denver-water',
                    'hayman-2002',
                    'sediment_yield_increase',
                    ?,
                    'tonnes/km^2/yr',
                    'ylorbr',
                    0.0,
                    100.0,
                    -9999.0,
                    DATE '2026-01-01'
                ),
                (
                    'denver-water',
                    'hayman-2002',
                    'turbidity_increase',
                    ?,
                    'NTU',
                    'ylorbr',
                    0.0,
                    50.0,
                    -9999.0,
                    DATE '2026-01-01'
                ),
                (
                    'foothills-utility',
                    'camp-2018',
                    'sediment_yield_increase',
                    ?,
                    'tonnes/km^2/yr',
                    'ylorbr',
                    0.0,
                    100.0,
                    -9999.0,
                    DATE '2026-01-01'
                )
        ) AS t(
            utility_id, wildfire_id, metric_key, cog_uri, units, colormap_name,
            rescale_min, rescale_max, nodata, as_of_date
        )
        """,
        # Store plain absolute paths so DuckDB and GDAL/TiTiler can both open them locally
        # (file:// URIs break DuckDB on paths with spaces and are not understood by GDAL).
        [sediment_path.as_posix(), turbidity_path.as_posix(), sediment_path.as_posix()],
    )

    for name in ["utilities", "wildfires", "pair_summary", "scalar_metrics", "raster_assets"]:
        conn.execute(f"COPY {name} TO '{(tables_dir / f'{name}.parquet').as_posix()}' (FORMAT PARQUET)")


if __name__ == "__main__":
    bootstrap_sample_data(Path("./data"))
