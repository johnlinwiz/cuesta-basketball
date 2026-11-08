"""Player stats in the browser: the Stats view (sorting, filters, column groups, leaders, URL state),
box scores in the game pop-up, the player pop-up (highs, chart, log), compare, CSV, following.

Fixture (conftest.stats_json): Gavilan (away, minutes recorded) and Sequoias (home, no minutes).
Ann Star 20+30 pts, FT 5-6; Cy Guard 12+5 pts, FT 1-1 (too few to rank), started only at Gavilan;
Bo Bench dressed but didn't play; Di Rookie is on the roster only."""
import json

import pytest
from playwright.sync_api import expect

from pagetools import horizontal_overflow

FOLLOW = "cuesta-follow"
COL = {"no": 2, "pos": 3, "gp": 4, "gs": 5, "min": 6, "pts": 7, "reb": 8, "ast": 9, "fgp": 13, "ftp": 15}   # Basic group


def season_names(page):
    return page.locator("table.season tbody td.pl button.link").all_inner_texts()


def cell(page, name, col):
    row = page.locator("table.season tbody tr", has=page.locator(f"button.link:text-is('{name}')"))
    return row.locator("td").nth(COL[col])


def stats_url(page):
    return page.evaluate("location.hash")


@pytest.mark.parametrize("mobile", [True, False], ids=["phone", "desktop"])
def test_stats_view(make_page, mobile):
    page, _ = make_page(mobile=mobile, hash="#stats")
    expect(page.locator("#view-stats")).to_be_visible()
    expect(page.locator("#view-tbl")).to_be_hidden()
    expect(page.locator("#m-stats")).to_have_class("on")
    # per game, by points; players who haven't played sort last
    assert season_names(page) == ["Ann Star", "Cy Guard", "Bo Bench", "Di Rookie"]
    assert cell(page, "Ann Star", "pts").inner_text() == "25.0"          # (20+30)/2
    assert cell(page, "Ann Star", "min").inner_text() == "30.0"          # only the game with minutes counts
    assert cell(page, "Ann Star", "fgp").inner_text() == "54.3"          # 19/35
    assert cell(page, "Cy Guard", "gs").inner_text() == "1"
    assert cell(page, "Ann Star", "pos").inner_text() == "G"
    expect(page.locator("table.season tbody tr").last).to_contain_text("—")
    expect(page.locator("table.season tbody tr").first).to_contain_text("Sophomore · 6-1")
    page.click("[data-st-mode=tot]")
    assert cell(page, "Ann Star", "pts").inner_text() == "50"
    if mobile:   # the wide table scrolls inside its box; the page itself never scrolls sideways
        assert not horizontal_overflow(page)


def test_sorting(make_page):
    page, _ = make_page(mobile=False, hash="#stats")
    page.click("table.season th[data-col=reb]")
    assert season_names(page)[0] == "Cy Guard"                          # 15 reb
    expect(page.locator("table.season th[data-col=reb]")).to_have_attribute("aria-sort", "descending")
    page.click("table.season th[data-col=reb]")                         # again: ascending, non-players still last
    assert season_names(page)[:2] == ["Ann Star", "Cy Guard"] and season_names(page)[-1] in ("Bo Bench", "Di Rookie")
    page.click("table.season th[data-col=name]")                        # by last name, everyone included
    assert season_names(page) == ["Bo Bench", "Cy Guard", "Di Rookie", "Ann Star"]
    page.click("table.season th[data-col=no]")
    assert season_names(page) == ["Ann Star", "Bo Bench", "Cy Guard", "Di Rookie"]   # 2, 5, 10, 21


def test_shooting_needs_a_real_sample(make_page):
    """Cy's 1-for-1 FT (100%) is faded and ranks below Ann's 5-for-6 (83.3%)."""
    page, _ = make_page(mobile=False, hash="#stats/sort=ftp")
    assert season_names(page)[:2] == ["Ann Star", "Cy Guard"]
    expect(cell(page, "Cy Guard", "ftp")).to_have_text("100.0")
    expect(cell(page, "Cy Guard", "ftp")).to_have_class("weak")
    expect(cell(page, "Ann Star", "ftp")).not_to_have_class("weak")


