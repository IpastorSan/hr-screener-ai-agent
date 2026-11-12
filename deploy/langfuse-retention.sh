#!/usr/bin/env bash
# Trace retention for self-hosted Langfuse. Project data retention is an enterprise feature, so
# the open-source build keeps traces forever; this deletes them after TRACE_RETENTION_DAYS (30,
# docs/deployment.md): ClickHouse rows by start time and the raw event files in MinIO by age.
#
# Usage: deploy/langfuse-retention.sh
# Env:   TRACE_RETENTION_DAYS (30), COMPOSE ("docker compose")
# Runs daily from systemd (systemd/sazon-daily.service); a no-op without the observability stack.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

read -r -a compose <<<"${COMPOSE:-docker compose}"
days=${TRACE_RETENTION_DAYS:-30}
case "$days" in '' | *[!0-9]*) echo "TRACE_RETENTION_DAYS must be a number of days" >&2; exit 2 ;; esac

if ! "${compose[@]}" ps --status running --services 2>/dev/null | grep -qx langfuse-clickhouse; then
  echo "Langfuse is not running here; nothing to do."
  exit 0
fi

# Tables and their time columns in Langfuse v4 (ALTER ... DELETE is an async mutation).
tables=(events_core:start_time events_full:start_time observations:start_time traces:timestamp
  scores:timestamp)
for entry in "${tables[@]}"; do
  table=${entry%%:*} column=${entry#*:}
  # </dev/null: `exec -T` would otherwise read this script's stdin.
  "${compose[@]}" exec -T langfuse-clickhouse sh -c \
    "clickhouse-client --user \"\$CLICKHOUSE_USER\" --password \"\$CLICKHOUSE_PASSWORD\" \
      -q \"ALTER TABLE default.$table DELETE WHERE $column < now() - INTERVAL $days DAY\"" \
    </dev/null
done

# Raw OTLP batches and media in the `langfuse` bucket: an expiry rule MinIO applies itself.
minio() { "${compose[@]}" exec -T langfuse-minio sh -c "$1" </dev/null; }
# shellcheck disable=SC2016  # expanded inside the container
minio 'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null'
if ! minio 'mc ilm rule ls local/langfuse 2>/dev/null' | grep -q "Expiration"; then
  minio "mc ilm rule add --expire-days $days local/langfuse >/dev/null"
fi

echo "Langfuse traces older than $days days deleted."
