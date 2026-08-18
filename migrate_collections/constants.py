"""Schema/mapping constants.

These are the values you're most likely to need to change for your actual
databases — grouped here so the rest of the code never hardcodes a table or
column name inline.
"""

from typing import Dict, Tuple

# Columns used to look up an existing v3 row (one row per campaign + collection type).
V3_MATCH_KEY_COLUMNS: Tuple[str, str] = ("CampaignID", "CollectionTypeID")

# v2 `files` JSON key -> v3 CampaignCollections column.
FILE_COLUMN_MAP: Dict[str, str] = {
    "top_file": "TopFileUrl",
    "bottom_file": "BottomFileUrl",
    "right_file": "RightSideFileUrl",
    "left_file": "LeftSideFileUrl",
}

V3_TABLE = "CampaignCollections"
V3_ID_COLUMN = "ID"

# Mapping table read by resolvers.resolve_campaign_id().
CAMPAIGN_MAP_TABLE = "CampaignIDMap"
CAMPAIGN_MAP_V2_COLUMN = "V2CampaignID"
CAMPAIGN_MAP_V3_COLUMN = "V3CampaignID"

# Mapping table read by resolvers.resolve_collection_type_id().
COLLECTION_TYPE_MAP_TABLE = "CollectionTypeIDMap"
COLLECTION_TYPE_MAP_V2_COLUMN = "V2CollectionID"
COLLECTION_TYPE_MAP_V3_COLUMN = "CollectionTypeID"

SOURCE_TABLE = "kiosks_collections"

SUCCESS_CSV = "migrated_success.csv"
FAILED_CSV = "migrated_failed.csv"
SKIPPED_CSV = "migrated_skipped.csv"

SUCCESS_HEADER = [
    "v2_id", "v3_id", "campaign_id_v2", "campaign_id_v3",
    "collection_type_id", "action", "columns_written", "timestamp",
]
FAILED_HEADER = [
    "v2_id", "campaign_id_v2", "collection_id_v2", "reason",
    "error_message", "timestamp",
]
SKIPPED_HEADER = [
    "v2_id", "campaign_id_v3", "collection_type_id", "reason", "timestamp",
]
