"""Schema/mapping constants.

These are the values you're most likely to need to change for your actual
databases — grouped here so the rest of the code never hardcodes a table or
column name inline.
"""

from typing import Dict, Tuple

# Columns used to look up an existing v3 row (one row per campaign + collection type).
V3_MATCH_KEY_COLUMNS: Tuple[str, str] = ("CampaignID", "CollectionTypeID")

# v2 `files` JSON key -> v3 CampaignArtCollections column.
FILE_COLUMN_MAP: Dict[str, str] = {
    "top_file": "TopFileUrl",
    "bottom_file": "BottomFileUrl",
    "right_file": "RightSideFileUrl",
    "left_file": "LeftSideFileUrl",
}

V3_TABLE = "CampaignArtCollections"
V3_ID_COLUMN = "ID"

# NOT NULL v3 columns with nothing in v2 to source them from; every bare
# insert sets them to these fixed values. CreatedDate/IsActive aren't listed
# here because the table already defaults those (getdate() / 1).
V3_FIXED_INSERT_VALUES: Dict[str, object] = {
    "CollectionName": "",
    "CreatedUserID": 0,
}

SOURCE_TABLE = "kiosks_collections"

SUCCESS_CSV = "migrated_success.csv"
FAILED_CSV = "migrated_failed.csv"
SKIPPED_CSV = "migrated_skipped.csv"

# One source/target column pair per FILE_COLUMN_MAP entry, named after the
# exact v2 JSON key and exact v3 column — e.g. "top_file", "TopFileUrl".
_FILE_URL_COLUMNS = [name for pair in FILE_COLUMN_MAP.items() for name in pair]

SUCCESS_HEADER = [
    "v2_id", "v3_id", "campaign_id_v2", "campaign_id_v3",
    "collection_type_id", "action",
] + _FILE_URL_COLUMNS + ["timestamp"]
FAILED_HEADER = [
    "v2_id", "campaign_id_v2", "campaign_id_v3", "collection_id_v2", "reason",
] + _FILE_URL_COLUMNS + ["error_message", "timestamp"]
SKIPPED_HEADER = [
    "v2_id", "campaign_id_v3", "collection_type_id", "reason",
] + _FILE_URL_COLUMNS + ["timestamp"]
