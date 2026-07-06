"""Bootstrap GCP credentials and config from Streamlit secrets for cloud deployment.

Local dev keeps working exactly as before: with no Streamlit secrets configured, this is a
no-op and the app authenticates through Application Default Credentials (``gcloud auth
application-default login``) or an explicit ``GOOGLE_APPLICATION_CREDENTIALS`` file.

On Streamlit Community Cloud there is no ADC and no ``.env``. There, an operator pastes a
read-only service-account key (and optional config) into the app's **Secrets**. This module
materializes that key to a temp file and points every GCS reader at it through
``GOOGLE_APPLICATION_CREDENTIALS`` — the single credential DuckDB (via gcsfs),
google-cloud-storage, and GDAL/TiTiler all understand.

``bootstrap_gcp_credentials()`` must run **before** ``core.settings`` is imported so pydantic
picks up any env vars injected here.
"""

from __future__ import annotations

import json
import os
import tempfile
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
)
# TOML table holding the service-account JSON fields (see DEPLOY.md).
_SERVICE_ACCOUNT_KEY = "gcp_service_account"


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


def bootstrap_gcp_credentials() -> None:
    """Inject GCP credentials/config from st.secrets into the environment (cloud only).

    No-op locally (no secrets). On Streamlit Cloud it forwards config keys and writes the
    service-account key to a temp file referenced by ``GOOGLE_APPLICATION_CREDENTIALS``.
    An already-valid ``GOOGLE_APPLICATION_CREDENTIALS`` file is left untouched.
    """
    secrets = _load_secrets()
    if secrets is None:
        return

    for key in _CONFIG_KEYS:
        try:
            if key in secrets and not os.environ.get(key):
                os.environ[key] = str(secrets[key])
        except Exception:  # noqa: BLE001 - never let optional config break startup
            continue

    existing = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if existing and os.path.exists(existing):
        return

    try:
        if _SERVICE_ACCOUNT_KEY not in secrets:
            return
        info = dict(secrets[_SERVICE_ACCOUNT_KEY])
    except Exception:  # noqa: BLE001 - malformed/missing key => fall back to ADC
        return
    if not info:
        return

    fd, path = tempfile.mkstemp(prefix="ember_gcp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(info, handle)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
