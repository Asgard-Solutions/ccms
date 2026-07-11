# Clinical Feature-Flag Rollback Runbook

**Scope:** Patient Profile > Clinical redesign. Frozen 2026-02-15.
**Applies to:** all environments (development, preview, staging, production).
**Sensitive?** Yes — flag flips can affect every logged-in user's Clinical page instantly. Change control applies.

## Ownership

| Flag | Env var | Primary owner | Backup |
|---|---|---|---|
| `clinicalRedesign` | `REACT_APP_CLINICAL_REDESIGN` | Clinical platform lead | Platform reliability |
| `clinicalRedesignPhase2WaveA` | `REACT_APP_CLINICAL_REDESIGN_PHASE2_WAVE_A` | Clinical platform lead | Clinical platform lead |
| `clinicalRedesignPhase2WaveB` | `REACT_APP_CLINICAL_REDESIGN_PHASE2_WAVE_B` | Clinical platform lead | Clinical platform lead |
| `clinicalRedesignPhase3` | `REACT_APP_CLINICAL_REDESIGN_PHASE3` | Clinical platform lead | Platform reliability |
| `clinicalRedesignPhase3Slice3` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE3` | Outcomes team | Clinical platform lead |
| `clinicalRedesignPhase3Slice4` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE4` | Imaging + data-quality team | Clinical platform lead |
| `clinicalRedesignPhase3Slice5` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE5` | Workspace / preferences team | Clinical platform lead |
| `clinicalRedesignPhase3Slice6` | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE6` | Platform reliability | Clinical platform lead |

**Approved rollback authority:** Clinical platform lead OR platform reliability lead.

## How flags resolve (source-of-truth: `frontend/src/utils/featureFlags.js`)

Resolution order per flag, highest wins:
1. `localStorage.ccms.flags.<key>` = `"on"` / `"off"` (per-user override).
2. `process.env.REACT_APP_<UPPER_KEY>` (environment default).
3. Hard-coded fallback in `FLAG_DEFAULTS` (currently `"on"` for every clinical flag).

Then the parent chain is walked: **a child flag can never be effectively `on` when any ancestor is `off`**, regardless of the child's own storage/env/default.

Parent chain:
```
clinicalRedesign
├── clinicalRedesignPhase2WaveA
├── clinicalRedesignPhase2WaveB
└── clinicalRedesignPhase3
    ├── clinicalRedesignPhase3Slice3
    ├── clinicalRedesignPhase3Slice4
    ├── clinicalRedesignPhase3Slice5
    └── clinicalRedesignPhase3Slice6
```

## Where env defaults are configured

- **All environments:** `frontend/.env` at build/deploy time. Values: `on` or `off`.
- **Preview:** current values live in `/app/frontend/.env` (this container).
- **Staging / production:** configured through the deployment pipeline (environment-variable substitution or `.env` template). See platform documentation for the deploy mechanism.

Changing an env value requires a **rebuild** (React inlines env vars at build time). Rebuild + redeploy + browser reload before the new value takes effect.

## Where user overrides are stored

- Browser `localStorage` under keys `ccms.flags.clinicalRedesign`, `ccms.flags.clinicalRedesignPhase3`, etc.
- Overrides are per-browser + per-user. Cleared when localStorage is cleared or via the "Reset UI defaults" action in the profile menu.

## Exact rollback procedures

### R1 — Emergency full rollback (Clinical page broken for many users)

1. **Notify:** paging channel `#clinical-oncall` + on-call clinical platform lead + on-call platform reliability.
2. **Verify blast radius:** confirm at least 3 users affected across ≥ 2 tenants OR one tenant with ≥ 5 users affected.
3. **Cut env default off:** set `REACT_APP_CLINICAL_REDESIGN=off` in the production deploy env vars.
4. **Rebuild + redeploy:** trigger the standard production build/deploy pipeline. Expected propagation: 5–15 min for CDN cache + user browsers.
5. **Invalidate CDN cache** for `/static/*.js` (build hashes change).
6. **Communicate:** post short user-facing message: "Clinical page has been temporarily reverted to the previous layout while we investigate."
7. **Confirm rollback:** open the affected patient chart as three different users (admin/doctor/staff). Expect legacy `[data-testid=patient-clinical-tab]` (not `-v2`).
8. **Open incident ticket** referencing this runbook and the rollback timestamp.

