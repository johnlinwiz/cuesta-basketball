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
