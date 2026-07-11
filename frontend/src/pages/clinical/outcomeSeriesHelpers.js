/**
 * outcomeSeriesHelpers — Phase 3 Slice 3.
 *
 * Pure derivation helpers for the Outcome Snapshot / Trend / Table
 * surface. Everything here is deterministic, structured-data-only,
 * and explicitly **non-clinical** — we never claim "improvement",
 * "deterioration", "clinically significant", or anything of the sort.
 * Callers render the raw numbers with clear, neutral labels.
 *
 * Guardrails locked in per Slice 3 brief:
 *  - No inference of improvement/deterioration.
 *  - Calculated values are labeled ("Change from baseline: ±N").
 *  - Duplicate captured_at handled deterministically (winner: latest
 *    `updated_at`; ties broken by lexicographic entry id) with both
 *    winner and superseded entries kept in the table view.
 *  - Missing baseline (≤ 1 usable point) → snapshot exposes
 *    `insufficient_baseline: true`; no delta computed.
 *  - Amended entries (`updated_at !== created_at`) flagged via
 *    `is_amended: true` — pill in UI, no data mutation.
 *  - Partial records (`score == null | NaN`) filtered from series but
 *    counted in `partial_count` so the UI can surface a "N incomplete"
 *    footer.
 *  - Long histories are safely truncated by callers using
 *    `windowSeriesToLastMonths(series, months)`.
 */

// Every configured instrument the app renders as a Slice 3 suggestion
// MUST appear here. Adding a new one requires:
//   1. Backend `OUTCOME_MEASURE` Literal.
//   2. Frontend `SUPPORTED_INSTRUMENTS` below.
//   3. `NextActionsPanel`/telemetry allow-list (if applicable).
export const SUPPORTED_INSTRUMENTS = {
  ndi: {
    key: "ndi",
    label: "Neck Disability Index",
    short_label: "NDI",
    unit: "",
    max_score: 100,
    direction: null, // deliberately null — no clinical inference
  },
  oswestry: {
    key: "oswestry",
    label: "Oswestry Disability Index",
    short_label: "Oswestry",
    unit: "",
    max_score: 100,
    direction: null,
  },
  pain_vas: {
    key: "pain_vas",
    label: "Pain (VAS)",
    short_label: "VAS",
    unit: "/10",
    max_score: 10,
    direction: null,
  },
  pain_scale: {
    key: "pain_scale",
    label: "Pain scale",
    short_label: "Pain",
    unit: "/10",
    max_score: 10,
    direction: null,
  },
  functional_index: {
    key: "functional_index",
    label: "Functional index",
    short_label: "FI",
    unit: "",
    max_score: null,
    direction: null,
  },
  bournemouth_neck: {
    key: "bournemouth_neck",
    label: "Bournemouth (neck)",
    short_label: "Bournemouth-N",
    unit: "",
    max_score: 70,
    direction: null,
  },
};

// Only these instrument keys may be surfaced as a Slice 3 suggestion.
// Anything else is silently ignored — never rendered as a suggestion.
export const SUGGESTABLE_INSTRUMENT_KEYS = new Set(
  Object.keys(SUPPORTED_INSTRUMENTS),
);

// ------------------------------------------------------------------
// Series derivation
// ------------------------------------------------------------------
function _num(v) {
  const n = typeof v === "number" ? v : parseFloat(v);
  return Number.isFinite(n) ? n : null;
}

function _pickWinner(a, b) {
  // Tie-break: later `updated_at`, then later `created_at`, then
  // lexicographic entry id. Deterministic and stable.
  const uA = a.updated_at || a.created_at || "";
  const uB = b.updated_at || b.created_at || "";
  if (uA !== uB) return uA > uB ? a : b;
  const cA = a.created_at || "";
  const cB = b.created_at || "";
  if (cA !== cB) return cA > cB ? a : b;
  return String(a.id || "") > String(b.id || "") ? a : b;
}

/**
 * Group raw outcome entries by instrument key + label.
 * Rejects entries whose `measure_type` isn't allow-listed for
 * rendering (unknown instruments won't appear).
 */
