# Appointment Workflow — Operational Rules

Single source of truth for every appointment state transition, the
physical-location concept, and the operational semantics used across
the calendar, Flow Board, Provider Queue, and Checkout page.

## Status vs. location

Two independent concepts travel on every appointment:

| Concept           | Field                   | Values                                                                                                                                                   |
|-------------------|-------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| Lifecycle status  | `status`                | `scheduled`, `confirmed`, `checked_in`, `ready_for_provider`, `in_progress`, `ready_for_checkout`, `completed`, `checked_out`, `no_show`, `canceled`     |
| Patient location  | `current_location_type` | `not_arrived`, `waiting_room`, `roomed`, `checkout`, `departed`                                                                                          |

The legacy `cancelled` spelling is still accepted for rows that predate
the Phase-1 rename; all new transitions emit `canceled`.

## Per-transition stamps

Each transition writes both WHO (`*_by_user_id`) and WHEN (`*_at`)
and some transitions also clear stale forward stamps when reversing.

Forward stamps: `checked_in_at/_by`, `ready_for_provider_at/_by`,
`visit_started_at/_by`, `ready_for_checkout_at/_by`, `completed_at/_by`,
`checked_out_at/_by`, `no_show_at/_by`, `location_updated_at/_by`,
`checkout_started_at/_by`, `room_assigned_at/_by`.

Reversals that clear forward stamps:
 * `undo_check_in` — clears checked_in_*, ready_for_provider_*, visit_started_*
 * `undo_ready_for_provider` — clears ready_for_provider_*
 * `undo_ready_for_checkout` — clears ready_for_checkout_*

## Endpoints (`/api/appointments/{id}/…`)

### Arrival / workflow transitions
| Endpoint                    | Status change                                          | Default `location` side-effect | Override path                                         |
|-----------------------------|--------------------------------------------------------|--------------------------------|-------------------------------------------------------|
| `/check-in`                 | `scheduled|confirmed → checked_in`                     | `waiting_room`                 | —                                                     |
| `/undo-check-in`            | `checked_in|ready_for_provider → scheduled`            | `not_arrived`                  | `in_progress → scheduled` (with `override`)           |
| `/no-show`                  | `scheduled|confirmed|checked_in|ready_for_provider → no_show` | —                       | —                                                     |
| `/ready-for-provider`       | `checked_in → ready_for_provider`                      | `roomed`                       | blocked unless intake completed OR `override`         |
| `/undo-ready-for-provider`  | `ready_for_provider → checked_in`                      | —                              | —                                                     |
| `/start-visit`              | `ready_for_provider → in_progress`                     | `roomed`                       | `checked_in → in_progress` (with `override`)          |
| `/ready-for-checkout`       | `in_progress → ready_for_checkout`                     | *(location unchanged)*         | —                                                     |
| `/undo-ready-for-checkout`  | `ready_for_checkout → in_progress`                     | —                              | —                                                     |
| `/complete`                 | `in_progress|ready_for_checkout → completed`           | —                              | —                                                     |
| `/start-checkout`           | *(no status change)*                                   | `checkout`                     | only from `ready_for_checkout|completed`              |
| `/checkout`                 | `completed → checked_out` + `checkout_notes/summary`   | `departed`                     | `ready_for_checkout → checked_out` (with `override`)  |
| `/depart`                   | *(no status change)*                                   | `departed`                     | any earlier status (with `override`)                  |
| `/location`                 | *(no status change)*                                   | explicit payload               | N/A                                                   |

### Rooms
| Endpoint                   | Effect                                                                                                 |
|----------------------------|--------------------------------------------------------------------------------------------------------|
| `/room` (POST)             | Assign or change the room. 409 on single-occupancy conflict; `force=true` + `reason` audited override. |
| `/clear-room` (POST)       | Clear `current_room_id`; optional `return_to_waiting=true` also sets location to `waiting_room`.       |
| `/room-history` (GET)      | Returns chronological `appointment_room_history` rows (from/to room + location, actor, forced, reason).|

## Validation rules (default-deny)

1. **Canceled appointments** reject every transition except `depart`.
2. **No-show** blocked once the visit has started (in_progress or later).
3. **Intake gating**: `ready_for_provider` blocked unless the patient has a completed intake form. `override=true` + `reason` bypasses and writes `intake_gate_bypassed=True` in the audit metadata.
4. **Start visit** before check-in requires `override=true` (jumps `ready` step).
5. **Complete** requires `in_progress` or `ready_for_checkout`.
6. **Checkout** requires `completed`; `ready_for_checkout → checked_out` only with `override=true`.
7. **Room assignment** blocked when the target room already has a non-terminal occupant; `force=true` + non-blank `reason` overrides (audited `forced=True`, flagged visibly in the UI).
8. **Provider conflict check** on booking blocks overlapping appointments in any *active* status (`scheduled`, `confirmed`, `checked_in`, `ready_for_provider`, `in_progress`, `ready_for_checkout`, `completed`). Cancelled / no-show / checked-out appointments never block a rebook.

## Audit & event bus

Every transition emits:
 * An audit row — `action`, `entity_type=appointment`, `entity_id`, `metadata.from_status`, `metadata.to_status`, `metadata.override`, `metadata.reason`, `metadata.location_after`, `metadata.tenant_id` (plus `intake_gate_bypassed` on intake override; `forced` on room overrides).
 * An event-bus publish — `appointment.<transition_name>` with the updated appointment + previous status. The checkout transition's payload is the clean hook point for future payment / invoice / follow-up / print integrations.

## Permissions

All workflow transitions are gated by `appointment.update`:
 * super_admin, org_owner, clinic_manager, front_desk, provider, clinical_staff — granted.
 * patient_portal — denied at the authz layer (401/403).
Rooms CRUD is `clinic_settings` read/update (admin-only for writes).

## UX rules

 * Status and intake are surfaced as text labels (badge + icon) across every surface — never color-alone.
 * `current_room_id` assigned via a conflict override renders an explicit "Override" pill next to the current-room badge so staff cannot mistake it for normal occupancy.
 * Waiting-room / roomed / ready / ready-for-checkout rows display overdue pills when the stage exceeds its expected duration (15m / 30m / 10m / 10m).
 * Calendar day/week views render a status-tinted left border and an inline status label per appointment card; cancelled appointments are strike-through but remain clickable (historical access).
 * Canceled appointments reappear in history queries and detail views but are completely invisible to conflict checks for rebooking.
