"""End-to-end admin upload tests that drive the real Streamlit app on sample data."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from core import admin_data
from core.app.admin_view import describe_write_failure
from core.catalog import Catalog
from core.case_study_costs import parse_case_study_csv
from core.settings import settings
from core.storage import LocalStorage
from scripts.bootstrap_sample_data import bootstrap_sample_data

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "core" / "app" / "streamlit_app.py"
SAMPLE_CSV = PROJECT_ROOT / "EMBER Case-Study Datasheet - EWEB_W_Costs.csv"


@pytest.fixture
def sample_app(tmp_path: Path, monkeypatch) -> AppTest:
    bootstrap_sample_data(tmp_path)
    monkeypatch.setattr(settings, "ember_storage_backend", "local")
    monkeypatch.setattr(settings, "ember_data_root", str(tmp_path))
    monkeypatch.setitem(settings.__dict__, "data_root_path", tmp_path)
    monkeypatch.setattr(settings, "ember_admin_password", "test-password")
    st.cache_data.clear()
    st.cache_resource.clear()
    app = AppTest.from_file(str(APP_PATH), default_timeout=120)
    yield app
    st.cache_data.clear()
    st.cache_resource.clear()


def _upload(app: AppTest, data: bytes) -> None:
    app.get("file_uploader")[0].upload("case_study.csv", data, "text/csv")
    next(
        button for button in app.button if button.label.startswith("Replace case-study")
    ).click().run()


def test_dashboard_renders_on_sample_data(sample_app: AppTest) -> None:
    sample_app.run()

    assert not sample_app.exception
    # The logo is embedded in the page, so it cannot break when media requests fail.
    assert any(
        'alt="EMBER"' in element.value and "data:image/png;base64," in element.value
        for element in sample_app.markdown
    )
    sample_app.selectbox[0].set_value("Denver Water (CO)").run()
    assert not sample_app.exception
    # The headline comes from the uploaded rows, not from a separately stored metric.
    assert sample_app.metric[0].value == "$2,100,000"


def test_upload_for_new_utility_appears_without_restart(sample_app: AppTest) -> None:
    # Load the dashboard first so every cache holds the pre-upload data.
    sample_app.run()
    assert sample_app.selectbox[0].options == ["Denver Water (CO)"]

    sample_app.session_state["admin_mode"] = True
    sample_app.run()
    sample_app.radio(key="admin_case_study_location_mode").set_value(
        "Enter coordinates (utility not on file)"
    ).run()
    sample_app.text_input(key="admin_case_study_custom_name").set_value(
        "Eugene Water & Electric Board"
    )
    sample_app.selectbox(key="admin_case_study_custom_state").set_value("OR")
    sample_app.number_input(key="admin_case_study_latitude").set_value(44.0521)
    sample_app.number_input(key="admin_case_study_longitude").set_value(-123.0868)
    sample_app.run()
    _upload(sample_app, SAMPLE_CSV.read_bytes())

    assert not sample_app.exception
    assert not sample_app.error
    next(
        button for button in sample_app.button if button.label.startswith("Return to")
    ).click().run()

    assert not sample_app.exception
    assert sample_app.selectbox[0].options == [
        "Denver Water (CO)",
        "Eugene Water & Electric Board (OR)",
    ]
    sample_app.selectbox[0].set_value("Eugene Water & Electric Board (OR)").run()
    assert not sample_app.exception
    # Sum of the six inflation-adjusted Cost rows in the sample spreadsheet.
    assert sample_app.metric[0].value == "$22,144,000"


def test_failed_storage_write_shows_an_error_instead_of_crashing(
    sample_app: AppTest, monkeypatch
) -> None:
    class Forbidden(Exception):
        """Stand-in for google.api_core.exceptions.Forbidden."""

    def deny(*_args, **_kwargs):
        raise Forbidden("403 ember-reader does not have storage.objects.create access")

    monkeypatch.setattr(admin_data, "_backup_and_publish", deny)
    sample_app.session_state["admin_mode"] = True
    sample_app.run()
    _upload(sample_app, SAMPLE_CSV.read_bytes())

    assert not sample_app.exception
    assert len(sample_app.error) == 1
    assert "not allowed to write" in sample_app.error[0].value


def test_invalid_csv_shows_an_error_instead_of_crashing(sample_app: AppTest) -> None:
    sample_app.session_state["admin_mode"] = True
    sample_app.run()
    _upload(sample_app, b"Item Type,Years Incurred,Raw Value\nCost,2020-2022,100\n")

    assert not sample_app.exception
    assert "Years Incurred" in sample_app.error[0].value


def test_expired_login_is_described_as_a_sign_in_problem() -> None:
    class RefreshError(Exception):
        """Stand-in for google.auth.exceptions.RefreshError."""

    message = describe_write_failure(RefreshError("invalid_grant: Invalid JWT Signature."))

    assert "could not sign in" in message


def test_catalog_refresh_reloads_a_republished_table(tmp_path: Path, monkeypatch) -> None:
    bootstrap_sample_data(tmp_path)
    monkeypatch.setattr(settings, "ember_storage_backend", "local")
    monkeypatch.setattr(admin_data, "_local_path", lambda key: tmp_path / key)
    catalog = Catalog(LocalStorage(tmp_path))
    assert len(catalog.list_case_study_costs("denver-water")) == 1

    admin_data.replace_case_study_costs(
        utility_id="denver-water",
        rows=parse_case_study_csv(SAMPLE_CSV.read_bytes()),
    )
    assert len(catalog.list_case_study_costs("denver-water")) == 1
    catalog.refresh("case_study_costs")

    assert len(catalog.list_case_study_costs("denver-water")) == 7


class _FakeBlob:
    def __init__(self, root: Path, name: str) -> None:
        self.name = name
        self._path = root / name

    def exists(self) -> bool:
        return self._path.exists()

    def download_to_filename(self, filename) -> None:
        Path(filename).write_bytes(self._path.read_bytes())

    def upload_from_filename(self, filename) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(Path(filename).read_bytes())


class _FakeBucket:
    def __init__(self, root: Path) -> None:
        self._root = root

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self._root, name)

    def copy_blob(self, blob: _FakeBlob, _bucket, new_name: str) -> None:
        _FakeBlob(self._root, new_name).upload_from_filename(blob._path)


class _FakeGCSClient:
    """Directory-backed stand-in for google.cloud.storage.Client."""

    root = Path()

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def bucket(self, _name: str) -> _FakeBucket:
        return _FakeBucket(self.root)


def test_gcs_upload_is_visible_after_refresh(tmp_path: Path, monkeypatch) -> None:
    """Exercise the production (GCS) write and reload path without network access."""
    from core import storage as storage_module

    bucket_root = tmp_path / "bucket"
    bootstrap_sample_data(bucket_root / "EMBER")
    monkeypatch.setattr(_FakeGCSClient, "root", bucket_root)
    monkeypatch.setattr(admin_data.gcs_storage, "Client", _FakeGCSClient)
    monkeypatch.setattr(storage_module.gcs_storage, "Client", _FakeGCSClient)
    monkeypatch.setattr(settings, "ember_storage_backend", "gcs")
    monkeypatch.setattr(settings, "gcs_prefix", "EMBER")
    catalog = Catalog(storage_module.GCSStorage(bucket="test-bucket", prefix="EMBER"))
    assert [study.utility_id for study in catalog.list_case_studies()] == ["denver-water"]

    utility_id, _ = admin_data.upsert_case_study_point_utility(
        name="Eugene Water & Electric Board",
        state="OR",
        latitude=44.0521,
        longitude=-123.0868,
    )
    result = admin_data.replace_case_study_costs(
        utility_id=utility_id,
        rows=parse_case_study_csv(SAMPLE_CSV.read_bytes()),
    )
    catalog.refresh("utilities", "case_study_costs")

    assert result.backup_uri is not None
    assert list((bucket_root / "EMBER" / "tables" / "backups").glob("case_study_costs.*"))
    assert [study.utility_id for study in catalog.list_case_studies()] == [
        "denver-water",
        utility_id,
    ]
    assert len(catalog.list_case_study_costs(utility_id)) == 7


def test_coordinates_for_a_mapped_utility_are_refused_with_a_message(
    sample_app: AppTest,
) -> None:
    sample_app.session_state["admin_mode"] = True
    sample_app.run()
    sample_app.radio(key="admin_case_study_location_mode").set_value(
        "Enter coordinates (utility not on file)"
    ).run()
    sample_app.text_input(key="admin_case_study_custom_name").set_value("Denver Water")
    sample_app.selectbox(key="admin_case_study_custom_state").set_value("CO")
    sample_app.number_input(key="admin_case_study_latitude").set_value(39.7)
    sample_app.number_input(key="admin_case_study_longitude").set_value(-105.0)
    sample_app.run()
    _upload(sample_app, SAMPLE_CSV.read_bytes())

    assert not sample_app.exception
    assert sample_app.error[0].value.startswith("Utility `denver-water` is already")
