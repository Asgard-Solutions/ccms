#!/usr/bin/env bash
# ── Core blue/green deploy (backend + frontend) — runs ON the VPS via CI ─────
# Steps: pull both images → migrate shared DB → start IDLE backend+frontend →
#        health-check both → switch Nginx → record state → stop OLD colour.
# Old colour containers are kept (stopped) for instant rollback.
#
# Usage: deploy.sh <backend_image_ref> <frontend_image_ref>
set -euo pipefail

BACKEND_IMAGE="${1:?usage: deploy.sh <backend_image> <frontend_image>}"
FRONTEND_IMAGE="${2:?usage: deploy.sh <backend_image> <frontend_image>}"

DEPLOY_DIR="${DEPLOY_DIR:-/opt/chiropro}"
NETWORK="${NETWORK:-chiro_net}"
STATE_FILE="${DEPLOY_DIR}/.active_color"
SCRIPTS="${DEPLOY_DIR}/scripts"

cd "$DEPLOY_DIR"
# shellcheck disable=SC1091
set -a; . "${DEPLOY_DIR}/.env"; set +a

BACKEND_HEALTH_PATH="${BACKEND_HEALTH_PATH:-/api/health}"
FRONTEND_HEALTH_PATH="${FRONTEND_HEALTH_PATH:-/healthz}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-120}"

# Determine active / idle colours + their host ports.
ACTIVE="$(cat "$STATE_FILE" 2>/dev/null || echo none)"
if [ "$ACTIVE" = "blue" ]; then
  IDLE=green; FE_PORT=8082; BE_PORT=9002
else
  IDLE=blue;  FE_PORT=8081; BE_PORT=9001
fi

echo "=================================================================="
echo " Backend  : $BACKEND_IMAGE"
echo " Frontend : $FRONTEND_IMAGE"
echo " Active   : $ACTIVE  →  deploying IDLE: $IDLE (fe:$FE_PORT be:$BE_PORT)"
echo "=================================================================="

echo "==> Pulling images..."
docker pull "$BACKEND_IMAGE"
docker pull "$FRONTEND_IMAGE"

# Ensure shared infra (network + Mongo + Redis) is up.
docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK"
docker compose -f "${DEPLOY_DIR}/docker-compose.infra.yml" up -d

# Encrypted backup of the shared DB + data dir BEFORE any change (idempotent
# seeding runs when the new colour boots, so capture the pre-deploy state now).
if [ "${BACKUP_BEFORE_DEPLOY:-true}" = "true" ] && [ "$ACTIVE" != "none" ]; then
  if ! bash "${SCRIPTS}/backup.sh" "predeploy"; then
    if [ "${BACKUP_REQUIRED:-true}" = "true" ]; then
      echo "!! Pre-deploy backup failed and BACKUP_REQUIRED=true — aborting (live colour untouched)." >&2
      exit 1
    fi
    echo "!! Pre-deploy backup failed but BACKUP_REQUIRED=false — continuing." >&2
  fi
fi

# Migrate/seed shared DB BEFORE switching (backward-compatible).
bash "${SCRIPTS}/migrate.sh" "$BACKEND_IMAGE"

# Start IDLE backend.
echo "==> Starting backend (${IDLE})..."
docker rm -f "chiropro_backend_${IDLE}" >/dev/null 2>&1 || true
docker run -d \
  --name "chiropro_backend_${IDLE}" \
  --network "$NETWORK" \
  --env-file "${DEPLOY_DIR}/.env" \
  -e PORT=8001 \
  -p "127.0.0.1:${BE_PORT}:8001" \
  -v "${DEPLOY_DIR}/data:/app/data" \
  --restart unless-stopped \
  --label "chiropro.color=${IDLE}" --label "chiropro.tier=backend" \
  "$BACKEND_IMAGE"

# Start IDLE frontend.
echo "==> Starting frontend (${IDLE})..."
docker rm -f "chiropro_frontend_${IDLE}" >/dev/null 2>&1 || true
docker run -d \
  --name "chiropro_frontend_${IDLE}" \
  --network "$NETWORK" \
  -p "127.0.0.1:${FE_PORT}:3000" \
  --restart unless-stopped \
  --label "chiropro.color=${IDLE}" --label "chiropro.tier=frontend" \
  "$FRONTEND_IMAGE"

# Health-check both before any traffic switch.
abort() {
  echo "!! $1 — aborting. Live traffic still on ${ACTIVE}." >&2
  docker logs --tail 60 "chiropro_backend_${IDLE}"  >&2 2>/dev/null || true
  docker logs --tail 30 "chiropro_frontend_${IDLE}" >&2 2>/dev/null || true
  docker rm -f "chiropro_backend_${IDLE}" "chiropro_frontend_${IDLE}" >/dev/null 2>&1 || true
  exit 1
}
bash "${SCRIPTS}/health-check.sh" "http://127.0.0.1:${BE_PORT}${BACKEND_HEALTH_PATH}" "$HEALTH_TIMEOUT_SECONDS" \
  || abort "backend ${IDLE} failed health check"
bash "${SCRIPTS}/health-check.sh" "http://127.0.0.1:${FE_PORT}${FRONTEND_HEALTH_PATH}" 60 \
  || abort "frontend ${IDLE} failed health check"

# Atomic cutover.
bash "${SCRIPTS}/switch.sh" "$IDLE"

# Record state.
echo "$IDLE"   > "$STATE_FILE"
echo "$ACTIVE" > "${DEPLOY_DIR}/.previous_color"

# Post-switch smoke test through the live edge (TLS + Nginx routing + app).
# On failure, auto-rollback to the previous colour (if any) and abort.
if [ "${RUN_SMOKE_TEST:-true}" = "true" ]; then
  SMOKE_BASE="${SMOKE_BASE_URL:-${PUBLIC_URL:-https://adjustpro.io}}"
  if ! bash "${SCRIPTS}/smoke-test.sh" "$SMOKE_BASE" "${SMOKE_PATHS:-/api/health,/api/,/}"; then
    echo "!! Post-switch smoke test FAILED." >&2
    if [ "$ACTIVE" != "none" ]; then
      echo "==> Auto-rolling back to ${ACTIVE}..." >&2
      if bash "${SCRIPTS}/rollback.sh"; then
        echo "!! Rolled back to ${ACTIVE}. New release ${IDLE} was NOT kept live." >&2
      else
        echo "!! AUTO-ROLLBACK FAILED — manual intervention required." >&2
      fi
    else
      echo "!! First deploy (no previous colour) — leaving ${IDLE} live; investigate immediately." >&2
    fi
    exit 1
  fi
fi

# Stop (keep) old colour for rollback.
if [ "$ACTIVE" != "none" ]; then
  echo "==> Stopping previous colour ${ACTIVE} (kept for rollback)"
  docker stop "chiropro_backend_${ACTIVE}" "chiropro_frontend_${ACTIVE}" >/dev/null 2>&1 || true
fi

echo "=================================================================="
echo " ✅ Deploy complete. LIVE colour is now: ${IDLE}"
echo "    Rollback: bash ${SCRIPTS}/rollback.sh"
echo "=================================================================="
