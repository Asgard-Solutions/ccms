# Clinical Support Brief

**Purpose:** How support triages Clinical-redesign issues without exposing PHI.

## Effective-flag identification (do this first)

Ask the user to open Chrome DevTools → Console and run:

```javascript
const flags = ['clinicalRedesign','clinicalRedesignPhase2WaveA','clinicalRedesignPhase2WaveB','clinicalRedesignPhase3','clinicalRedesignPhase3Slice3','clinicalRedesignPhase3Slice4','clinicalRedesignPhase3Slice5','clinicalRedesignPhase3Slice6'];
flags.map(k => `${k}=${localStorage.getItem('ccms.flags.'+k) ?? '(default)'}`).join('\n')
```

Paste the response into the ticket. Do **not** copy any other console output — it may contain user data.

## Known-issue decision tree

**Q1: "Clinical page is blank."**
- Check flags (Q above). If `clinicalRedesign=off` → user set the legacy fallback; restore with `localStorage.removeItem('ccms.flags.clinicalRedesign')`.
- If flags look normal → ask which browser + version. Test in incognito to rule out extensions.
- If it reproduces in incognito → escalate to platform reliability with the tenant + role.

**Q2: "Section says 'Section unavailable — Retry'."**
- This is Slice 6 partial-failure handling. Ask which section slug.
- Ask them to click Retry. If it recovers → transient — file a monitoring note only.
- If it persists → collect the section slug + timestamp + tenant id. Escalate to platform reliability. Do not ask for encounter IDs.

**Q3: "My preferences reset."**
- Reproduce the user's mode switch in a support account. Confirm the PATCH `/auth/me/preferences` returns 200 in the user's browser (Network tab).
- If PATCH returns 500/503 → escalate to platform reliability.
- If PATCH returns 422 → the user has an outdated bundle; ask them to hard-reload.

**Q4: "The Workspace Mode selector is missing."**
- Check flags: `clinicalRedesignPhase3Slice5` must be effectively `on` (parent chain on).
- Check the user's role — patients cannot switch modes.
- If the user's role should have access → escalate to workspace/preferences team.

**Q5: "Billing count on Current Care Status is empty."**
- This is expected for staff / front-desk roles. The row is intentionally hidden for roles the aggregate returns 403 for.
- If the user is admin / doctor / billing → check `/api/patients/{id}/clinical/billing-readiness/aggregate` response in the user's Network tab.

**Q6: "The Next Actions panel is empty."**
- Confirm the parent chain is on.
- Confirm the user has write permission on the chart (Next Actions suppresses write-scoped rules for read-only viewers).
- Confirm the chart actually has actionable signals (no unsigned notes, no billing warnings, etc.).

**Q7: "Preview watermark blocks the back-to-top button."**
- Preview environment only. Does not ship to production tenants. Documented in `PHASE1_TEST_DISPOSITION.md`. Manual click works.

## How to distinguish permission-denied from service failure

Ask the user to open Network tab → look at the specific `/api/patients/*/clinical/*` request:

- `403` → permission-denied. Confirm role. Do not switch to legacy fallback.
- `500` / `502` / `503` → service failure. Ask for tenant id + timestamp. Escalate.
- `499` / network error → transient. Ask them to retry.

## PHI-safe diagnostic checklist

**Ask the user for:**
- Their role + tenant id.
- The section slug (e.g., `outcomes`, `imaging`).
- The screenshot **with faces / DOBs / names blurred**.
- The Network tab response status code (not the response body).

**Never ask for:**
- Encounter IDs, note IDs, patient IDs, dates of service.
- Free-text notes or search text.
- Diagnosis codes.

## How to switch to legacy fallback

1. DevTools → Console → `localStorage.setItem('ccms.flags.clinicalRedesign','off'); location.reload();`
2. To restore: `localStorage.removeItem('ccms.flags.clinicalRedesign'); location.reload();`

## Escalation matrix

| Symptom | Team | Owner |
|---|---|---|
| Blank Clinical page (multiple users) | Platform reliability | On-call PR lead |
| Preference PATCH failing | Workspace / preferences | On-call preferences owner |
| Timeline / outcome / imaging fetch failing | Clinical platform | On-call clinical lead |
| Billing readiness aggregate wrong | Billing team | On-call billing lead |
| Audit-log gap | Compliance | Compliance officer |
| Suspected data mutation | Halt, page platform reliability + compliance simultaneously | Both |

## Incident communication template

```
Subject: Clinical page — <symptom> — investigating

We are aware of a Clinical page issue affecting <tenant/roles/count>. Investigating.
Rollback is available if the issue escalates.

Next update: <15 min from now>.
```