def test_filters_and_shareable_url(make_page):
    page, _ = make_page(mobile=False, hash="#stats/site=home&sort=reb")
    expect(page.locator("#st-site")).to_have_value("home")
    expect(page.locator("#st-body h2")).to_contain_text("1 game")
    assert season_names(page)[0] == "Cy Guard" and cell(page, "Ann Star", "pts").inner_text() == "30.0"
    expect(page.locator("#st-reset")).to_be_visible()
    page.select_option("#st-site", "all")
    page.select_option("#st-role", "starter")
    assert "role=starter" in stats_url(page) and "site" not in stats_url(page)
    assert cell(page, "Cy Guard", "gp").inner_text() == "1"            # started only at Gavilan
    page.click("#st-reset")
    assert stats_url(page) == "#stats/sort=reb"
    expect(page.locator("#st-reset")).to_be_hidden()
    page.select_option("#st-games", "conf")                            # no conference games yet
    expect(page.locator("#st-body")).to_contain_text("No games match these filters.")


def test_column_groups(make_page):
    page, _ = make_page(mobile=False, hash="#stats")
    page.click("[data-st-cols=shoot]")
    expect(page.locator("table.season thead")).to_contain_text("eFG%")
    expect(page.locator("table.season thead")).to_contain_text("FTM-A")
    page.click("[data-st-cols=adv]")                                   # no Pts column: re-sorts by Pts/40
    expect(page.locator("table.season th.on")).to_have_text("Pts/40")
    assert "cols=adv" in stats_url(page) and "sort=pts40" in stats_url(page)
    page.click("[data-st-cols=reb]")
    expect(page.locator("table.season thead")).to_contain_text("OReb")


def test_leaders(make_page):
    page, _ = make_page(mobile=False, hash="#stats")
    cards = page.locator(".leaders .ld")
    expect(cards.nth(0)).to_contain_text("Points")
    expect(cards.nth(0).locator("li").first).to_contain_text("Star")
    expect(cards.nth(0).locator("li").first).to_contain_text("25.0")
    expect(cards.nth(1).locator("li").first).to_contain_text("Guard")    # 7.5 reb/game
    cards.nth(2).locator("button.link").first.click()                   # a leader opens the player
    expect(page.locator("#d-title")).to_have_text("#2 Ann Star")


def test_player_popup(make_page):
    page, _ = make_page(mobile=True, hash="#stats")
    page.click("table.season button.link:text-is('Ann Star')")
    dlg = page.locator("#dlg")
    expect(dlg.locator("#d-title")).to_have_text("#2 Ann Star")
    expect(dlg.locator("#d-sub")).to_contain_text("Pasadena, CA / Muir")
    expect(dlg).to_contain_text("25.0 pts · 3.0 reb · 4.0 ast per game")
    expect(dlg).to_contain_text("Season highs: 30 pts (vs Sequoias) · 4 reb (@ Gavilan) · 5 ast (vs Sequoias)")
    expect(dlg.locator(".pchart path.bar")).to_have_count(2)
    expect(dlg.locator(".pchart text.v")).to_have_text("30")             # only the high is labelled
    dlg.locator(".pchart rect.hit").nth(0).click()
    expect(page.locator("#pc-read")).to_contain_text("Gavilan: 20 pts")
    expect(dlg.locator("table.st tbody tr")).to_have_count(2)
    expect(dlg.locator("table.st b.hi")).to_have_text(["4", "30", "5"])  # reb high @ Gavilan; pts + ast vs Sequoias
    page.click("#d-follow")
    expect(page.locator("#d-follow")).to_have_text("★ Following")
    expect(page.locator("table.season tr.fol")).to_contain_text("Ann Star")


