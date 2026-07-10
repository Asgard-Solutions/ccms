# UI telemetry schema

Fire-and-forget signals for the CCMS frontend. Persisted in MongoDB
collections `ui_telemetry_events` and `ui_telemetry_actions` and scoped
by `tenant_id` + `actor_id`.

## Non-negotiable rules

1. **No PHI.** No patient IDs, encounter IDs, appointment IDs,
   diagnoses, dates, counts, free-form strings, URLs, or any value
   derived from PHI.
2. **Allow-listed fields only.** Every payload has
   `model_config = {"extra": "forbid"}`. Unknown keys → 422.
3. **Enum-restricted values.** No free-form vocabulary. Adding a new
   slug requires a schema change + code review.
4. **Attempted intent only.** We record when a user selects an
   action, not when the downstream workflow completes. Success events
   are added *only* when an existing workflow can confirm completion
   without exposing record data.
5. **Best-effort.** Telemetry outages never block or degrade the
   clinical UI. Clients dedupe rapid duplicate emits (500 ms).

## `POST /api/telemetry/ui-event`

Persisted in `ui_telemetry_events`.

| Field | Type | Notes |
|---|---|---|
| `event` | enum, required | `clinical.layout.activated` · `clinical.nav.jump` · `clinical.section.load_failed` |
| `layout` | enum, optional | `v1` · `v2` |
| `section` | enum, optional | `summary` · `history` · `diagnoses` · `encounters` · `care-plan` · `timeline` · `imaging` · `outcomes` |
| `error_code` | string, optional | Max 64 chars. HTTP status or `network_error` / `load_exception`. |

Auto-attached server-side: `tenant_id`, `actor_id`, `actor_role`, `ts`,
`ua` (first 200 chars). Response: **204**.

## `POST /api/telemetry/ui-action`

Persisted in `ui_telemetry_actions`.

The one currently supported event:

```json
{
  "event_name":     "clinical_care_status_action_selected",
  "section_slug":   "current-care-status",
  "action_slug":    "schedule-reexam",
  "source_surface": "patient-clinical",
  "layout_version": "v2"
}
```

| Field | Enum values |
|---|---|
| `event_name` | `clinical_care_status_action_selected` |
| `section_slug` | `current-care-status` |
| `source_surface` | `patient-clinical` |
| `layout_version` | `v1` · `v2` |
| `action_slug` | `open-encounter` · `add-note` · `record-outcome` · `schedule-visit` · `schedule-reexam` · `review-billing-issues` · `edit-missing-information` |

All five fields are **required**. Any extra field returns
**422 `extra_forbidden`**. Any unknown enum value returns
**422 `literal_error`**.

Auto-attached server-side: `tenant_id`, `actor_id`, `actor_role`, `ts`,
`ua` (first 200 chars). Response: **204**.

## Expanding the vocabulary

1. Update the corresponding `Literal[...]` in
   `services/telemetry/router.py`.
2. Update the client-side allow-list in
   `frontend/src/utils/telemetry.js` (`CARE_STATUS_ACTION_SLUGS`) and,
   if you add a new CTA site, the local mirror in
   `pages/clinical/CurrentCareStatusPanel.jsx`.
3. Update this file.
4. Update / add tests in
   `backend/tests/test_telemetry_ui_action.py` — the "unknown slug
   rejected" case is the single source of truth for the current
   vocabulary size.

## Explicitly NOT tracked

* Patient identifiers of any kind.
* Encounter, claim, invoice, appointment IDs.
* Absolute dates / counts / DOBs.
* Diagnosis codes or labels.
* Free-form input text.
* Navigation URLs beyond the section slug.
* Clinical outcome success — record-level success is inferred from the
  existing audit trail in `audit_logs`, not from UX telemetry.
