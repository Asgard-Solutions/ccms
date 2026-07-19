# Phase 3 Slice 1 — Cross-record linking & Deterministic Next Actions

Delivered under nested feature flag `clinicalRedesignPhase3` (child of `clinicalRedesign`).

## Scope shipped

1. **`useClinicalReturnState()` hook** — Patient-specific transient UI state.
   * Session/in-memory scope only. Mirrored to `sessionStorage` for cross-page-hop persistence within the same tab.
   * Keyed by an **opaque route-instance token** stored in `history.state.ccms_route_token`. Never patient IDs, never record IDs, never in `localStorage`.
   * TTL: 30 minutes.
   * Cleared on: logout, tenant switch, permission-set change, TTL expiry, explicit `.clear()`.
   * Browser back/forward and refresh preserve state via `history.state`. Direct URL entry starts empty.
   * Cross-chart isolation verified: chart A's token ≠ chart B's token.

2. **`NextActionsPanel`** — Deterministic workflow follow-ups, computed from already-loaded chart data.

   | Priority | Rule id | Fires when | Dismissible |
   |---|---|---|---|
   | 1 | `sign-unsigned-note` | Any unsigned note / initial exam / re-exam | No |
   | 2 | `complete-missing-documentation` | A grouped visit has no attached note | No |
   | 3 | `attach-or-link-diagnosis` | Active work exists but no primary diagnosis is linked | No |
   | 4 | `open-blocked-billing-readiness` | Aggregate reports blocked visits | No |
   | 5 | `review-billing-warning` | Aggregate reports warnings and no blocked entries | No |
   | 6 | `schedule-due-or-overdue-reexam` | Re-exam is due within 7 days or past | No |
   | 7 | `schedule-remaining-planned-visits` | Active plan has planned − completed − scheduled > 0 | Yes |
   | 8 | `review-missing-required-intake` | Required intake fields still blank | No |
   | 9 | `record-configured-outcome-measure` | Active plan has configured instrument + no measurement in 14d | Yes |

   Rule guarantees:
   * **Deterministic** (same input → same output; snapshot-tested).
   * **Structured-data only** (no free-form clinical inference).
   * **One-sentence explanation** per rule.
   * **Non-clinical language** (workflow verbs only).
   * **Permission-aware** (billing rules silent when aggregate access denied; write-scoped rules silent for read-only users).
   * **Deduplicated** (billing-warning suppressed when a blocked-billing rule fires).
   * **Stable priority order** (fixed enum).
   * **Dismissible only when optional** — mandatory workflow gaps cannot be silenced.

3. **PHI-safe next-action telemetry.**
   * New event: `clinical_next_action_interaction`.
   * Allow-listed `action_id` (9 values) × `interaction` (`opened`/`dismissed`).
   * Shared endpoint with care-status CTA telemetry; cross-field mixes are rejected 422 by the validator.

4. **`clinicalRedesignPhase3` feature flag** with nested-parent dependency.
   * Default: **on**.
   * Child of `clinicalRedesign` (parent off → child off, regardless of local override).

## Non-goals

* No new durable `/me/preferences` field added this slice. The hook is architected to plug a durable-scope surface in later, but Slice 1 only exposes transient scope.
* No cross-record deep navigation added beyond section-jump (already implemented by the shell). Slice 2 will layer in filter-aware deep links.
* "Set inactive" diagnosis state still deferred pending backend status-model decision.
* "Order imaging" is deliberately **NOT** an emitted next-action rule — clinical recommendation is out of scope for workflow guidance.

## Route-token TTL & reset triggers

| Concern | Value |
|---|---|
| Storage key | `sessionStorage["ccms.clinical.returnState.v1"]` |
| Token location | `history.state.ccms_route_token` (opaque `r_<random>` string) |
| TTL | **30 minutes** (`TTL_MS = 30 * 60 * 1000`) |
| Cleared on | `emitSessionReset()` custom event `ccms-session-reset` |
| Reset triggers | `AuthContext.logout()` · `PermissionsContext` when `role_keys`/`tenant_id` change · explicit `.clear()` from consumers · TTL expiry |
| Never touches | `localStorage` — no durable device-scoped persistence for patient-specific UI state |

See `PHASE3_SLICE1_CONTRACTS.md` §3–§4 for the full contract.

## Files touched

| File | Change |
|---|---|
| `frontend/src/utils/featureFlags.js` | + `clinicalRedesignPhase3` (nested-parent chain) |
| `frontend/src/pages/clinical/useClinicalReturnState.js` | **new** — hook + session store |
| `frontend/src/pages/clinical/useClinicalReturnState.test.js` | **new** — 12 jsdom contract tests |
| `frontend/src/pages/clinical/nextActionsEngine.js` | **new** — pure rule engine |
| `frontend/src/pages/clinical/nextActionsEngine.test.js` | **new** — 13 unit tests |
| `frontend/src/pages/clinical/NextActionsPanel.jsx` | **new** — UI component |
| `frontend/src/pages/clinical/ClinicalTabV2.jsx` | mount NextActionsPanel behind flag; provide route-instance token |
| `frontend/src/pages/clinical/DiagnosesCard.jsx` | wire missing `onViewHistory` prop |
| `frontend/src/utils/telemetry.js` | + `trackNextActionInteraction` |
| `frontend/src/contexts/AuthContext.jsx` | dispatch `ccms-session-reset` on logout |
| `frontend/src/contexts/PermissionsContext.jsx` | dispatch `ccms-session-reset` on permission-set change |
| `backend/services/telemetry/router.py` | union `UIActionPayload` with next-action shape |
| `backend/services/telemetry/SCHEMA.md` | document new event shape |
| `backend/tests/test_next_action_telemetry.py` | **new** — 13 contract tests |

## Test outcomes

* Backend `pytest`: 50/50 telemetry + clinical-grouped + billing-aggregate tests pass.
* Frontend `jest`: 25/25 engine + hook tests pass.