export function groupByInstrument(entries) {
  const groups = new Map();
  for (const e of entries || []) {
    const kind = e.measure_type;
    if (!SUPPORTED_INSTRUMENTS[kind]) continue;
    const key = `${kind}::${e.label || kind}`;
    if (!groups.has(key)) {
      groups.set(key, {
        instrument_key: kind,
        label: e.label || SUPPORTED_INSTRUMENTS[kind].label,
        entries: [],
      });
    }
    groups.get(key).entries.push(e);
  }
  return Array.from(groups.values());
}

/**
 * Given a group's raw entries, produce the immutable series used by
 * chart + table + snapshot.
 *
 * Deterministic contract:
 *   - Duplicate `captured_at` (day precision) → winner picked via
 *     `_pickWinner`. Superseded entries stay in `superseded` for the
 *     table view but are NOT plotted.
 *   - `score` is coerced with `_num`. Entries whose score can't be
 *     parsed contribute to `partial_count` and are excluded.
 *   - Baseline = earliest-captured usable point. Latest = last usable.
 *   - `change_since_baseline`, `change_since_prev`: numeric deltas
 *     labeled with sign. No qualitative language.
 *   - `is_amended` is per-entry: `updated_at !== created_at`.
 */
export function deriveSeries(group) {
  const inst = SUPPORTED_INSTRUMENTS[group.instrument_key] || {};
  const raw = [...(group.entries || [])];
  raw.sort((a, b) => (a.captured_at || "").localeCompare(b.captured_at || ""));

  const partial = [];
  const usable = [];
  for (const e of raw) {
    const s = _num(e.score);
    if (s == null) {
      partial.push(e);
      continue;
    }
    usable.push({ ...e, _score: s });
  }

  // Dedupe by captured_at day-precision. Keep winners in `points`,
  // losers in `superseded`.
  const byDay = new Map(); // day → winner
  const superseded = [];
  for (const e of usable) {
    const day = (e.captured_at || "").slice(0, 10);
    if (!byDay.has(day)) {
      byDay.set(day, e);
    } else {
      const prev = byDay.get(day);
      const w = _pickWinner(prev, e);
      const loser = w === prev ? e : prev;
      superseded.push(loser);
      byDay.set(day, w);
    }
  }

  const points = Array.from(byDay.values()).sort(
    (a, b) => (a.captured_at || "").localeCompare(b.captured_at || ""),
  );

  const withAmended = points.map((e) => ({
    entry_id: e.id,
    captured_at: e.captured_at,
    score: e._score,
    unit: e.unit || inst.unit || null,
    max_score: e.max_score ?? inst.max_score ?? null,
    source: e.source,
    linked_reexam_id: e.linked_reexam_id || null,
    linked_treatment_plan_id: e.linked_treatment_plan_id || null,
    is_amended: Boolean(
      e.updated_at && e.created_at && e.updated_at !== e.created_at,
    ),
    note: e.note || null,
  }));

  const usable_count = withAmended.length;
  const baseline = usable_count >= 1 ? withAmended[0] : null;
  const latest = usable_count >= 1 ? withAmended[usable_count - 1] : null;
  const previous = usable_count >= 2 ? withAmended[usable_count - 2] : null;

  const insufficient_baseline = usable_count < 2;
  const change_since_baseline =
    baseline && latest && baseline !== latest
      ? latest.score - baseline.score
      : null;
  const change_since_prev =
    previous && latest ? latest.score - previous.score : null;

  return {
    instrument_key: group.instrument_key,
    instrument_label: inst.label || group.label,
    short_label: inst.short_label || group.label,
    unit: latest?.unit || inst.unit || null,
    max_score: latest?.max_score ?? inst.max_score ?? null,
    points: withAmended,
    superseded: superseded.map((e) => ({
      entry_id: e.id,
      captured_at: e.captured_at,
      score: e._score,
      superseded: true,
    })),
    partial_count: partial.length,
    usable_count,
    insufficient_baseline,
    baseline,
    latest,
    previous,
    change_since_baseline,
    change_since_prev,
  };
}

/**
 * Format a numeric delta with an explicit sign and a fixed number of
 * decimals. Returns "±0" for zero rather than a bare "0".
 */
export function formatDelta(value, { decimals = 0 } = {}) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value === 0) return "±0";
  const sign = value > 0 ? "+" : "−";
  return `${sign}${Math.abs(value).toFixed(decimals)}`;
}

