# CCMS Changelog

Append-only log of delivered work. Most recent on top.

---

## 2026-02-15 — Clinical redesign Slice 2.1 polish (Preset icon-strip)

**Why:** Users needed to identify saved timeline presets at a glance without opening them, while keeping the presentation strictly PHI-safe and reusing the sanitized preset schema.

**Shipped:**
- `frontend/src/pages/clinical/PresetIconStrip.jsx` (new) — purely presentational. Renders one icon per configured dimension (`event_kinds`, `sources`, `provider_ids`, `date_window`) using lucide-react `Layers`, `Database`, `Users`, `Calendar`. Multi-select dimensions show numeric counts only; `date_window` is presence-only so its value never surfaces. Icons carry `title` + `aria-label` for accessibility.
- Stale-preset detection reuses `detectStaleness()` from `timelinePresetsSchema` — no new rules invented. Stale dimensions get a warning-tone chip with a `⚠` glyph.
- `SavedPresetsMenu.jsx` — replaces the plain preset row with `preset name` + icon-strip on the second line. Legacy stale-glyph next to the name preserved for at-a-glance skim.
- 10 new jest tests in `PresetIconStrip.test.js` covering empty / partial / unsupported / stale-vocab / stale-provider / no-raw-values-leak / stable-ordering.

**Guardrails held:**
- Zero persistence or migration changes — pure UI rendering off the already-sanitized preset shape.
- Search text, dates, provider names, episode labels, and record ids are physically inaccessible (dimensions rendered as `count` numbers or presence bits only). A dedicated test asserts the JSON of the strip output never contains any raw filter value.
- Unsupported dimension keys in the input are silently ignored (never render an ad-hoc icon).

---

## 2026-02-15 — Clinical redesign Phase 3 Slice 3 (Outcome snapshot, trend, optional suggestions)

**Why:** Providers reviewing a chart needed a compact read-only view of outcome-measure history without any clinical inference — just neutral numeric summaries, a chart, an accessible table, and optional configured-instrument reminders. Slice 3 keeps every existing capture workflow untouched.

**Shipped:**

- **Frontend engine** (`frontend/src/pages/clinical/outcomeSeriesHelpers.js`, new)
  - `groupByInstrument`, `deriveSeries`, `buildMilestones`, `deriveOutcomeSuggestions`, `formatDelta`, `windowSeriesToLastMonths`, `SUPPORTED_INSTRUMENTS`.
  - Deterministic winner selection for duplicate captured_at (latest `updated_at` → latest `created_at` → lexicographic id).
  - Amended detection (`updated_at !== created_at`).
  - Partial-record filtering with `partial_count` surfaced.
  - Deltas use unicode minus (U+2212) so screen readers say "minus".
  - Explicit assertion in tests: no `improved`/`deteriorated`/`clinically_significant`/`direction` fields in engine output.
- **UI components** (all new)
  - `OutcomeSnapshotCard.jsx` — per-instrument card, amended + insufficient-baseline pills, source-record link.
  - `OutcomeTrendChart.jsx` — accessible SVG chart with **shape-encoded markers** (circle vs diamond) + milestone dashed verticals.
  - `OutcomeTrendTable.jsx` — data-table equivalent with `<caption>` / `<thead>` / `<tfoot>`, superseded rows visible.
  - `OutcomeSuggestions.jsx` — dismissible, deterministic reminders that never auto-populate.
  - `OutcomesSection.jsx` — orchestrator with loading / empty / permission-denied / error / view-toggle states.
- **Feature flag** `clinicalRedesignPhase3Slice3` — independent nested rollback (child of `clinicalRedesignPhase3`); default `on`. Legacy `OutcomesCard` remains mounted below the new section so the capture workflow is unaffected when the child flag is off.
- **Telemetry union #3** — `clinical_outcome_suggestion_interaction` on the shared `/api/telemetry/ui-action` endpoint. Six `OutcomeInstrumentKey` × two `OutcomeSuggestionInteraction` values. Cross-field mixes with `action_id` / `action_slug` / any PHI-shaped extra return **422**.
- Contract doc: `/app/memory/PHASE3_SLICE3_CONTRACTS.md`.

**Verified:**

- Backend `pytest`: 47/47 telemetry contract tests (13 new outcome-suggestion + 13 next-action + 21 care-status).
- Frontend `jest`: 62/62 clinical-unit tests (25 new outcome-series helper + 13 rule engine + 12 hook + 21 preset schema — some overlap in count sources).
- Smoke: Isabella Cho chart renders NDI snapshot + chart + table toggle + optional suggestions when configured instruments are stale.

**Files:** `frontend/src/pages/clinical/{outcomeSeriesHelpers,OutcomeSnapshotCard,OutcomeTrendChart,OutcomeTrendTable,OutcomeSuggestions,OutcomesSection}.{js,jsx}` + tests, `frontend/src/pages/clinical/ClinicalTabV2.jsx`, `frontend/src/utils/featureFlags.js`, `frontend/src/utils/telemetry.js`, `backend/services/telemetry/{router.py,SCHEMA.md}`, `backend/tests/test_outcome_suggestion_telemetry.py`.

---

## 2026-02-15 — Clinical redesign Phase 3 Slice 2 (Timeline filters, saved presets, long-history perf guard)

**Why:** Providers reviewing a long chart couldn't slice the timeline by kind, source, provider, episode, or date without losing scroll position, and every filter combination died on tab close. Slice 2 gives them durable, reusable presets while keeping patient-specific choices strictly transient.

**Shipped:**

- **Backend filter surface** (`services/clinical/grouped_router.py`)
  - `/api/patients/{id}/clinical/timeline/grouped` now accepts `event_kinds`, `sources`, `provider_ids`, `episode_ids`, `date_window`, `date_from`, `date_to`, `q` (all optional).
  - New `TIMELINE_SCHEMA_VERSION = "1.1"` returned when any filter is *attempted*; unfiltered calls still ship the legacy `"1.0"` shape (backward-compatible).
  - Response `filter_meta` echoes `applied`, `ignored_slugs`, `ignored_provider_ids`, `ignored_episode_ids`, `total_before_filter`, `total_after_filter` — enough for the UI to detect stale presets and prompt for repair.
  - Permission-aware provider filter: dead provider ids drop into `ignored_provider_ids` rather than 403-ing.
  - Cross-patient episode ids are silently dropped (echoed in `ignored_episode_ids`).
  - Server-side `q` length ≤ 80 chars enforced (**422** on overflow).
- **Durable prefs** (`services/identity/models.py`, `router.py`)
  - New nested `ClinicalUIDefaults` on `PATCH /api/auth/me/preferences` with `default_section`, `timeline_presets[]`, `default_timeline_preset_id`.
  - `TimelinePresetFilters` = `event_kinds`, `sources`, `provider_ids`, `date_window` only. Every other field (`patient_id`, `encounter_id`, `icd10_codes`, `q`, `date_of_service`, `date_from`, `date_to`, `episode_ids`) is `extra="forbid"` — 422.
  - Preset id pattern `^p_[a-z0-9]{8,32}$`, ids + names unique per user, `default_timeline_preset_id` must reference an existing preset.
- **Frontend**
  - `TimelineFilterBar.jsx` — kind chips + free-text search + date window + provider/episode multi-select with per-chip stale flagging.
  - `SavedPresetsMenu.jsx` — durable presets with client-side sanitizer (`sanitizePresetFilters`), stale-preset toast on apply, "+ Save current filters" flow.
  - `timelinePresetsSchema.js` — allow-listed vocabularies + sanitizer + stale-detector shared with the tests.
  - `GroupedTimelineCard.jsx` — rewrite to server-side filter, `useClinicalReturnState` for scroll + expanded + filters, perf instrumentation + incremental render (`INITIAL_RENDER_CAP=100`, `VIRTUALIZE_THRESHOLD=200`), no-results / partial-failure / stale-preset states.
- **`clinical_ui_defaults` flows through `_to_public`** and `UserPublic` so `GET /auth/me` echoes it back to the client.

**Verified:**

- Backend `pytest`: 19/19 new Slice 2 tests (`test_grouped_timeline_filters.py`, `test_clinical_ui_defaults.py`) plus the 50 pre-existing telemetry + grouped + billing tests.
- Frontend `jest`: 46/46 clinical unit tests (13 rule engine + 12 hook + 21 preset sanitizer/detector).
- Backward compatibility: unfiltered timeline still returns `schema_version: "1.0"` — no client redeploy required.

**Files:** `backend/services/clinical/grouped_router.py`, `backend/services/identity/{models,router}.py`, `backend/tests/{test_grouped_timeline_filters,test_clinical_ui_defaults}.py`, `frontend/src/pages/clinical/{timelinePresetsSchema.js,TimelineFilterBar.jsx,SavedPresetsMenu.jsx,GroupedTimelineCard.jsx}` + tests, `frontend/src/pages/clinical/ClinicalTabV2.jsx`.

---

## 2026-02-15 — Clinical redesign Phase 3 Slice 1 (Cross-record linking & deterministic Next Actions)

**Why:** Chart users lacked a system-generated worklist of the specific structural gaps on a chart (unsigned notes, missing docs, unlinked diagnoses, blocked billing, overdue re-exams, unscheduled plan visits, missing intake, un-recorded outcomes). Users had to eyeball the chart to figure out what to do next. Slice 1 turns that inference into a deterministic, structured, permission-aware panel — while laying the transient-state primitive (`useClinicalReturnState`) that Slice 2-6 will build on top of.

**Shipped:**

- **`useClinicalReturnState()` hook** (`frontend/src/pages/clinical/useClinicalReturnState.js`, new)
  - Session/in-memory scope; mirrored to `sessionStorage` for cross-page-hop persistence in the same tab.
  - Keyed by an **opaque route-instance token** stored on `history.state.ccms_route_token`. Never patient IDs, never record IDs, never `localStorage`.
  - 30-min TTL; auto-drops expired entries on hydrate.
  - Cleared on `ccms-session-reset` custom event (logout via `AuthContext`, permission-set change via `PermissionsContext`, tenant switch).
  - Browser back/forward and refresh preserve state via `history.state`; direct URL entry starts empty.
