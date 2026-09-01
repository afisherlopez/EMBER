"""Tests for public-app service-account bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

from core.gcp_auth import bootstrap_gcp_credentials, local_service_account_path


def test_local_service_account_is_preferred_over_user_adc(
    monkeypatch, tmp_path: Path
) -> None:
    sa_path = tmp_path / "ember-sa.json"
    sa_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("core.gcp_auth._LOCAL_SERVICE_ACCOUNT", sa_path)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    assert local_service_account_path() == sa_path
    bootstrap_gcp_credentials()
    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(sa_path)


def test_explicit_credentials_env_wins(monkeypatch, tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", explicit.as_posix())

    assert local_service_account_path() == explicit
