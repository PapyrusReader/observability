#!/usr/bin/env sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
if [ -f /var/lib/papyrus-observability/loki-stopped ]; then
  echo 'Disk guard active. Follow the recovery runbook before deploying.' >&2
  exit 1
fi
docker network inspect papyrus-edge >/dev/null
docker network inspect papyrus-production_default >/dev/null
docker compose --env-file monitoring.env config --quiet
docker compose --env-file monitoring.env pull
docker compose --env-file monitoring.env up -d
