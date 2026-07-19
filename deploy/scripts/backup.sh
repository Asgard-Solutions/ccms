#!/usr/bin/env bash
# ── Encrypted backup of the shared MongoDB + on-disk data dir ───────────────
# Runs BEFORE every deploy (and can be run manually / via cron). Produces
# AES-256 encrypted archives in $DEPLOY_DIR/backups. Restore instructions are
# in deploy/db/README.md.
#
# Usage: backup.sh [label]
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/chiropro}"
LABEL="${1:-manual}"

# shellcheck disable=SC1091
set -a; . "${DEPLOY_DIR}/.env"; set +a

BACKUP_DIR="${DEPLOY_DIR}/backups"
RETENTION="${BACKUP_RETENTION:-7}"
mkdir -p "$BACKUP_DIR"

if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
  echo "!! BACKUP_PASSPHRASE is not set in .env — skipping encrypted backup." >&2
  echo "   (Set it to enable pre-deploy backups. Refusing to write UNENCRYPTED PHI.)" >&2
  # Non-fatal by design so a missing passphrase can't wedge deploys; deploy.sh
  # decides whether that is acceptable via BACKUP_REQUIRED.
  exit 0
fi

ts="$(date -u +%Y%m%d-%H%M%S)"
mongo_out="${BACKUP_DIR}/mongo-${ts}-${LABEL}.archive.gz.enc"
data_out="${BACKUP_DIR}/data-${ts}-${LABEL}.tgz.enc"

enc() { openssl enc -aes-256-cbc -pbkdf2 -salt -pass "pass:${BACKUP_PASSPHRASE}"; }

echo "==> Backing up MongoDB (encrypted) → ${mongo_out}"
# mongodump runs INSIDE the mongo container; MONGO_URL is passed via env to
# avoid leaking credentials in the process list.
docker exec -e MURI="${MONGO_URL}" chiro_mongo \
  sh -c 'mongodump --uri "$MURI" --archive --gzip' \
  | enc > "$mongo_out"

echo "==> Backing up data dir (encrypted) → ${data_out}"
tar czf - -C "$DEPLOY_DIR" data | enc > "$data_out"

# Integrity: fail loudly if either archive is empty.
for f in "$mongo_out" "$data_out"; do
  if [ ! -s "$f" ]; then
    echo "!! Backup produced an empty file: $f" >&2
    exit 1
  fi
done

# Retention: keep the newest $RETENTION of each kind.
prune() {
  local pattern="$1"
  # shellcheck disable=SC2012
  ls -1t "$BACKUP_DIR"/$pattern 2>/dev/null | tail -n +"$((RETENTION + 1))" | xargs -r rm -f
}
prune "mongo-*.archive.gz.enc"
prune "data-*.tgz.enc"

echo "==> Backup complete ($(du -h "$mongo_out" | cut -f1) mongo, $(du -h "$data_out" | cut -f1) data). Kept newest ${RETENTION}."
