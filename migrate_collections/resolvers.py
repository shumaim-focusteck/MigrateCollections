"""v2 -> v3 ID resolution.

v2 campaign_id/collection_id and v3 CampaignID/CollectionTypeID are the same
numeric space in this deployment (confirmed against Terraboost-Dev) — no
mapping table, so this is a straight pass-through. `cache`/`mssql_cursor`
stay in the signature so callers (processor.py) don't need to know that;
swap the body here if that ever stops being true for a given environment.
"""

from typing import Dict, Optional

import pyodbc


def resolve_campaign_id(
    v2_campaign_id: int,
    mssql_cursor: pyodbc.Cursor,
    cache: Dict[int, Optional[int]],
) -> Optional[int]:
    """v2 campaign_id === v3 CampaignID."""
    return v2_campaign_id


def resolve_collection_type_id(
    v2_collection_id: int,
    mssql_cursor: pyodbc.Cursor,
    cache: Dict[int, Optional[int]],
) -> Optional[int]:
    """v2 collection_id === v3 CollectionTypeID."""
    return v2_collection_id
