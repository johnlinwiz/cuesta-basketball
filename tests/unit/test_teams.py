"""Multi-team layout: data/teams/<key>/ — per-team outputs, id prefixes, and team-scoped issue sync."""
import json
import shutil

import pytest
import yaml

import build
import check_feed
import common
import gh
import sync_issues
from helpers import SEASON, read


@pytest.fixture
def two_teams(season_root):
    """Turn the legacy fixture root into data/teams/{mbb,wbb}; wbb = same games with a prefix."""
    data = season_root / "data"
    base = yaml.safe_load((data / "schedule.yaml").read_text())
    (data / "schedule.yaml").unlink()
    for key, short, prefix, name in (("mbb", "MBB", "", "Men's"), ("wbb", "WBB", "wbb-", "Women's")):
        d = data / "teams" / key
        d.mkdir(parents=True)
        sched = dict(base, short=short, id_prefix=prefix, ics=f"cuesta-{key}.ics",
                     team=f"Cuesta College {name} Basketball")
        (d / "schedule.yaml").write_text(yaml.safe_dump(sched, sort_keys=False))
    return season_root


def test_teams_discovered_and_legacy_fallback(season_root, two_teams):
    assert common.teams() == ["mbb", "wbb"]
    sched, *_ , events = common.load("wbb")
    assert sched["key"] == "wbb" and sched["site_url"].endswith("?team=wbb")
    assert all(e.id.startswith("wbb-") for e in events)
    mbb_ids = {e.id for e in common.load("mbb")[3]}
    assert not mbb_ids & {e.id for e in events}                        # same games, never the same id
    assert all(e.summary().startswith("Cuesta WBB ") for e in events)


def test_legacy_layout_is_single_team(season_root):
    assert common.teams() == [None]
    assert common.load()[0]["site_url"] == common.PAGES_URL


def test_build_writes_per_team_outputs(two_teams):
    build.main()
    for key in ("mbb", "wbb"):
        assert (two_teams / key / "README.md").exists() and (two_teams / key / "CALENDAR.md").exists()
        data = json.loads((two_teams / "docs" / key / "events.json").read_text())
        assert data["teamKey"] == key and [t["key"] for t in data["teams"]] == ["mbb", "wbb", "all"]
        assert not any(e["title"].startswith(("👨", "👩")) for e in data["events"])   # single team: untagged
        assert (two_teams / "docs" / f"cuesta-{key}.ics").exists()
    combined = (two_teams / "docs" / common.COMBINED_ICS).read_text()
    n = combined.count("BEGIN:VEVENT")
    assert n == 2 * (two_teams / "docs" / "cuesta-mbb.ics").read_text().count("BEGIN:VEVENT")
    both = json.loads((two_teams / "docs" / "all" / "events.json").read_text())
    per_team = sum(len(json.loads((two_teams / "docs" / k / "events.json").read_text())["events"]) for k in ("mbb", "wbb"))
    assert both["teamKey"] == "all" and len(both["events"]) == per_team
    assert len({e["id"] for e in both["events"]}) == per_team                 # no id collisions across teams
    assert {e["title"][:1] for e in both["events"]} == {"👨", "👩"}
    assert both["record"].startswith("Men ") and " · Women " in both["record"]
    assert [e["start"] for e in both["events"]] == sorted(e["start"] for e in both["events"])
    index = (two_teams / "README.md").read_text()
    assert "[MBB schedule](mbb/README.md)" in index and "[WBB schedule](wbb/README.md)" in index
    assert not (two_teams / "docs" / "events.json").exists()            # no stale single-team file


class FakeGH:
    def __init__(self):
        self.issues, self.calls, self.next = {}, [], 100

    def api(self, path, method="GET", fields=None, paginate=False):
        self.calls.append((method, path.split("?")[0], fields))
        if path.startswith(("labels", "milestones")) and method == "GET":
            return []
        if path in ("labels", "milestones") and method == "POST":
            return {"number": 1}
        if path.startswith("issues?"):
            return list(self.issues.values())
        if path == "issues" and method == "POST":
            self.next += 1; self.issues[self.next] = dict(fields, number=self.next, state="open"); return {"number": self.next}
        n = int(path.split("/")[1])
        if method == "PATCH":
            self.issues[n].update(fields)
        return self.issues.get(n, {"state": "open"})


def test_sync_never_touches_the_other_teams_issues(two_teams, monkeypatch):
    """The MBB pass must not treat a live WBB issue as 'not on my schedule' and close it."""
    import datetime as dt
    fake = FakeGH()
    monkeypatch.setattr(gh, "api", fake.api)
    monkeypatch.setattr(sync_issues, "today_pt", lambda: dt.date(2000, 1, 1))     # every fixture game is "future"
    mbb_id = common.load("mbb")[3][0].id                                # a real game on the fixture schedule
    wbb_id = "wbb-" + mbb_id                                            # the same game on the WBB schedule
    fake.issues[7] = {"number": 7, "state": "open", "title": "old",
                      "body": f"<!-- event-id: {wbb_id} -->\n<!-- team: wbb -->"}
    fake.issues[8] = {"number": 8, "state": "open", "title": "old",   # pre-multi-team issue: no team marker
                      "body": f"<!-- event-id: {mbb_id} -->"}
    sync_issues.main()
    mbb = json.loads((two_teams / "data/teams/mbb/issues.json").read_text())
    wbb = json.loads((two_teams / "data/teams/wbb/issues.json").read_text())
    assert mbb[mbb_id] == 8 and wbb[wbb_id] == 7                         # each team adopted its own issue
    assert fake.issues[8]["title"].startswith("[MBB] ") and fake.issues[7]["title"].startswith("[WBB] ")
    assert fake.issues[7]["state"] == "open" and fake.issues[8]["state"] == "open"
    assert not [c for c in fake.calls if c[0] == "PATCH" and (c[2] or {}).get("state") == "closed"]
    new_wbb = [i for i in fake.issues.values() if i.get("title", "").startswith("[WBB] ") and i["number"] != 7]
    assert new_wbb and all("wbb" in i["labels"] and "<!-- team: wbb -->" in i["body"] for i in new_wbb)


def test_feed_sport_filter_separates_mens_and_womens():
    mens = read("league.ics")
    womens = mens.replace("(Men's Basketball)", "(Women's Basketball)")
    assert len(check_feed.parse_feed(womens)) == 0                       # men's filter ignores women's events
    assert len(check_feed.parse_feed(womens, sport="Women's Basketball")) == len(check_feed.parse_feed(mens))
