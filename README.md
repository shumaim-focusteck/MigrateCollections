# MigrateCollections

Migrates collection/file data from the legacy MySQL v2 table
`kiosks_collections` into the SQL Server v3 table `CampaignArtCollections`.
Each v2 file URL is downloaded and re-hosted in Azure Blob Storage; the v3
row is written with the resulting blob URL, not the original v2 URL.

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
  blob_storage.py                   downloads each v2 file URL and re-hosts it in Azure Blob Storage
  resolvers.py                      v2 -> v3 ID resolution — adapt these too
  transform.py                       parses the v2 `files` JSON column
  processor.py                        per-row insert/update/skip decision logic
  batch_runner.py                      transactions, batching, checkpointing, retry
```

## Setup

0. Install the system ODBC libraries `pyodbc` needs to talk to SQL Server —
   these are OS packages, not something `requirements.txt`/`pip` can install:
   - **macOS**: `brew install unixodbc`, then
     `brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release`
     and `ACCEPT_EULA=Y brew install msodbcsql18` (Homebrew will ask you to
     run `brew trust microsoft/mssql-release` first if the tap isn't trusted yet).
   - **Ubuntu**:
     ```
     sudo apt-get update
     sudo apt-get install -y python3-venv python3-pip build-essential curl

     # Registers Microsoft's apt repo for this Ubuntu version, then installs
     # the SQL Server ODBC driver + the ODBC dev headers pyodbc compiles against
     curl -sSL -O https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb
     sudo dpkg -i packages-microsoft-prod.deb
     rm packages-microsoft-prod.deb
     sudo apt-get update
     sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
     ```
   - `make install` only installs the Python packages (`pymysql`, `pyodbc`,
     `azure-storage-blob`, `requests`) into `.venv` — it does not and cannot
     install the above, so this step is one-time, per machine, before
     `make install`.
1. `make install` (or `pip install -r requirements.txt` directly) — creates
   `.venv` and installs `pymysql`/`pyodbc` into it.
2. Copy `config.example.json` to `config.json` and fill in your connection
   details:
   - `mysql.host`, `mysql.port`, `mysql.user`, `mysql.password`, `mysql.database`
   - `mssql.conn_str` — a full pyodbc connection string, e.g.
     `DRIVER={ODBC Driver 18 for SQL Server};SERVER=host;DATABASE=db;UID=user;PWD=pass;TrustServerCertificate=yes`
   - `azure_blob.connection_string` — the storage account's connection string
     (Azure Portal → storage account → Access keys), e.g.
     `DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net`
   - `azure_blob.container_name` — target container; created automatically on
     first real (non-dry-run) use if it doesn't already exist
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
3. `resolve_campaign_id()` / `resolve_collection_type_id()` in
   [migrate_collections/resolvers.py](migrate_collections/resolvers.py)
   currently pass v2 ids straight through, since v2 campaign_id/collection_id
   and v3 CampaignID/CollectionTypeID are the same numeric space in this
   deployment. If that's ever not true for another environment, rewrite
   those two functions (e.g. to look up a mapping table).

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

## Makefile shortcuts

```
make install               # pip install -r requirements.txt
make dry-run                # python main.py --dry-run
make run                    # python main.py
make dry-run-limit          # python main.py --dry-run --limit 3 (override with LIMIT=N)
make run-limit               # python main.py --limit 3 (override with LIMIT=N)
make retry-failed-dry-run   # python main.py --retry-failed --dry-run
make retry-failed           # python main.py --retry-failed
make clean                  # remove output/, migration_checkpoint.json, migration.log
```

Override `CONFIG=path/to/file.json` on any target to point at a non-default config file, and
`LIMIT=N` on `dry-run-limit`/`run-limit` to change the row cap (default 3).

## Resuming

Progress is checkpointed to `migration_checkpoint.json` (path configurable via
`tuning.checkpoint_file` in `config.json`) after every committed batch. Just
re-run `python main.py` with no extra flags and it picks up from
`last_v2_id + 1`. Ctrl+C finishes the in-flight batch, saves the checkpoint,
and exits cleanly.

## Retrying failures

`output/migrated_failed.csv` is the one output file that stays a fixed name
and persists across runs (see "Output" below) — it accumulates rows that
failed for any reason. Run with `--retry-failed` to reprocess only those v2
ids; the file is rewritten to contain only whatever still fails after the
retry. Rows that succeed on retry go to that run's own
`migrated_success_<timestamp>.csv` as usual.

## File re-hosting

Every v2 file URL that would be written to a v3 column is first downloaded
and uploaded to Azure Blob Storage (`blob_storage.py`); the v3 column gets
the resulting blob URL, never the original v2 URL. Each file's blob path is
deterministic — `{campaign_id_v3}/{v3_column}/{filename}` — so if that blob
already exists (e.g. from an earlier run interrupted before its batch
committed to v3), it's reused instead of re-downloaded/re-uploaded. Every
row for the same campaign shares that campaign's folder, with one
subfolder per file type. For a brand-new v3 row, the row is inserted bare
(match-key columns only) first to obtain a `v3_id` for the later UPDATE,
then files are uploaded (keyed on `campaign_id_v3`, so this works even in
`--dry-run` where no real `v3_id` exists yet), then the row is updated with
the resulting blob URLs; if upload fails, that bare insert is deleted
before the failure is recorded, so v3 never ends up with an orphaned,
file-less row. `--dry-run` skips all blob reads/writes entirely (including container
creation), same as it skips v3 writes.

## Output

Three CSVs under `tuning.output_dir`, flushed after every batch so they stay
accurate even if the process is killed:
- `migrated_success_<run_id>.csv` and `migrated_skipped_<run_id>.csv` — a
  fresh, uniquely-named file every run (`<run_id>` is that run's start time,
  e.g. `20260820_181241`), so nothing from a previous run is ever
  overwritten. `migration.log` gets the same per-run treatment
  (`migration_<run_id>.log`, next to `config.json`).
- `migrated_failed.csv` — the one exception, a fixed name that persists and
  is rewritten across runs; see "Retrying failures" above for why.

## Column-level idempotency

A v3 file column is written if its current value doesn't already match the
deterministic blob URL that file would get (see "File re-hosting" above) —
covering both an empty column and one holding some other/stale URL (e.g.
left over from before a blob-path scheme change). A column already holding
exactly that URL is left untouched and logged to the skipped CSV with reason
`already_updated` instead. `--dry-run` can't compute a real candidate URL
without a live Azure connection, so its preview falls back to a simpler
empty-vs-non-empty check — this only affects what the preview *shows*,
never what a real run actually does.