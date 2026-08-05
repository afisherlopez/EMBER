"""Catalog query tests against a tiny fixture dataset."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import sleep

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


def test_pair_summary_and_scalar_states(tmp_path: Path) -> None:
    """Fixture data should include overlap, no-overlap, and pending scalar rows."""
    catalog = _catalog_with_fixture(tmp_path)
    overlap_pair = catalog.get_pair_summary("denver-water", "hayman-2002")
    no_overlap_pair = catalog.get_pair_summary("denver-water", "camp-2018")
    pending_scalar = catalog.get_scalar("denver-water", "camp-2018", "total_econ_impact")
    assert overlap_pair.has_overlap is True
    assert no_overlap_pair.has_overlap is False
    assert pending_scalar is None


def test_case_study_cost_rows_are_scoped_to_selected_pair(tmp_path: Path) -> None:
    catalog = _catalog_with_fixture(tmp_path)

    case_studies = catalog.list_case_studies()
    rows = catalog.list_case_study_costs("denver-water", "hayman-2002")

    assert len(case_studies) == 1
    assert case_studies[0].utility_id == "denver-water"
    assert case_studies[0].wildfire_id == "hayman-2002"
    assert len(rows) == 1
    assert rows[0].inflation_adjusted_cost == 2_100_000
    assert catalog.list_case_study_costs("denver-water", "camp-2018") == []


def test_raster_and_geojson_lookup(tmp_path: Path) -> None:
    """Fixture should expose raster URI and simplified GeoJSON feature."""
    catalog = _catalog_with_fixture(tmp_path)
    asset = catalog.get_raster_asset("denver-water", "hayman-2002", "sediment_yield_increase")
    geojson = catalog.get_geojson("utilities", "denver-water", simplify_tolerance=0.0005)
    assert asset is not None
    assert asset.cog_uri.endswith(".tif")
    assert Path(asset.cog_uri).is_absolute()
    assert geojson["type"] == "Feature"


def test_overview_geojson_includes_all_map_features(tmp_path: Path) -> None:
    """Overview layers should include every fixture geometry and tooltip property."""
    catalog = _catalog_with_fixture(tmp_path)
    utilities = catalog.get_overview_geojson("utilities")
    wildfires = catalog.get_overview_geojson("wildfires")

    assert len(utilities["features"]) == 2
    assert len(wildfires["features"]) == 2
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
