#!/usr/bin/env bash
# Restore a backup made by backup.sh. Stops the api, checks the copy, swaps it in (the current
# database is kept as data/sazon.db.before-restore-<time>), starts the api and waits for it.
#
# Usage: deploy/restore.sh <data/backups/sazon-....db.gz | gs://bucket/sqlite/sazon-....db.gz>
# Env:   COMPOSE ("docker compose"), DATA_DIR (data; only for the message)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

source=${1:?usage: restore.sh <backup.db.gz | gs://...>}
read -r -a compose <<<"${COMPOSE:-docker compose}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if [[ "$source" == gs://* ]]; then
  gsutil=$(command -v gsutil || echo /snap/bin/gsutil)
  "$gsutil" -q cp "$source" "$work/backup.db.gz"
  source="$work/backup.db.gz"
fi
gunzip -t "$source"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
"${compose[@]}" stop api
# Written from inside the container, as its user (uid 1000), so ownership stays right.
# shellcheck disable=SC2016  # the script runs in the container's shell
gunzip -c "$source" | "${compose[@]}" run --rm --no-deps -T api sh -euc '
  cat > /data/sazon.db.restoring
  python -c "import sqlite3, sys; r = sqlite3.connect(sys.argv[1]).execute(\"PRAGMA integrity_check\").fetchone()[0]; sys.exit(r != \"ok\")" /data/sazon.db.restoring
  for suffix in "" -wal -shm; do
    if [ -f "/data/sazon.db$suffix" ]; then
      mv "/data/sazon.db$suffix" "/data/sazon.db.before-restore-'"$stamp"'$suffix"
    fi
  done
  mv /data/sazon.db.restoring /data/sazon.db
'
"${compose[@]}" up -d --wait api
echo "Restored from $1; the previous database is ${DATA_DIR:-data}/sazon.db.before-restore-$stamp"
