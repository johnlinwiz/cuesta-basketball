"""Browser-test harness for docs/index.html.

* docs/ is served from a local thread (http.server); events.json is replaced by one generated
  with scripts/build.py from the frozen data in tests/e2e/data/ (made-up results included).
* Every request that leaves 127.0.0.1 is intercepted: Leaflet/FullCalendar come from
  tests/e2e/vendor/ (byte-identical to the CDN files, so SRI still passes), OSM tiles get a
  blank PNG, Nominatim/OSRM get canned answers. Anything else is aborted and fails the test.
* The clock is pinned to FROZEN_NOW, after the first three (fake-scored) games.
"""
import base64
import functools
import http.server
import json
import pathlib
import shutil
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
VENDOR = HERE / "vendor"
FROZEN_NOW = "2026-11-05T12:00:00-08:00"

CDN = {
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js": ("leaflet.js", "application/javascript"),
    "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css": ("leaflet.css", "text/css"),
    "https://cdn.jsdelivr.net/npm/temporal-polyfill@1.0.8/global.js": ("temporal-polyfill.global.js", "application/javascript"),
    "https://cdn.jsdelivr.net/npm/fullcalendar@7.1.4/all/global.js": ("fullcalendar7.all.global.js", "application/javascript"),
    "https://cdn.jsdelivr.net/npm/fullcalendar@7.1.4/themes/classic/global.js": ("fullcalendar7.classic.global.js", "application/javascript"),
    "https://cdn.jsdelivr.net/npm/fullcalendar@7.1.4/skeleton.css": ("fullcalendar7.skeleton.css", "text/css"),
    "https://cdn.jsdelivr.net/npm/fullcalendar@7.1.4/themes/classic/theme.css": ("fullcalendar7.classic.theme.css", "text/css"),
    "https://cdn.jsdelivr.net/npm/fullcalendar@7.1.4/themes/classic/palette.css": ("fullcalendar7.classic.palette.css", "text/css"),
}
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")

# Address lookups the fake Nominatim knows about.
PLACES = {
    "fresno, ca": {"lat": "36.7378", "lon": "-119.7871",
                   "display_name": "Fresno, Fresno County, California, United States"},
}

MOBILE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True,
              user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Mobile Safari/537.36")
DESKTOP = dict(viewport={"width": 1280, "height": 900})


def osrm_seconds(i):
    """Fake drive time to the i-th venue (0-based, in events.json venue order)."""
    return 1800 + 900 * i


def osrm_meters(i):
    return 40000 + 16093.44 * i


def _line(name, no, starter, mins, pts, reb=0, ast=0, fgm=0, fga=0):
    return {"name": name, "id": None, "no": no, "starter": starter, "min": mins, "fgm": fgm, "fga": fga, "fg3m": 0, "fg3a": 0,
            "ftm": 0, "fta": 0, "oreb": 0, "dreb": reb, "reb": reb, "ast": ast, "stl": 0, "blk": 0, "to": 0, "pf": 0, "pts": pts}


@pytest.fixture(scope="session")
def stats_json():
    """docs/stats.json for the e2e games: two box scores (the 2nd without minutes) + a roster."""
    import fetch_stats
    games = [fetch_stats.normalize_game(g) for g in (
        {"event": "2026-10-29-gavilan", "date": "2026-10-29", "opponent": "Gavilan", "boxscore": "https://example.test/box/gavilan",
         "lines": [_line("Ann Star", "2", True, 30, 20, 4, 3, 8, 15), _line("Cy Guard", "10", True, 25, 12, 6, 1, 5, 9),
                   _line("Bo Bench", "5", False, 0, 0)]},
        {"event": "2026-10-31-sequoias", "date": "2026-10-31", "opponent": "Sequoias", "boxscore": "https://example.test/box/sequoias",
         "lines": [_line("Ann Star", "2", True, 1, 30, 2, 5, 11, 20), _line("Cy Guard", "10", True, 1, 5, 9, 0, 2, 7)]},
    )]
    roster = [{"no": n, "name": nm, "pos": "Guard", "cl": "Sophomore", "ht": "6-1", "home": "Pasadena, CA / Muir"}
              for n, nm in (("2", "Ann Star"), ("10", "Cy Guard"), ("5", "Bo Bench"), ("21", "Di Rookie"))]
    return {"team": "Cuesta College Men's Basketball", "short": "MBB", "key": None, "roster": roster,
            "season": fetch_stats.season_totals(games, set()), "games": games}