/**
 * Truncate a series to the last `months` months by captured_at.
 * Preserves ordering. Returns a NEW array — the input is not mutated.
 */
export function windowSeriesToLastMonths(points, months = 24) {
  if (!points?.length || !Number.isFinite(months) || months <= 0) return points || [];
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - months);
  const cutoffIso = cutoff.toISOString();
  return points.filter((p) => (p.captured_at || "") >= cutoffIso);
}

// ------------------------------------------------------------------
// Milestone helpers (Slice 3 delivery step #4)
// ------------------------------------------------------------------
/**
 * Convert an active-plan payload into deterministic milestones the
 * trend chart can render as vertical markers. Milestones are strictly
 * structured — no clinical prose.
 */
export function buildMilestones({ activePlan } = {}) {
  const out = [];
  if (activePlan?.start_date) {
    out.push({
      kind: "plan_start",
      at: activePlan.start_date,
      label: "Plan start",
    });
  }
  if (activePlan?.re_exam_date) {
    out.push({
      kind: "reexam_due",
      at: activePlan.re_exam_date,
      label: "Re-exam due",
    });
  }
  if (activePlan?.discharged_at) {
    out.push({
      kind: "plan_discharged",
      at: activePlan.discharged_at,
      label: "Plan discharged",
    });
  }
  // Deterministic ordering by date.
  out.sort((a, b) => (a.at || "").localeCompare(b.at || ""));
  return out;
}

// ------------------------------------------------------------------
// Optional-suggestion engine (Slice 3 delivery step #5)
// ------------------------------------------------------------------
/**
 * Deterministic optional-suggestion list.
 *
 * Rules (all AND):
 *   - Instrument key MUST be in `SUPPORTED_INSTRUMENTS`.
 *   - Instrument MUST be in `configured_outcome_measures` on the
 *     active plan (product-configured, not auto-inferred).
 *   - Fires only if there is NO usable entry for that instrument in
 *     the last `staleAfterDays` (default 30).
 *   - Skipped when the caller lacks write permission.
 *   - Dismissible; dismissed ids come in via `dismissed`.
 *
 * Never triggers a workflow — the UI only offers a link to the
 * "Record outcome" dialog. No auto-start / auto-populate / auto-submit.
 */
export function deriveOutcomeSuggestions({
  activePlan,
  entries,
  canWrite,
  dismissed = new Set(),
  now = Date.now(),
  staleAfterDays = 30,
} = {}) {
  if (!canWrite) return [];
  const configured = activePlan?.configured_outcome_measures || [];
  if (!configured.length) return [];

  const byInstrumentLatest = new Map();
  for (const e of entries || []) {
    if (!e?.measure_type || !SUPPORTED_INSTRUMENTS[e.measure_type]) continue;
    if (_num(e.score) == null) continue;
    const cur = byInstrumentLatest.get(e.measure_type);
    if (!cur || (e.captured_at || "") > (cur.captured_at || "")) {
      byInstrumentLatest.set(e.measure_type, e);
    }
  }

  const cutoffMs = now - staleAfterDays * 24 * 60 * 60 * 1000;
  const suggestions = [];
  const seen = new Set();
  for (const key of configured) {
    if (!SUGGESTABLE_INSTRUMENT_KEYS.has(key)) continue;
    if (seen.has(key)) continue;
    if (dismissed.has(key)) continue;
    const latest = byInstrumentLatest.get(key);
    let reason;
    if (!latest) {
      reason = "no_record_on_file";
    } else {
      const ms = Date.parse(latest.captured_at || "");
      if (Number.isFinite(ms) && ms >= cutoffMs) continue;
      reason = "stale_record";
    }
    const inst = SUPPORTED_INSTRUMENTS[key];
    suggestions.push({
      instrument_key: key,
      label: inst.label,
      short_label: inst.short_label,
      reason,
      why:
        reason === "no_record_on_file"
          ? `${inst.short_label} is configured on the active plan and has no entry on file.`
          : `${inst.short_label} is configured on the active plan; the last entry is more than ${staleAfterDays} days old.`,
      dismissible: true,
    });
    seen.add(key);
  }
  return suggestions;
}
