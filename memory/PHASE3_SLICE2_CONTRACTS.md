# Phase 3 Slice 2 — Frozen Contracts

Signed off 2026-02-15. Any change below requires a fresh design pass +
Slice-boundary review + coordinated backend/frontend/telemetry update.

## 1. Filter surface (transient, per-chart)

Rendered by `TimelineFilterBar.jsx`. Held in `useClinicalReturnState`
under section `timeline`. Never touches `localStorage`.

| Dimension | Vocabulary | Persistable in preset? |
|---|---|:-:|
| `event_kinds` | `visit`, `initial_exam`, `treatment_plan`, `clinical_media`, `outcome_entry` | ✅ |
| `sources` | `appointment`, `encounter`, `note`, `initial_exam`, `reexam`, `outcome`, `media` | ✅ |
| `provider_ids` | Tenant-scoped provider UUIDs | ✅ |
| `date_window` | `last_7d`, `last_30d`, `last_90d`, `last_180d`, `last_365d`, `all` | ✅ |
| `date_from`, `date_to` | Absolute ISO dates | ❌ (transient only) |
| `episode_ids` | Patient-owned episode UUIDs | ❌ (patient-scoped, never durable) |
| `q` | Free-text ≤ 80 chars | ❌ (never persisted) |

## 2. Durable preset shape (`/me/preferences.clinical_ui_defaults`)

```jsonc
{
  "clinical_ui_defaults": {
    "default_section": "encounters",           // ClinicalSectionSlug | null
    "timeline_presets": [
      {
        "id": "p_<8..32 lower-alnum>",         // Pydantic-validated pattern
        "name": "<1..40 chars>",               // user label; not free-form filter
        "filters": {
          "event_kinds": ["visit", "outcome_entry"],
          "sources": ["encounter", "note"],
          "provider_ids": ["<uuid>", ...],
          "date_window": "last_90d"
        }
      }
    ],
    "default_timeline_preset_id": "p_..."      // must reference an existing preset
  }
}
```

**Server-side guarantees** (`identity/models.py`):

- `extra="forbid"` at every nesting level. Any field not listed above
  returns **422 `extra_forbidden`**.
- Explicit tests reject `patient_id`, `encounter_id`, `icd10_codes`,
  `q`, `date_of_service`, `date_from`, `date_to`, `episode_ids`.
- `default_timeline_preset_id` MUST reference a preset in the same
  update. Otherwise 422.
- Preset ids and names MUST be unique within a user's collection.
- Preset id pattern: `^p_[a-z0-9]{8,32}$`.

## 3. Server response — `GET /api/patients/{id}/clinical/timeline/grouped`

Backwards-compatible: unfiltered calls return `schema_version: "1.0"`
identical to the pre-Slice-2 shape. Any filter attempt bumps to
`schema_version: "1.1"` and adds:

```jsonc
{
  "schema_version": "1.1",
  "events": [ ... ],
  "filter_meta": {
    "applied": {
      "event_kinds": ["visit"],
      "sources": ["encounter"],
      "provider_ids": ["<uuid>"],
      "episode_ids": ["<uuid>"],
      "date_window": "last_90d",
      "date_from": "2025-11-17",
      "date_to": null,
      "q_present": false
    },
    "ignored_slugs": ["pizza"],               // stale-preset detection
    "ignored_provider_ids": ["dead-uuid"],    // stale-preset detection
    "ignored_episode_ids": ["stolen-uuid"],   // cross-patient guard
    "total_before_filter": 12,
    "total_after_filter": 4
  }
}
```

Rules:

- Unknown slugs are **dropped, not 400**. The UI uses `ignored_*` to
  surface stale-preset warnings and prompt for repair.
- `q` is length-bounded server-side (**≤ 80 chars, else 422**) and
  never persists.
- Cross-patient episode ids are silently dropped (they show up in
  `ignored_episode_ids`).
- Permission-aware: providers the caller cannot see are treated as
  stale (echoed in `ignored_provider_ids`) rather than 403'd.

## 4. Transient-scope isolation

- `useClinicalReturnState({ section: "timeline" })` holds:
  `filters` (all dimensions, incl. `episode_ids` and `q`) and
  `expanded` (row ids). Session-scoped, opaque route token,
  30-min TTL, cleared on logout / permission change.
- `SavedPresetsMenu` runs every save through
  `sanitizePresetFilters()` — the last line of defence that strips
  patient-scoped fields (`episode_ids`, `q`, `date_from`, `date_to`)
  and unknown slugs before the payload leaves the browser.

## 5. Performance

- No external virtualization library added in Slice 2.
- `INITIAL_RENDER_CAP = 100`; user clicks **Load more** to page in
  additional rows in `INITIAL_RENDER_CAP` chunks.
- `VIRTUALIZE_THRESHOLD = 200`; timelines beyond that render a "perf:
  long timeline" hint next to the load-more button so a future slice
  can hook actual windowing measurements.
- Slow-fetch measurement (`> 800 ms`) is `console.info`-logged with
  event count so ops can decide whether Slice 6 should introduce a
  real virtualization library.

## 6. Feature-flag inheritance

Slice 2 lives inside `clinicalRedesignPhase3` (child of
`clinicalRedesign`). No new flag added — Slice 2 and Slice 1 roll back
as one unit. See `PHASE3_SLICE1_FLAG_MATRIX.md`.
