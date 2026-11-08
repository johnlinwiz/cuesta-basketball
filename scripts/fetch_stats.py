#!/usr/bin/env python3
"""Player stats from the official box scores + roster (PrestoSports).

    python3 scripts/fetch_stats.py              # every team; polite: only fetches what's missing
    python3 scripts/fetch_stats.py --force      # re-read roster and every box score

GitHub's runners get HTTP 405 from these pages, so this runs from a home connection (the Pi4 cron
job, scripts/pi_stats_job.sh). Writes per team:
  data/teams/<t>/box/<event-id>.json   one parsed box score per finished game (never refetched)
  data/teams/<t>/stats.json            roster + per-game lines + season totals (scrimmages excluded)
"""
import datetime as dt
import html
import json
import re
import subprocess
import sys
import time

from check_feed import norm
import common
from common import TZ, load, team_file, teams, today_pt
from fetch_results import parse_page

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141 Safari/537.36")
COLS = ["min", "fg", "fg3", "ft", "oreb", "dreb", "reb", "ast", "stl", "blk", "to", "pf", "pts"]
SPLIT = {"fg": ("fgm", "fga"), "fg3": ("fg3m", "fg3a"), "ft": ("ftm", "fta")}
TOTALS = ["min", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "to", "pf", "pts"]
ROSTER_MAX_AGE = dt.timedelta(hours=24)
RETRY_DAYS = dt.timedelta(days=7)


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def fetch(url):
    r = subprocess.run(["curl", "-sL", "--max-time", "60", "-A", BROWSER_UA, "-w", "\n%{http_code}", url],
                       capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    if code != "200":
        raise RuntimeError(f"{url} -> HTTP {code or '?'}")
    return body


def num(s):
    s = (s or "").strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else 0


def parse_box(page, team_name="Cuesta"):
    """The full-game player table for `team_name` (first <table> captioned with it)."""
    for m in re.finditer(r"<table\b.*?</table>", page, re.S):
        tb = m.group(0)
        cap = re.search(r"<caption[^>]*>(.*?)</caption>", tb, re.S)
        if not cap or text(cap.group(1)) != team_name:
            continue
        lines, starter = [], True
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", tb, re.S):
            label = text(tr)
            if label.upper().startswith("RESERVES"):
                starter = False; continue
            name = re.search(r'class="player-name"[^>]*>(.*?)</a>', tr, re.S)
            if not name:
                continue
            pid = re.search(r"players\?id=([a-z0-9]+)", tr)
            no = re.search(r'<span class="uniform">\s*(\d+)', tr)
            cells = [text(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)]
            if len(cells) < len(COLS):
                continue
            line = {"name": text(name.group(1)), "id": pid.group(1) if pid else None,
                    "no": no.group(1) if no else "", "starter": starter}
            for col, raw in zip(COLS, cells):
                if col in SPLIT:
                    made, _, att = raw.partition("-")
                    line[SPLIT[col][0]], line[SPLIT[col][1]] = num(made), num(att)
                else:
                    line[col] = num(raw)
            lines.append(line)
        if lines:
            return lines
    return []


def parse_roster(page):
    """Roster rows: number, name, position, class, height, hometown."""
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.S):
        cells = [text(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, re.S)]
        name = re.search(r'<a [^>]*href="[^"]*/(?:bios|players)/[^"]*"[^>]*>(.*?)</a>', tr, re.S)   # Presto: /bios/<slug>
        if not name or len(cells) < 4:
            continue
        def field(label):
            for c in cells:
                if c.startswith(label + ":"):
                    return c.split(":", 1)[1].strip()
            return ""
        no = field("No.") or (cells[0] if re.fullmatch(r"\d+", cells[0]) else "")
        out.append({"no": no, "name": text(name.group(1)), "pos": field("Pos."), "cl": field("Cl."),
                    "ht": field("Ht."), "home": field("Hometown/High School")})
    seen, uniq = set(), []
    for p in out:
        if p["name"] not in seen:
            seen.add(p["name"]); uniq.append(p)
    return uniq


def box_is_complete(lines, e):
    """Only cache a box score once it is final: players' points must add up to Cuesta's score.

    Presto sometimes publishes a box score before the stats are entered (all zeros, or template
    placeholders); caching that would freeze wrong numbers forever, since box scores aren't refetched.
    """
    if not lines:
        return False, "no Cuesta player table"
    pts = sum(l.get("pts", 0) for l in lines)
    us = (e.result or {}).get("us") if e.result and "outcome" in e.result else None
    if us is not None and not (e.result or {}).get("forfeit"):
        return (pts == us, f"player points {pts} vs final score {us}")
    return (pts > 0, f"player points {pts}")


