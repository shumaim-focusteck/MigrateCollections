"""Shared dataclasses passed between the processor and the batch runner."""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Stats:
    read: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class RowResult:
    """Outcome of processing a single v2 row."""

    status: str  # "success" | "skipped" | "failed"
    v2_id: int
    reason: Optional[str] = None
    error: Optional[str] = None
    action: Optional[str] = None  # "inserted" | "updated"
    v3_id: Optional[int] = None
    campaign_id_v2: Optional[int] = None
    campaign_id_v3: Optional[int] = None
    collection_id_v2: Optional[int] = None
    collection_type_id: Optional[int] = None
    # Both keyed by v3 column name (e.g. "TopFileUrl"). source_by_column holds
    # every file URL v2 actually had, whether or not it ended up written this
    # run; written_by_column holds only the ones actually written this run.
    source_by_column: Dict[str, str] = field(default_factory=dict)
    written_by_column: Dict[str, str] = field(default_factory=dict)
    # Columns that were *not* attempted this run because v3 already had a
    # value for them (row-level idempotency). Non-empty only on a
    # success/failed row that also had some already-populated columns
    # alongside the ones actually attempted; record_result() logs these to
    # the skipped CSV in addition to the row's main success/failed entry.
    already_updated_source: Dict[str, str] = field(default_factory=dict)
    already_updated_existing: Dict[str, str] = field(default_factory=dict)
