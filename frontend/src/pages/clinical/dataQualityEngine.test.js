/**
 * @jest-environment node
 */
const {
  deriveDataQualityIssues,
  RULE_IDS,
  SEVERITY_ORDER,
} = require("./dataQualityEngine");

const NOW = Date.parse("2026-02-15T00:00:00Z");

function baseInput(over) {
  return {
    canWrite: true,
    summary: { notes: { open: 0 } },
    activePlan: null,
    primaryDx: null,
    encounterGroups: [],
    imaging: [],
    episodes: [],
    outcomeEntries: [],
    now: NOW,
    ...over,
  };
}

describe("dataQualityEngine — quiet baseline", () => {
  test("clean chart yields no issues", () => {
    expect(deriveDataQualityIssues(baseInput())).toEqual([]);
  });
});

describe("dataQualityEngine — rules", () => {
  test("missing-primary-diagnosis fires only when active work exists without primary dx", () => {
    // No active work → silent
    expect(
      deriveDataQualityIssues(baseInput({ primaryDx: null })).map((r) => r.id),
    ).not.toContain("missing-primary-diagnosis");
    // Active work + no dx → fires
    const out = deriveDataQualityIssues(
      baseInput({ summary: { notes: { open: 1 } } }),
    );
    expect(out.map((r) => r.id)).toContain("missing-primary-diagnosis");
    // Active work + primaryDx present → silent
    expect(
      deriveDataQualityIssues(
        baseInput({
          summary: { notes: { open: 1 } },
          primaryDx: { icd10_code: "M54.5", is_primary: true },
        }),
      ).map((r) => r.id),
    ).not.toContain("missing-primary-diagnosis");
  });

  test("unsigned-note-older-than-7d counts stale drafts", () => {
    const eightDaysAgo = new Date(NOW - 8 * 86400_000).toISOString();
    const twoDaysAgo = new Date(NOW - 2 * 86400_000).toISOString();
    const out = deriveDataQualityIssues(
      baseInput({
        encounterGroups: [
          { status: { documentation: "draft" }, visit_at: eightDaysAgo },
          { status: { documentation: "draft" }, visit_at: twoDaysAgo },
        ],
      }),
    );
    const rule = out.find((r) => r.id === "unsigned-note-older-than-7d");
    expect(rule).toBeTruthy();
    expect(rule.count).toBe(1); // only the 8-day-old draft counts
  });

  test("encounter-missing-provider fires regardless of write scope", () => {
    const groups = [
      { status: {}, provider_id: null, provider_name: null, visit_at: "2026-02-01" },
      { status: {}, provider_id: "p1", provider_name: "Dr. A", visit_at: "2026-02-02" },
    ];
    for (const canWrite of [true, false]) {
      const out = deriveDataQualityIssues(baseInput({ encounterGroups: groups, canWrite }));
      const rule = out.find((r) => r.id === "encounter-missing-provider");
      expect(rule?.count).toBe(1);
    }
  });

  test("imaging-missing-classification counts media without modality/kind", () => {
    const out = deriveDataQualityIssues(
      baseInput({
        imaging: [
          { id: "m1", imaging_modality: null, kind: null },
          { id: "m2", kind: "X-ray" },
          { id: "m3", imaging_modality: "MRI" },
        ],
      }),
    );
    const rule = out.find((r) => r.id === "imaging-missing-classification");
    expect(rule?.count).toBe(1);
  });

  test("episode-without-encounters flags open episodes with zero visits", () => {
    const out = deriveDataQualityIssues(
      baseInput({
        episodes: [
          { id: "ep1", status: "open" },
          { id: "ep2", status: "open" },
          { id: "ep3", status: "closed" },
        ],
        encounterGroups: [{ episode_id: "ep2" }],
      }),
    );
    const rule = out.find((r) => r.id === "episode-without-encounters");
    expect(rule?.count).toBe(1); // only ep1 is open + orphaned
  });

  test("active-plan-without-configured-outcomes fires when list is empty", () => {
    const out = deriveDataQualityIssues(
      baseInput({ activePlan: { id: "p", configured_outcome_measures: [] } }),
    );
    expect(out.map((r) => r.id)).toContain("active-plan-without-configured-outcomes");
    const configured = deriveDataQualityIssues(
      baseInput({ activePlan: { id: "p", configured_outcome_measures: ["ndi"] } }),
    );
    expect(configured.map((r) => r.id)).not.toContain(
      "active-plan-without-configured-outcomes",
    );
  });

  test("duplicate-outcome-day flags instrument/day pairs with >1 entry", () => {
    const out = deriveDataQualityIssues(
      baseInput({
        outcomeEntries: [
          { measure_type: "ndi", captured_at: "2026-02-10T09:00:00Z" },
          { measure_type: "ndi", captured_at: "2026-02-10T15:00:00Z" }, // dup day
          { measure_type: "pain_vas", captured_at: "2026-02-10T09:00:00Z" },
        ],
      }),
    );
    const rule = out.find((r) => r.id === "duplicate-outcome-day");
    expect(rule?.count).toBe(1);
  });
});