def normalize_game(g):
    """Presto writes MIN 1 for every player when the stat crew didn't track minutes (the points and
    shooting are still right). Mark those minutes unknown (None) instead of 1, so per-game minutes
    aren't dragged down; such box scores list only the players who appeared."""
    if "minutes" in g:                                # already normalized
        return g
    lines = g["lines"]
    known = not (lines and all(l.get("min", 0) <= 1 for l in lines))
    if not known:
        lines = [{**l, "min": None} for l in lines]
    return {**g, "lines": lines, "minutes": known}


def appeared(ln):
    return ln.get("min") is None or ln.get("min", 0) > 0


def season_totals(games, exclude):
    """Per player: games played/started, stat totals; `mgp` = games with minutes recorded (MIN/game = min/mgp)."""
    tot = {}
    for g in map(normalize_game, games):
        if g["event"] in exclude:
            continue
        for ln in g["lines"]:
            key = ln.get("id") or ln["name"]
            t = tot.setdefault(key, {"name": ln["name"], "no": ln.get("no", ""), "gp": 0, "gs": 0, "mgp": 0, **{k: 0 for k in TOTALS}})
            if appeared(ln):
                t["gp"] += 1
                t["gs"] += 1 if ln.get("starter") else 0
                t["mgp"] += 1 if ln.get("min") is not None else 0
            for k in TOTALS:
                t[k] += ln.get(k) or 0
    return sorted(tot.values(), key=lambda t: (-t["pts"], t["name"]))


def roster_stamp(team):
    """When the roster was last read: kept in .cache/ (git-ignored), so a daily re-check that finds
    nothing new doesn't produce a commit."""
    return common.ROOT / ".cache" / f"roster-{team or 'team'}.txt"


def run(team, force=False):
    sched, _, _, events = load(team)
    stats_path = team_file(team, "stats.json")
    box_dir = team_file(team, "box"); box_dir.mkdir(exist_ok=True)
    on_disk = stats_path.read_text() if stats_path.exists() else ""
    prev = json.loads(on_disk) if on_disk else {}
    today = today_pt()
    finished = [e for e in events if e.end_date <= today]
    # A box score still missing/incomplete a week after the game is left alone (re-run with --force).
    recent = today - RETRY_DAYS
    missing = [e for e in finished if force or (e.end_date >= recent and not (box_dir / f"{e.id}.json").exists())]
    stamp = roster_stamp(team)
    roster_age = dt.datetime.now(TZ) - dt.datetime.fromisoformat(stamp.read_text().strip()) if stamp.exists() else None
    need_roster = force or not prev.get("roster") or roster_age is None or roster_age > ROSTER_MAX_AGE

    if missing or need_roster:
        page = fetch(sched["source_url"])           # one request maps every game to its box score
        aliases = {k.lower(): v.lower() for k, v in (sched.get("feed_aliases") or {}).items()}
        box_of = {(r["date"], norm(r["opponent"], aliases)): r.get("boxscore") for r in parse_page(page, sched["source_url"])}
        for e in missing:
            url = box_of.get((e.date, norm(e.opponent, aliases)))
            if not url:
                continue                              # scrimmage / not posted yet: try again next run
            lines = parse_box(fetch(url))
            ok, why = box_is_complete(lines, e)
            if not ok:
                print(f"{team}: box score {e.id} not final yet ({why}); will retry")
                continue
            if lines:
                (box_dir / f"{e.id}.json").write_text(json.dumps(
                    {"event": e.id, "date": e.date.isoformat(), "opponent": e.opponent, "boxscore": url, "lines": lines},
                    indent=1) + "\n")
                print(f"{team}: box score {e.id} ({len(lines)} players)")
            time.sleep(1)                             # polite to the athletics site
        if need_roster:
            roster_url = re.sub(r"/schedule.*$", "/roster", sched["source_url"])
            try:
                roster = parse_roster(fetch(roster_url))
                prev["roster"] = roster
                stamp.parent.mkdir(exist_ok=True)
                stamp.write_text(dt.datetime.now(TZ).isoformat(timespec="seconds") + "\n")
                print(f"{team}: roster {len(roster)} players")
            except Exception as err:
                print(f"::warning::{team} roster skipped: {err}")

    games = sorted((normalize_game(json.loads(p.read_text())) for p in box_dir.glob("*.json")), key=lambda g: g["date"])
    exclude = {e.id for e in events if e.type == "scrimmage"}
    out = {"roster": prev.get("roster", []), "games": games, "season": season_totals(games, exclude)}
    new = json.dumps(out, indent=1) + "\n"
    if new != on_disk:                                # compare with the file, not the (mutated) dict
        stats_path.write_text(new)
    print(f"{team}: {len(games)} box scores, {len(out['season'])} players")


def main(argv):
    for key in teams():
        try:
            run(key, force="--force" in argv)
        except Exception as err:                      # one team failing never blocks the other
            print(f"::warning::{key} stats skipped: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