### R2 — Selective slice rollback

Use to disable one child slice (e.g., Slice 5) while keeping Phase 3 alive.

1. **Notify:** slice owner + clinical platform lead.
2. **Set env var off:** e.g. `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE5=off`.
3. **Rebuild + redeploy.**
4. **Confirm:** loading a chart shows the slice's fallback (e.g., Slice 5 off → workspace switcher hidden, `NAV_ITEMS` default order).
5. **Existing user overrides:** any user who explicitly set `ccms.flags.clinicalRedesignPhase3Slice5=on` will still see `off` because the parent chain evaluates before the child's storage override — for a parent (`clinicalRedesignPhase3`) that stays on. If the env var alone is set to `off`, users can technically override via `localStorage.setItem('ccms.flags.clinicalRedesignPhase3Slice5','on')`. To fully suppress, disable the parent (`clinicalRedesignPhase3`) instead.

### R3 — Per-user rollback (single user reports layout issue)

1. Ask the user to open browser dev-tools → Console.
2. Run: `localStorage.setItem('ccms.flags.clinicalRedesign','off'); location.reload();`
3. To restore: `localStorage.removeItem('ccms.flags.clinicalRedesign'); location.reload();`

### R4 — Clearing a bad stored override

If a user's stored value is unparseable:
1. `localStorage.removeItem('ccms.flags.<key>')`
2. Reload.

The registry (`normalise()`) already coerces `"true"`/`"1"`/`"enabled"` → `"on"` and rejects everything else — returning `null` from the storage layer causes the env default to win, so bad values fail safe.

## Expected propagation time

| Vector | Propagation |
|---|---|
| Per-user localStorage override | Immediate (next `getFlag()` call — usually within a re-render) |
| Env-var change | 5–15 min after rebuild + redeploy + CDN cache invalidation |
| Storage listener | Immediate cross-tab (the registry emits `ccms-flag-change` + listens on the native `storage` event) |

## How to verify rollback

1. Open a demo patient chart in an incognito window.
2. Expected DOM signals:
   - Full rollback (`clinicalRedesign=off`) → `[data-testid=patient-clinical-tab]` present, `-v2` absent, no sticky patient-context header, no section nav.
   - Phase 3 off → sticky header + section nav visible, Care Status visible, **but** no Next Actions panel, no workspace switcher.
   - Slice 5 off → workspace switcher hidden, default section order restored, no Move up/down controls in summary.
3. Check backend audit logs for `patient.clinical_view` events using the legacy layout tag.

## How to restore the redesign

Reverse each step: clear the env var back to `on`, rebuild + redeploy. Advise affected users to clear any `ccms.flags.*=off` localStorage overrides.

## Non-events (things rollback never does)

- Never mutates patient data.
- Never mutates signed encounter notes.
- Never mutates preferences (persisted `ClinicalUIDefaults` stays intact; effective mode falls back to `general` on the client if the containing slice is off).
- Never mutates audit trail.
- Never touches billing readiness rows.

## Escalation matrix

| Situation | Contact |
|---|---|
| Blank Clinical page after rollback | Platform reliability on-call |
| Auth cookie gone / users logged out | Identity oncall |
| Audit-log emission failed during rollback | Compliance oncall |
| Data mutation suspected | Immediately halt rollback, page clinical platform lead + platform reliability lead + compliance |

## Communication template

```
Subject: Clinical page temporary rollback — <timestamp>

We have rolled back a portion of the recent Clinical page redesign as part
of a routine safety measure. Existing patient data, encounter notes, and
billing information are unaffected. Affected users may see the previous
layout for the next few hours while we investigate.

If you have questions, contact <clinical platform on-call>.
```

## Audit requirements

- Log the rollback timestamp + operator name + reason to the change-control channel.
- Retain the rebuild artefact (bundle hash + env-var diff) for 90 days.
- Include the rollback timestamp in the next release-notes update.

## Restoration checklist

- [ ] Env var(s) restored.
- [ ] Rebuild + redeploy triggered.
- [ ] Users notified.
- [ ] Rollback ticket closed with root cause + retest.
- [ ] Runbook updated if a new failure mode was discovered.
