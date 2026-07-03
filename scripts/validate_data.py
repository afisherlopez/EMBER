"""Validate that EMBER sample datasets and referenced raster assets are present."""

from __future__ import annotations

from pathlib import Path

import duckdb


def validate_data(data_root: Path, config_dir: Path) -> None:
    """Validate dataset shape and referenced assets for a catalog root."""
    conn = duckdb.connect(database=":memory:")

    tables_dir = data_root / "tables"
    required = [
        "utilities.parquet",
        "wildfires.parquet",
        "pair_summary.parquet",
        "scalar_metrics.parquet",
        "raster_assets.parquet",
    ]
    missing = [name for name in required if not (tables_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing required tables: {', '.join(missing)}")

    metrics_rows = conn.execute(
        f"SELECT DISTINCT metric_key FROM read_parquet('{(tables_dir / 'scalar_metrics.parquet').as_posix()}')"
    ).fetchall()
    raster_rows = conn.execute(
        f"SELECT DISTINCT metric_key, cog_uri FROM read_parquet('{(tables_dir / 'raster_assets.parquet').as_posix()}')"
    ).fetchall()
    metric_keys = {row[0] for row in metrics_rows} | {row[0] for row in raster_rows}

    metrics_yaml = (config_dir / "metrics.yaml").read_text(encoding="utf-8")
    missing_metric_definitions = [key for key in sorted(metric_keys) if key not in metrics_yaml]
    if missing_metric_definitions:
        raise ValueError(f"Metric keys missing in metrics.yaml: {', '.join(missing_metric_definitions)}")

    for _, cog_uri in raster_rows:
        if not isinstance(cog_uri, str):
            raise ValueError("Raster asset `cog_uri` must be a string.")
        # Only local (non-gs://) assets can be checked on the filesystem here.
        if not cog_uri.startswith("gs://"):
            local_path = Path(cog_uri.replace("file://", "", 1))
            if not local_path.exists():
                raise FileNotFoundError(f"Missing raster file: {local_path}")


if __name__ == "__main__":
    validate_data(Path("./data"), Path("./config"))