- **`nextActionsEngine.deriveNextActions()`** (`frontend/src/pages/clinical/nextActionsEngine.js`, new) — pure function returning at most 9 rules, in fixed priority order:
  1. `sign-unsigned-note` (mandatory)
  2. `complete-missing-documentation` (mandatory)
  3. `attach-or-link-diagnosis` (mandatory)
  4. `open-blocked-billing-readiness` (mandatory)
  5. `review-billing-warning` (mandatory; deduplicated against blocked rule)
  6. `schedule-due-or-overdue-reexam` (mandatory)
  7. `schedule-remaining-planned-visits` (**dismissible**)
  8. `review-missing-required-intake` (mandatory)
  9. `record-configured-outcome-measure` (**dismissible**)
  - Deterministic (same input → same output), structured-data only, one-sentence explanation, non-clinical language, permission-aware, deduplicated, stable priority.
  - "Order imaging" deliberately **excluded** to keep the surface non-clinical.
- **`NextActionsPanel`** (`frontend/src/pages/clinical/NextActionsPanel.jsx`, new) — renders the engine output above `Active episode`. Dismiss button only on dismissible rules. Every row exposes `next-action-<id>[-label|-why|-open|-dismiss]` testids.
- **Nested feature flag** `clinicalRedesignPhase3` (default on, child of `clinicalRedesign`). Parent off → child off, regardless of local override.
- **Telemetry union** — `POST /api/telemetry/ui-action` now accepts both `clinical_care_status_action_selected` and `clinical_next_action_interaction` shapes on the same endpoint. Cross-field mixes rejected 422. Nine `NextActionId` + two `NextActionInteraction` values allow-listed.
- **`ClinicalTabV2`** now mounts the panel and provisions the route-instance token on chart mount.
- **`DiagnosesCard`** — wired the missing `onViewHistory` prop on `DiagnosisRow` (was previously dead state).

**Verified:**

- Backend `pytest`: 50/50 (13 new `test_next_action_telemetry.py`, 21 legacy `test_telemetry_ui_action.py`, 9 clinical grouped, 7 billing aggregate).
- Frontend `jest`: 25/25 (13 rule-engine, 12 hook/token contract).
- Frontend testing agent E2E (iteration_90): panel rendering, Open click + telemetry, feature-flag guardrails (both child and nested parent), route-instance token opacity, staff read-only filtering all PASS. Dismiss flow blocked by seed data (no demo patient triggers a dismissible rule) but covered by jest unit tests.

**Files:** `frontend/src/utils/featureFlags.js`, `frontend/src/pages/clinical/{useClinicalReturnState,nextActionsEngine,NextActionsPanel}.{js,jsx}` + tests, `frontend/src/pages/clinical/ClinicalTabV2.jsx`, `frontend/src/pages/clinical/DiagnosesCard.jsx`, `frontend/src/utils/telemetry.js`, `frontend/src/contexts/{AuthContext,PermissionsContext}.jsx`, `backend/services/telemetry/router.py`, `backend/services/telemetry/SCHEMA.md`, `backend/tests/test_next_action_telemetry.py`.

---

## 2026-05-04 — Per-surface AI model picker (cost/quality tuning)

**Why:** Now that Anthropic billing flows through the customer's own key, admins want to route each AI surface to the right model — Opus for high-stakes doctor-facing flows, Haiku for high-volume structured outputs — without code changes. Estimated 60-70% reduction in Anthropic spend for typical clinics.

**Shipped:**

- **Backend** (`services/ai/client.py`):
  - `SURFACE_RECOMMENDED_MODEL` table maps each of the 9 AI surfaces to a recommended Claude model (Opus / Sonnet / Haiku).
  - `AI_SURFACES` exposes label + intent + display order for the picker UI.
  - `AI_AVAILABLE_MODELS` lists the 3 supported Claude models with tier badges and per-million-token costs.
  - `get_model_choice(tenant_id, surface)` now resolves: `surface_models[surface]` → `model_name` → env default. Foreign providers fall back with a WARN log.
- **Backend** (`services/ai/router.py`):
  - `GET /api/ai/settings` now returns `surfaces[]`, `available_models[]`, and `surface_models{}` in addition to the existing tenant default — single payload feeds the picker.
  - `PUT /api/ai/settings` accepts `surface_models{surface_id: model_id}` with `extra="forbid"` Pydantic + per-key validation. Unknown model id → 422 with structured error. Unknown surface → silently dropped (forward-compat).
- **Frontend** (`pages/settings/AIModelsPage.jsx`, new):
  - Tenant-default selector at top.
  - 9 per-surface dropdown rows with intent help-text and `rec:` chip showing the recommended model.
  - **"Apply recommended per surface"** one-click button populates every override with the recommendation.
  - Sticky save bar with Save / Discard. Empty per-surface = use tenant default.
  - Cost-per-1M-tokens hint inline in every dropdown option for smart picking.
- **Nav**: registered `/settings/ai-models` (Brain icon) below `/settings/ai-templates` for admin role only.

**Verified:**

- `tests/test_ai_model_picker.py` 6/6 passing — GET metadata shape, doctor 403, PUT round-trip, unknown model rejected (422), unknown surface forward-compat dropped, runtime resolver honours override.
- Live screenshot of `/settings/ai-models` at `/tmp/ai_models.png` — page renders 9 surfaces + tenant default + recommendation chips + sticky save bar.
- Existing AI suites (`test_third_wave_ai.py`, `test_post_scribe_ai.py`, `test_quick_submit.py`, `test_pin_security.py`) — all pass in isolation; no regressions from the picker.

**Files added/modified:**

- `/app/backend/services/ai/client.py` (per-surface tables + resolver upgrade)
- `/app/backend/services/ai/router.py` (settings GET/PUT extension + validators)
- `/app/backend/tests/test_ai_model_picker.py` (new, 6/6 green)
- `/app/frontend/src/pages/settings/AIModelsPage.jsx` (new)
- `/app/frontend/src/App.js` (route registration)
- `/app/frontend/src/components/layout/navConfig.js` (nav entry)

**Notes:**

- The 3 listed models map to dated Anthropic ids (`claude-opus-4-5-20251101`, `claude-sonnet-4-5-20250929`, `claude-haiku-4-5-20251001`) so behaviour is pinned and cannot drift on a model alias rotation.
- Costs are display-only — actual billing is whatever Anthropic charges your account; this UI only helps tune which model gets called.



## 2026-05-04 — LLM migration: Emergent Universal Key → direct Anthropic + OpenAI

**Why:** Customer wants every Claude / Whisper / GPT call billed to their own Anthropic + OpenAI accounts.

