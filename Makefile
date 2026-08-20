VENV    := .venv
PYTHON  ?= $(VENV)/bin/python
CONFIG  ?= config.json
LIMIT   ?= 3

.PHONY: install dry-run run dry-run-limit run-limit retry-failed retry-failed-dry-run clean

$(VENV)/bin/python:
	python3 -m venv $(VENV)

# Creates ./.venv on first run (macOS system Python refuses global pip installs), then
# installs requirements into it. All other targets run through $(VENV)/bin/python.
install: $(VENV)/bin/python
	$(PYTHON) -m pip install -r requirements.txt

# Rehearse a full run: pipeline runs end-to-end, CSVs are written, nothing is committed to v3.
dry-run:
	$(PYTHON) main.py --dry-run --config $(CONFIG)

# Real run: commits to v3, advances the checkpoint. Safe to re-run — resumes where it left off.
run:
	$(PYTHON) main.py --config $(CONFIG)

# Fully safe smoke test: LIMIT rows (default 3), dry-run. Override e.g. `make dry-run-limit LIMIT=10`.
dry-run-limit:
	$(PYTHON) main.py --dry-run --limit $(LIMIT) --config $(CONFIG)

# Real run capped at LIMIT rows (default 3): commits for real, checkpoint advances normally.
# Override e.g. `make run-limit LIMIT=10`.
run-limit:
	$(PYTHON) main.py --limit $(LIMIT) --config $(CONFIG)

# Rehearse reprocessing migrated_failed.csv without committing.
retry-failed-dry-run:
	$(PYTHON) main.py --retry-failed --dry-run --config $(CONFIG)

# Reprocess only the v2 ids in migrated_failed.csv for real.
retry-failed:
	$(PYTHON) main.py --retry-failed --config $(CONFIG)

# Remove generated output (CSVs, checkpoint, log) — does not touch config.json or .venv.
clean:
	rm -rf output migration_checkpoint.json migration.log
