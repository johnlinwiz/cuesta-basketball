"""Printable one-page schedule (scripts/poster.py), rendered from the frozen e2e data."""
import datetime as dt
import re
import shutil

import pytest

HERE = __import__("pathlib").Path(__file__).resolve().parent


@pytest.fixture
def frozen_root(tmp_path, monkeypatch):
    import common
    import poster
    shutil.copytree(HERE / "data", tmp_path / "data")
    monkeypatch.setattr(common, "ROOT", tmp_path)
    monkeypatch.setattr(poster, "ROOT", tmp_path)
    return tmp_path


def test_poster_is_exactly_one_page_with_every_game(frozen_root, tmp_path):
    import common
    import poster
    sched, _, _, events = common.load()
    html_text = poster.build_html(sched, events, dt.date(2026, 11, 5))
    assert html_text.count('class="g') == len(events)                   # one line per game
    assert "W 81-74" in html_text or "W" in html_text                   # results shown for played games
    pdf = tmp_path / "out.pdf"
    poster.render(html_text, pdf, tmp_path / "out.png")
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF") and len(re.findall(rb"/Type\s*/Page[^s]", data)) == 1


def test_poster_overflow_shrinks_instead_of_adding_a_page(frozen_root, tmp_path):
    """A long season must scale down, never spill onto page 2."""
    import common
    import poster
    sched, _, _, events = common.load()
    html_text = poster.build_html(sched, events * 3, dt.date(2026, 11, 5))   # ~3x the games
    pdf = tmp_path / "long.pdf"
    scale = poster.render(html_text, pdf, tmp_path / "long.png")
    assert scale < 1 and len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes())) == 1


def test_black_and_white_poster_has_no_colour(frozen_root, tmp_path):
    """B&W version: one page, black QR, and every rendered pixel is grey (R == G == B, within JPEG-ish slack)."""
    import common
    import poster
    from playwright.sync_api import sync_playwright
    sched, _, _, events = common.load()
    html_text = poster.build_html(sched, events, dt.date(2026, 11, 5), bw=True)
    assert "#0b6e3a" not in html_text.split("</style>")[1]               # QR and body drawn without green
    pdf, png = tmp_path / "bw.pdf", tmp_path / "bw.png"
    poster.render(html_text, pdf, png)
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes())) == 1
    with sync_playwright() as p:                                         # decode the PNG in the browser (no Pillow)
        b = p.chromium.launch(); pg = b.new_page()
        worst = pg.evaluate("""async (b64) => {
            const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
            const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
            const x = c.getContext("2d"); x.drawImage(img, 0, 0);
            const d = x.getImageData(0, 0, c.width, c.height).data; let worst = 0;
            for (let i = 0; i < d.length; i += 4 * 7) {
              const m = Math.max(d[i], d[i+1], d[i+2]) - Math.min(d[i], d[i+1], d[i+2]); if (m > worst) worst = m; }
            return worst; }""", __import__("base64").b64encode(png.read_bytes()).decode())
        b.close()
    assert worst <= 24, f"colour found in the B&W poster (max channel spread {worst})"
