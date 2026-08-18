"""Graceful Ctrl+C handling shared between the orchestrator and the batch runner.

`install_sigint_handler()` is called once from main(). The batch runner polls
`is_shutdown_requested()` between batches so an in-flight batch always finishes
(commit + checkpoint) before the process exits.
"""

import logging
import signal
from typing import Any

_shutdown_requested = False


def _handle_sigint(signum: int, frame: Any) -> None:
    global _shutdown_requested
    if not _shutdown_requested:
        logging.warning("SIGINT received — finishing the current batch, then exiting.")
    _shutdown_requested = True


def install_sigint_handler() -> None:
    signal.signal(signal.SIGINT, _handle_sigint)


def is_shutdown_requested() -> bool:
    return _shutdown_requested
