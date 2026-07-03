"""Typed application settings loaded from environment variables."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized typed settings for app, catalog, storage, and tiler wiring."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ember_storage_backend: Literal["local", "gcs"] = Field(
        default="local", description="Storage backend used by storage abstraction."
    )
    ember_data_root: str = Field(default="./data", description="Local data root for backend=local.")

    gcs_bucket: str = Field(default="ember-data", description="GCS bucket holding tables and COGs.")
    gcs_prefix: str = Field(
        default="",
        description=(
            "Optional object-key prefix (folder) within the bucket under which `tables/` and "
            "`cogs/` live, e.g. `EMBER`. Leave blank to read from the bucket root."
        ),
    )
    gcs_project: str = Field(
        default="",
        description=(
            "Optional GCP project id for the storage client. Only needed for object "
            "read_bytes/exists; reading Parquet/COGs by URI does not require it. Leave blank "
            "when using a service-account JSON (project is inferred from the key)."
        ),
    )
    google_application_credentials: str = Field(
        default="",
        description=(
            "Service-account JSON path used by every GCS reader (DuckDB via gcsfs, "
            "GDAL/TiTiler, and the storage client). Leave blank on managed runtimes "
            "(e.g. Cloud Run) to use the attached service account via ADC."
        ),
    )

    tiler_url: str = Field(default="http://localhost:8000", description="Base URL for TiTiler service.")
    # Stored as a plain comma-separated string so pydantic-settings does not attempt to
    # JSON-decode the env value; use `cors_origin_list` for the parsed origins.
    cors_origins: str = Field(
        default="http://localhost:8501",
        description="Comma-separated allowed origins for tiler CORS middleware.",
    )
    geojson_simplify_tolerance: float = Field(
        default=0.0005, description="DuckDB ST_Simplify tolerance in degrees."
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """Return CORS origins parsed from the comma-separated `cors_origins` value."""
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @cached_property
    def data_root_path(self) -> Path:
        """Return local data root as an absolute path."""
        return Path(self.ember_data_root).expanduser().resolve()


settings = Settings()
