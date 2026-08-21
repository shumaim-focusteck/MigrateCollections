"""Batch orchestration: transaction boundaries, checkpointing, and the retry pass.

`run_batch()` is the only place a v3 transaction is committed or rolled back.
A row-level exception is caught there, logged as a `db_error` failure, and the
batch continues — it never aborts the rest of the batch.
"""

import csv
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
import pyodbc
from azure.storage.blob import ContainerClient

from .checkpoint import load_checkpoint, save_checkpoint
from .config import Config
from .csv_logger import CsvLogger
from .models import RowResult, Stats
from .mysql_source import fetch_batch, fetch_by_ids
from .processor import process_row, record_result
from .shutdown import is_shutdown_requested


def run_batch(
    rows: List[Dict[str, Any]],
    mssql_conn: pyodbc.Connection,
    mssql_cursor: pyodbc.Cursor,
    blob_container: Optional[ContainerClient],
    campaign_cache: Dict[int, Optional[int]],
    collection_cache: Dict[int, Optional[int]],
    dry_run: bool,
    success_logger: CsvLogger,
    failed_logger: CsvLogger,
    skipped_logger: CsvLogger,
    stats: Stats,
) -> Optional[int]:
    """Process one batch inside a single transaction.

    Returns the highest v2 id seen in this batch (used to advance the
    checkpoint), or None if `rows` was empty.
    """
    max_id: Optional[int] = None
    try:
        for row in rows:
            # Track the highest id we've seen so far so the checkpoint can
            # advance even if some rows in the batch fail or get skipped.
            max_id = row["id"] if max_id is None else max(max_id, row["id"])

            logging.info(
                "Processing v2 id=%s campaign_id=%s collection_id=%s",
                row["id"], row.get("campaign_id"), row.get("collection_id"),
            )
            try:
                result = process_row(row, mssql_cursor, blob_container, campaign_cache, collection_cache, dry_run)
            except Exception as exc:  # noqa: BLE001 - a single row must not abort the batch
                # Anything unexpected (bad connection, constraint violation, etc.)
                # is recorded as a failed row instead of killing the whole batch.
                logging.exception("Row-level error processing v2 id=%s", row["id"])
                result = RowResult(
                    status="failed", v2_id=row["id"], reason="db_error", error=str(exc),
                    campaign_id_v2=row.get("campaign_id"), collection_id_v2=row.get("collection_id"),
                )
            record_result(result, success_logger, failed_logger, skipped_logger, stats, dry_run)

        # --dry-run: everything above ran (including SELECTs used to decide
        # insert vs. update), but we throw the writes away instead of committing.
        if dry_run:
            mssql_conn.rollback()
        else:
            mssql_conn.commit()
    except Exception:
        # Something broke badly enough to abort the whole batch (e.g. the
        # connection dropped) — undo any uncommitted writes and re-raise.
        mssql_conn.rollback()
        raise
    return max_id


def _flush_all(*loggers: CsvLogger) -> None:
    """fsync every CSV so the output files are accurate even if the process
    is killed immediately after this call."""
    for logger in loggers:
        logger.flush()


