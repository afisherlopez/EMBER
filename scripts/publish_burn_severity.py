"""Convert annual MTBS CONUS raster archives to COGs and publish a manifest.

The source uploads are LZW-compressed, striped GeoTIFFs inside one ZIP per year.
They are converted to tiled Cloud-Optimized GeoTIFFs so the tile service can answer map
tile requests with efficient GCS range reads.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path

SOURCE_PREFIX = "data_main_gcs/EMBER/fire_burn_severity/CONUS_burn_severity_data"
OUTPUT_PREFIX = "data_main_gcs/EMBER/fire_burn_severity/cogs"
MANIFEST_PATH = f"{OUTPUT_PREFIX}/manifest.json"
ACTIVE_STATES = ("WA", "OR", "CA", "CO")
_ARCHIVE_PATTERN = re.compile(r"mtbs_CONUS_((?:19|20)\d{2})\.zip$", re.IGNORECASE)


def _parse_years(value: str) -> set[int]:
    """Parse comma-separated years and inclusive ranges."""
    years: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if end < start:
                raise argparse.ArgumentTypeError(f"Invalid descending year range: {token}")
            years.update(range(start, end + 1))
        else:
            years.add(int(token))
    if not years:
        raise argparse.ArgumentTypeError("At least one year is required.")
    return years


def _convert_archive(remote_archive: str, year: int, force: bool) -> dict[str, object]:
    """Convert and upload one annual archive; safe to run in a worker process."""
    import gcsfs
    import rasterio
    from rasterio.shutil import copy as rio_copy

    fs = gcsfs.GCSFileSystem()
    remote_cog = f"{OUTPUT_PREFIX}/mtbs_CONUS_{year}.tif"
    cog_uri = f"gs://{remote_cog}"
    if fs.exists(remote_cog) and not force:
        return {
            "year": year,
            "cog_uri": cog_uri,
            "size_bytes": int(fs.info(remote_cog)["size"]),
            "status": "existing",
        }

    import zipfile

    with tempfile.TemporaryDirectory(prefix=f"mtbs_severity_{year}_") as tmp:
        root = Path(tmp)
        archive_path = root / Path(remote_archive).name
        source_tif = root / f"mtbs_CONUS_{year}.tif"
        output_cog = root / f"mtbs_CONUS_{year}.cog.tif"
        fs.get(remote_archive, archive_path.as_posix())

        with zipfile.ZipFile(archive_path) as archive:
            tif_members = [
                name for name in archive.namelist() if name.lower().endswith((".tif", ".tiff"))
            ]
            if len(tif_members) != 1:
                raise ValueError(
                    f"Expected one GeoTIFF in {remote_archive}, found {len(tif_members)}."
                )
            with archive.open(tif_members[0]) as source, source_tif.open("wb") as target:
                shutil.copyfileobj(source, target, length=8 * 1024 * 1024)

        rio_copy(
            source_tif.as_posix(),
            output_cog.as_posix(),
            driver="COG",
            compress="LZW",
            blocksize=512,
            overview_resampling="nearest",
            nodata=0,
            BIGTIFF="IF_SAFER",
            NUM_THREADS=2,
        )
        with rasterio.open(output_cog) as dataset:
            if not dataset.is_tiled or not dataset.overviews(1):
                raise ValueError(f"COG validation failed for {output_cog}")
            if dataset.nodata != 0:
                raise ValueError(f"Unexpected nodata value for {output_cog}: {dataset.nodata}")

        fs.put(output_cog.as_posix(), remote_cog)
        return {
            "year": year,
            "cog_uri": cog_uri,
            "size_bytes": output_cog.stat().st_size,
            "status": "published",
        }


def publish_burn_severity(
    *,
    years: set[int] | None = None,
    workers: int = 4,
    force: bool = False,
) -> None:
    """Publish requested annual COGs and a runtime-readable manifest."""
    import gcsfs

    fs = gcsfs.GCSFileSystem()
    archives: list[tuple[str, int]] = []
    for path in fs.find(SOURCE_PREFIX):
        match = _ARCHIVE_PATTERN.search(Path(path).name)
        if not match:
            continue
        year = int(match.group(1))
        if years is None or year in years:
            archives.append((path, year))
    archives.sort(key=lambda item: item[1])
    if not archives:
        raise FileNotFoundError("No matching annual MTBS burn-severity archives found.")

    print(
        f"Preparing {len(archives)} annual burn-severity COGs with {workers} workers...",
        flush=True,
    )
    assets: list[dict[str, object]] = []
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=get_context("spawn"),
    ) as executor:
        futures = {
            executor.submit(_convert_archive, path, year, force): year
            for path, year in archives
        }
        for future in as_completed(futures):
            asset = future.result()
            assets.append(asset)
            size_mb = int(asset["size_bytes"]) / 1024 / 1024
            print(
                f"{asset['year']}: {asset['status']} ({size_mb:,.1f} MB)",
                flush=True,
            )

    assets.sort(key=lambda asset: int(asset["year"]))
    manifest = {
        "dataset": "MTBS annual CONUS burn severity",
        "states_shown_by_ember": list(ACTIVE_STATES),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assets": [
            {
                "year": int(asset["year"]),
                "cog_uri": str(asset["cog_uri"]),
                "size_bytes": int(asset["size_bytes"]),
            }
            for asset in assets
        ],
    }
    with fs.open(MANIFEST_PATH, "wb") as destination:
        destination.write(json.dumps(manifest, indent=2).encode("utf-8"))
    print(f"Published gs://{MANIFEST_PATH}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert annual MTBS burn-severity ZIPs to COGs and publish them."
    )
    parser.add_argument(
        "--years",
        type=_parse_years,
        default=None,
        help="Years/ranges such as 1984-1990,2024. Default: every uploaded annual archive.",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="Replace existing annual COGs.")
    args = parser.parse_args()
    publish_burn_severity(years=args.years, workers=args.workers, force=args.force)
