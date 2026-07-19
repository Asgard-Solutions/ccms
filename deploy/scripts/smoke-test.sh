#!/usr/bin/env bash
# ── Post-switch smoke test (through the live edge) ──────────────────────────
# Hits a few unauthenticated, safe endpoints on the PUBLIC origin to verify the
# whole path after a cutover: TLS → host Nginx routing (/api → backend, / →
# frontend) → app liveness. Uses --resolve to force the request onto this host
# so it works regardless of DNS/hairpin-NAT.
#
# Usage: smoke-test.sh <base_url> [comma_separated_paths]
#   e.g. smoke-test.sh https://adjustpro.io "/api/health,/api/,/"
set -euo pipefail

BASE="${1:?usage: smoke-test.sh <base_url> [paths]}"
PATHS="${2:-/api/health,/api/,/}"

# Build a --resolve flag so HTTPS requests hit 127.0.0.1 (this box) directly.
host="$(printf '%s' "$BASE" | sed -E 's#https?://([^/:]+).*#\1#')"
resolve=()
case "$BASE" in
  https://*) resolve=(--resolve "${host}:443:127.0.0.1") ;;
  http://*)  resolve=(--resolve "${host}:80:127.0.0.1") ;;
esac

echo "==> Smoke test against ${BASE} (host ${host} → 127.0.0.1)"
fail=0
IFS=',' read -ra items <<< "$PATHS"
for p in "${items[@]}"; do
  p="$(printf '%s' "$p" | xargs)"   # trim
  [ -n "$p" ] || continue
  code="$(curl -fsS -k "${resolve[@]}" -o /dev/null -w '%{http_code}' --max-time 12 "${BASE}${p}" 2>/dev/null || echo 000)"
  if [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
    echo "   ✅ ${p} → ${code}"
  else
    echo "   ❌ ${p} → ${code}"
    fail=1
  fi
done

# Extra assertion: /api/health should actually report healthy JSON.
if printf '%s' "$PATHS" | grep -q "/api/health"; then
  body="$(curl -fsS -k "${resolve[@]}" --max-time 12 "${BASE}/api/health" 2>/dev/null || echo '')"
  if printf '%s' "$body" | grep -qi 'healthy\|ok'; then
    echo "   ✅ /api/health body OK"
  else
    echo "   ❌ /api/health body unexpected: ${body:-<empty>}"
    fail=1
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo "!! Smoke test FAILED." >&2
  exit 1
fi
echo "==> Smoke test passed."
