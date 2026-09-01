"""Bootstrap the public-app service account before any GCS client is created.

The dashboard is meant to be used without a personal ``gcloud`` login. Readers
authenticate as the EMBER service account, the same identity Streamlit Community
Cloud uses. User Application Default Credentials are not consulted.

``bootstrap_gcp_credentials()`` must run **before** ``core.settings`` is imported so
pydantic and google-cloud-storage see ``GOOGLE_APPLICATION_CREDENTIALS``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Plain config values that may be supplied via st.secrets and forwarded to the environment,
# because Streamlit Cloud has no .env for pydantic Settings to read.
_CONFIG_KEYS = (
    "EMBER_STORAGE_BACKEND",
    "GCS_BUCKET",
    "GCS_PREFIX",
    "GCS_PROJECT",
    "TILER_URL",
    "CORS_ORIGINS",
    "GEOJSON_SIMPLIFY_TOLERANCE",
    "EMBER_WILDFIRE_STATES",
    "EMBER_ADMIN_PASSWORD",
)
# TOML table holding the service-account JSON fields (see docs/deployment.md).
_SERVICE_ACCOUNT_KEY = "gcp_service_account"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SERVICE_ACCOUNT = _PROJECT_ROOT / "secrets" / "ember-sa.json"


def local_service_account_path() -> Path | None:
    """Return the service-account JSON used by the public app, when present."""
    explicit = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit and os.path.isfile(explicit):
        return Path(explicit)
    if _LOCAL_SERVICE_ACCOUNT.is_file():
        return _LOCAL_SERVICE_ACCOUNT
    return None


def _load_secrets() -> Any | None:
    """Return the Streamlit secrets mapping, or None when unavailable.

    Accessing ``st.secrets`` with no secrets file raises; running outside a Streamlit
    runtime (tests, ingest scripts) has no secrets either. Both are treated as "no secrets"
    so this module is safe to import and call from anywhere.
    """
    try:
        import streamlit as st
    except Exception:  # noqa: BLE001 - streamlit may be absent in non-app contexts
        return None
    try:
        # Probe for a secrets.toml without touching st.secrets directly: accessing
        # st.secrets when no file exists renders a "No secrets found" st.error element,
        # which would run before set_page_config and break the app. load_if_toml_exists()
        # returns False (rendering nothing) locally, and True on Streamlit Cloud where the
        # pasted secrets are materialized to a secrets.toml.
        if not st.secrets.load_if_toml_exists():
            return None
        return st.secrets
    except Exception:  # noqa: BLE001 - no secrets configured is the normal local case
        return None


def _materialize_service_account(info: dict[str, Any]) -> None:
    fd, path = tempfile.mkstemp(prefix="ember_gcp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(info, handle)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path


def bootstrap_gcp_credentials() -> None:
    """Point every GCS reader at the public-app service account.

    Order: an already-valid ``GOOGLE_APPLICATION_CREDENTIALS`` file, Streamlit
    secrets, then ``secrets/ember-sa.json``. User ADC is never used.
    """
    existing = local_service_account_path()
    if existing is not None:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(existing)
        return

    secrets = _load_secrets()
    if secrets is None:
        return

    for key in _CONFIG_KEYS:
        try:
            if key in secrets and not os.environ.get(key):
                os.environ[key] = str(secrets[key])
        except Exception:  # noqa: BLE001 - never let optional config break startup
            continue

    existing = local_service_account_path()
    if existing is not None:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(existing)
        return

    try:
        if _SERVICE_ACCOUNT_KEY not in secrets:
            return
        info = dict(secrets[_SERVICE_ACCOUNT_KEY])
    except Exception:  # noqa: BLE001 - malformed/missing key is handled below
        return
    if not info:
        return
    _materialize_service_account(info)
