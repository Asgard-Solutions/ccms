/**
 * Phase 3 Slice 2 — Timeline filter + saved-preset vocabularies.
 *
 * MUST stay in lockstep with:
 *   - backend/services/identity/models.py   (TimelineEventKind, TimelineSource,
 *                                            TimelineDateWindow, ClinicalUIDefaults)
 *   - backend/services/clinical/grouped_router.py (_TIMELINE_KINDS etc.)
 *
 * Design rules (see `PHASE3_SLICE2_CONTRACTS.md`):
 *   - Every filter dimension exposes an allow-listed vocabulary of
 *     structured slugs. No free text ever ends up in a saved preset.
 *   - Preset filter shape is a strict subset of the transient filter
 *     shape — patient-scoped fields (`episode_ids`, `q`) are NEVER
 *     stored in durable preferences.
 *   - Callers pass raw candidate values through `sanitizePresetFilters`
 *     before serialising to `/me/preferences` to catch drift/attacks.
 */

export const TIMELINE_EVENT_KINDS = [
  "visit",
  "initial_exam",
  "treatment_plan",
  "clinical_media",
  "outcome_entry",
];

export const TIMELINE_EVENT_KIND_LABEL = {
  visit: "Encounters",
  initial_exam: "Exams",
  treatment_plan: "Plans",
  clinical_media: "Imaging",
  outcome_entry: "Outcomes",
};

export const TIMELINE_SOURCES = [
  "appointment",
  "encounter",
  "note",
  "initial_exam",
  "reexam",
  "outcome",
  "media",
];

export const TIMELINE_DATE_WINDOWS = [
  "last_7d",
  "last_30d",
  "last_90d",
  "last_180d",
  "last_365d",
  "all",
];

export const TIMELINE_DATE_WINDOW_LABEL = {
  last_7d: "Last 7 days",
  last_30d: "Last 30 days",
  last_90d: "Last 90 days",
  last_180d: "Last 6 months",
  last_365d: "Last 12 months",
  all: "All time",
};

// ---------------------------------------------------------------------
// Preset-id helper (matches backend regex `^p_[a-z0-9]{8,32}$`).
// ---------------------------------------------------------------------
export function newPresetId() {
  const rand = Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
  return "p_" + rand.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 24);
}

// ---------------------------------------------------------------------
// Sanitizer — drops everything not in the allow-list and refuses to let
// patient-scoped keys (episode_ids, q) survive into a durable preset.
// Returns { filters, dropped: [{ key, value }] } so the caller can
// surface a warning when a preset silently lost fidelity.
// ---------------------------------------------------------------------
const PRESET_ALLOWED_KEYS = new Set([
  "event_kinds",
  "sources",
  "provider_ids",
  "date_window",
]);

const PROVIDER_ID_RE = /^[a-z0-9-]{8,64}$/i;

export function sanitizePresetFilters(input) {
  const dropped = [];
  const out = {
    event_kinds: [],
    sources: [],
    provider_ids: [],
    date_window: null,
  };
  if (!input || typeof input !== "object") return { filters: out, dropped };

  for (const key of Object.keys(input)) {
    if (!PRESET_ALLOWED_KEYS.has(key)) {
      dropped.push({ key, reason: "not_allowed_in_preset" });
    }
  }

  if (Array.isArray(input.event_kinds)) {
    for (const v of input.event_kinds) {
      if (TIMELINE_EVENT_KINDS.includes(v)) out.event_kinds.push(v);
      else dropped.push({ key: "event_kinds", value: v, reason: "unknown_slug" });
    }
  }
  if (Array.isArray(input.sources)) {
    for (const v of input.sources) {
      if (TIMELINE_SOURCES.includes(v)) out.sources.push(v);
      else dropped.push({ key: "sources", value: v, reason: "unknown_slug" });
    }
  }
  if (Array.isArray(input.provider_ids)) {
    for (const v of input.provider_ids) {
      if (typeof v === "string" && PROVIDER_ID_RE.test(v)) out.provider_ids.push(v);
      else dropped.push({ key: "provider_ids", value: v, reason: "bad_id" });
    }
  }
  if (input.date_window && TIMELINE_DATE_WINDOWS.includes(input.date_window)) {
    out.date_window = input.date_window;
  } else if (input.date_window) {
    dropped.push({ key: "date_window", value: input.date_window, reason: "unknown_slug" });
  }

  // Dedupe.
  out.event_kinds = Array.from(new Set(out.event_kinds));
  out.sources = Array.from(new Set(out.sources));
  out.provider_ids = Array.from(new Set(out.provider_ids));

  return { filters: out, dropped };
}

// ---------------------------------------------------------------------
// Stale-preset detection — given a preset and the current filter_meta
// echoed by the server, decide whether the preset references dead
// providers so the UI can surface "1 provider in this preset is no
// longer available".
// ---------------------------------------------------------------------
export function detectStaleness(preset, filterMeta) {
  if (!preset || !filterMeta) return { stale: false, reasons: [] };
  const reasons = [];
  const ignoredProviders = new Set(filterMeta.ignored_provider_ids || []);
  const presetProviders = preset.filters?.provider_ids || [];
  const staleProviders = presetProviders.filter((p) => ignoredProviders.has(p));
  if (staleProviders.length) {
    reasons.push({
      key: "provider_ids",
      count: staleProviders.length,
      values: staleProviders,
    });
  }
  const ignoredSlugs = new Set(filterMeta.ignored_slugs || []);
  for (const slug of ignoredSlugs) {
    // Only flag if it originated from this preset's serialised filters.
    if (
      (preset.filters?.event_kinds || []).includes(slug) ||
      (preset.filters?.sources || []).includes(slug) ||
      preset.filters?.date_window === slug
    ) {
      reasons.push({ key: "vocab", value: slug });
    }
  }
  return { stale: reasons.length > 0, reasons };
}

// ---------------------------------------------------------------------
// Transient filter shape — patient-scoped, held in useClinicalReturnState.
// Includes the fields disallowed in presets so we can never accidentally
// serialise them.
// ---------------------------------------------------------------------
export function emptyTransientFilters() {
  return {
    event_kinds: [],
    sources: [],
    provider_ids: [],
    date_window: null,
    // Patient-scoped — NEVER copied into a preset.
    episode_ids: [],
    q: "",
    // Reference to the currently-active preset (id only, opaque).
    active_preset_id: null,
  };
}

export function transientToQueryParams(f) {
  const p = {};
  if (f.event_kinds?.length) p.event_kinds = f.event_kinds.join(",");
  if (f.sources?.length) p.sources = f.sources.join(",");
  if (f.provider_ids?.length) p.provider_ids = f.provider_ids.join(",");
  if (f.episode_ids?.length) p.episode_ids = f.episode_ids.join(",");
  if (f.date_window) p.date_window = f.date_window;
  if (f.q?.trim()) p.q = f.q.trim().slice(0, 80);
  return p;
}

export function anyFilterActive(f) {
  if (!f) return false;
  return Boolean(
    f.event_kinds?.length ||
      f.sources?.length ||
      f.provider_ids?.length ||
      f.episode_ids?.length ||
      f.date_window ||
      f.q?.trim(),
  );
}
