"""Catalog query tests against a tiny fixture dataset."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import sleep

import pytest

from core.catalog import Catalog
from core.settings import settings
from core.storage import LocalStorage
from scripts.bootstrap_sample_data import bootstrap_sample_data


def _catalog_with_fixture(tmp_path: Path) -> Catalog:
    bootstrap_sample_data(tmp_path)
    storage = LocalStorage(root=tmp_path)
    return Catalog(storage)


def test_list_queries_return_fixture_rows(tmp_path: Path) -> None:
    """Selectors should list utility and wildfire rows from fixture data."""
    catalog = _catalog_with_fixture(tmp_path)
    utilities = catalog.list_utilities()
    wildfires = catalog.list_wildfires()
    assert len(utilities) == 2
    assert len(wildfires) == 2
    assert all(wildfire.acres is not None for wildfire in wildfires)
    foothills = next(utility for utility in utilities if utility.utility_id == "foothills-utility")
    denver = next(utility for utility in utilities if utility.utility_id == "denver-water")
    assert foothills.has_source_area is True
    assert foothills.has_service_area is True
    assert denver.has_service_area is True


def test_pair_summary_and_scalar_states(tmp_path: Path) -> None:
    """Fixture data should include overlap, no-overlap, and pending scalar rows."""
    catalog = _catalog_with_fixture(tmp_path)
    overlap_pair = catalog.get_pair_summary("denver-water", "hayman-2002")
    no_overlap_pair = catalog.get_pair_summary("denver-water", "camp-2018")
    pending_scalar = catalog.get_scalar("denver-water", "camp-2018", "total_econ_impact")
    utility_total = catalog.get_utility_scalar("denver-water", "total_econ_impact")
    pre_fire_revenue = catalog.get_utility_scalar(
        "denver-water", "pre_fire_annual_operating_revenue"
    )
    assert overlap_pair.has_overlap is True
    assert no_overlap_pair.has_overlap is False
    assert pending_scalar is None
    assert utility_total is not None
    assert utility_total.value == 68_000_000
    assert pre_fire_revenue is not None
    assert pre_fire_revenue.value == 120_000_000


def test_yearly_intersected_area_sums_source_and_service_overlaps(
    tmp_path: Path,
) -> None:
    catalog = _catalog_with_fixture(tmp_path)

    assert catalog.list_yearly_intersected_area() == [
        (2002, 580.2),
        (2018, 210.1),
    ]


def test_yearly_burned_area_converts_acres_to_square_kilometers(
    tmp_path: Path,
) -> None:
    catalog = _catalog_with_fixture(tmp_path)

    yearly_area = dict(catalog.list_yearly_burned_area())
    assert yearly_area[2002] == pytest.approx(137_760 * 0.0040468564224)
    assert yearly_area[2018] == pytest.approx(153_336 * 0.0040468564224)


def test_case_study_cost_rows_are_scoped_to_utility(tmp_path: Path) -> None:
    catalog = _catalog_with_fixture(tmp_path)

    case_studies = catalog.list_case_studies()
    rows = catalog.list_case_study_costs("denver-water")

    assert len(case_studies) == 1
    assert case_studies[0].utility_id == "denver-water"
    assert len(rows) == 1
    assert rows[0].inflation_adjusted_cost == 2_100_000
    assert catalog.list_case_study_costs("foothills-utility") == []


def test_raster_and_geojson_lookup(tmp_path: Path) -> None:
    """Fixture should expose raster URI and simplified GeoJSON feature."""
    catalog = _catalog_with_fixture(tmp_path)
    asset = catalog.get_raster_asset("denver-water", "hayman-2002", "sediment_yield_increase")
    geojson = catalog.get_geojson("utilities", "denver-water", simplify_tolerance=0.0005)
    assert asset is not None
    assert asset.cog_uri.endswith(".tif")
    assert Path(asset.cog_uri).is_absolute()
    assert geojson["type"] == "Feature"


def test_service_area_and_transitive_source_lookup(tmp_path: Path) -> None:
    """Service geometry is separate from direct and upstream source records."""
    catalog = _catalog_with_fixture(tmp_path)

    service_area = catalog.get_utility_geojson("foothills-utility", "service")
    denver_service_area = catalog.get_utility_geojson("denver-water", "service")
    sources = catalog.list_utility_sources("foothills-utility")

    assert service_area is not None
    assert service_area["properties"]["area_type"] == "service"
    assert denver_service_area is not None
    assert denver_service_area["geometry"]["type"] == "Point"
    assert denver_service_area["geometry"]["coordinates"] == [-104.9903, 39.7392]
    assert [(source.source_name, source.depth) for source in sources] == [
        ("Regional Water Authority", 1),
        ("Sierra Reservoir", 2),
    ]


def test_wildfire_service_area_and_source_location_lookup(tmp_path: Path) -> None:
    """Wildfire view queries should identify service polygons and covered points."""
    catalog = _catalog_with_fixture(tmp_path)

    service_areas = catalog.list_intersecting_service_areas("camp-2018")
    source_locations = catalog.list_intersecting_source_locations("camp-2018")

    assert [(area.utility_id, area.name) for area in service_areas] == [
        ("foothills-utility", "Foothills Utility")
    ]
    assert [
        (location.source_name, location.utility_name, location.depth)
        for location in source_locations
    ] == [("Regional Water Authority", "Foothills Utility", 1)]


def test_overview_geojson_includes_all_map_features(tmp_path: Path) -> None:
    """Overview layers should include every fixture geometry and tooltip property."""
    catalog = _catalog_with_fixture(tmp_path)
    utilities = catalog.get_overview_geojson("utilities")
    service_areas = catalog.get_overview_geojson("service_areas")
    wildfires = catalog.get_overview_geojson("wildfires")

    assert len(utilities["features"]) == 2
    assert len(service_areas["features"]) == 2
    assert len(wildfires["features"]) == 2
    assert any(
        feature["properties"]["utility_id"] == "denver-water"
        and feature["geometry"]["type"] == "Point"
        for feature in service_areas["features"]
    )
    assert {"name", "state", "source_area_name"} <= set(
        utilities["features"][0]["properties"]
    )
    assert {"name", "state", "acres"} <= set(wildfires["features"][0]["properties"])


def test_gcs_catalog_downloads_parquet_once_before_duckdb_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Remote tables should be cached locally without registering Python gcsfs."""
    source_root = tmp_path / "source"
    bootstrap_sample_data(source_root)
    storage = LocalStorage(root=source_root)
    downloads: list[str] = []
    original_download = storage.download_to_path

    def record_download(key: str, destination: Path) -> None:
        downloads.append(key)
        original_download(key, destination)

    monkeypatch.setattr(settings, "ember_storage_backend", "gcs")
    monkeypatch.setattr(storage, "download_to_path", record_download)
    catalog = Catalog(storage)

    catalog.list_utilities()
    catalog.list_utilities()

    assert downloads == ["tables/utilities.parquet"]


def test_gcs_catalog_serializes_concurrent_dataset_downloads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Concurrent Streamlit sessions must share one completed local cache file."""
    source_root = tmp_path / "source"
    source = source_root / "tables" / "utilities.parquet"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"parquet-data")
    storage = LocalStorage(root=source_root)
    download_count = 0
    original_download = storage.download_to_path

    def slow_download(key: str, destination: Path) -> None:
        nonlocal download_count
        download_count += 1
        sleep(0.05)
        original_download(key, destination)

    monkeypatch.setattr(settings, "ember_storage_backend", "gcs")
    monkeypatch.setattr(storage, "download_to_path", slow_download)
    catalog = Catalog(storage)

    with ThreadPoolExecutor(max_workers=2) as executor:
        paths = list(executor.map(lambda _: catalog._dataset("utilities"), range(2)))

    assert paths[0] == paths[1]
    assert Path(paths[0]).read_bytes() == b"parquet-data"
    assert download_count == 1
