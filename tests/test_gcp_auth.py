"""Tests for local GDAL authentication bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.gcp_auth import _bootstrap_gdal_from_local_adc


def test_local_adc_is_forwarded_to_gdal(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "gcloud"
    config_dir.mkdir()
    (config_dir / "application_default_credentials.json").write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": "client",
                "client_secret": "secret",
                "refresh_token": "refresh",
            }
        )
    )
    monkeypatch.setenv("CLOUDSDK_CONFIG", config_dir.as_posix())
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    for key in (
        "GS_OAUTH2_CLIENT_ID",
        "GS_OAUTH2_CLIENT_SECRET",
        "GS_OAUTH2_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    _bootstrap_gdal_from_local_adc()

    assert os.environ["GS_OAUTH2_CLIENT_ID"] == "client"
    assert os.environ["GS_OAUTH2_CLIENT_SECRET"] == "secret"
    assert os.environ["GS_OAUTH2_REFRESH_TOKEN"] == "refresh"
