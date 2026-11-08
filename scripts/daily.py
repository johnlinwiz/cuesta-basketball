#!/usr/bin/env python3
"""Daily job: post reminder comments on upcoming games' issues and close finished ones.

Run by .github/workflows/daily.yml. Safe to run repeatedly — each reminder comment
carries a hidden marker so it is posted at most once per issue.
"""
import datetime as dt
import json
import sys

import gh
from common import (PAGES_URL, fmt_dur, load, load_issue_map, maps_dir, maps_place, record_label, result_games,
                    result_label, team_file, today_pt, teams)
from fetch_stats import normalize_game

BUFFER_MIN = 30  # arrive this long before tip-off
BOX_COMMENT_DAYS = 3   # box scores arriving later than this after the game don't get a comment (no backfill spam)


def leave_by(e, origin):
    d = e.drive.get(origin)
    if not d or e.all_day:
        return None
    return e.start - dt.timedelta(minutes=d["minutes"] + BUFFER_MIN)


def reminder_body(e, days, mentions):
    when = {0: "**Today!**", 1: "**Tomorrow**"}.get(days, f"**In {days} days**")
    lines = [f"🔔 {' '.join('@' + m for m in mentions)} {when} — Cuesta {e.matchup}{e.conf_star}", "",
             f"- 🕑 {e.date_label} · {e.time_label} PT",
             f"- 📍 [{e.venue['name']}]({maps_place(e.venue)}) — {e.venue['address']}"]
    for origin, label in (("pasadena", "Pasadena"), ("slo", "SLO")):
        d = e.drive.get(origin)
        if not d:
            continue
        lb = leave_by(e, origin)
        extra = f" → leave by **{lb.strftime('%-I:%M %p')}**" if lb and d["minutes"] > 20 else ""
        lines.append(f"- 🚗 From {label}: [{fmt_dur(d['minutes'])}]({maps_dir(origin, e.venue)}) "
                     f"· {d['miles']} mi{extra}")
    if e.video:
        lines.append(f"- 📺 **Watch live:** [{e.video_label}]({e.video})")
    if e.audio:
        lines.append(f"- 🎙️ Audio: {e.audio}")
    if e.stats:
        lines.append(f"- 📊 [Live stats / box score]({e.stats})")
    elif e.live_stats:
        lines.append("- 📊 Live stats available")
    if not e.site_confirmed:
        lines.append("- ⚠️ Home/away inferred from poster — confirm before you go")
    if any(e.drive.get(o) for o in ("pasadena", "slo")) and not e.all_day:
        lines.append(f"\n<sub>Leave-by = OSRM drive estimate + {BUFFER_MIN} min buffer; check live traffic.</sub>")
    lines.append(f"\n<!-- reminder:{days}d -->")
    return "\n".join(lines)


def result_body(e, games, mentions, record):
    lines = []
    for g in games:
        icon = {"W": "🟢", "L": "🔴"}.get(g["outcome"], "⚪")
        who = f" vs {g['opponent']}" if g.get("opponent") else ""
        links = " · ".join(f"[{lbl}]({g[k]})" for k, lbl in (("boxscore", "Box score"), ("recap", "Recap")) if g.get(k))
        lines.append(f"{icon} **Final{who}: Cuesta {result_label(g)}**" + (f" — {links}" if links else ""))
    head = f"🏁 {' '.join('@' + m for m in mentions)} Cuesta {e.matchup}{e.conf_star}"
    tail = [f"", f"Season record: **{record}**"] if record and e.type != "scrimmage" else []
    return "\n".join([head, "", *lines, *tail, "", "<!-- result -->"])


def stat_line(l):
    if l.get("min") is not None and l["min"] <= 0:
        return "did not play"
    mins = f" · {l['min']} min" if l.get("min") else ""
    return (f"{l['pts']} pt{'' if l['pts'] == 1 else 's'} · {l['reb']} reb · {l['ast']} ast{mins} "
            f"(FG {l['fgm']}-{l['fga']}, 3PT {l['fg3m']}-{l['fg3a']}, FT {l['ftm']}-{l['fta']})")


