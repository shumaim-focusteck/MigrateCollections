"""Append-friendly CSV writer used for the three per-record output files."""

import csv
import os
from pathlib import Path
from typing import Any, List


class CsvLogger:
    """Writes a header exactly once, then appends rows. `flush()` fsyncs so
    the file is accurate on disk even if the process is killed mid-run."""

    def __init__(self, path: Path, header: List[str], mode: str = "a"):
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = mode == "w" or not path.exists() or path.stat().st_size == 0
        self._fh = open(path, mode, newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        if needs_header:
            self._writer.writerow(header)
            self._fh.flush()

    def write_row(self, row: List[Any]) -> None:
        self._writer.writerow(row)

    def flush(self) -> None:
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass

    def close(self) -> None:
        self._fh.close()
