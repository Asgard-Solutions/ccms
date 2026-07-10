/**
 * nextActionsEngine — Phase 3 Slice 1.
 *
 * Pure derivation of deterministic "Next actions" from already-loaded
 * chart data. Everything in here MUST:
 *
 *   1. Be deterministic — same input → same output.
 *   2. Operate only on structured fields the caller has already loaded.
 *   3. Emit at most one action per rule (deduplicated by `id`).
 *   4. Return actions in a stable priority order (index of ACTION_RULES).
 *   5. Explain each action in one sentence (`why` string).
 *   6. Stay strictly non-clinical — this is workflow guidance, not
 *      medical recommendation.
 *   7. Be permission-aware — actions gated behind `canWrite` are
 *      suppressed for read-only users.
 *   8. Support dismissibility only when the action is *optional*
 *      (e.g., record outcome measure). Mandatory workflow gaps
 *      (unsigned notes, missing intake) cannot be dismissed.
 *
 * Rule vocabulary is fixed. Adding a new rule requires updating the
 * telemetry allow-list in `services/telemetry/router.py` (NextActionId
 * literal) + `SCHEMA.md` in the same change.
 */

// ---- rule ids (stable priority order) -----------------------------
export const NEXT_ACTION_IDS = [
  "sign-unsigned-note",
  "complete-missing-documentation",
  "attach-or-link-diagnosis",
  "open-blocked-billing-readiness",
  "review-billing-warning",
  "schedule-due-or-overdue-reexam",
  "schedule-remaining-planned-visits",
  "review-missing-required-intake",
  "record-configured-outcome-measure",
];

const DISMISSIBLE_IDS = new Set([
  // Optional workflow — clinician may knowingly skip.
  "record-configured-outcome-measure",
  "schedule-remaining-planned-visits",
]);

// ---- helpers ------------------------------------------------------
function daysBetween(iso, now) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.floor((now - t) / (1000 * 60 * 60 * 24));
}

function hasBillingPermission(input) {
  // Billing signal is null when the caller lacks permission (per
  // ClinicalTabV2's aggregate fetch). We treat null as "no access".
  return input.billingAggregate !== null && input.billingAggregate !== undefined;
}

