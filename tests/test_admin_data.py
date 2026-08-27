"""Admin publication tests for utility-scoped case-study CSV uploads."""

from datetime import date
from pathlib import Path

import duckdb

from core import admin_data
from core.admin_data import (
    replace_case_study_costs,
    upsert_case_study_point_utility,
    upsert_utility_scalar_metric,
    utility_id_from_name,
)
from core.case_study_costs import parse_case_study_csv
from core.catalog import Catalog
from core.settings import settings
from core.storage import LocalStorage
from scripts.bootstrap_sample_data import bootstrap_sample_data


def test_case_study_upload_overwrites_existing_utility(
    tmp_path: Path, monkeypatch
) -> None:
    bootstrap_sample_data(tmp_path)
    source_table = tmp_path / "tables" / "case_study_costs.parquet"
    legacy_table = tmp_path / "tables" / "case_study_costs.legacy.parquet"
    conn = duckdb.connect()
    conn.execute(
        f"""
        COPY (
            SELECT
                utility_id,
                'hayman-2002' AS wildfire_id,
                * EXCLUDE (utility_id)
            FROM read_parquet('{source_table.as_posix()}')
        ) TO '{legacy_table.as_posix()}' (FORMAT PARQUET)
        """
    )
    legacy_table.replace(source_table)
    csv_path = (
        Path(__file__).resolve().parents[1]
        / "EMBER Case-Study Datasheet - EWEB_W_Costs.csv"
    )
    uploaded_rows = parse_case_study_csv(csv_path.read_bytes())[:2]
    monkeypatch.setattr(settings, "ember_storage_backend", "local")
    monkeypatch.setattr(admin_data, "_local_path", lambda key: tmp_path / key)

    result = replace_case_study_costs(
        utility_id="denver-water",
        rows=uploaded_rows,
    )

    catalog = Catalog(LocalStorage(tmp_path))
    replaced_rows = catalog.list_case_study_costs("denver-water")
    assert len(replaced_rows) == 2
    assert [row.start_year for row in replaced_rows] == [2020, 2021]
    assert all(row.description != "Sample watershed recovery cost" for row in replaced_rows)
    published_columns = {
        row[0]
        for row in conn.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)",
            [source_table.as_posix()],
        ).fetchall()
    }
    assert "wildfire_id" not in published_columns
    assert result.backup_uri is not None
    assert Path(result.backup_uri).exists()


def test_admin_can_update_pre_fire_operating_revenue(
    tmp_path: Path, monkeypatch
) -> None:
    bootstrap_sample_data(tmp_path)
    monkeypatch.setattr(settings, "ember_storage_backend", "local")
    monkeypatch.setattr(admin_data, "_local_path", lambda key: tmp_path / key)

    upsert_utility_scalar_metric(
        utility_id="denver-water",
        metric_key="pre_fire_annual_operating_revenue",
        value=150_000_000,
        unit="USD",
        method="annual report",
        source_note=None,
        as_of_date=date(2025, 1, 1),
    )

    metric = Catalog(LocalStorage(tmp_path)).get_utility_scalar(
        "denver-water",
        "pre_fire_annual_operating_revenue",
    )
    assert metric is not None
    assert metric.value == 150_000_000


def test_admin_can_map_a_case_study_utility_by_coordinates(
    tmp_path: Path, monkeypatch
) -> None:
    bootstrap_sample_data(tmp_path)
    csv_path = (
        Path(__file__).resolve().parents[1]
        / "EMBER Case-Study Datasheet - EWEB_W_Costs.csv"
    )
    uploaded_rows = parse_case_study_csv(csv_path.read_bytes())[:1]
    monkeypatch.setattr(settings, "ember_storage_backend", "local")
    monkeypatch.setattr(admin_data, "_local_path", lambda key: tmp_path / key)

    utility_id, _ = upsert_case_study_point_utility(
        name="Eugene Water & Electric Board",
        state="OR",
        latitude=44.0521,
        longitude=-123.0868,
    )
    replace_case_study_costs(utility_id=utility_id, rows=uploaded_rows)

    catalog = Catalog(LocalStorage(tmp_path))
    case_studies = catalog.list_case_studies()
    point = catalog.get_utility_geojson(utility_id, "service")
    mapped = next(utility for utility in catalog.list_utilities() if utility.utility_id == utility_id)

    assert utility_id == "eugene-water-electric-board"
    assert any(case_study.utility_id == utility_id for case_study in case_studies)
    assert mapped.centroid_lat == 44.0521
    assert mapped.centroid_lon == -123.0868
    assert mapped.has_source_area is False
    assert mapped.has_service_area is True
    assert point is not None
    assert point["geometry"]["type"] == "Point"
    assert point["geometry"]["coordinates"] == [-123.0868, 44.0521]


def test_coordinate_mapping_refuses_to_overwrite_catalog_geometry(
    tmp_path: Path, monkeypatch
) -> None:
    bootstrap_sample_data(tmp_path)
    monkeypatch.setattr(settings, "ember_storage_backend", "local")
    monkeypatch.setattr(admin_data, "_local_path", lambda key: tmp_path / key)

    try:
        upsert_case_study_point_utility(
            name="Denver Water",
            state="CO",
            latitude=39.7,
            longitude=-105.0,
        )
    except ValueError as exc:
        assert "already in the catalog" in str(exc)
    else:
        raise AssertionError("Expected mapped catalog utilities to be rejected")


def test_utility_id_from_name_slugifies_names() -> None:
    assert utility_id_from_name("Eugene Water & Electric Board") == (
        "eugene-water-electric-board"
    )
