/**
 * dataQualityEngine — Phase 3 Slice 4.
 *
 * Deterministic, patient-chart-scoped data-quality rules. Every rule
 * derives from structured data already loaded into the chart and
 * emits at most one row per kind. Focused on **remediation** — each
 * row carries a `resolution` target section so the reviewer can jump
 * straight to fixing the issue.
 *
 * Constraints locked in per Slice 4 brief:
 *   - Patient-chart scoped only. NO cross-patient aggregation. NO
 *     tenant counters. An operational dashboard is out of scope and
 *     must live behind a separate versioned aggregate contract.
 *   - Deterministic severity ladder: `info` → `warning` → `error`.
 *     Severities are workflow-oriented, not clinical.
 *   - Non-mutating — this engine only *reads* chart data.
 *   - Permission-aware: rules that require write scope silence
 *     themselves for read-only viewers.
 *   - Stable priority order (index of `RULE_IDS`).
 *   - Explanations are one-sentence, non-clinical.
 */

export const RULE_IDS = [
  "missing-primary-diagnosis",
  "unsigned-note-older-than-7d",
  "missing-note-on-encounter",
  "encounter-missing-provider",
  "imaging-missing-classification",
  "episode-without-encounters",
  "active-plan-without-configured-outcomes",
  "duplicate-outcome-day",
];

// Severity ladder — order matters for chart sorting when rule
// priorities tie.
export const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };

function _daysBetween(iso, now) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  return Math.floor((now - t) / (1000 * 60 * 60 * 24));
}

const RULES = {
  "missing-primary-diagnosis": (input) => {
    if (!input.canWrite) return null;
    const hasActiveWork =
      (input.summary?.notes?.open || 0) > 0 ||
      (input.encounterGroups || []).some(
        (g) => g?.status?.workflow === "in_progress",
      );
    if (!hasActiveWork) return null;
    if (input.primaryDx) return null;
    return {
      id: "missing-primary-diagnosis",
      severity: "warning",
      label: "Primary diagnosis not linked",
      why: "Active documentation is in progress but no primary diagnosis is linked to the chart.",
      count: 1,
      resolution: { section: "diagnoses" },
    };
  },
  "unsigned-note-older-than-7d": (input) => {
    if (!input.canWrite) return null;
    const now = input.now ?? Date.now();
    const stale = (input.encounterGroups || []).filter((g) => {
      if (g?.status?.documentation !== "draft") return false;
      const d = _daysBetween(g?.visit_at, now);
      return d != null && d > 7;
    }).length;
    if (stale <= 0) return null;
    return {
      id: "unsigned-note-older-than-7d",
      severity: "warning",
      label: "Unsigned notes older than 7 days",
      why: `${stale} draft note${stale === 1 ? "" : "s"} ${stale === 1 ? "has" : "have"} been open for more than a week.`,
      count: stale,
      resolution: { section: "encounters" },
    };
  },
  "missing-note-on-encounter": (input) => {
    if (!input.canWrite) return null;
    const groups = input.encounterGroups || [];
    const missing = groups.filter((g) => g?.status?.documentation === "missing").length;
    if (missing <= 0) return null;
    return {
      id: "missing-note-on-encounter",
      severity: "info",
      label: "Visits without an attached note",
      why: `${missing} completed visit${missing === 1 ? " has" : "s have"} no clinical note on file.`,
      count: missing,
      resolution: { section: "encounters" },
    };
  },
  "encounter-missing-provider": (input) => {
    const groups = input.encounterGroups || [];
    const missing = groups.filter(
      (g) => !g?.provider_id && !g?.provider_name,
    ).length;
    if (missing <= 0) return null;
    return {
      id: "encounter-missing-provider",
      severity: "info",
      label: "Encounters missing a provider",
      why: `${missing} visit${missing === 1 ? " lacks" : "s lack"} a linked provider.`,
      count: missing,
      resolution: { section: "encounters" },
    };
  },
  "imaging-missing-classification": (input) => {
    const imagings = input.imaging || [];
    const missing = imagings.filter(
      (m) => !m?.imaging_modality && !m?.kind,
    ).length;
    if (missing <= 0) return null;
    return {
      id: "imaging-missing-classification",
      severity: "info",
      label: "Imaging without modality classification",
      why: `${missing} imaging record${missing === 1 ? " has" : "s have"} no modality classification.`,
      count: missing,
      resolution: { section: "imaging" },
    };
  },
  "episode-without-encounters": (input) => {
    const eps = input.episodes || [];
    const orphaned = eps.filter(
      (ep) =>
        ep &&
        (ep.status || "open") === "open" &&
        !(input.encounterGroups || []).some((g) => g?.episode_id === ep.id),
    ).length;
    if (orphaned <= 0) return null;
    return {
      id: "episode-without-encounters",
      severity: "info",
      label: "Open episode without any encounters",
      why: `${orphaned} open episode${orphaned === 1 ? " has" : "s have"} no linked visits yet.`,
      count: orphaned,
      resolution: { section: "history" },
    };
  },
  "active-plan-without-configured-outcomes": (input) => {
    if (!input.canWrite) return null;
    const plan = input.activePlan;
    if (!plan) return null;
    const configured = (plan.configured_outcome_measures || []).length;
    if (configured > 0) return null;
    return {
      id: "active-plan-without-configured-outcomes",
      severity: "info",
      label: "Active plan has no configured outcome measures",
      why: "The active treatment plan does not list any configured outcome instruments to track.",
      count: 1,
      resolution: { section: "care-plan" },
    };
  },
  "duplicate-outcome-day": (input) => {
    const entries = input.outcomeEntries || [];
    const byDay = new Map();
    for (const e of entries) {
      const key = `${e.measure_type}::${(e.captured_at || "").slice(0, 10)}`;
      byDay.set(key, (byDay.get(key) || 0) + 1);
    }
    let dupDays = 0;
    for (const c of byDay.values()) if (c > 1) dupDays += 1;
    if (dupDays <= 0) return null;
    return {
      id: "duplicate-outcome-day",
      severity: "info",
      label: "Multiple outcome entries recorded on the same day",
      why: `${dupDays} instrument/day pair${dupDays === 1 ? " has" : "s have"} more than one entry — the trend view shows the latest revision.`,
      count: dupDays,
      resolution: { section: "outcomes" },
    };
  },
};

/**
 * Derive the ordered list of patient-scoped data-quality issues.
 *
 * @param {object} input
 * @param {boolean} input.canWrite
 * @param {object|null} input.summary
 * @param {object|null} input.activePlan
 * @param {object|null} input.primaryDx
 * @param {Array}       input.encounterGroups
 * @param {Array}       input.imaging
 * @param {Array}       input.episodes
 * @param {Array}       input.outcomeEntries
 * @param {number}      [input.now]
 * @returns {Array<{id,severity,label,why,count,resolution}>}
 */
export function deriveDataQualityIssues(input) {
  const out = [];
  for (const id of RULE_IDS) {
    const rule = RULES[id];
    if (!rule) continue;
    const row = rule(input);
    if (row) out.push(row);
  }
  // Sort by severity primarily, priority (position in RULE_IDS)
  // secondarily. Preserves deterministic output.
  out.sort((a, b) => {
    const sd = (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99);
    if (sd !== 0) return sd;
    return RULE_IDS.indexOf(a.id) - RULE_IDS.indexOf(b.id);
  });
  return out;
}

export const __rulesForTest = RULES;
