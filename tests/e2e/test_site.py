"""docs/index.html in a real (headless Chromium) browser, against faked network services."""
import hashlib
import base64
import json
import re

import pytest
from playwright.sync_api import expect

from conftest import CDN, REPO, VENDOR
from pagetools import fmt_dur, horizontal_overflow, pinch, text_size

VIEWS = {"table": "#view-tbl", "cal": "#view-cal", "map": "#view-map"}
BUTTONS = {"table": "#m-tbl", "cal": "#m-cal", "map": "#m-map"}


def choose_from(page, value):
    """Pick a Drive-from suggestion (geo / o:<preset> / v:<venue>) from the combobox list."""
    page.click("#i-addr")
    page.click(f"#from-list li[data-v='{value}']")


def shown_views(page):
    return [m for m, sel in VIEWS.items()
            if page.eval_on_selector(sel, "el => getComputedStyle(el).display !== 'none' && el.getClientRects().length > 0")]


# ---------------------------------------------------------------- views
@pytest.mark.parametrize("mobile", [True, False], ids=["phone", "desktop"])
def test_each_view_shows_alone(make_page, mobile):
    page, _ = make_page(mobile=mobile)
    assert shown_views(page) == ["table"]
    for mode in ("map", "cal", "table", "map"):
        page.click(BUTTONS[mode])
        assert shown_views(page) == [mode], f"after clicking {mode}"
        assert page.eval_on_selector(BUTTONS[mode], "b => b.classList.contains('on')")
    # the old bug: #view-map's display:grid beat [hidden], so the map sat under the other views
    page.click(BUTTONS["table"])
    assert page.eval_on_selector("#view-map", "el => el.hidden && getComputedStyle(el).display") == "none"


@pytest.mark.parametrize("hash, mode", [("#map", "map"), ("#calendar", "cal"), ("#cal", "cal"), ("", "table")])
def test_url_hash_picks_the_view(make_page, hash, mode):
    page, _ = make_page(mobile=False, hash=hash)
    assert shown_views(page) == [mode]
    if mode == "map":
        expect(page.locator("#map .leaflet-interactive").first).to_be_attached()
    if mode == "cal":
        expect(page.locator("#cal .cmbb-ev").first).to_be_visible()      # our eventClass hook (v7 has no .fc-event)


@pytest.mark.parametrize("mobile", [False, True])
def test_no_runtime_errors_switching_views(make_page, mobile):
    """Real timers (frozen=False): Playwright's fake clock swallows errors thrown in setTimeout
    callbacks, which hid 'CAL.updateSize is not a function' after the FullCalendar 7 upgrade."""
    page, _ = make_page(mobile=mobile, hash="#calendar", frozen=False)
    for btn in ("#m-tbl", "#m-map", "#m-cal", "#m-tbl", "#m-cal"):
        page.click(btn)
        page.wait_for_timeout(150)          # let deferred work (resize, map invalidate) run
    page.wait_for_timeout(300)
    assert page.errors == [], page.errors


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_calendar_theme_follows_os_and_uses_cuesta_green(make_page, scheme):
    """FullCalendar 7 puts data-color-scheme on #cal itself; our palette overrides must match it
    (a descendant selector silently left the buttons slate-grey)."""
    page, _ = make_page(mobile=False, hash="#calendar")
    page.emulate_media(color_scheme=scheme)
    page.reload()
    page.wait_for_selector("#cal .cmbb-ev")
    assert page.get_attribute("#cal", "data-color-scheme") == scheme
    bg = page.evaluate("getComputedStyle(document.querySelector('#cal button')).backgroundColor")
    assert bg == "rgb(11, 110, 58)", bg


# ---------------------------------------------------------------- phone layout
@pytest.mark.parametrize("size", [None, "130"], ids=["text-100", "text-130"])
@pytest.mark.parametrize("mode", ["table", "cal", "map"])
def test_nothing_scrolls_sideways_at_390px(make_page, mode, size):
    page, _ = make_page(mobile=True, text_size=size)
    assert text_size(page)[0] == ("130%" if size else "")
    page.click(BUTTONS[mode])
    page.wait_for_timeout(100)
    assert horizontal_overflow(page) == []
    choose_from(page, "o:pasadena")                            # drive columns get longer once an origin is set
    expect(page.locator("#from-status")).to_have_text("From Pasadena")
    page.wait_for_timeout(100)
    assert horizontal_overflow(page) == []


