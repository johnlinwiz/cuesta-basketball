"""Player stats: box score / roster parsing (saved real pages), validation, season totals,
the polite fetch loop, and the opt-in box-score comment for followed players."""
import datetime as dt
import json
import pathlib

import pytest
import yaml

import common
import daily
import fetch_stats
import gh
from fetch_stats import box_is_complete, normalize_game, parse_box, parse_roster, season_totals
from test_issues import FakeGitHub

FIX = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "stats"
page = lambda name: (FIX / name).read_text()


def test_parse_box_full_game_table():
    lines = parse_box(page("box-gavilan.html"))
    assert len(lines) == 11
    assert sum(l["pts"] for l in lines) == 93                    # W 93-53
    assert sum(l["starter"] for l in lines) == 5
    j = next(l for l in lines if l["name"] == "Ahmad Jacquet")
    assert (j["no"], j["pts"]) == ("5", 26)
    assert j["fgm"] <= j["fga"] and j["fg3m"] <= j["fg3a"] and j["reb"] == j["oreb"] + j["dreb"]
    assert sum(l["min"] for l in lines) == 200                   # 5 players x 40 min


def test_minutes_placeholder_becomes_unknown():
    """Presto writes MIN 1 for everyone when minutes weren't tracked; points are still right."""
    lines = parse_box(page("box-sequoias.html"))
    assert sum(l["pts"] for l in lines) == 82 and {l["min"] for l in lines} == {1}
    g = normalize_game({"event": "x", "lines": lines})
    assert g["minutes"] is False and all(l["min"] is None for l in g["lines"])
    assert normalize_game(g) is g                                # idempotent
    real = normalize_game({"event": "y", "lines": parse_box(page("box-gavilan.html"))})
    assert real["minutes"] is True


def test_parse_roster():
    roster = parse_roster(page("roster-mbb.html"))
    assert len(roster) >= 12 and len({p["name"] for p in roster}) == len(roster)
    lin = next(p for p in roster if p["name"] == "Evan Lin")
    assert lin["no"] == "2" and lin["pos"] and lin["cl"] and lin["ht"]


class Ev:
    def __init__(self, result=None):
        self.result = result


@pytest.mark.parametrize("lines, result, ok", [
    ([], None, False),                                           # no Cuesta table
    ([{"pts": 0}] * 8, {"outcome": "W", "us": 72}, False),       # all zeros: stats not entered yet
    ([{"pts": 40}, {"pts": 32}], {"outcome": "W", "us": 72}, True),
    ([{"pts": 40}, {"pts": 30}], {"outcome": "W", "us": 72}, False),  # partial
    ([{"pts": 10}], None, True),                                 # no final known: any points
    ([{"pts": 0}], {"outcome": "W", "us": 2, "forfeit": True}, False),
])
def test_box_is_complete(lines, result, ok):
    assert box_is_complete(lines, Ev(result))[0] is ok


def test_season_totals_skip_scrimmages_and_unknown_minutes():
    a = {"name": "A", "no": "1", "starter": True, "min": 30, "pts": 10, "reb": 5}
    games = [
        {"event": "g1", "lines": [a, {"name": "B", "no": "2", "starter": False, "min": 0, "pts": 0}]},
        {"event": "g2", "lines": [{**a, "min": 1, "pts": 20}]},          # minutes not tracked
        {"event": "scrim", "lines": [{**a, "pts": 99}]},
    ]
    tot = {t["name"]: t for t in season_totals(games, exclude={"scrim"})}
    assert (tot["A"]["gp"], tot["A"]["gs"], tot["A"]["mgp"], tot["A"]["min"], tot["A"]["pts"]) == (2, 2, 1, 30, 30)
    assert tot["B"]["gp"] == 0                                    # dressed, didn't play


@pytest.fixture
def stats_env(season_root, monkeypatch):
    """First real game of the season, the day after; the box score URL maps to a saved page."""
    sched, _, _, events = common.load()
    e = next(x for x in events if x.type != "scrimmage")
    monkeypatch.setattr(fetch_stats, "today_pt", lambda: e.end_date + dt.timedelta(days=1))
    monkeypatch.setattr(fetch_stats, "parse_page", lambda body, base: [
        {"date": e.date, "opponent": e.opponent, "boxscore": "https://box.example/1"}])
    monkeypatch.setattr(fetch_stats.time, "sleep", lambda s: None)
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        if "box.example" in url:
            return page("box-gavilan.html")
        return page("roster-mbb.html") if url.endswith("/roster") else "<html></html>"
    monkeypatch.setattr(fetch_stats, "fetch", fake_fetch)
    return season_root, e, fetched, events[-1].end_date


def test_run_caches_a_complete_box_score_once(stats_env):
    root, e, fetched, _ = stats_env
    fetch_stats.run(None)
    cached = json.loads((root / "data/box" / f"{e.id}.json").read_text())
    assert sum(l["pts"] for l in cached["lines"]) == 93
    stats = json.loads((root / "data/stats.json").read_text())
    assert stats["season"] and stats["roster"]
    n = len(fetched)
    fetch_stats.run(None)                                         # nothing missing, roster fresh: no requests
    assert len(fetched) == n


