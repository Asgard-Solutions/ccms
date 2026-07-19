# Clinical Redesign — Incident Runbook

**Purpose:** Response playbook for incidents involving the Clinical redesign.

## Severity guide

| Severity | Definition | Response time | Rollback trigger? |
|---|---|---|:-:|
| Blocker | Clinical page blank / unreachable / masks failing / cross-tenant leak / signed-record mutation | Immediate (page on-call) | Yes |
| Critical | Multiple users cannot complete a documented workflow | 30 min | Selective rollback per slice |
| Major | Single-section outage, degraded UX, elevated error rate | 2 hours | No (retry + monitor) |
| Minor | Cosmetic, sporadic, one user | Next business day | No |

## Detection

Sources:
1. Alert firing from `CLINICAL_MONITORING_PLAN.md`.
2. Support ticket routed via `CLINICAL_SUPPORT_BRIEF.md`.
3. Clinical or operations lead direct report.
4. Automated test failure in CI.

## Initial response (any severity)

1. Acknowledge alert / ticket within response time.
2. Post to incident channel: symptom, affected users/tenants, timestamp.
3. Verify against `CLINICAL_SUPPORT_BRIEF.md` decision tree.
4. Reproduce in the internal / staging environment if possible before touching production.
5. Do **not** touch patient data, signed records, or audit rows in response to an incident.

## Escalation

- Blocker/Critical → clinical platform lead + platform reliability + compliance officer (all).
- Major → slice owner + clinical platform lead.
- Minor → slice owner.

## Rollback decision matrix

| Blast radius | Confidence in root cause | Action |
|---|---|---|
| ≥ 2 tenants OR ≥ 5 users | Low | Full rollback per `CLINICAL_ROLLBACK_RUNBOOK.md` §R1 |
| 1 tenant | High (single slice) | Selective slice rollback per §R2 |
| 1 user | Any | Per-user override per §R3; do not modify production settings |
| Any masking or cross-tenant leak | Any | Full rollback immediately + compliance page |
| Any signed-record mutation | Any | Full rollback + compliance page + halt all writes to the affected tenant |

## During rollback

- Announce in the incident channel.
- Verify legacy layout renders after propagation.
- Confirm no downstream service went into a retry storm as a result.
- Confirm audit-log emission continues for legacy layout.

## Post-incident

1. Root-cause analysis within 3 business days.
2. Regression test added referencing the failing scenario.
3. Runbook update if a new failure mode was discovered.
4. Post-mortem shared with the release manager + clinical platform lead + platform reliability + compliance.
5. Retrospective note filed in `/app/memory/CHANGELOG.md`.

## What must never happen

- No incident response should trigger a **feature** change (even a small one) during rollout. Change control still applies.
- No incident response should mutate patient data or signed records.
- No incident response should log PHI to the incident channel.

## Communication templates

**Initial acknowledgement**
```
Investigating: Clinical page <symptom>. Affected: <scope>. Timestamp: <UTC>.
Next update in 15 min.
```

**Rollback in progress**
```
Rolling back the Clinical redesign to the legacy layout as a precaution. Existing data unaffected. Estimated propagation: 5-15 min.
```

**Resolved**
```
Clinical page issue resolved. <One-line RCA>. Post-mortem link: <TBD>.
```

## Contact list

| Role | Owner | Backup |
|---|---|---|
| Incident commander | Release manager | Clinical platform lead |
| Clinical platform | Clinical platform lead | Platform reliability |
| Compliance | Compliance officer | Legal (as needed) |
| Support | Support lead | On-call support |