def test_details_dialog_fits_on_a_phone(make_page):
    page, _ = make_page(mobile=True, text_size="130")
    page.click("#tbl-b button.link >> nth=2")
    expect(page.locator("#dlg")).to_be_visible()
    box = page.locator("#dlg").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390


# ---------------------------------------------------------------- text size
def test_text_size_buttons(make_page):
    page, _ = make_page(mobile=True)
    assert text_size(page) == ["", None]
    page.click("#ts-up")
    assert text_size(page) == ["115%", "115"]
    expect(page.locator("#ts-badge")).to_have_text("Text 115%")
    page.click("#ts-up")
    assert text_size(page) == ["130%", "130"]
    expect(page.locator("#ts-up")).to_be_disabled()
    for _ in range(3):                                      # 130 -> 115 -> 100 -> 90
        page.click("#ts-down")
    assert text_size(page) == ["90%", "90"]
    expect(page.locator("#ts-down")).to_be_disabled()
    page.click("#ts-up")
    assert text_size(page) == ["", "100"]                   # 100% clears the inline style


def test_text_size_survives_reload(make_page):
    page, _ = make_page(mobile=True)
    page.click("#ts-up")
    page.reload()
    page.wait_for_selector("#tbl-b tr")
    assert text_size(page) == ["115%", "115"]
    expect(page.locator("#ts-up")).to_be_enabled()
    page.evaluate("localStorage.setItem('cuesta-mbb-textsize', 'xl')")    # value saved by an older version
    page.reload()
    page.wait_for_selector("#tbl-b tr")
    assert text_size(page)[0] == "130%"
    page.evaluate("localStorage.setItem('cuesta-mbb-textsize', 'garbage')")
    page.reload()
    page.wait_for_selector("#tbl-b tr")
    assert text_size(page)[0] == ""


def test_pinch_resizes_text_and_saves_where_fingers_stop(make_page):
    page, _ = make_page(mobile=True)
    # 200px -> 230px (115%), let it paint, then 260px (130%) and lift before the next frame.
    pinch(page, "#tbl-b", [200, 230, 260], frame_gaps=[0, 2, 0])
    assert text_size(page) == ["130%", "130"]             # not 115: the end of the pinch wins
    expect(page.locator("#ts-badge")).to_have_text("Text 130%")
    page.reload()
    page.wait_for_selector("#tbl-b tr")
    assert text_size(page) == ["130%", "130"]


def test_pinch_in_and_clamping(make_page):
    page, _ = make_page(mobile=True)
    pinch(page, "#next", [300, 240])                      # fingers together -> smaller
    assert text_size(page) == ["80%", "80"]
    pinch(page, "#next", [100, 400])                      # way out -> clamped at 160%
    assert text_size(page) == ["160%", "160"]
    page.click("#ts-down")                                # buttons step from wherever the pinch left it
    assert text_size(page) == ["130%", "130"]


def test_pinch_on_map_leaves_text_alone(make_page):
    page, _ = make_page(mobile=True, hash="#map")
    expect(page.locator("#map .leaflet-interactive").first).to_be_attached()
    pinch(page, "#map", [100, 250])
    assert text_size(page) == ["", None]
    pinch(page, "#map-side", [100, 130])                 # the venue list beside it is fair game
    assert text_size(page) == ["130%", "130"]


