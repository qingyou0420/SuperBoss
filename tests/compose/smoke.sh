#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
COMPOSE_FILE=${COMPOSE_FILE:-"$REPO_ROOT/docker-compose.yml"}
ENV_FILE=${ENV_FILE:-"$REPO_ROOT/.env"}
READINESS_TIMEOUT_SECONDS=${READINESS_TIMEOUT_SECONDS:-600}

case "$READINESS_TIMEOUT_SECONDS" in
  ''|*[!0-9]*)
    echo "READINESS_TIMEOUT_SECONDS must be an integer from 1 through 600" >&2
    exit 2
    ;;
esac
if [ "$READINESS_TIMEOUT_SECONDS" -lt 1 ] || [ "$READINESS_TIMEOUT_SECONDS" -gt 600 ]; then
  echo "READINESS_TIMEOUT_SECONDS must be an integer from 1 through 600" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to validate the rendered Compose port model" >&2
  exit 2
fi

started=0
cleanup() {
  if [ "$started" -eq 1 ]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down || true
  fi
}
trap cleanup EXIT HUP INT TERM

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build
started=1
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
  exec -T api alembic upgrade head

deadline=$(( $(date +%s) + READINESS_TIMEOUT_SECONDS ))
while ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" \
  exec -T nginx wget -q -O /dev/null http://api:8000/api/v1/health/ready; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "readiness did not become healthy within the bounded window" >&2
    exit 1
  fi
  sleep 2
done

config_json=$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --format json)
printf '%s' "$config_json" | python3 -c '
import json
import sys

model = json.load(sys.stdin)
published = []
for service_name, service in model["services"].items():
    for port in service.get("ports") or []:
        if port.get("published") is not None:
            published.append((service_name, int(port["published"])))
if published != [("nginx", 443)]:
    raise SystemExit("production compose may publish only nginx port 443")
'

echo "M1_COMPOSE_SMOKE_PASSED"
