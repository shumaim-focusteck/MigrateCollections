"""Parsing of the v2 `files` JSON blob into v3 column values."""

import json
from typing import Any, Dict

from .constants import FILE_COLUMN_MAP


def parse_files_json(raw: Any) -> Dict[str, str]:
    """Parse the v2 `files` column into {v3_column: url}, dropping any key
    that's missing, null, or blank.

    Raises json.JSONDecodeError / ValueError on malformed input — callers
    should treat that as an `invalid_json` failed row.
    """
    if raw is None:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        data = json.loads(raw)
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError(f"Unexpected type for files column: {type(raw)!r}")

    result: Dict[str, str] = {}
    for json_key, column in FILE_COLUMN_MAP.items():
        value = data.get(json_key)
        if isinstance(value, str) and value.strip():
            result[column] = value.strip()
    return result
