#!/usr/bin/env bash
# Flip BOTH Nginx upstreams (frontend + backend) to the given colour, reload.
# Usage: switch.sh <blue|green>
#
# Runs as root OR as a non-root deploy user. When non-root, the privileged
# Nginx operations are performed via `sudo` (see deploy/docs/SETUP.md for the
# scoped sudoers rule they require).
set -euo pipefail

COLOR="${1:-}"
NGINX_ACTIVE_CONF="${NGINX_ACTIVE_CONF:-/etc/nginx/conf.d/chiropro-active.conf}"

# Use sudo only when we're not already root.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

case "$COLOR" in
  blue)  FE_PORT=8081; BE_PORT=9001 ;;
  green) FE_PORT=8082; BE_PORT=9002 ;;
  *) echo "Usage: $0 <blue|green>" >&2; exit 2 ;;
esac

echo "==> Switching Nginx to ${COLOR} (frontend:${FE_PORT}, backend:${BE_PORT})"

# Write the switchable upstream file (needs root — use tee so the redirection
# itself is privileged).
$SUDO tee "$NGINX_ACTIVE_CONF" >/dev/null <<EOF
# Managed by switch.sh — do not edit by hand.
upstream chiropro_frontend {
    server 127.0.0.1:${FE_PORT} max_fails=3 fail_timeout=10s;  # active: ${COLOR}
}
upstream chiropro_backend {
    server 127.0.0.1:${BE_PORT} max_fails=3 fail_timeout=10s;  # active: ${COLOR}
}
EOF

if ! $SUDO nginx -t; then
  echo "!! nginx -t failed — NOT reloading." >&2
  exit 1
fi

$SUDO systemctl reload nginx

echo "==> Nginx now serving ${COLOR}."
