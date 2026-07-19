/**
 * @jest-environment node
 *
 * Unit tests for the Slice 2.1 preset icon-strip derivation.
 *
 * The strip is purely presentational, and its guardrails are best
 * enforced at the derivation layer so `PresetIconStrip.jsx` stays a
 * thin wrapper. These tests exercise:
 *   - Empty presets → zero icons.
 *   - Partial presets → only the configured dimensions.
 *   - Unsupported / unknown dimensions in the input → silently
 *     ignored (never render).
 *   - Stale-detection reuse — no new stale rules invented.
 */
const { buildDimensionsForStrip } = require("./PresetIconStrip");
const { detectStaleness, newPresetId } = require("./timelinePresetsSchema");

describe("buildDimensionsForStrip", () => {
  test("empty / null / undefined preset filters → []", () => {
    expect(buildDimensionsForStrip(null)).toEqual([]);
    expect(buildDimensionsForStrip(undefined)).toEqual([]);
    expect(buildDimensionsForStrip({})).toEqual([]);
    expect(
      buildDimensionsForStrip({
        event_kinds: [],
        sources: [],
        provider_ids: [],
        date_window: null,
      }),
    ).toEqual([]);
  });

  test("partial preset renders only configured dimensions", () => {
    expect(
      buildDimensionsForStrip({
        event_kinds: ["visit"],
        sources: [],
        provider_ids: [],
        date_window: null,
      }),
    ).toEqual([{ key: "event_kinds", count: 1 }]);

    expect(
      buildDimensionsForStrip({
        event_kinds: ["visit", "outcome_entry"],
        sources: [],
        provider_ids: ["b99e7285-6efa-47b4-b552-2ad2920657dc"],
        date_window: "last_30d",
      }),
    ).toEqual([
      { key: "event_kinds", count: 2 },
      { key: "provider_ids", count: 1 },
      { key: "date_window", count: null },
    ]);
  });

  test("preserves stable dimension ordering regardless of filter key order", () => {
    const out = buildDimensionsForStrip({
      date_window: "last_7d",
      provider_ids: ["b99e7285-6efa-47b4-b552-2ad2920657dc"],
      sources: ["encounter"],
      event_kinds: ["visit"],
    });
    expect(out.map((d) => d.key)).toEqual([
      "event_kinds",
      "sources",
      "provider_ids",
      "date_window",
    ]);
  });

  test("unsupported dimensions in the input are silently ignored", () => {
    // The strip has no icon for `phases` or `episode_ids` etc. — the
    // fact that a caller passed one in must NOT render an ad-hoc icon.
    const out = buildDimensionsForStrip({
      event_kinds: ["visit"],
      phases: ["initial"],
      episode_ids: ["ep-1"],
      q: "positive fever",
      // date_from is a transient-only dimension, never persisted, but
      // we still guard against a bad caller passing it here.
      date_from: "2026-01-01",
    });
    expect(out).toEqual([{ key: "event_kinds", count: 1 }]);
  });

  test("date_window is presence-only — the actual value never surfaces", () => {
    const out = buildDimensionsForStrip({
      date_window: "last_30d",
      event_kinds: [],
      sources: [],
      provider_ids: [],
    });
    expect(out).toEqual([{ key: "date_window", count: null }]);
    // The count field is `null`, which means the strip UI does NOT
    // render any numeric or text hint that could leak the window
    // value.
  });

  test("no raw filter values appear in the output — only counts", () => {
    const filters = {
      event_kinds: ["visit", "outcome_entry", "clinical_media"],
      sources: ["encounter"],
      provider_ids: [
        "b99e7285-6efa-47b4-b552-2ad2920657dc",
        "d41b48bc-13d7-45b1-baae-4ccf8aa253f9",
      ],
      date_window: "last_365d",
    };
    const out = buildDimensionsForStrip(filters);
    for (const { key, count } of out) {
      // Only the shape (key + count) — never a raw filter value.
      expect(typeof key).toBe("string");
      expect(count === null || typeof count === "number").toBe(true);
    }
    // Sanity: JSON-stringified strip output must not contain any
    // filter value the caller supplied.
    const stringified = JSON.stringify(out);
    for (const v of filters.event_kinds) {
      expect(stringified).not.toContain(v);
    }
    for (const v of filters.provider_ids) {
      expect(stringified).not.toContain(v);
    }
    expect(stringified).not.toContain("last_365d");
  });
});

describe("staleness re-uses timelinePresetsSchema (no new rules)", () => {
  const preset = {
    id: newPresetId(),
    name: "Test",
    filters: {
      event_kinds: ["visit", "outcome_entry"],
      sources: [],
      provider_ids: ["dead-provider-uuid"],
      date_window: "last_30d",
    },
  };

  test("no staleness when filter_meta is empty", () => {
    const s = detectStaleness(preset, { ignored_slugs: [], ignored_provider_ids: [] });
    expect(s.stale).toBe(false);
  });

  test("provider staleness flows through detectStaleness", () => {
    const s = detectStaleness(preset, {
      ignored_slugs: [],
      ignored_provider_ids: ["dead-provider-uuid"],
    });
    expect(s.stale).toBe(true);
    expect(s.reasons.some((r) => r.key === "provider_ids")).toBe(true);
  });

  test("vocab staleness (a slug the caller passed in is now dropped)", () => {
    const s = detectStaleness(
      { ...preset, filters: { ...preset.filters, event_kinds: ["visit", "pizza"] } },
      { ignored_slugs: ["pizza"], ignored_provider_ids: [] },
    );
    expect(s.stale).toBe(true);
    expect(s.reasons.some((r) => r.key === "vocab" && r.value === "pizza")).toBe(true);
  });
});
