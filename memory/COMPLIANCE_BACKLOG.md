# CCMS Compliance Backlog

**Last updated:** 2026-02-18
**Purpose:** Convert the `Missing` and `Partial` rows from `CONTROL_INVENTORY.md` into an engineering-ready backlog, grouped by priority.

> Out-of-App items (infra, legal, HR, operational) are listed under §4 for visibility but are not engineering deliverables.

---

## 1. P0 — High priority (block SOC 2 readiness review / harden production)

### P0.1 Retention worker (`AU-4`, `DM-5`, `ISO-A.8.10`)
- **Why:** Soft-delete sets `retention_until`, but no scheduled job purges records when that date passes. CCPA right-to-delete + HIPAA retention both depend on this.
- **Scope:**
  - New nightly job (cron-style, in-process scheduler or external runner) that:
    - Finds patients with `status == "deleted"` AND `retention_until < now`
    - Physically deletes the patient row + associated medical records + associated notifications
    - Writes a `patient.purged` audit row with counts
  - Dry-run mode + metrics counter (`ccms_retention_purges_total`)
- **Files:** new `backend/services/patient/retention_worker.py`, wire from `server.py::on_startup`, unit test.

### P0.2 Audit log immutability at DB layer (`AU-3`, `ISO-A.8.15`)
- **Why:** Today nothing in-app mutates `audit_logs`, but an admin with DB credentials could. Auditors expect structural immutability.
- **Scope:**
  - Add a MongoDB collection-level `$jsonSchema` validator rejecting updates (Mongo 5+)
  - Document migration path to an append-only store (S3 Object Lock / Postgres row-level permissions) for production
- **Files:** `core/db.py::create_indexes` (validator), `HIPAA_COMPLIANCE.md` update.

### P0.3 KMS-backed encryption key (`CR-3`, `ISO-A.8.24`)
- **Why:** `DATA_ENCRYPTION_KEY` is an env var; a KMS-wrapped DEK is the SOC 2 + ISO expectation.
- **Scope (code side only — infra is out of app scope):**
  - Add a `core/crypto.py::load_key()` abstraction so the source of the key becomes pluggable
  - Provide a `KMS_KEY_ID` env branch that, when set, fetches via `boto3` + `kms.decrypt`
  - Ciphertext already has `enc:v1:` prefix → supports `v2:` rotation
- **Files:** `core/crypto.py`, `HIPAA_COMPLIANCE.md` update.

### P0.4 Consent capture on registration (`DM-6`, `SOC2-P`, `CCPA-§1798.100(b)`)
- **Why:** No versioned record that the user accepted a Privacy Notice / ToS — auditable consent is a CCPA + SOC 2 Privacy requirement.
- **Scope:**
  - New `consents` collection: `{id, user_id, policy_version, policy_type, accepted_at, ip}`
  - `POST /api/auth/register` requires `accepted_privacy_version` in body; rejects if missing
  - `/api/auth/me` returns `latest_consents` so UI can force re-acceptance on version bump
  - Migration: seed consent for existing users as `legacy`
- **Files:** `services/identity/models.py`, `services/identity/router.py`, `frontend/src/pages/Register.jsx`.

### P0.5 Privacy Notice surface (`DM-7`, `CCPA`)
- **Why:** A privacy notice link is an explicit CCPA requirement and a SOC 2 Privacy expectation.
- **Scope:**
  - `/privacy` public route rendering a versioned notice (MDX or `.md` in repo, fetched by backend to record version)
  - Footer link on Login, Register, Dashboard
  - Copy drafted by Legal (out of app scope); Eng provides structure
- **Files:** new `pages/PrivacyNotice.jsx`, new `services/identity/privacy_notice.py` endpoint returning `{version, html}`.

### P0.6 Dependency SCA in CI (`VD-1`, `ISO-A.8.8`)
- **Why:** No automated CVE scan; a supply-chain regression would not surface.
- **Scope:**
  - GitHub Dependabot or equivalent for `requirements.txt` + `package.json`
  - Nightly `pip-audit` + `yarn npm audit --severity high` runs
  - Alert routes to `#security` channel (out of app scope)
- **Files:** `.github/dependabot.yml` (when CI joins), `docs/ci.md`.

---

## 2. P1 — Medium priority (next iteration)

### P1.1 Structured JSON logging (`OB-3`)
- Swap `logging.basicConfig` for `structlog`; inject `request_id`, `actor_id`, `ip` into every log event. Enables auditor-grade log search.

### P1.2 CSV evidence export for auditors (`BK-2`)
- New admin endpoint `GET /api/audit-logs/export.csv?from=&to=` (reauth-gated). Streams rows to CSV with a manifest header. Writes its own `audit_log.exported` row.

