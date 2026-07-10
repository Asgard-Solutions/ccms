/**
 * Fire-and-forget UI telemetry client.
 *
 * Called from feature-flag switch points, section navigation, and error
 * boundaries so ops can track new-layout adoption without shipping PHI.
 *
 * - Payloads are constrained to a small allowlist server-side.
 * - Failures never surface to the user.
 * - In-flight dedupe prevents double-fire from React StrictMode / rapid
 *   re-renders.
 */
import { api } from "../api/client";

const inflight = new Map(); // key -> timestamp
const DEDUPE_MS = 500;

export function trackUiEvent(event, props = {}) {
  if (!event) return;
  const key = `${event}:${JSON.stringify(props)}`;
  const now = Date.now();
  const last = inflight.get(key);
  if (last && now - last < DEDUPE_MS) return;
  inflight.set(key, now);

  const body = { event, ...props };
  try {
    // We deliberately do NOT await this. The catch swallows all failures
    // so telemetry outages never affect the clinician.
    api
      .post("/telemetry/ui-event", body)
      .catch(() => {
        /* ignore */
      })
      .finally(() => {
        setTimeout(() => inflight.delete(key), DEDUPE_MS);
      });
  } catch {
    inflight.delete(key);
  }
}
