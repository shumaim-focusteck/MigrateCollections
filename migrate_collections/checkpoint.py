"""Resume-from-last-id checkpoint file.

Written atomically (tmp file + rename) after every committed batch, so a
crash mid-write never leaves a corrupt checkpoint.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path


def load_checkpoint(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("last_v2_id", 0))
    except (json.JSONDecodeError, ValueError, OSError):
        logging.warning("Checkpoint file %s is unreadable; starting from id 0.", path)
        return 0


def save_checkpoint(path: Path, last_v2_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps({"last_v2_id": last_v2_id, "updated_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    tmp_path.replace(path)
