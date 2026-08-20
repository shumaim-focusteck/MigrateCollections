"""Re-hosts v2 file URLs in Azure Blob Storage.

Each file gets a deterministic blob path: `{campaign_id_v3}/{v3_column}/{filename}`.
That determinism is what makes upload idempotent — a blob that's already
there (from a prior run, including one interrupted mid-batch before the v3
transaction committed) is reused instead of re-downloaded/re-uploaded — and
lets processor.py compare a v3 column's current value against this file's
would-be blob URL (compute_blob_url()) before deciding whether it even
needs a write.
"""

from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import requests
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContainerClient

from .config import Config

_DOWNLOAD_TIMEOUT_SECONDS = 30


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


def blob_name_for(campaign_id_v3: int, column: str, source_url: str) -> str:
    """Deterministic blob path for one file: {campaign_id_v3}/{column}/{filename}."""
    filename = urlparse(source_url).path.rsplit("/", 1)[-1] or "file"
    return f"{campaign_id_v3}/{column}/{filename}"


def compute_blob_url(container: ContainerClient, campaign_id_v3: int, column: str, source_url: str) -> str:
    """The deterministic blob URL `source_url` would get, without touching
    the network — pure string construction from the container/blob name.
    Lets callers decide "is v3's current value already correct" before
    committing to an upload attempt."""
    return container.get_blob_client(blob_name_for(campaign_id_v3, column, source_url)).url


def upload_file_url(
    container: Optional[ContainerClient], campaign_id_v3: int, column: str, source_url: str, dry_run: bool
) -> str:
    """Return the v3-facing blob URL for `source_url`, downloading and
    uploading it only if that blob doesn't already exist in the container.

    In dry-run mode (`container` is None), returns a preview path without
    touching Azure at all — nothing downstream persists this value anyway."""
    blob_name = blob_name_for(campaign_id_v3, column, source_url)
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
    container: Optional[ContainerClient], campaign_id_v3: int, columns: Dict[str, str], dry_run: bool
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Attempts every column independently, even after one fails, so a
    single bad file doesn't lose the others in the same row. Returns
    (written, errors): written maps v3_column -> blob URL for columns that
    succeeded; errors maps v3_column -> error message for columns that
    didn't. Callers persist `written` regardless of whether `errors` is
    non-empty, and report `errors` as a partial (or total) row failure."""
    written: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    for column, source_url in columns.items():
        try:
            written[column] = upload_file_url(container, campaign_id_v3, column, source_url, dry_run)
        except Exception as exc:  # noqa: BLE001 - network/HTTP errors from the download/upload
            errors[column] = str(exc)
    return written, errors
