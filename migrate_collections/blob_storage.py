"""Re-hosts v2 file URLs in Azure Blob Storage.

Each file gets a deterministic blob path: `{v3_id}/{v3_column}/{filename}`.
That determinism is what makes upload idempotent — a blob that's already
there (from a prior run, including one interrupted mid-batch before the v3
transaction committed) is reused instead of re-downloaded/re-uploaded.
"""

from typing import Dict, Optional, Union
from urllib.parse import urlparse

import requests
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContainerClient

from .config import Config

_DOWNLOAD_TIMEOUT_SECONDS = 30

# Real runs key blob paths on the v3 row's id (an int). Dry-run previews have
# no real v3_id yet (nothing was actually inserted), so callers pass a
# placeholder string instead — see processor.py.
BlobId = Union[int, str]


def connect_blob_container(cfg: Config, dry_run: bool) -> Optional[ContainerClient]:
    """Returns None for --dry-run: no blob is ever read, uploaded, or its
    URL persisted in that mode, so a real connection/credentials/container
    aren't needed at all — only a real run requires them."""
    if dry_run:
        return None
    if not cfg.azure_blob_conn_str or not cfg.azure_blob_container:
        raise ValueError(
            "azure_blob.connection_string and azure_blob.container_name are "
            "required in config.json for a real (non-dry-run) run."
        )
    container = ContainerClient.from_connection_string(
        cfg.azure_blob_conn_str, container_name=cfg.azure_blob_container
    )
    try:
        container.create_container()
    except ResourceExistsError:
        pass
    return container


def blob_name_for(v3_id: BlobId, column: str, source_url: str) -> str:
    """Deterministic blob path for one file: {v3_id}/{column}/{filename}."""
    filename = urlparse(source_url).path.rsplit("/", 1)[-1] or "file"
    return f"{v3_id}/{column}/{filename}"


def upload_file_url(
    container: Optional[ContainerClient], v3_id: BlobId, column: str, source_url: str, dry_run: bool
) -> str:
    """Return the v3-facing blob URL for `source_url`, downloading and
    uploading it only if that blob doesn't already exist in the container.

    In dry-run mode (`container` is None), returns a preview path without
    touching Azure at all — nothing downstream persists this value anyway."""
    blob_name = blob_name_for(v3_id, column, source_url)
    if dry_run or container is None:
        return f"(dry-run preview) {blob_name}"

    blob_client = container.get_blob_client(blob_name)
    if blob_client.exists():
        return blob_client.url

    response = requests.get(source_url, stream=True, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    blob_client.upload_blob(response.content, overwrite=False)
    return blob_client.url


def upload_file_columns(
    container: Optional[ContainerClient], v3_id: BlobId, columns: Dict[str, str], dry_run: bool
) -> Dict[str, str]:
    """Map {v3_column: source_url} -> {v3_column: blob_url}, uploading
    whatever isn't already in blob storage. Raises on the first
    download/upload failure — callers treat that as a failed row."""
    return {
        column: upload_file_url(container, v3_id, column, source_url, dry_run)
        for column, source_url in columns.items()
    }
