#!/usr/bin/env python3
"""Create/update one GitHub issue per game (the "reminder" for that event).

Idempotent: each issue body carries a hidden `<!-- event-id: ... -->` marker, so
re-running updates titles/bodies/labels in place instead of duplicating.
Writes the event-id -> issue-number map to the team's issues.json. With several teams each issue
also carries `<!-- team: <key> -->`; unmarked (older) issues belong to the first team, and a team's
sync never touches another team's issues.
"""
import calendar
import json

import gh
from common import PAGES_URL, ROOT, TYPE_LABEL, fmt_dur, load, maps_dir, maps_place, team_file, teams, today_pt  # noqa: F401 (ROOT: tests patch it)

TEAM_COLORS = {"mbb": "1d76db", "wbb": "d4c5f9"}

LABELS = {
    "home": ("0b6e3a", "Home game at Cuesta"),
    "away": ("5b6670", "Away game"),
    "tournament": ("c9a227", "Multi-day tournament"),
    "conference": ("6f42c1", "Western State Conference game"),
    "non-conference": ("bfd4f2", "Non-conference game"),
    "scrimmage": ("e4e669", "Scrimmage"),
    "site-unconfirmed": ("d93f0b", "Home/away inferred from poster — confirm"),
}


def labels_for(e):
    ls = ["tournament" if e.is_tournament else e.site]
    ls.append(e.type if e.type != "tournament" else None)
    if not e.site_confirmed:
        ls.append("site-unconfirmed")
    return [l for l in ls if l]


def drive_row(e, origin, label):
    d = e.drive.get(origin)
    if not d:
        return f"| **🚗 From {label}** | — |"
    return f"| **🚗 From {label}** | [{fmt_dur(d['minutes'])}]({maps_dir(origin, e.venue)}) · {d['miles']} mi |"


def body_for(e, sched):
    when = e.date_label + (", " + str(e.date.year))
    when += "" if e.all_day else f" · **{e.time_label} PT**"
    rows = [
        f"## 🏀 Cuesta {e.matchup}{e.conf_star}", "",
        "| | |", "|---|---|",
        f"| **When** | {when} |",
        f"| **Game** | {TYPE_LABEL[e.type]} — {e.site_label} |",
        f"| **Venue** | [{e.venue['name']}]({maps_place(e.venue)}) |",
        f"| **Address** | {e.venue['address']} |",
        drive_row(e, "pasadena", "Pasadena"),
        drive_row(e, "slo", "SLO"),
    ]
    if e.video:
        rows.append(f"| **📺 Watch live** | [{e.video_label}]({e.video})"
                    + (" <sub>(per-game link usually posted on game day)</sub>" if e.video_is_channel else "") + " |")
    if e.audio:
        rows.append(f"| **🎙️ Audio** | [{e.audio}]({e.audio}) |")
    if e.stats:
        rows.append(f"| **📊 Live stats** | [Live stats / box score]({e.stats}) |")
    elif e.live_stats:
        rows.append("| **📊 Live stats** | Yes (link not posted yet) |")
    if e.notes:
        rows.append(f"| **Note** | {e.notes} |")
    rows.append("")
    if not e.site_confirmed:
        rows += ["> [!WARNING]", "> Home/away is inferred — the poster had no `@`/`Vs` for this game. "
                 "Confirm before traveling.", ""]
    if e.all_day:
        rows += ["> [!NOTE]", "> Tournament schedule/times TBA — update `data/schedule.yaml` when the bracket is out.", ""]
    days = ", ".join(f"{d} days" for d in sched["reminder_days_before"] if d)
    rows += [
        f"**Add to calendar:** [Google Calendar]({e.gcal_link()}) · "
        f"[Season .ics feed]({PAGES_URL}{sched['ics']}) · [Live schedule]({sched['site_url']})", "",
        f"🔔 A reminder comment is posted {days} before and on game day; this issue closes after the game.",
        "", "<sub>Drive times: OSRM free-flow estimate (no traffic). Links open live Google Maps directions.</sub>",
        "", f"<!-- event-id: {e.id} -->",
        *([f"<!-- team: {sched['key']} -->"] if sched.get("key") else []),
    ]
    return "\n".join(rows)


