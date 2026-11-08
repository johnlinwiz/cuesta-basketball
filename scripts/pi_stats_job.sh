#!/bin/sh
# Player stats from a home connection (cron on the Pi4). cuestaathletics.com answers GitHub's
# runners with HTTP 405, so box scores and the roster are read here and only the data is pushed:
#   data/teams/<t>/box/*.json, data/teams/<t>/stats.json
# That push triggers the "daily reminders" workflow, which rebuilds docs/ (site + stats.json) and
# posts the opt-in box-score comments. Nothing else is touched, so it can't conflict with the
# workflow's own commits.
#
# fetch_stats.py is polite: it only requests a game's box score until it has a complete one (up to
# a week after the game), and the roster once a day, so most runs make no requests at all.
#
#   */30 * * * * $HOME/cuesta-basketball/scripts/pi_stats_job.sh >> $HOME/.cache/cuesta-stats.log 2>&1
set -eu
cd "$(dirname "$0")/.."
echo "== $(date '+%F %T')"
git pull -q --rebase
python3 scripts/fetch_stats.py
git add data/teams/*/box data/teams/*/stats.json 2>/dev/null || true
if git diff --cached --quiet; then
  echo "no change"; exit 0
fi
git commit -q -m "chore: box scores and roster (pi)"
for try in 1 2 3; do
  if git push -q; then echo "pushed"; exit 0; fi
  sleep $((try * 15)); git pull -q --rebase      # the workflow pushed meanwhile: replay on top
done
echo "push failed" >&2; exit 1
