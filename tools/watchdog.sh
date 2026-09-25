#!/usr/bin/env bash
# Run this from ANOTHER machine (cron every 5 min). Alerts Slack when the dashboard heartbeat
# is stale or the dashboard is unreachable, so a dead box does not go unnoticed.
#
#   DASH_URL=http://box.tailnet.ts.net:8080 DASH_TOKEN=... SLACK_WEBHOOK=... tools/watchdog.sh
set -euo pipefail

: "${DASH_URL:?set DASH_URL}"
: "${DASH_TOKEN:?set DASH_TOKEN}"
: "${SLACK_WEBHOOK:?set SLACK_WEBHOOK}"
STALE_S="${STALE_S:-600}"

alert() {
  curl -fsS -X POST -H 'Content-Type: application/json' \
    --data "$(printf '{"text":"WATCHDOG: %s"}' "$1")" "$SLACK_WEBHOOK" >/dev/null
  echo "alerted: $1"
}

if ! body="$(curl -fsS --max-time 15 "$DASH_URL/api/status?token=$DASH_TOKEN")"; then
  alert "animaleyes dashboard unreachable at $DASH_URL"
  exit 1
fi

age="$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("heartbeat_age_s") or 10**9)')"
if [ "$age" -gt "$STALE_S" ]; then
  alert "animaleyes heartbeat stale: ${age}s"
  exit 1
fi
echo "ok: heartbeat ${age}s ago"
