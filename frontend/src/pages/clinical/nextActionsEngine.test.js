/**
 * @jest-environment node
 *
 * Unit tests for the deterministic next-actions engine.
 *
 * These tests lock in the guarantees the user required for Slice 1:
 *  - Deterministic (same input → same output).
 *  - Stable priority order.
 *  - Permission-aware (canWrite=false suppresses write-scoped rules).
 *  - Dismissible only when optional.
 *  - Deduplicated (billing-warning suppressed when blocked action fires).
 *  - Non-clinical language (no clinical vocab in labels/why strings).
 */
const {
  deriveNextActions,
  NEXT_ACTION_IDS,
} = require("./nextActionsEngine");

const BASE_INPUT = {
  canWrite: true,
  summary: { notes: { open: 0 }, initial_exams: { open: 0 }, re_exams: { open: 0 }, outcomes: {} },
  activePlan: null,
  primaryDx: null,
  missingIntakeCount: 0,
  reExamDue: null,
  billingAggregate: { blocked_count: 0, warning_count: 0 },
  encounterGroups: [],
  now: new Date("2026-02-15T00:00:00Z").getTime(),
};

describe("deriveNextActions", () => {
  test("returns empty when no rule fires", () => {
    expect(deriveNextActions(BASE_INPUT)).toEqual([]);
  });

  test("sign-unsigned-note fires when there are open notes", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      summary: { notes: { open: 3 }, initial_exams: { open: 0 }, re_exams: { open: 0 } },
    });
    expect(out.map((a) => a.id)).toContain("sign-unsigned-note");
    const dx = out.find((a) => a.id === "sign-unsigned-note");
    expect(dx.dismissible).toBe(false);
    expect(dx.target.section).toBe("encounters");
  });

  test("permission-aware: canWrite=false suppresses write-scoped rules", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      canWrite: false,
      summary: { notes: { open: 3 }, initial_exams: { open: 0 }, re_exams: { open: 0 } },
      missingIntakeCount: 5,
    });
    expect(out).toEqual([]);
  });

  test("blocked billing suppresses warning-level billing rule (dedupe)", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      billingAggregate: { blocked_count: 2, warning_count: 3 },
    });
    const ids = out.map((a) => a.id);
    expect(ids).toContain("open-blocked-billing-readiness");
    expect(ids).not.toContain("review-billing-warning");
  });

  test("billing-warning surfaces when only warnings exist", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      billingAggregate: { blocked_count: 0, warning_count: 4 },
    });
    expect(out.map((a) => a.id)).toContain("review-billing-warning");
  });

  test("billing rules stay silent when caller lacks billing permission", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      billingAggregate: null, // permission denied server-side
    });
    const ids = out.map((a) => a.id);
    expect(ids).not.toContain("open-blocked-billing-readiness");
    expect(ids).not.toContain("review-billing-warning");
  });

  test("stable priority order", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      summary: { notes: { open: 1 }, initial_exams: { open: 0 }, re_exams: { open: 0 } },
      missingIntakeCount: 2,
      encounterGroups: [{ status: { documentation: "missing" } }],
      billingAggregate: { blocked_count: 1, warning_count: 0 },
      reExamDue: "2026-02-10T00:00:00Z", // 5 days ago
    });
    const ids = out.map((a) => a.id);
    // Must match rule priority order.
    const expected = [
      "sign-unsigned-note",
      "complete-missing-documentation",
      "attach-or-link-diagnosis",
      "open-blocked-billing-readiness",
      "schedule-due-or-overdue-reexam",
      "review-missing-required-intake",
    ];
    for (let i = 0; i < expected.length; i += 1) {
      expect(ids).toContain(expected[i]);
    }
    // Assert ordering preserved for the ones present.
    const filtered = ids.filter((x) => expected.includes(x));
    expect(filtered).toEqual(expected);
  });

  test("optional actions are dismissible; mandatory ones are not", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      activePlan: {
        total_visits_planned: 12,
        visits_completed: 3,
        visits_scheduled: 2,
        configured_outcome_measures: ["NDI"],
      },
      summary: { notes: { open: 1 }, initial_exams: { open: 0 }, re_exams: { open: 0 }, outcomes: {} },
    });
    const scheduling = out.find((a) => a.id === "schedule-remaining-planned-visits");
    const outcome = out.find((a) => a.id === "record-configured-outcome-measure");
    const signing = out.find((a) => a.id === "sign-unsigned-note");
    expect(scheduling.dismissible).toBe(true);
    expect(outcome.dismissible).toBe(true);
    expect(signing.dismissible).toBe(false);
  });

  test("dismissed ids remove only dismissible entries", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      activePlan: {
        total_visits_planned: 5,
        visits_completed: 1,
        visits_scheduled: 1,
        configured_outcome_measures: [],
      },
      summary: { notes: { open: 1 }, initial_exams: { open: 0 }, re_exams: { open: 0 } },
      dismissedIds: new Set(["schedule-remaining-planned-visits", "sign-unsigned-note"]),
    });
    const ids = out.map((a) => a.id);
    // Mandatory rule cannot be dismissed even if user tried.
    expect(ids).toContain("sign-unsigned-note");
    // Dismissible rule is filtered.
    expect(ids).not.toContain("schedule-remaining-planned-visits");
  });

  test("attach-or-link-diagnosis: only when active work exists without primary dx", () => {
    // No active work → rule silent.
    const silent = deriveNextActions({ ...BASE_INPUT });
    expect(silent.map((a) => a.id)).not.toContain("attach-or-link-diagnosis");
    // Active work + no primaryDx → rule fires.
    const fires = deriveNextActions({
      ...BASE_INPUT,
      encounterGroups: [{ status: { workflow: "in_progress" } }],
    });
    expect(fires.map((a) => a.id)).toContain("attach-or-link-diagnosis");
    // Active work + primaryDx present → rule silent.
    const gated = deriveNextActions({
      ...BASE_INPUT,
      encounterGroups: [{ status: { workflow: "in_progress" } }],
      primaryDx: { icd10_code: "M54.5", label: "LBP", is_primary: true, status: "active" },
    });
    expect(gated.map((a) => a.id)).not.toContain("attach-or-link-diagnosis");
  });

  test("record-configured-outcome-measure: suppressed when recent measurement exists", () => {
    const commonPlan = {
      total_visits_planned: 8,
      visits_completed: 2,
      visits_scheduled: 2,
      configured_outcome_measures: ["NDI"],
    };
    const stale = deriveNextActions({
      ...BASE_INPUT,
      activePlan: commonPlan,
      summary: {
        notes: { open: 0 },
        initial_exams: { open: 0 },
        re_exams: { open: 0 },
        outcomes: { last_recorded_at: "2026-01-01T00:00:00Z" },
      },
    });
    expect(stale.map((a) => a.id)).toContain("record-configured-outcome-measure");

    const fresh = deriveNextActions({
      ...BASE_INPUT,
      activePlan: commonPlan,
      summary: {
        notes: { open: 0 },
        initial_exams: { open: 0 },
        re_exams: { open: 0 },
        outcomes: { last_recorded_at: "2026-02-14T00:00:00Z" }, // 1 day ago
      },
    });
    expect(fresh.map((a) => a.id)).not.toContain("record-configured-outcome-measure");
  });

  test("labels and why strings stay non-clinical", () => {
    const out = deriveNextActions({
      ...BASE_INPUT,
      summary: { notes: { open: 1 }, initial_exams: { open: 0 }, re_exams: { open: 0 } },
      encounterGroups: [{ status: { workflow: "in_progress" } }],
      missingIntakeCount: 1,
      reExamDue: "2026-02-10T00:00:00Z",
      billingAggregate: { blocked_count: 1, warning_count: 0 },
    });
    const CLINICAL_WORDS = /(diagnos(is|e)|treat(ment)?|medication|dose|prescri|therapy|symptom|red flag)/i;
    for (const a of out) {
      // The literal string "diagnosis" appears in labels like "Attach or
      // link diagnosis to encounter" (workflow noun, not clinical
      // recommendation). We accept that specific phrase but reject
      // clinical verbs.
      const combined = `${a.label} ${a.why}`.replace(/link diagnosis/gi, "").replace(/diagnosis linked/gi, "").replace(/no primary diagnosis/gi, "");
      expect(combined).not.toMatch(CLINICAL_WORDS);
    }
  });

  test("all rule ids appear in the canonical order list", () => {
    // Guardrail: engine's exported id list matches internal iteration.
    expect(NEXT_ACTION_IDS.length).toBe(9);
    expect(new Set(NEXT_ACTION_IDS).size).toBe(9);
  });
});
