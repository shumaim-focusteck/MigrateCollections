"""All reads against the v2 MySQL `kiosks_collections` table."""

from typing import Any, Dict, List, Optional

import pymysql
import pymysql.cursors

from .config import Config
from .constants import SOURCE_TABLE

_SELECT_COLUMNS = "id, campaign_id, collection_id, files"


def connect_mysql(cfg: Config) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=cfg.mysql_host,
        port=cfg.mysql_port,
        user=cfg.mysql_user,
        password=cfg.mysql_password,
        database=cfg.mysql_database,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        autocommit=True,
    )


def fetch_batch(
    mysql_conn: pymysql.connections.Connection,
    after_id: int,
    batch_size: int,
    campaign_id_min: Optional[int] = None,
    campaign_id_max: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Next page of source rows, ordered by id, strictly after `after_id`.

    `campaign_id_min`/`campaign_id_max` (from config.json's `filters` section)
    restrict the migration to a specific v2 campaign_id range, inclusive.
    Either bound can be omitted to leave that side unbounded.
    """
    conditions = ["id > %s"]
    params: List[Any] = [after_id]
    if campaign_id_min is not None:
        conditions.append("campaign_id >= %s")
        params.append(campaign_id_min)
    if campaign_id_max is not None:
        conditions.append("campaign_id <= %s")
        params.append(campaign_id_max)
    params.append(batch_size)

    with mysql_conn.cursor() as cur:
        cur.execute(
            f"SELECT {_SELECT_COLUMNS} FROM {SOURCE_TABLE} "
            f"WHERE {' AND '.join(conditions)} ORDER BY id ASC LIMIT %s",
            params,
        )
        return list(cur.fetchall())


def fetch_by_ids(mysql_conn: pymysql.connections.Connection, ids: List[int]) -> List[Dict[str, Any]]:
    """Used by --retry-failed to re-read only the previously failed rows."""
    if not ids:
        return []
    placeholders = ", ".join(["%s"] * len(ids))
    with mysql_conn.cursor() as cur:
        cur.execute(
            f"SELECT {_SELECT_COLUMNS} FROM {SOURCE_TABLE} "
            f"WHERE id IN ({placeholders}) ORDER BY id ASC",
            ids,
        )
        return list(cur.fetchall())