# ---------------------------------------------------------------- drive from
def test_drive_from_typed_address(make_page, events_json):
    page, net = make_page(mobile=False)
    page.fill("#i-addr", "Fresno, CA")
    page.press("#i-addr", "Enter")
    label = "Fresno, Fresno County"
    expect(page.locator("#from-status")).to_have_text(f"From {label}")
    assert any("nominatim.openstreetmap.org/search" in u and "Fresno" in u for u in net.requests)

    nxt = next(e for e in events_json["events"] if e["start"] >= "2026-11-05T12")
    assert nxt["id"] == "2026-11-07-diablo-valley"
    mins, miles = net.drive(nxt["extendedProps"]["venueKey"])

    # table: header + the next game's row
    expect(page.locator("#tbl-th-drive")).to_have_text(f"🚗 From {label}")
    row = page.locator("#tbl-b tr", has=page.locator(f"button[data-id='{nxt['id']}']"))
    expect(row.locator("td[data-label=Drive]")).to_have_text(f"{fmt_dur(mins)} · {miles} mi")
    # next-up line
    expect(page.locator("#next")).to_contain_text(f"From {label}: {fmt_dur(mins)} · {miles} mi")
    expect(page.locator("#next")).to_contain_text("leave by")
    saved = json.loads(page.evaluate("localStorage.getItem('cuesta-mbb-origin')"))
    assert (saved["label"], saved["lat"], saved["lon"]) == (label, 36.7378, -119.7871)

    # map: side list, origin marker, and a drawn route
    page.click("#m-map")
    venue = page.locator(f"#map-list .v[data-key={nxt['extendedProps']['venueKey']}]")
    expect(venue).to_contain_text(f"{fmt_dur(mins)} · {miles} mi")
    n_markers = len(events_json["venues"]) + 1
    expect(page.locator("#map path.leaflet-interactive")).to_have_count(n_markers)
    venue.click()
    page.click(".leaflet-popup button[data-route=me]")
    expect(page.locator("#route-info")).to_contain_text(f"{label} →")
    expect(page.locator("#route-info")).to_contain_text("1h 30m · 100 mi")

    # calendar view gets the upcoming-trips table
    page.click("#m-cal")
    expect(page.locator("#trips-h")).to_have_text(f"Upcoming games — drive from {label}")

    # remembered on reload, then cleared
    page.click("#m-tbl")
    page.reload()
    expect(page.locator("#from-status")).to_have_text(f"From {label}")
    page.click("#b-clear")
    expect(page.locator("#tbl-th-drive")).to_have_text("🚗 Pasadena · SLO")
    assert page.evaluate("localStorage.getItem('cuesta-mbb-origin')") is None


def test_drive_from_unknown_address(make_page):
    page, _ = make_page(mobile=True)
    page.fill("#i-addr", "Atlantis")
    page.press("#i-addr", "Enter")
    expect(page.locator("#from-status")).to_have_text("Couldn't find “Atlantis”.")
    expect(page.locator("#b-clear")).to_be_hidden()


# ---------------------------------------------------------------- results
def test_results_badges_and_record(make_page):
    page, _ = make_page(mobile=False)
    expect(page.locator("#next")).to_contain_text("Record: 2-1")      # the scrimmage isn't scored
    expect(page.locator("#next")).to_contain_text("Next up: Sat Nov 7 · 3:00 PM · vs Diablo Valley")
    badges = page.locator("#tbl-b .res")
    expect(badges).to_have_text(["L 68-75", "W 81-74 (OT)", "W (forfeit)"])
    expect(page.locator("#tbl-b .res.W")).to_have_count(2)
    expect(page.locator("#tbl-b .res.L")).to_have_count(1)
    gavilan = page.locator("#tbl-b tr", has=page.locator("button[data-id='2026-10-29-gavilan']"))
    expect(gavilan).to_have_class("past")
    expect(gavilan.locator("td[data-label=Watch] a")).to_have_text(["box"])


def test_details_dialog_shows_result(make_page):
    page, _ = make_page(mobile=False)
    page.click("button[data-id='2026-10-31-sequoias']")
    dlg = page.locator("#dlg")
    expect(dlg).to_be_visible()
    expect(page.locator("#d-title")).to_have_text("Cuesta vs Sequoias")
    expect(page.locator("#d-body .res.W")).to_have_text("W 81-74 (OT)")
    expect(page.locator("#d-body .res-links a")).to_have_text(["box", "recap"])
    expect(page.locator("#d-body a", has_text="recap")).to_have_attribute("href", "https://example.test/recap/sequoias")
    expect(page.locator("#d-foot a", has_text="Reminder issue")).to_have_attribute(
        "href", re.compile(r"github\.com/johnlinwiz/cuesta-basketball/issues/\d+"))
    page.click("#d-foot button")
    expect(dlg).to_be_hidden()


def test_calendar_titles_carry_results(make_page):
    page, _ = make_page(mobile=False, hash="#calendar")
    page.locator("#cal").get_by_role("button", name=re.compile(r"prev", re.I)).click()   # opens on November; go to October
    expect(page.locator("#cal .cmbb-ev", has_text="W 81-74 (OT)")).to_be_visible()


