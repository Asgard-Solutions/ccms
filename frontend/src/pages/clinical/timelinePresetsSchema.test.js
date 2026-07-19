/**
 * @jest-environment node
 *
 * Contract tests for the durable timeline-preset sanitizer.
 *
 * Focus: the sanitizer is the last line of defence against PHI / free
 * text leaking into `/me/preferences.clinical_ui_defaults`. It MUST:
 *   1. Drop every key not in the preset allow-list.
 *   2. Drop every value that isn't in its dimension's slug vocabulary.
 *   3. Never let `episode_ids`, `q`, `date_from`, `date_to`, or record
 *      ids survive into the sanitized output.
 *   4. Dedupe repeated values.
 */
const {
  sanitizePresetFilters,
  detectStaleness,
  transientToQueryParams,
  emptyTransientFilters,
  anyFilterActive,
  newPresetId,
} = require("./timelinePresetsSchema");

describe("sanitizePresetFilters", () => {
  test("drops unknown top-level keys", () => {
    const { filters, dropped } = sanitizePresetFilters({
      event_kinds: ["visit"],
      // Everything below is illegal in a saved preset:
      patient_id: "abc-123",
      encounter_id: "enc-1",
      diagnosis_codes: ["M54.2"],
      q: "positive fever",
      date_of_service: "2026-01-15",
      episode_ids: ["ep-1"],
      date_from: "2026-01-01",
      date_to: "2026-02-01",
    });
    expect(filters.event_kinds).toEqual(["visit"]);
    const droppedKeys = new Set(dropped.map((d) => d.key));
    for (const k of [
      "patient_id",
      "encounter_id",
      "diagnosis_codes",
      "q",
      "date_of_service",
      "episode_ids",
      "date_from",
      "date_to",
    ]) {
      expect(droppedKeys.has(k)).toBe(true);
    }
    expect("episode_ids" in filters).toBe(false);
    expect("q" in filters).toBe(false);
  });

  test("drops unknown slugs inside allowed keys", () => {
    const { filters, dropped } = sanitizePresetFilters({
      event_kinds: ["visit", "pizza"],
      sources: ["encounter", "not_a_source"],
      date_window: "since_forever",
    });
    expect(filters.event_kinds).toEqual(["visit"]);
    expect(filters.sources).toEqual(["encounter"]);
    expect(filters.date_window).toBeNull();
    const reasons = dropped.map((d) => d.value);
    expect(reasons).toEqual(expect.arrayContaining(["pizza", "not_a_source", "since_forever"]));
  });

  test("dedupes repeated values", () => {
    const { filters } = sanitizePresetFilters({
      event_kinds: ["visit", "visit", "outcome_entry"],
      sources: ["note", "note"],
    });
    expect(filters.event_kinds).toEqual(["visit", "outcome_entry"]);
    expect(filters.sources).toEqual(["note"]);
  });

  test("rejects bad provider ids but keeps well-formed uuids", () => {
    const good = "b99e7285-6efa-47b4-b552-2ad2920657dc";
    const { filters } = sanitizePresetFilters({
      provider_ids: [good, "<script>", "", 42, null],
    });
    expect(filters.provider_ids).toEqual([good]);
  });

  test("null/undefined input yields empty preset", () => {
    expect(sanitizePresetFilters(null).filters).toEqual({
      event_kinds: [],
      sources: [],
      provider_ids: [],
      date_window: null,
    });
  });
});

describe("detectStaleness", () => {
  test("flags providers that are echoed in filter_meta.ignored_provider_ids", () => {
    const preset = {
      id: newPresetId(),
      name: "T",
      filters: {
        event_kinds: ["visit"],
        provider_ids: ["p1", "p2"],
        sources: [],
        date_window: null,
      },
    };
    const { stale, reasons } = detectStaleness(preset, {
      ignored_provider_ids: ["p1"],
      ignored_slugs: [],
    });
    expect(stale).toBe(true);
    expect(reasons[0].key).toBe("provider_ids");
    expect(reasons[0].values).toEqual(["p1"]);
  });

  test("flags dead vocabulary slugs", () => {
    const preset = {
      id: newPresetId(),
      name: "T",
      filters: { event_kinds: ["pizza"], sources: [], provider_ids: [], date_window: null },
    };
    const { stale, reasons } = detectStaleness(preset, {
      ignored_slugs: ["pizza"],
    });
    expect(stale).toBe(true);
    expect(reasons[0].key).toBe("vocab");
  });

  test("no stale — clean preset", () => {
    const preset = {
      id: newPresetId(),
      name: "T",
      filters: { event_kinds: ["visit"], sources: [], provider_ids: [], date_window: null },
    };
    expect(detectStaleness(preset, { ignored_slugs: [], ignored_provider_ids: [] }).stale).toBe(false);
  });
});

describe("transientToQueryParams", () => {
  test("only includes non-empty fields", () => {
    const f = emptyTransientFilters();
    expect(transientToQueryParams(f)).toEqual({});
    f.event_kinds = ["visit"];
    f.q = "  cerv  ";
    f.date_window = "last_30d";
    expect(transientToQueryParams(f)).toEqual({
      event_kinds: "visit",
      q: "cerv",
      date_window: "last_30d",
    });
  });

  test("clamps q to 80 chars", () => {
    const f = emptyTransientFilters();
    f.q = "x".repeat(200);
    expect(transientToQueryParams(f).q.length).toBe(80);
  });
});

describe("anyFilterActive", () => {
  test("returns false for empty transient filter", () => {
    expect(anyFilterActive(emptyTransientFilters())).toBe(false);
  });
  test("true when episode_ids has entries", () => {
    const f = emptyTransientFilters();
    f.episode_ids = ["ep-1"];
    expect(anyFilterActive(f)).toBe(true);
  });
});
