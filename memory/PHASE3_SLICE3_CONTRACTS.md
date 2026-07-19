# Phase 3 Slice 3 — Frozen Contracts

Signed off 2026-02-15. Read-only outcome snapshot + trend + optional
suggestions. Every derived value is neutrally labeled — the surface
never claims clinical improvement, deterioration, or significance.

## 1. Feature flag

- Independent nested flag: `clinicalRedesignPhase3Slice3` (child of
  `clinicalRedesignPhase3`, grandchild of `clinicalRedesign`).
- Default: `on`. Legacy `OutcomesCard` remains mounted below the new
  section so the capture workflow keeps working even when the child
  flag is off — Slice 3 is intentionally read-only.

## 2. Supported instruments (allow-list)

| Key | Label | Max | Unit |
|---|---|---|---|
| `ndi` | Neck Disability Index | 100 | – |
| `oswestry` | Oswestry Disability Index | 100 | – |
| `pain_vas` | Pain (VAS) | 10 | /10 |
| `pain_scale` | Pain scale | 10 | /10 |
| `functional_index` | Functional index | – | – |
| `bournemouth_neck` | Bournemouth (neck) | 70 | – |

Adding a new instrument requires updates to:

1. Frontend `outcomeSeriesHelpers.SUPPORTED_INSTRUMENTS`.
2. Backend `OutcomeInstrumentKey` literal.
3. `SCHEMA.md` outcome-suggestion table.
4. `test_outcome_suggestion_telemetry.py` allow-list.

## 3. Derivation rules (deterministic)

- **Baseline** = earliest usable `captured_at`.
- **Latest** = last usable `captured_at`.
- **Previous** = the point immediately before latest (only when `usable_count >= 2`).
- **Change from baseline** = `latest.score - baseline.score`, labeled explicitly, no qualitative language.
- **Change from previous** = `latest.score - previous.score`.
- **Insufficient baseline** (`usable_count < 2`) → both delta values `null`; UI renders a neutral "Not enough entries" pill.
- **Duplicate captured_at day** → winner picked by `_pickWinner` (latest `updated_at` → latest `created_at` → lexicographic entry id). Losers kept in `superseded` for the table view.
- **Amended** entry = `updated_at !== created_at`. Rendered as a diamond marker + "Amended" pill.
- **Partial** entry (`score` null / NaN) → excluded from series, counted in `partial_count`, surfaced as a warning-toned footer.
- **Long histories** → windowed to last 24 months at render time (chart only; the table view still shows the full history).

## 4. Suggestions (deterministic, optional, dismissible)

- Fires only when:
  - `canWrite === true`, AND
  - `activePlan.configured_outcome_measures` contains the instrument, AND
  - the instrument's latest usable entry is > 30 days old OR no entry on file.
- Dismissal is session-scoped (`useClinicalReturnState.section === "outcomes"`).
- Reasons: `no_record_on_file` · `stale_record`.
- **Never** auto-starts, auto-populates, or auto-submits a measure.
  The "Record" action just scrolls to the legacy OutcomesCard where
  the clinician fills in every field manually.

## 5. Telemetry

Third shape on `POST /api/telemetry/ui-action`:

```json
{
  "event_name":     "clinical_outcome_suggestion_interaction",
  "section_slug":   "outcomes",
  "source_surface": "patient-clinical",
  "layout_version": "v2",
  "instrument_key": "ndi",
  "interaction":    "opened" | "dismissed"
}
```

Guarantees: strict `extra="forbid"`; cross-field mixes with
`action_id` / `action_slug` / any PHI-shaped extra (`patient_id`,
`captured_at`, `score`, `note`, `linked_reexam_id`) → **422**.

## 6. Accessibility

- Trend chart uses **shape encoding** (circle = regular, diamond =
  amended) in addition to color so the chart works without color.
- Every chart has a visible `Show as table` toggle backed by
  `OutcomeTrendTable` — full data table equivalent, sr-only caption,
  proper `<caption>` + `<thead>` + `<tfoot>`.
- Milestone markers render both as SVG lines AND as `<tfoot>` rows.
- Delta values use unicode `−` (U+2212) so screen readers announce
  "minus", not "hyphen".

## 7. States (all `data-testid`-tagged)

| State | Test id |
|---|---|
| Loading | `outcomes-section-loading` |
| Empty (no entries) | `outcomes-section-empty` |
| Permission-denied | `outcomes-section-permission-denied` |
| Server error | `outcomes-section-error` |
| Empty chart layout | `outcome-<key>-chart-empty` |
| Superseded note | `outcome-<key>-superseded-note` |
| Amended pill | `outcome-<key>-amended-badge` |
| Insufficient baseline | `outcome-<key>-insufficient-baseline` |

## 8. Source-record links

- Every snapshot card exposes an **Open source entry** button that
  routes to the entry (delegated via `onRecordOutcome({ view_entry_id })`).
- Superseded entries are surfaced in the table view with their entry
  ids intact — nothing is silently dropped.
- Records themselves remain immutable — Slice 3 does not add any
  mutation surface. `PATCH /clinical/outcomes/{oid}` continues to
  reject reexam-sourced entries (409).
