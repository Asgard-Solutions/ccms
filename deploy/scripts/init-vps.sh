#!/usr/bin/env bash
# ── One-time VPS bootstrap for ChiroPro blue/green ──────────────────────────
# Idempotent. Does NOT touch the SilvertreeSolutions.co site.
# Run as root:  sudo bash deploy/scripts/init-vps.sh
# Optional non-root CI user (creates user + docker access + scoped nginx sudo):
#   sudo DEPLOY_USER=deploy bash deploy/scripts/init-vps.sh
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/chiropro}"
DOMAIN="${DOMAIN:-adjustpro.io}"
DEPLOY_USER="${DEPLOY_USER:-}"
NETWORK="chiro_net"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the deploy/ folder

echo "==> ChiroPro VPS bootstrap  (dir=$DEPLOY_DIR domain=$DOMAIN src=$REPO_DIR)"
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo/root." >&2; exit 1; }

# 1. Prerequisites
command -v docker  >/dev/null 2>&1 || { echo "==> Installing Docker...";  curl -fsSL https://get.docker.com | sh; }
command -v nginx   >/dev/null 2>&1 || { echo "==> Installing Nginx...";   apt-get update && apt-get install -y nginx; }
command -v certbot >/dev/null 2>&1 || { echo "==> Installing certbot..."; apt-get update && apt-get install -y certbot python3-certbot-nginx; }

# 2. Deploy dir + files
mkdir -p "$DEPLOY_DIR/scripts"
# Shared, persistent data dir (exports + local PHI storage) bind-mounted into
# BOTH colours' backend containers so files survive deploys and are shared.
mkdir -p "$DEPLOY_DIR/data/exports" "$DEPLOY_DIR/data/storage"
mkdir -p "$DEPLOY_DIR/backups"
# Backend container runs as non-root uid 1000 (appuser); make the shared data
# dir writable by it.
chown -R 1000:1000 "$DEPLOY_DIR/data"
cp "$REPO_DIR"/scripts/*.sh              "$DEPLOY_DIR/scripts/"
cp "$REPO_DIR"/docker-compose.infra.yml  "$DEPLOY_DIR/"
chmod +x "$DEPLOY_DIR"/scripts/*.sh
if [ ! -f "$DEPLOY_DIR/.env" ]; then
  SAMPLE=""
  for c in "$REPO_DIR/.env.example" "$REPO_DIR/env.example" "$REPO_DIR/env.sample"; do
    [ -f "$c" ] && SAMPLE="$c" && break
  done
  if [ -n "$SAMPLE" ]; then
    cp "$SAMPLE" "$DEPLOY_DIR/.env"
    echo "!! Created $DEPLOY_DIR/.env from $(basename "$SAMPLE") — EDIT with real secrets (Mongo/Redis + JWT_SECRET + DATA_ENCRYPTION_KEY + app secrets) before deploying."
  else
    echo "!! No env sample found in $REPO_DIR and $DEPLOY_DIR/.env is missing."
    echo "   Create $DEPLOY_DIR/.env manually (see deploy/.env.example in the repo) before deploying."
  fi
fi

# 3. Shared network + datastores (Mongo + Redis)
docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK"
( cd "$DEPLOY_DIR" && set -a && . ./.env && set +a && docker compose -f docker-compose.infra.yml up -d )

# 4. Nginx switchable upstream + site
mkdir -p /var/www/certbot
cp "$REPO_DIR/nginx/chiropro-active.conf" /etc/nginx/conf.d/chiropro-active.conf
cp "$REPO_DIR/nginx/adjustpro.io.conf" /etc/nginx/sites-available/${DOMAIN}.conf

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
  ln -sf /etc/nginx/sites-available/${DOMAIN}.conf /etc/nginx/sites-enabled/${DOMAIN}.conf
  nginx -t && systemctl reload nginx
  echo "==> HTTPS site enabled."
else
  cat > /etc/nginx/sites-enabled/${DOMAIN}.conf <<EOF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 200 'ChiroPro bootstrap OK — run certbot next.'; add_header Content-Type text/plain; }
}
EOF
  nginx -t && systemctl reload nginx
  echo ""
  echo "==> NEXT: issue TLS cert, then enable the full HTTPS site:"
  echo "    certbot certonly --webroot -w /var/www/certbot -d ${DOMAIN} -d www.${DOMAIN}"
  echo "    ln -sf /etc/nginx/sites-available/${DOMAIN}.conf /etc/nginx/sites-enabled/${DOMAIN}.conf"
  echo "    nginx -t && systemctl reload nginx"
fi

# 5. Optional: configure a non-root deploy user for CI (DEPLOY_USER=deploy)
if [ -n "$DEPLOY_USER" ]; then
  echo "==> Configuring non-root deploy user: ${DEPLOY_USER}"
  id -u "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER"
  usermod -aG docker "$DEPLOY_USER"

  # Own the deploy dir; keep the app data dir as uid 1000 (backend container user).
  chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$DEPLOY_DIR"
  chown -R 1000:1000 "$DEPLOY_DIR/data"

  # Scoped passwordless sudo for ONLY the Nginx actions switch.sh performs.
  NGINX_BIN="$(command -v nginx || echo /usr/sbin/nginx)"
  SYSTEMCTL_BIN="$(command -v systemctl || echo /usr/bin/systemctl)"
  TEE_BIN="$(command -v tee || echo /usr/bin/tee)"
  cat > /etc/sudoers.d/chiropro <<EOF
${DEPLOY_USER} ALL=(root) NOPASSWD: ${NGINX_BIN} -t, ${SYSTEMCTL_BIN} reload nginx, ${TEE_BIN} /etc/nginx/conf.d/chiropro-active.conf
EOF
  chmod 440 /etc/sudoers.d/chiropro
  visudo -cf /etc/sudoers.d/chiropro

  # Prepare the SSH dir; the operator adds the CI public key.
  install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh"
  touch "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chown "$DEPLOY_USER":"$DEPLOY_USER" "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chmod 600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"

  echo "   → Add your CI PUBLIC key to /home/${DEPLOY_USER}/.ssh/authorized_keys"
  echo "   → GitHub secrets: VPS_USER=${DEPLOY_USER} + matching private key in VPS_SSH_KEY"
fi

echo ""
echo "==> Bootstrap complete."
echo "    1) Edit $DEPLOY_DIR/.env (Mongo/Redis passwords + MONGO_URL/REDIS_URL + ALL backend secrets)."
echo "    2) Issue cert + enable HTTPS site (above)."
echo "    3) Push to GitHub main — CI deploys the first (blue) release."
