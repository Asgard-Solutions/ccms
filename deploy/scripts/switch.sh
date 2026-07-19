#!/usr/bin/env bash
# Flip BOTH Nginx upstreams (frontend + backend) to the given colour, reload.
# Usage: switch.sh <blue|green>
set -euo pipefail

COLOR="${1:-}"
NGINX_ACTIVE_CONF="${NGINX_ACTIVE_CONF:-/etc/nginx/conf.d/chiropro-active.conf}"

case "$COLOR" in
  blue)  FE_PORT=8081; BE_PORT=9001 ;;
  green) FE_PORT=8082; BE_PORT=9002 ;;
  *) echo "Usage: $0 <blue|green>" >&2; exit 2 ;;
esac

echo "==> Switching Nginx to ${COLOR} (frontend:${FE_PORT}, backend:${BE_PORT})"

cat > "$NGINX_ACTIVE_CONF" <<EOF
# Managed by switch.sh — do not edit by hand.
upstream chiropro_frontend {
    server 127.0.0.1:${FE_PORT} max_fails=3 fail_timeout=10s;  # active: ${COLOR}
}
upstream chiropro_backend {
    server 127.0.0.1:${BE_PORT} max_fails=3 fail_timeout=10s;  # active: ${COLOR}
}
EOF

if ! nginx -t; then
  echo "!! nginx -t failed — NOT reloading." >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl reload nginx
else
  nginx -s reload
fi

echo "==> Nginx now serving ${COLOR}."
