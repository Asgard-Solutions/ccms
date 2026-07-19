/**
 * Phase 3 Slice 5A — Role-aware Clinical workspace modes.
 *
 * This module is a pure, deterministic registry. It never talks to
 * the network or reads durable preferences. Consumers pass the
 * current role + a candidate mode, and the helpers here return:
 *
 *   - the allowed set of modes for that role
 *   - the effective mode (falling back to `general` when unauthorised)
 *   - a section-priority ordering used to reorder Clinical navigation
 *   - a summary-module priority ordering used to seed Slice 5B defaults
 *
 * Restrictions:
 *   - Role-aware ordering only *emphasises* what a user can already see.
 *   - It never grants access, never hides an authorised section that
 *     is the target of an active remediation, and never exposes
 *     restricted data.
 *   - Section slugs match `NAV_ITEMS` in clinicalHelpers.js exactly.
 */

export const CLINICAL_WORKSPACE_MODES = [
  "general",
  "provider",
  "front_desk",
  "billing",
  "administrator",
];

export const MODE_LABEL = {
  general: "General",
  provider: "Provider",
  front_desk: "Front desk",
  billing: "Billing",
  administrator: "Administrator",
};

export const MODE_DESCRIPTION = {
  general: "Balanced Clinical page order.",
  provider: "Prioritise Next Actions, Active Episode, encounters, outcomes.",
  front_desk: "Prioritise scheduling, intake completion, re-exam.",
  billing: "Prioritise billing readiness, blocked encounters, diagnosis linkage.",
  administrator: "Prioritise data-quality, audit-sensitive actions, record state.",
};

// Which modes each role may switch into. `general` is always available.
// A user whose role isn't listed falls back to `general`.
const ROLE_ALLOWED_MODES = {
  admin:          ["general", "provider", "front_desk", "billing", "administrator"],
  platform_admin: ["general", "provider", "front_desk", "billing", "administrator"],
  super_admin:    ["general", "provider", "front_desk", "billing", "administrator"],
  doctor:         ["general", "provider"],
  staff:          ["general", "front_desk", "billing"],
  patient:        ["general"],
};

// Section ordering per mode. Sections omitted from a mode's list fall
// back to their natural order at the end.
const MODE_SECTION_ORDER = {
  general:      ["summary", "history", "diagnoses", "encounters", "care-plan", "timeline", "imaging", "outcomes"],
  provider:     ["summary", "encounters", "care-plan", "outcomes", "diagnoses", "timeline", "imaging", "history"],
  front_desk:   ["summary", "encounters", "care-plan", "history", "diagnoses", "timeline", "imaging", "outcomes"],
  billing:      ["summary", "encounters", "diagnoses", "care-plan", "timeline", "history", "imaging", "outcomes"],
  administrator:["summary", "diagnoses", "encounters", "care-plan", "history", "timeline", "imaging", "outcomes"],
};

// Default summary-module order per mode. Modules the user's role can't
// see are pruned at render time by SummaryRail — this is just the seed.
const MODE_SUMMARY_DEFAULTS = {
  general: [
    "active_episode", "primary_diagnosis", "current_treatment_plan",
    "next_appointment", "reexam_status", "documentation_tasks",
    "billing_readiness", "safety_summary", "latest_clinical_response",
    "outcomes_trend", "recent_imaging", "data_quality", "next_actions",
  ],
  provider: [
    "next_actions", "active_episode", "primary_diagnosis",
    "documentation_tasks", "reexam_status", "current_treatment_plan",
    "latest_clinical_response", "outcomes_trend", "safety_summary",
    "next_appointment", "recent_imaging", "billing_readiness",
    "data_quality",
  ],
  front_desk: [
    "next_appointment", "reexam_status", "active_episode",
    "current_treatment_plan", "documentation_tasks", "billing_readiness",
    "primary_diagnosis", "safety_summary", "recent_imaging",
    "latest_clinical_response", "outcomes_trend", "next_actions",
    "data_quality",
  ],
  billing: [
    "billing_readiness", "documentation_tasks", "primary_diagnosis",
    "active_episode", "current_treatment_plan", "next_actions",
    "next_appointment", "reexam_status", "recent_imaging",
    "safety_summary", "latest_clinical_response", "outcomes_trend",
    "data_quality",
  ],
  administrator: [
    "data_quality", "documentation_tasks", "billing_readiness",
    "active_episode", "primary_diagnosis", "current_treatment_plan",
    "next_actions", "reexam_status", "next_appointment",
    "safety_summary", "latest_clinical_response", "outcomes_trend",
    "recent_imaging",
  ],
};

export function allowedModesForRole(role) {
  return ROLE_ALLOWED_MODES[role] || ["general"];
}

export function effectiveMode({ role, requested }) {
  const allowed = allowedModesForRole(role);
  if (requested && allowed.includes(requested)) return requested;
  return "general";
}

export function sectionOrderForMode(mode) {
  return MODE_SECTION_ORDER[mode] || MODE_SECTION_ORDER.general;
}

export function summaryDefaultsForMode(mode) {
  return MODE_SUMMARY_DEFAULTS[mode] || MODE_SUMMARY_DEFAULTS.general;
}

/**
 * Merge a user's stored `summary_module_order` with the mode default
 * so newly-added modules always appear (at the end) and stale ones
 * (removed from the registry) are silently pruned.
 */
export function resolveSummaryOrder({ mode, stored }) {
  const defaults = summaryDefaultsForMode(mode);
  const allow = new Set(defaults);
  const out = [];
  const seen = new Set();
  if (Array.isArray(stored)) {
    for (const slug of stored) {
      if (allow.has(slug) && !seen.has(slug)) {
        out.push(slug);
        seen.add(slug);
      }
    }
  }
  for (const slug of defaults) {
    if (!seen.has(slug)) out.push(slug);
  }
  return out;
}

/**
 * Move a slug up (delta=-1) or down (delta=+1) within an order list.
 * Returns a fresh array; the original is never mutated.
 */
export function reorderSummary({ order, slug, delta }) {
  const idx = order.indexOf(slug);
  if (idx < 0) return order.slice();
  const next = order.slice();
  const target = Math.min(Math.max(idx + delta, 0), next.length - 1);
  if (target === idx) return next;
  const [item] = next.splice(idx, 1);
  next.splice(target, 0, item);
  return next;
}
