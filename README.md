# MigrateCollections

Migrates collection/file data from the legacy MySQL v2 table
`kiosks_collections` into the SQL Server v3 table `CampaignCollections`.

## Layout

```
main.py                       orchestrator entrypoint (CLI args, wiring, logging)
migrate_collections/
  config.py                   Config + load_config() (reads config.json)
  constants.py                table/column names and mapping constants — adapt these first
  models.py                   Stats, RowResult dataclasses
  csv_logger.py                append-friendly CSV writer for the 3 output files
  checkpoint.py                 resume-from-last-id checkpoint file
  shutdown.py                    graceful Ctrl+C handling
  mysql_source.py                 v2 reads (connect, fetch_batch, fetch_by_ids)
  mssql_dest.py                    v3 reads/writes (connect, fetch/insert/update)
  resolvers.py                      v2 -> v3 ID resolution — adapt these too
  transform.py                       parses the v2 `files` JSON column
  processor.py                        per-row insert/update/skip decision logic
  batch_runner.py                      transactions, batching, checkpointing, retry
```

## Setup

1. `pip install -r requirements.txt`
2. Copy `config.example.json` to `config.json` and fill in your connection
   details:
   - `mysql.host`, `mysql.port`, `mysql.user`, `mysql.password`, `mysql.database`
   - `mssql.conn_str` — a full pyodbc connection string, e.g.
     `DRIVER={ODBC Driver 18 for SQL Server};SERVER=host;DATABASE=db;UID=user;PWD=pass;TrustServerCertificate=yes`
   - `tuning.batch_size` (optional, default 500)
   - `tuning.output_dir` (optional, default `output`)
   - `tuning.checkpoint_file` (optional, default `migration_checkpoint.json`)
   - `tuning.log_file` (optional, default `migration.log`)
   - `filters.campaign_id_min` / `filters.campaign_id_max` (optional) — restrict
     the migration to v2 rows whose `campaign_id` falls in this inclusive
     range (e.g. `1` to `14491`). Omit either bound, or the whole `filters`
     section, to leave that side unbounded.

   `config.json` holds real credentials and is git-ignored; only
   `config.example.json` should be committed. Use `--config path/to/file.json`
   to point at a different config file (e.g. for a second environment).
3. Before running for real, populate the campaign/collection-type mapping
   tables referenced by `resolve_campaign_id()` / `resolve_collection_type_id()`
   in [migrate_collections/resolvers.py](migrate_collections/resolvers.py) —
   or rewrite those two functions to match however you actually map v2 IDs
   to v3 IDs. The table/column names they use are constants at the top of
   [migrate_collections/constants.py](migrate_collections/constants.py).

## Running

```
python main.py                       # normal run
python main.py --dry-run             # rehearse: logs + CSVs written, nothing committed to v3
python main.py --retry-failed        # reprocess only the v2 ids in migrated_failed.csv
python main.py --limit 2             # process at most 2 source rows, then stop
python main.py --dry-run --limit 2   # smoke test: 2 rows, nothing committed
```

`--limit N` caps how many source rows a run touches — useful for a quick
smoke test against real data before letting a run process the whole table.
It composes with `--dry-run` (fully safe test) and with a plain run (writes
2 rows for real, checkpoint advances normally, re-run picks up where it left
off). It also works with `--retry-failed`, capping how many failed ids are
retried.

## Resuming

Progress is checkpointed to `migration_checkpoint.json` (path configurable via
`tuning.checkpoint_file` in `config.json`) after every committed batch. Just
re-run `python main.py` with no extra flags and it picks up from
`last_v2_id + 1`. Ctrl+C finishes the in-flight batch, saves the checkpoint,
and exits cleanly.

## Retrying failures

`output/migrated_failed.csv` accumulates rows that failed for any reason. Run
with `--retry-failed` to reprocess only those v2 ids; the file is rewritten to
contain only whatever still fails after the retry. Rows that succeed on retry
are appended to `migrated_success.csv` as usual.

## Output

Three CSVs under `tuning.output_dir` (`migrated_success.csv`, `migrated_failed.csv`,
`migrated_skipped.csv`) are appended to as the run progresses and flushed
after every batch, so they stay accurate even if the process is killed.

## Known gap

v3's `CampaignCollections` has a `CollectionName` column that nothing in v2
provides — `kiosks_collections` has no name field. Inserts currently leave it
untouched (relies on it being nullable or having a default). If it's
`NOT NULL` with no default, inserts will fail until this is addressed.
