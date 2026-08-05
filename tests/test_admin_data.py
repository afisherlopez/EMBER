"""Admin publication tests for pair-scoped case-study CSV uploads."""

from pathlib import Path

from core import admin_data
from core.admin_data import replace_case_study_costs
from core.case_study_costs import parse_case_study_csv
from core.catalog import Catalog
from core.settings import settings
from core.storage import LocalStorage
from scripts.bootstrap_sample_data import bootstrap_sample_data


def test_case_study_upload_replaces_only_the_selected_pair(
    tmp_path: Path, monkeypatch
) -> None:
    bootstrap_sample_data(tmp_path)
    csv_path = (
        Path(__file__).resolve().parents[1]
        / "EMBER Case-Study Datasheet - EWEB_W_Costs.csv"
    )
    uploaded_rows = parse_case_study_csv(csv_path.read_bytes())[:2]
    monkeypatch.setattr(settings, "ember_storage_backend", "local")
    monkeypatch.setattr(admin_data, "_local_path", lambda key: tmp_path / key)

    result = replace_case_study_costs(
        utility_id="foothills-utility",
        wildfire_id="camp-2018",
        rows=uploaded_rows,
    )

    catalog = Catalog(LocalStorage(tmp_path))
    original_rows = catalog.list_case_study_costs("denver-water", "hayman-2002")
    replaced_rows = catalog.list_case_study_costs("foothills-utility", "camp-2018")
    assert len(original_rows) == 1
    assert [row.start_year for row in replaced_rows] == [2020, 2021]
    assert result.backup_uri is not None
    assert Path(result.backup_uri).exists()