@pytest.fixture(scope="session")
def events_json(tmp_path_factory, stats_json):
    """docs/events.json as scripts/build.py would write it for the frozen e2e data."""
    import build
    import common
    root = tmp_path_factory.mktemp("e2e-root")
    shutil.copytree(HERE / "data", root / "data")
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(common, "ROOT", root)
        mp.setattr(build, "ROOT", root)
        sched, venues_cfg, _, events = common.load()
        issues = {e.id: n for n, e in enumerate(events, start=1)}
        boxes = {g["event"]: {"top": build.box_summary(g["lines"]), "url": g["boxscore"]} for g in stats_json["games"]}
        return json.loads(build.build_events_json(sched, venues_cfg, events, issues, boxes=boxes))
    finally:
        mp.undo()


@pytest.fixture(scope="session")
def site_url():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=str(REPO / "docs"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


class Net:
    """Fake outside world. Records what the page asked for."""

    def __init__(self, events_json, stats_json=None):
        self.events_json, self.stats_json = events_json, stats_json
        self.venue_keys = list(events_json["venues"])
        self.requests, self.unexpected = [], []

    def handle(self, route):
        url = route.request.url
        u = urlsplit(url)            # not urlparse: it would cut OSRM's ';'-separated coordinates
        self.requests.append(url)
        if u.hostname == "127.0.0.1":
            if u.path.endswith("/events.json"):
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(self.events_json))
            if u.path.endswith("/stats.json"):        # never the real docs/*/stats.json
                if self.stats_json is None:
                    return route.fulfill(status=404, body="")
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(self.stats_json))
            return route.continue_()
        cors = {"Access-Control-Allow-Origin": "*"}
        if url in CDN:
            name, ctype = CDN[url]
            return route.fulfill(status=200, headers=cors, content_type=ctype, body=(VENDOR / name).read_bytes())
        if u.hostname == "tile.openstreetmap.org":
            return route.fulfill(status=200, headers=cors, content_type="image/png", body=PNG_1PX)
        if u.hostname == "nominatim.openstreetmap.org":
            q = parse_qs(u.query)
            if u.path == "/search":
                hit = PLACES.get(q["q"][0].strip().lower())
                return route.fulfill(status=200, headers=cors, json=[hit] if hit else [])
            return route.fulfill(status=200, headers=cors, json={"address": {"city": "Testville"}})
        if u.hostname == "router.project-osrm.org":
            if u.path.startswith("/table/"):
                n = u.path.rsplit("/", 1)[1].count(";")          # destinations after the source
                return route.fulfill(status=200, headers=cors, json={
                    "code": "Ok", "durations": [[0] + [osrm_seconds(i) for i in range(n)]],
                    "distances": [[0] + [osrm_meters(i) for i in range(n)]]})
            if u.path.startswith("/route/"):
                (a, b) = [tuple(map(float, p.split(","))) for p in u.path.rsplit("/", 1)[1].split(";")]
                return route.fulfill(status=200, headers=cors, json={"code": "Ok", "routes": [{
                    "duration": 5400, "distance": 160934.4,
                    "geometry": {"type": "LineString", "coordinates": [list(a), list(b)]}}]})
        self.unexpected.append(url)
        return route.abort()

    def drive(self, venue_key):
        """(minutes, miles) the page should show for a venue, mirroring index.html's rounding."""
        i = self.venue_keys.index(venue_key)
        return round(osrm_seconds(i) / 60), round(osrm_meters(i) / 1609.344)


@pytest.fixture
def make_page(browser, site_url, events_json, stats_json):
    """make_page(mobile=True, text_size=None, hash="", frozen=True) -> (page, net). Fails the test on errors.

    frozen=True pins the clock to FROZEN_NOW via Playwright's fake clock. That clock also fakes
    setTimeout, and errors thrown inside faked timer callbacks never reach "pageerror" - so tests
    that hunt for runtime errors must use frozen=False (real timers)."""
    contexts, nets = [], []

    def make(mobile=True, text_size=None, hash="", frozen=True, data=None, stats=True, init_script=None):
        ctx = browser.new_context(**(MOBILE if mobile else DESKTOP), timezone_id="America/Los_Angeles",
                                  locale="en-US", service_workers="block")
        contexts.append(ctx)
        net = Net(data or events_json, stats_json if stats is True else (stats or None)); nets.append(net)
        ctx.route("**/*", net.handle)
        if text_size is not None:
            ctx.add_init_script(f"localStorage.setItem('cuesta-mbb-textsize', {json.dumps(str(text_size))})")
        if init_script:
            ctx.add_init_script(init_script)
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda exc: page.errors.append(str(exc)))
        if frozen:
            page.clock.set_fixed_time(FROZEN_NOW)
        page.goto(site_url + hash)
        page.wait_for_function("document.querySelector('#next') && !/Loading/.test(document.querySelector('#next').textContent)")
        return page, net

    yield make
    for net in nets:
        assert not net.unexpected, f"page tried to reach un-faked hosts: {net.unexpected}"
    for ctx in contexts:
        for p in ctx.pages:
            assert not p.errors, f"JavaScript errors: {p.errors}"
        ctx.close()
