"""Tests for annual burn-severity asset discovery."""

from __future__ import annotations

import json
from pathlib import Path

from core.burn_severity import (
    BURN_SEVERITY_MANIFEST,
    load_burn_severity_assets,
    newest_first_asset_uris,
)
from core.storage import LocalStorage


def test_manifest_assets_are_keyed_by_year(tmp_path: Path) -> None:
    manifest = tmp_path / BURN_SEVERITY_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {"year": 2023, "cog_uri": "gs://bucket/mtbs_CONUS_2023.tif"},
                    {"year": 2024, "cog_uri": "gs://bucket/mtbs_CONUS_2024.tif"},
                ]
            }
        )
    )

    assets = load_burn_severity_assets(LocalStorage(tmp_path))

    assert assets == {
        2023: "gs://bucket/mtbs_CONUS_2023.tif",
        2024: "gs://bucket/mtbs_CONUS_2024.tif",
    }


def test_local_discovery_ignores_non_raster_files(tmp_path: Path) -> None:
    cog_dir = tmp_path / "fire_burn_severity" / "cogs"
    cog_dir.mkdir(parents=True)
    raster = cog_dir / "mtbs_CONUS_2024.tif"
    raster.write_bytes(b"")
    (cog_dir / "notes.txt").write_text("ignore")

    assets = load_burn_severity_assets(LocalStorage(tmp_path))

    assert assets == {2024: raster.resolve().as_posix()}


def test_selected_assets_are_ordered_newest_first() -> None:
    assets = {
        2020: "gs://bucket/2020.tif",
        2022: "gs://bucket/2022.tif",
        2024: "gs://bucket/2024.tif",
    }

    assert newest_first_asset_uris(assets, {2020, 2024, 2022}) == [
        "gs://bucket/2024.tif",
        "gs://bucket/2022.tif",
        "gs://bucket/2020.tif",
    ]
