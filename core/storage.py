"""Storage abstraction for local filesystem and Google Cloud Storage backends."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from google.cloud import storage as gcs_storage

from core.settings import settings


class Storage(Protocol):
    """Protocol for resolving and reading EMBER data assets."""

    def uri_for(self, key: str) -> str:
        """Return URI for a relative object key."""

    def public_url_for(self, key: str) -> str:
        """Return a browser-readable URL for a relative object key."""

    def read_bytes(self, key: str) -> bytes:
        """Read object bytes from storage for a key."""

    def download_to_path(self, key: str, destination: Path) -> None:
        """Stream an object to a local path."""

    def exists(self, key: str) -> bool:
        """Return whether object key exists in storage."""

    def dataset_uri(self, name: str) -> str:
        """Return URI for a named dataset path under tables/."""

    def list_uris(self, prefix: str, suffixes: tuple[str, ...] = ()) -> list[str]:
        """List asset URIs below a relative storage prefix."""


@dataclass
class LocalStorage:
    """Local storage backend used by development and tests."""

    root: Path

    def _path_for(self, key: str) -> Path:
        return (self.root / key).resolve()

    def uri_for(self, key: str) -> str:
        """Return a plain absolute filesystem path for a local key.

        A plain path (not a ``file://`` URI) is used because both consumers must read it:
        DuckDB cannot resolve percent-encoded ``file://`` URIs (e.g. when the path contains
        spaces), and GDAL/rio-tiler does not understand the ``file://`` scheme for local COGs.
        Both, however, accept a plain absolute path.
        """
        return self._path_for(key).as_posix()

    def public_url_for(self, key: str) -> str:
        """Return a file URL for a local asset."""
        return self._path_for(key).as_uri()

    def read_bytes(self, key: str) -> bytes:
        """Read bytes from local file key."""
        return self._path_for(key).read_bytes()

    def download_to_path(self, key: str, destination: Path) -> None:
        """Copy a local object to the requested path."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path_for(key), destination)

    def exists(self, key: str) -> bool:
        """Check whether local file exists."""
        return self._path_for(key).exists()

    def dataset_uri(self, name: str) -> str:
        """Return dataset URI under local `tables/` prefix."""
        return self.uri_for(f"tables/{name}.parquet")

    def list_uris(self, prefix: str, suffixes: tuple[str, ...] = ()) -> list[str]:
        """List local asset paths below a relative prefix."""
        root = self._path_for(prefix)
        if not root.exists():
            return []
        normalized_suffixes = tuple(suffix.lower() for suffix in suffixes)
        return sorted(
            path.resolve().as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and (
                not normalized_suffixes
                or path.suffix.lower() in normalized_suffixes
            )
        )


@dataclass
class GCSStorage:
    """GCS storage backend returning `gs://` URIs and object access.

    ``prefix`` is an optional folder within the bucket (e.g. ``EMBER``) under which the
    ``tables/`` and ``cogs/`` layout lives, so the same relative keys resolve to
    ``gs://{bucket}/{prefix}/{key}``. Leave it empty to read from the bucket root.

    The underlying ``storage.Client`` is created lazily because the app's hot path only
    needs ``uri_for``/``dataset_uri`` (pure strings consumed by DuckDB and GDAL). Building
    the client eagerly would fail under user Application Default Credentials that carry no
    project, even though no object API call is ever made. ``project`` is only needed if
    ``read_bytes``/``exists`` are used.
    """

    bucket: str
    prefix: str = ""
    project: str = ""
    anonymous: bool = False
    _client: gcs_storage.Client | None = field(default=None, init=False, repr=False)

    @property
    def client(self) -> gcs_storage.Client:
        """Lazily build and cache the GCS client (only needed for object reads)."""
        if self._client is None:
            if self.anonymous:
                self._client = gcs_storage.Client.create_anonymous_client()
            else:
                self._client = gcs_storage.Client(project=self.project or None)
        return self._client

    def _object_key(self, key: str) -> str:
        cleaned_prefix = self.prefix.strip("/")
        return f"{cleaned_prefix}/{key}" if cleaned_prefix else key

    def uri_for(self, key: str) -> str:
        """Return canonical `gs://` URI for object key, including any bucket prefix."""
        return f"gs://{self.bucket}/{self._object_key(key)}"

    def public_url_for(self, key: str) -> str:
        """Return the public HTTPS URL for a GCS object."""
        object_key = quote(self._object_key(key), safe="/")
        return f"https://storage.googleapis.com/{quote(self.bucket, safe='')}/{object_key}"

    def read_bytes(self, key: str) -> bytes:
        """Read bytes from GCS object key."""
        blob = self.client.bucket(self.bucket).blob(self._object_key(key))
        return blob.download_as_bytes()

    def download_to_path(self, key: str, destination: Path) -> None:
        """Stream a GCS object directly to disk without buffering it in memory."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob = self.client.bucket(self.bucket).blob(self._object_key(key))
        blob.download_to_filename(destination)

    def exists(self, key: str) -> bool:
        """Check whether a GCS object exists."""
        blob = self.client.bucket(self.bucket).blob(self._object_key(key))
        return blob.exists()

    def dataset_uri(self, name: str) -> str:
        """Return dataset URI under `tables/` prefix."""
        return self.uri_for(f"tables/{name}.parquet")

    def list_uris(self, prefix: str, suffixes: tuple[str, ...] = ()) -> list[str]:
        """List GCS asset URIs below a relative prefix."""
        object_prefix = self._object_key(prefix).rstrip("/") + "/"
        normalized_suffixes = tuple(suffix.lower() for suffix in suffixes)
        return sorted(
            f"gs://{self.bucket}/{blob.name}"
            for blob in self.client.list_blobs(self.bucket, prefix=object_prefix)
            if not blob.name.endswith("/")
            and (
                not normalized_suffixes
                or Path(blob.name).suffix.lower() in normalized_suffixes
            )
        )


def is_gcs_auth_error(exc: BaseException) -> bool:
    """Return whether an exception is a stale or missing GCS login."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in {
            "RefreshError",
            "DefaultCredentialsError",
            "Unauthenticated",
        }:
            return True
        message = str(current).casefold()
        if "invalid_grant" in message or "reauthentication is needed" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def get_storage() -> Storage:
    """Build storage backend implementation from settings."""
    if settings.ember_storage_backend == "gcs":
        return GCSStorage(
            bucket=settings.gcs_bucket,
            prefix=settings.gcs_prefix,
            project=settings.gcs_project,
        )
    return LocalStorage(root=settings.data_root_path)


def resolve_app_storage() -> Storage:
    """Return the public GCS catalog, using anonymous reads if the SA login fails.

    The dashboard never falls back to local sample Parquet. Ingest and publish
    scripts still use ``get_storage()`` so a dead token fails loudly there.
    """
    storage = get_storage()
    if settings.ember_storage_backend != "gcs":
        return storage
    try:
        storage.exists("tables/utilities.parquet")
    except Exception as exc:
        if is_gcs_auth_error(exc):
            return GCSStorage(
                bucket=settings.gcs_bucket,
                prefix=settings.gcs_prefix,
                project=settings.gcs_project,
                anonymous=True,
            )
        raise
    return storage
