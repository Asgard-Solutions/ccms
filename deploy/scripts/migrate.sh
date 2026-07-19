#!/usr/bin/env bash
# Run DB migrations/seed against the SHARED MongoDB using a one-off backend
# container, BEFORE traffic is switched. Blue+green share one DB, so this runs
# once per deploy and MUST be backward-compatible (see deploy/db/README.md).
#
# Usage: migrate.sh <backend_image_ref>
set -euo pipefail

IMAGE="${1:?usage: migrate.sh <backend_image_ref>}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/chiropro}"
NETWORK="${NETWORK:-chiro_net}"

# shellcheck disable=SC1091
set -a; . "${DEPLOY_DIR}/.env"; set +a

if [ -z "${MIGRATE_CMD:-}" ]; then
  echo "==> MIGRATE_CMD is empty — skipping migrations/seed."
  exit 0
fi

echo "==> Running migrations/seed: ${MIGRATE_CMD}"
docker run --rm \
  --network "$NETWORK" \
  --env-file "${DEPLOY_DIR}/.env" \
  "$IMAGE" \
  sh -c "$MIGRATE_CMD"

echo "==> Migrations/seed complete."
