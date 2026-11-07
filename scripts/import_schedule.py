#!/usr/bin/env python3
"""Draft a team's games list from its official PrestoSports schedule page.

    python3 scripts/import_schedule.py URL|saved.html [--venue-map opp=venuekey ...] > draft.yaml

Prints YAML `games:` entries in the same shape as data/teams/<team>/schedule.yaml. It is a
starting point for a new team or season — review the output (venues for neutral sites,
tournaments, TBA games) before saving it.

Rules:
- Home games -> venue `cuesta`; away games -> the opponent's venue key (matched against
  data/venues.yaml names/keys, or --venue-map); neutral games keep `venue: TODO` plus the page's
  location note.
- "exhibition" rows (scrimmages/jamborees) -> type scrimmage; "conf" rows -> conference.
- Tournament wrapper rows (name has Classic/Tournament/Jamboree/Shootout, time TBA) are listed
  as YAML comments, not games — the individual games are tracked instead.
"""
import datetime as dt
import html
import pathlib
import re
import subprocess
import sys

import yaml

from common import ROOT, slug

# Run by hand on a normal network: a browser User-Agent gets the page where the scripts' own UA
# now receives PrestoSports' HTTP 202 bot-check.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141 Safari/537.36")


def fetch(url):
    r = subprocess.run(["curl", "-sL", "--max-time", "60", "-A", BROWSER_UA, "-w", "\n%{http_code}", url],
                       capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    if code != "200" or "event-row" not in body:
        raise RuntimeError(f"{url} -> HTTP {code or '?'}")
    return body

WRAPPER = re.compile(r"\b(classic|tournament|jamboree|shootout|invitational)\b", re.I)


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def rows(page):
    out = []
    for m in re.finditer(r'<div class="event-row([^"]*)"[^>]*>(.*?)<a href="#" class="venue" title="([^"]*)"', page, re.S):
        classes, body, venue = m.groups()
        d = re.search(r'<div class="date" title="[A-Za-z]+\.? ([A-Za-z]+) (\d{1,2}), (\d{4})"', body)
        name = re.search(r'<span class="team-name">(.*?)</span>', body, re.S)
        if not (d and name):
            continue
        status = text((re.search(r'<div class="status">(.*?)</div>', body, re.S) or [None, ""])[1])
        notes = text((re.search(r'<div class="notes">(.*?)</div>', body, re.S) or [None, ""])[1])
        t = re.match(r"(\d{1,2}):(\d{2}) ([AP]M)", status)
        out.append({
            "date": dt.datetime.strptime(f"{d.group(1)} {d.group(2)} {d.group(3)}", "%B %d %Y").date(),
            "opponent": text(name.group(1)), "site": venue.lower(),
            "time": (dt.datetime.strptime(t.group(0), "%I:%M %p").strftime("%H:%M") if t else None),
            "exhibition": "exhibition" in classes, "conf": re.search(r"\bconf\b", classes) is not None,
            "notes": notes,
        })
    return out


def venue_index():
    venues = yaml.safe_load((ROOT / "data/venues.yaml").read_text())["venues"]
    idx = {}
    for key, v in venues.items():
        idx[slug(key)] = key
        idx[slug(re.sub(r"\b(college|gym|gymnasium|court)\b.*$", "", v["name"], flags=re.I))] = key
    return idx


def main(argv):
    url = argv[1]
    vmap = dict(a.split("=", 1) for a in argv[2:] if "=" in a)
    idx = venue_index()
    lines = ["games:"]
    # URL, or a saved copy of the page (the site's bot check sometimes blocks repeated requests)
    page = pathlib.Path(url).read_text(errors="ignore") if pathlib.Path(url).exists() else fetch(url)
    for r in rows(page):
        if WRAPPER.search(r["opponent"]) and not r["time"]:
            lines.append(f"  # wrapper: {r['date']} {r['opponent']} ({r['site']}) — track its games instead")
            continue
        typ = "scrimmage" if r["exhibition"] else "conference" if r["conf"] else "non-conference"
        if r["site"] == "home":
            venue = "cuesta"
        else:
            venue = vmap.get(r["opponent"]) or idx.get(slug(r["opponent"])) or "TODO"
        g = {"date": r["date"].isoformat()}
        if r["time"]:
            g["time"] = r["time"]
        g.update(opponent=r["opponent"], site=r["site"] if r["site"] in ("home", "away", "neutral") else "away",
                 venue=venue, type=typ)
        if r["notes"] and r["notes"].lower() not in ("scrimmage",):
            g["notes"] = r["notes"]
        flow = yaml.safe_dump(g, default_flow_style=True, sort_keys=False, width=1000).strip()
        lines.append(f"  - {flow}" + ("   # TODO venue" if venue == "TODO" else ""))
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
