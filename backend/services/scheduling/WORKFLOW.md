# Appointment Workflow Backbone — Implementation Notes (Phase 1)

This document captures the backend foundation for the clinic operational
workflow. Phase 1 is backend-only; room management, flow board, intake, and
checkout UIs come in later phases.

## Lifecycle model

Two independent concepts travel on every appointment:

| Concept            | Field                    | Values                                                                                                                 |
|--------------------|--------------------------|------------------------------------------------------------------------------------------------------------------------|
| Lifecycle status   | `status`                 | `scheduled`, `confirmed`, `checked_in`, `ready_for_provider`, `in_progress`, `ready_for_checkout`, `completed`, `checked_out`, `no_show`, `canceled` |
| Patient location   | `current_location_type`  | `not_arrived`, `waiting_room`, `roomed`, `checkout`, `departed`                                                        |

Legacy `cancelled` spelling is still accepted for rows that predate this change; all new transitions emit `canceled`.

## Workflow metadata (stamped on transition)

Every transition records WHO performed it and WHEN:

- `checked_in_at` / `checked_in_by_user_id`
- `ready_for_provider_at` / `ready_for_provider_by_user_id`
- `visit_started_at` / `visit_started_by_user_id`
- `ready_for_checkout_at` / `ready_for_checkout_by_user_id`
- `completed_at` / `completed_by_user_id`
- `checked_out_at` / `checked_out_by_user_id`
- `no_show_at` / `no_show_by_user_id`
- `location_updated_at` / `location_updated_by_user_id`

## Endpoints (`/api/appointments/{id}/...`)

| Endpoint               | Status change            | Default `location` side-effect | Override path (requires `override=true`) |
|------------------------|--------------------------|---------------------------------|-------------------------------------------|
| `/check-in`            | `scheduled\|confirmed → checked_in` | `waiting_room`       | —                                         |
| `/undo-check-in`       | `checked_in\|ready_for_provider → scheduled` | `not_arrived` | `in_progress → scheduled` (with `override`) |
| `/no-show`             | `scheduled\|confirmed\|checked_in\|ready_for_provider → no_show` | — | — |
| `/ready-for-provider`  | `checked_in → ready_for_provider` | `roomed`            | —                                         |
| `/start-visit`         | `ready_for_provider → in_progress` | `roomed`           | `checked_in → in_progress` (skip "ready")  |
| `/ready-for-checkout`  | `in_progress → ready_for_checkout` | `checkout`         | —                                         |
| `/complete`            | `in_progress\|ready_for_checkout → completed` | —         | —                                         |
| `/checkout`            | `completed → checked_out` | `departed`                     | `ready_for_checkout → checked_out`        |
| `/depart`              | — (lifecycle unchanged)   | `departed`                     | any earlier status (with `override`)      |
| `/location`            | — (lifecycle unchanged)   | explicit `location` payload     | N/A                                       |

All endpoints accept `{ "reason": str, "override": bool, "location": <override default> }`.

## Validation rules (server-side, default-deny)

1. **Cannot check in a canceled appointment.** Canceled appointments reject every transition except `depart` (which is allowed because departure is the terminal physical motion).
2. **Cannot start visit before check-in** unless `override=true` is passed (audited separately).
3. **Cannot complete** before the visit has started (only from `in_progress` / `ready_for_checkout`).
4. **Cannot check out** before the provider phase is complete. `completed → checked_out` is the norm; `ready_for_checkout → checked_out` requires `override=true`.
5. **Cannot mark no-show** after the visit has started (`in_progress`, `ready_for_checkout`, `completed`, `checked_out` are all blocked).
6. **Undo check-in** is always explicit. It is allowed from `checked_in` / `ready_for_provider`; from `in_progress` it requires `override=true`.
7. Every reversion, override, and forced transition is audited as a distinct event with `from_status`, `to_status`, `override`, and the caller-supplied `reason`.

## Permissions

All workflow endpoints depend on `require_permission("appointment", "update", ...)`. This keeps the policy centralised: roles that already had the `appointment.update` grant (super_admin, org_owner, clinic_manager, front_desk, provider, clinical_staff) inherit workflow capability automatically.

- **Patient portal** does not have `appointment.update`, so portal users are rejected at the authz layer (403).
- **Privileged transitions** (override, undo after visit start) still go through the same guard; overrides do not bypass permission checks — they only loosen the specific state-machine rule.

## Audit

Every transition emits a row via `audit_success(...)` with:

- `action` — `appointment.checked_in`, `appointment.visit_started`, …
- `entity_type` — `"appointment"`
- `entity_id` — appointment id
- `metadata.from_status` / `metadata.to_status`
- `metadata.override` (bool)
- `metadata.reason` (string, caller-supplied — no PHI)
- `metadata.location_after`

`appointment.location_changed` is emitted from the explicit `/location` endpoint and carries `from_location` / `to_location`.

## Event bus

Each transition publishes `appointment.<transition_name>` with the updated appointment and the previous status. Downstream subscribers (billing, notifications, analytics) can hook these without touching the router.

## Multi-tenancy + scope

- Every workflow endpoint resolves the appointment via `scoped_filter(..., location_scoped=True)` so a user outside the appointment's tenant or assigned locations receives a 404.
- Tenant id is included in audit metadata for every transition.

## Backward compatibility

- Existing `PATCH /appointments/{id}` still accepts an arbitrary `status` change (legacy UI). Workflow transitions are additive; nothing using the old endpoint has changed.
- `POST /appointments/{id}/cancel` continues to emit the legacy `cancelled` spelling and the legacy event. No migration is required.

## File map

- `/app/backend/services/scheduling/models.py` — Pydantic models + lifecycle enums + workflow request shapes.
- `/app/backend/services/scheduling/workflow.py` — single-source-of-truth transition engine, validation, audit.
- `/app/backend/services/scheduling/router.py` — thin HTTP endpoints.
- `/app/backend/tests/test_appointment_workflow.py` — 14 integration tests covering happy path, validation, overrides, reversions, and permissions.