// ---- rules --------------------------------------------------------
// Each rule receives the assembled `input` object and returns either
// `null` (rule not applicable) or an action descriptor. The engine
// concatenates non-null results, respecting the id order above and
// any per-user dismissals.
const RULES = {
  "sign-unsigned-note": (input) => {
    if (!input.canWrite) return null;
    const openNotes = input.summary?.notes?.open || 0;
    const openInitialExams = input.summary?.initial_exams?.open || 0;
    const openReExams = input.summary?.re_exams?.open || 0;
    const total = openNotes + openInitialExams + openReExams;
    if (total <= 0) return null;
    return {
      id: "sign-unsigned-note",
      label: "Sign unsigned note",
      why: `${total} unsigned document${total === 1 ? "" : "s"} in this chart require a signature.`,
      target: { section: "encounters" },
      tone: "warning",
    };
  },
  "complete-missing-documentation": (input) => {
    if (!input.canWrite) return null;
    const groups = input.encounterGroups || [];
    const missing = groups.filter((g) => g?.status?.documentation === "missing").length;
    if (missing <= 0) return null;
    return {
      id: "complete-missing-documentation",
      label: "Complete missing documentation",
      why: `${missing} visit${missing === 1 ? " has" : "s have"} no attached note yet.`,
      target: { section: "encounters", filter: "missing_note" },
      tone: "warning",
    };
  },
  "attach-or-link-diagnosis": (input) => {
    if (!input.canWrite) return null;
    // Fires when there's an active encounter AND no primary diagnosis
    // exists (workflow gap, not a clinical judgement — the record just
    // lacks a linkage).
    const hasActiveWork =
      (input.summary?.notes?.open || 0) > 0 ||
      (input.encounterGroups || []).some(
        (g) => g?.status?.workflow === "in_progress",
      );
    if (!hasActiveWork) return null;
    if (input.primaryDx) return null;
    return {
      id: "attach-or-link-diagnosis",
      label: "Attach or link diagnosis to encounter",
      why: "There is active documentation but no primary diagnosis linked on the chart.",
      target: { section: "diagnoses" },
      tone: "warning",
    };
  },
  "open-blocked-billing-readiness": (input) => {
    if (!hasBillingPermission(input)) return null;
    const blocked = input.billingAggregate?.blocked_count || 0;
    if (blocked <= 0) return null;
    return {
      id: "open-blocked-billing-readiness",
      label: "Open blocked billing-readiness issue",
      why: `${blocked} visit${blocked === 1 ? "" : "s"} cannot be billed until the flagged readiness issue is resolved.`,
      target: { section: "encounters", filter: "billing" },
      tone: "destructive",
    };
  },
  "review-billing-warning": (input) => {
    if (!hasBillingPermission(input)) return null;
    const blocked = input.billingAggregate?.blocked_count || 0;
    const warnings = input.billingAggregate?.warning_count || 0;
    // If a blocked action already surfaced, keep this row deduplicated
    // to avoid double-billing (see rule ordering above). Only fire when
    // warnings exist *and* nothing is blocked.
    if (blocked > 0) return null;
    if (warnings <= 0) return null;
    return {
      id: "review-billing-warning",
      label: "Review billing warning",
      why: `${warnings} billing warning${warnings === 1 ? "" : "s"} require${warnings === 1 ? "s" : ""} review before the claim is sent.`,
      target: { section: "encounters", filter: "billing" },
      tone: "warning",
    };
  },
  "schedule-due-or-overdue-reexam": (input) => {
    if (!input.canWrite) return null;
    const reExamDue = input.reExamDue;
    if (!reExamDue) return null;
    const now = input.now ?? Date.now();
    const daysAgo = daysBetween(reExamDue, now);
    if (daysAgo == null) return null;
    // Overdue (past) OR due within 7 days.
    if (daysAgo < -7) return null;
    return {
      id: "schedule-due-or-overdue-reexam",
      label: "Schedule due or overdue re-exam",
      why:
        daysAgo >= 0
          ? "The scheduled re-exam date has passed and no new re-exam is on the calendar."
          : "The next re-exam is due within a week and no visit is scheduled.",
      target: { section: "care-plan" },
      tone: daysAgo >= 0 ? "destructive" : "warning",
    };
  },
  "schedule-remaining-planned-visits": (input) => {
    if (!input.canWrite) return null;
    const plan = input.activePlan;
    if (!plan) return null;
    const planned = plan.total_visits_planned ?? plan.visits_planned ?? null;
    const completed = plan.visits_completed ?? 0;
    const scheduled = plan.visits_scheduled ?? 0;
    if (planned == null) return null;
    const remaining = planned - completed - scheduled;
    if (remaining <= 0) return null;
    return {
      id: "schedule-remaining-planned-visits",
      label: "Schedule remaining planned visits",
      why: `${remaining} planned visit${remaining === 1 ? "" : "s"} on the active plan ${remaining === 1 ? "is" : "are"} not on the calendar yet.`,
      target: { section: "care-plan" },
      tone: "info",
    };
  },
  "review-missing-required-intake": (input) => {
    if (!input.canWrite) return null;
    if (!(input.missingIntakeCount > 0)) return null;
    return {
      id: "review-missing-required-intake",
      label: "Review missing required intake information",
      why: `${input.missingIntakeCount} required intake field${
        input.missingIntakeCount === 1 ? "" : "s"
      } ${input.missingIntakeCount === 1 ? "is" : "are"} not yet filled in.`,
      target: { section: "history" },
      tone: "warning",
    };
  },
  "record-configured-outcome-measure": (input) => {
    if (!input.canWrite) return null;
    // Optional (dismissible). Fires when the chart has active plan +
    // configured outcome instruments AND no outcome measurement has
    // been recorded in the last 14 days.
    const plan = input.activePlan;
    if (!plan) return null;
    const configured = (plan.configured_outcome_measures || []).length;
    if (configured <= 0) return null;
    const lastRecordedIso = input.summary?.outcomes?.last_recorded_at;
    if (lastRecordedIso) {
      const days = daysBetween(lastRecordedIso, input.now ?? Date.now());
      if (days != null && days < 14) return null;
    }
    return {
      id: "record-configured-outcome-measure",
      label: "Record configured outcome measure",
      why: "The active plan has a configured outcome instrument and no recent measurement is on file.",
      target: { section: "outcomes" },
      tone: "info",
    };
  },
};

/**
 * Given the current chart snapshot, return the ordered, deduplicated
 * list of next actions.
 *
 * @param {object} input
 * @param {boolean} input.canWrite
 * @param {object|null} input.summary                — /clinical/summary
 * @param {object|null} input.activePlan             — active treatment plan
 * @param {object|null} input.primaryDx              — pickPrimaryDiagnosis(...)
 * @param {number}      input.missingIntakeCount
 * @param {string|null} input.reExamDue              — ISO date
 * @param {object|null} input.billingAggregate       — /billing-readiness/aggregate ({} or null when hidden)
 * @param {Array}       input.encounterGroups        — grouped encounters (may be empty)
 * @param {Set<string>} [input.dismissedIds]         — user-dismissed optional actions (session scope)
 * @param {number}      [input.now]                  — override for tests
 * @returns {Array<{id,label,why,target,tone,dismissible}>}
 */
export function deriveNextActions(input) {
  const dismissed = input.dismissedIds instanceof Set ? input.dismissedIds : new Set();
  const out = [];
  const seen = new Set();
  for (const id of NEXT_ACTION_IDS) {
    if (seen.has(id)) continue;
    const rule = RULES[id];
    if (!rule) continue;
    const action = rule(input);
    if (!action) continue;
    const dismissible = DISMISSIBLE_IDS.has(action.id);
    if (dismissible && dismissed.has(action.id)) continue;
    out.push({ ...action, dismissible });
    seen.add(action.id);
  }
  return out;
}

export const __rulesForTest = RULES;
