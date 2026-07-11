/**
 * @jest-environment node
 *
 * Unit tests for the Phase 3 Slice 3 outcome derivation helpers.
 *
 * Every guardrail the Slice 3 brief called out has a corresponding
 * test below. Adding a rule to the engine requires adding a test.
 */
const {
  groupByInstrument,
  deriveSeries,
  formatDelta,
  windowSeriesToLastMonths,
  buildMilestones,
  deriveOutcomeSuggestions,
  SUPPORTED_INSTRUMENTS,
  SUGGESTABLE_INSTRUMENT_KEYS,
} = require("./outcomeSeriesHelpers");

const now = Date.parse("2026-02-15T00:00:00Z");

function entry(over) {
  return {
    id: "e_" + Math.random().toString(36).slice(2, 10),
    measure_type: "ndi",
    label: "Neck Disability Index",
    score: 30,
    captured_at: "2026-01-01T10:00:00Z",
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:00Z",
    source: "provider_charted",
    ...over,
  };
}

describe("groupByInstrument", () => {
  test("groups by (measure_type, label) and drops unknown instruments", () => {
    const groups = groupByInstrument([
      entry({ measure_type: "ndi" }),
      entry({ measure_type: "oswestry", label: "Oswestry" }),
      entry({ measure_type: "unknown_measure" }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.instrument_key).sort()).toEqual([
      "ndi", "oswestry",
    ]);
  });
});

describe("deriveSeries — happy path", () => {
  test("baseline / latest / previous / deltas", () => {
    const s = deriveSeries({
      instrument_key: "ndi",
      label: "NDI",
      entries: [
        entry({ score: 40, captured_at: "2026-01-01T10:00:00Z" }),
        entry({ score: 28, captured_at: "2026-01-15T10:00:00Z" }),
        entry({ score: 22, captured_at: "2026-02-01T10:00:00Z" }),
      ],
    });
    expect(s.baseline.score).toBe(40);
    expect(s.latest.score).toBe(22);
    expect(s.previous.score).toBe(28);
    expect(s.change_since_baseline).toBe(-18);
    expect(s.change_since_prev).toBe(-6);
    expect(s.insufficient_baseline).toBe(false);
    expect(s.points).toHaveLength(3);
  });
});

describe("deriveSeries — guardrails", () => {
  test("missing baseline (only one usable point)", () => {
    const s = deriveSeries({
      instrument_key: "ndi",
      label: "NDI",
      entries: [entry({ score: 40 })],
    });
    expect(s.insufficient_baseline).toBe(true);
    expect(s.change_since_baseline).toBeNull();
    expect(s.change_since_prev).toBeNull();
    expect(s.latest.score).toBe(40);
  });

  test("empty entries → no baseline, no latest", () => {
    const s = deriveSeries({ instrument_key: "ndi", entries: [] });
    expect(s.baseline).toBeNull();
    expect(s.latest).toBeNull();
    expect(s.insufficient_baseline).toBe(true);
  });

  test("partial records (null / NaN score) are counted but not plotted", () => {
    const s = deriveSeries({
      instrument_key: "ndi",
      entries: [
        entry({ score: 40, captured_at: "2026-01-01T00:00:00Z" }),
        entry({ score: null, captured_at: "2026-01-05T00:00:00Z" }),
        entry({ score: "not-a-number", captured_at: "2026-01-10T00:00:00Z" }),
        entry({ score: 22, captured_at: "2026-02-01T00:00:00Z" }),
      ],
    });
    expect(s.partial_count).toBe(2);
    expect(s.usable_count).toBe(2);
    expect(s.points.map((p) => p.score)).toEqual([40, 22]);
  });

  test("duplicate captured_at day: winner is latest updated_at; loser stays in superseded", () => {
    const s = deriveSeries({
      instrument_key: "ndi",
      entries: [
        entry({
          id: "a", score: 30, captured_at: "2026-02-01T09:00:00Z",
          created_at: "2026-02-01T09:00:00Z",
          updated_at: "2026-02-01T09:00:00Z",
        }),
        entry({
          id: "b", score: 25, captured_at: "2026-02-01T13:00:00Z",
          created_at: "2026-02-01T13:00:00Z",
          updated_at: "2026-02-05T00:00:00Z",
        }),
      ],
    });
    expect(s.points).toHaveLength(1);
    expect(s.points[0].entry_id).toBe("b");
    expect(s.superseded).toHaveLength(1);
    expect(s.superseded[0].entry_id).toBe("a");
  });

  test("amended entries are flagged (updated_at != created_at)", () => {
    const s = deriveSeries({
      instrument_key: "ndi",
      entries: [
        entry({
          id: "a", score: 30, captured_at: "2026-01-01T00:00:00Z",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
        entry({
          id: "b", score: 22, captured_at: "2026-02-01T00:00:00Z",
          created_at: "2026-02-01T00:00:00Z",
          updated_at: "2026-02-10T00:00:00Z",
        }),
      ],
    });
    expect(s.points[0].is_amended).toBe(false);
    expect(s.points[1].is_amended).toBe(true);
  });

  test("no clinical inference — output shape has no `improved`/`worse` fields", () => {
    const s = deriveSeries({
      instrument_key: "ndi",
      entries: [entry({ score: 40 }), entry({ score: 22, captured_at: "2026-02-01T00:00:00Z" })],
    });
    for (const forbidden of [
      "improved",
      "improving",
      "deteriorated",
      "worsening",
      "clinically_significant",
      "direction",
    ]) {
      expect(forbidden in s).toBe(false);
    }
  });
});

describe("formatDelta", () => {
  test("nulls / non-finite → em-dash", () => {
    expect(formatDelta(null)).toBe("—");
    expect(formatDelta(NaN)).toBe("—");
    expect(formatDelta(undefined)).toBe("—");
  });
  test("zero uses ±0 (never bare 0)", () => {
    expect(formatDelta(0)).toBe("±0");
  });
  test("negative uses unicode minus", () => {
    expect(formatDelta(-8)).toBe("−8");
  });
  test("positive uses plus", () => {
    expect(formatDelta(2)).toBe("+2");
  });
  test("decimals", () => {
    expect(formatDelta(-1.234, { decimals: 1 })).toBe("−1.2");
  });
});

describe("windowSeriesToLastMonths", () => {
  test("drops points older than window", () => {
    const points = [
      { captured_at: "2020-01-01T00:00:00Z", score: 1 },
      { captured_at: new Date().toISOString(), score: 2 },
    ];
    const out = windowSeriesToLastMonths(points, 12);
    expect(out).toHaveLength(1);
    expect(out[0].score).toBe(2);
  });
  test("noop when months <= 0", () => {
    const points = [{ captured_at: "2020-01-01T00:00:00Z", score: 1 }];
    expect(windowSeriesToLastMonths(points, 0)).toBe(points);
  });
});

describe("buildMilestones", () => {
  test("emits start / reexam / discharge in date order", () => {
    const m = buildMilestones({
      activePlan: {
        start_date: "2026-01-01T00:00:00Z",
        re_exam_date: "2026-03-01T00:00:00Z",
        discharged_at: "2026-04-01T00:00:00Z",
      },
    });
    expect(m.map((x) => x.kind)).toEqual([
      "plan_start", "reexam_due", "plan_discharged",
    ]);
  });
  test("empty plan yields no milestones", () => {
    expect(buildMilestones({ activePlan: null })).toEqual([]);
    expect(buildMilestones({ activePlan: {} })).toEqual([]);
  });
});

describe("deriveOutcomeSuggestions", () => {
  const base = {
    canWrite: true,
    activePlan: { configured_outcome_measures: ["ndi", "pain_vas"] },
    entries: [],
    now,
  };

  test("no suggestions when read-only", () => {
    expect(deriveOutcomeSuggestions({ ...base, canWrite: false })).toEqual([]);
  });

  test("no suggestions when plan configures nothing", () => {
    expect(
      deriveOutcomeSuggestions({ ...base, activePlan: { configured_outcome_measures: [] } }),
    ).toEqual([]);
  });

  test("suggests instruments with no entries at all", () => {
    const out = deriveOutcomeSuggestions(base);
    expect(out.map((s) => s.instrument_key).sort()).toEqual(["ndi", "pain_vas"]);
    for (const s of out) {
      expect(s.reason).toBe("no_record_on_file");
      expect(s.dismissible).toBe(true);
    }
  });

  test("suggests instruments whose latest entry is stale (> 30d)", () => {
    const out = deriveOutcomeSuggestions({
      ...base,
      entries: [
        // 60 days old (stale)
        entry({ measure_type: "ndi", score: 20, captured_at: "2025-12-15T00:00:00Z" }),
        // 5 days old (fresh — no suggestion)
        entry({ measure_type: "pain_vas", score: 3, captured_at: "2026-02-10T00:00:00Z" }),
      ],
    });
    expect(out.map((s) => s.instrument_key)).toEqual(["ndi"]);
    expect(out[0].reason).toBe("stale_record");
  });

  test("dismissed suggestions do not resurface", () => {
    const out = deriveOutcomeSuggestions({
      ...base,
      dismissed: new Set(["ndi"]),
    });
    expect(out.map((s) => s.instrument_key)).toEqual(["pain_vas"]);
  });

  test("unsupported instrument keys in configured list are silently ignored", () => {
    const out = deriveOutcomeSuggestions({
      ...base,
      activePlan: { configured_outcome_measures: ["ndi", "totally_made_up"] },
    });
    expect(out.map((s) => s.instrument_key)).toEqual(["ndi"]);
  });

  test("wording is non-clinical (no improvement/worse/significant vocabulary)", () => {
    const out = deriveOutcomeSuggestions(base);
    const badWords = /(improv|deterior|significan|clinical|worse|better)/i;
    for (const s of out) {
      expect(s.why).not.toMatch(badWords);
      expect(s.label).not.toMatch(badWords);
    }
  });
});

describe("SUGGESTABLE_INSTRUMENT_KEYS", () => {
  test("contains every SUPPORTED_INSTRUMENTS key and nothing else", () => {
    expect(new Set(SUGGESTABLE_INSTRUMENT_KEYS)).toEqual(
      new Set(Object.keys(SUPPORTED_INSTRUMENTS)),
    );
  });
});
