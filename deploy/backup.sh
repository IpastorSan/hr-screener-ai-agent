#!/usr/bin/env bash
# SQLite backup: an online copy made inside the api container (`sazon backup`, the same
# page-by-page copy as sqlite3's `.backup`, checked with integrity_check), gzipped, uploaded to
# gs://$GCS_BACKUP_BUCKET/sqlite/, and the newest $BACKUP_KEEP kept in data/backups.
#
# Usage: deploy/backup.sh [--no-upload]
# Env:   GCS_BACKUP_BUCKET (else read from .env), BACKUP_KEEP (14),
#        COMPOSE ("docker compose"), DATA_DIR (data)
# Runs nightly from systemd (systemd/sazon-backup.timer) as root.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

upload=1
for arg in "$@"; do
  case "$arg" in
    --no-upload) upload=0 ;;
    -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

read -r -a compose <<<"${COMPOSE:-docker compose}"
data_dir=${DATA_DIR:-data}
keep=${BACKUP_KEEP:-14}
env_value() { grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true; }
bucket=${GCS_BACKUP_BUCKET:-$(env_value GCS_BACKUP_BUCKET)}

if [ "$upload" = 1 ] && [ -z "$bucket" ]; then
  echo "FATAL: GCS_BACKUP_BUCKET is empty; set it or pass --no-upload" >&2
  exit 1
fi

name="sazon-$(date -u +%Y%m%dT%H%M%SZ).db"
# </dev/null: `exec -T` would otherwise read this script's stdin.
"${compose[@]}" exec -T api sazon backup "/data/backups/$name" </dev/null
gzip -9 "$data_dir/backups/$name"
file="$data_dir/backups/$name.gz"

if [ "$upload" = 1 ]; then
  # systemd and cron run with a minimal PATH; look where the Cloud SDK usually lives.
  gsutil=$(command -v gsutil || true)
  for candidate in /usr/bin/gsutil /snap/bin/gsutil /usr/lib/google-cloud-sdk/bin/gsutil; do
    [ -n "$gsutil" ] && break
    [ -x "$candidate" ] && gsutil=$candidate
  done
  if [ -z "$gsutil" ]; then
    echo "FATAL: gsutil not found; install the Google Cloud CLI" >&2
    exit 1
  fi
  "$gsutil" -q cp "$file" "gs://${bucket#gs://}/sqlite/$name.gz"
  echo "Uploaded gs://${bucket#gs://}/sqlite/$name.gz"
fi

# Keep the newest $keep local copies (names are ours: sazon-<UTC time>.db.gz).
# shellcheck disable=SC2012
ls -1t "$data_dir"/backups/sazon-*.db.gz 2>/dev/null | tail -n +"$((keep + 1))" | xargs -r rm -f --
echo "Backup $file ($(du -h "$file" | cut -f1))"