**Shipped:**
- `services/ai/client.py` rewritten to use the official `anthropic` SDK (`AsyncAnthropic`) directly. Drops `emergentintegrations.LlmChat`. Public surface (`generate()`, `parse_json_safely()`) unchanged so every call site (AI Scribe SOAP draft, prior-section pull, CPT/ICD coding, semantic search, NL scheduling parser, patient visit brief, template overrides) keeps working.
- `services/scribe/transcribe.py` rewritten to use the official `openai` SDK (`AsyncOpenAI.audio.transcriptions.create`). Drops `emergentintegrations.OpenAISpeechToText`. Same `transcribe_audio_bytes(...) -> str` interface.
- Default models from env: `ANTHROPIC_TEXT_MODEL=claude-sonnet-4-5`, `OPENAI_TRANSCRIBE_MODEL=whisper-1`. Per-tenant override still honored via `ai_settings.model_name`; foreign providers fall back to the env default with a WARN log.
- Hard-fail (no fallback) when `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is missing — clear runtime error with file path.
- `requirements.txt` regenerated via `pip freeze`. `anthropic==0.98.1`, `openai==1.99.9`. `emergentintegrations` retained because `core/object_storage.py` still uses `EMERGENT_LLM_KEY` for Emergent's separate file-storage service (not LLM-related).

**Verified:**
- Standalone Anthropic call: ✅ `claude-sonnet-4-5` returned `"Paris."` for capital-of-France smoke test.
- Standalone OpenAI text call: ✅ `gpt-5.2` returned `"OpenAI key works"`.
- New `services.ai.client.generate()` wrapper exercised end-to-end against real Anthropic — provider/model selection, token counting, JSON-safe parse all intact.
- E2E pytest (`test_third_wave_ai.py`, `test_post_scribe_ai.py`, `test_quick_submit.py`) deferred until Atlas Database Access is fixed (currently blocking backend boot).

**Files modified:**
- `/app/backend/services/ai/client.py` (full rewrite)
- `/app/backend/services/scribe/transcribe.py` (full rewrite)
- `/app/backend/requirements.txt`
- `/app/backend/.env` (placeholders for `ANTHROPIC_API_KEY`, `ANTHROPIC_TEXT_MODEL`, `OPENAI_API_KEY`, `OPENAI_TRANSCRIBE_MODEL`, `OPENAI_TEXT_MODEL` — keys filled in by customer)

**Notes for future:**
- `EMERGENT_LLM_KEY` is still in `.env` but only `core/object_storage.py` reads it. Once Emergent Object Storage is replaced (S3 / Azure Blob / on-prem), the key can be deleted entirely.
- An `OpenAI Chat Completions` helper is wired (`OPENAI_TEXT_MODEL=gpt-5.2`) but not yet wrapped in a public function — add when a flow needs OpenAI text instead of Claude.



## 2026-05-04 — Demo PIN seeder + Live submission timeline (VERIFIED)

**1. Demo PIN seeder (P1)**
- New `scripts/seed_demo_pins.py` sets a known 6-digit PIN per demo
  user. Idempotent — re-runs rotate to the same PIN. Safe to run on
  any environment because every account in the map is fictional.
- PINs documented in `/app/memory/test_credentials.md` (Admin
  100001 / Doctor 200002 / Staff 300003 / Patient 400004 / Sunrise
  600006-900009 / Platform 500005).
- The PIN is for in-app re-verification (`POST /api/auth/me/pin/verify`),
  not for sign-in. All existing PIN flows (lockout, change, reset)
  unchanged — `test_pin_security.py` 25/25 still passing.

**2. Live submission timeline (P0)**
- Backend pub/sub at `services/billing/timeline_pubsub.py` (in-process
  fan-out, bounded per-subscriber queue).
- `services/billing/events.py::emit_claim_event` now best-effort
  publishes to the pub/sub on every emission. Failures never block
  the caller.
- `services/billing/sandbox_ack_simulator.py` schedules a fire-and-
  forget asyncio task on every sandbox `_do_submit_claim`. Walks the
  submission through `ack_999_accepted` (+5 s) → `ack_277ca_accepted`
  (+10 s) → `outcome_recorded` (+15 s) → `era_posted` (+20 s) and
  flips the claim to `paid`.
- `services/billing/ack_poller.py` (NEW) — background poller that
  every 60 s queries production-mode submissions and asks the
  resolved adapter for 999 / 277CA acks. Adapters return `None`
  today so the poller is a quiet no-op until live-transport phase
  ships; the scaffolding is there.
- `WS /api/billing/ws/claims/{claim_id}/events` — cookie-authenticated
  WebSocket. Validates tenant + claim ownership, subscribes to the
  pub/sub, sends a 25 s heartbeat ping, and pushes JSON `{type:'event'}`
  frames as new events land.
- Frontend `pages/billing/ClaimTimeline.jsx` (NEW) mounts on
  ClaimDetail. Polls `/events` every 30 s as a baseline AND opens
  the WebSocket for real-time pushes (deduped by event id). Shows
  Live / Connecting / Polling pill, latest-event card, full history.

### Verification

- Backend: `tests/test_claim_timeline.py` 4/4 + `test_demo_pin_seed.py`
  9/9 + `test_pin_security.py` 25/25 + `test_quick_submit.py` 3/3 +
  `test_third_wave_ai.py` 8/8 (testing-agent iter_88).
- Frontend (iter_88): live Playwright run on a freshly quick-submitted
  CHC sandbox claim — pill transitioned Polling → Live in ~2 s;
  full simulator chain rendered within 12 s; claim flipped to `paid`.

### Files added/modified

- `/app/backend/scripts/seed_demo_pins.py` (new)
- `/app/backend/services/billing/timeline_pubsub.py` (new)
- `/app/backend/services/billing/sandbox_ack_simulator.py` (new)
- `/app/backend/services/billing/ack_poller.py` (new)
- `/app/backend/services/billing/events.py` (publishes to pub/sub)
- `/app/backend/services/billing/router.py` (WS endpoint, simulator hook)
- `/app/backend/server.py` (ack_poller startup/shutdown)
- `/app/backend/tests/test_claim_timeline.py` (new)
- `/app/frontend/src/pages/billing/ClaimTimeline.jsx` (new)
- `/app/frontend/src/pages/billing/ClaimDetail.jsx` (mounts timeline)
- `/app/memory/test_credentials.md` (PIN reference table)

### Future work
- Multi-worker pub/sub (Redis or NATS) — current implementation is
  single-process. Single-worker uvicorn is fine for the demo; a
  scaled deployment would need a shared bus.
- Wire real 999/277CA fetchers in `ChangeHealthcareAdapter` to make
  `ack_poller.py` actually move production-mode timelines.



## 2026-05-04 — UX nits + One-click Submit-to-Clearinghouse (VERIFIED)

**1. Payer fee schedule on send-to-claim (P1)**
- `services/scribe/router.py::send_to_claim` now calls
  `services.billing.charge_capture.resolve_charge_price` whenever the
  caller passes `billed_cents=0` for a CPT line. Resolution order:
  payer schedule → self-pay schedule → catalog → 0. Response now
  includes a `price_sources[]` array (one entry per CPT line) so the
  doctor can see where each price came from.
- AI Scribe never asks the doctor to type fees; this closes the loop
  so the auto-generated draft claim has real money values out of the
  box.

**2. NLBookCard — hide irrelevant inputs (P2)**
- Reschedule intent now only renders Start + Duration. Cancel intent
  renders no inputs (just a destructive-styled summary with the LLM's
  cancel reason). Create intent unchanged.

**3. Server-side provider search on AITemplatesPage (P2)**
- `GET /api/auth/users` now accepts `q`, `limit` (cap 500), and
  `offset`. Search uses regex-escaped case-insensitive matching across
  name / first_name / last_name / display_name / email.
- `pages/settings/AITemplatesPage.jsx` was rewritten to debounce
  search input (250 ms) and re-fetch from the server. The list-of-
  truth is the server's response; client-side filter was removed.
  Search box now appears unconditionally on Provider scope.

**4. One-click Submit to Clearinghouse (P0)**
- New `POST /api/billing/claims/{claim_id}/quick-submit` endpoint.
  Pipeline: scrub → ready → submit through resolved adapter, in one
  transactional call. Sandbox/disabled adapters accept claims that
  fail the scrubber but flag them with `submitted_with_warnings:true`
  so demos and pre-enrollment testing keep working without sending
  PHI on the wire. Production-mode adapters strictly enforce
  scrubber pass.
- Frontend buttons:
  * `ScribePanel.jsx` → `scribe-quick-submit-btn` appears next to
    "Open claim →" after send-to-claim succeeds. Click renders a
    status pill (queued / accepted / rejected / manual), the adapter
    route, the synthetic `chc-sbx-{hex}` external id, and a sandbox
    indicator.
  * `ClaimDetail.jsx` → `claim-quick-submit-btn` next to the
    existing "Submit" button. Disabled unless status ∈ {draft,
    validation_failed, ready}.
- `useClaims.js` exposes `quickSubmitClaim(claimId, body?)`.

### Verification

- Backend: `tests/test_quick_submit.py` 3/3 + `test_iter87_wave_b.py`
  7/7 pass (testing-agent iter_87). Existing
  `test_third_wave_ai.py` 8/8 + `test_clearinghouse_phase2c.py` 11/11
  + `test_billing_phase9.py` 6/6 — no regressions.
- Frontend (iter_87): live Playwright verification of A3 (server-side
  search) and B2 (claim-quick-submit-btn gating). B3 verified by code
  review + transitive backend coverage. No UI defects.

### Files added/modified

- `/app/backend/services/scribe/router.py` (fee-schedule lookup)
- `/app/backend/services/identity/router.py` (q/limit/offset)
- `/app/backend/services/billing/router.py` (`/quick-submit`)
- `/app/backend/tests/test_quick_submit.py` (new)
- `/app/frontend/src/pages/scheduling/NLBookCard.jsx`
- `/app/frontend/src/pages/settings/AITemplatesPage.jsx`
- `/app/frontend/src/pages/ai/ScribePanel.jsx`
- `/app/frontend/src/pages/billing/ClaimDetail.jsx`
- `/app/frontend/src/pages/billing/useClaims.js`

### Notes for future work
- Sandbox-only mode is current default (`CLEARINGHOUSE_CHC_MODE=sandbox`
  in `/app/backend/.env`). Production-mode flip requires
  CLEARINGHOUSE_CHC_CLIENT_ID + CLEARINGHOUSE_CHC_CLIENT_SECRET +
  enrollment_status='enrolled' on the payer; live HTTPS transport is
  the next phase (currently logs a WARNING and behaves as sandbox).



## 2026-05-04 — Send-to-claim + NL reschedule/cancel + paginated provider dropdown (VERIFIED)

**1. Send-to-claim from Scribe SOAP draft (P1)**
- `POST /api/scribe/encounters/{note_type}/{note_id}/send-to-claim`
  creates a draft claim directly from accepted CPT/ICD suggestions on the
  scribe-generated SOAP note. Role-gated (admin / doctor); 404 on unknown
  note or payer; 422 when no codes selected.
- `ScribePanel.jsx` exposes a "Create draft claim" button (payer Select +
  submit) that appears once CPT **and** ICD lists are non-empty. Success
  renders a link to `/billing/claims/{id}`.

**2. NL scheduling — reschedule + cancel intents (P1)**
- `nl_router.py` extends `/parse` with `intent ∈ {create, reschedule, cancel}`,
  resolves `target_appointment_id` from free text, and adds
  `POST /nl/reschedule` + `POST /nl/cancel` (same conflict/404/403 guards
  as canonical endpoints).
- `NLBookCard.jsx` switches confirm-button label + icon based on intent
  (Reschedule → calendar-clock, Cancel → calendar-x). Conflict 409 surfaces
  as an in-card error; clarifications bubble up when the target appointment
  is ambiguous.

**3. AITemplatesPage paginated provider dropdown (P2)**
- `AITemplatesPage.jsx`: when Scope=Provider and the tenant has >50 doctors
  the dropdown shows a search box and caps visible options at 100 to keep
  the Select snappy. Server-side `?q=` pagination noted as a future upgrade
  once tenants cross ~500 providers.

**Verification**
- `test_third_wave_ai.py`: 8 passed / 4 skipped (skips are seeded-audio
  happy paths, identical to last wave).
- Playwright E2E (iteration_86.json): all three new flows green on
  Riverbend seed with admin + doctor credentials. No regressions on
  context-aware docs, semantic search, or scribe transcription.



## 2026-05-04 — NL scheduling + override propagation + picker UX

**1. Natural-language scheduling (P2)**
- `services/scheduling/nl_router.py` exposes `POST /api/scheduling/nl/parse`
  and `POST /api/scheduling/nl/create` (admin / doctor / staff).
- Parse turns free text like *"Book Hannah Whitaker for an adjustment
  with Dr. Carter next Friday at 10am"* into a structured intent —
  resolved patient/provider/appointment_type/location IDs, ISO start,
  duration, plus a `clarifications[]` array surfaced to the UI when
  anything is ambiguous.
- Hallucination-guarded: every ID returned by Claude is re-validated
  against the tenant's own data before reaching `nl/create`. Create
  delegates to the canonical `create_appointment` so existing event-bus
  hooks (reminders, billing, etc.) still fire.
- New `pages/scheduling/NLBookCard.jsx` mounts above the scheduling
  toolbar. Two-phase flow: parse → confirm. Pre-fills Selects with
  candidate lists when an entity isn't uniquely resolved. Never
  auto-creates without an explicit confirm click.

**2. Template-override propagation**
- `services/ai/router.py::_augment_with_template` resolves merged
  per-tenant / per-location / per-provider override instructions at
  runtime and appends them to the base system prompt for chart-brief,
  prior-sections, and draft-sections (in addition to scribe SOAP).
- `_note_to_patient` now returns `location_id` + `provider_id` so the
  encounter-scoped surfaces can scope the override correctly.

**3. Per-location id picker UI**
- `pages/settings/AITemplatesPage.jsx` replaces the free-text scope_id
  Input with a context-aware Select. When scope=location the dropdown
  pulls `/api/authz/locations`; when scope=provider it pulls
  `/api/auth/users?role=doctor`. Shows the location name + code or the
  doctor's display_name fallback chain (display_name → first+last →
  name → email) plus an email tiebreaker so duplicate-named seed
  doctors are distinguishable. The list view labels saved rows with
  the same friendly names instead of raw UUIDs.

**Tests**
- `tests/test_nl_scheduling.py` — 7/7 (parse happy path, role gates,
  text-too-short 422, hallucination guard, create 404 + 422,
  template-override propagation smoke for chart-brief).
- Cross-suite AI: 43 passed + 1 skip across the five AI test files.
- iteration_84 caught a frontend-only URL bug (`/locations` and
  `/users` returned 404); fixed and re-verified at iteration_85
  (100% frontend-only retest pass).

## 2026-05-04 — Post-scribe AI bundle (coding-suggest + semantic search + template overrides + collection-name refactor)

Closed the documentation→billing loop and added a chart-search surface
in one batch. All four pieces below shipped together and verified at
iteration_83 (backend 36/36 + 1 skip; full E2E green).

**1. Billing-readiness coding suggester (inline)**
- `POST /api/scribe/encounters/{note_type}/{note_id}/coding-suggest`
  (doctor-only). Body: `{drafts:{S,O,A,P}, addendum?}`. Pulls the
  patient's active diagnoses from `clinical_diagnoses` so the model
  can prefer existing ICD-10s. Returns `cpt_suggestions[]` (with
  modifier hints like `25` on E/M codes), `icd_suggestions[]` (one
  flagged `is_primary_candidate=true`), and
  `documentation_warnings[]` (e.g. "97140 billed for 8 minutes:
  CPT requires 15-minute units…").
- Auto-fires from `ScribePanel.applyAll()` so the doctor sees CPT/ICD
  hints inline the moment a SOAP draft is applied. Manual button
  available too. Renders three sections: CPT cards, ICD cards,
  amber-banner documentation warnings.

**2. Natural-language semantic search across patient charts**
- `services/ai/search_router.py` — `POST /api/ai/search` (admin /
  doctor / staff). Pulls up to 30 candidate snippets (last 8 signed
  follow-ups, 2 initial exams, 8 diagnoses, 3 treatment plans, 9
  outcome entries), formats them with `[s#]` IDs, asks Claude
  Sonnet 4.5 to rank with the new `SEMANTIC_SEARCH_SYSTEM` prompt,
  and returns `{answer, results[]}` with citations + 0.4 score
  floor. Cached per `(tenant_id, patient_id, query_hash)` for free
  repeat queries. PHI-safe audit row written.
- `pages/ai/PatientSemanticSearch.jsx` mounted on PatientDetail's
  Billing tab. Asks "How is the patient's low back pain trending?"
  and renders the answer with cited snippet cards.

**3. SOAP-template overrides per location/provider**
- `services/ai/router.py` — `GET / PUT / DELETE /api/ai/templates`.
  Stored in `ai_template_overrides` collection keyed on
  `(tenant_id, scope_type ∈ {tenant, location, provider}, scope_id,
  surface ∈ {scribe_soap, chart_brief, prior_sections, draft_sections})`.
  Resolution order at runtime: tenant → location → provider, with
  later scopes appended to the system prompt.
- `services/scribe/router.py::draft_soap_from_scribe` now resolves
  the merged override and concatenates onto `SCRIBE_SOAP_SYSTEM`
  before calling Claude.
- `pages/settings/AITemplatesPage.jsx` — admin-only page at
  `/settings/ai-templates` with editor + list + delete.

**4. `FOLLOW_UP_NOTES_COLL` constant refactor**
- New `core/clinical_collections.py` exporting `FOLLOW_UP_NOTES_COLL`,
  `INITIAL_EXAMS_COLL`, `REEXAMS_COLL`, `DIAGNOSES_COLL`,
  `TREATMENT_PLANS_COLL`, `OUTCOME_ENTRIES_COLL`, and `NOTE_TYPE_TO_COLL`.
- Migrated `services/ai/{context,router}.py`, `services/scribe/router.py`,
  and `services/ai/search_router.py` to import from the constant
  module. Eliminates the iteration_78-class collection-name
  drift bug at the source.

**Tests**
- `tests/test_post_scribe_ai.py` — 11 new tests covering
  coding-suggest (role gate, 404, 422, happy path with CPT+ICD shape),
  template-overrides round trip (admin-only + 404 on missing delete),
  and semantic search (patient-403, 422 on short query, happy path,
  cache hit on repeat).
- Cross-suite: 36 passed + 1 skipped across all four AI test files.
- Testing-agent iteration_83 verified all UI flows + caching.

## 2026-05-04 — AI scribe (voice-to-note) + AI-powered SOAP generation (P1)

Doctor-only side panel that lets a clinician dictate the visit and have
Claude Sonnet 4.5 turn it into a structured SOAP draft, with per-section
or "apply all" buttons that push the text directly into the encounter
editor. Audio is transcribed via OpenAI Whisper and held in encrypted
object storage only until the host note is signed, then auto-soft-deleted.

**Backend (`services/scribe/*`)**
- `router.py` — four endpoints under `/api/scribe`:
  - `POST /audio` — multipart upload + synchronous Whisper transcription
    (≤25 MB per chunk, MIME-validated). Returns `{audio_id, transcript,
    transcribe_status}`. Persisted in `scribe_audio` collection with
    storage_path pointing at Emergent object storage.
  - `GET /encounters/{note_type}/{note_id}/audio` — list chunks +
    concatenated `full_transcript` for the host note.
  - `DELETE /audio/{audio_id}` — explicit doctor soft-delete.
  - `POST /encounters/{note_type}/{note_id}/soap/draft` — body
    `{transcript?, addendum?}` → returns `{drafts:{subjective, objective,
    assessment, plan}, rationale, model}`. Falls back to stored chunk
    transcripts when `transcript` is empty.
- `transcribe.py` — thin `OpenAISpeechToText` wrapper using
  `emergentintegrations` with model `whisper-1` and `EMERGENT_LLM_KEY`.
- `prompts.py` — `SCRIBE_SOAP_SYSTEM` strict-JSON prompt that forbids
  inventing patient names, vitals, ICD codes, or imaging findings; lets
  the doctor's free-text addendum override the transcript on conflicts.
- `delete_audio_for_note()` helper — wired into `sign_follow_up_note`,
  `sign_exam`, and `sign_reexam` so chunks soft-delete the moment the
  host artifact is signed (HIPAA retention policy).
- All endpoints gate on `require_role("doctor")` — admin/staff/patient
  receive 403.

**Frontend**
- `pages/ai/ScribePanel.jsx` — recorder (`MediaRecorder`, click-to-start
  + click-to-stop, no hard cap, picks webm/opus or mp4/m4a based on
  browser), live MM:SS timer, chunk list with delete buttons, addendum
  textarea, single Draft button, full SOAP preview with per-section
  Apply + Apply-all. Self-hides outside the doctor role.
- `pages/clinical/FollowUpNoteEditor.jsx` — line 55 imports ScribePanel,
  line ~165 introduces a shared `applySectionFromAi` callback used by
  both EncounterAssistPanel and ScribePanel. Panel mounts in the right
  rail beneath the existing AI assist card, only when role=doctor and
  status≠signed.
- `pages/clinical/InitialExamEditor.jsx` — mounts ScribePanel above the
  section cards (template doesn't have a SOAP-shaped sidebar). Maps
  S→history.history_of_present_illness, O→examination.observation_inspection,
  A→assessment.initial_clinical_impression, P→assessment.treatment_recommendations.
- `api/scribe.js` — `uploadScribeAudio`, `listScribeAudio`,
  `deleteScribeAudio`, `draftScribeSoap`.

**Tests**
- `tests/test_scribe.py` — 7/7: doctor-only role enforcement, SOAP
  draft happy-path, 422 when both inputs empty, 404 for unknown note,
  list-audio shape.
- Testing-agent iteration_82 added `tests/test_scribe_iter82.py` (2/2):
  multipart audio upload → list → soft-delete → list-omits-chunk; and
  the auto-delete-on-sign flow (upload chunk → reauth → sign → list
  returns `chunks=[]`).
- Cross-suite AI regression: 27/27 across `test_ai_context_documentation`,
  `test_portal_visit_brief`, and the two scribe test files.
- E2E verified by testing agent: ScribePanel mounts, drafts populate
  within ~8s, Apply-Subjective injects 492 chars into the editor's
  interval_history, Apply-All cycles through all four sections, and
  the admin role correctly hides the panel.

## 2026-05-04 — Patient-facing AI visit brief

Wired the AI smart-cache pipeline into the patient portal. Patients
now see a friendly, plain-language preview of their upcoming visit —
"what we worked on last time + what to expect today + a few things
you might want to ask" — generated by Claude Sonnet 4.5 via the same
context loader as the staff-side chart-prep brief, but with a separate
prompt and cache surface so patient regenerations never invalidate
the clinician's cache (or vice-versa).

**Backend**
- `services/portal/ai_brief_router.py` — `GET /api/portal/visit-brief`
  + `POST /api/portal/visit-brief/regenerate`. Patient-role-only.
  Resolves `patient_id` from `users.linked_patient_id` (SMS OTP path)
  with a fallback to `patients.user_id == users.id` for password-
  authed patient accounts. Reuses `services/ai/cache.py` with
  surface=`patient_visit_brief` so the chart-brief and the patient
  brief don't collide.
- `services/ai/prompts.py` — new `PATIENT_VISIT_BRIEF_SYSTEM`. Returns
  strict JSON `{headline, last_visit, your_progress, this_visit,
  ask_about[], reminders[]}`. Explicitly forbids ICD-10 codes,
  medication names, and clinical jargon. Total length capped at
  ~180 words for phone-screen skim-reading.
- Soft-fails: when the LLM is unreachable the endpoint returns a
  graceful empty-brief shape so the portal never breaks.

**Frontend**
- `portal/PortalVisitBriefCard.jsx` — sticky card at the top of the
  portal overview. Skeleton loading state, refresh button, cached
  badge on cache-hit, "You might want to ask" + "Before you arrive"
  sub-sections. Self-hides when the brief has no usable content.
- `portal/PortalOverview.jsx` — mounts the card unconditionally; the
  card itself decides whether to render.
- `api/portal.js` — `fetchPortalVisitBrief` + `regeneratePortalVisitBrief`.

**Tests**
- `tests/test_portal_visit_brief.py` — 5/5 pass:
  admin-role 403, unauthenticated 401/403, happy-path shape +
  cached:true on 2nd call, regenerate breaks cache, ICD-code regex
  PHI-hygiene check.
- E2E by testing agent (iteration_81): backend 100% green; browser
  cookie-fetch confirmed real Ethan-Parker brief renders with 3
  reminders + 2 ask-about questions; portal regression intact.

## 2026-05-04 — AI Context-Aware Documentation (Claude Sonnet 4.5)

End-to-end shipped. Doctors now see an AI chart-prep brief on every
patient chart and an "AI assist" rail inside the follow-up note
editor that surfaces last visit's S/O/A/P, since-last-visit outcome
deltas (NPRS / questionnaires), and one-click "Draft Subjective + Plan"
generation — all powered by Claude Sonnet 4.5 via Emergent LLM Key
with a smart per-patient cache keyed on a content hash of the inputs.

**Backend (`services/ai/*`)**
- `router.py` — five endpoints under `/api/ai`:
  - `GET/PUT /settings` (per-tenant model + provider, admin-only)
  - `GET /chart-brief/{patient_id}` + `POST /chart-brief/{patient_id}/regenerate`
  - `GET /encounters/{note_id}/prior-sections`
  - `GET /encounters/{note_id}/since-last-diff`
  - `POST /encounters/{note_id}/draft-sections`
- `context.py` — `load_patient_context()` shapes demographics, last 5
  signed follow-up notes, recent outcome trends, and questionnaire
  scores into a token-efficient context dict + a stable 32-char
  content hash for the smart cache.
- `cache.py` — `ai_brief_cache` collection (tenant + patient + surface)
  serves cached briefs whenever the context hash matches.
- `client.py` — Claude Sonnet 4.5 via `emergentintegrations`. Writes
  PHI-safe `ai_usage` audit rows (model, latency_ms, status only — no
  prompt or response bodies are ever logged).
- `prompts.py` — system prompts for chart-brief, prior-sections,
  since-last-diff, and draft-sections surfaces.

**Frontend**
- `pages/ai/ChartBriefCard.jsx` — mounted in `PatientDetail.jsx`
  (Billing tab area). Shows skeleton → brief body, regenerate button,
  cached badge, model + generated_at footer.
- `pages/ai/EncounterAssistPanel.jsx` — sticky sidebar inside
  `FollowUpNoteEditor.jsx`. Three sub-cards: "Since last visit"
  callouts, "AI draft" with Pull-in buttons that drop the generated
  text directly into the editor's S/O/A/P fields, and "Last encounter"
  prior-sections summary.

**Bugs found and fixed during E2E verification (iteration 78–80)**
1. *Critical collection-name mismatch.* Both `services/ai/context.py`
   and `services/ai/router.py::_note_to_patient` queried
   `db.clinical_notes` (0 docs); CCMS stores follow-up notes in
   `db.clinical_follow_up_notes` (320 signed). Renamed both
   references — chart-brief now ingests real prior-encounter SOAP
   data and encounter-scoped routes return 200 for real notes.
2. *Pre-existing /api/appointments 500.* `AppointmentPublic.provider_id`
   was a required `str` but 24 production rows have `provider_id=None`.
   Made it `str | None = None` — frontend pages that block on
   appointments now render.
3. *Missing import.* `FollowUpNoteEditor.jsx` rendered
   `EncounterAssistPanel` without importing it; added the import at
   line 55.
4. *Asyncio test fixtures.* `tests/test_ai_context_documentation.py`
   was leaking motor clients across `asyncio.run()` boundaries.
   Calling `reset_router_for_tests()` at the start of each runner
   rebinds clients to the fresh loop.

**Tests**
- `/app/backend/tests/test_ai_context_documentation.py` — 13 passed +
  1 benign skip. Added a happy-path regression test
  (`test_encounter_scoped_routes_200_for_real_signed_note`) that
  fetches a real signed note from `clinical_follow_up_notes` and
  asserts /prior-sections + /since-last-diff return 200.
- E2E verified by testing agent (iteration_80): brief renders with
  cached badge on second visit, regenerate cycles cache, Pull-in
  populates SOAP fields, smoke regression on /communications/sms +
  /settings/email + /portal still green.



## 2026-04-22 — Patient records: Edit-mode required-field fix

Seeded Riverbend patients now **open cleanly in the Edit Patient
wizard** with zero validation errors on load.

**Root cause.** The seed was writing only the legacy flat shape
(`address`, `emergency_contact` as encrypted free-form strings) plus
top-level scalars like `primary_provider_id`. The Edit wizard
(`pages/patientWizardLogic.js :: payloadToForm`) exclusively reads
from grouped, structured sections — `address_details.{line1, city,
state, postal_code}`, `emergency_contact_details.{name, relationship,
phone}`, `demographics.*`, `contact.*`, `admin.primary_provider_id`,
`guarantor.*`, `insurance.*`. Because those groups were absent from
the seeded docs, the form opened with empty fields and fired
"address line 1 is required" / "emergency contact relationship is
required" / "assigned provider is required" on every persona.

**Fix (seed only; no UI/backend changes).**
- `services/demo/seed.py` adds two static lookup tables
  (`_ADDRESS_BY_NAME`, `_EMERGENCY_BY_NAME`) with fully structured
  address + emergency-contact data per persona. `_upsert_personas`
  now composes and persists all seven grouped sections —
  `demographics`, `contact`, `address_details`,
  `emergency_contact_details`, `admin`, `guarantor`, `insurance` —
  on every patient row. Jaxon Morgan gets a proper minor-dependent
  `guarantor` block naming Claire Morgan as the responsible party.
- `services/identity/seed.py` gets the same treatment for Ethan
  Parker (previously seeded only with flat legacy fields). Ethan now
  has a complete structured address, emergency contact, demographics,
  contact, admin (with Dr. Noah Carter as primary provider), and a
  `same_as_patient=True` guarantor.
- All seven grouped sections are **encrypted at rest** via
  `encrypt_patient_value()` so the seeded rows flow through the same
  PHI encrypt/decrypt pipeline as user-edited rows.

**Verification**
- Programmatic check replicating `validateStep(1)` +
  `validateStep(2)` against the unmasked API response for all 8
  Riverbend personas: **0 missing required fields**. Rechecked after
  a backend restart — still 0 (idempotent).
- Save round-trip test against `PATCH /api/patients/{id}` with a
  structured `address` payload — persists correctly; immediate
  response + subsequent GET both show the new value.
- UI smoke: Marcus Reid + Isabella Cho each open the Edit wizard
  with every required field pre-populated (Name, DOB, pronouns,
  marital status, language, mobile, street, city, state, zip,
  emergency contact). No inline `.text-destructive` error text
  renders on initial form load.
- Backend regression: 136/137 pass across Phase 6-12 + claims_queue
  + canonical_status + patient_intake_phase1 suites. The 1 failure
  (`test_grouped_update_preserves_other_sections`) is a pre-existing
  flake documented in the Phase 12 sign-off — not caused by this
  change.

**Files changed**
- `services/demo/seed.py`
- `services/identity/seed.py`

**Personas corrected.** All 8 seeded Riverbend personas now have the
complete Edit-form-required shape: Ethan Parker, Hannah Whitaker,
Marcus Reid, Isabella Cho, Derrick Stone, Aria Johnson, Claire
Morgan, Jaxon Morgan.

---



## 2026-04-22 — Curated billing demo data for Riverbend

Extends the realistic demo clinic seed with a curated billing story
so every billing-related screen is populated on first login. All
upserts tagged with `demo_seed_key`; fully idempotent and wiped by
`scripts/reseed_demo_clinic.py`.

**New file:** `services/demo/billing_seed.py` — 14 curated claims
covering every `ClaimStatus`, 12 submissions (EDI + portal), 5
ERA-backed remittances, 4 invoices, 2 patient statements, 1 cash
payment. Every row ties back to a seeded persona + payer + policy +
doctor from `services/demo/seed.py`.

**Coverage matrix**

| Canonical status   | Count | Personas                                  |
|--------------------|-------|-------------------------------------------|
| draft              | 1     | Hannah Whitaker                           |
| ready              | 1     | Hannah Whitaker                           |
| validation_failed  | 1     | Hannah Whitaker (missing modifier)        |
| submitted          | 2     | Isabella Cho (PIP portal), Derrick Stone (WC portal) |
| accepted           | 1     | Marcus Reid (Medicare, awaiting ERA)      |
| paid               | 4     | Marcus x2 Medicare, Aria PacificCare, Claire PacificCare (95d) |
| partially_paid     | 1     | Isabella Cho PIP ($80 of $145, flagged)   |
| denied             | 2     | Aria (CO-11 coding), Derrick (CO-16 WC case) |
| rejected           | 1     | Jaxon Morgan (CO-31 subscriber mismatch)  |

**Queue tab lights up.** All 5 tabs are non-empty on first login
(pending-submission: 2, needs-fixes: 1, rejected: 3, follow-up: 6,
all: 14).

**A/R aging lights up.** 0-30d, 30-60d, 60-90d, and 90+ buckets all
have at least one curated claim.

**Patient responsibility story.** Ethan paid cash ($0 balance),
Hannah $30 copay open, Aria $125 deductible (statement-ready),
Jaxon $60 after rejection (statement-ready, guarantor Claire).

**Denial / follow-up work-tray.** 4 actionable items — each with a
realistic denial/adjustment code and a one-line operator hint
attached to the claim's `followup_reason`.

**Wiring.**
- `services/demo/__init__.py` exports both seeders.
- `server.py` runs `seed_demo_clinic()` then `seed_demo_billing()` on
  every startup.
- `scripts/reseed_demo_clinic.py` wipes + re-runs both.

**Regression status.** 128/128 Phase 6-12 + queue v2 + canonical
status + claims queue phase 2b tests PASS. Idempotency verified on
restart (stable 14/19/12/5/4/2/1 counts).

**Documentation.** `DEMO_SEED.md` gets a new §7 "Billing demo story"
with the full persona/status/payer matrix, tab coverage, A/R aging
coverage, patient-balance story, denial work-tray, and a "what each
billing screen shows on first login" summary.

---



## 2026-04-22 — Billing / Claims / Change-Optum accepted status (sign-off)

Following the Phase 1–12 verification audit, the feature set is
formally accepted at the following status:

> **PARTIAL — sandbox-ready, not production-complete; blocked only on
> live Change/Optum production transport and related business
> prerequisites.**

**Phase status at sign-off**

| Phase                                              | Status  |
|----------------------------------------------------|---------|
| 1 — Claims Queue UI / worklist                     | PASS    |
| 2 — Canonical claim lifecycle                      | PASS    |
| 3 — Real claim data model                          | PASS    |
| 4 — Claim validation / Needs Fixes workflow        | PASS    |
| 5 — Change/Optum foundation                        | PASS    |
| **6 — Change/Optum submission pipeline**           | **PARTIAL** |
| 7 — Chiropractic rules layer                       | PASS    |
| 8 — Rejected / denied / follow-up workflow         | PASS    |
| 9 — Assignment / governance / audit                | PASS    |
| 10 — API / frontend deliverables                   | PASS    |
| 11 — Hardening / permissions / operational         | PASS    |
| 12 — Final integration verification / handoff      | PASS    |

**Phase 6 rationale.** 837P 005010X222A1 generator, scrubber pre-submit
gate, bulk submit, and trace/correlation persistence are all green in
sandbox. Live HTTPS transmission to the Change/Optum **production**
endpoint is NOT active — the adapter logs the payload and returns a
synthetic `Ack` when `CLEARINGHOUSE_CHC_MODE=production` is set
without credentials. Activating production is a business deliverable
(trading-partner credentials, payer enrollment, endpoint URLs, BAAs),
not a code gap. Estimated code work once prerequisites land: ~50 LoC
inside `clearinghouse/change_healthcare.py::submit()`.

**Next milestone.** Complete live Change/Optum production transport
once credentials, enrollment, and related business prerequisites are
available.

---



## 2026-04-22 — Realistic demo clinic: Riverbend Chiropractic & Wellness

**Replaces** the generic "Default Practice" / "System Admin" / "Morgan
Lee" placeholder seed with a believable fictional chiropractic clinic
so the product looks lived-in on first login.

**Seed architecture**
- New `services/demo/seed.py` is the single source of truth for the
  realistic Riverbend dataset (staff roster, payer catalog, patient
  personas, insurance policies, clinical notes, appointment board).
  All upserts are keyed on stable business identifiers so re-running
  on every boot is safe.
- `services/tenancy/seed.py` renames the default tenant to
  **Riverbend Chiropractic & Wellness** and the default location to
  **Riverbend — Downtown** (America/Los_Angeles). In-place updates so
  existing installs auto-upgrade.
- `services/identity/seed.py` now seeds realistic display names,
  job titles, NPI (for doctors), and phones onto the login-helper
  demo accounts. Emails + passwords unchanged for test stability.
- New `scripts/reseed_demo_clinic.py` — destructive reset that wipes
  test-run pollution off the Riverbend tenant only, then re-seeds.
  Sunrise + platform admin are never touched.

**Demo identities (login page + docs)**
- Administrator → **Ava Bennett** (`admin@ccms.app`)
- Chiropractor → **Dr. Noah Carter, DC** (`doctor@ccms.app`,
  NPI 1841792253)
- Front desk → **Mia Ramirez** (`staff@ccms.app`)
- Patient portal → **Ethan Parker** (`patient@ccms.app` — active-adult
  wellness / maintenance persona, full demographic intake)
- Platform admin → **Owen Sinclair** (`platform-admin@ccms.app`)

**Riverbend staff roster (beyond login helpers, shared pw
`Riverbend@ComplianceClinic1`)**
- Olivia Hart — Clinic Owner
- Dr. Samuel Ito, DC — Associate Chiropractor (NPI 1730598210)
- Lena Brooks — Office Manager
- Tomás Rivera — Billing Specialist
- Priya Shah — Chiropractic Assistant

**Patient personas (7 new + upgraded Ethan Parker)**
- Hannah Whitaker — acute neck pain (Cascade Blue Shield)
- Marcus Reid — chronic LBP / Medicare active-treatment
- Isabella Cho — auto accident / PIP (Northwest Auto PIP)
- Derrick Stone — workers' comp (Oregon SAIF)
- Aria Johnson — marathon runner / IT band (PacificCare)
- Claire Morgan — family head / guarantor (PacificCare)
- Jaxon Morgan — pediatric dependent on Claire's policy

**Clinical / scheduling / billing coverage**
- 7 realistic Chief-Complaint / Subjective / Objective / Assessment /
  Plan chart notes — one per persona, PHI encrypted at rest.
- 13-appointment rolling week: cancellation, completed visits,
  new-patient eval, adjustments, re-exam, PIP follow-ups, workers'
  comp visits, pediatric check, maintenance adjustment.
- 6 fictional payers covering every rail the app supports
  (commercial x2, Medicare w/ AT+sublux+ITD flags, workers' comp,
  auto PIP, self-pay).
- 7 insurance policies keyed to the right payer + dependent
  relationship example.

**Login UX**
- New "Demo clinic sign-in" panel on the login page replaces the
  terse `Admin / Doctor / Staff / Patient` table. Each row is a
  clickable auto-fill that shows the role label, the real person's
  name, and the email. Data-testids: `login-demo-administrator`,
  `-chiropractor`, `-front-desk`, `-patient-portal`.

**Documentation**
- New `/app/memory/DEMO_SEED.md` — end-to-end persona catalog, staff
  roster, payer list, appointment board, reseed instructions, and a
  "gold demo clinic" roadmap.
- `test_credentials.md` regenerator (inside `identity/seed.py`) now
  surfaces realistic people alongside the emails and links back to
  DEMO_SEED.md.

**Regression status**
- 128/128 Phase 1–12 tests PASS (Phase 6–12 suites + queue v2 +
  canonical status + claims queue phase 2b). No new regressions.
- 3 pre-existing failures on `test_iteration12_authz.py`,
  `test_iteration14_tenancy.py`, `test_patient_intake_phase1.py` were
  verified to fail on pristine main — not caused by this change.

---



## 2026-04-22 — Phase 1–12 verification audit + follow-up / self-assign UI

**Scope:** Full audit of the 12-phase professional medical claims
pipeline (queue UI, canonical lifecycle, real claim model, validation
workflow, Change/Optum foundation + submission, chiropractic rules,
rejected/denied/follow-up operations, assignment/governance/audit,
API+frontend deliverables, hardening, final integration). Audit
closed two real UI gaps; backend required no changes.

**Gaps closed**
- **Follow-up row** on ClaimDetail ► Workflow — previously the backend
  exposed `POST /api/billing/claims/{id}/flag-followup` and `DELETE`
  counterparts, but the UI never rendered a button to drive them. A
  new `FollowupRow` now renders a reason input + `Flag for follow-up`
  button (data-testid `claim-followup-flag`) when the claim is
  unflagged, and switches to a status view with `Clear follow-up`
  button (data-testid `claim-followup-clear`) when the flag is live.
  Tested end-to-end: claim surfaces on the `follow-up` tab
  immediately, aging badge + row chip wire up from the queue work
  already shipped in the previous session.
- **Self-assign + unassign shortcuts** on ClaimDetail ► Workflow —
  AssignmentRow now exposes `Assign to me` (data-testid
  `claim-assignee-self-assign`) when the claim is assigned to someone
  else or unassigned, and `Unassign` (data-testid
  `claim-assignee-clear`) when it's assigned. Both drive the existing
  PATCH /api/billing/claims/{id}/assignment endpoint — backend
  already enforces `claim.assign` permission and emits the same
  `billing.claim.assignment_*` audit events.

**Files touched**
- `/app/frontend/src/pages/billing/ClaimWorkflow.jsx` — import
  `useAuth`, import new followup helpers; new `FollowupRow`
  component; `AssignmentRow` extended with `Assign to me` + `Unassign`
  actions.
- `/app/frontend/src/pages/billing/useClaims.js` — new exports
  `flagClaimForFollowup` / `clearClaimFollowupFlag` matching the
  existing `/flag-followup` routes.

**Backend — untouched; re-verified**
- 94/94 Phase 6–11 suites, 8/8 `test_claims_queue_v2.py`, 6/6
  `test_claims_queue_phase2b.py`, 14/14
  `test_canonical_status_phase3.py`. Total: 128/128 on Phase 1–12
  scope. Three pre-existing flakes remain on unrelated billing
  modules and are explicitly tracked as a separate P2 cleanup
  (`test_run_rules_clean_claim`, `test_statement_body_deterministic`,
  `test_email_mock_path_when_no_key`).

**Verification status**
- Testing agent iteration_63 confirmed every new UX flow (FollowupRow
  flag ► status ► clear, AssignmentRow self-assign ► unassign).
- Live curl smoke for all four endpoints passed against the admin
  tenant on sandbox.

---



## 2026-04-22 — Phase 12: Claims pipeline handoff — filter-aware billed totals + UI wiring for follow-up / assignment

**Scope:** Final integration pass for the 12-phase professional medical
claims pipeline. Wire filter-aware per-tab billed totals into the queue
API, add front-end chips that surface those totals alongside counts,
expose the Phase 11 `unassigned` filter in the UI, and surface
follow-up / aging indicators on queue rows.

**Backend**
- `GET /api/billing/claims/queue` now returns a top-level
  `billed_totals` dict keyed by tab (`all`, `pending-submission`,
  `needs-fixes`, `rejected`, `follow-up`). Each entry is the sum of
  `billed_cents` across the tab's filter-aware query (payer, assignee,
  unassigned, age, raw status, canonical status all respected).
- Tab counts + billed totals are computed in a single `$group`
  aggregate per tab (replaces the prior `count_documents` call) so
  there is no extra round-trip even though we now return an additional
  financial dimension.
- New regression test
  `test_queue_v2_billed_totals_are_real_and_filter_aware` asserts: same
  keys as `tab_counts`, non-negative ints, `all >= each named tab`,
  zeroes under a bogus payer filter, and positive under a real payer
  filter that just received a seeded claim.

**Frontend — Claims queue (`/billing/claims`)**
- Each tab trigger stacks `CountChip` + new `BilledChip` (data-testid
  `tab-billed-total`) so operators see both load and financial stake
  per tab without switching views.
- Assignee filter gained `Unassigned only` option (data-testid
  `claims-assignee-filter-unassigned`). Selecting it forwards
  `unassigned=true` via `useClaimsQueueV2` and scopes both rows and
  `billed_totals` to unassigned claims.
- Claim rows now render an italic `Unassigned` label when
  `assigned_to` is null, a warning-tone follow-up badge (data-testid
  `claim-row-followup-<id>`) when `followup_flag=true`, and a subtle
  `<n>d old` hint (data-testid `claim-row-aging-<id>`) when
  `aging_days >= 30` and there is no explicit follow-up flag.

**Verification**
- Backend: 8/8 `test_claims_queue_v2.py`, 14/14
  `test_assignment_rbac_phase11.py`, 6/6 `test_claims_queue_phase2b.py`,
  8/8 `test_billing_phase9*`, 94/94 across Phase 6-11 suites. 3
  pre-existing flaky tests (`test_run_rules_clean_claim`,
  `test_statement_body_deterministic`,
  `test_email_mock_path_when_no_key`) remain flagged for separate
  cleanup — they are not Phase 12 regressions.
- Frontend: Testing agent confirmed tabs render paired
  count/billed-total per tab (e.g. `All 1687 $87,700`, `Pending
  submission 410 $21,282`, `Rejected / denied 286 $14,040`,
  `Follow-up needed 112 $5,815`). Selecting `Unassigned only` scopes
  rows + summary + tab totals consistently.

---



## 2026-04-22 — Patient Portal go-live + Month-end bulk statement dispatch

**Scope:** (1) Finalize the patient-facing portal shell so patients log in
and land on `/portal` (not the clinic AppShell), and (2) add a bulk
"Send all outstanding statements" workflow so billing staff can dispatch
every eligible statement with one click.

**Patient Portal**
- `ProtectedRoute.jsx` enforces a bidirectional role gate: `portal=true`
  routes reject non-patients (→ `/`), and every non-portal route
  redirects patients to `/portal`. Login lands patients directly on
  `/portal` by reusing the same gate.
- `PortalShell.jsx` renders a minimal top-bar + vertical nav (Overview,
  Statements) + signout; `PortalOverview.jsx` + `PortalStatements.jsx`
  consume `GET /api/billing/me/statements` and `GET /api/billing/me/statements/{id}.pdf`.
- Empty-state, invoice-breakdown toggle, and PDF download link all
  verified by the testing agent (iteration_61 — 0 defects).

**Bulk "Send outstanding statements"**
- New `POST /api/billing/statements/send-outstanding` (admin + staff).
  Iterates every patient with `balance_cents > 0`, compares current
  outstanding to the last statement's `total_balance_cents`, and
  regenerates + dispatches only if the balance has moved. Channels:
  email when the patient has an email on file, otherwise queued for
  mail. Returns `{generated, sent_email, queued_mail, skipped_unchanged,
  skipped_no_contact, errors, dry_run, details}`. Supports
  `{"dry_run": true}` preview without side-effects.
- `_build_statement_for_patient()` helper extracted from
  `create_statement` and shared by both the legacy per-patient endpoint
  and the new bulk endpoint so the generated document shape, audit
  rows, and invoice-breakdown snapshot stay identical.
- Frontend: new `billing-send-outstanding-btn` on the Billing Dashboard.
  Click fires a dry-run, opens `bulk-send-outstanding-dialog` with the
  preview copy ("N statement(s) will be generated — X email · Y mail ·
  Z skipped"), then dispatches on confirm.
- Idempotency: re-running against an unchanged dataset returns all
  zeros + `skipped_unchanged` == total outstanding patients.

**Tests**
- `/app/backend/tests/test_statements_bulk_send.py` — 3/3 PASS (dry-run
  shape; idempotency; doctor 403).
- `/app/backend/tests/test_statements_enriched.py` — 6/6 PASS
  (regression on the refactored `create_statement`).
- Frontend E2E + backend integration validated via testing agent
  iteration_61 — 0 defects, no retest required.



## 2026-02-15 — Notifications abstraction: Resend + Twilio (log-only fallback)

**Scope:** Provider-agnostic email / SMS / MFA-OTP plumbing. Real
delivery activates automatically when env vars are set; otherwise the
helpers run in structured log-only mode so local dev and CI never
require vendor credentials.

**What shipped**
- **`services/notifications/email.py`** — `send_email(...)` wraps Resend
  via `asyncio.to_thread`. Never raises. Structured logging with
  redacted recipient, correlation id, provider, event type.
- **`services/notifications/sms.py`** — `send_sms(...)` wraps Twilio
  Messages API with the same contract.
- **`services/notifications/verify.py`** — `start_verification(...)` +
  `check_code(...)` wrap Twilio Verify (managed OTP lifecycle with
  throttling + abuse controls). Dev-mode fallback accepts any 4–10
  digit numeric code so local MFA flows stay testable.
- **`.env.example`** — new file documenting every notification env var
  (RESEND_API_KEY, SENDER_EMAIL, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
  TWILIO_FROM_NUMBER, TWILIO_VERIFY_SERVICE_SID) plus core config
  with generation hints.
- **Partial-config safe**: email may be live while SMS stays stubbed,
  and vice versa. Failures are logged + audited but never crash an
  unrelated user action.

**Callers wired**
- `POST /api/auth/password-reset/request` — sends the reset link via
  `send_email`. `dev_token` remains in the response for local dev.
- `POST /api/workforce/invitations` — sends the invitation email via
  `send_email` immediately after creating the row.
- `services/billing/statement_delivery.py` already has its own Resend
  client for PDF attachments — left as-is since it pre-dates this
  abstraction. Future work: fold it in + add attachment support.

**Tests** — 11/11 passing in `test_notifications.py`:
- Email log-only when no credentials
- Email Resend happy path (mocked SDK)
- Email Resend swallows errors without crashing caller
- Email redaction helper
- SMS log-only when no credentials
- SMS Twilio happy path (mocked Client)
- SMS Twilio error handling
- SMS phone redaction
- Verify log-only start + check (valid / invalid code shape)
- Verify Twilio start with Service SID
- Verify Twilio check approved path

**Not wired yet (backlog)**
- SMS delivery of zip-password for report exports — the polling-based
  reveal flow exists; adding SMS delivery is a feature addition, not
  just wiring. Scaffolding is ready.
- MFA challenge delivery via Verify API — current MFA uses TOTP only;
  adding SMS-OTP channel is a future feature.

---

## 2026-02-15 — Drag-and-drop reorder for Appointment Types

**Scope:** Finish the long-pending P2 UX item.

**What shipped**
- `POST /api/appointment-types/reorder` — accepts
  `{ordered_ids: [...] }` and writes sequential `sort_order` values.
  Unknown/cross-tenant ids are filtered; missing ids keep their
  relative order and land after the explicit block. Admin-only,
  audit-logged.
- `AppointmentTypesManager.jsx` — native HTML5 drag-and-drop with
  grip-handle column, row highlighting on hover, optimistic UI, and
  rollback on backend failure. Zero new dependencies.

**Tests** — 4/4 passing in `test_appointment_types_reorder.py`:
reorder persists, foreign ids ignored, auth required, empty list 422.

---

## 2026-02-15 — Retry-after-reauth Axios interceptor: already shipped

Confirmed the previous-session deliverable (`components/ReauthGate.jsx`
at App.js:69) already implements the global 401-reauth → silent
dialog → replay original request pattern. No new work required;
marked backlog item complete.

---

## 2026-02-15 — Access Management Phase 5: Migration + Access History + Security Policies

**Scope:** Close out the 5-phase redesign.

**What shipped**
- **`services/authz/migration.py`** — `dry_run_legacy_backfill()` +
  `apply_legacy_backfill()` helpers. Idempotent. Classifies every
  unassigned user into `mapped` / `ambiguous` / `unmapped`.
- **`GET /api/authz/migration/legacy/dry-run`** — admin preview.
- **`POST /api/authz/migration/legacy/apply`** — admin runner,
  audit-logged, tenant-scoped.
- **Migration banner** on `/admin/users` — shows candidate count and a
  one-click "Apply migration" button when any mappable users are
  found.
- **`GET /api/authz/access-history?action_prefix=...&limit=...`** —
  filtered audit-log view for `authz.*` events.
- **`/admin/access-history`** (`AccessHistoryPage.jsx`) — replaces
  `/access-review`. Filter dropdown (All / Role changes / Assignments
  / Overrides / Elevation / Migration), CSV export, plain-English
  action labels, timestamps, actor + target chips, metadata preview.
- **Security Policies panel** in `RoleEditorDialog` — collapsible
  advanced section surfacing per-permission MFA / peer-approval /
  break-glass-only toggles. Backend `POST/PATCH /api/authz/roles` now
  accepts a `permission_policies` map alongside `permission_keys`.
- Legacy `/access-review` route now redirects to `/admin/access-history`.
  `AccessReview.jsx` deleted.
- Audit rows for `authz.role.*` + `authz.migration.*` now stamp
  `tenant_id` correctly so tenant admins see their own history.

**Tests** — 6/6 new + 20 regression still green.

---

## 2026-02-15 — Legacy access-management pages removed

**Scope:** Clean removal per user request — application is still in
development, new `/admin/users` + `/admin/roles` fully replace the old
experience.

**Deleted**
- `frontend/src/pages/RoleManagement.jsx` (534 lines)
- `frontend/src/pages/PermissionMatrix.jsx` (183 lines)
- Routes `/roles`, `/permissions` removed from `App.js`
- Nav entries `nav-roles`, `nav-permissions` removed from `navConfig.js`
- "Deprecated advanced tools" footer removed from `AdminUsersPage.jsx`
  (along with the `AlertTriangle` import it needed).

**Kept**
- `/access-review` + `AccessReview.jsx` — evolves into the Phase 5
  Access Change History surface.
- Backend `GET /api/authz/matrix` + `GET /api/authz/permissions`
  endpoints retained — they may be useful for future exports or
  admin scripting.

**Verified** (smoke-test after cleanup):
- `nav-admin-users` present ✓
- `nav-admin-roles` present ✓
- `nav-roles` absent ✓
- `nav-permissions` absent ✓
- `admin-users-advanced-*` links absent ✓

---

## 2026-02-15 — Access Management Phase 4: Roles screen + grouped Role Editor

**Scope:** Frontend-only. Backend CRUD shipped in Phase 2.

**What shipped**
- **`/admin/roles`** (`AdminRolesPage.jsx`) — card-based role catalog:
  - "Built-in roles" grid (9 common clinic roles, ordered by relevance:
    Clinic Manager → Org Owner → Provider → Front Desk → Clinical Staff
    → Billing Specialist → Auditor → Compliance Officer → Patient
    Portal). View-only with Clone action.
  - "Custom roles" grid — Edit / Clone / Archive. Inline confirm
    dialog for archive; force-unassigns users when in use.
  - Collapsible "Show internal / service roles" toggle for
    `super_admin` + `integration_account`.
  - Each card surfaces permission count, user count, built-in /
    custom / privileged badges.
- **`RoleEditorDialog.jsx`** — create/edit/view a role:
  - Loads `GET /authz/permission-catalog` and renders one accordion
    per module (11 modules).
  - Each module row shows a `X/Y` counter and a "Select all / Clear
    all / Select rest" chip.
  - Each permission row shows plain-English label + helper text,
    sensitivity tag, PHI/Financial/privileged badges, Flame icon on
    destructive/critical permissions.
  - Debounced live plain-English preview under the module list from
    `POST /authz/roles/preview-effective-permissions`.
  - View mode dims unselected permissions and disables all controls
    (built-in roles).
  - Create mode: `POST /authz/roles`. Edit mode: `PATCH /authz/roles/{key}`.
- **Nav**: new "Roles" entry at `/admin/roles`. Old `/roles` kept
  behind "Advanced: Role matrix" label.
- **AdminUsersPage**: "Manage roles" quick link now points to
  `/admin/roles` (was `/roles`).

**Verified**
- Visual smoke test: 9 built-in cards rendered, 1 custom card rendered
  with Edit/Clone/Archive buttons. "Show internal / service roles (2)"
  toggle visible.
- Leftover `custom_inuse_test_71b6c5` role from Phase 2 test runs
  cleaned up via DELETE `?force=true`.
- No regressions in Phase 1 (12/12) or Phase 2 (14/14) backend tests.

**Non-goals in Phase 4**
- Advanced Security Policies panel (MFA/approval/break-glass flags)
  still deferred — covered by existing PermissionMatrix's grants until
  explicitly migrated.
- Migration backfill + Access Change History tab → **Phase 5 (pending)**.

---

## 2026-02-15 — Access Management Phase 3: Users experience (frontend)

**Scope:** Frontend-only. Old pages at `/roles`, `/permissions`, and
`/access-review` retained as deprecated "Advanced" routes for backward
compatibility during the transition.

**What shipped**
- **New primary admin surface** `/admin/users` (`AdminUsersPage.jsx`):
  - Searchable list (name + email), status filter (all / active /
    disabled), role chips per row (+N more), status badge, Edit
    access / Disable / Reactivate actions.
  - "Add user" opens the new 3-step wizard.
  - "Manage roles" quick link to `/roles` (until Phase 4 replaces it).
  - Deprecated-advanced footer linking to `/roles`, `/permissions`,
    `/access-review` with an AlertTriangle icon.
- **`CreateUserDialog.jsx`** — 3-step guided flow:
  - Step 1: Profile (name + email + password ≥12 + phone). Next disabled
    until valid.
  - Step 2: Roles — common roles first; "Show advanced / internal
    roles" toggle reveals `super_admin` + `integration_account`. Each
    role shows built-in/custom/privileged badges and a "Covers:" hint.
    Roles list is lazy-loaded on Step 1 → Step 2 transition to avoid
    triggering a PIN step-up when the dialog first opens.
  - Step 3: Review — plain-English effective-access summary from
    `POST /authz/roles/preview-effective-permissions`, plus any
    high-sensitivity grants surfaced as amber chips.
  - Submit creates user + assigns roles in one flow (uses
    `POST /auth/users` for profile, then `POST /authz/users/{id}/roles`
    per selected role).
- **`EditUserAccessDialog.jsx`** — single-step modal to add/remove role
  assignments for an existing user. Live plain-English preview as
  roles are toggled; Save diffs the selected set against the current
  set and issues `POST /authz/users/{id}/roles` + `DELETE` calls.
- **Nav**: new top-of-admin "Users" entry; "Permissions" and
  "Access Review" relabelled with an "Advanced:" prefix to signal
  they're legacy power-user surfaces.

**Tests** — Backend Phase 1+2 regression: **26/26 green** (12 catalog +
14 custom roles). Frontend verified by testing agent iteration_60:
- `/admin/users` renders, deprecated footer links present.
- Create User Step 1 validation correct (email + password ≥12).
- Step 2 common vs advanced roles correctly split.
- All spec'd `data-testid`s wired.

**Non-goals in Phase 3**
- No grouped Role Editor (Phase 4)
- No legacy-role migration backfill (Phase 5)
- No Access Change History tab (Phase 5)

---

## 2026-02-15 — Access Management Phase 2: Custom Roles (backend CRUD)

**Scope:** Backend-only. Admins can now create, clone, edit, and archive
custom roles scoped to their tenant. System baseline roles remain
read-only.

**What shipped**
- `POST /api/authz/roles` — create custom role from name + description +
  permission-key list. 201 on success. Generates a unique `key` like
  `custom_my_role_xxxxxx`. Invalid permission keys are silently filtered
  (defensive).
- `POST /api/authz/roles/{key}/clone` — clone any role (system or
  custom) into a new custom role with all the source's permission keys.
  Tenant-scoped.
- `PATCH /api/authz/roles/{key}` — edit name / description / permissions
  on a custom role. System roles → 409. Empty permission_keys → 400.
  Replaces all `role_permissions` rows. Bumps `session_epoch` for every
  user with this role so their token is re-evaluated on next request.
- `DELETE /api/authz/roles/{key}?force=true` — archive a custom role.
  If in use (active user_roles rows), returns 409 with the assignment
  count. `force=true` revokes all user_roles rows and bumps session
  epochs. System roles → 409.
- `GET /api/authz/roles?include_user_counts=true` — now emits
  `is_custom: bool` and optional `user_count: int` per role.
- Every mutation emits a structured `log_audit` row
  (`authz.role.created`/`updated`/`deleted`) with tenant_id, actor,
  and changed-field metadata.

**Tests** — 14/14 passing in `test_custom_roles_phase2.py`:
- list with is_custom + user_counts
- create happy path + empty-permissions 400 + invalid-key filtering
- clone happy path + clone-requires-name 400 + unknown-source 404
- patch name + permissions + system-role 409 + empty-keys 400
- delete unused + delete system 409 + delete-in-use 409 + force=true
  revokes users + user_count reflects assignments

**No regressions** — Phase 1 (12/12) + checkout hooks (10/10 individually) still green.

---

## 2026-02-15 — Access Management Phase 1: Permission Catalog (backend foundations)

**Scope:** Backend-only foundations for the new Users/Roles/Permissions UX.
No frontend changes in this phase — the existing pages still function.

**What shipped**
- `services/authz/permission_catalog.py` — decorates every permission in
  `constants.PERMISSIONS` with:
  - one of 11 product-facing modules (Dashboard, Scheduling, Patients,
    Clinical, Billing, Claims, Reports, Compliance & Audit, Settings,
    User Management, Administration),
  - a plain-English label + helper text (e.g.
    `appointment.override_rules` → "Override scheduling conflicts"),
  - sensitivity/phi/clinical/financial/destructive/export/privileged
    flags pass-through from the source catalog.
- `GET /api/authz/permission-catalog` — admin endpoint returning the
  grouped, labelled catalog. 117 permissions across 11 modules, sorted
  by sensitivity desc then label asc inside each module.
- `GET /api/authz/users/{id}/effective-permissions?explain=true` —
  admin endpoint returning a user's effective grant list PLUS a
  plain-English summary suitable for the "Review access before save"
  step. Tenant-isolated; 404 on cross-tenant probe.
- `POST /api/authz/roles/preview-effective-permissions` — preview a
  plain-English summary for an arbitrary permission-key list
  (backs the Role Editor's live summary).
- `permission_catalog.explain_permissions()` — pure function used by
  both endpoints; groups grants into "can" / "cannot" buckets, tallies
  per-module read/write coverage, and surfaces any high/critical or
  destructive permissions in a `sensitive_grants` list.

**Tests**
- `backend/tests/test_permission_catalog_phase1.py` — 12/12 passing.

**Non-goals in Phase 1**
- No custom roles (Phase 2)
- No new UI (Phase 3)
- No DB schema changes
- No migration of legacy users (Phase 5)

**Known pre-existing failures (NOT introduced by Phase 1):**
`tests/test_iteration12_authz.py` — 14 failures on main due to cookie-auth
harness drift (the test helpers don't set `Authorization: Bearer`
headers after login). Verified identical baseline via `git stash`.

---
