"""Storage URI resolution tests for the GCS backend prefix behavior."""

from pathlib import Path

from core.storage import (
    GCSStorage,
    LocalStorage,
    is_gcs_auth_error,
    resolve_app_storage,
)


def _gcs(prefix: str) -> GCSStorage:
    # The GCS client is lazy and unused by URI resolution, so no credentials are needed here.
    return GCSStorage(bucket="data_main_gcs", prefix=prefix)


def test_prefix_is_inserted_between_bucket_and_key() -> None:
    """A configured prefix should nest keys under that folder."""
    storage = _gcs("EMBER")
    assert storage.uri_for("cogs/x.tif") == "gs://data_main_gcs/EMBER/cogs/x.tif"
    assert storage.dataset_uri("utilities") == "gs://data_main_gcs/EMBER/tables/utilities.parquet"


def test_public_url_encodes_case_study_pdf_name() -> None:
    storage = _gcs("EMBER")
    assert storage.public_url_for("case_studies/EWEB Report 2020.pdf") == (
        "https://storage.googleapis.com/data_main_gcs/EMBER/"
        "case_studies/EWEB%20Report%202020.pdf"
    )


def test_blank_prefix_reads_from_bucket_root() -> None:
    """An empty prefix should resolve keys directly under the bucket."""
    storage = _gcs("")
    assert storage.dataset_uri("wildfires") == "gs://data_main_gcs/tables/wildfires.parquet"


def test_prefix_slashes_are_normalized() -> None:
    """Leading/trailing slashes on the prefix should not create empty path segments."""
    storage = _gcs("/EMBER/")
    assert storage.dataset_uri("utilities") == "gs://data_main_gcs/EMBER/tables/utilities.parquet"


def test_local_asset_listing_filters_extensions(tmp_path: Path) -> None:
    """Local asset discovery should recurse and keep only requested suffixes."""
    severity_dir = tmp_path / "fire_burn_severity" / "nested"
    severity_dir.mkdir(parents=True)
    (severity_dir / "severity.tif").write_bytes(b"")
    (severity_dir / "notes.txt").write_text("ignore", encoding="utf-8")

    storage = LocalStorage(root=tmp_path)
    assert storage.list_uris("fire_burn_severity", (".tif", ".tiff")) == [
        (severity_dir / "severity.tif").resolve().as_posix()
    ]


def test_local_download_to_path_streams_to_destination(tmp_path: Path) -> None:
    source = tmp_path / "source" / "tables" / "utilities.parquet"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"parquet-data")
    destination = tmp_path / "cache" / "utilities.parquet"

    LocalStorage(root=tmp_path / "source").download_to_path(
        "tables/utilities.parquet",
        destination,
    )

    assert destination.read_bytes() == b"parquet-data"


def test_is_gcs_auth_error_detects_invalid_grant() -> None:
    """Expired user ADC should be treated as a recoverable local-dev failure."""
    exc = RuntimeError("('invalid_grant: Bad Request', {'error': 'invalid_grant'})")
    assert is_gcs_auth_error(exc)
    assert not is_gcs_auth_error(FileNotFoundError("tables/utilities.parquet"))


def test_resolve_app_storage_falls_back_to_public_gcs(monkeypatch) -> None:
    """A dead personal login should still use the public GCS catalog."""
    from core.settings import settings

    monkeypatch.setattr(settings, "ember_storage_backend", "gcs")
    monkeypatch.setattr(settings, "gcs_bucket", "data_main_gcs")
    monkeypatch.setattr(settings, "gcs_prefix", "EMBER")
    monkeypatch.setattr(settings, "gcs_project", "")

    def _boom(self, key: str) -> bool:
        raise RuntimeError("invalid_grant: Bad Request")

    monkeypatch.setattr(GCSStorage, "exists", _boom)
    storage = resolve_app_storage()
    assert isinstance(storage, GCSStorage)
    assert storage.anonymous is True
    assert storage.bucket == "data_main_gcs"
