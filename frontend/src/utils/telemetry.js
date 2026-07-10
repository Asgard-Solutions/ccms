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

/**
 * Client-side allow-list mirror of the backend enum. Kept in sync with
 * `services/telemetry/router.py::ActionSlug` and
 * `services/telemetry/SCHEMA.md`. Any slug NOT in this list is dropped
 * before the request leaves the browser so we can never widen the
 * schema by accident.
 */
export const CARE_STATUS_ACTION_SLUGS = new Set([
  "open-encounter",
  "add-note",
  "record-outcome",
  "schedule-visit",
  "schedule-reexam",
  "review-billing-issues",
  "edit-missing-information",
]);

function firePost(url, body) {
  try {
    api
      .post(url, body)
      .catch(() => {
        /* telemetry outages never affect the clinician */
      });
  } catch {
    /* ignore */
  }
}

function dedupe(key, fn) {
  const now = Date.now();
  const last = inflight.get(key);
  if (last && now - last < DEDUPE_MS) return;
  inflight.set(key, now);
  try {
    fn();
  } finally {
    setTimeout(() => inflight.delete(key), DEDUPE_MS);
  }
}

export function trackUiEvent(event, props = {}) {
  if (!event) return;
  const key = `${event}:${JSON.stringify(props)}`;
  dedupe(key, () => firePost("/telemetry/ui-event", { event, ...props }));
}

/**
 * Fire the strictly-scoped `clinical_care_status_action_selected`
 * event. Payload shape is locked here — no callers can widen the
 * vocabulary.
 *
 *   {
 *     event_name:     "clinical_care_status_action_selected",
 *     section_slug:   "current-care-status",
 *     action_slug:    <allow-listed slug>,
 *     source_surface: "patient-clinical",
 *     layout_version: "v2"
 *   }
 */
export function trackCareStatusAction(actionSlug) {
  if (!CARE_STATUS_ACTION_SLUGS.has(actionSlug)) return;
  const body = {
    event_name: "clinical_care_status_action_selected",
    section_slug: "current-care-status",
    action_slug: actionSlug,
    source_surface: "patient-clinical",
    layout_version: "v2",
  };
  const key = `care-status:${actionSlug}`;
  dedupe(key, () => firePost("/telemetry/ui-action", body));
}

/**
 * Next-actions telemetry — Phase 3 Slice 1.
 *
 * Mirrors the backend `NextActionId` + `NextActionInteraction` literals
 * in `services/telemetry/router.py`. Only attempt-level interaction is
 * tracked — success/failure of the downstream workflow is inferred
 * from the existing audit trail, never from UX telemetry.
 */
export const NEXT_ACTION_IDS = new Set([
  "sign-unsigned-note",
  "complete-missing-documentation",
  "attach-or-link-diagnosis",
  "open-blocked-billing-readiness",
  "review-billing-warning",
  "schedule-due-or-overdue-reexam",
  "schedule-remaining-planned-visits",
  "review-missing-required-intake",
  "record-configured-outcome-measure",
]);

const NEXT_ACTION_INTERACTIONS = new Set(["opened", "dismissed"]);

export function trackNextActionInteraction({ action_id, interaction }) {
  if (!NEXT_ACTION_IDS.has(action_id)) return;
  if (!NEXT_ACTION_INTERACTIONS.has(interaction)) return;
  const body = {
    event_name: "clinical_next_action_interaction",
    section_slug: "next-actions",
    source_surface: "patient-clinical",
    layout_version: "v2",
    action_id,
    interaction,
  };
  const key = `next-action:${action_id}:${interaction}`;
  dedupe(key, () => firePost("/telemetry/ui-action", body));
}
