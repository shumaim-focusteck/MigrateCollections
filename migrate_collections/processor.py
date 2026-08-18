"""Per-row decision logic: parse -> resolve IDs -> insert/update/skip -> record.

This is the only place that implements the column-level idempotency rule:
a v3 column is written if and only if it's currently NULL/empty and the
source has a non-empty value for it. An existing value is never overwritten.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pyodbc

from .constants import V3_ID_COLUMN
from .csv_logger import CsvLogger
from .models import RowResult, Stats
from .mssql_dest import fetch_existing_v3_row, insert_v3_row, update_v3_row
from .resolvers import resolve_campaign_id, resolve_collection_type_id
from .transform import parse_files_json


def process_row(
    row: Dict[str, Any],
    mssql_cursor: pyodbc.Cursor,
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
    existing = fetch_existing_v3_row(mssql_cursor, v3_campaign_id, collection_type_id)

    if existing is None:
        # No row yet -> insert with whatever file URLs the source has.
        v3_id, written = insert_v3_row(mssql_cursor, v3_campaign_id, collection_type_id, file_columns, dry_run)
        return RowResult(
            status="success", v2_id=v2_id, action="inserted", v3_id=v3_id,
            campaign_id_v2=v2_campaign_id, campaign_id_v3=v3_campaign_id,
            collection_type_id=collection_type_id, columns_written=written,
        )

    # Row exists -> column-level idempotency: only fill columns that are
    # currently NULL/empty in v3. Never overwrite a column that already has
    # a value, even if the source value differs.
    to_fill = {col: url for col, url in file_columns.items() if not existing.get(col)}
    if not to_fill:
        return RowResult(
            status="skipped", v2_id=v2_id, reason="all_columns_already_populated",
            campaign_id_v3=v3_campaign_id, collection_type_id=collection_type_id,
        )

    update_v3_row(mssql_cursor, existing[V3_ID_COLUMN], to_fill, dry_run)
    return RowResult(
        status="success", v2_id=v2_id, action="updated", v3_id=existing[V3_ID_COLUMN],
        campaign_id_v2=v2_campaign_id, campaign_id_v3=v3_campaign_id,
        collection_type_id=collection_type_id, columns_written=list(to_fill.keys()),
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
        success_logger.write_row([
            result.v2_id,
            result.v3_id if result.v3_id is not None else "",
            result.campaign_id_v2,
            result.campaign_id_v3,
            result.collection_type_id,
            action,
            ";".join(result.columns_written),
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
