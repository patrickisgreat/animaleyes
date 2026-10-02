#!/usr/bin/env bash
#
# Off-box backup of the irreplaceable data: reference photos, auto-collected training frames,
# and the SQLite event history. Runtime frames (data/frames) are transient and skipped.
#
# The photos live only on this box's disk (bind-mounted into the container), so a disk failure
# or an accidental `rm -rf data` loses them. This rsyncs them to another machine on the tailnet
# (e.g. the terramaster NAS). Nothing here is in git — it's a public repo and these are personal.
#
#   DEST=pb@terramaster-26-1.tail378cd6.ts.net:/volume1/backups/animaleyes tools/backup.sh
#
# Wire it as a nightly cron on this box (runs at 03:30):
#   (crontab -l 2>/dev/null; echo "30 3 * * * DEST=... $HOME/code/animaleyes/tools/backup.sh >> $HOME/animaleyes-backup.log 2>&1") | crontab -
#
set -euo pipefail

DEST="${DEST:-}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/data"

if [[ -z "$DEST" ]]; then
  echo "usage: DEST=user@host:/path tools/backup.sh" >&2
  echo "   (host should be a tailnet machine; needs SSH key access)" >&2
  exit 1
fi

echo "backing up reference photos, training frames and the event DB -> $DEST"
# -a archive, -z compress, --mkpath creates the dest dir. Only the durable subtrees.
rsync -az --mkpath --delete \
  --include="reference/***" \
  --include="training/***" \
  --include="db/***" \
  --exclude="*" \
  "$SRC/" "$DEST/"

echo "done: $(date)"
