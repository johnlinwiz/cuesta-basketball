#!/usr/bin/env python3
"""One-page printable season schedule (PDF + PNG preview) for a team, poster style.

    .venv/bin/python scripts/poster.py wbb            # -> docs/cuesta-wbb-schedule.pdf (+ .png)
    .venv/bin/python scripts/poster.py mbb --out ~/Downloads/mbb.pdf

Laid out like the @cuesta_mbb season poster: month blocks, "@" = away, "Vs" = home,
"*" = Western State Conference, times Pacific. Games already played show the final score.
A QR code links to the live schedule for that team. Rendered with headless Chromium
(Playwright, already a dev dependency) and auto-scaled to fit one Letter page.
"""
import argparse
import html
import io
import pathlib
import sys

import segno

from common import PAGES_URL, ROOT, load, record_label, result_games, result_label, today_pt


def esc(s):
    return html.escape(str(s or ""), quote=True)


def game_line(e, today):
    day = f"{e.date:%d}" + (f"–{e.end_date:%d}" if e.end_date != e.date else "")
    dow = e.date.strftime("%a").upper()
    if e.is_tournament and e.all_day:
        opp = esc(e.opponent)
        prefix = ""
    else:
        prefix = "@" if e.site == "away" else "Vs" if e.site == "home" else "vs"
        opp = esc(e.opponent)
    star = "*" if e.type == "conference" else ""
    sub = []
    if e.type == "scrimmage":
        sub.append("Scrimmage")
    note = (e.notes or "").split(";")[0]
    # Printed schedule: keep where/what context, drop housekeeping (stream, replacement, venue repeats)
    if note and not note.startswith(("Replaces", "Played at")) and "Stream" not in note and not (e.is_tournament and e.all_day):
        sub.append(note)
    if e.venue_key == "robgym":
        sub.append("at Rob Gym (UCSB)")
    if e.site == "neutral" or (e.is_tournament and e.all_day):
        sub.append(f"at {e.venue['name'].split(' — ')[0]}, {e.venue['city'].split(',')[0]}")
    res = result_games(e)
    if res:
        right = " · ".join(f'<span class="res {g["outcome"]}">{esc(result_label(g, short=True))}</span>' for g in res)
    elif e.end_date < today:
        right = '<span class="muted">Final</span>'
    else:
        right = esc(e.time_label if not e.all_day else "TBA")
    return (f'<div class="g{" home" if e.site == "home" else ""}"><span class="d">{day}</span><span class="w">{dow}</span>'
            f'<span class="o"><b class="pre">{prefix}</b> {opp}<b class="star">{star}</b>'
            + (f'<small>{esc(" · ".join(sub))}</small>' if sub else "") + f'</span><span class="t">{right}</span></div>')


def build_html(sched, events, today):
    months = []
    for e in events:
        key = (e.date.year, e.date.month)
        if not months or months[-1][0] != key:
            months.append((key, e.date.strftime("%B").upper(), []))
        months[-1][2].append(e)
    site = sched["site_url"]
    qr = io.BytesIO()
    segno.make(site, error="m").save(qr, kind="svg", scale=4, border=1, dark="#0b6e3a")
    qr_svg = qr.getvalue().decode()
    rec = record_label(events)
    women = "Women" in sched["team"]
    blocks = "".join(f'<section class="m"><h2>{name}</h2>{"".join(game_line(e, today) for e in evs)}</section>'
                     for _, name, evs in months)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: Letter; margin: 0 }}
