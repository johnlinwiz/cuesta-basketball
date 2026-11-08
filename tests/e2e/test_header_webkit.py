"""Header on Safari's engine (WebKit) with iPhone profiles.

The rest of the browser suite runs Chromium; this file exists because a header bug ("Both" sliding
under the view icons) only reproduced in WebKit. Skipped if WebKit isn't installed locally.
"""
import json

import pytest
from playwright.sync_api import sync_playwright

from conftest import FROZEN_NOW, Net
from test_site import two_team

HEADER_CHECK = """() => { const R = e => e.getBoundingClientRect(), hdr = R(document.querySelector('header'));
  const boxes = [...document.querySelectorAll('#teams button, .views button, .ts button, #menu summary')]
    .map(e => ({t: e.textContent.trim(), r: R(e)}));
  const out = boxes.filter(b => b.r.right > hdr.right - 1 || b.r.left < hdr.left).map(b => b.t);
  const over = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) { const a = boxes[i].r, b = boxes[j].r;
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) over.push(boxes[i].t + ' x ' + boxes[j].t); }
  const tb = document.getElementById('teams');
  return { out, over, teamsOverflow: R(tb.lastElementChild).right > R(tb).right + 1,
           height: Math.round(hdr.height), pageOverflow: document.documentElement.scrollWidth > innerWidth }; }"""


@pytest.fixture(scope="module")
def webkit():
    """Own Playwright instance, stopped at module end, so it never overlaps the shared one
    (pytest-playwright's) or the poster tests' own sync_playwright()."""
    pw = sync_playwright().start()
    try:
        b = pw.webkit.launch()
    except Exception as err:                       # not installed locally: `playwright install webkit`
        pw.stop()
        pytest.skip(f"WebKit not available: {err}")
    yield pw, b
    b.close()
    pw.stop()


@pytest.mark.parametrize("device", ["iPhone SE", "iPhone 14"])
@pytest.mark.parametrize("text_size", [100, 130, 160])
def test_iphone_header_never_clips_or_overlaps(webkit, site_url, events_json, device, text_size):
    playwright, webkit = webkit
    heights = set()
    for key in ("mbb", "wbb", "all"):
        dev = {k: v for k, v in playwright.devices[device].items() if k != "default_browser_type"}
        ctx = webkit.new_context(**dev, timezone_id="America/Los_Angeles", locale="en-US", service_workers="block")
        net = Net(two_team(events_json, key))
        ctx.route("**/*", net.handle)
        ctx.add_init_script(f"localStorage.setItem('cuesta-mbb-textsize', {json.dumps(str(text_size))})")
        page = ctx.new_page()
        page.clock.set_fixed_time(FROZEN_NOW)
        page.goto(site_url)
        page.wait_for_selector("#teams button.on")
        m = page.evaluate(HEADER_CHECK)
        ctx.close()
        assert not (m["out"] or m["over"] or m["teamsOverflow"] or m["pageOverflow"]), (key, m)
        heights.add(m["height"])
    assert len(heights) == 1, f"header height differs between teams: {heights}"