def test_compare(make_page):
    page, _ = make_page(mobile=False, hash="#stats")
    page.click("#st-compare")
    sels = page.locator(".cmp-pick select")
    expect(sels).to_have_count(3)
    expect(page.locator("table.cmp thead")).to_contain_text("Ann Star")   # default: top scorers
    expect(page.locator("table.cmp thead")).to_contain_text("Cy Guard")
    pts = page.locator("table.cmp tr", has=page.locator("th:text-is('Pts')"))
    expect(pts.locator("td b")).to_have_text("25.0")                     # best in bold
    to = page.locator("table.cmp tr", has=page.locator("th:text-is('TO')"))
    expect(to.locator("td")).to_have_count(2)
    sels.nth(1).select_option(label="Ann Star")                          # duplicate pick collapses
    expect(page.locator("table.cmp thead th")).to_have_count(2)
    page.keyboard.press("Escape")
    page.click("table.season button.link:text-is('Cy Guard')")
    page.click("#d-compare")                                             # from a player: that player first
    expect(page.locator("table.cmp thead th").nth(1)).to_have_text("Cy Guard")


def test_csv_download(make_page):
    page, _ = make_page(mobile=False, hash="#stats/site=away")
    with page.expect_download() as dl:
        page.click("#st-csv")
    assert dl.value.suggested_filename == "cuesta-team-stats-per-game-away.csv"
    rows = open(dl.value.path()).read().strip().split("\n")
    head = rows[0].split(",")
    assert head[:3] == ["Team", "#", "Player"] and "TS%" in head and "PTS/40" in head
    assert len(rows) == 1 + 4
    ann = dict(zip(head, rows[1].split(",")))
    assert ann["Player"] == "Ann Star" and ann["PTS"] == "20.0" and ann["FT%"] == "75.0"


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
    page.reload()                                                        # remembered on this device
    page.wait_for_function("!/Loading/.test(document.querySelector('#next').textContent)")
    expect(page.locator("#tbl-b .box-lines .me").first).to_contain_text("Cy Guard")
    page.click("#tbl-b button.link:has-text('Gavilan')")                 # unfollow from the game pop-up
    expect(page.locator("#dlg table.box tr.fol")).to_contain_text("Cy Guard")
    page.click("#dlg table.box button.star[data-name='Cy Guard']")
    expect(page.locator("#dlg table.box tr.fol")).to_have_count(0)
    expect(page.locator("#dlg")).to_be_visible()
    assert json.loads(page.evaluate(f"localStorage.getItem('{FOLLOW}')")) == []


def test_game_popup_box_score(make_page):
    page, _ = make_page(mobile=False)
    page.click("#tbl-b button.link:has-text('Gavilan')")
    box = page.locator("#dlg table.box")
    expect(box.locator("tbody tr")).to_have_count(3)
    expect(box.locator("tbody tr.dnp")).to_contain_text("DNP")
    assert box.locator("tfoot td").nth(3).inner_text() == "32"
    page.keyboard.press("Escape")
    page.click("#tbl-b button.link:has-text('Sequoias')")
    expect(page.locator("#dlg")).to_contain_text("minutes weren't recorded")
    expect(page.locator("#dlg table.box tbody tr").first.locator("td").nth(2)).to_have_text("—")
    page.keyboard.press("Escape")
    page.click("#tbl-b button.link:has-text('Reedley')")
    expect(page.locator("#dlg table.box")).to_have_count(0)


def test_no_stats_file_is_fine(make_page):
    page, _ = make_page(mobile=False, hash="#stats", stats=None)
    expect(page.locator("#st-body")).to_contain_text("No box scores yet")
    page.click("#m-tbl")
    expect(page.locator("#tbl-b .box-lines").first).to_contain_text("🏀")
    page.click("#tbl-b button.link:has-text('Gavilan')")
    expect(page.locator("#dlg table.box")).to_have_count(0)


def test_bad_state_is_ignored(make_page):
    page, _ = make_page(mobile=False, hash="#stats/sort=evil&site=moon&span=99",
                        init_script=f"localStorage.setItem('{FOLLOW}', '{{not json')")
    expect(page.locator("table.season tbody tr")).to_have_count(4)
    expect(page.locator("table.season th.on")).to_have_text("Pts")
    expect(page.locator("#st-site")).to_have_value("all")
