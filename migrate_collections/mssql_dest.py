"""All reads/writes against the v3 SQL Server `CampaignCollections` table."""

from typing import Any, Dict, List, Optional, Tuple

import pyodbc

from .config import Config
from .constants import FILE_COLUMN_MAP, V3_ID_COLUMN, V3_MATCH_KEY_COLUMNS, V3_TABLE


def connect_mssql(cfg: Config) -> pyodbc.Connection:
    conn = pyodbc.connect(cfg.mssql_conn_str)
    conn.autocommit = False
    return conn


def fetch_existing_v3_row(
    cursor: pyodbc.Cursor, campaign_id: int, collection_type_id: int
) -> Optional[Dict[str, Any]]:
    """Look up an existing row by the (CampaignID, CollectionTypeID) match key."""
    key_a, key_b = V3_MATCH_KEY_COLUMNS
    columns = [V3_ID_COLUMN] + list(FILE_COLUMN_MAP.values())
    query = f"SELECT {', '.join(columns)} FROM {V3_TABLE} WHERE {key_a} = ? AND {key_b} = ?"
    cursor.execute(query, (campaign_id, collection_type_id))
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip(columns, row))


def insert_v3_row(
    cursor: pyodbc.Cursor,
    campaign_id: int,
    collection_type_id: int,
    file_columns: Dict[str, str],
    dry_run: bool,
) -> Tuple[Optional[int], List[str]]:
    """Insert a new row with whatever file URLs are available. Returns the
    new v3 id (None in dry-run mode) and the list of columns written."""
    key_a, key_b = V3_MATCH_KEY_COLUMNS
    columns = [key_a, key_b] + list(file_columns.keys())
    values = [campaign_id, collection_type_id] + list(file_columns.values())
    placeholders = ", ".join("?" for _ in columns)
    query = (
        f"INSERT INTO {V3_TABLE} ({', '.join(columns)}) "
        f"OUTPUT INSERTED.{V3_ID_COLUMN} VALUES ({placeholders})"
    )
    if dry_run:
        return None, list(file_columns.keys())
    cursor.execute(query, values)
    new_id = cursor.fetchone()[0]
    return new_id, list(file_columns.keys())


def update_v3_row(
    cursor: pyodbc.Cursor, v3_id: int, columns_to_fill: Dict[str, str], dry_run: bool
) -> None:
    """Fill only the given (currently-empty) columns. Caller guarantees
    `columns_to_fill` is non-empty and excludes anything already populated."""
    set_clause = ", ".join(f"{col} = ?" for col in columns_to_fill)
    values = list(columns_to_fill.values()) + [v3_id]
    query = f"UPDATE {V3_TABLE} SET {set_clause} WHERE {V3_ID_COLUMN} = ?"
    if dry_run:
        return
    cursor.execute(query, values)
