/**
 * Phase 3 Slice 6 — flag-matrix contract & rollback safety.
 *
 * Because the jest resolver in this CRA/craco setup doesn't understand
 * webpack's `@` alias or `react-router-dom` v7's `exports` field, we
 * exercise the flag surface at the registry layer rather than the
 * full render layer. That still catches the regressions we care
 * about — bad flag defaults, missing parent chain, invalid env
 * mapping — while an in-browser render test (`test_slice6_ui.py` on
 * the backend E2E suite) covers the "does it actually render" side.
 *
 * The claim we assert here per the spec:
 *
 *   "No flag combination may produce invalid JSX or blank rendering."
 *
 * With a well-formed registry, every legal combination reduces to a
 * boolean per flag; the ClinicalTabV2 render tree gates every optional
 * subtree behind a boolean read from that registry. The registry
 * therefore is the safety-critical surface.
 */
const {
  isFlagOn,
  setFlagOverride,
  FLAG_KEYS,
  FLAG_PARENTS,
  FLAG_DEFAULTS,
  FLAG_ENV_VARS,
} = require("../../utils/featureFlags");

describe("clinical redesign flag registry", () => {
  test("registry advertises the full expected key set", () => {
    const expected = [
      "clinicalRedesign",
      "clinicalRedesignPhase2WaveA",
      "clinicalRedesignPhase2WaveB",
      "clinicalRedesignPhase3",
      "clinicalRedesignPhase3Slice3",
      "clinicalRedesignPhase3Slice4",
      "clinicalRedesignPhase3Slice5",
      "clinicalRedesignPhase3Slice6",
    ];
    for (const key of expected) {
      expect(FLAG_KEYS).toContain(key);
    }
  });

  test("every child flag declares an existing parent", () => {
    for (const [child, parent] of Object.entries(FLAG_PARENTS)) {
      expect(FLAG_KEYS).toContain(child);
      expect(FLAG_KEYS).toContain(parent);
    }
  });

  test("every registered flag has a default and an env-var mapping", () => {
    for (const key of FLAG_KEYS) {
      expect(FLAG_DEFAULTS[key]).toMatch(/^(on|off)$/);
      expect(FLAG_ENV_VARS[key]).toMatch(/^REACT_APP_CLINICAL_REDESIGN/);
    }
  });

  test("all Phase 3 slices default on", () => {
    for (const key of [
      "clinicalRedesignPhase3Slice3",
      "clinicalRedesignPhase3Slice4",
      "clinicalRedesignPhase3Slice5",
      "clinicalRedesignPhase3Slice6",
    ]) {
      expect(FLAG_DEFAULTS[key]).toBe("on");
    }
  });
});

describe("clinical redesign flag matrix — parent invalidation", () => {
  const CLEAR = () => FLAG_KEYS.forEach((k) => setFlagOverride(k, null));
  afterEach(CLEAR);

  test("parent off disables every descendant even if the child override is on", () => {
    // Force parent off, every child on.
    setFlagOverride("clinicalRedesign", "off");
    for (const child of [
      "clinicalRedesignPhase2WaveA",
      "clinicalRedesignPhase2WaveB",
      "clinicalRedesignPhase3",
      "clinicalRedesignPhase3Slice3",
      "clinicalRedesignPhase3Slice4",
      "clinicalRedesignPhase3Slice5",
      "clinicalRedesignPhase3Slice6",
    ]) {
      setFlagOverride(child, "on");
      expect(isFlagOn(child)).toBe(false);
    }
  });

  test("phase 3 off disables every slice even if the slice override is on", () => {
    setFlagOverride("clinicalRedesign", "on");
    setFlagOverride("clinicalRedesignPhase3", "off");
    for (const slice of [
      "clinicalRedesignPhase3Slice3",
      "clinicalRedesignPhase3Slice4",
      "clinicalRedesignPhase3Slice5",
      "clinicalRedesignPhase3Slice6",
    ]) {
      setFlagOverride(slice, "on");
      expect(isFlagOn(slice)).toBe(false);
    }
  });

  test("each slice is independently rollback-safe", () => {
    // Parent + phase 3 on; each slice individually off must not turn
    // off any of its siblings.
    setFlagOverride("clinicalRedesign", "on");
    setFlagOverride("clinicalRedesignPhase3", "on");
    const slices = [
      "clinicalRedesignPhase3Slice3",
      "clinicalRedesignPhase3Slice4",
      "clinicalRedesignPhase3Slice5",
      "clinicalRedesignPhase3Slice6",
    ];
    for (const s of slices) setFlagOverride(s, "on");
    for (const target of slices) {
      setFlagOverride(target, "off");
      expect(isFlagOn(target)).toBe(false);
      for (const sibling of slices) {
        if (sibling !== target) expect(isFlagOn(sibling)).toBe(true);
      }
      setFlagOverride(target, "on");
    }
  });

  test("environment default on is overridable by user off", () => {
    setFlagOverride("clinicalRedesignPhase3Slice5", "off");
    expect(isFlagOn("clinicalRedesignPhase3Slice5")).toBe(false);
    setFlagOverride("clinicalRedesignPhase3Slice5", null); // clear override
    // Falls back to registered default (on) provided parents remain on.
    setFlagOverride("clinicalRedesign", null);
    setFlagOverride("clinicalRedesignPhase3", null);
    expect(isFlagOn("clinicalRedesignPhase3Slice5")).toBe(true);
  });
});
