"""Console+file logging setup and the end-of-run summary."""

import logging
import sys
from pathlib import Path

from .models import Stats


def setup_logging(log_file: Path) -> Path:
    """Sets up the normal INFO+ log file/console, plus a second WARNING+ file
    (e.g. `migration.log` -> `migration_errors.log`) holding only failures and
    unexpected exceptions, so those don't have to be grepped out of the full
    (much noisier, per-row) log. Returns the error log's path."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    error_log_file = log_file.with_name(f"{log_file.stem}_errors{log_file.suffix}")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    error_handler = logging.FileHandler(error_log_file, encoding="utf-8")
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger().addHandler(error_handler)
    return error_log_file


def print_summary(
    stats: Stats, elapsed: float, success_csv: Path, failed_csv: Path, skipped_csv: Path,
    error_log: Path, dry_run: bool
) -> None:
    mode_note = " (DRY RUN — nothing was committed to v3)" if dry_run else ""
    summary = (
        "\n" + "=" * 60 + "\n"
        f"Migration finished{mode_note}\n"
        f"  Rows read   : {stats.read}\n"
        f"  Inserted    : {stats.inserted}\n"
        f"  Updated     : {stats.updated}\n"
        f"  Skipped     : {stats.skipped}\n"
        f"  Failed      : {stats.failed}\n"
        f"  Elapsed     : {elapsed:.1f}s\n"
        f"  Success CSV : {success_csv}\n"
        f"  Failed CSV  : {failed_csv}\n"
        f"  Skipped CSV : {skipped_csv}\n"
        f"  Error log   : {error_log}\n"
        + "=" * 60
    )
    logging.info(summary)