def ensure_labels(sched=None):
    have = {l["name"] for l in gh.api("labels?per_page=100", paginate=True)}
    wanted = dict(LABELS)
    if sched and sched.get("key"):
        wanted[sched["key"]] = (TEAM_COLORS.get(sched["key"], "ededed"), sched["team"])
    for name, (color, desc) in wanted.items():
        if name not in have:
            gh.api("labels", "POST", {"name": name, "color": color, "description": desc})


def ensure_milestones(events):
    have = {m["title"]: m["number"] for m in gh.api("milestones?state=all&per_page=100", paginate=True)}
    out = {}
    for y, m in sorted({(e.date.year, e.date.month) for e in events}):
        title = f"{calendar.month_name[m]} {y}"
        if title not in have:
            last = calendar.monthrange(y, m)[1]
            ms = gh.api("milestones", "POST", {"title": title, "due_on": f"{y}-{m:02d}-{last}T23:00:00Z",
                                                 "description": f"Cuesta basketball games in {title}"})
            have[title] = ms["number"]
        out[(y, m)] = have[title]
    return out


def issue_title(e, sched):
    return f"[{sched['short']}] {e.title()}" if sched.get("key") else e.title()


def team_of(body, default):
    return body.split("<!-- team: ", 1)[1].split(" -->", 1)[0] if "<!-- team: " in body else default


def sync_team(key, all_issues, default_team):
    sched, _, _, events = load(key)
    ensure_labels(sched)
    milestones = ensure_milestones(events)
    existing = {}
    for iss in all_issues:
        body = iss.get("body") or ""
        if "<!-- event-id: " in body and "pull_request" not in iss:
            if key and team_of(body, default_team) != key:
                continue                              # another team's issue: never touch it
            eid = body.split("<!-- event-id: ", 1)[1].split(" -->", 1)[0]
            existing[eid] = iss["number"]

    issue_map = {}
    for e in events:
        labels = labels_for(e) + ([key] if key else [])
        payload = {"title": issue_title(e, sched), "body": body_for(e, sched), "labels": labels,
                   "milestone": milestones[(e.date.year, e.date.month)]}
        if e.id in existing:
            n = existing[e.id]
            gh.api(f"issues/{n}", "PATCH", payload)
            print(f"updated #{n} {payload['title']}")
        elif e.end_date < today_pt():
            continue                       # already played when first imported: no reminder issue to open+close
        else:
            n = gh.api("issues", "POST", payload)["number"]
            print(f"created #{n} {payload['title']}")
        issue_map[e.id] = n
    close_superseded(sched, events, existing, issue_map)
    team_file(key, "issues.json").write_text(json.dumps(issue_map, indent=2) + "\n")


def main():
    keys = teams()
    all_issues = gh.api("issues?state=all&per_page=100", paginate=True)
    for key in keys:
        sync_team(key, all_issues, default_team=keys[0])


def close_superseded(sched, events, existing, issue_map):
    """Close open issues whose event is no longer on the schedule (cancelled/moved)."""
    current = {e.id for e in events}
    replaced_by = {}
    for e in events:
        for old in e.replaces:
            replaced_by.setdefault(old, []).append(e)
    mentions = " ".join("@" + m for m in sched.get("notify", []))
    for eid, n in existing.items():
        if eid in current:
            continue
        iss = gh.api(f"issues/{n}")
        if iss["state"] != "open":
            continue
        new = replaced_by.get(eid, [])
        if new:
            links = ", ".join(f"#{issue_map[x.id]} ({x.title()})" for x in new if x.id in issue_map)
            msg = f"📅 {mentions} **Schedule change** — this event was replaced by {links}."
        else:
            msg = f"❌ {mentions} **Removed from the official schedule** (cancelled or moved). Closing."
        gh.api(f"issues/{n}/comments", "POST", {"body": msg})
        gh.api(f"issues/{n}", "PATCH", {"state": "closed", "state_reason": "not_planned"})
        print(f"closed #{n} ({eid}) — superseded")


if __name__ == "__main__":
    main()
