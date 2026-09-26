#!/usr/bin/env bash
#
# Make the dashboard reachable from a phone, two ways, with no inbound port on the router:
#
#   1. Cloudflare Tunnel: adds <hostname> to the tunnel robot-computer already runs on this
#      box (one cloudflared service, one config, one more ingress rule). TLS at Cloudflare.
#   2. Tailscale Serve: https://<this box>.<tailnet>.ts.net:8443 for devices on the tailnet.
#
# Both proxy to 127.0.0.1:8081, where the dashboard asks for basic auth on every route.
#
#   tools/expose.sh grrr.threaditate.com
#
# POC: basic auth is the only gate and nothing rate-limits it. Put Cloudflare Access in
# front of the hostname (see the note printed at the end) before trusting it.
set -euo pipefail

HOSTNAME_="${1:-}"
LOCAL="http://127.0.0.1:8081"
CONFIG="/etc/cloudflared/config.yml"
TS_PORT="${TS_PORT:-8443}"

if [[ -z "$HOSTNAME_" ]]; then
  echo "usage: tools/expose.sh <hostname on a Cloudflare zone, e.g. grrr.threaditate.com>" >&2
  exit 1
fi

step() { printf '\n=== %s\n' "$1"; }

step "checking the dashboard is up and asks for a login"
curl -fsS -o /dev/null "$LOCAL/healthz" \
  || { echo "nothing on $LOCAL -- run 'docker compose up -d --build' first" >&2; exit 1; }
status="$(curl -sS -o /dev/null -w '%{http_code}' "$LOCAL/" || echo 000)"
if [[ "$status" != "401" ]]; then
  echo "$LOCAL/ answered $status without credentials, not 401. Refusing to publish it." >&2
  exit 1
fi

step "adding $HOSTNAME_ to the existing tunnel"
[[ -f "$CONFIG" ]] || { echo "$CONFIG not found; is cloudflared set up on this box?" >&2; exit 1; }
if grep -q "hostname: $HOSTNAME_\$" "$CONFIG"; then
  echo "already present"
else
  backup="$CONFIG.bak.$(date +%Y%m%d%H%M%S)"
  sudo cp "$CONFIG" "$backup"
  echo "backed up to $backup"
  # Insert the rule just above the catch-all, which must stay last.
  tmp="$(mktemp)"
  awk -v host="$HOSTNAME_" -v svc="$LOCAL" '
    (/^  # Anything not matching/ || /^  - service: http_status:404/) && !done {
      print "  - hostname: " host
      print "    service: " svc
      print "    originRequest:"
      print "      # The live view is a long-lived MJPEG response; do not buffer it."
      print "      disableChunkedEncoding: false"
      print "      connectTimeout: 10s"
      done = 1
    }
    { print }
    END { if (!done) exit 1 }
  ' "$CONFIG" > "$tmp" || { echo "no catch-all rule found in $CONFIG; add the rule by hand" >&2; exit 1; }
  if ! cloudflared tunnel --config "$tmp" ingress validate; then
    echo "the new config does not validate; $CONFIG is unchanged" >&2
    exit 1
  fi
  sudo install -m 0644 "$tmp" "$CONFIG"
  rm -f "$tmp"
fi

TUNNEL_ID="$(sudo awk '/^tunnel:/ {print $2}' "$CONFIG")"
cloudflared tunnel route dns "$TUNNEL_ID" "$HOSTNAME_" || true
sudo systemctl restart cloudflared
sleep 3
systemctl is-active --quiet cloudflared || { echo "cloudflared did not come back; restore $CONFIG from the backup" >&2; exit 1; }

step "serving on the tailnet"
sudo tailscale serve --bg --https="$TS_PORT" "$LOCAL"
tailscale serve status

cat <<DONE

Done.
  https://$HOSTNAME_              (Cloudflare, anywhere)
  the https URL above on :$TS_PORT (tailnet only)

Set DASH_PUBLIC_URL=https://$HOSTNAME_ in .env and restart so Slack links point there.

Strongly recommended, same as robot-computer: Cloudflare Zero Trust > Access >
Applications > Self-hosted, hostname $HOSTNAME_, one Allow policy with your email. That puts
a real login with rate limiting ahead of basic auth. Then, from another machine:

    curl -sI https://$HOSTNAME_/ | head -2

With Access: a 302 to <team>.cloudflareaccess.com. Without: 401 (basic auth only). A 200
means the dashboard is open to the internet; remove the DNS record.
DONE
