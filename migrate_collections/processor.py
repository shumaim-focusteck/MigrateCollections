"""Per-row decision logic: parse -> resolve IDs -> re-host files -> insert/update/skip -> record.

This is the only place that implements the column-level idempotency rule: a
v3 column is written if its current value doesn't match the deterministic
blob URL this file would get (blob_storage.compute_blob_url()) — covering
both an empty column and one holding some other/stale URL. A column already
holding exactly that URL is left untouched (see "already_updated" in the
skipped CSV). Only columns that will actually be written get re-hosted in
blob storage,
via blob_storage.upload_file_columns() (itself idempotent per blob, keyed on
campaign_id_v3). A brand-new row is inserted bare first (to obtain a v3_id
for the later UPDATE) before files are uploaded and the row updated with the
resulting blob URLs; a failed upload deletes that bare insert rather than
leaving a stray row.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pyodbc
from azure.storage.blob import ContainerClient

from .blob_storage import compute_blob_url, upload_file_columns
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
            source_by_column=file_columns,
        )

    collection_type_id = resolve_collection_type_id(v2_collection_id, mssql_cursor, collection_cache)
    if collection_type_id is None:
        return RowResult(
            status="failed", v2_id=v2_id, reason="unresolved_collection",
            error=f"No v3 collection type mapping found for v2 collection_id={v2_collection_id}",
            campaign_id_v2=v2_campaign_id, campaign_id_v3=v3_campaign_id, collection_id_v2=v2_collection_id,
            source_by_column=file_columns,
        )

    # Step 3: nothing to write at all -> skip rather than insert/update an
    # empty-looking row.
    if not file_columns:
        return RowResult(
            status="skipped", v2_id=v2_id, reason="no_file_urls_in_source",
            campaign_id_v3=v3_campaign_id, collection_type_id=collection_type_id,
        )

    # Step 4: does a v3 row already exist for this (campaign, collection type)?
    # A bare row (match-key columns only) is inserted first for a brand-new
    # row so there's a v3_id ready for the later UPDATE. This also means the
    # insert and update paths converge below: both end up as "a v3_id, plus
    # the file columns still needing a value".
    existing = fetch_existing_v3_row(mssql_cursor, v3_campaign_id, collection_type_id)

    if existing is None:
        action = "inserted"
        v3_id, _ = insert_v3_row(mssql_cursor, v3_campaign_id, collection_type_id, {}, dry_run)
        to_fill_source = file_columns
        already_updated_source: Dict[str, str] = {}
        already_updated_existing: Dict[str, str] = {}
    else:
        action = "updated"
        v3_id = existing[V3_ID_COLUMN]
        already_updated_source = {}
        already_updated_existing = {}
        to_fill_source = {}
        if dry_run:
            # No real container/account to compute a comparable candidate
            # URL against in dry-run -> fall back to the simpler
            # empty-vs-non-empty check. Only affects what the preview
            # *shows*; nothing is ever written in dry-run either way.
            for col, url in file_columns.items():
                if existing.get(col):
                    already_updated_source[col] = url
                    already_updated_existing[col] = existing[col]
                else:
                    to_fill_source[col] = url
        else:
            # Column-level idempotency: a column is written if its current
            # value doesn't match the deterministic blob URL this file
            # would get — covering both an empty column and one holding
            # some other/stale value (e.g. from before a blob-path scheme
            # change). A column already holding exactly that URL is left
            # untouched, logged instead as an "already_updated" skip.
            for col, url in file_columns.items():
                candidate_url = compute_blob_url(blob_container, v3_campaign_id, col, url)
                current = existing.get(col)
                if current and current == candidate_url:
                    already_updated_source[col] = url
                    already_updated_existing[col] = current
                else:
                    to_fill_source[col] = url
        if not to_fill_source:
            return RowResult(
                status="skipped", v2_id=v2_id, reason="all_columns_already_populated",
                campaign_id_v3=v3_campaign_id, collection_type_id=collection_type_id,
                source_by_column=file_columns, written_by_column=already_updated_existing,
            )

    # Every column is attempted independently — a bad file in one column
    # doesn't cost the others. Whatever succeeded gets persisted regardless
    # of whether some columns failed alongside it. Blob paths are keyed on
    # campaign_id_v3, which (unlike v3_id) is already known even in
    # dry-run, so previews show the real path, not a placeholder.
    to_fill, upload_errors = upload_file_columns(blob_container, v3_campaign_id, to_fill_source, dry_run)

    if to_fill:
        update_v3_row(mssql_cursor, v3_id, to_fill, dry_run)

    if upload_errors:
        if not to_fill and action == "inserted" and not dry_run:
            # Nothing at all succeeded -> undo the bare insert (still
            # uncommitted within this batch's transaction) so a fully
            # failed row never leaves an orphaned, file-less row behind.
            delete_v3_row(mssql_cursor, v3_id)
        error_message = "; ".join(f"{col}: {err}" for col, err in upload_errors.items())
        reason = "blob_upload_failed_partial" if to_fill else "blob_upload_failed_full"
        return RowResult(
            status="failed", v2_id=v2_id, reason=reason, error=error_message,
            campaign_id_v2=v2_campaign_id, campaign_id_v3=v3_campaign_id, collection_id_v2=v2_collection_id,
            # source_by_column: every file v2 had for this row.
            # written_by_column: only the columns that actually succeeded —
            # a column with a source cell but no written cell is the one(s)
            # that failed (error_message says why, per column).
            source_by_column=file_columns, written_by_column=to_fill,
            already_updated_source=already_updated_source, already_updated_existing=already_updated_existing,
        )

    return RowResult(
        status="success", v2_id=v2_id, action=action, v3_id=v3_id,
        campaign_id_v2=v2_campaign_id, campaign_id_v3=v3_campaign_id,
        collection_type_id=collection_type_id,
        # source_by_column: every file v2 had for this row, written or not.
        # written_by_column: only the ones actually written this run.
        source_by_column=file_columns, written_by_column=to_fill,
        already_updated_source=already_updated_source, already_updated_existing=already_updated_existing,
    )


def _file_url_cells(source_by_column: Dict[str, str], target_by_column: Dict[str, str]) -> list:
    """One source/target cell per FILE_COLUMN_MAP entry (e.g. "top_file",
    "TopFileUrl"), blank where that file has no entry in the given dicts."""
    cells = []
    for v3_column in FILE_COLUMN_MAP.values():
        cells.append(source_by_column.get(v3_column, ""))
        cells.append(target_by_column.get(v3_column, ""))
    return cells


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
        success_logger.write_row([
            result.v2_id,
            result.v3_id if result.v3_id is not None else "",
            result.campaign_id_v2,
            result.campaign_id_v3,
            result.collection_type_id,
            action,
            *_file_url_cells(result.source_by_column, result.written_by_column),
            now,
        ])
        if result.action == "inserted":
            stats.inserted += 1
        elif result.action == "updated":
            stats.updated += 1

    elif result.status == "skipped":
        skipped_logger.write_row([
            result.v2_id, result.campaign_id_v3, result.collection_type_id, result.reason,
            *_file_url_cells(result.source_by_column, result.written_by_column),
            now,
        ])
        stats.skipped += 1

    else:
        failed_logger.write_row([
            result.v2_id, result.campaign_id_v2, result.campaign_id_v3, result.collection_id_v2, result.reason,
            *_file_url_cells(result.source_by_column, result.written_by_column),
            result.error or "", now,
        ])
        stats.failed += 1

    # Regardless of the row's main outcome above, a success/failed row can
    # also have columns that were skipped this run because v3 already had a
    # value for them (row-level idempotency) — log those to the skipped CSV
    # too, so they're never silently invisible. Not counted in stats.skipped
    # (that tracks rows, not columns) to keep read == inserted+updated+skipped+failed.
    if result.already_updated_source:
        skipped_logger.write_row([
            result.v2_id, result.campaign_id_v3, result.collection_type_id, "already_updated",
            *_file_url_cells(result.already_updated_source, result.already_updated_existing),
            now,
        ])
