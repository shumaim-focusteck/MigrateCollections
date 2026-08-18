"""Shared dataclasses passed between the processor and the batch runner."""

from dataclasses import dataclass, field
from typing import List, Optional


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
    columns_written: List[str] = field(default_factory=list)