# ---------------------------------------------------------------- the vendored copies stay honest
@pytest.mark.parametrize("url", list(CDN))
def test_vendored_cdn_files_match_page_integrity(url):
    html = (REPO / "docs/index.html").read_text()
    m = re.search(rf'(?:src|href)="{re.escape(url)}" integrity="(sha384-[^"]+)"', html)
    assert m, f"{url} no longer referenced by docs/index.html; update tests/e2e/conftest.py CDN + vendor/"
    digest = "sha384-" + base64.b64encode(hashlib.sha384((VENDOR / CDN[url][0]).read_bytes()).digest()).decode()
    assert digest == m.group(1), f"re-download {url} into tests/e2e/vendor/"


# ---------------------------------------------------------------- two teams (Men / Women switch)
def two_team(events_json, key):
    d = dict(events_json, teamKey=key,
             teams=[{"key": "mbb", "short": "MBB", "team": "Cuesta College Men's Basketball"},
                    {"key": "wbb", "short": "WBB", "team": "Cuesta College Women's Basketball"},
                    {"key": "all", "short": "ALL", "team": "Both teams"}])
    if key == "wbb":
        d["team"] = "Cuesta College Women's Basketball"
    return d


def test_team_switch_shows_and_navigates(make_page, events_json):
    page, net = make_page(mobile=False, hash="#map", data=two_team(events_json, "mbb"))
    btns = page.locator("#teams button")
    expect(btns).to_have_text(["👨 Men", "👩 Women", "👥 Both"])
    expect(page.locator("#teams button.on")).to_have_text("👨 Men")
    assert "?team=mbb" in page.url and page.url.endswith("#map")        # URL made shareable, view kept
    net.events_json = two_team(events_json, "wbb")
    btns.nth(1).click()
    page.wait_for_url(re.compile(r"\?team=wbb#map$"))
    expect(page.locator("#teams button.on")).to_have_text("👩 Women")
    expect(page.locator("#h1")).to_contain_text("Women's")
    assert shown_views(page) == ["map"]
    # A bare link always opens Men's, even right after viewing Women's (no remembered team).
    net.requests.clear()
    net.events_json = two_team(events_json, "mbb")
    page.goto(page.url.split("?")[0])
    expect(page.locator("#teams button.on")).to_have_text("👨 Men")
    asked = [r for r in net.requests if r.split("?")[0].endswith("events.json")]
    assert asked and asked[0].split("?")[0].endswith("/mbb/events.json"), asked


def test_view_switch_keeps_team_in_url(make_page, events_json):
    page, _ = make_page(mobile=False, data=two_team(events_json, "wbb"))
    for btn, tail in (("#m-cal", "?team=wbb#calendar"), ("#m-map", "?team=wbb#map"), ("#m-tbl", "?team=wbb")):
        page.click(btn)
        assert page.url.endswith(tail), page.url


def test_single_team_data_has_no_switch(make_page):
    page, _ = make_page(mobile=False)
    expect(page.locator("#teams")).to_be_hidden()


def test_both_view_shows_every_team_and_links_all_issues(make_page, events_json):
    both = two_team(events_json, "all")
    both["team"] = "Cuesta College Basketball"
    both["record"] = "Men 1-2 · Women 0-2"
    both["events"] = [dict(e, title=("👩 " if i % 2 else "👨 ") + e["title"]) for i, e in enumerate(events_json["events"])]
    page, _ = make_page(mobile=False, data=both)
    expect(page.locator("#teams button.on")).to_have_text("👥 Both")
    assert "?team=all" in page.url
    expect(page.locator("#next")).to_contain_text("Men 1-2 · Women 0-2")
    titles = page.locator("#tbl-b button.link").all_text_contents()
    assert any(t.startswith("👨") for t in titles) and any(t.startswith("👩") for t in titles)
    assert "label%3A" not in page.get_attribute("#l-issues", "href")    # Both -> every team's reminders


# ---------------------------------------------------------------- drive from a venue; back-to-back legs; header
def test_drive_from_a_venue_picker(make_page, events_json):
    page, net = make_page(mobile=False)
    key, v = next(iter(events_json["venues"].items()))
    choose_from(page, f"v:{key}")
    expect(page.locator("#from-status")).to_contain_text("From ")
    o = json.loads(page.evaluate("localStorage.getItem('cuesta-mbb-origin')"))
    assert o["key"] == f"venue:{key}" and (o["lat"], o["lon"]) == (v["lat"], v["lon"])
    assert any("router.project-osrm.org/table" in u and f"{v['lon']:.5f},{v['lat']:.5f}" in u for u in net.requests)
    expect(page.locator("#tbl-th-drive")).to_contain_text("From ")
    page.click("#b-clear")
    expect(page.locator("#i-addr")).to_have_value("")


