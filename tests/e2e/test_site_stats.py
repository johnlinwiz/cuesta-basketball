"""Player stats in the browser: the Stats view, box scores in the game pop-up, following a player."""
import json

import pytest
from playwright.sync_api import expect

from pagetools import horizontal_overflow

FOLLOW = "cuesta-follow"


def season_names(page):
    return [t.split(" ", 1)[1] for t in page.locator("table.season tbody td.pl button.link").all_inner_texts()]


@pytest.mark.parametrize("mobile", [True, False], ids=["phone", "desktop"])
def test_stats_view(make_page, mobile):
    page, _ = make_page(mobile=mobile, hash="#stats")
    expect(page.locator("#view-stats")).to_be_visible()
    expect(page.locator("#view-tbl")).to_be_hidden()
    expect(page.locator("#m-stats")).to_have_class("on")
    # per game, by points; a rostered player who hasn't played (or only dressed) sorts last
    assert season_names(page) == ["Ann Star", "Cy Guard", "Bo Bench", "Di Rookie"]
    ann = page.locator("table.season tbody tr").first.locator("td")
    assert ann.nth(4).inner_text() == "25.0"           # PTS: (20+30)/2
    assert ann.nth(3).inner_text() == "30.0"           # MIN: only the game with minutes counts
    assert ann.nth(10).inner_text() == "54.3"          # FG%: 19/35
    expect(page.locator("table.season tbody tr").last).to_contain_text("—")
    expect(page.locator("table.season tbody tr").first).to_contain_text("Guard · Sophomore · 6-1")
    page.click("#st-tot")
    assert page.locator("table.season tbody tr").first.locator("td").nth(4).inner_text() == "50"
    page.click("table.season th[data-col=reb]")
    assert season_names(page)[0] == "Cy Guard"         # 15 reb
    page.click("table.season th[data-col=reb]")        # again: ascending (players who haven't played stay last)
    assert season_names(page)[:2] == ["Ann Star", "Cy Guard"]
    if mobile:   # the wide table scrolls inside its box; the page itself never scrolls sideways
        assert not horizontal_overflow(page)


def test_follow_a_player(make_page):
    page, _ = make_page(mobile=False, hash="#stats")
    page.click("table.season button.star[data-name='Cy Guard']")
    expect(page.locator("table.season tr.fol")).to_contain_text("Cy Guard")
    assert json.loads(page.evaluate(f"localStorage.getItem('{FOLLOW}')")) == ["|Cy Guard"]
    # schedule rows: top scorers for every box score, plus the followed player's own line
    page.click("#m-tbl")
    row = page.locator("#tbl-b tr", has=page.locator("button.link", has_text="Gavilan"))
    expect(row.locator(".box-lines")).to_contain_text("🏀 Star 20 · Guard 12")
    expect(row.locator(".box-lines .me")).to_have_text("⭐ Cy Guard: 12 pts · 6 reb · 1 ast · 25 min")
    seq = page.locator("#tbl-b tr", has=page.locator("button.link", has_text="Sequoias"))
    expect(seq.locator(".box-lines .me")).to_have_text("⭐ Cy Guard: 5 pts · 9 reb · 0 ast")   # no minutes recorded
    # it's remembered on this device
    page.reload()
    page.wait_for_function("!/Loading/.test(document.querySelector('#next').textContent)")
    expect(page.locator("#tbl-b .box-lines .me").first).to_contain_text("Cy Guard")
    # unfollow from the game pop-up's box score
    page.click("#tbl-b button.link:has-text('Gavilan')")
    expect(page.locator("#dlg table.box tr.fol")).to_contain_text("Cy Guard")
    page.click("#dlg table.box button.star[data-name='Cy Guard']")
    expect(page.locator("#dlg table.box tr.fol")).to_have_count(0)
    expect(page.locator("#dlg")).to_be_visible()                       # stays open
    assert json.loads(page.evaluate(f"localStorage.getItem('{FOLLOW}')")) == []


def test_game_popup_box_score(make_page):
    page, _ = make_page(mobile=False)
    page.click("#tbl-b button.link:has-text('Gavilan')")
    box = page.locator("#dlg table.box")
    expect(box.locator("tbody tr")).to_have_count(3)
    expect(box.locator("tbody tr.dnp")).to_contain_text("DNP")          # Bo Bench dressed, didn't play
    expect(box.locator("tfoot")).to_contain_text("Team")
    assert box.locator("tfoot td").nth(3).inner_text() == "32"          # team points
    page.keyboard.press("Escape")
    page.click("#tbl-b button.link:has-text('Sequoias')")
    expect(page.locator("#dlg")).to_contain_text("minutes weren't recorded")
    expect(page.locator("#dlg table.box tbody tr").first.locator("td").nth(2)).to_have_text("—")
    page.keyboard.press("Escape")
    page.click("#tbl-b button.link:has-text('Reedley')")                # no box score: no table
    expect(page.locator("#dlg table.box")).to_have_count(0)


def test_player_game_log(make_page):
    page, _ = make_page(mobile=True, hash="#stats")
    page.click("table.season button.link:has-text('Ann Star')")
    dlg = page.locator("#dlg")
    expect(dlg.locator("#d-title")).to_have_text("#2 Ann Star")
    expect(dlg.locator("#d-sub")).to_contain_text("Pasadena, CA / Muir")
    expect(dlg).to_contain_text("25.0 pts · 3.0 reb · 4.0 ast per game")
    expect(dlg.locator("tbody tr")).to_have_count(2)
    page.click("#d-follow")
    expect(page.locator("#d-follow")).to_have_text("★ Following")
    expect(page.locator("table.season tr.fol")).to_contain_text("Ann Star")


def test_no_stats_file_is_fine(make_page):
    """Before the first box score (or if stats.json is missing) the site works as before."""
    page, _ = make_page(mobile=False, hash="#stats", stats=None)
    expect(page.locator("#st-body")).to_contain_text("No box scores yet")
    page.click("#m-tbl")
    expect(page.locator("#tbl-b .box-lines").first).to_contain_text("🏀")    # top scorers still come from events.json
    page.click("#tbl-b button.link:has-text('Gavilan')")
    expect(page.locator("#dlg table.box")).to_have_count(0)


def test_bad_follow_storage_is_ignored(make_page):
    page, _ = make_page(mobile=False, hash="#stats", init_script=f"localStorage.setItem('{FOLLOW}', '{{not json')")
    expect(page.locator("table.season tbody tr")).to_have_count(4)