* {{ box-sizing:border-box }}
html, body {{ margin:0 }} body {{ overflow:hidden }}
body {{ width:8.5in; height:11in; font-family:"Oswald","Arial Narrow","Helvetica Neue",Arial,sans-serif;
        background:#0f1512; color:#eef3ef; -webkit-print-color-adjust:exact; print-color-adjust:exact }}
#page {{ padding:.42in .45in .35in }}
header {{ display:flex; align-items:center; gap:.22in; border-bottom:4px solid #c9a227; padding-bottom:.12in }}
.ball {{ width:.95in; height:.95in; flex:none }}
.kicker {{ font-size:15pt; letter-spacing:.18em; color:#c9a227; font-weight:700 }}
h1 {{ font-size:34pt; line-height:1; margin:.02in 0; letter-spacing:.01em; text-transform:uppercase; font-weight:900 }}
.season {{ font-size:19pt; font-weight:800; letter-spacing:.06em; color:#9fd8b6 }}
.cols {{ columns:2; column-gap:.35in; margin-top:.18in }}
.m {{ break-inside:avoid; margin:0 0 .14in }}
h2 {{ font-size:15pt; letter-spacing:.12em; color:#c9a227; margin:0 0 .04in; border-bottom:1px solid #c9a22766 }}
.g {{ display:grid; grid-template-columns:.42in .42in 1fr auto; align-items:baseline; gap:.06in;
      font-size:11.2pt; padding:.028in 0; border-bottom:1px dotted #ffffff1f }}
.g.home .o {{ color:#ffffff }} .g:not(.home) .o {{ color:#d6dfda }}
.d {{ font-weight:800; font-size:13pt; color:#ffffff }} .w {{ color:#9fb3a8; font-size:9.5pt; letter-spacing:.05em }}
.pre {{ color:#c9a227; font-weight:800 }} .g.home .pre {{ color:#6fd39b }}
.star {{ color:#c9a227 }}
.o small {{ display:block; font-size:8.3pt; color:#9fb3a8; line-height:1.15 }}
.t {{ font-weight:700; white-space:nowrap; font-size:10.5pt }}
.res {{ padding:0 .05in; border-radius:3px }} .res.W {{ background:#1f8f4e; color:#fff }} .res.L {{ background:#7d2a22; color:#fff }}
.muted {{ color:#9fb3a8; font-weight:400 }}
footer {{ display:flex; gap:.22in; align-items:center; border-top:2px solid #c9a22799; padding-top:.1in; margin-top:.02in }}
footer .qr svg {{ width:1.05in; height:1.05in; background:#fff; border-radius:6px }}
.legend {{ font-size:9.5pt; color:#c9d5ce; line-height:1.5 }}
.legend b {{ color:#c9a227 }} .legend .url {{ font-size:10.5pt; color:#ffffff; font-weight:700 }}
.rec {{ font-size:12pt; font-weight:800; color:#ffffff }}
</style></head><body><div id="page">
<header>
  <svg class="ball" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0b6e3a"/>
    <circle cx="32" cy="32" r="22" fill="#f28c28" stroke="#1d2421" stroke-width="2.5"/>
    <g fill="none" stroke="#1d2421" stroke-width="2.5" stroke-linecap="round"><path d="M32 10v44M10 32h44"/>
    <path d="M17 16.5c6 4.5 9 10 9 15.5s-3 11-9 15.5"/><path d="M47 16.5c-6 4.5-9 10-9 15.5s3 11 9 15.5"/></g></svg>
  <div><div class="kicker">CUESTA COLLEGE COUGARS</div>
    <h1>{"Women's" if women else "Men's"} Basketball</h1>
    <div class="season">{esc(sched["season"].replace("-", "–20"))} SEASON SCHEDULE</div></div>
</header>
<div class="cols">{blocks}</div>
<footer>
  <div class="qr">{qr_svg}</div>
  <div class="legend">
    {f'<div class="rec">Record: {esc(rec)}</div>' if rec else ''}
    <b>Vs</b> home (Stork Gym, Cuesta College, San Luis Obispo) · <b>@</b> away · <b>*</b> Western State Conference · times Pacific<br>
    Live schedule, drive times, streams &amp; reminders:<br><span class="url">{esc(site.replace("https://", ""))}</span><br>
    <span style="color:#7f948a">As of {today:%b %-d, %Y} — check the live site for changes.</span>
  </div>
</footer></div></body></html>"""


def render(html_text, pdf_path, png_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 816, "height": 1056})          # 8.5x11in at 96 dpi
        pg.set_content(html_text, wait_until="load")
        # Scale-to-fit (up to 118%) so it is always exactly one full page, however many games the team has.
        scale = pg.evaluate("""() => { const el = document.getElementById('page');
            // Too tall: shrink uniformly. Short: open up row spacing until the page is full.
            // CSS zoom (not transform): it shrinks the laid-out height too, so print never spills to page 2.
            if (el.scrollHeight > 1056) {
              let s = 1056 / el.scrollHeight;
              for (let i = 0; i < 20; i++) { el.style.zoom = s; if (el.getBoundingClientRect().height <= 1054) break; s *= 0.98; }
              return s;
            }
            const rows = [...document.querySelectorAll('.g')], months = [...document.querySelectorAll('.m')];
            let pad = 2.7;
            for (let i = 0; i < 80 && el.scrollHeight <= 1050; i++) {
              pad += 0.25;
              rows.forEach(r => { r.style.paddingTop = r.style.paddingBottom = pad + 'px'; });
              months.forEach(m => m.style.marginBottom = (13 + pad * 2) + 'px');
            }
            if (el.scrollHeight > 1050) { pad -= 0.25;
              rows.forEach(r => { r.style.paddingTop = r.style.paddingBottom = pad + 'px'; });
              months.forEach(m => m.style.marginBottom = (13 + pad * 2) + 'px'); }
            return 1 + pad / 100; }""")
        pg.pdf(path=str(pdf_path), format="Letter", print_background=True, prefer_css_page_size=True,
               margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        pg.screenshot(path=str(png_path), full_page=False)
        b.close()
    return scale


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("team", nargs="?", default="wbb")
    ap.add_argument("--out", help="PDF path (default docs/cuesta-<team>-schedule.pdf)")
    a = ap.parse_args(argv)
    sched, _, _, events = load(a.team)
    pdf = pathlib.Path(a.out).expanduser() if a.out else ROOT / "docs" / f"cuesta-{a.team}-schedule.pdf"
    png = pdf.with_suffix(".png")
    scale = render(build_html(sched, events, today_pt()), pdf, png)
    print(f"{pdf}  ({len(events)} games, fit {scale:.3f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