### P1.3 Alerting rule set committed to repo (`AU-6`, `OB-5`)
- Add `deploy/alerts/*.yaml` with Prometheus rules: login failure spike, cache error rate, DB read ratio drop, Redis down, HTTP 5xx ratio. Runbook links per alert.

### P1.4 SAST in CI (`VD-2`, `ISO-A.8.28`)
- Add `bandit` for Python + `eslint-plugin-security` + `semgrep` baseline rules. Fail the build on `HIGH`.

### P1.5 Purpose taxonomy (`DM-8`)
- Replace free-text `reason` with an enum (`treatment`, `billing_query`, `insurance_audit`, `patient_request`, `emergency`, `other`) + a free-text detail. Propagate through `audit_logs.purpose_code`.

### P1.6 Admin approval-based elevation (`AC-10`, `ISO-A.8.2`)
- Before a second admin can perform `DELETE /patients` or `PATCH /auth/users/{id}` against another admin, require a peer-approval token (ticket stored in Redis, TTL 10 min).

### P1.7 Versioned API schema export for auditors (`ISO-A.8.26`)
- Stable OpenAPI 3.1 at `/api/openapi.json`, version-tag responses. Already provided by FastAPI; add a published build into `/docs/api/`.

### P1.8 Protected-branch + required-review policy (`CM-1`)
- Enforce via platform (when Git provider is joined). Committed marker: `.github/CODEOWNERS`.

### P1.9 Staged environment (`SM-4`)
- Provision a staging deployment with independent `.env`, anonymised seed data, and a pre-merge smoke suite.

### P1.10 Right-to-know "plain language" view (`CCPA`)
- Extend `/patients/{id}/export` with `?format=report` returning a human-readable categories list (identifiers, health, billing…) next to the raw payload.

---

## 3. P2 — Lower priority (hardening)

### P2.1 Session fingerprint drift detection
- Bind access token to a hash of IP + UA; require reauth on drift. Watch for false positives on mobile networks.

### P2.2 Limit-use-of-sensitive-PI toggle (`DM-9`, `CCPA`)
- Patient-facing toggle that withholds sensitive fields from non-essential views (e.g., marketing emails — currently N/A but future-proofs).

### P2.3 Multi-tenancy `tenant_id` propagation
- Add `tenant_id` to every collection + JWT claim. Unblocks the multi-tenant goal from the original PRD.

### P2.4 Just-in-time admin elevation expiry
- Default admins to a read-only role; elevate for a time-boxed window via MFA re-challenge.

### P2.5 DLP — egress monitoring for exports
- Rate-limit + anomaly-detect PHI export volume per admin per day.

### P2.6 Formal SDLC checklist (`ISO-A.8.25`)
- Committed `docs/sdlc.md` + PR template with security checkboxes.

### P2.7 Versioned crypto policy doc (`CR-6`, `ISO-A.8.24`)
- Policy: allowed algorithms, key sizes, rotation cadence. Reviewed annually.

### P2.8 In-app incident reporting form (`IR-3`)
- Staff-accessible `/security/report` that files a ticket + audit row for suspected incidents.

### P2.9 Read-only read replica enforcement (`Processing Integrity`)
- When replica set is live, `get_db_read()` should assert `read_preference.mode != Primary` and warn on drift.

### P2.10 Regional data residency support (`CCPA`, `ISO-A.5.23`)
- Tenant-level `region` claim that routes DB traffic to a region-pinned cluster. Requires multi-region infra.

---

## 4. Out-of-App (tracked for visibility — not engineering work)

- **Infra:** WAF/DDoS, TLS 1.2+ cipher policy at ingress, VPC segmentation, backup strategy + tested restore, multi-region failover, centralised log sink, NTP, container image scanning, KMS provisioning.
- **Operational:** quarterly access reviews, incident runbook + tabletop, vendor risk reviews, change-advisory process, capacity planning docs.
- **Legal/Policy:** Privacy Notice copy, ToS, BAAs/DPAs with every PHI processor, crypto policy, retention policy, sanction policy, data-processing addendums.
- **HR:** onboarding, annual training completion, offboarding checklist, NDAs.

Each of the above should have a named owner in the governance wiki before a SOC 2 or ISO 27001 readiness assessment.

---

## 5. How priorities were assigned

| Priority | Criterion |
|---|---|
| P0 | Blocks production go-live OR blocks a SOC 2 readiness review OR is a literal CCPA requirement |
| P1 | Materially strengthens audit evidence or developer workflow; could be deferred to post-MVP |
| P2 | Hardening / nice-to-have; improves posture but not blocking |

Priorities should be re-evaluated at every quarterly security review.
