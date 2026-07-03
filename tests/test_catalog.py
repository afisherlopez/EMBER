"""Catalog query tests against a tiny fixture dataset."""

from pathlib import Path

from core.catalog import Catalog
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


def test_pair_summary_and_scalar_states(tmp_path: Path) -> None:
    """Fixture data should include overlap, no-overlap, and pending scalar rows."""
    catalog = _catalog_with_fixture(tmp_path)
    overlap_pair = catalog.get_pair_summary("denver-water", "hayman-2002")
    no_overlap_pair = catalog.get_pair_summary("denver-water", "camp-2018")
    pending_scalar = catalog.get_scalar("denver-water", "camp-2018", "total_econ_impact")
    assert overlap_pair.has_overlap is True
    assert no_overlap_pair.has_overlap is False
    assert pending_scalar is None


def test_raster_and_geojson_lookup(tmp_path: Path) -> None:
    """Fixture should expose raster URI and simplified GeoJSON feature."""
    catalog = _catalog_with_fixture(tmp_path)
    asset = catalog.get_raster_asset("denver-water", "hayman-2002", "sediment_yield_increase")
    geojson = catalog.get_geojson("utilities", "denver-water", simplify_tolerance=0.0005)
    assert asset is not None
    assert asset.cog_uri.endswith(".tif")
    assert Path(asset.cog_uri).is_absolute()
    assert geojson["type"] == "Feature"