def test_drive_from_here_in_the_game_dialog(make_page, events_json):
    page, _ = make_page(mobile=False)
    ev = events_json["events"][3]
    page.click(f"button[data-id='{ev['id']}']")
    page.click("#d-from-here")
    expect(page.locator("#dlg")).to_be_hidden()
    expect(page.locator("#from-status")).to_contain_text("From ")        # drives computed, origin saved
    o = json.loads(page.evaluate("localStorage.getItem('cuesta-mbb-origin')"))
    assert o["key"] == "venue:" + ev["extendedProps"]["venueKey"]
    expect(page.locator("#i-addr")).to_have_class(re.compile(r"\bset\b"))
    expect(page.locator("#i-addr")).to_have_value(re.compile(re.escape(o["label"])))


def test_back_to_back_leg_shows_in_table_and_dialog(make_page, events_json):
    data = json.loads(json.dumps(events_json))
    a, b = data["events"][1], data["events"][2]
    b["extendedProps"]["fromPrev"] = {"title": a["title"], "dateLabel": "Fri Oct 23", "time": "2:00 PM",
                                      "venue": "Prev Gym", "city": "Saratoga, CA", "same": False, "sameDay": False,
                                      "minutes": 222, "miles": 189, "dir": "https://www.google.com/maps/dir/?api=1&x=1"}
    data["events"][3]["extendedProps"]["fromPrev"] = dict(b["extendedProps"]["fromPrev"], same=True, sameDay=True)
    page, _ = make_page(mobile=False, data=data)
    row = page.locator("#tbl-b tr", has=page.locator(f"button[data-id='{b['id']}']"))
    expect(row.locator(".leg")).to_contain_text("3h 42m · 189 mi from")
    same = page.locator("#tbl-b tr", has=page.locator(f"button[data-id='{data['events'][3]['id']}']"))
    expect(same.locator(".leg")).to_contain_text("Same gym as")
    page.click(f"button[data-id='{b['id']}']")
    expect(page.locator("#d-body")).to_contain_text("Back-to-back: 3h 42m · 189 mi from the previous game")


@pytest.mark.parametrize("mobile, max_rows", [(False, 1), (True, 2)])
def test_header_is_condensed(make_page, mobile, max_rows, events_json):
    page, _ = make_page(mobile=mobile, data=two_team(events_json, "mbb"))
    rows = page.evaluate("""() => new Set([...document.querySelectorAll('header > *')]
        .filter(el => el.offsetParent !== null || el.tagName === 'DETAILS')
        .map(el => Math.round(el.getBoundingClientRect().top / 8))).size""")
    assert rows <= max_rows, f"header uses {rows} rows"
    expect(page.locator("#l-ics")).to_be_hidden()                       # secondary links live in the ☰ menu
    page.click("#menu summary")
    expect(page.locator("#l-ics")).to_be_visible()
    expect(page.locator("#l-pdf")).to_have_attribute("href", "cuesta-mbb-schedule.pdf")
    expect(page.locator("#l-pdf-bw")).to_have_attribute("href", "cuesta-mbb-schedule-bw.pdf")
    page.keyboard.press("Escape")
    expect(page.locator("#l-ics")).to_be_hidden()


@pytest.mark.parametrize("width", [360, 390, 430, 768, 1280])
def test_header_does_not_move_when_the_team_title_changes(make_page, events_json, width):
    """Regression: the title's length (Men's / Women's / Both) used to reflow the phone header."""
    geo = []
    for key, name in (("mbb", "Cuesta College Men's Basketball"), ("wbb", "Cuesta College Women's Basketball"),
                      ("all", "Cuesta College Basketball")):
        page, _ = make_page(mobile=width < 641, data=dict(two_team(events_json, key), team=name))
        page.set_viewport_size({"width": width, "height": 800})
        page.wait_for_timeout(100)
        geo.append(page.evaluate("""() => [document.querySelector('header').getBoundingClientRect().height,
            ...['#teams', '.views', '.ts', '#menu'].map(s => { const r = document.querySelector(s).getBoundingClientRect();
                return [Math.round(r.x), Math.round(r.y), Math.round(r.width)]; })]"""))
        assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert geo[0] == geo[1] == geo[2], geo