describe("dataQualityEngine — guardrails", () => {
  test("read-only viewer sees only permission-agnostic rules", () => {
    const out = deriveDataQualityIssues(
      baseInput({
        canWrite: false,
        summary: { notes: { open: 3 } },
        activePlan: { configured_outcome_measures: [] }, // write-scoped
        encounterGroups: [
          { status: {}, provider_id: null, provider_name: null, visit_at: "2026-02-01" },
        ],
      }),
    );
    const ids = out.map((r) => r.id);
    // encounter-missing-provider is permission-agnostic → fires
    expect(ids).toContain("encounter-missing-provider");
    // write-scoped rules → silent
    expect(ids).not.toContain("missing-primary-diagnosis");
    expect(ids).not.toContain("active-plan-without-configured-outcomes");
  });

  test("deterministic ordering — severity first, priority second", () => {
    const eightDaysAgo = new Date(NOW - 8 * 86400_000).toISOString();
    const out = deriveDataQualityIssues(
      baseInput({
        summary: { notes: { open: 1 } },
        encounterGroups: [
          { status: { documentation: "draft" }, visit_at: eightDaysAgo, provider_id: null },
        ],
        imaging: [{ imaging_modality: null, kind: null }],
      }),
    );
    // severities: warning (missing-primary-diagnosis, unsigned-note-older-than-7d) then info
    const severities = out.map((r) => r.severity);
    for (let i = 1; i < severities.length; i += 1) {
      expect(SEVERITY_ORDER[severities[i]]).toBeGreaterThanOrEqual(
        SEVERITY_ORDER[severities[i - 1]],
      );
    }
  });

  test("labels + why strings are non-clinical", () => {
    const eightDaysAgo = new Date(NOW - 8 * 86400_000).toISOString();
    const out = deriveDataQualityIssues(
      baseInput({
        summary: { notes: { open: 1 } },
        encounterGroups: [
          { status: { documentation: "draft" }, visit_at: eightDaysAgo, provider_id: null },
          { status: { documentation: "missing" }, provider_id: "p1", visit_at: "2026-02-01" },
        ],
        imaging: [{ imaging_modality: null, kind: null }],
        episodes: [{ id: "ep1", status: "open" }],
        activePlan: { configured_outcome_measures: [] },
        outcomeEntries: [
          { measure_type: "ndi", captured_at: "2026-02-10T09:00:00Z" },
          { measure_type: "ndi", captured_at: "2026-02-10T15:00:00Z" },
        ],
      }),
    );
    const bad = /(improv|deterior|significan|worse|better|diagnos(ed|ing))/i;
    for (const row of out) {
      expect(`${row.label} ${row.why}`).not.toMatch(bad);
    }
  });

  test("output never carries patient/record identifiers or free text", () => {
    const out = deriveDataQualityIssues(
      baseInput({
        summary: { notes: { open: 1 } },
        encounterGroups: [
          {
            status: { documentation: "draft" },
            visit_at: "2026-01-01",
            episode_id: "leak-me-ep-uuid",
            provider_id: "leak-me-prov-uuid",
          },
        ],
      }),
    );
    const stringified = JSON.stringify(out);
    expect(stringified).not.toContain("leak-me-ep-uuid");
    expect(stringified).not.toContain("leak-me-prov-uuid");
  });

  test("engine is non-mutating — input arrays are not modified", () => {
    const groups = [
      { status: { documentation: "draft" }, visit_at: "2026-01-01", provider_id: null },
    ];
    const before = JSON.stringify(groups);
    deriveDataQualityIssues(baseInput({ encounterGroups: groups }));
    expect(JSON.stringify(groups)).toBe(before);
  });

  test("all rule ids in canonical list are unique", () => {
    expect(RULE_IDS.length).toBe(new Set(RULE_IDS).size);
  });
});
