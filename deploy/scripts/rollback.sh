#!/usr/bin/env bash
# ── Instant rollback to the PREVIOUS colour (backend + frontend) ────────────
# Usage: rollback.sh
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/chiropro}"
SCRIPTS="${DEPLOY_DIR}/scripts"
STATE_FILE="${DEPLOY_DIR}/.active_color"
PREV_FILE="${DEPLOY_DIR}/.previous_color"

cd "$DEPLOY_DIR"
# shellcheck disable=SC1091
set -a; . "${DEPLOY_DIR}/.env"; set +a
BACKEND_HEALTH_PATH="${BACKEND_HEALTH_PATH:-/api/health}"
FRONTEND_HEALTH_PATH="${FRONTEND_HEALTH_PATH:-/healthz}"

CURRENT="$(cat "$STATE_FILE" 2>/dev/null || echo none)"
PREVIOUS="$(cat "$PREV_FILE" 2>/dev/null || echo none)"

[ -n "$PREVIOUS" ] && [ "$PREVIOUS" != "none" ] || { echo "!! No previous colour to roll back to." >&2; exit 1; }

case "$PREVIOUS" in
  blue)  FE_PORT=8081; BE_PORT=9001 ;;
  green) FE_PORT=8082; BE_PORT=9002 ;;
  *) echo "!! Unknown previous colour: $PREVIOUS" >&2; exit 1 ;;
esac

echo "==> Rolling back: ${CURRENT} → ${PREVIOUS}"

docker start "chiropro_backend_${PREVIOUS}"  >/dev/null 2>&1 || { echo "!! backend_${PREVIOUS} missing — redeploy old tag instead." >&2; exit 1; }
docker start "chiropro_frontend_${PREVIOUS}" >/dev/null 2>&1 || { echo "!! frontend_${PREVIOUS} missing — redeploy old tag instead." >&2; exit 1; }

bash "${SCRIPTS}/health-check.sh" "http://127.0.0.1:${BE_PORT}${BACKEND_HEALTH_PATH}" 60
bash "${SCRIPTS}/health-check.sh" "http://127.0.0.1:${FE_PORT}${FRONTEND_HEALTH_PATH}" 30
bash "${SCRIPTS}/switch.sh" "$PREVIOUS"

echo "$PREVIOUS" > "$STATE_FILE"
echo "$CURRENT"  > "$PREV_FILE"

if [ "$CURRENT" != "none" ]; then
  docker stop "chiropro_backend_${CURRENT}" "chiropro_frontend_${CURRENT}" >/dev/null 2>&1 || true
fi

echo "==> ✅ Rolled back. LIVE colour is now: ${PREVIOUS}"
