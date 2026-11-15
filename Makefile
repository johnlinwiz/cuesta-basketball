# Uses the johnlinwiz gh account regardless of which account is active globally.
GH_TOKEN ?= $(shell gh auth token --user johnlinwiz 2>/dev/null)
export GH_TOKEN

# Everything uses .venv if it exists (make dev / make dev-uv create it); otherwise whatever python3 is.
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
# Playwright's own image, pinned to the playwright version in requirements-dev.txt (browsers included).
PW_IMAGE ?= mcr.microsoft.com/playwright/python:v$(shell sed -n 's/^playwright==//p' requirements-dev.txt)-noble
PYBOOT ?= $(shell command -v python3.12 || command -v python3.13 || command -v python3.14 || echo python3)

.PHONY: build drive issues feed results poster daily serve all dev dev-uv test e2e e2e-docker test-all
build:            ## regenerate README, CALENDAR.md, venues.geojson, docs/
	$(PY) scripts/build.py
drive:            ## recompute drive times (OSRM) after editing venues
	$(PY) scripts/drive_times.py
issues:           ## create/update one reminder issue per game, then rebuild links
	$(PY) scripts/sync_issues.py && $(PY) scripts/build.py
feed:             ## compare the official 3C2A feed with schedule.yaml (prints diffs)
	$(PY) scripts/check_feed.py
results:          ## scrape final scores into each team's results.json, then rebuild
	$(PY) scripts/fetch_results.py && $(PY) scripts/build.py
poster:           ## one-page printable PDFs per team (colour + black & white) -> docs/cuesta-<team>-schedule[-bw].pdf
	cd scripts && for t in $$(ls ../data/teams); do ../$(PY) poster.py $$t && ../$(PY) poster.py $$t --bw; done
daily:            ## run the reminder/close job locally
	$(PY) scripts/daily.py
serve:            ## preview the Pages site at http://127.0.0.1:8000 (loopback only)
	cd docs && ../$(PY) -m http.server --bind 127.0.0.1 8000
all: drive issues
dev:              ## create .venv with the test tools + headless Chromium
	$(PYBOOT) -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/python -m playwright install chromium
dev-uv:           ## Linux hosts with an old system Python (e.g. cd2, Python 3.7): .venv with Python 3.12 via uv (no browsers)
	uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python -r requirements-dev.txt
test:             ## unit tests (offline, saved 2025-26 pages)
	$(PY) -m pytest tests/unit -q
e2e:              ## browser tests of docs/index.html (headless Chromium, network faked)
	$(PY) -m pytest tests/e2e -q
e2e-docker:       ## browser tests in Playwright's Docker image (hosts whose libraries are too old for its browsers)
	docker run --rm --user $$(id -u):$$(id -g) -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 -v "$(CURDIR)":/w -w /w $(PW_IMAGE) \
	  sh -c 'pip install -q --user --no-warn-script-location -r requirements-dev.txt && python -m pytest tests/e2e -q -p no:cacheprovider'
test-all: test e2e
