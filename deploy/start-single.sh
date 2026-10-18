#!/bin/sh
# Start FalkorDB inside the container, listening on 127.0.0.1 only (never exposed),
# then the ShutOff web app on $PORT. Storage is ephemeral: the app seeds the synthetic
# network on start-up, and "Reset demo" restores it.
set -e
mkdir -p /tmp/falkordb
redis-server --bind 127.0.0.1 --port 6379 --protected-mode yes --save "" --appendonly no \
  --dir /tmp/falkordb --daemonize yes \
  --loadmodule /var/lib/falkordb/bin/falkordb.so ${FALKORDB_ARGS}
i=0
until redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 100 ] && echo "FalkorDB did not start" >&2 && exit 1
  sleep 0.1
done
exec uvicorn shutoff.api:app --host 0.0.0.0 --port "${PORT:-7860}"
