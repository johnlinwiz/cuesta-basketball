#!/usr/bin/env python3
"""Is now a game window? Decides whether a frequent (every-15-min) workflow run should do work.

A window opens WINDOW_BEFORE before tip-off and closes WINDOW_AFTER after the game's expected
end (default length + overtime slack), so finals posted late in the evening are still caught.
All-day events (tournaments with no time yet) count as 10:00–23:59 Pacific on each day.

    python3 scripts/gameday.py                 # prints active=true|false (+ why)
    GITHUB_OUTPUT=... python3 scripts/gameday.py --gha

Runs that aren't the frequent schedule (push, manual, the two morning crons) are always active:
set EVENT_NAME / EVENT_SCHEDULE (from github.event_name / github.event.schedule).
"""
import datetime as dt
import os
import sys

from common import TZ, load, teams

WINDOW_BEFORE = dt.timedelta(hours=1)
WINDOW_AFTER = dt.timedelta(hours=6)
FULL_RUN_CRONS = {"17 11 * * *", "47 13 * * *"}     # the daily morning runs in daily.yml


def windows(events):
    for e in events:
        if e.all_day:
            d = e.date
            while d <= e.end_date:
                yield (e, dt.datetime.combine(d, dt.time(10, 0), TZ), dt.datetime.combine(d, dt.time(23, 59), TZ))
                d += dt.timedelta(days=1)
        else:
            yield (e, e.start - WINDOW_BEFORE, e.end + WINDOW_AFTER)


def active_game(now, all_events):
    """The first event whose window contains `now`, else None."""
    for e, start, end in windows(all_events):
        if start <= now <= end:
            return e
    return None


def decide(now=None, event_name=None, schedule=None):
    event_name = event_name if event_name is not None else os.environ.get("EVENT_NAME", "")
    schedule = schedule if schedule is not None else os.environ.get("EVENT_SCHEDULE", "")
    if event_name != "schedule" or schedule in FULL_RUN_CRONS:
        return True, f"full run ({event_name or 'local'}{' ' + schedule if schedule else ''})"
    now = now or dt.datetime.now(TZ)
    events = [e for key in teams() for e in load(key)[3]]
    e = active_game(now, events)
    if e:
        return True, f"game window: {e.team_short} {e.title()}"
    return False, f"no game window at {now:%a %b %-d %-I:%M %p} PT"


def main(argv):
    active, why = decide()
    print(f"active={'true' if active else 'false'}  # {why}")
    if "--gha" in argv and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"active={'true' if active else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