def test_drive_from_is_one_compact_bar(make_page):
    """Phone: picker + ✕ + ⓘ on one row, address box on the next - nothing else."""
    page, _ = make_page(mobile=True)
    rows = page.evaluate("""() => new Set([...document.querySelectorAll('#from > *')].filter(e => e.offsetParent !== null)
        .map(e => Math.round(e.getBoundingClientRect().top / 10))).size""")
    assert rows <= 1, rows                                             # one row now: box + Go + ⓘ
    assert page.locator("#from").bounding_box()["height"] < 60
    expect(page.locator("#from-note")).to_be_hidden()
    page.click("#b-info"); expect(page.locator("#from-note")).to_be_visible()
    page.click("#i-addr")
    opts = page.locator("#from-list li[role=option]").all_text_contents()
    assert opts[0] == "📍 My location" and "Pasadena" in opts and "SLO" in opts and len(opts) > 6


def test_typed_place_shows_in_the_box_and_switching_replaces_it(make_page):
    page, _ = make_page(mobile=False)
    page.fill("#i-addr", "Fresno, CA"); page.press("#i-addr", "Enter")
    expect(page.locator("#i-addr")).to_have_value("Fresno, Fresno County")
    expect(page.locator("#i-addr")).to_have_class(re.compile(r"\bset\b"))
    choose_from(page, "o:slo")
    expect(page.locator("#i-addr")).to_have_value("SLO")
    page.click("#b-clear")
    expect(page.locator("#i-addr")).to_have_value("")


def test_combobox_filters_as_you_type_and_offers_a_lookup(make_page, events_json):
    page, net = make_page(mobile=False)
    key, v = next(iter(events_json["venues"].items()))
    word = v["name"].split()[0]
    page.click("#i-addr"); page.fill("#i-addr", word.lower())
    opts = page.locator("#from-list li[role=option]")
    labels = opts.all_text_contents()
    assert all(word.lower() in l.lower() for l in labels[:-1]), labels      # filtered (venue name or city)
    assert labels[-1].startswith("🔎 Look up")                             # free text is always an option
    page.keyboard.press("ArrowDown"); page.keyboard.press("Enter")          # keyboard picks the first match
    expect(page.locator("#from-status")).to_contain_text("From ")
    assert json.loads(page.evaluate("localStorage.getItem('cuesta-mbb-origin')"))["key"].startswith(("venue:", "pasadena", "slo"))


def test_combobox_escape_restores_the_current_choice(make_page):
    page, _ = make_page(mobile=False)
    choose_from(page, "o:pasadena")
    expect(page.locator("#i-addr")).to_have_value("Pasadena")
    page.click("#i-addr"); page.keyboard.type("xyz")
    page.keyboard.press("Escape")
    expect(page.locator("#from-list")).to_be_hidden()
    expect(page.locator("#i-addr")).to_have_value("Pasadena")


@pytest.mark.parametrize("mobile", [True, False])
def test_view_buttons_jump_to_the_view(make_page, mobile):
    page, _ = make_page(mobile=mobile, frozen=False)
    page.evaluate("window.scrollTo(0, 0)")
    hdr = page.locator("header").bounding_box()["height"]
    for btn, view in (("#m-cal", "#view-cal"), ("#m-map", "#view-map"), ("#m-tbl", "#view-tbl")):
        page.evaluate("window.scrollTo(0, 0)")
        page.click(btn)
        page.wait_for_function(f"Math.abs(document.querySelector('{view}').getBoundingClientRect().top - {hdr} - 8) < 4"
                               f" || document.documentElement.scrollHeight - innerHeight - scrollY < 2", timeout=4000)
        top = page.locator(view).bounding_box()["y"]
        assert top >= hdr - 1, f"{view} hidden under the sticky header ({top} < {hdr})"


def test_page_load_does_not_jump(make_page):
    page, _ = make_page(mobile=True, hash="#calendar", frozen=False)
    page.wait_for_timeout(400)
    assert page.evaluate("scrollY") == 0
