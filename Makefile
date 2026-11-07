# Uses the johnlinwiz gh account regardless of which account is active globally.
GH_TOKEN ?= $(shell gh auth token --user johnlinwiz 2>/dev/null)
export GH_TOKEN

# Tests use .venv if it exists (make dev creates it); otherwise whatever python3 is.
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PYBOOT ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3.14 || echo python3)

.PHONY: build drive issues feed results poster daily serve all dev test e2e test-all
build:            ## regenerate README, CALENDAR.md, venues.geojson, docs/
	python3 scripts/build.py
drive:            ## recompute drive times (OSRM) after editing venues
	python3 scripts/drive_times.py
issues:           ## create/update one reminder issue per game, then rebuild links
	python3 scripts/sync_issues.py && python3 scripts/build.py
feed:             ## compare the official 3C2A feed with schedule.yaml (prints diffs)
	python3 scripts/check_feed.py
results:          ## scrape final scores into each team's results.json, then rebuild
	python3 scripts/fetch_results.py && python3 scripts/build.py
poster:           ## one-page printable PDF per team -> docs/cuesta-<team>-schedule.pdf
	cd scripts && for t in $$(ls ../data/teams); do ../$(PY) poster.py $$t; done
daily:            ## run the reminder/close job locally
	python3 scripts/daily.py
serve:            ## preview the Pages site at http://127.0.0.1:8000 (loopback only)
	cd docs && python3 -m http.server --bind 127.0.0.1 8000
all: drive issues
dev:              ## create .venv with the test tools + headless Chromium
	$(PYBOOT) -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/python -m playwright install chromium
test:             ## unit tests (offline, saved 2025-26 pages)
	$(PY) -m pytest tests/unit -q
e2e:              ## browser tests of docs/index.html (headless Chromium, network faked)
	$(PY) -m pytest tests/e2e -q
test-all: test e2e
