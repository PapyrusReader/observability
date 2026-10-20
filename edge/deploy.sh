#!/usr/bin/env sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
docker network inspect papyrus-edge >/dev/null 2>&1 || docker network create papyrus-edge
docker compose --env-file edge.env -f compose.yml config --quiet
docker compose --env-file edge.env -f compose.yml pull
docker compose --env-file edge.env -f compose.yml up -d
