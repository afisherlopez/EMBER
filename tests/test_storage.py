"""Storage URI resolution tests for the GCS backend prefix behavior."""

from core.storage import GCSStorage


def _gcs(prefix: str) -> GCSStorage:
    # The GCS client is lazy and unused by URI resolution, so no credentials are needed here.
    return GCSStorage(bucket="data_main_gcs", prefix=prefix)


def test_prefix_is_inserted_between_bucket_and_key() -> None:
    """A configured prefix should nest keys under that folder."""
    storage = _gcs("EMBER")
    assert storage.uri_for("cogs/x.tif") == "gs://data_main_gcs/EMBER/cogs/x.tif"
    assert storage.dataset_uri("utilities") == "gs://data_main_gcs/EMBER/tables/utilities.parquet"


def test_blank_prefix_reads_from_bucket_root() -> None:
    """An empty prefix should resolve keys directly under the bucket."""
    storage = _gcs("")
    assert storage.dataset_uri("wildfires") == "gs://data_main_gcs/tables/wildfires.parquet"


def test_prefix_slashes_are_normalized() -> None:
    """Leading/trailing slashes on the prefix should not create empty path segments."""
    storage = _gcs("/EMBER/")
    assert storage.dataset_uri("utilities") == "gs://data_main_gcs/EMBER/tables/utilities.parquet"