def box_body(e, box, follow, mentions, team):
    """Followed players' lines + top scorers, posted once the box score is in (it often lands after the final)."""
    games = result_games(e)
    res = f" ({', '.join(result_label(g) for g in games)})" if games else ""
    by_name = {l["name"].lower(): l for l in box["lines"]}
    lines = [f"📈 {' '.join('@' + m for m in mentions)} Box score — Cuesta {e.matchup}{res}", ""]
    for name in follow:
        l = by_name.get(name.lower())
        lines.append(f"- ⭐ **{l['name'] if l else name}**: {stat_line(l) if l else 'did not play'}")
    top = sorted((l for l in box["lines"] if l.get("pts")), key=lambda l: -l["pts"])[:3]
    if top:
        lines.append("- 🏀 Top scorers: " + " · ".join(f"{l['name']} {l['pts']}" for l in top))
    links = [f"[Full box score]({box['boxscore']})"] if box.get("boxscore") else []
    links.append(f"[Season stats]({PAGES_URL}?team={team}#stats)" if team else f"[Season stats]({PAGES_URL}#stats)")
    return "\n".join([*lines, "", " · ".join(links), "", "<!-- box -->"])


def load_box(team, e):
    p = team_file(team, "box") / f"{e.id}.json"
    return normalize_game(json.loads(p.read_text())) if p.exists() else None


def main():
    for key in teams():
        if key:
            print(f"== {key}")
        run(key)
    return 0


def run(team):
    sched, _, _, events = load(team)
    issues = load_issue_map(team)
    mentions = sched.get("notify", [])
    today = today_pt()
    for e in events:
        n = issues.get(e.id)
        if not n:
            continue
        days = (e.date - today).days
        if days in sched["reminder_days_before"]:
            marker = f"<!-- reminder:{days}d -->"
            comments = gh.api(f"issues/{n}/comments?per_page=100", paginate=True)
            if not any(marker in (c.get("body") or "") for c in comments):
                gh.api(f"issues/{n}/comments", "POST", {"body": reminder_body(e, days, mentions)})
                print(f"#{n}: posted {days}d reminder")
        # Finished: the day has passed, or (single-day games) a final score is already in tonight.
        # Multi-day tournaments wait for the day after, so day-1 scores don't close them early.
        same_day_final = e.date == e.end_date == today and bool(result_games(e))
        if e.end_date < today or same_day_final:
            games = result_games(e)
            iss = gh.api(f"issues/{n}")
            comments = None
            if games:   # post the final score once (scores often land after the issue already closed)
                comments = gh.api(f"issues/{n}/comments?per_page=100")
                if not any("<!-- result -->" in (c.get("body") or "") for c in comments):
                    gh.api(f"issues/{n}/comments", "POST", {"body": result_body(e, games, mentions, record_label(events))})
                    print(f"#{n}: posted result")
            follow = sched.get("follow") or []
            box = load_box(team, e) if follow and (today - e.end_date).days <= BOX_COMMENT_DAYS else None
            if box:     # opt-in (`follow:` in schedule.yaml): one comment with the followed players' lines
                comments = comments if comments is not None else gh.api(f"issues/{n}/comments?per_page=100")
                if not any("<!-- box -->" in (c.get("body") or "") for c in comments):
                    gh.api(f"issues/{n}/comments", "POST", {"body": box_body(e, box, follow, mentions, team)})
                    print(f"#{n}: posted box score")
            if iss["state"] == "open":
                if not games:
                    gh.api(f"issues/{n}/comments", "POST", {"body":
                           "🏁 Game day has passed — closing. The final score will be added here once it's posted "
                           "(scrimmages usually aren't scored; add `result: \"W 70-62\"` in schedule.yaml if you know it)."})
                gh.api(f"issues/{n}", "PATCH", {"state": "closed", "state_reason": "completed"})
                print(f"#{n}: closed")


if __name__ == "__main__":
    sys.exit(main())
