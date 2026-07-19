/**
 * Phase 3 Slice 5A/5B — pure workspaceModes registry tests.
 * Non-mutation, allow-lists, effective-mode fallback, reorder helpers.
 */
const {
  CLINICAL_WORKSPACE_MODES,
  allowedModesForRole,
  effectiveMode,
  sectionOrderForMode,
  summaryDefaultsForMode,
  resolveSummaryOrder,
  reorderSummary,
} = require("./workspaceModes");

describe("workspaceModes — allowed modes per role", () => {
  test("doctor may switch into general and provider", () => {
    const modes = allowedModesForRole("doctor");
    expect(modes).toEqual(expect.arrayContaining(["general", "provider"]));
    expect(modes).not.toEqual(expect.arrayContaining(["billing", "administrator"]));
  });

  test("staff may switch into general, front_desk, billing", () => {
    const modes = allowedModesForRole("staff");
    expect(modes).toEqual(expect.arrayContaining(["general", "front_desk", "billing"]));
    expect(modes).not.toEqual(expect.arrayContaining(["administrator"]));
  });

  test("admin roles get every mode", () => {
    for (const role of ["admin", "platform_admin", "super_admin"]) {
      expect(allowedModesForRole(role)).toEqual(
        expect.arrayContaining(CLINICAL_WORKSPACE_MODES),
      );
    }
  });

  test("unknown role falls back to general only", () => {
    expect(allowedModesForRole("marketing")).toEqual(["general"]);
  });
});

describe("workspaceModes — effectiveMode fallback", () => {
  test("returns requested mode when the role can use it", () => {
    expect(effectiveMode({ role: "doctor", requested: "provider" })).toBe("provider");
  });

  test("falls back to general when the role cannot", () => {
    expect(effectiveMode({ role: "doctor", requested: "billing" })).toBe("general");
    expect(effectiveMode({ role: "patient", requested: "administrator" })).toBe("general");
  });

  test("handles missing request", () => {
    expect(effectiveMode({ role: "doctor" })).toBe("general");
  });
});

describe("workspaceModes — section order", () => {
  test("every mode returns a full section list", () => {
    for (const mode of CLINICAL_WORKSPACE_MODES) {
      const sections = sectionOrderForMode(mode);
      expect(sections.length).toBe(8);
      expect(new Set(sections).size).toBe(sections.length);
    }
  });

  test("provider mode leads with summary and encounters", () => {
    const sections = sectionOrderForMode("provider");
    expect(sections[0]).toBe("summary");
    expect(sections.slice(0, 3)).toEqual(["summary", "encounters", "care-plan"]);
  });

  test("billing mode surfaces encounters + diagnoses early", () => {
    const sections = sectionOrderForMode("billing");
    expect(sections.slice(0, 3)).toEqual(["summary", "encounters", "diagnoses"]);
  });

  test("unknown mode falls back to general", () => {
    expect(sectionOrderForMode("nonsense")).toEqual(sectionOrderForMode("general"));
  });
});

describe("workspaceModes — summary defaults", () => {
  test("provider mode leads with next_actions", () => {
    expect(summaryDefaultsForMode("provider")[0]).toBe("next_actions");
  });

  test("billing mode leads with billing_readiness", () => {
    expect(summaryDefaultsForMode("billing")[0]).toBe("billing_readiness");
  });

  test("front_desk mode leads with next_appointment", () => {
    expect(summaryDefaultsForMode("front_desk")[0]).toBe("next_appointment");
  });

  test("administrator mode leads with data_quality", () => {
    expect(summaryDefaultsForMode("administrator")[0]).toBe("data_quality");
  });
});

describe("workspaceModes — resolveSummaryOrder", () => {
  test("keeps stored order and appends missing modules at the end", () => {
    const stored = ["active_episode", "primary_diagnosis"];
    const resolved = resolveSummaryOrder({ mode: "general", stored });
    expect(resolved.slice(0, 2)).toEqual(["active_episode", "primary_diagnosis"]);
    expect(new Set(resolved).size).toBe(resolved.length);
    // Sanity: every default slug is present.
    for (const slug of summaryDefaultsForMode("general")) {
      expect(resolved).toContain(slug);
    }
  });

  test("drops slugs that no longer exist in the registry", () => {
    const stored = ["active_episode", "nonsense_module", "primary_diagnosis"];
    const resolved = resolveSummaryOrder({ mode: "general", stored });
    expect(resolved).not.toContain("nonsense_module");
  });

  test("deduplicates repeated entries", () => {
    const stored = ["active_episode", "active_episode", "primary_diagnosis"];
    const resolved = resolveSummaryOrder({ mode: "general", stored });
    expect(resolved.filter((s) => s === "active_episode")).toHaveLength(1);
  });

  test("null/undefined stored returns mode defaults verbatim", () => {
    expect(resolveSummaryOrder({ mode: "provider", stored: null }))
      .toEqual(summaryDefaultsForMode("provider"));
  });
});

describe("workspaceModes — reorderSummary", () => {
  test("move up decreases index", () => {
    const order = ["a", "b", "c", "d"];
    const next = reorderSummary({ order, slug: "c", delta: -1 });
    expect(next).toEqual(["a", "c", "b", "d"]);
  });

  test("move down increases index", () => {
    const order = ["a", "b", "c", "d"];
    const next = reorderSummary({ order, slug: "b", delta: +1 });
    expect(next).toEqual(["a", "c", "b", "d"]);
  });

  test("clamps at boundaries", () => {
    const order = ["a", "b", "c"];
    expect(reorderSummary({ order, slug: "a", delta: -1 })).toEqual(["a", "b", "c"]);
    expect(reorderSummary({ order, slug: "c", delta: +1 })).toEqual(["a", "b", "c"]);
  });

  test("unknown slug is a no-op", () => {
    const order = ["a", "b"];
    expect(reorderSummary({ order, slug: "z", delta: -1 })).toEqual(["a", "b"]);
  });

  test("never mutates the input array", () => {
    const order = ["a", "b", "c"];
    const snapshot = [...order];
    reorderSummary({ order, slug: "a", delta: +1 });
    expect(order).toEqual(snapshot);
  });
});
