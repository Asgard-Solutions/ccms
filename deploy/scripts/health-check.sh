#!/usr/bin/env bash
# Poll an HTTP URL until it returns 2xx, or fail after a timeout.
# Usage: health-check.sh <url> [timeout_seconds]
set -euo pipefail

URL="${1:?usage: health-check.sh <url> [timeout_seconds]}"
TIMEOUT="${2:-90}"
INTERVAL=3
elapsed=0

echo "==> Health check: $URL (timeout ${TIMEOUT}s)"
until code=$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 5 "$URL" 2>/dev/null) \
      && [ "$code" -ge 200 ] && [ "$code" -lt 300 ]; do
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "!! Health check FAILED after ${TIMEOUT}s (last code: ${code:-none})" >&2
    exit 1
  fi
  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
  echo "   ...waiting (${elapsed}s, last code: ${code:-none})"
done

echo "==> Healthy (HTTP $code)."
