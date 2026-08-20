#!/usr/bin/env python3
"""Orchestrator entrypoint for the v2 -> v3 collection migration.

This file only wires things together: parse CLI args, load config, open
connections and CSV loggers, then hand off to migrate_collections.batch_runner
for the actual read/transform/write work. See README.md for setup, resuming,
and retrying failures.

    python main.py                       # normal run (reads ./config.json)
    python main.py --dry-run             # rehearse: logs + CSVs written, nothing committed to v3
    python main.py --retry-failed        # reprocess only migrated_failed.csv rows
    python main.py --limit 2             # process at most 2 source rows (for testing)
    python main.py --dry-run --limit 2   # combine both: safe, quick smoke test
    python main.py --config other.json   # use a different config file
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from migrate_collections.batch_runner import load_failed_ids, run_normal, run_retry
from migrate_collections.blob_storage import connect_blob_container
from migrate_collections.config import Config, DEFAULT_CONFIG_PATH, load_config
from migrate_collections.constants import FAILED_CSV, FAILED_HEADER, SKIPPED_CSV, SKIPPED_HEADER, SUCCESS_CSV, SUCCESS_HEADER
from migrate_collections.csv_logger import CsvLogger
from migrate_collections.logging_setup import print_summary, setup_logging
from migrate_collections.models import Stats
from migrate_collections.mssql_dest import connect_mssql
from migrate_collections.mysql_source import connect_mysql
from migrate_collections.shutdown import install_sigint_handler


def _timestamped(path: Path, run_id: str) -> Path:
    """`foo.csv` -> `foo_20260820_181530.csv`, same directory/suffix."""
    return path.with_name(f"{path.stem}_{run_id}{path.suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate collection/file data from v2 MySQL to v3 SQL Server.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run the full read/resolve/decide pipeline and write the CSVs, but roll back "
             "instead of committing to v3. Use this to preview a run before it touches data.",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Skip the normal checkpointed sweep and instead reprocess only the v2 ids "
             "listed in migrated_failed.csv, rewriting that file with whatever still fails.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Process at most N source rows then stop. Handy for a quick smoke test "
             "(e.g. --limit 2) before letting a run process the full table. "
             "Combine with --dry-run for a completely safe test.",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Path to the JSON config file (default: {DEFAULT_CONFIG_PATH}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 1. Load DB connection strings / batch size / output paths from config.json.
    cfg: Config = load_config(args.config)
    # Every run gets its own success/skipped CSVs and log file, so nothing
    # from a previous run is ever overwritten. migrated_failed.csv is the
    # one exception — it stays a fixed, persistent name (see step 4) since
    # --retry-failed depends on reading it back in and rewriting it.
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = _timestamped(cfg.log_file, run_id)
    setup_logging(log_file)
    # 2. Ctrl+C during a run should finish the in-flight batch and exit cleanly
    #    instead of dying mid-transaction; see migrate_collections/shutdown.py.
    install_sigint_handler()

    logging.info(
        "Starting migration (dry_run=%s, retry_failed=%s, limit=%s)",
        args.dry_run, args.retry_failed, args.limit,
    )
    start_time = time.monotonic()
    stats = Stats()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    # 3. Open both database connections. v3 (SQL Server) is used in
    #    autocommit=False mode so each batch can be committed/rolled back
    #    as a unit — see mssql_dest.connect_mssql().
    mysql_conn = connect_mysql(cfg)
    mssql_conn = connect_mssql(cfg)
    mssql_cursor = mssql_conn.cursor()
    # v2 file URLs are downloaded and re-hosted here; see blob_storage.py.
    blob_container = connect_blob_container(cfg, args.dry_run)

    # In-memory caches so each distinct v2 campaign_id / collection_id only
    # needs one mapping-table lookup per run, no matter how many rows share it.
    campaign_cache: Dict[int, Optional[int]] = {}
    collection_cache: Dict[int, Optional[int]] = {}

    # 4. Open the three per-record CSV loggers. success/skipped are fresh,
    #    uniquely-named files every run (see run_id above); the failed-rows
    #    CSV keeps its fixed name, opened in "w" (truncate) mode only for
    #    --retry-failed, since that pass rewrites it from scratch.
    success_path = _timestamped(cfg.output_dir / SUCCESS_CSV, run_id)
    skipped_path = _timestamped(cfg.output_dir / SKIPPED_CSV, run_id)
    failed_path = cfg.output_dir / FAILED_CSV
    success_logger = CsvLogger(success_path, SUCCESS_HEADER)
    skipped_logger = CsvLogger(skipped_path, SKIPPED_HEADER)

    try:
        if args.retry_failed:
            ids_to_retry = load_failed_ids(failed_path)
            failed_logger = CsvLogger(failed_path, FAILED_HEADER, mode="w")
            run_retry(
                cfg, args.dry_run, mysql_conn, mssql_conn, mssql_cursor, blob_container,
                campaign_cache, collection_cache,
                success_logger, failed_logger, skipped_logger, stats, ids_to_retry,
                limit=args.limit,
            )
        else:
            failed_logger = CsvLogger(failed_path, FAILED_HEADER)
            run_normal(
                cfg, args.dry_run, mysql_conn, mssql_conn, mssql_cursor, blob_container,
                campaign_cache, collection_cache,
                success_logger, failed_logger, skipped_logger, stats,
                limit=args.limit,
            )
    finally:
        # Always close files/connections, even if a batch raised and the
        # exception is about to propagate out of main().
        success_logger.close()
        skipped_logger.close()
        failed_logger.close()
        mssql_cursor.close()
        mysql_conn.close()
        mssql_conn.close()

    elapsed = time.monotonic() - start_time
    print_summary(stats, elapsed, success_path, failed_path, skipped_path, args.dry_run)


if __name__ == "__main__":
    main()
