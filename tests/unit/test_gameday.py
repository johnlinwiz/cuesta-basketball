"""Game-day refresh: when frequent runs do work (gameday.py), and same-night finals (daily.py)."""
import datetime as dt
import json

import pytest
import yaml

import common
import daily
import gameday
import gh
from common import TZ
from test_issues import FakeGitHub

FREQUENT = "7,22,37,52 17-23 * * *"


@pytest.fixture
def games(season_root):
    """One timed single-day game, one all-day tournament (Dec 18-20)."""
    p = season_root / "data/schedule.yaml"
    sched = yaml.safe_load(p.read_text())
    sched["games"] = [
        {"date": "2026-11-09", "time": "13:00", "opponent": "Redwoods", "site": "home", "venue": "cuesta",
         "type": "non-conference"},
        {"date": "2026-12-18", "end_date": "2026-12-20", "opponent": "Delta Tournament", "site": "neutral",
         "venue": "cuesta", "type": "tournament"},
    ]
    p.write_text(yaml.safe_dump(sched, sort_keys=False))
    return season_root


def at(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=TZ)


@pytest.mark.parametrize("now, active", [
    (at(2026, 11, 9, 11, 59), False),     # >1 h before a 1 PM tip-off
    (at(2026, 11, 9, 12, 0), True),       # window opens 1 h before
    (at(2026, 11, 9, 21, 0), True),       # 2 h game + 6 h: still catching a late final
    (at(2026, 11, 9, 21, 1), False),
    (at(2026, 11, 10, 15, 0), False),     # day after, no game
    (at(2026, 12, 19, 12, 0), True),      # all-day tournament, middle day
    (at(2026, 12, 20, 23, 0), True),      # last day
    (at(2026, 12, 21, 12, 0), False),
])
def test_frequent_runs_only_work_in_game_windows(games, now, active):
    assert gameday.decide(now, "schedule", FREQUENT)[0] is active


@pytest.mark.parametrize("event, schedule", [("push", ""), ("workflow_dispatch", ""),
                                             ("schedule", "17 11 * * *"), ("schedule", "47 13 * * *")])
def test_full_runs_always_work(games, event, schedule):
    assert gameday.decide(at(2026, 11, 10, 3, 0), event, schedule)[0] is True


def test_workflow_full_run_crons_match_gameday():
    """FULL_RUN_CRONS must be the workflow's morning crons, or those runs would be skipped."""
    wf = yaml.safe_load((common.ROOT / ".github/workflows/daily.yml").read_text())
    crons = {c["cron"] for c in wf[True]["schedule"]}                 # YAML 'on:' parses as True
    assert gameday.FULL_RUN_CRONS <= crons
    assert any(c.startswith("7,22,37,52") for c in crons - gameday.FULL_RUN_CRONS)


def test_single_day_final_posts_the_same_night(games, monkeypatch):
    fake = FakeGitHub(); monkeypatch.setattr(gh, "api", fake.api)
    eid = "2026-11-09-redwoods"
    fake.add_issue(5, f"<!-- event-id: {eid} -->")
    (games / "data/issues.json").write_text(json.dumps({eid: 5}))
    (games / "data/results.json").write_text(json.dumps(
        {eid: {"outcome": "W", "us": 70, "them": 60, "score": "70-60", "raw": "W, 70-60", "status": "Final"}}))
    monkeypatch.setattr(daily, "today_pt", lambda: dt.date(2026, 11, 9))
    daily.main()
    assert any("<!-- result -->" in c and "W 70-60" in c for c in fake.comments.get(5, []))
    assert fake.issues[5]["state"] == "closed"


def test_tournament_waits_for_the_day_after(games, monkeypatch):
    """A day-1 tournament score must not close the tournament's issue mid-event."""
    fake = FakeGitHub(); monkeypatch.setattr(gh, "api", fake.api)
    eid = "2026-12-18-delta-tournament"
    fake.add_issue(6, f"<!-- event-id: {eid} -->")
    (games / "data/issues.json").write_text(json.dumps({eid: 6}))
    (games / "data/results.json").write_text(json.dumps({eid: {"games": [
        {"date": "2026-12-18", "opponent": "X", "outcome": "W", "us": 70, "them": 60, "score": "70-60",
         "raw": "W, 70-60", "status": "Final"}]}}))
    monkeypatch.setattr(daily, "today_pt", lambda: dt.date(2026, 12, 18))
    daily.main()
    assert fake.issues[6]["state"] == "open"                                # day-1 score: still open
    assert not any("<!-- result -->" in c for c in fake.comments.get(6, []))    # (a game-day reminder is fine)
    monkeypatch.setattr(daily, "today_pt", lambda: dt.date(2026, 12, 21))
    daily.main()
    assert fake.issues[6]["state"] == "closed"
