"""Per-row decision logic: parse -> resolve IDs -> re-host files -> insert/update/skip -> record.

This is the only place that implements the column-level idempotency rule:
a v3 column is written if and only if it's currently NULL/empty and the
source has a non-empty value for it. An existing value is never overwritten.
Only columns that will actually be written get re-hosted in blob storage,
via blob_storage.upload_file_columns() (itself idempotent per blob, keyed on
v3_id). A brand-new row is inserted bare first (to obtain a v3_id) before
files are uploaded and the row updated with the resulting blob URLs; a
failed upload deletes that bare insert rather than leaving a stray row.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pyodbc
from azure.storage.blob import ContainerClient

from .blob_storage import upload_file_columns
from .constants import FILE_COLUMN_MAP, V3_ID_COLUMN
from .csv_logger import CsvLogger
from .models import RowResult, Stats
from .mssql_dest import delete_v3_row, fetch_existing_v3_row, insert_v3_row, update_v3_row
from .resolvers import resolve_campaign_id, resolve_collection_type_id
from .transform import parse_files_json


def process_row(
    row: Dict[str, Any],
    mssql_cursor: pyodbc.Cursor,
    blob_container: Optional[ContainerClient],
    campaign_cache: Dict[int, Optional[int]],
    collection_cache: Dict[int, Optional[int]],
    dry_run: bool,
) -> RowResult:
    v2_id = row["id"]
    v2_campaign_id = row["campaign_id"]
    v2_collection_id = row["collection_id"]

    # Step 1: parse the `files` JSON blob into {v3_column: url}. A malformed
    # blob fails the row outright — there's nothing sensible to write.
    try:
        file_columns = parse_files_json(row["files"])
    except (json.JSONDecodeError, ValueError) as exc:
        return RowResult(
            status="failed", v2_id=v2_id, reason="invalid_json", error=str(exc),
            campaign_id_v2=v2_campaign_id, collection_id_v2=v2_collection_id,
        )

    # Step 2: resolve v2 ids to their v3 equivalents. Both live in a
    # different ID space in v3, so a row we can't map is a failure, not a skip.
    v3_campaign_id = resolve_campaign_id(v2_campaign_id, mssql_cursor, campaign_cache)
    if v3_campaign_id is None:
        return RowResult(
            status="failed", v2_id=v2_id, reason="unresolved_campaign",
            error=f"No v3 campaign mapping found for v2 campaign_id={v2_campaign_id}",
            campaign_id_v2=v2_campaign_id, collection_id_v2=v2_collection_id,
        )

    collection_type_id = resolve_collection_type_id(v2_collection_id, mssql_cursor, collection_cache)
    if collection_type_id is None:
        return RowResult(
            status="failed", v2_id=v2_id, reason="unresolved_collection",
            error=f"No v3 collection type mapping found for v2 collection_id={v2_collection_id}",
            campaign_id_v2=v2_campaign_id, collection_id_v2=v2_collection_id,
        )

    # Step 3: nothing to write at all -> skip rather than insert/update an
    # empty-looking row.
    if not file_columns:
        return RowResult(
            status="skipped", v2_id=v2_id, reason="no_file_urls_in_source",
            campaign_id_v3=v3_campaign_id, collection_type_id=collection_type_id,
        )

    # Step 4: does a v3 row already exist for this (campaign, collection type)?
    # Blob paths are keyed by v3_id, which doesn't exist yet for a brand-new
    # row -> insert a bare row (match-key columns only) first to obtain one.
    # This also means the insert and update paths converge below: both end
    # up as "a v3_id, plus the file columns still needing a value".
    existing = fetch_existing_v3_row(mssql_cursor, v3_campaign_id, collection_type_id)

    if existing is None:
        action = "inserted"
        v3_id, _ = insert_v3_row(mssql_cursor, v3_campaign_id, collection_type_id, {}, dry_run)
        to_fill_source = file_columns
    else:
        action = "updated"
        v3_id = existing[V3_ID_COLUMN]
        # Column-level idempotency: only fill columns that are currently
        # NULL/empty in v3. Never overwrite a column that already has a
        # value, even if the source value differs.
        to_fill_source = {col: url for col, url in file_columns.items() if not existing.get(col)}
        if not to_fill_source:
            return RowResult(
                status="skipped", v2_id=v2_id, reason="all_columns_already_populated",
                campaign_id_v3=v3_campaign_id, collection_type_id=collection_type_id,
            )

    # dry-run never actually inserts, so there's no real v3_id yet to key
    # blob paths on -> fall back to a clearly-marked placeholder for the
    # preview; nothing is downloaded/uploaded/written either way.
    blob_id = v3_id if v3_id is not None else f"dryrun-pending-v2-{v2_id}"

    try:
        to_fill = upload_file_columns(blob_container, blob_id, to_fill_source, dry_run)
    except Exception as exc:  # noqa: BLE001 - network/HTTP errors from the download/upload
        if action == "inserted" and not dry_run:
            # Undo the bare insert (still uncommitted within this batch's
            # transaction) so a failed row never leaves an orphaned,
            # file-less row behind in v3.
            delete_v3_row(mssql_cursor, v3_id)
        return RowResult(
            status="failed", v2_id=v2_id, reason="blob_upload_failed", error=str(exc),
            campaign_id_v2=v2_campaign_id, collection_id_v2=v2_collection_id,
        )

    update_v3_row(mssql_cursor, v3_id, to_fill, dry_run)
    return RowResult(
        status="success", v2_id=v2_id, action=action, v3_id=v3_id,
        campaign_id_v2=v2_campaign_id, campaign_id_v3=v3_campaign_id,
        collection_type_id=collection_type_id,
        # source_by_column: every file v2 had for this row, written or not.
        # written_by_column: only the ones actually written this run.
        source_by_column=file_columns, written_by_column=to_fill,
    )


def record_result(
    result: RowResult,
    success_logger: CsvLogger,
    failed_logger: CsvLogger,
    skipped_logger: CsvLogger,
    stats: Stats,
    dry_run: bool,
) -> None:
    """Write one CSV row for `result` and bump the matching stats counter."""
    now = datetime.now(timezone.utc).isoformat()

    if result.status == "success":
        # In dry-run mode nothing was actually committed, so the action is
        # prefixed to make that unambiguous when reading the CSV later.
        action = f"would_{result.action}" if dry_run else result.action
        # One source/target cell per FILE_COLUMN_MAP entry (e.g. "top_file",
        # "TopFileUrl"), blank where that file wasn't in v2 / wasn't written.
        file_url_cells = []
        for v3_column in FILE_COLUMN_MAP.values():
            file_url_cells.append(result.source_by_column.get(v3_column, ""))
            file_url_cells.append(result.written_by_column.get(v3_column, ""))
        success_logger.write_row([
            result.v2_id,
            result.v3_id if result.v3_id is not None else "",
            result.campaign_id_v2,
            result.campaign_id_v3,
            result.collection_type_id,
            action,
            *file_url_cells,
            now,
        ])
        if result.action == "inserted":
            stats.inserted += 1
        elif result.action == "updated":
            stats.updated += 1

    elif result.status == "skipped":
        skipped_logger.write_row([
            result.v2_id, result.campaign_id_v3, result.collection_type_id, result.reason, now,
        ])
        stats.skipped += 1

    else:
        failed_logger.write_row([
            result.v2_id, result.campaign_id_v2, result.collection_id_v2,
            result.reason, result.error or "", now,
        ])
        stats.failed += 1
