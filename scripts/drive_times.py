#!/usr/bin/env python3
"""Compute road distance + drive time from each origin to each venue via OSRM.

Writes data/drive_times.json (each origin -> each venue) and data/drive_pairs.json
(venue -> venue for back-to-back games on the same/next day, every team plus the combined view).
OSRM times are free-flow (no traffic) and run a bit conservative versus Google; the docs also
link Google Maps directions for live traffic estimates.

    python3 scripts/drive_times.py            # both files (recomputes origins)
    python3 scripts/drive_times.py --pairs    # only fill in missing venue->venue pairs (cheap; daily job)
"""
import json
import pathlib
import subprocess
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
OSRM = "https://router.project-osrm.org/route/v1/driving/{a};{b}?overview=false"


def route(o, v):
    url = OSRM.format(a=f"{o['lon']},{o['lat']}", b=f"{v['lon']},{v['lat']}")
    # curl rather than urllib: macOS system Python's old SSL can't handshake with OSRM.
    raw = subprocess.run(["curl", "-sf", "--max-time", "30", "-A", "cuesta-mbb-schedule", url],
                         check=True, capture_output=True, text=True).stdout
    data = json.loads(raw)
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error for {url}: {data}")
    leg = data["routes"][0]
    return {"miles": round(leg["distance"] / 1609.344), "minutes": round(leg["duration"] / 60)}


def fill_pairs():
    """Add any venue->venue pairs the current schedules need; keep the ones already cached."""
    from common import legs, load, load_pairs, pair_key, teams
    cfg = yaml.safe_load((ROOT / "data/venues.yaml").read_text())["venues"]
    pairs, everything, needed = load_pairs(), [], set()
    for key in teams():
        events = load(key)[3]; everything += events
        for prev, nxt in legs(events):
            needed.add((prev.venue_key, nxt.venue_key))
    for prev, nxt in legs(everything):                     # the combined "Both" view
        needed.add((prev.venue_key, nxt.venue_key))
    added = 0
    for a, b in sorted(needed):
        k = pair_key(a, b)
        if a == b:
            pairs[k] = {"miles": 0, "minutes": 0}
        elif k not in pairs:
            pairs[k] = route(cfg[a], cfg[b]); added += 1
            time.sleep(1)
            print("pair", k, pairs[k])
    (ROOT / "data/drive_pairs.json").write_text(json.dumps(dict(sorted(pairs.items())), indent=1) + "\n")
    print(f"{len(needed)} back-to-back pairs needed, {added} newly routed")



def main():
    import sys
    if "--pairs" in sys.argv:
        fill_pairs()
        return
    cfg = yaml.safe_load((ROOT / "data/venues.yaml").read_text())
    out = {}
    for key, v in cfg["venues"].items():
        out[key] = {}
        for okey, o in cfg["origins"].items():
            out[key][okey] = route(o, v)
            time.sleep(1)  # be polite to the public demo server
        print(key, out[key])
    (ROOT / "data/drive_times.json").write_text(json.dumps(out, indent=2) + "\n")
    fill_pairs()


if __name__ == "__main__":
    main()
