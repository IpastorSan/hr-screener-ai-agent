#!/usr/bin/env bash
# Restore drill on the local stack (make stack-up): back up, delete the database, restore it
# and compare row counts. No upload. Exits non-zero when anything differs.
#
# Usage: deploy/test-restore.sh      Env: STACK_PROJECT (sazon-local)
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

export COMPOSE="docker compose -p ${STACK_PROJECT:-sazon-local} --env-file local.env -f compose.yaml -f compose.local.yaml"
export DATA_DIR=data-local
read -r -a compose <<<"$COMPOSE"

counts() {
  "${compose[@]}" exec -T api python -c '
import sqlite3
db = sqlite3.connect("/data/sazon.db")
query = "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE ? ORDER BY name"
tables = [name for (name,) in db.execute(query, ("table", "sqlite_%"))]
counts = [(t, db.execute("SELECT count(*) FROM " + t).fetchone()[0]) for t in tables]
print(" ".join(f"{t}={n}" for t, n in counts))
' </dev/null
}

before=$(counts)
echo "before:   $before"
./backup.sh --no-upload
# shellcheck disable=SC2012
latest=$(ls -1t "$DATA_DIR"/backups/sazon-*.db.gz | head -n 1)

"${compose[@]}" stop api
"${compose[@]}" run --rm --no-deps -T api sh -c 'rm -f /data/sazon.db /data/sazon.db-wal /data/sazon.db-shm' </dev/null
"${compose[@]}" up -d --wait api
echo "deleted:  $(counts)"

./restore.sh "$latest"
after=$(counts)
echo "restored: $after"
"${compose[@]}" exec -T api python -c '
import json, sys, urllib.request
body = json.load(urllib.request.urlopen("http://127.0.0.1:8000/readyz", timeout=10))
print("readyz:", body["status"]); sys.exit(body["status"] != "ok")' </dev/null

if [ "$before" != "$after" ] || [[ "$before" == *"conversation=0 "* ]]; then
  echo "FAIL: row counts differ (or the database was empty)" >&2
  exit 1
fi
echo "PASS: restore drill"
