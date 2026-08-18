"""v2 -> v3 ID resolution.

These are the two functions you're most likely to need to rewrite: v2 and v3
IDs live in different spaces, so "how do I map one to the other" is specific
to your data. The default implementation looks up a mapping table in the v3
database; swap the body for a static dict, another table, an API call, etc.
Both cache results (including unresolved lookups) for the lifetime of the run.
"""

from typing import Dict, Optional

import pyodbc

from .constants import (
    CAMPAIGN_MAP_TABLE,
    CAMPAIGN_MAP_V2_COLUMN,
    CAMPAIGN_MAP_V3_COLUMN,
    COLLECTION_TYPE_MAP_TABLE,
    COLLECTION_TYPE_MAP_V2_COLUMN,
    COLLECTION_TYPE_MAP_V3_COLUMN,
)


def resolve_campaign_id(
    v2_campaign_id: int,
    mssql_cursor: pyodbc.Cursor,
    cache: Dict[int, Optional[int]],
) -> Optional[int]:
    """Resolve a v2 campaign_id to its v3 CampaignID, or None if unmapped."""
    if v2_campaign_id in cache:
        return cache[v2_campaign_id]
    query = (
        f"SELECT {CAMPAIGN_MAP_V3_COLUMN} FROM {CAMPAIGN_MAP_TABLE} "
        f"WHERE {CAMPAIGN_MAP_V2_COLUMN} = ?"
    )
    mssql_cursor.execute(query, (v2_campaign_id,))
    row = mssql_cursor.fetchone()
    resolved = row[0] if row is not None else None
    cache[v2_campaign_id] = resolved
    return resolved


def resolve_collection_type_id(
    v2_collection_id: int,
    mssql_cursor: pyodbc.Cursor,
    cache: Dict[int, Optional[int]],
) -> Optional[int]:
    """Resolve a v2 collection_id to its v3 CollectionTypeID, or None if unmapped."""
    if v2_collection_id in cache:
        return cache[v2_collection_id]
    query = (
        f"SELECT {COLLECTION_TYPE_MAP_V3_COLUMN} FROM {COLLECTION_TYPE_MAP_TABLE} "
        f"WHERE {COLLECTION_TYPE_MAP_V2_COLUMN} = ?"
    )
    mssql_cursor.execute(query, (v2_collection_id,))
    row = mssql_cursor.fetchone()
    resolved = row[0] if row is not None else None
    cache[v2_collection_id] = resolved
    return resolved