def run_normal(
    cfg: Config,
    dry_run: bool,
    mysql_conn: pymysql.connections.Connection,
    mssql_conn: pyodbc.Connection,
    mssql_cursor: pyodbc.Cursor,
    blob_container: Optional[ContainerClient],
    campaign_cache: Dict[int, Optional[int]],
    collection_cache: Dict[int, Optional[int]],
    success_logger: CsvLogger,
    failed_logger: CsvLogger,
    skipped_logger: CsvLogger,
    stats: Stats,
    limit: Optional[int] = None,
) -> None:
    """Read v2 in id-ordered batches from the checkpoint onward, committing
    and checkpointing after each one, until the source is exhausted, a
    shutdown is requested, or (if given) `limit` rows have been read.

    `limit` is meant for smoke-testing the migration against a handful of
    rows (e.g. `--limit 2`) before running it against the full table.
    `cfg.campaign_id_min`/`cfg.campaign_id_max` (config.json's `filters`
    section) restrict which v2 campaign_ids are migrated at all.
    """
    last_id = load_checkpoint(cfg.checkpoint_file)
    logging.info("Resuming from v2 id > %s", last_id)
    if limit is not None:
        logging.info("Test mode: will stop after %s row(s) total.", limit)
    if cfg.campaign_id_min is not None or cfg.campaign_id_max is not None:
        logging.info(
            "Restricting to v2 campaign_id range [%s, %s].",
            cfg.campaign_id_min, cfg.campaign_id_max,
        )

    batch_num = 0
    while not is_shutdown_requested():
        # When --limit is set, never fetch more rows than are still needed
        # to hit it, so a trial run genuinely touches only N source rows.
        batch_size = cfg.batch_size
        if limit is not None:
            remaining = limit - stats.read
            if remaining <= 0:
                logging.info("Reached --limit of %s row(s); stopping.", limit)
                break
            batch_size = min(batch_size, remaining)

        rows = fetch_batch(mysql_conn, last_id, batch_size, cfg.campaign_id_min, cfg.campaign_id_max)
        if not rows:
            logging.info("No more rows to process.")
            break

        batch_num += 1
        before = (stats.inserted, stats.updated, stats.skipped, stats.failed)

        # Everything for this batch - resolving IDs, deciding insert/update/
        # skip, and committing (or rolling back on --dry-run) - happens here.
        max_id = run_batch(
            rows, mssql_conn, mssql_cursor, blob_container, campaign_cache, collection_cache, dry_run,
            success_logger, failed_logger, skipped_logger, stats,
        )
        stats.read += len(rows)
        _flush_all(success_logger, failed_logger, skipped_logger)

        # Advance and persist the checkpoint so a re-run resumes here, not
        # from the start. Skipped during --dry-run since nothing was written.
        last_id = max_id if max_id is not None else last_id
        if not dry_run:
            save_checkpoint(cfg.checkpoint_file, last_id)

        logging.info(
            "Batch %s: read=%s inserted=%s updated=%s skipped=%s failed=%s",
            batch_num, len(rows),
            stats.inserted - before[0], stats.updated - before[1],
            stats.skipped - before[2], stats.failed - before[3],
        )

        if is_shutdown_requested():
            # Ctrl+C was caught mid-batch: the batch above already finished
            # and was committed/checkpointed, so it's safe to stop here.
            logging.warning("Shutdown requested — stopping after batch %s.", batch_num)
            break


def load_failed_ids(path: Path) -> List[int]:
    """Read the v2 ids out of a previous migrated_failed.csv for --retry-failed."""
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [int(r["v2_id"]) for r in csv.DictReader(fh)]


def run_retry(
    cfg: Config,
    dry_run: bool,
    mysql_conn: pymysql.connections.Connection,
    mssql_conn: pyodbc.Connection,
    mssql_cursor: pyodbc.Cursor,
    blob_container: Optional[ContainerClient],
    campaign_cache: Dict[int, Optional[int]],
    collection_cache: Dict[int, Optional[int]],
    success_logger: CsvLogger,
    failed_logger: CsvLogger,
    skipped_logger: CsvLogger,
    stats: Stats,
    ids_to_retry: List[int],
    limit: Optional[int] = None,
) -> None:
    """Reprocess only the given v2 ids (from a previous migrated_failed.csv).

    Same `limit` behavior as run_normal: caps how many of the failed ids are
    retried, for testing.
    """
    if limit is not None:
        ids_to_retry = ids_to_retry[:limit]
    logging.info("Retrying %s previously failed row(s).", len(ids_to_retry))

    batch_num = 0
    for i in range(0, len(ids_to_retry), cfg.batch_size):
        if is_shutdown_requested():
            logging.warning("Shutdown requested — stopping retry pass.")
            break

        chunk = ids_to_retry[i:i + cfg.batch_size]
        rows = fetch_by_ids(mysql_conn, chunk)
        batch_num += 1
        run_batch(
            rows, mssql_conn, mssql_cursor, blob_container, campaign_cache, collection_cache, dry_run,
            success_logger, failed_logger, skipped_logger, stats,
        )
        stats.read += len(rows)
        _flush_all(success_logger, failed_logger, skipped_logger)

        logging.info("Retry batch %s: attempted=%s", batch_num, len(rows))