def test_run_does_not_cache_a_box_score_that_disagrees_with_the_final(stats_env):
    root, e, _, _ = stats_env
    (root / "data/results.json").write_text(json.dumps(
        {e.id: {"outcome": "W", "us": 50, "them": 40, "score": "50-40", "raw": "W, 50-40", "status": "Final"}}))
    fetch_stats.run(None)
    assert not (root / "data/box" / f"{e.id}.json").exists()      # retried on later runs


def test_run_stops_retrying_after_a_week(stats_env, monkeypatch):
    root, e, fetched, last = stats_env
    (root / "data/stats.json").write_text(json.dumps({"roster": [{"name": "X"}]}))
    fetch_stats.roster_stamp(None).parent.mkdir()
    fetch_stats.roster_stamp(None).write_text(dt.datetime.now(common.TZ).isoformat())
    monkeypatch.setattr(fetch_stats, "today_pt", lambda: last + dt.timedelta(days=8))
    fetch_stats.run(None)
    assert fetched == []                                          # every game's box is > a week old


# ---------- opt-in box-score comment (daily.py) ----------
@pytest.fixture
def followed_game(season_root, monkeypatch):
    p = season_root / "data/schedule.yaml"
    sched = yaml.safe_load(p.read_text())
    sched["games"] = [{"date": "2026-11-09", "time": "13:00", "opponent": "Redwoods", "site": "home",
                       "venue": "cuesta", "type": "non-conference"}]
    sched["follow"] = ["evan lin", "Nobody Here"]
    p.write_text(yaml.safe_dump(sched, sort_keys=False))
    eid = "2026-11-09-redwoods"
    (season_root / "data/box").mkdir()
    lines = parse_box(page("box-gavilan.html"))
    (season_root / "data/box" / f"{eid}.json").write_text(json.dumps(
        {"event": eid, "date": "2026-11-09", "opponent": "Redwoods", "boxscore": "https://box.example/1", "lines": lines}))
    (season_root / "data/issues.json").write_text(json.dumps({eid: 7}))
    fake = FakeGitHub(); monkeypatch.setattr(gh, "api", fake.api)
    fake.add_issue(7, f"<!-- event-id: {eid} -->", state="closed")
    return season_root, fake, lines


def test_box_comment_lists_followed_players_once(followed_game, monkeypatch):
    root, fake, lines = followed_game
    monkeypatch.setattr(daily, "today_pt", lambda: dt.date(2026, 11, 10))
    daily.main(); daily.main()
    box = [c for c in fake.comments.get(7, []) if "<!-- box -->" in c]
    assert len(box) == 1
    lin = next(l for l in lines if l["name"] == "Evan Lin")
    assert f"⭐ **Evan Lin**: {daily.stat_line(lin)}" in box[0]
    assert "⭐ **Nobody Here**: did not play" in box[0]
    assert "Top scorers: Ahmad Jacquet 26" in box[0] and "@johnlinwiz" in box[0]


def test_box_comment_is_opt_in_and_not_backfilled(followed_game, monkeypatch):
    root, fake, _ = followed_game
    monkeypatch.setattr(daily, "today_pt", lambda: dt.date(2026, 11, 20))      # > BOX_COMMENT_DAYS later
    daily.main()
    assert not any("<!-- box -->" in c for c in fake.comments.get(7, []))
    p = root / "data/schedule.yaml"
    sched = yaml.safe_load(p.read_text()); sched["follow"] = []; p.write_text(yaml.safe_dump(sched, sort_keys=False))
    monkeypatch.setattr(daily, "today_pt", lambda: dt.date(2026, 11, 10))
    daily.main()
    assert not any("<!-- box -->" in c for c in fake.comments.get(7, []))


def test_box_saved_without_minutes_is_rechecked_and_updated(stats_env, monkeypatch):
    """Presto often finishes a box score later: one saved without minutes is re-read (for a week)."""
    root, e, fetched, _ = stats_env
    pages = {"box": "box-sequoias.html"}                                  # first: no minutes (MIN 1 placeholders)
    real_fetch = fetch_stats.fetch
    def fetch(url):
        if "box.example" in url:
            fetched.append(url); return page(pages["box"])
        return real_fetch(url)
    monkeypatch.setattr(fetch_stats, "fetch", fetch)
    fetch_stats.run(None)
    f = root / "data/box" / f"{e.id}.json"
    assert not normalize_game(json.loads(f.read_text()))["minutes"]
    pages["box"] = "box-gavilan.html"                                     # later: the finished box score
    fetch_stats.run(None)
    assert normalize_game(json.loads(f.read_text()))["minutes"]
    n = len(fetched)
    fetch_stats.run(None)                                                 # complete now: no more box requests
    assert not any("box.example" in u for u in fetched[n:])
