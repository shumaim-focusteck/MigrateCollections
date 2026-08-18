"""Configuration loaded from a JSON config file (default: config.json)."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CONFIG_PATH = Path("config.json")


@dataclass
class Config:
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    mssql_conn_str: str
    batch_size: int
    output_dir: Path
    checkpoint_file: Path
    log_file: Path
    # Optional v2 campaign_id range to migrate (inclusive). None = unbounded.
    campaign_id_min: Optional[int]
    campaign_id_max: Optional[int]


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Read connection strings and tuning values from a JSON config file.

    See config.example.json for the expected shape.
    """
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            f"Copy config.example.json to {config_path} and fill in your values."
        )

    data: Dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
    mysql = data.get("mysql", {})
    mssql = data.get("mssql", {})
    tuning = data.get("tuning", {})
    filters = data.get("filters", {})

    campaign_id_min = filters.get("campaign_id_min")
    campaign_id_max = filters.get("campaign_id_max")

    return Config(
        mysql_host=mysql["host"],
        mysql_port=int(mysql.get("port", 3306)),
        mysql_user=mysql["user"],
        mysql_password=mysql["password"],
        mysql_database=mysql["database"],
        mssql_conn_str=mssql["conn_str"],
        batch_size=int(tuning.get("batch_size", 500)),
        output_dir=Path(tuning.get("output_dir", "output")),
        checkpoint_file=Path(tuning.get("checkpoint_file", "migration_checkpoint.json")),
        log_file=Path(tuning.get("log_file", "migration.log")),
        campaign_id_min=int(campaign_id_min) if campaign_id_min is not None else None,
        campaign_id_max=int(campaign_id_max) if campaign_id_max is not None else None,
    )
